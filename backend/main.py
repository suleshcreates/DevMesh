import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from backend.database import Database
from backend.models import Agent, MessageCreate, Acknowledgement, poll_timeout
from backend.ws_manager import WebSocketManager

ROOT = Path(__file__).resolve().parents[1]

def create_app(db_path=None):
    @asynccontextmanager
    async def lifespan(app):
        app.state.db = Database(db_path or ROOT / ".devmesh" / "devmesh.sqlite3")
        app.state.ws = WebSocketManager()
        app.state.changed = asyncio.Condition()
        await app.state.db.open()
        try:
            yield
        finally:
            await app.state.db.close()

    app = FastAPI(lifespan=lifespan)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        # Pydantic errors include input values by default: do not echo them.
        return JSONResponse(status_code=422, content={"detail": "Invalid request"})

    async def publish(kind, data, agents):
        await app.state.ws.broadcast({"event": kind, "data": data}, agents)

    @app.post("/api/messages", status_code=201)
    async def send_message(request: MessageCreate):
        async with app.state.changed:
            data = await app.state.db.create_message(request)
            app.state.changed.notify_all()
        await publish("message.created", data, {data["sender"], data["recipient"]})
        return data

    @app.get("/api/inbox/{agent}")
    async def inbox(agent: Agent):
        return {"messages": await app.state.db.inbox(agent)}

    @app.post("/api/messages/{message_id}/ack")
    async def ack(message_id: UUID, request: Acknowledgement):
        data, changed = await app.state.db.acknowledge(message_id, request.agent)
        if data is None:
            raise HTTPException(404, "Message not found for recipient")
        if changed:
            await publish("message.acknowledged", data, {data["sender"], data["recipient"]})
        return data

    @app.get("/api/poll/{agent}")
    async def poll(agent: Agent, timeout_seconds: float = 45):
        try:
            duration = poll_timeout(timeout_seconds)
        except ValueError:
            raise HTTPException(422, "Invalid timeout") from None
        deadline = asyncio.get_running_loop().time() + duration
        async with app.state.changed:
            while True:
                messages = await app.state.db.inbox(agent)
                if messages:
                    return {"status": "task", "messages": messages}
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    return {"status": "no_task"}
                try:
                    await asyncio.wait_for(app.state.changed.wait(), remaining)
                except TimeoutError:
                    return {"status": "no_task"}

    @app.post("/api/agents/{agent}/heartbeat")
    async def heartbeat(agent: Agent):
        data = await app.state.db.heartbeat(agent)
        await publish("agent.heartbeat", data, {agent})
        return data

    async def socket_loop(socket, agent=None):
        await app.state.ws.connect(socket, agent)
        try:
            while True:
                await socket.receive_text()
        except WebSocketDisconnect:
            pass
        finally:
            app.state.ws.disconnect(socket)

    @app.websocket("/ws/dashboard")
    async def dashboard(socket: WebSocket):
        await socket_loop(socket)

    @app.websocket("/ws/agents/{agent}")
    async def agent_socket(socket: WebSocket, agent: Agent):
        await socket_loop(socket, agent)

    return app

app = create_app()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.main:app", host="127.0.0.1", port=8420, workers=1, access_log=False)
