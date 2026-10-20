# Milestone 5: project orchestration and activation

Implemented in `C:\DevMesh` on `milestone-5`, based on verified M4 documentation
commit `60633ba` and implementation `3885aad`. The real implementation `main`
remains `b302df01e3b9d4f8dc30515958a7b50f4828e8ef`. No deployment, publication,
prerequisite merge, destructive cleanup, paid API key, credential-file read, or
existing MCP configuration-content access is part of this work.

## Normal Windows setup and startup

Use one backend process and one worker per database. From a fresh PowerShell:

```powershell
Set-Location C:\DevMesh
$activeMcpConfig = Read-Host 'Paste the active MCP config path confirmed in Antigravity settings'
.\start_devmesh.ps1 -McpConfigPath $activeMcpConfig
```

The supplied path is checked for existence only. The launcher does not inspect,
copy, parse, log or change its contents. Existence does not establish registration.

The launcher reuses a valid Python 3.11+ venv. If none exists, it verifies an
explicit interpreter or a suitable PATH interpreter before creating `.venv`:

```powershell
$python311Plus = Read-Host 'Absolute path to an installed Python 3.11+ executable'
.\start_devmesh.ps1 -McpConfigPath $activeMcpConfig -PythonPath $python311Plus
```

An invalid/incomplete existing venv is retained for manual inspection. Python
requirements use `requirements-m1.lock.txt`; imports, pinned package versions,
`pip check`, and a requirements hash determine when installation is needed.
Frontend dependencies use `npm.cmd ci` when the lockfile marker is absent/stale.
Codex and Node/npm must be on PATH. Codex authenticates through its own CLI;
DevMesh supplies no API keys and reads no authentication file.

The launcher binds the API to `127.0.0.1:8420`, Vite to `localhost:5173`, waits for
readiness, and opens the dashboard. `-NoBrowser` suppresses opening the browser.
Hidden helper processes write `.devmesh/api.*.log` and `.devmesh/vite.*.log`.
PID/start-time records are saved in `.devmesh/launcher-processes.json`. Occupied
ports fail closed, identifying known owned versus unverified processes; no
process is terminated. Stop a process only after checking identity in its owning
terminal or with the saved PID/start time. Partial startup failures preserve logs
and process records for inspection.

Normal API startup uses `scripts/start_normal_api.py`, which removes the three
named lab overrides before importing the app. It never inherits a lab database,
execution repository, test command, or port 8421. Equivalent manual startup:

```powershell
.\.venv\Scripts\python.exe scripts\start_normal_api.py
# In a second terminal:
Set-Location C:\DevMesh\frontend
npm.cmd run dev
```

Open http://localhost:5173. Enter the goal, explicitly selected external target
repository, and test command as a JSON argument array. The target must be a clean
Git top-level worktree, ignore `/.worktrees/`, have `main` unchecked out in every
worktree, and start on an operator branch whose HEAD equals main. DevMesh itself,
its descendants and worktrees sharing its Git common directory are rejected.
The executable must exist. This validates configuration, not test success.
Every actual test invocation still requires exact-action operator approval.

## Antigravity registration and activation

Through Antigravity's own MCP settings, identify the active config path and
manually verify this entry inside its existing `mcpServers` object:

```json
"devmesh-bridge": {
  "command": "C:\\DevMesh\\.venv\\Scripts\\python.exe",
  "args": ["C:\\DevMesh\\mcp_bridge\\devmesh_mcp.py"]
}
```

Do not replace other server entries or copy config contents into chat/logs.
The launcher prints this exact entry and three activation steps:

1. Confirm the active path and manually verify the server entry.
2. Refresh the bridge and verify lifecycle tools are listed.
3. Start a project, click **Copy Antigravity instructions**, and paste the actual
   current-project prompt into the IDE.

The concise reusable instructions are in `scripts/ANTIGRAVITY_SYSTEM_PROMPT.md`.
The copied prompt contains the persisted project ID, role, durable poll loop,
explicit acknowledgements, typed result instructions, manual execution boundary,
review/approval rules, budgets and stop conditions. `no_task` means poll again.
If the IDE stops or exhausts its own step budget, re-paste the current prompt.
Prior three-empty-poll evidence does not establish indefinite execution.

## Architecture and lifecycle

