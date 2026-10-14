# DevMesh Milestone 0 feasibility

Checked locally on 15 October 2026 (Asia/Calcutta). **Milestone 1 is gated.**
Only integration spikes are implemented. There is no API server, task queue,
database, dashboard, deliberation engine, or automated agent execution.

Validation: **12 tests passed in 48.06 seconds** in the staging workspace.
Both real Codex probes classified the upstream failure as `model_unavailable`.
The exact upstream text was discarded. Use `--model` to select a model you have
verified is available in your CLI account, then rerun; no alternative was guessed.

## Integration results

| Integration | Result | Evidence | Fallback / next action |
| --- | --- | --- | --- |
| Installed Codex CLI | works | `codex --version`: `codex-cli 0.154.0`; root, app-server, exec, and schema-generator help inspected | Recheck flags and regenerate schemas after CLI upgrades |
| Codex app-server stdio protocol | partial | Initialize, thread/start, turn/start worked and returned thread/turn UUIDs. A real turn reported failed; no ACK or agent-message delta observed. See `codex-spike-results.json` | Repair the reported CLI/service limitation privately and rerun the ACK test. Prefer this method only after ACK succeeds |
| Codex exec JSONL | partial | Real `thread.started` event returned a thread UUID; the request emitted error/turn.failed without ACK | Available as a candidate fallback, **not yet a proven inference fallback**. Rerun after resolving the failure |
| Official MCP SDK FastMCP stdio | works | MCP 1.30.0; real client initialize/list_tools/ping, 45-second default wait, and repeated calls passed | Pin the tested SDK v1 dependencies; revalidate before migrating to SDK v2 |
| Antigravity config format and documented locations | partial | Official docs specify `mcpServers` with command/args, a workspace `.agents/mcp_config.json`, and global `~/.gemini/config/mcp_config.json`. Both global and legacy `~/.gemini/antigravity/mcp_config.json` exist here; existence checked only. Antigravity IDE package version 1.107.0 is installed | Use the workspace registration first. Confirm the active path in this installed application's settings; do not infer active config from file existence |
| Antigravity discovery and tool invocation | unverified | An SDK test client can invoke both tools, but this is not evidence of invocation by Antigravity | Perform the manual test below |
| Antigravity autonomous polling loop | unverified | No Antigravity agent loop has been observed in this run | If the agent stops after no_task or its step budget, switch to semi-manual activation and re-paste the prompt. Do not assume an unlimited loop |

Safe evidence records only event names, fixed error categories, identifiers and
ACK booleans. It does not retain raw upstream errors, stderr, tool output,
credentials or environment values. No credential files were read. Codex children
inherit the user environment and use their own authentication; no API key was
provided by these scripts. Do not supply paid API keys to run the spikes.

## Reproduce locally

Python on PATH is 3.10, below the requested minimum. This run used the installed
Codex bundled Python 3.12.14 to create `.venv`. Activation is optional; invoking
the venv executable avoids PowerShell execution-policy changes.

```powershell
cd C:\DevMesh
.\.venv\Scripts\python.exe -m pip install -r requirements-m0.lock.txt
.\.venv\Scripts\python.exe scripts\spike_codex.py --timeout 45 --output docs\codex-spike-results.json
.\.venv\Scripts\python.exe -m pytest -q
```

The spike exits 0 only when every selected method returns exactly ACK_CODEX and
completes successfully. Exit 1 is expected while either connection remains
partial. Each method has a bounded timeout. No retry loop is used.

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
{"id":2,"method":"thread/start","params":{"cwd":"C:\\DevMesh","sandbox":"read-only","approvalPolicy":"never"}}
{"id":3,"method":"turn/start","params":{"threadId":"<returned thread UUID>","input":[{"type":"text","text":"Reply with ACK_CODEX. Do not use tools or read any files."}]}}
```

The UUID notation above is documentation of a response substitution, not a
configured or fabricated session. Read `result.thread.id` from response 2 and
`result.turn.id` from response 3. Continue reading notifications while the turn
runs. Filter by threadId; concatenate `item/agentMessage/delta.params.delta`, and
capture `item/completed.params.item.text` for an agentMessage. Only a completed
turn with the exact ACK counts as success. Streaming deltas are supported by
the generated protocol schema but have **not** been observed successfully here.

Persist a thread ID and use `thread/resume` before another `turn/start` to continue
across app-server processes. The spike offers `--method app-server --thread-id`
for this check; successful conversation resume/inference is not yet verified.
Server-initiated requests are rejected by the ACK-only spike; it cannot approve
tools, provide authentication tokens or resolve elicitation requests.

The other command is:

```powershell
codex exec --json --sandbox read-only -C C:\DevMesh "Reply with ACK_CODEX. Do not use tools or read any files."
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

**Do not proceed to Milestone 1 until the human has read this report, tested the
Antigravity loop, and selected the operating mode. Codex inference must also
produce ACK before a connector is called proven.**

## Sources and verification boundary

- [Official Codex app-server documentation](https://learn.chatgpt.com/docs/app-server)
- Installed `codex --help`, `codex app-server --help`, `codex exec --help`, and
  generated 0.154.0 protocol schemas (local interface evidence takes precedence
  over examples for a different version).
- [Official Antigravity MCP documentation](https://antigravity.google/docs/mcp)
- [Google Antigravity getting-started codelab](https://codelabs.developers.google.com/getting-started-google-antigravity)
- [Official MCP SDK v1 documentation](https://py.sdk.modelcontextprotocol.io/v1/)

Native Windows UI control is unavailable in this chat. Therefore the active
Antigravity config and agent loop are manual checks, not silently assumed passes.
