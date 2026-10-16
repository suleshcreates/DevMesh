# Milestone 1: local messaging and CLI connector

Implemented in C:\DevMesh only. The separate OneDrive staging copy and all
existing MCP configurations are untouched. M0 scripts and regression tests are
preserved. No frontend, decision engine, decision endpoints, orchestrator, or
task execution is included. Posting a message does not invoke Codex.

## Start and test (PowerShell)

```powershell
Set-Location C:\DevMesh
.\.venv\Scripts\python.exe -m pip install -r requirements-m1.lock.txt
.\.venv\Scripts\python.exe -m backend.main
```

API: http://127.0.0.1:8420. Stop with Ctrl+C. Use one process/worker; long-poll
wakeups and WebSocket subscriptions are process-local. In another terminal:

```powershell
Set-Location C:\DevMesh
.\.venv\Scripts\python.exe -m pytest -q
```

SQLite WAL data lives in .devmesh/devmesh.sqlite3 (ignored by Git). Messages
survive shutdown and restart. Tests use temporary databases and deterministic
fake Codex processes; they do not call real agents. The preserved M0 stdio test
intentionally waits about 45 seconds. If the desktop sandbox blocks Windows
asyncio loopback creation, run these commands in ordinary PowerShell.

## Antigravity: manually replace the staging registration

Open the active MCP configuration through Antigravity's own MCP settings.
Locate the existing devmesh-bridge entry under mcpServers and replace that entry
only with the following. Preserve all other entries. Do not copy configuration
contents into chat, logs, or this repository. This implementation does not read,
parse, or modify any existing configuration or credential file.

```json
"devmesh-bridge": {
  "command": "C:\\DevMesh\\.venv\\Scripts\\python.exe",
  "args": ["C:\\DevMesh\\mcp_bridge\\devmesh_mcp.py"]
}
```

The snippet is an entry inside the existing mcpServers object, not a complete
replacement for the config file. Do not run the M0 setup script to register M1:
it intentionally still targets the M0 spike. Refresh/restart the MCP server in
Antigravity after replacement. The six M1 tools should appear:
send_agent_message, read_agent_inbox, await_next_task, acknowledge_message,
submit_proposal, submit_review. M1 does not expose the spike's ping tool.

## Manual end-to-end verification (not an automated test result)

1. Start the API and replace/refresh the registration as above.
2. Create a real manual verification proposal from PowerShell:

```powershell
$body = @{
  sender = 'codex'
  recipient = 'antigravity'
  type = 'proposal'
  subject = 'Milestone 1 manual verification'
  content = 'Please review the Milestone 1 messaging connection.'
  requires_response = $true
} | ConvertTo-Json
$message = Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:8420/api/messages' -ContentType 'application/json' -Body $body
$message
```

3. Ask Antigravity to call read_agent_inbox(agent_name="antigravity"), then
   await_next_task(agent_name="antigravity", timeout_seconds=45). Require actual
   tool invocations and verify both contain the returned message ID and
   delivered=false. Neither tool acknowledges it.
4. Stop/restart the API. Read the inbox again; the same ID must remain pending.
5. Ask Antigravity to call acknowledge_message with that actual message ID and
   agent_name="antigravity". It must return delivered=true. Read again; the
   message must no longer be in the pending inbox. Acknowledge any other pending
   verification messages before checking empty polling.
6. Ask for three successive await_next_task calls with agent_name="antigravity"
   and timeout_seconds=45. Each empty call must return {"status":"no_task"}.
   Inspect actual tool history; do not accept a claim without invocations.
7. Ask Antigravity to submit_review with sender="antigravity", recipient="codex",
   the proposal's actual conversation_id and its review text. Verify with
   GET /api/inbox/codex. Reviews are messages only; they do not approve decisions.
8. If Antigravity stops polling, manually reactivate it with a prompt.

Optional real Codex checks are separate from pytest:

```powershell
Set-Location C:\DevMesh
.\.venv\Scripts\python.exe scripts\spike_codex.py --model gpt-6-astra --timeout 90
.\.venv\Scripts\python.exe -c "import asyncio; from backend.codex_connector import CodexAppServerClient; r=asyncio.run(CodexAppServerClient().send_prompt('Reply with ACK_CODEX. Do not use tools or read files.')); print(r)"
```

The connector uses the M0 newline-JSON app-server protocol with explicit
gpt-6-astra and the preserved Windows shutdown implementation. It accepts
thread_id and uses the committed ThreadResumeParams schema for thread/resume.
Real cross-process resume remains unverified. No new CLI flags are introduced.
Exec fallback uses the proven one-shot flags and the same model; it is attempted
only before any turn/start write, and never when resuming an existing thread.
After submission, failure/timeout reports an uncertain outcome and never retries.
The caller must inspect the session before deciding whether to retry.

Responses expose method, thread_id, turn_id (app-server), content, streamed,
and cleanup_warnings. Streaming is consumed internally and buffered before
exposure so redaction can recognize secrets split across delta boundaries.
Every invocation shuts down its child process, including cancellation. A
timeout bounds protocol work; bounded Windows cleanup can add several seconds.
CLI authentication uses the inherited environment. No credentials are read or
API keys supplied.

## Semantics and limitations

- POST /api/messages persists a message and creates its conversation if needed.
  GET /api/inbox/{agent} and GET /api/poll/{agent} return pending messages only.
  Poll waits at most 45 seconds; zero is a nonblocking check.
- POST /api/messages/{id}/ack requires {"agent":"<recipient>"}.
  Acknowledgement is idempotent. A wrong recipient gets 404.
- POST /api/agents/{agent}/heartbeat stores last_seen.
- WS /ws/dashboard receives all successfully persisted mutation events.
  WS /ws/agents/{agent} receives message events where it is sender/recipient
  and its own heartbeat. Event names are message.created,
  message.acknowledged and agent.heartbeat. No event is sent for a read or
  repeated ack. Slow/broken sockets are disconnected.
- WebSockets are live notifications, not durable event replay. After reconnect,
  recover pending messages through the SQLite-backed inbox. A process crash
  between commit and broadcast can lose a notification, but not the message.
- Agent names are codex, antigravity, human. This is a trusted local-only API;
  the supplied agent name is not authenticated. Do not expose it on a network.
- Redaction runs before SQL writes and broadcasts and at MCP boundaries.
  Patterns cover common API key formats, JWTs, PEM private keys, named secret
  fields and all .env-style assignments (including quoted multiline values).
  Broad assignment redaction can mask ordinary configuration. Arbitrary unlabeled
  or encoded secrets cannot be guaranteed detectable. No .env/credential file
  is inspected and no raw payload logging is added.
- M0 real Codex ACK and Antigravity bounded-loop success are prior evidence.
  M1 Antigravity registration/invocation, real connector inference/resume and
  indefinite polling require separate manual verification. Human reactivation
  remains the fallback.

## Automated verification result

16 October 2026: complete pytest suite passed **48 tests in 57.15 seconds**,
including all 15 preserved M0 tests. One dependency warning remains:
Starlette TestClient deprecates its httpx adapter in favor of httpx2. This does
not affect the passing real loopback HTTP/WebSocket test. pip check reported
no broken requirements. git diff --check passed. The M0 scripts, regression
tests and requirements-m0.lock.txt are unchanged. No real-agent inference or
Antigravity UI verification was performed for M1.