SQLite remains authoritative. `backend/orchestrator.py` coordinates existing
`DecisionEngine`, `TaskPlanner`, `MemoryManager`, `ReviewRunner` and `ApprovalGuard`.
It adds no alternate agreement, shell, deployment or promotion mechanism.

`projects.document` stores the goal/configuration, current state/revision,
independent analyses, request/response message and decision associations,
immutable memory snapshot/version, task links/dependencies through M3 tasks,
execution attempts/timestamps, progress, operation reservations, pause/completion
reasons and control history. `project_requests` stores hashed-payload receipts;
`project_usage` stores deduplicated observable identifiers. M3's task, request,
approval, audit and integration-attempt tables continue owning their records.

There is **one project per database**, including after cancellation/completion.
A second conflicting start is rejected. Legacy task records also require a fresh
project database so repositories and task memory cannot mix. Existing historical
M2 decisions remain visible in the dashboard but never enter M5 requirements.
Before memory lock, context renders only the current goal/design. After lock,
project memory renders the persisted immutable snapshot through the existing
atomic Markdown renderer. Editing generated Markdown never changes SQLite.

States:

```text
analysis -> critique -> planning -> tasks
                            tasks -> execution -> review -> approval -> tasks
                            tasks -> complete
any active state -> paused -> explicit resume of saved phase
any nonterminal state -> cancelled
```

- Both independent analysis jobs contain only the goal and their typed response
  schema. Codex uses the connector with explicit `gpt-6-astra`; Antigravity uses
  the durable inbox/poll queue. No peer analysis is sent until both are collected.
- Each independently authored analysis becomes an existing M2 design decision.
  The other agent critiques it. This preserves both original proposal authors
  rather than silently synthesizing or replacing their designs. The two design
  decisions are processed through M2, each with its existing three-round cap,
  blocking-risk checks, alternating counter-proposal authorship, invalid-response
  re-ask and escalation rules. Both must reach RECORD before memory locks.
  Cross-critique includes both analyses and the current design decisions so peers
  can challenge incompatibilities. Human resolution remains `human_resolved`.
- The memory snapshot contains the goal and both resolved design records. Its
  SHA-256 version is recorded for every project task and execution attempt.
  Direct source replacement is rejected once a project starts. Requirement
  amendments require a new decision workflow and deliberately versioned project;
  in-place requirement migration is unsupported and cannot silently alter tasks.
- Codex proposes a typed plan against that locked snapshot. The entire plan is
  checked before any task creation: unique keys, valid categories/routing,
  nonempty scope/acceptance criteria and existing acyclic dependency references.
  Topological order is validated; tasks and their project links commit together.
  Malformed plans pause with no invented replacement. An operator may explicitly
  resume for a fresh planning request.
- Eligible tasks execute serially. M3 creates/validates the assigned worktree and
  branch. A new dependent worktree advances to tested main before execution so
  it receives completed prerequisites. File scopes are checked against actual
  clean Git content before completion is accepted. Empty/nonexistent completion
  commits cannot establish implementation success.
- Task review goes to the opposite agent for the captured exact commit. M5 review
  reports invoke protected M3 feedback, not the message-only M1 review tool.
  A changed branch invalidates review and stale queued review assignments.
- Peer APPROVED feedback produces a pending exact-action request. The scheduler
  waits for the existing local operator route/ApprovalCenter decision. Once
  approved, it invokes only M3's integration runner: actual configured tests,
  clean/unchanged candidate checks and atomic tested-candidate ref promotion.
- Only all required tasks complete **and** recorded successful promotion gates
  produce PROJECT_COMPLETE. Generic messages, agent claims or passing-looking
  text never change lifecycle agreement/integration state.

## REST and bridge contracts

Bodies forbid extra fields. Use stable request IDs for transport retries. Reads
return current authoritative state; an idempotent mutation replay returns its
original snapshot. HTTP 409 indicates stale/conflicting/unsafe state; 422 indicates
invalid input. Validation never echoes submitted secret-bearing values.

