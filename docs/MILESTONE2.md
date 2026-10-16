# Milestone 2: deliberation and generated project memory

Implemented on `milestone-2`, based on verified M1 commit `e8ae166` (implementation
`f3d4aba`). The clean repository is `C:\DevMesh`; the OneDrive M0 staging tree is
not the implementation repository. No later milestone is included.

## Run and test in PowerShell

```powershell
Set-Location C:\DevMesh
.\.venv\Scripts\python.exe -m backend.main
```

In another terminal:

```powershell
Set-Location C:\DevMesh
.\.venv\Scripts\python.exe -m pytest -q
```

Existing M1 dependencies suffice (`requirements-m1.lock.txt`). Bind only to
127.0.0.1:8420, one worker. No paid keys, credential reads, real-agent calls or
Antigravity process are needed by pytest. M0's stdio test includes a 45-second
wait. Swagger schemas are at http://127.0.0.1:8420/docs.

## Persisted state and agreement rules

`decisions` stores an ordered decision document with ID (DEC-001 onwards), title,
state, status, revision, round_count, invalid_count, proposals, critiques,
transition history with timestamps, resolution, rationale and human_resolution.
The SQLite transaction also writes durable messages and a replay record in
`decision_requests`. Request IDs are hashed before storage. Redacted request
fingerprints detect conflicting reuse; inputs that redact identically cannot be
distinguished. Only redacted critique request text is retained in replay fingerprints; errors never echo payloads.

Transitions: NEW -> ANALYSIS -> EXCHANGE -> CRITIQUE -> RESOLVE -> RECORD or
ESCALATE. A nonterminal challenge goes RESOLVE -> EXCHANGE. Invalid JSON follows
CRITIQUE -> RESOLVE -> EXCHANGE for the one re-ask, then ESCALATE on another
invalid response. Human resolution is ESCALATE -> RESOLVE -> RECORD. All steps
are recorded within a transaction; ANALYSIS is intake validation, not an agent
inference phase. No autonomous debate or orchestrator runs.

Only codex and antigravity can propose or critique. The latest proposal's author
cannot critique it. A nonempty counter_proposal on a non-agreement becomes the
active proposal; the opposite agent must next critique it. Without a counter,
the active proposal remains unchanged. Every valid critique consumes one round,
including AGREE with blocking risks. At round three a non-agreement escalates.
An explicit peer AGREE with zero blocking risks alone produces status `agreed`.
Message text, extra status fields, and self-approval cannot produce agreement.

Risks are objects with required `description` and strict boolean `blocking`.
Missing blocking, string booleans, missing required fields, extra fields and
invalid JSON are invalid critiques. Nonblocking risks may be recorded with AGREE.
AGREE with any blocking risk is retained as a non-agreement and counts toward the
three-round limit. Counter-proposals are not invented when absent.

One invalid response triggers one persisted CRITIQUE_REASK per decision, not per
round. A second escalates without consuming a valid round. Repeated delivery of
the same request ID replays the original response without another event/round.
Clients must reuse that ID for retries and use a new ID for a genuine re-answer.
Every new mutation requires the latest revision. BEGIN IMMEDIATE serializes
writers even across SQLite connections; competing requests with the same
revision cannot both mutate state. Terminal agent submissions are rejected.

Events: DECISION_PROPOSED, CRITIQUE_REQUIRED, CRITIQUE_REASK, DECISION_AGREED,
HUMAN_APPROVAL_REQUIRED and DECISION_HUMAN_RESOLVED. Agent inbox messages include
the persisted decision, all proposals (including both authors when supplied),
and critique history. Escalation also writes to the human inbox. No missing
proposal is fabricated. The legacy message schema has no system sender, so these
engine notifications use sender `human` as a routing label, not a claim that a
human approved them. Their subject identifies the engine event.

Inbox messages commit atomically with state before message.created and the named
decision event are broadcast through the existing WebSocket manager. Retries
do not duplicate events. Live notifications can be lost after commit on crash;
recover with GET decision/inbox. No WebSocket replay or cross-worker wakeups.

## REST contract

All bodies forbid unknown fields. Request IDs are caller-generated strings of
1–80 ASCII letters/digits/hyphens. Text limits: title 500, proposal/rationale,
critique_json, directive, source content 100000 characters. Revision starts at 1
and advances on each committed critique attempt or human resolution.

