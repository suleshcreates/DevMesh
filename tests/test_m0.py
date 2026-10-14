import asyncio
from datetime import timedelta
import json
import os
from pathlib import Path
import sys
import time

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from scripts import spike_mcp
from scripts.spike_codex import classify_error
from scripts.setup_antigravity_mcp import register

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("message,expected", [
    ("429 usage limit", "usage_limit"),
    ("refresh token invalid", "authentication"),
    ("stream disconnected", "network"),
    ("unexpected private material", "unknown_upstream_error"),
])
def test_error_classification_retains_only_fixed_categories(message, expected):
    assert classify_error(message) == expected


async def test_stdio_discovery_ping_and_repeated_poll():
    params = StdioServerParameters(command=sys.executable,
                                   args=[str(ROOT / "scripts" / "spike_mcp.py")])
    with open(os.devnull, "w") as errlog:
        async with stdio_client(params, errlog=errlog) as (read, write):
            async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=60)) as session:
                await session.initialize()
                tools = await session.list_tools()
                assert {t.name for t in tools.tools} == {"ping", "await_next_task"}
                assert (await session.call_tool("ping")).structuredContent == {"status": "pong"}
                # Exercise the default 45-second timeout over a real stdio session.
                start = time.monotonic()
                result = await session.call_tool("await_next_task", {"agent_name": "antigravity"})
                assert 44 <= time.monotonic() - start < 55
                assert not result.isError
                assert result.structuredContent == {"status": "no_task"}
                # A client can repeat the tool call; IDE agent autonomy is a separate gate.
                for _ in range(2):
                    result = await session.call_tool("await_next_task", {
                        "agent_name": "antigravity", "timeout_seconds": .05})
                    assert result.structuredContent == {"status": "no_task"}


async def test_timeout_cap(monkeypatch):
    waits = []

    async def observe_sleep(delay):
        waits.append(delay)

    monkeypatch.setattr(spike_mcp.asyncio, "sleep", observe_sleep)
    assert await spike_mcp.await_next_task("antigravity", 999) == {"status": "no_task"}
    assert waits == [45]


@pytest.mark.parametrize("timeout", [-1, float("nan"), float("inf")])
async def test_invalid_timeout(timeout):
    with pytest.raises(ValueError):
        await spike_mcp.await_next_task("antigravity", timeout)


async def test_blank_agent():
    with pytest.raises(ValueError):
        await spike_mcp.await_next_task(" ", 0)


async def test_poll_cancellation():
    task = asyncio.create_task(spike_mcp.await_next_task("antigravity"))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


def test_registration_refuses_existing_config(tmp_path):
    (tmp_path / ".venv" / "Scripts").mkdir(parents=True)
    (tmp_path / ".venv" / "Scripts" / "python.exe").touch()
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "spike_mcp.py").touch()
    path = register(tmp_path)
    expected = {"mcpServers": {"devmesh-bridge": {
        "command": str(tmp_path / ".venv" / "Scripts" / "python.exe"),
        "args": [str(tmp_path / "scripts" / "spike_mcp.py")]}}}
    assert json.loads(path.read_text(encoding="utf-8")) == expected
    before = path.read_bytes()
    with pytest.raises(FileExistsError):
        register(tmp_path)
    assert path.read_bytes() == before
