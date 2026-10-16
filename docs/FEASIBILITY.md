# DevMesh Milestone 0 feasibility

Checked locally on 15 October 2026 (Asia/Calcutta). **The bounded Milestone 0
integration gate passed based on the user's reported Antigravity manual test.**
Only integration spikes are implemented. There is no API server, task queue,
database, dashboard, deliberation engine, or automated agent execution.

Validation after the Windows cleanup fix: **15 tests passed in 48.03 seconds**
in the staging workspace. Both real Codex probes produced ACK using the
user-verified model `gpt-6-astra`. App-server streamed the response; exec exited
0. Both returned thread IDs and no cleanup warnings. The earlier CLI default
`gpt-6.1-sol` was rejected by the ChatGPT-backed CLI connection. Explicitly pass
`--model gpt-6-astra` when reproducing the successful check.

## Integration results

| Integration | Result | Evidence | Fallback / next action |
| --- | --- | --- | --- |
| Installed Codex CLI | works | `codex --version`: `codex-cli 0.154.0`; root, app-server, exec, and schema-generator help inspected | Recheck flags and regenerate schemas after CLI upgrades |
| Codex app-server stdio protocol | works | Initialize, thread/start, turn/start, agent-message deltas and turn/completed observed. Exact ACK, completed status, thread/turn IDs, clean shutdown. See `codex-spike-results.json` | Preferred connection for M1, with explicit model gpt-6-astra; resume across processes remains unverified |
| Codex exec JSONL | works | Real thread.started, item.completed and turn.completed events; exact ACK and process exit 0; clean shutdown | Proven one-shot fallback using explicit model gpt-6-astra; exec session resume remains unverified |
| Official MCP SDK FastMCP stdio | works | MCP 1.30.0; real client initialize/list_tools/ping, 45-second default wait, and repeated calls passed | Pin the tested SDK v1 dependencies; revalidate before migrating to SDK v2 |
| Antigravity config format and documented locations | partial | Official docs specify `mcpServers` with command/args, a workspace `.agents/mcp_config.json`, and global `~/.gemini/config/mcp_config.json`. Both global and legacy `~/.gemini/antigravity/mcp_config.json` exist here; existence checked only. Antigravity IDE package version 1.107.0 is installed | Use the workspace registration first. Confirm the active path in this installed application's settings; do not infer active config from file existence |
| Antigravity discovery and tool invocation | works (manual, user reported) | User pasted Antigravity's reported MCP ping invocation returning status pong; local cached ping and await_next_task schemas also exist | Recheck actual tool invocation when M1 replaces the spike server |
| Antigravity bounded polling loop | works (manual, user reported) | User reported exactly 3 MCP await_next_task calls, each returning status no_task, in response to the bounded loop probe | Proceed with cooperative polling and retain human reactivation when the IDE stops. Unlimited polling and longer-running task execution remain unverified |

Safe evidence records only event names, fixed error categories, identifiers and
ACK booleans. It does not retain raw upstream errors, stderr, tool output,
credentials or environment values. No credential files were read. Codex children
inherit the user environment and use their own authentication; no API key was
provided by these scripts. Do not supply paid API keys to run the spikes.

## Antigravity manual gate evidence

The user supplied these results in this chat on 15 October 2026:

- MCP `ping`: reported actual invocation with `{"status":"pong"}`.
- MCP `await_next_task`: reported exactly 3 actual MCP calls, all returning
  `{"status":"no_task"}`, after the prompt requesting three successive calls
  without asking the human to continue.

This is human-supplied evidence, not a tool trace independently captured by
Codex. Exact durations, the active Antigravity model, and indefinite polling were
not verified. The user reported that the working global server entry now points
to `C:\Users\sules\OneDrive\Desktop\dev_center\DevMesh`.
Both that staging path and `C:\DevMesh` contain the spike executable and script,
as checked locally. `C:\DevMesh` contains the committed project history. M1 must
confirm the active bridge points to its real implementation and retest the
Antigravity tools; do not silently assume a config path or edit credentials.

The bounded gate is accepted for proceeding to M1. Keep the re-paste/activation
fallback: this result supports a cooperative polling design, not a guarantee
that an IDE agent will run indefinitely.

## Reproduce locally

