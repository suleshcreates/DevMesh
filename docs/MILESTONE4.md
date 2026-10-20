# Milestone 4: local frontend dashboard

Implemented in `C:\DevMesh` on `milestone-4`, branched from verified M3 history
`f1863b0` (implementation `91dbb1e`). Real `main` remains
`b302df01e3b9d4f8dc30515958a7b50f4828e8ef`. No merge, deployment, publication,
orchestration, scheduler, project-start endpoint, automatic agent loop, or
integration execution was added. The OneDrive staging checkout was not modified.

## Startup: exact PowerShell commands

Use a fresh PowerShell terminal so disposable-lab environment overrides are not
inherited. If the older backend is already running on 8420, stop it with Ctrl+C
in its owning terminal first; restart is required to load the new read endpoint
and CORS middleware. The existing M3 server was deliberately left running during
verification rather than stopping an operator-owned process.

```powershell
Set-Location C:\DevMesh
.\.venv\Scripts\python.exe -m backend.main
```

In a second terminal:

```powershell
Set-Location C:\DevMesh\frontend
npm.cmd ci
npm.cmd run dev
```

Open http://localhost:5173. The frontend binds to localhost, requires port 5173
(strictPort), and uses only `http://127.0.0.1:8420` and
`ws://127.0.0.1:8420/ws/dashboard`. No environment file, external service,
remote font, paid key, or port-8421 lab connection is needed. Use one API worker.

## UI and authoritative contracts

Vite + React + TypeScript + Tailwind CSS; system fonts, dark glass cards,
emerald/cyan Codex accents, violet/amber critique accents, and a canvas particle
network. Reduced-motion changes disable animation; resize listeners and animation
frames are cleaned up. Mobile layouts, visible focus, skip link, semantic forms,
native expandable details and a keyboard-focusable horizontal kanban are included.

Existing REST routes used:

- `GET /api/decisions?open_only=false`: complete decision documents, including
  proposals, counter-proposals, authors, critiques/risks/tradeoffs, round count,
  state, revision, resolution, rationale and separate human resolution.
- `GET /api/tasks`: persisted task contracts and the existing M3 refresh behavior.
  M3 may invalidate stale commit-bound reviews on this GET; M4 does not change it.
- `GET /api/approvals`: `{requests: [...]}`; each record's `document` is the
  redacted action, alongside its request ID, fingerprint and current state.
- `POST /api/decisions/{id}/human-resolve`: exact selected proposal content or
  custom directive, rationale, displayed `expected_revision`, and UUID request ID.
- `POST /api/approvals/{id}/decision`: displayed fingerprint, approved/denied,
  and reason. This existing endpoint does not accept a separate client request ID;
  the persisted approval ID and fingerprint bind the decision.

The new minimal read-only route is `GET /api/dashboard/snapshot`, implemented
in `backend/dashboard_routes.py`. It returns:

- `messages`: all persisted messages, including acknowledged history, using the
  existing message shape (`id`, `conversation_id`, sender/recipient, type,
  subject/content, created_at, delivered, requires_response).
- `agents`: persisted `{name, last_seen}` heartbeat rows.
- `integration_attempts`: persisted `{id, task_id, state, document}` rows, with
  JSON documents decoded, including actual result/test_log when recorded.

It performs SELECTs under the database lock, applies the existing redactor and
never acknowledges messages, creates records, consumes approvals, or runs Git.
SQLite is authoritative. No domain data is written to localStorage or IndexedDB.
The goal textarea is only a transient component-state draft: no project spec
replacement or project execution. Clipboard instructions use existing bridge
methods and a single bounded poll; actual clipboard completion/failure is shown.

Only `http://localhost:5173` is allowed by CORS, with GET/POST and Content-Type.
There is no wildcard and no credentialed CORS policy. `127.0.0.1:5173` is not a
supported frontend origin; use the documented localhost URL.

## Status, safety and refresh semantics

Agent badges use heartbeat timestamps, never socket connectivity: no row means
Unknown; age up to 60 seconds means Recent heartbeat; 60–180 seconds means Stale;
over 180 seconds means Offline. A local 10-second clock continues aging badges
during API outages. Heartbeats do not prove current agent work.

Active tasks exclude complete and blocked. Agreed decisions count only backend
`status=agreed`, excluding human_resolved and generic review messages. Pending
approvals count pending exact actions plus ESCALATE decisions. Test gate is Not
run with no attempt, Unknown for unfinished/missing outcomes, or the count of
recorded promoted attempts. It never claims that the current working tree passes
tests; per-task details show actual persisted logs and failed/non-promoted results.

Conversation records deduplicate by persisted message ID. Markdown disables raw
HTML, uses the renderer's safe URL handling, omits remote image loading and shows
diffs as code/text. Displaying a message never sends an acknowledgement.
Decision criteria are shown through recorded proposal rationale and structured
tradeoff analysis; M2 has no separate criteria field. Escalation reasons are
explained from the engine's invalid-count/round-limit branches. Every proposal is
shown; A and B name records #1 and #2, and subsequent choices retain their actual
proposal number and author. Human decisions remain human_resolved.

Forms disable duplicate pending submissions and require nonempty reasons.
Polling that changes a decision revision disables resolution until the operator
explicitly reviews the refreshed record. Failed/uncertain submissions refresh
state and require explicit re-review; text is retained while the record remains
actionable. Human request IDs are reused only when the complete payload is
identical. Exact actions bind the displayed request ID/fingerprint; terminal or
consumed requests offer no decision controls. Approval does not call integration.

