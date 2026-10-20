import asyncio
import json
import time
from uuid import uuid4
import httpx
import pytest
from fastapi.testclient import TestClient
from backend.database import Database
from backend.main import create_app
from backend.models import MessageCreate, poll_timeout
from backend.security_guard import redact
from backend.ws_manager import WebSocketManager
from mcp_bridge import devmesh_mcp as bridge

SECRET = "sk-testsecret123456789"
BODY = {"sender": "codex", "recipient": "antigravity", "type": "proposal",
        "subject": "Review proposal", "content": "Plan", "requires_response": True}

async def test_wal_restart_and_ack(tmp_path):
    path = tmp_path / "durable.db"
    db = Database(path)
    await db.open()
    async with db.connection.execute("PRAGMA journal_mode") as cur:
        assert (await cur.fetchone())[0] == "wal"
    message = await db.create_message(MessageCreate(**BODY))
    assert message["id"] != message["conversation_id"]
    assert not (await db.inbox("antigravity"))[0]["delivered"]
    await db.close()
    db = Database(path)
    await db.open()
    assert (await db.inbox("antigravity"))[0] == message
    assert await db.acknowledge(message["id"], "codex") == (None, False)
    ack, changed = await db.acknowledge(message["id"], "antigravity")
    assert changed and ack["delivered"]
    assert not (await db.acknowledge(message["id"], "antigravity"))[1]
    assert await db.inbox("antigravity") == []
    await db.close()

def test_api_websockets_redaction_and_explicit_ack(tmp_path):
    path = tmp_path / "api.db"
    with TestClient(create_app(path)) as client:
        with client.websocket_connect("/ws/dashboard") as dashboard:
            with client.websocket_connect("/ws/agents/antigravity") as receiver:
                with client.websocket_connect("/ws/agents/codex") as sender:
                    data = client.post("/api/messages", json={**BODY, "content": SECRET}).json()
                    assert data["content"] == "[REDACTED]"
                    for ws in (dashboard, receiver, sender):
                        event = ws.receive_json()
                        assert event == {"event": "message.created", "data": data}
                        assert SECRET not in json.dumps(event)
                    assert client.get("/api/inbox/antigravity").json()["messages"] == [data]
                    assert client.get("/api/poll/antigravity?timeout_seconds=0").json()["messages"] == [data]
                    assert not client.get("/api/inbox/antigravity").json()["messages"][0]["delivered"]
                    assert client.post(f"/api/messages/{data['id']}/ack", json={"agent": "codex"}).status_code == 404
                    ack = client.post(f"/api/messages/{data['id']}/ack", json={"agent": "antigravity"}).json()
                    assert ack["delivered"]
                    for ws in (dashboard, receiver, sender):
                        assert ws.receive_json() == {"event": "message.acknowledged", "data": ack}
                    heartbeat = client.post("/api/agents/codex/heartbeat").json()
                    assert dashboard.receive_json()["data"] == heartbeat
                    assert sender.receive_json()["data"] == heartbeat
                    assert client.get("/api/inbox/antigravity").json() == {"messages": []}
                    start = time.monotonic()
                    assert client.get("/api/poll/antigravity?timeout_seconds=0.02").json() == {"status": "no_task"}
                    assert time.monotonic() - start < 1
                    invalid = client.post("/api/messages", json={**BODY, "recipient": SECRET})
                    assert invalid.status_code == 422 and SECRET not in invalid.text
                    assert client.get("/api/poll/codex?timeout_seconds=-1").status_code == 422
    assert SECRET.encode() not in path.read_bytes()

async def test_poll_wakes_and_cap(tmp_path, monkeypatch):
    app = create_app(tmp_path / "poll.db")
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            poll = asyncio.create_task(client.get("/api/poll/antigravity?timeout_seconds=1"))
            await asyncio.sleep(.01)
            message = (await client.post("/api/messages", json=BODY)).json()
            assert (await poll).json()["messages"] == [message]
            await client.post(f"/api/messages/{message['id']}/ack", json={"agent": "antigravity"})
            observed = []
            real_wait = asyncio.wait_for
            async def bounded_wait(awaitable, timeout):
                observed.append(timeout)
                return await real_wait(awaitable, .001)
            monkeypatch.setattr("backend.main.asyncio.wait_for", bounded_wait)
            assert (await client.get("/api/poll/antigravity?timeout_seconds=999")).json() == {"status": "no_task"}
            assert 44 < observed[0] <= 45

@pytest.mark.parametrize("secret", [
    SECRET, "AIza" + "a" * 30, "ghp_" + "b" * 20,
    "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ0ZXN0In0.signature",
    "-----BEGIN PRIVATE KEY-----\nprivate material\n-----END PRIVATE KEY-----",
    'DATABASE_URL="postgres://user:pass@localhost/db"',
    "export ANY_SETTING=private-value",
    'PASSWORD="first\nsecond"',
    '{"api_key": "unprefixed-private-value"}',
    "Authorization: Bearer unprefixed-value",
])
def test_redaction(secret):
    assert redact(secret) != secret
    assert "[REDACTED]" in redact(secret)

