"""Deterministic adapters: no real Codex calls or authentication."""
import asyncio
import json
from types import SimpleNamespace
import pytest
from backend.codex_connector import CodexAppServerClient, ConnectorError, MODEL

class Reader:
    def __init__(self, events=(), hang=False):
        self.events = list(events)
        self.hang = hang
    async def readline(self):
        if self.events:
            return (json.dumps(self.events.pop(0)) + "\n").encode()
        if self.hang:
            await asyncio.sleep(20)
        return b""
    async def read(self, size):
        return b""

class Process:
    def __init__(self, events=(), hang=False, running=False):
        self.stdout = Reader(events, hang)
        self.stderr = Reader()
        self.returncode = None if running else 0
        self.closed = False
        self.sent = []
        self.pid = 12345
        self.stdin = SimpleNamespace(write=self.write, drain=self.drain)
        self._transport = SimpleNamespace(close=self.close)
    def write(self, data): self.sent.append(json.loads(data))
    async def drain(self): pass
    def close(self):
        self.closed = True
        self.returncode = 0

def events(status="completed", early=False):
    start = [{"id": 1, "result": {}}, {"id": 2, "result": {"thread": {"id": "thread"}}}]
    deltas = [{"method": "item/agentMessage/delta", "params": {"threadId": "thread", "turnId": "turn", "itemId": "item", "delta": s}}
              for s in ("sk-test", "secret123456789")]
    turn = {"id": 3, "result": {"turn": {"id": "turn"}}}
    return start + (deltas + [turn] if early else [turn] + deltas) + [
        {"method": "turn/completed", "params": {"threadId": "thread", "turn": {"id": "turn", "status": status}}}]

async def test_streamed_response_model_and_cleanup():
    process = Process(events(early=True))
    calls = []
    async def spawn(*args, **kwargs):
        calls.append((args, kwargs))
        return process
    client = CodexAppServerClient(spawn=spawn)
    result = await client.send_prompt("Plan")
    assert result.content == "[REDACTED]" and result.streamed
    assert (result.thread_id, result.turn_id) == ("thread", "turn")
    assert process.closed and result.cleanup_warnings == []
    assert process.sent[2]["params"]["model"] == MODEL
    assert "env" not in calls[0][1]

@pytest.mark.parametrize("failure", ["timeout", "eof", "turn_failed", "rpc"])
async def test_no_fallback_after_submission(failure):
    data = events("failed") if failure == "turn_failed" else events()[:2]
    if failure == "rpc":
        data.append({"id": 3, "error": {"message": "sk-private-material"}})
    process = Process(data, hang=failure == "timeout")
    calls = []
    async def spawn(*args, **kwargs):
        calls.append(args)
        return process
    client = CodexAppServerClient(spawn=spawn, timeout=.03)
    with pytest.raises(ConnectorError) as error:
        await client.send_prompt("Plan")
    assert error.value.uncertain and len(calls) == 1 and process.closed
    assert "private" not in str(error.value)

async def test_pre_submission_failure_falls_back_explicit_model():
    app = Process([{"id": 1, "error": {"message": "private upstream error"}}])
    fallback = Process([
        {"type": "thread.started", "thread_id": "exec-thread"},
        {"type": "item.completed", "item": {"type": "agent_message", "text": "response"}},
        {"type": "turn.completed"}])
    calls = []
    async def spawn(*args, **kwargs):
        calls.append(args)
        return app if len(calls) == 1 else fallback
    result = await CodexAppServerClient(spawn=spawn).send_prompt("Plan")
    assert result.method == "exec" and result.thread_id == "exec-thread"
    assert calls[1][calls[1].index("--model") + 1] == MODEL
    assert app.closed and fallback.closed

async def test_resume_does_not_silently_fork_on_failure():
    process = Process([])
    calls = []
    async def spawn(*args, **kwargs):
        calls.append(args)
        return process
    with pytest.raises(ConnectorError):
        await CodexAppServerClient(spawn=spawn).send_prompt("continue", thread_id="existing")
    assert len(calls) == 1 and process.closed

async def test_exec_timeout_no_retry():
    process = Process([], hang=True)
    calls = []
    async def spawn(*args, **kwargs):
        calls.append(args)
        if len(calls) == 1:
            raise FileNotFoundError
        return process
    with pytest.raises(ConnectorError) as error:
        await CodexAppServerClient(spawn=spawn, timeout=.02).send_prompt("plan")
    assert error.value.uncertain and len(calls) == 2 and process.closed

async def test_cancellation_closes_transport():
    process = Process(events()[:2], hang=True)
    async def spawn(*args, **kwargs): return process
    task = asyncio.create_task(CodexAppServerClient(spawn=spawn).send_prompt("plan"))
    await asyncio.sleep(.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert process.closed

async def test_windows_cleanup_tree_and_transport(monkeypatch):
    from scripts import spike_codex
    process = Process(running=True)
    killer = Process()
    commands = []
    async def spawn(*args, **kwargs):
        commands.append(args)
        process.returncode = 1
        return killer
    monkeypatch.setattr(spike_codex, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(spike_codex.asyncio, "create_subprocess_exec", spawn)
    warnings = await spike_codex.shutdown(process, asyncio.create_task(spike_codex.discard(process.stderr)))
    assert commands == [("taskkill", "/PID", "12345", "/T", "/F")]
    assert process.closed and killer.closed and warnings == []

async def test_resume_protocol_success():
    process = Process(events())
    async def spawn(*args, **kwargs): return process
    await CodexAppServerClient(spawn=spawn).send_prompt("continue", thread_id="existing")
    assert process.sent[2]["method"] == "thread/resume"
    assert process.sent[2]["params"]["threadId"] == "existing"
    assert process.sent[2]["params"]["model"] == MODEL

async def test_windows_cleanup_failure_still_closes(monkeypatch):
    from scripts import spike_codex
    process = Process(running=True)
    async def spawn(*args, **kwargs): raise OSError("private upstream material")
    monkeypatch.setattr(spike_codex, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(spike_codex.asyncio, "create_subprocess_exec", spawn)
    warnings = await spike_codex.shutdown(process, asyncio.create_task(spike_codex.discard(process.stderr)))
    assert warnings == ["tree_termination_failed"] and process.closed

async def test_exec_failed_turn_is_safe():
    process = Process([{"type": "turn.failed", "error": {"message": "private upstream material"}}])
    calls = []
    async def spawn(*args, **kwargs):
        calls.append(args)
        if len(calls) == 1:
            raise OSError
        return process
    with pytest.raises(ConnectorError) as error:
        await CodexAppServerClient(spawn=spawn).send_prompt("plan")
    assert str(error.value) == "exec_failed"
    assert len(calls) == 2 and process.closed