| Method and route | Body / response |
| --- | --- |
| POST /api/decisions/propose | `{request_id, agent, title, content, rationale}` -> 201 full decision |
| POST /api/decisions/{id}/critique | `{request_id, agent, expected_revision, critique_json}` -> 200 full decision |
| POST /api/decisions/{id}/human-resolve | `{request_id, expected_revision, directive, rationale}` -> 200 full decision; escalation required |
| GET /api/decisions | `{decisions: [...]}`; open_only=true by default, includes escalations; false includes recorded decisions |
| GET /api/decisions/{id} | Full decision with history |
| GET /api/context/{doc_name} | `{doc_name, content, generated}`; DB-derived content even if file write fails |
| PUT /api/context/{doc_name}/source | `{content}` -> same context shape; replaces persisted source text and broadcasts memory.source_updated |

critique_json is a JSON-encoded string with this object schema:

```json
{
  "verdict": "AGREE",
  "identified_risks": [],
  "counter_proposal": null,
  "tradeoff_analysis": {
    "security": "Local-only access is acceptable.",
    "deployment": "One process is sufficient.",
    "complexity": "The design is small enough to maintain."
  }
}
```

CHALLENGE requires a nonempty counter_proposal, ensuring both proposals are available at third-round escalation, and may include
risks such as `{"description":"Recovery is insufficient","blocking":true}`.
All three tradeoff strings are required and nonempty. counter_proposal is
required but nullable for AGREE. Round numbers and states are never client-controlled.

404 means unknown decision. 409 means stale revision, request ID conflict,
self-critique or invalid state transition. 422 means invalid outer request or
unsupported document. Invalid *inner* critique_json deliberately returns 200
with persisted re-ask/escalation state. Validation errors never echo raw input.
An exact idempotent replay returns its original snapshot, which may be older than
the latest state; GET the decision before the next new mutation.

Human directives and rationales are separately stored in human_resolution and
produce `human_resolved`, never `agreed`. There is no human-resolution MCP tool.
The REST route is a trusted local operator interface, not authenticated human
identity. Any local HTTP client can invoke it; agent-name claims are also not
authenticated. Do not expose the API to a network.

## Generated memory

The exact allowlist is PROJECT_SPEC.md, ARCHITECTURE.md, API_CONTRACT.md,
DECISIONS.md, TASKS.md, KNOWN_ISSUES.md. Names are case-sensitive; paths and
traversal are rejected. Context is rendered from SQLite, never read from these
files. memory_sources accepts only the spec, architecture, API contract and
known issues documents; there is no task source endpoint or task planner.
Source records are operator-supplied project facts, not scanned Markdown.

DECISIONS includes all persisted decisions; ARCHITECTURE appends recorded
decisions and their persisted content to any supplied architecture source.
Decisions currently have no domain classification, so all recorded decisions
are shown in ARCHITECTURE. TASKS states that no task planning exists in M2.
Other empty documents honestly report no source content. No sample data is seeded.

All six files regenerate at startup and after agreement/human resolution.
Context reads regenerate the requested file. Each write creates a temporary
file in the destination directory, writes, flushes, fsyncs, closes, then calls
os.replace. Failed temporary files are cleaned up. Each file is atomic; the
six-file set is not one filesystem transaction. OSError does not undo committed
decisions or prevent DB context reads (`generated=false`). Restart or request
context to retry. Deleting or editing generated files cannot alter SQLite.

Existing redaction remains before persistence and broadcasts. Broad assignment
redaction can mask ordinary prose including delivered=false, and can mask
adjacent serialized text in legacy string message content or Markdown. Prefer
the structured decision read API for state. MCP redaction can also make a
secret-bearing critique_json string invalid; it then follows the persisted
re-ask policy rather than being silently repaired. Unlabeled/encoded secrets
cannot be guaranteed detectable. No credential-bearing configs were accessed.

## MCP and manual verification

New tools: list_open_decisions, submit_decision_proposal, submit_critique,
get_project_context(doc_name). Existing submit_proposal/submit_review remain M1
message-only tools for compatibility, with no agreement authority. The bridge
uses the same path as M1; refresh/restart it to discover the new tools. If an
older registration still points to staging, replace only this entry manually
inside the active config's mcpServers object:

```json
"devmesh-bridge": {
  "command": "C:\\DevMesh\\.venv\\Scripts\\python.exe",
  "args": ["C:\\DevMesh\\mcp_bridge\\devmesh_mcp.py"]
}
```

Manual proposal/challenge/agreement, using actual returned IDs/revisions:

```powershell
$api = 'http://127.0.0.1:8420'
$p = @{ request_id=[guid]::NewGuid().ToString(); agent='codex'; title='Manual storage decision'; content='SQLite WAL'; rationale='Local persistence' }
$d = Invoke-RestMethod "$api/api/decisions/propose" -Method Post -ContentType 'application/json' -Body ($p | ConvertTo-Json)
$critique = @{ verdict='CHALLENGE'; identified_risks=@(@{description='Recovery needed'; blocking=$true}); counter_proposal='SQLite WAL with backups'; tradeoff_analysis=@{security='Local access'; deployment='One worker'; complexity='Backup procedure'} }
$b = @{ request_id=[guid]::NewGuid().ToString(); agent='antigravity'; expected_revision=$d.revision; critique_json=($critique | ConvertTo-Json -Depth 8 -Compress) }
$d = Invoke-RestMethod "$api/api/decisions/$($d.id)/critique" -Method Post -ContentType 'application/json' -Body ($b | ConvertTo-Json)
$critique.verdict='AGREE'; $critique.identified_risks=@(); $critique.counter_proposal=$null
$b.request_id=[guid]::NewGuid().ToString(); $b.agent='codex'; $b.expected_revision=$d.revision; $b.critique_json=($critique | ConvertTo-Json -Depth 8 -Compress)
$d = Invoke-RestMethod "$api/api/decisions/$($d.id)/critique" -Method Post -ContentType 'application/json' -Body ($b | ConvertTo-Json)
$d
Get-Content C:\DevMesh\.devmesh\DECISIONS.md
Get-Content C:\DevMesh\.devmesh\ARCHITECTURE.md
```

For escalation, create another proposal with a new request ID. Submit three
CHALLENGEs with nonempty counter-proposals, alternating antigravity, codex,
antigravity and updating request ID/revision each time. Expect round_count=3,
state ESCALATE, status escalated_to_human. GET /api/inbox/human must contain
HUMAN_APPROVAL_REQUIRED with both proposals and all three critiques. Then:

```powershell
$h = @{ request_id=[guid]::NewGuid().ToString(); expected_revision=$d.revision; directive='Use the backup design'; rationale='Recovery is required' }
$d = Invoke-RestMethod "$api/api/decisions/$($d.id)/human-resolve" -Method Post -ContentType 'application/json' -Body ($h | ConvertTo-Json)
Invoke-RestMethod "$api/api/context/ARCHITECTURE.md"
```

Confirm human_resolved and a separate human_resolution record. Restart the API,
GET the decision, and verify unchanged history. Remove only a generated Markdown
file and call get_project_context to recreate it; changing Markdown must not
change returned DB content. For malformed critique tests, send critique_json
`not JSON` twice with distinct request IDs/latest revisions; confirm one re-ask
then human escalation. Retry the same request ID and verify no new round/event.
An AGREE from the active proposer must get 409. AGREE with blocking=true must
remain unresolved (or escalate on round three). Unsupported context paths must
fail before file access.

## Verification boundary

Automated tests use temporary SQLite databases and fake HTTP/process adapters;
real loopback and MCP stdio regressions remain in the full suite. Existing M1
test assertions are preserved, with only the exact MCP discovery set extended.
No real M2 agent session or manual UI workflow has been verified. M1 live
inference/resume with gpt-6-astra and user-reported Antigravity tests remain prior
evidence, not new M2 results. Indefinite polling is unverified; retain human
reactivation. No frontend, worktree, task execution/planner, destructive-command
gate, review/merge runner, orchestration or deployment was implemented.

Final automated verification (16 October 2026): **69 passed in 54.24 seconds**, including all 48 existing tests and 21 new tests. One existing Starlette/httpx TestClient deprecation warning remains. pip check found no broken requirements; git diff --check passed. No real agents were called.

## Manual verification follow-up (16 October 2026)

See [MILESTONE2_VERIFICATION.md](MILESTONE2_VERIFICATION.md) for evidence and
verification boundaries, superseding earlier notes that the M2 manual workflow
was unverified. User-supplied results passed M2 MCP discovery/context, DEC-001
two-round peer agreement, DEC-002 three-round human escalation, the human inbox
notification, explicit operator resolution, and regenerated decision/architecture
memory. Direct local API and file checks confirmed both final records and exact
agreement between generated Markdown and DB-derived context. The API restart
itself was not independently observed by Codex. Automated coverage remains the
reported 69 passing tests; the suite was not rerun for this documentation update.
No later milestone was implemented.