def test_nested_redaction_and_limits():
    assert redact({"api_key": "unknown", "content": SECRET}) == {"api_key": "[REDACTED]", "content": "[REDACTED]"}
    assert poll_timeout(999) == 45
    for value in (-1, float("nan"), float("inf")):
        with pytest.raises(ValueError):
            poll_timeout(value)

async def test_broadcast_routing_and_failed_client():
    class Socket:
        def __init__(self, fail=False):
            self.events = []
            self.fail = fail
        async def accept(self): pass
        async def close(self, code): pass
        async def send_json(self, event):
            if self.fail:
                raise OSError
            self.events.append(event)
    manager = WebSocketManager()
    dashboard, receiver, unrelated, broken = Socket(), Socket(), Socket(), Socket(True)
    for socket, agent in ((dashboard, None), (receiver, "antigravity"), (unrelated, "human"), (broken, None)):
        await manager.connect(socket, agent)
    await manager.broadcast({"content": SECRET}, {"antigravity"})
    assert dashboard.events == receiver.events == [{"content": "[REDACTED]"}]
    assert unrelated.events == [] and broken not in manager.clients

async def test_failed_persistence_does_not_broadcast(tmp_path, monkeypatch):
    app = create_app(tmp_path / "fail.db")
    async with app.router.lifespan_context(app):
        events = []
        async def fail(request):
            raise RuntimeError("storage unavailable")
        async def record(*args): events.append(args)
        monkeypatch.setattr(app.state.db, "create_message", fail)
        monkeypatch.setattr(app.state.ws, "broadcast", record)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test") as client:
            assert (await client.post("/api/messages", json=BODY)).status_code == 500
        assert events == []

async def test_bridge_thin_wrappers_and_timeout(monkeypatch):
    calls = []
    async def fake(method, path, **kwargs):
        calls.append((method, path, kwargs))
        return {"status": "no_task"}
    monkeypatch.setattr(bridge, "request", fake)
    assert await bridge.await_next_task("antigravity", 999) == {"status": "no_task"}
    assert calls[-1][2]["params"]["timeout_seconds"] == 45
    assert calls[-1][2]["timeout"] == 50
    await bridge.submit_proposal("codex", "antigravity", "subject", "plan")
    assert calls[-1][2]["payload"]["type"] == "proposal"
    await bridge.submit_review("antigravity", "codex", str(uuid4()), "review")
    assert calls[-1][2]["payload"]["type"] == "review"
    await bridge.read_agent_inbox("antigravity")
    assert calls[-1][0] == "GET"
    await bridge.acknowledge_message(uuid4(), "antigravity")
    assert calls[-1][1].endswith("/ack")

async def test_bridge_request_redacts_and_uses_rest(tmp_path, monkeypatch):
    app = create_app(tmp_path / "bridge.db")
    original_client = httpx.AsyncClient
    def client(**kwargs):
        return original_client(**kwargs, transport=httpx.ASGITransport(app=app))
    monkeypatch.setattr(bridge.httpx, "AsyncClient", client)
    async with app.router.lifespan_context(app):
        message = await bridge.submit_proposal("codex", "antigravity", "review", SECRET)
        assert message["content"] == "[REDACTED]"
        assert (await bridge.await_next_task("antigravity", 0))["messages"] == [message]
        assert (await bridge.read_agent_inbox("antigravity"))["messages"] == [message]
        await bridge.acknowledge_message(message["id"], "antigravity")
        assert await bridge.await_next_task("antigravity", .001) == {"status": "no_task"}

async def test_bridge_stdio_discovery_and_safe_validation():
    import os
    import sys
    from pathlib import Path
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    params = StdioServerParameters(command=sys.executable,
        args=[str(Path(__file__).resolve().parents[1] / "mcp_bridge" / "devmesh_mcp.py")])
    with open(os.devnull, "w") as errlog:
        async with stdio_client(params, errlog=errlog) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                names = {tool.name for tool in (await session.list_tools()).tools}
                assert names == {"send_agent_message", "read_agent_inbox", "await_next_task",
                                 "acknowledge_message", "submit_proposal", "submit_review",
                                 "list_open_decisions", "submit_decision_proposal", "submit_critique", "get_project_context", "update_task_status", "get_assignment_context", "report_project_result", "report_project_usage"}
                result = await session.call_tool("send_agent_message",
                    {"sender": SECRET, "recipient": "antigravity", "content": SECRET})
                assert result.isError
                assert SECRET not in result.model_dump_json()

async def test_real_loopback_http_and_websocket(tmp_path):
    import socket
    import uvicorn
    from websockets.asyncio.client import connect
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(create_app(tmp_path / "network.db"),
                           host="127.0.0.1", port=port, log_level="error",
                           access_log=False, ws="websockets-sansio"))
    task = asyncio.create_task(server.serve(sockets=[listener]))
    try:
        for _ in range(200):
            if server.started:
                break
            if task.done():
                await task
            await asyncio.sleep(.01)
        assert server.started
        async with connect(f"ws://127.0.0.1:{port}/ws/dashboard") as ws:
            async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", trust_env=False) as client:
                response = await client.post("/api/messages", json={**BODY, "content": SECRET})
                assert response.status_code == 201
                event = json.loads(await asyncio.wait_for(ws.recv(), 2))
                assert event["data"] == response.json()
                assert event["data"]["content"] == "[REDACTED]"
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 5)
        listener.close()
