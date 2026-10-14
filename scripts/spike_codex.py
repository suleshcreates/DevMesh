"""Exercise two installed Codex interfaces, recording only safe, selected fields.

No credential files or environment values are inspected. Child processes inherit
the environment. Stdout is decoded in memory; raw protocol/config data is never
printed or saved. Only the expected ACK, event names and identifiers are retained.
"""
import argparse
import asyncio
from contextlib import suppress
import json
import os
from pathlib import Path
import shutil
import time

ROOT = Path(__file__).resolve().parents[1]
ACK = "ACK_CODEX"
PROMPT = "Reply with ACK_CODEX. Do not use tools or read any files."


def classify_error(value) -> str:
    """Return fixed categories only; never retain upstream error strings."""
    text = str(value).lower()
    if any(word in text for word in ("usage limit", "quota", "rate limit", "429")):
        return "usage_limit"
    if any(word in text for word in ("unauthorized", "authentication", "access token", "refresh token", "401", "sign in")):
        return "authentication"
    if any(word in text for word in ("not supported", "model not found", "does not exist")):
        return "model_unavailable"
    if any(word in text for word in ("connection", "network", "timed out", "dns", "stream disconnected")):
        return "network"
    return "unknown_upstream_error"


async def discard(stream):
    while await stream.read(65536):
        pass