| Contract | Purpose |
| --- | --- |
| POST `/api/projects/start` | `{request_id, goal, repository, test_command: string[], execution_mode: "manual"}` |
| GET `/api/projects` | `{projects: [...]}` |
| GET `/api/projects/{id}` | Persisted operator-facing lifecycle document |
| POST `/api/projects/{id}/pause`, `/resume`, `/cancel` | `{request_id, expected_revision, reason, manual_handoff: false}`; manual_handoff is optional and only relevant to explicit manual execution resume |
| GET `/api/projects/{id}/activation` | Current project's actual activation prompt |
| GET `/api/projects/{id}/assignments/{correlation}?agent=...` | Assigned job/schema/context only; optional stable request_id deduplicates attributable bridge reads |
| POST `/api/projects/{id}/reports` | `{request_id, correlation_id, agent, phase, task_id, decision_id, commit, payload}`; nullable IDs must match assignment exactly |
| POST `/api/projects/{id}/usage` | `{agent, correlation_id, event_id, kind, detail}`; kind tool/progress/error/stall |

Payload schemas are returned on jobs and in OpenAPI: Analysis, Critique, Plan,
ExecutionResult, ReviewResult. `commit` in the review envelope is the reviewed
commit; execution's resulting actual commit is inside its typed payload.
Correlation matching includes project, phase, assigned agent, task, decision and
reviewed commit. Wrong-agent, stale, cancelled and conflicting responses fail.

New MCP tools are `get_assignment_context`, `report_project_result` and
`report_project_usage`, thin clients of these same REST/domain services. Existing
`acknowledge_message` remains the explicit delivery acknowledgement. Inbox/poll
reads never acknowledge. Owned Codex adapters explicitly accept their assignment
before invocation; reading/context hydration alone does not accept work. Actual
agent responses are durable messages attributed to the reporting agent, with
correlation IDs. Independent response messages go to the operator until both
analyses are available. No human approval method is exposed as an agent tool.
Legacy task mutation routes cannot bypass a project pause or cancelled lifecycle.

## Execution modes, budgets and recovery

The installed CLI is `codex-cli 0.154.0`. Installed `app-server --help` and generated
v2 schemas were inspected; selected schema evidence is committed in
`docs/protocol/M5ObservableTools.json`. Analysis/planning/critique/review request
read-only thread and turn policies, explicitly disable turn network access and
reject server approval requests. Observable app-server item/started/completed
notifications are deduplicated by thread/turn/item IDs. With an observer attached,
there is no exec fallback whose tool accounting has not been verified.

**Production automatic editing is disabled.** Although the installed schema has
workspace-write policies, no verified interface enforces every file-scope,
destructive-command and operator-approval boundary for arbitrary IDE tools.
The production API accepts only manual execution mode. It pauses after preparing
an assigned worktree. Inspect the task, scope, branch and locked memory version,
select the manual-handoff checkbox, enter a reason, review the current revision,
and explicitly resume. Perform only the assigned edits manually in that worktree,
commit them, then submit the actual structured execution result for the pending
correlation. The connector never receives an automatic edit request. Antigravity
activation also forbids automatic execution in this mode. This is a concrete
manual boundary, not a claim of autonomous two-agent code execution.

- MAX_CRITIQUE_ROUNDS=3 remains owned by each M2 decision.
- MAX_TASK_RETRIES=3 remains M3's cumulative failed-review/test-attempt limit.
  Retries retain counts and need a fresh exact review/action approval as applicable.
- MAX_TOOL_CALLS_PER_TASK=40 combines existing M3 operation accounting with
  deduplicated protocol events and attributable context/ack/progress bridge
  operations. Counter, usage receipt and terminal budget blocking commit together.
  Unattributed inbox/poll reads are not charged to an arbitrary task.
- TASK_TIMEOUT_SECONDS=600 bounds each active execution attempt using persisted
  wall-clock start time. Human review/activation waiting is not fake execution.
  M3 separately preserves its 600-second integration deadline/cleanup behavior.
- Independent IDE tools are not fully observable or enforceable. Reported tool
  IDs account for observable events; they cannot prove a complete IDE tool count.
  Worktree instructions and actual-diff validation are cooperative controls,
  **not OS isolation**. Approved test programs retain ordinary user privileges.
- Blocked M3 tasks remain terminal. No resume resets counters or unblocks them.
  Inspect and deliberately plan replacement work in a fresh project database.