The local-only operator and agent identities remain unauthenticated. Redaction
can conceal action text: the UI calls this out and requires separate local
inspection of masked details. This is not a production authorization system.

WebSocket notifications trigger debounced REST refresh rather than inventing
record formats. Reconnect delay grows 1/2/4/8/16/30 seconds (capped); a connection
must remain open for 10 seconds before resetting the backoff. Every reconnect
fetches authoritative snapshots because notifications have no replay. All data
also refreshes every 10 seconds because M3 has no dedicated task/approval socket
events. Socket/timer cleanup is registered on unmount. Requests time out at 10
seconds. Outages keep the last snapshot labeled stale and disable approval forms;
initial loading, empty, error and manual retry states are distinct.

## Verification results: 20 October 2026 (Asia/Calcutta)

```powershell
Set-Location C:\DevMesh
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m pip check
git diff --check
Set-Location C:\DevMesh\frontend
npm.cmd run build
npm.cmd test
npm.cmd audit
```

- Complete backend suite: **119 passed in 178.89s**, including all 117 existing
  M0–M3 tests and two new snapshot/CORS tests. One pre-existing Starlette/httpx
  TestClient deprecation warning remains. No broken Python requirements.
- New endpoint tests verify empty hydration, acknowledged and pending history,
  unchanged acknowledgement/audit/approval state, integration logs, heartbeat
  data, restart persistence, permitted preflight and denied origins/methods.
- Production build passed with **zero TypeScript errors**.
- Frontend tests: **3 passed**, covering heartbeat boundaries, identifier-based
  message reconciliation and honest missing/unfinished test results.
- npm audit: **0 vulnerabilities** at verification time.
- Real headless Microsoft Edge / Playwright: **10 browser scenarios passed**,
  including empty/loading-to-connected state, unknown heartbeats, keyboard focus,
  390px mobile/no page overflow, clipboard, persisted messages/Markdown safety,
  multi-proposal decisions, tasks, exact action display and approval, denial,
  consumed state, recorded logs, custom directive failure preservation and
  identical-request retry, API outage and WebSocket reconnect hydration.
- No unexpected console errors or page exceptions. Expected connection failures
  were excluded only during intentional outage tests. No real integration ran.
- Existing records were checked using SQLite's online backup with a read-only
  source connection: **19 messages**, DEC-001 agreed and DEC-002 human_resolved
  rendered in Edge. Copied decision documents matched the live read-only REST
  responses exactly. No original operator records were mutated.
- Desktop/mobile and existing-decision screenshots were visually inspected.
  Evidence: `m4-browser-results.json`, `m4-existing-results.json`,
  `m4-desktop.png`, `m4-mobile.png`, `m4-existing-records.png`.

Browser reproduction (start the frontend first; installed Microsoft Edge required):

```powershell
Set-Location C:\DevMesh
node frontend\e2e\verify.mjs
node frontend\e2e\read-existing.mjs
```

`verify.mjs` starts an isolated real FastAPI process and temporary SQLite DB on
18420, refusing an occupied port. Test-only browser transport routing forwards the
app's fixed 8420 requests/socket to that fixture API. It creates disposable
messages/decisions/tasks/approvals via real endpoints. Consumed state and a test
log are explicitly synthetic persisted fixtures inserted into that temporary DB;
no integration is executed to create them. Mutation fixtures are not production
seed data. API disconnect/reconnect is tested by stopping/restarting that owned
process against the same DB. The copy check runs its isolated copy on 18422.
Both scripts stop only their owned API processes and retain temporary data.

## Known limits and unverified flows

- The pre-existing real API on 8420 still runs M3 code and needs an operator
  restart. Direct browser operation against that restarted original process was
  not claimed; real-browser coverage used actual isolated backend instances and
  a read-only copy, with the app at localhost:5173.
- Human resolution, action approval/denial and retry tests used isolated data.
  No real pending action was approved and no existing decision was resolved.
  Consumed-state/log rendering used explicit persisted test fixtures, not an
  integration execution. No real two-agent M3 execution was added or verified.
- Snapshot and history reads currently load all records; very large databases
  may need future pagination/virtualization. Existing REST calls are separate
  snapshots, not a global multi-endpoint transaction.
- Automated accessibility checks exercised keyboard entry, labels, focus and
  responsive overflow; no screen-reader certification or exhaustive browser
  matrix is claimed. Particle animation resource cleanup was inspected in code;
  long-duration performance profiling is not claimed.
- No browser domain persistence, authenticated security, cross-worker event
  delivery, task state editing, execution controls or later milestone work.

## Normal-backend manual follow-up (20 October 2026)

See [MILESTONE4_MANUAL_VERIFICATION.md](MILESTONE4_MANUAL_VERIFICATION.md).
User screenshots show the connected overview, persisted conversation history,
DEC-001 agreed, DEC-002 human_resolved, and the expected empty normal-database
task/approval views. The user reported reconnect without browser refresh done;
this is recorded as a user-reported pass, not an independently captured restart
trace. This supplements the earlier unperformed normal-backend manual check.
No real approvals, decision resolutions or integrations were executed through
the UI in this follow-up. Existing automated evidence remains separate; checks
were not rerun for this documentation-only update. No M5 implementation was added.