async def shutdown(process, stderr_task):
    stderr_task.cancel()
    with suppress(asyncio.CancelledError):
        await stderr_task
    if process.returncode is None:
        if os.name == "nt":
            # Stop our process tree before EOF can orphan launcher children.
            killer = await asyncio.create_subprocess_exec(
                "taskkill", "/PID", str(process.pid), "/T", "/F",
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
            await asyncio.wait_for(killer.wait(), 5)
        else:
            process.terminate()
    try:
        await asyncio.wait_for(process.communicate(), 5)
    except TimeoutError:
        if process.returncode is None:
            process.kill()
        await asyncio.wait_for(process.communicate(), 5)
    await asyncio.sleep(.1)


async def app_server(codex: str, timeout: float, thread_id: str | None = None, model: str | None = None) -> dict:
    result = {"method": "app-server", "status": "partial", "events": [],
              "thread_id": thread_id, "ack": False, "streamed": False}
    process = await asyncio.create_subprocess_exec(
        codex, "app-server", "--listen", "stdio://", cwd=ROOT,
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE, limit=4 * 1024 * 1024)
    stderr_task = asyncio.create_task(discard(process.stderr))
    deadline = time.monotonic() + timeout

    async def send(payload):
        process.stdin.write((json.dumps(payload) + "\n").encode("utf-8"))
        await process.stdin.drain()

    async def receive():
        line = await asyncio.wait_for(process.stdout.readline(), max(.001, deadline - time.monotonic()))
        if not line:
            raise EOFError
        msg = json.loads(line)
        method = msg.get("method")
        if method and method not in result["events"]:
            result["events"].append(method)
        if method and "id" in msg:
            # A spike cannot grant approvals or supply tokens/elicitation.
            await send({"id": msg["id"], "error": {"code": -32601, "message": "Unsupported by ACK-only spike"}})
            result["server_request_rejected"] = True
        return msg

    async def request(id_, method, params):
        await send({"id": id_, "method": method, "params": params})
        while True:
            msg = await receive()
            if msg.get("id") == id_ and "method" not in msg:
                if "error" in msg:
                    result["rpc_error_code"] = msg["error"].get("code")
                    raise RuntimeError("RPC rejected")
                return msg["result"]

    try:
        await request(1, "initialize", {"clientInfo": {"name": "devmesh_spike", "version": "0.0.1"}})
        await send({"method": "initialized", "params": {}})
        result["initialized"] = True
        params = {"cwd": str(ROOT), "sandbox": "read-only", "approvalPolicy": "never"}
        if model:
            params["model"] = model
        if thread_id:
            params["threadId"] = thread_id
        thread = await request(2, "thread/resume" if thread_id else "thread/start", params)
        result["thread_id"] = thread["thread"]["id"]
        turn = await request(3, "turn/start", {"threadId": result["thread_id"],
                             "input": [{"type": "text", "text": PROMPT}]})
        result["turn_id"] = turn["turn"]["id"]
        deltas = ""
        final = ""
        while True:
            msg = await receive()
            p = msg.get("params", {})
            if p.get("threadId") != result["thread_id"]:
                continue
            if msg.get("method") == "error":
                result["error_category"] = classify_error(p.get("error"))
            if msg.get("method") == "item/agentMessage/delta":
                result["streamed"] = True
                deltas += p.get("delta", "")
            if msg.get("method") == "item/completed" and p.get("item", {}).get("type") == "agentMessage":
                final = p["item"].get("text", "")
            if msg.get("method") == "turn/completed":
                result["turn_status"] = p["turn"]["status"]
                if p["turn"].get("error"):
                    result["error_category"] = classify_error(p["turn"]["error"])
                result["ack"] = (final or deltas).strip() == ACK
                if result["turn_status"] == "completed" and result["ack"]:
                    result["status"] = "works"
                break
    except (TimeoutError, EOFError, RuntimeError, ValueError, KeyError) as exc:
        result["failure"] = type(exc).__name__
    finally:
        await shutdown(process, stderr_task)
    return result


async def exec_spike(codex: str, timeout: float, model: str | None = None) -> dict:
    result = {"method": "exec", "status": "partial", "events": [], "ack": False}
    command = [codex, "exec", "--json", "--sandbox", "read-only", "-C", str(ROOT)]
    if model:
        command.extend(["--model", model])
    process = await asyncio.create_subprocess_exec(
        *command, PROMPT,
        cwd=ROOT, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        limit=4 * 1024 * 1024)
    stderr_task = asyncio.create_task(discard(process.stderr))
    deadline = time.monotonic() + timeout
    try:
        while True:
            line = await asyncio.wait_for(process.stdout.readline(), max(.001, deadline - time.monotonic()))
            if not line:
                break
            event = json.loads(line)
            kind = event.get("type")
            if kind and kind not in result["events"]:
                result["events"].append(kind)
            if kind == "thread.started":
                result["thread_id"] = event.get("thread_id")
            if kind in ("error", "turn.failed"):
                result["error_category"] = classify_error(event.get("error", event.get("message")))
            if kind == "turn.failed":
                result["turn_failed"] = True
                break
            if kind == "item.completed" and event.get("item", {}).get("type") == "agent_message":
                result["ack"] = event["item"].get("text", "").strip() == ACK
            if kind == "turn.completed":
                result["turn_completed"] = True
        if not result.get("turn_failed"):
            await asyncio.wait_for(process.wait(), max(.001, deadline - time.monotonic()))
            result["exit_code"] = process.returncode
        if process.returncode == 0 and result["ack"] and result.get("turn_completed"):
            result["status"] = "works"
    except (TimeoutError, ValueError) as exc:
        result["failure"] = type(exc).__name__
    finally:
        await shutdown(process, stderr_task)
    return result


async def main(args):
    codex = shutil.which("codex")
    if not codex:
        raise SystemExit("codex is not on PATH")
    results = []
    if args.method in ("both", "app-server"):
        results.append(await app_server(codex, args.timeout, args.thread_id, args.model))
    if args.method in ("both", "exec"):
        results.append(await exec_spike(codex, args.timeout, args.model))
    report = {"results": results}
    rendered = json.dumps(report, indent=2)
    print(rendered)
    if args.output:
        output = args.output.resolve()
        if not output.is_relative_to(ROOT):
            raise SystemExit("Output must be inside the repository")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered + "\n", encoding="utf-8")
    return 0 if all(r["status"] == "works" for r in results) else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--method", choices=["both", "app-server", "exec"], default="both")
    parser.add_argument("--timeout", type=float, default=90)
    parser.add_argument("--thread-id", help="Resume an existing app-server thread")
    parser.add_argument("--model", help="Explicit CLI model override; omit to inherit the CLI default")
    parser.add_argument("--output", type=Path)
    options = parser.parse_args()
    if not 0 < options.timeout <= 600:
        parser.error("timeout must be greater than zero and no more than 600 seconds")
    raise SystemExit(asyncio.run(main(options)))