- Operation reservations persist before Git/inference effects. A restart with a
  running/uncertain operation or execution pauses for inspection. M3 also blocks
  interrupted integration attempts. A promotion before a lost DB result cannot
  trigger another promotion. Uncertain cleanup, timeout or changed main pauses;
  no uncertain execution or promotion is replayed automatically.
- Safe queued analysis/critique work survives restart without repeating already
  recorded responses. Read-only inference failures may also have submitted a
  turn, so they require inspection rather than blind replay.
- Cancellation stops future scheduling and rejects late results. It cannot undo
  already running independent manual IDE actions. Control requests serialize with
  a currently running guarded integration; inspect an uncertain HTTP outcome.

The dashboard shows real project state, required-task progress, pending activation,
worktree/memory context, persisted attempts/tool/retry counters, pauses, failures,
actual test logs and completion. It preserves M4 stale snapshots, disconnected
controls and reconnect hydration. Approval authorizes the displayed fingerprint;
when orchestration is waiting, approval enables that exact action to run.

## Disposable real-agent/manual verification

These steps are instructions, not a claimed real-agent pass. Stop the normal API
in its owning terminal before using 8420. Keep the normal launcher separate.

```powershell
Set-Location C:\DevMesh
.\.venv\Scripts\python.exe scripts\manual_m5_lab.py --port 8420
```

This creates a new TEMP repository/database and prints the repository, test JSON
and a login goal with harmless fixture acceptance tests. Existing explicit lab
directories are refused. It leaves artifacts for inspection and performs no
cleanup. A REST-only lab may instead use the helper's default port 8425.

1. Open the running frontend. Enter the printed goal/repository/test JSON and
   start. Confirm both analysis requests are persisted and independent.
2. Copy/paste the current Antigravity activation prompt; require actual bridge
   tool invocations. Confirm both authored design decisions receive typed peer
   critiques. Exercise challenges/escalation in another disposable lab if needed.
3. Resolve any escalation through ApprovalCenter. Confirm it stays paused until
   an explicit revision-bound resume. Confirm locked memory/task versions match.
4. At the manual execution pause, inspect the actual worktree/branch/scope. Resume
   with the manual checkbox and a reason; manually implement and commit assigned
   work only. Fetch the pending execution job from the project read endpoint.
5. Submit a structured execution report using the actual IDs/commit, for example:

```powershell
$api = 'http://127.0.0.1:8420'
$p = (Invoke-RestMethod "$api/api/projects").projects[0]
$j = $p.jobs | Where-Object { $_.phase -eq 'execution' -and $_.status -eq 'pending' } | Select-Object -First 1
$commit = Read-Host 'Paste git rev-parse HEAD from the assigned worktree after inspecting the diff'
$body = @{
  request_id=[guid]::NewGuid().ToString(); correlation_id=$j.id
  agent=$j.agent; phase=$j.phase; task_id=$j.task_id
  decision_id=$j.decision_id; commit=$j.commit
  payload=@{commit=$commit; summary='Manually implemented and committed the assigned scope'}
}
Invoke-RestMethod "$api/api/projects/$($p.id)/reports" -Method Post -ContentType 'application/json' -Body ($body | ConvertTo-Json -Depth 12)
```

6. Require actual opposite-agent structured review for that commit. A generic
   M1 submit_review message must not unlock integration. Change the task commit
   in a separate lab to verify review/action invalidation.
7. Before approval, inspect disposable main and the pending exact action. Confirm
   no tests/promotion occurred. Approve only after inspecting the command, scope,
   repository, candidate path, reviewed commit, request ID and fingerprint.
8. Inspect actual test exit/log, candidate and main identities. Every dependent
   task must repeat execution/review/approval/test/promotion gates. PROJECT_COMPLETE
   must appear only when all required gates completed.
9. Exercise failing tests, three failed attempts, stalled execution, explicit pause,
   cancellation and interrupted operations in separate disposable labs. Restart
   with the existing lab database using an explicit command, not a new helper run:

```powershell
$env:DEVMESH_DATABASE = Read-Host 'Existing disposable lab state.sqlite path'
.\.venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8420 --no-access-log
```

Use a fresh terminal for normal startup. Never use C:\DevMesh as the verification
execution target and never merge prerequisite history into its main.

## Verification results

Final verification on 20 October 2026 (Asia/Calcutta):

