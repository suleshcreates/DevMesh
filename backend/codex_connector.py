"""Bounded CLI connections; fallback is permitted only before turn submission."""
import asyncio
from collections import deque
from dataclasses import dataclass, field
import json
from pathlib import Path
import shutil
import time
from backend.security_guard import redact
from scripts.spike_codex import discard, shutdown, wait_for_exit

MODEL = "gpt-6-astra"

class ConnectorError(RuntimeError):
    def __init__(self, category, uncertain=False):
        super().__init__(category)
        self.category = category
        self.uncertain = uncertain

@dataclass
class PromptResult:
    method: str
    thread_id: str | None = None
    turn_id: str | None = None
    content: str = ""
    streamed: bool = False
    cleanup_warnings: list[str] = field(default_factory=list)

class CodexAppServerClient:
    def __init__(self, cwd=None, timeout=90, codex=None, spawn=None):
        if not 0 < timeout <= 600:
            raise ValueError("Timeout must be in (0, 600]")
        self.cwd = Path(cwd or Path(__file__).resolve().parents[1])
        self.timeout = timeout
        self.codex = codex or shutil.which("codex") or "codex"
        self.spawn = spawn or asyncio.create_subprocess_exec
        self.lock = asyncio.Lock()
        self.cleanup_warnings = []

    async def send_prompt(self, prompt, thread_id=None):
        # Each invocation owns its process and closes it before returning.
        async with self.lock:
            self.cleanup_warnings = []
            try:
                return await self._app_server(redact(prompt), thread_id)
            except ConnectorError as exc:
                if exc.uncertain or thread_id:
                    raise
                return await self._exec(redact(prompt))

    async def _launch(self, *args, stdin=False):
        try:
            return await asyncio.wait_for(self.spawn(
                self.codex, *args, cwd=self.cwd,
                stdin=asyncio.subprocess.PIPE if stdin else asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                limit=4 * 1024 * 1024), self.timeout)
        except (OSError, TimeoutError):
            raise ConnectorError("launch_failed") from None

    async def _app_server(self, prompt, thread_id):
        process = await self._launch("app-server", "--listen", "stdio://", stdin=True)
        stderr_task = asyncio.create_task(discard(process.stderr))
        started = False
        result = PromptResult("app-server", thread_id)
        pending = deque()
        deadline = time.monotonic() + self.timeout

        async def send(value):
            process.stdin.write((json.dumps(value) + "\n").encode())
            await asyncio.wait_for(process.stdin.drain(), max(.001, deadline - time.monotonic()))

        async def receive():
            line = await asyncio.wait_for(process.stdout.readline(), max(.001, deadline - time.monotonic()))
            if not line:
                raise EOFError
            msg = json.loads(line)
            if "id" in msg and "method" in msg:
                await send({"id": msg["id"], "error": {"code": -32601, "message": "Unsupported request"}})
            return msg

        async def request(number, method, params):
            await send({"id": number, "method": method, "params": params})
            while True:
                msg = await receive()
                if msg.get("id") == number and "method" not in msg:
                    if "error" in msg:
                        raise ConnectorError("rpc_failed", started)
                    return msg["result"]
                pending.append(msg)

        try:
            await request(1, "initialize", {"clientInfo": {"name": "devmesh", "version": "0.1.0"}})
            await send({"method": "initialized", "params": {}})
            params = {"cwd": str(self.cwd), "sandbox": "read-only", "approvalPolicy": "never", "model": MODEL}
            if thread_id:
                params["threadId"] = thread_id
            thread = await request(2, "thread/resume" if thread_id else "thread/start", params)
            result.thread_id = thread["thread"]["id"]
            # Mark BEFORE writing: even a failed drain may have submitted the prompt.
            started = True
            turn = await request(3, "turn/start", {"threadId": result.thread_id,
                                "input": [{"type": "text", "text": prompt}]})
            result.turn_id = turn["turn"]["id"]
            deltas = {}
            finals = {}
            while True:
                msg = pending.popleft() if pending else await receive()
                p = msg.get("params", {})
                if p.get("threadId") != result.thread_id:
                    continue
                if p.get("turnId", result.turn_id) != result.turn_id:
                    continue
                method = msg.get("method")
                if method == "item/agentMessage/delta":
                    result.streamed = True
                    key = p.get("itemId", "response")
                    deltas[key] = deltas.get(key, "") + p.get("delta", "")
                if method == "item/completed" and p.get("item", {}).get("type") == "agentMessage":
                    item = p["item"]
                    finals[item.get("id", "response")] = item.get("text", "")
                if method == "turn/completed" and p["turn"]["id"] == result.turn_id:
                    if p["turn"]["status"] != "completed":
                        raise ConnectorError("turn_failed", True)
                    # Buffer before exposing: secrets may cross streaming boundaries.
                    result.content = redact("\n".join(finals.values()) if finals else "".join(deltas.values()))
                    return result
        except asyncio.CancelledError:
            raise
        except ConnectorError:
            raise
        except (TimeoutError, EOFError, ValueError, KeyError, TypeError, OSError):
            raise ConnectorError("connection_failed_or_timeout", started) from None
        finally:
            result.cleanup_warnings = await shutdown(process, stderr_task)
            self.cleanup_warnings.extend(result.cleanup_warnings)

    async def _exec(self, prompt):
        process = await self._launch("exec", "--json", "--sandbox", "read-only",
                                     "-C", str(self.cwd), "--model", MODEL, prompt)
        stderr_task = asyncio.create_task(discard(process.stderr))
        result = PromptResult("exec")
        deadline = time.monotonic() + self.timeout
        parts = []
        try:
            while True:
                line = await asyncio.wait_for(process.stdout.readline(), max(.001, deadline - time.monotonic()))
                if not line:
                    raise EOFError
                event = json.loads(line)
                kind = event.get("type")
                if kind == "thread.started":
                    result.thread_id = event["thread_id"]
                if kind in ("turn.failed", "error"):
                    raise ConnectorError("exec_failed", True)
                if kind == "item.completed" and event.get("item", {}).get("type") == "agent_message":
                    parts.append(event["item"].get("text", ""))
                if kind == "turn.completed":
                    if await wait_for_exit(process, max(.001, deadline - time.monotonic())) != 0:
                        raise ConnectorError("exec_failed", True)
                    result.content = redact("\n".join(parts))
                    return result
        except asyncio.CancelledError:
            raise
        except ConnectorError:
            raise
        except (TimeoutError, EOFError, ValueError, KeyError, TypeError, OSError):
            raise ConnectorError("exec_failed_or_timeout", True) from None
        finally:
            result.cleanup_warnings = await shutdown(process, stderr_task)
            self.cleanup_warnings.extend(result.cleanup_warnings)
