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

from backend.decision_engine import DecisionEngine, DecisionError
from backend.decision_models import Proposal, CritiqueRequest, HumanResolution, MemorySource
from backend.memory_manager import MemoryManager

ROOT = Path(__file__).resolve().parents[1]

def create_app(db_path=None, repository=None, test_command=None):
    @asynccontextmanager
    async def lifespan(app):
        app.state.db = Database(db_path or ROOT / ".devmesh" / "devmesh.sqlite3")
        app.state.ws = WebSocketManager()
        app.state.changed = asyncio.Condition()
        await app.state.db.open()
        app.state.decisions = DecisionEngine(app.state.db)
        await app.state.decisions.initialize()
        from backend.task_planner import TaskPlanner
        from backend.security_guard import ApprovalGuard
        from backend.review_runner import ReviewRunner
        app.state.tasks = TaskPlanner(app.state.db)
        await app.state.tasks.initialize()
        app.state.guard = ApprovalGuard(app.state.db)
        await app.state.guard.initialize()
        app.state.runner = ReviewRunner(app.state.tasks, app.state.guard, repository or ROOT, test_command)
        await app.state.runner.recover()
        app.state.memory = MemoryManager(app.state.db, app.state.db.path.parent)
        await app.state.memory.regenerate()
        try:
            yield
        finally:
            await app.state.db.close()

    app = FastAPI(lifespan=lifespan)
    from fastapi.middleware.cors import CORSMiddleware
    app.add_middleware(CORSMiddleware, allow_origins=['http://localhost:5173'],
                       allow_methods=['GET', 'POST'], allow_headers=['Content-Type'])
    from backend.dashboard_routes import register as register_dashboard
    register_dashboard(app)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        # Pydantic errors include input values by default: do not echo them.
        return JSONResponse(status_code=422, content={"detail": "Invalid request"})

    async def publish(kind, data, agents):
        await app.state.ws.broadcast({"event": kind, "data": data}, agents)

    @app.exception_handler(DecisionError)
    async def decision_error(request, exc):
        return JSONResponse(status_code=exc.code, content={"detail": exc.detail})

    async def mutate_decision(action, request, decision_id=None):
        async with app.state.changed:
            data, messages = await app.state.decisions.mutate(action, request, decision_id)
            app.state.changed.notify_all()
        for message in messages:
            await publish("message.created", message, {message["recipient"]})
        if messages:
            await publish(messages[0]["subject"], data, {"codex", "antigravity", "human"})
        if data["state"] == "RECORD":
            await app.state.memory.regenerate()
        return data

    @app.post("/api/decisions/propose", status_code=201)
    async def propose(request: Proposal):
        return await mutate_decision("propose", request)

    @app.get("/api/decisions")
    async def decisions(open_only: bool = True):
        return {"decisions": await app.state.decisions.list(open_only)}

    @app.get("/api/decisions/{decision_id}")
    async def decision(decision_id: str):
        return await app.state.decisions.get(decision_id)

    @app.post("/api/decisions/{decision_id}/critique")
    async def critique(decision_id: str, request: CritiqueRequest):
        return await mutate_decision("critique", request, decision_id)

    @app.post("/api/decisions/{decision_id}/human-resolve")
    async def human_resolve(decision_id: str, request: HumanResolution):
        return await mutate_decision("human", request, decision_id)

    @app.get("/api/context/{doc_name}")
    async def context(doc_name: str):
        try:
            return await app.state.memory.context(doc_name)
        except ValueError:
            raise HTTPException(422, "Unsupported document name") from None

    @app.put("/api/context/{doc_name}/source")
    async def source(doc_name: str, request: MemorySource):
        try:
            await app.state.memory.set_source(doc_name, request.content)
            result = await app.state.memory.context(doc_name)
            await publish("memory.source_updated", result, {"codex", "antigravity", "human"})
            return result
        except ValueError:
            raise HTTPException(422, "Unsupported source document") from None

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

    from backend.task_routes import register
    register(app)
    return app

import os
import json
app = create_app(db_path=os.environ.get('DEVMESH_DATABASE'),
                 repository=os.environ.get('DEVMESH_EXECUTION_REPOSITORY'),
                 test_command=json.loads(os.environ['DEVMESH_TEST_COMMAND']) if os.environ.get('DEVMESH_TEST_COMMAND') else None)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.main:app", host="127.0.0.1", port=8420, workers=1, access_log=False)