- Complete pytest suite: **147 passed in 287.49 seconds**, preserving all 119
  M0-M4 checks plus 26 M5 end-to-end cases and two protocol-observer regressions.
  One existing Starlette/httpx TestClient deprecation warning remains.
- Python dependency check: **No broken requirements found**. Python compilation
  and Git whitespace checks passed.
- Frontend: **3 tests passed**; production build passed with **zero TypeScript
  errors**.
- Real headless Microsoft Edge: **7 M5 lifecycle scenarios and all 10 M4 regression
  scenarios passed**, with no page exceptions/unexpected regression-console
  errors. M5 exercised real disposable commits, configured unittest execution,
  exact operator action/fingerprint decisions and guarded promotion, including
  actual API stop/restart and reconnect.
- Launcher: PowerShell syntax, occupied-port refusal, normal-startup lab-override
  removal and actual native argv handling with a spaced script path passed.
  Verification helpers were stopped; disposable repositories remain for inspection.

Automated inference is strictly injected FakeCodex/FakeAntigravity. Fake agents create real scoped
fixture commits; configured unittest commands and M3 Git gates actually run.
No real-agent inference is required by automated tests or browser fixtures.

```powershell
Set-Location C:\DevMesh
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m pip check
git diff --check
Set-Location C:\DevMesh\frontend
npm.cmd test
npm.cmd run build
# With Vite running, separate isolated Edge checks:
Set-Location C:\DevMesh
node frontend\e2e\verify-m5.mjs
$env:DEVMESH_BROWSER_EVIDENCE_PREFIX = 'm5-regression'
node frontend\e2e\verify.mjs
```

The browser fixture is test-only Python dependency injection; production exposes
no fake-adapter switch. Evidence: `m5-browser-results.json`, `m5-complete.png`,
`m5-mobile.png`, and separate `m5-regression-*` M4 regression evidence. Historical
M4 evidence is retained. The complete backend suite preserves M0-M4 checks; only
the strict MCP discovery expectation extends the supported tool set.

Known limits: trusted local unauthenticated role/operator labels, single-process
operation, notifications without replay, whole-history snapshots, pattern-based
redaction limitations, ordinary OS privileges, manual production editing,
unverified real-agent M5 execution/reviews and indefinite IDE polling. No real
two-agent end-to-end pass or production security claim follows from fake adapters.

## Change manifest and remaining manual checks

Created: `backend/orchestrator.py`, `backend/project_models.py`,
`backend/project_routes.py`, `backend/agent_adapters.py`,
`frontend/src/ProjectControls.tsx`, `start_devmesh.ps1`,
`scripts/start_normal_api.py`, `scripts/manual_m5_lab.py`,
`scripts/ANTIGRAVITY_SYSTEM_PROMPT.md`, `tests/test_e2e_devmesh.py`,
`frontend/e2e/m5_fixture.py`, `frontend/e2e/verify-m5.mjs`,
`docs/protocol/M5ObservableTools.json`, this runbook and `docs/m5-*` browser
results/screenshots (including the readable `m5-overview.png` and `m5-verification-results.json`).

Updated: `backend/main.py`, `backend/codex_connector.py`,
`backend/memory_manager.py`, `backend/task_planner.py`, `backend/task_routes.py`,
`mcp_bridge/devmesh_mcp.py`, `frontend/src/api.ts`, `frontend/src/contracts.ts`,
`frontend/src/main.tsx`, `frontend/e2e/verify.mjs`, `tests/test_m1.py`,
`tests/test_m1_connector.py` and `docs/FEASIBILITY.md`.

Launcher validation exercised syntax, native argument quoting, occupied-port refusal and normal-startup
lab-override removal without opening the normal database or terminating any
unrelated process. Cold venv creation/dependency installation and a complete
normal launcher run against a user-confirmed active MCP path were not exercised;
existing configured environments were used for the passing API/browser checks.
MCP config registration is intentionally a manual verification boundary.

Only protocol events from the assigned thread/turn and attributable bridge/domain
operations are counted. Nested agent/plugin internals and independent IDE tools
may be outside those observations; the counter is not a claim of complete IDE
telemetry. Unsupported automatic execution remains disabled. Real-agent M5
analysis/reviews, manual execution handoffs, live Antigravity lifecycle calls and
indefinite polling still require the disposable manual checks above.
