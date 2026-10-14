"""Regression: CLI exit and turn completion must not wait for inherited pipe EOF."""
import asyncio
import json
import sys
import time

import pytest

from scripts import spike_codex


async def test_success_with_descendant_holding_stdout(monkeypatch):
    # The descendant inherits both pipe handles after the parent exits with 0.
    # It exits itself after 3 seconds, so this test leaves no persistent process.
    source = "\n".join([
        "import json, subprocess, sys",
        "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(3)'])",
        "print(json.dumps({'type':'thread.started','thread_id':'test-thread'}), flush=True)",
        "print(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':'ACK_CODEX'}}), flush=True)",
        "print(json.dumps({'type':'turn.completed'}), flush=True)",
    ])
    create = asyncio.create_subprocess_exec

    async def launch(*args, **kwargs):
        if args[0] == "test-codex":
            return await create(sys.executable, "-u", "-c", source, **kwargs)
        return await create(*args, **kwargs)

    monkeypatch.setattr(spike_codex.asyncio, "create_subprocess_exec", launch)
    start = time.monotonic()
    result = await spike_codex.exec_spike("test-codex", 1.5, "verified-model")
    assert time.monotonic() - start < 2.5
    assert result["status"] == "works"
    assert result["ack"]
    assert result["exit_code"] == 0
    assert result["cleanup_warnings"] == []


async def test_failed_turn_still_returns_report(monkeypatch):
    source = "import json; print(json.dumps({'type':'turn.failed','error':{'message':'usage limit'}}),flush=True)"
    create = asyncio.create_subprocess_exec

    async def launch(*args, **kwargs):
        if args[0] == "test-codex":
            return await create(sys.executable, "-u", "-c", source, **kwargs)
        return await create(*args, **kwargs)

    monkeypatch.setattr(spike_codex.asyncio, "create_subprocess_exec", launch)
    result = await spike_codex.exec_spike("test-codex", 2)
    assert result["status"] == "partial"
    assert result["turn_failed"]
    assert result["error_category"] == "usage_limit"
    assert not result["ack"]


async def test_hung_child_is_stopped_with_bounded_cleanup():
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-c", "import time; time.sleep(30)",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    stderr_task = asyncio.create_task(spike_codex.discard(process.stderr))
    start = time.monotonic()
    warnings = await spike_codex.shutdown(process, stderr_task)
    assert time.monotonic() - start < 7.5
    assert process.returncode is not None
    assert process._transport.is_closing()
    assert warnings == []