Python on PATH is 3.10, below the requested minimum. This run used the installed
Codex bundled Python 3.12.14 to create `.venv`. Activation is optional; invoking
the venv executable avoids PowerShell execution-policy changes.

```powershell
cd C:\DevMesh
.\.venv\Scripts\python.exe -m pip install -r requirements-m0.lock.txt
.\.venv\Scripts\python.exe scripts\spike_codex.py --model gpt-6-astra --timeout 90 --output docs\codex-spike-results.json
.\.venv\Scripts\python.exe -m pytest -q
```

The spike exits 0 only when every selected method returns exactly ACK_CODEX and
completes successfully. Exit 1 is expected while either connection remains
partial. Each method has a bounded timeout. No retry loop is used. Exec stops
reading protocol output at turn.completed and separately observes process exit;
it does not wait for stdout EOF, which inherited pipe handles can prevent.
Shutdown closes subprocess transports while the Windows event loop still runs,
and reports any bounded cleanup warning without discarding the probe results.
Regression tests cover an exited parent with a descendant holding stdout/stderr,
a failed turn, and bounded termination of a hung process.

The desktop filesystem sandbox blocked Windows asyncio's loopback socket pair:
a diagnostic trace stopped in `socket._fallback_socketpair` / `accept` before
an event loop started. Tests completed outside that restriction. Run these
checks in an ordinary local PowerShell session, or approve the required local
process/network access; a frozen sandboxed startup is not an integration result.

## Exact Codex framing and stream handling

Installed CLI help confirms `codex app-server --listen stdio://`. Write UTF-8 JSON
objects, **one per line**, with a trailing LF. There is no Content-Length header
and the observed app-server messages omit a `jsonrpc` property. Stdout carries
protocol data; stderr is separate and is discarded by this spike.

The script sends these messages in order, waiting for each numbered response:

```json
{"id":1,"method":"initialize","params":{"clientInfo":{"name":"devmesh_spike","version":"0.0.1"}}}
{"method":"initialized","params":{}}
{"id":2,"method":"thread/start","params":{"cwd":"C:\\DevMesh","sandbox":"read-only","approvalPolicy":"never","model":"gpt-6-astra"}}
{"id":3,"method":"turn/start","params":{"threadId":"<returned thread UUID>","input":[{"type":"text","text":"Reply with ACK_CODEX. Do not use tools or read any files."}]}}
```

The UUID notation above is documentation of a response substitution, not a
configured or fabricated session. Read `result.thread.id` from response 2 and
`result.turn.id` from response 3. Continue reading notifications while the turn
runs. Filter by threadId; concatenate `item/agentMessage/delta.params.delta`, and
capture `item/completed.params.item.text` for an agentMessage. Only a completed
turn with the exact ACK counts as success. Streaming deltas were observed in the
successful gpt-6-astra probe and are supported by the generated protocol schema.

Persist a thread ID and use `thread/resume` before another `turn/start` to continue
across app-server processes. The spike offers `--method app-server --thread-id`
for this check; successful conversation resume/inference is not yet verified.
Server-initiated requests are rejected by the ACK-only spike; it cannot approve
tools, provide authentication tokens or resolve elicitation requests.

The other command is:

```powershell
codex exec --json --sandbox read-only --model gpt-6-astra -C C:\DevMesh "Reply with ACK_CODEX. Do not use tools or read any files."
```

Read stdout line by line as JSONL. The observed ID is in
`thread.started.thread_id`. Final assistant output is in an
`item.completed` event whose item.type is `agent_message`, with item.text.
Completion requires `turn.completed` and process exit 0. Exec provides an event
stream; token deltas were not observed. Its installed help exposes
`codex exec resume` for continuing a session; exact resume flags should be
checked with `codex exec resume --help` before using that fallback.

The six relevant schemas from this installed CLI are committed under
`docs/protocol/`. Regenerate the full, ignored bundle with:

```powershell
codex app-server generate-json-schema --out docs\codex-schema
```

## Register Antigravity and perform the gate

Registration:

```powershell
cd C:\DevMesh
.\.venv\Scripts\python.exe scripts\setup_antigravity_mcp.py
```

This exclusively creates `.agents/mcp_config.json` with absolute venv and server
paths. It refuses an existing file without reading it. If already registered,
skip the command. To generate the single server entry for manual insertion:

```powershell
.\.venv\Scripts\python.exe scripts\setup_antigravity_mcp.py --print-entry
```

Open C:\DevMesh in Antigravity. For the IDE, use agent panel … > MCP Servers >
Manage MCP Servers > View raw config to confirm which configuration it actually
uses. For the standalone app, use Settings > Customizations > Installed MCP
Servers > Open MCP Config. Refresh the server list after registration. If the
workspace config is not discovered, manually insert the printed devmesh-bridge
entry into the active config's mcpServers object. Keep other entries untouched;
do not paste their contents into DevMesh, chat or logs.

1. Enable devmesh-bridge and confirm both ping and await_next_task are listed.
2. Ask the agent: `Call devmesh-bridge ping. Show the actual tool result.`
   Require a visible tool invocation and `{"status":"pong"}`.
3. Paste this bounded loop probe:

   ```text
   Use devmesh-bridge. Call await_next_task with agent_name="antigravity"
   and timeout_seconds=45. When its result is no_task, call it again
   immediately. Complete three calls total, without asking me to continue.
   Do not read or edit files or use other tools. After the third result,
   report the actual number of completed calls and stop.
   ```

4. Inspect the actual tool-call history: three successive calls should each
   take roughly 45 seconds and return `{"status":"no_task"}`. Record whether it
   required intervention, hit a step limit, errored, or merely claimed success
   without calls. A short bounded success supports feasibility but cannot prove
   an indefinitely autonomous loop.
5. If polling stops, select the semi-manual design: the human reactivates the
   Antigravity agent with a prompt when work is available or a step budget ends.
   The later server would still use durable inbox/ack semantics; it must not
   depend on the IDE polling forever.

**The user-reported manual results satisfy the bounded Antigravity gate.
The Codex ACK gate passed with explicit model gpt-6-astra. M1 may proceed in a
separate chat using cooperative polling with human reactivation as the fallback.
This chat stops at M0; no server or later milestone has been implemented.**

## Sources and verification boundary

- [Official Codex app-server documentation](https://learn.chatgpt.com/docs/app-server)
- Installed `codex --help`, `codex app-server --help`, `codex exec --help`, and
  generated 0.154.0 protocol schemas (local interface evidence takes precedence
  over examples for a different version).
- [Official Antigravity MCP documentation](https://antigravity.google/docs/mcp)
- [Google Antigravity getting-started codelab](https://codelabs.developers.google.com/getting-started-google-antigravity)
- [Official MCP SDK v1 documentation](https://py.sdk.modelcontextprotocol.io/v1/)

Native Windows UI control is unavailable in this chat. Antigravity invocation
and bounded polling are recorded as user-reported manual passes; no independent
UI/tool-call trace was captured by Codex.

## Milestone 1 implementation update (16 October 2026)

Milestone 1 now implements durable SQLite WAL messaging, explicit delivery
acknowledgements, REST long-polling, redacted WebSocket notifications, the official
FastMCP stdio bridge, and a bounded Codex connector. See MILESTONE1.md for exact
startup commands, manual registration replacement, semantics and verification.
The M0 conclusions above remain historical evidence; they are not M1 manual
integration results. The staging registration was not inspected or modified.
M1 must be registered manually against C:\DevMesh\mcp_bridge\devmesh_mcp.py.
Real Antigravity M1 invocation, real connector inference/resume and indefinite
polling remain unverified. Human reactivation remains required when polling stops.
The connector reuses the M0 framing/flags and shutdown implementation; the
committed ThreadResumeParams schema verifies the optional resume parameters.

## Manual and live verification follow-up (16 October 2026)

See [MILESTONE1_VERIFICATION.md](MILESTONE1_VERIFICATION.md) for the latest
verification status, superseding earlier unverified M1 integration notes.
The user supplied passing Antigravity MCP results for restart persistence,
pending-message polling, explicit acknowledgement, three timed empty polls,
and a review delivered to Codex's inbox. Codex directly verified real connector
inference and cross-process resume with remembered context using gpt-6-astra;
both turns streamed with no cleanup warnings. The review was not acknowledged.
Indefinite polling remains unverified. Broad redaction masked ordinary
delivered=false prose in the review, as detailed in the verification note.
No later milestone was implemented.
