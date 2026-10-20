# Milestone 3: task execution safety

Implemented in C:\DevMesh on milestone-3, based on verified M2 history 9581b7a
(implementation cee26d9). At intake main was b302df0 and was an ancestor of M2:
`git rev-list --left-right --count main...milestone-2` returned `0 4`.
M1 was e8ae166. No M1/M2/M3 history was merged into the real main branch.
The old OneDrive staging copy and credential-bearing MCP configurations are untouched.

## Start and test (PowerShell)

```powershell
Set-Location C:\DevMesh
.\.venv\Scripts\python.exe -m backend.main
```

Existing requirements-m1.lock.txt dependencies suffice. The server binds only
127.0.0.1:8420; use one worker and one server process per database. Stop with Ctrl+C.
Task and approval data use the existing WAL SQLite database. No tasks are seeded.

```powershell
Set-Location C:\DevMesh
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m pip check
git diff --check
```

Tests use temporary Git repositories, temporary databases, deterministic peer
feedback, and harmless Python commands. They require neither Antigravity nor real
agent inference. The complete suite preserves the M0 timed stdio/loopback tests.
Do not run an integration demonstration against C:\DevMesh: use the disposable
repository instructions below. Runtime tests require an explicitly configured JSON
argument array in DEVMESH_TEST_COMMAND; no default test-success command exists.
DEVMESH_EXECUTION_REPOSITORY selects the repository and DEVMESH_DATABASE selects
the SQLite path. These three named settings are read at startup. No credential
files are read and no paid API keys are supplied.

## Durable tasks and limits

The tasks table has actual columns for id, title, description, assigned_agent,
status, dependencies, touched_files_scope, acceptance_criteria, branch,
worktree_path, review_feedback, retry_count, revision, history, review, tool_calls.
Lists/structured records are JSON column values. task_requests stores hashed
request identifiers, raw-payload hashes, and redacted replay responses.
SQLite BEGIN IMMEDIATE serializes each task mutation; durable messages commit in
the same transaction. Restart reloads state; generated TASKS.md is a disposable
projection. Markdown is never imported and no task source endpoint exists.

Default routing: backend/database/logic/debugging/unit_tests to codex;
ui_ux/frontend/browser_qa to antigravity. Explicit assignment is supported.
Dependencies reference existing tasks, are immutable, and must all be complete
before assignment, starting, review, or integration. References to future tasks
are rejected, so self-dependencies and cycles cannot be constructed through the API.

Transitions:

- planned -> assigned or blocked
- assigned -> in_progress or blocked
- in_progress -> review or blocked
- review -> in_progress, testing, or blocked
- testing -> in_progress, complete, or blocked
- complete and blocked are terminal

The status endpoint only permits assigned, in_progress, or blocked transitions;
review/testing/complete require the dedicated workflow. Returning to in_progress
invalidates any prior review. Failed reviews/tests increment retry_count once per
attempt. At MAX_TASK_RETRIES=3 the task blocks and a durable human notification is
created. Blocked tasks require operator inspection and a deliberately planned
replacement task; no API silently resets retry counts or overrides peer approval.

MAX_TOOL_CALLS_PER_TASK=40 counts accepted unique status, challenge, workspace,
review, feedback and integration operations. Replays do not count. The next
operation at the cap blocks the task and notifies the human. Read-only polling,
internal Git commands, and tool calls made independently inside an IDE are not
counted. Failed validation rolls back the call reservation. M3 has no agent loop
that could measure or control independent IDE tools. Integration execution has a
600-second overall deadline, including tests. Individual Git subprocesses are
also bounded. Bounded process/pipe cleanup can add several seconds after a timeout. Cleanup attempts Windows process-tree termination and closes inherited pipe handles; it does not provide isolation from hostile detached descendants. Idle human review time is not an execution timeout. There is no
background scheduler or autonomous orchestrator.

## Git workspace and overlap rules

Task branches are feat/codex-TASK-001 or feat/antigravity-TASK-002. Worktrees live
under the selected repository's ignored .worktrees/TASK-xxx directory. The
repository must already ignore /.worktrees/; the implementation repository now
does. Existing matching branches/worktrees are verified and reused. Mismatches,
checked-out-elsewhere branches, failed Git commands, and uncertain candidates
stop safely. No branch reset, force push, conflict resolution, or automatic
worktree deletion is performed.

Scope entries are exact relative files, or directories ending in `/` (for example
`backend/`). They are not globs. Absolute/drive/UNC paths, traversal, control
characters, Windows device names/trailing-dot aliases, .git and .worktrees are
rejected. Resolved write paths must stay inside the assigned worktree and scope.
Git symlink and submodule trees are rejected. Branch/worktree registration must
match the task's persisted identity. A task worktree must be clean before review
and integration. Both added and deleted paths are checked, with rename detection
disabled. This is a cooperative application boundary, not a Windows filesystem
ACL sandbox: independent IDE tools or an approved arbitrary script retain their
normal OS privileges.

Branches start from the selected repository's current HEAD, preserving the
verified M1/M2 implementation history. The actual comparison is always
`git diff --name-only main...<branch>` (NUL-delimited, no rename detection).
Because real main lacks M1/M2, narrow task scopes based on M2 will reject those
inherited changes. This is intentional: M3 does not silently merge prerequisite
milestones or exempt inherited changes. An operator must separately decide the
baseline/history and scope before using integration on that repository.

Overlap is checked case-insensitively across all other unfinished persisted task
branches, including blocked branches that still contain edits. An overlap outside
either declared scope blocks integration and creates one blocked serialization
task plus a human notification. Repeated detection of the same task pair/path set
reuses that record. An overlap within both declared scopes is intentional only
with explicit dependency ordering; otherwise it also requires serialization.
Dependency readiness still requires the predecessor to finish. Human resolution
means inspecting the branches and planning correctly ordered replacement tasks;
M3 never silently resolves conflicting edits. Existing failed candidates remain
available for inspection and are never automatically retried or reset.

## Peer review and integration

Review-ready captures the branch's exact commit, merge base, actual redacted diff,
and acceptance criteria. A durable code_review message goes to the other agent.
Approval requires a structured APPROVED or CHANGES_REQUESTED verdict for that
commit. Self-review, wrong reviewer, stale commit, extra fields and unstructured
chat do not authorize integration. Old M1 submit_review remains message-only.
A later branch commit invalidates approval on the next task read, review, feedback,
or integration request; branch identity is checked again atomically at promotion.
There is no Git filesystem watcher claiming immediate background invalidation.

Integration validates dependencies, scope, clean worktrees, overlap and current
peer approval. It persists a running reservation before Git mutations. It creates
a unique staging/integration/TASK-xxx-<attempt-hash> branch and candidate worktree,
merges the reviewed commit there, and runs the configured test argument array
with that worktree as cwd. Git hooks and commit signing are disabled for these
service Git commands. Git CLI calls use asyncio.create_subprocess_exec argument
arrays, never shell strings or GitPython. Git environment overrides are stripped
to prevent redirected index/repository writes. New merge commits are attributed
to the local DevMesh integration service.

Main must be unchecked out in every worktree. This avoids leaving its working tree
and index inconsistent after a ref promotion. After exit 0, the runner checks
candidate HEAD, clean files, task scope and ancestry. A single Git update-ref
transaction verifies the reviewed task ref and tested candidate ref and compares
main against its recorded original commit before moving main to the tested
candidate. Concurrent changes to any of those refs stop promotion. The candidate
is immutable Git content; no untested tip is substituted. There is no push.
Conflicts, nonzero tests, timeout, dirty candidate or concurrent ref changes
preserve main, record redacted diagnostics, and consume one failed attempt.

Repeated integration request IDs replay the persisted result without another
merge/test/retry. Running/interrupted attempts are not replayed. On restart a
running attempt blocks its task, records interruption, and requests human action.
Inspect the stored candidate/main identities and actual refs before planning
replacement work: a crash after Git promotion but before SQLite completion can
leave main advanced with a blocked task. Git refs and SQLite cannot be committed
as one transaction. M3 chooses operator inspection over an uncertain retry.

## Exact-action approval and operator boundary

All configured test-command executions require a single-use local operator
approval, including harmless commands. This conservative rule covers destructive
scripts without pretending regex classification can prove them safe. Approval
is bound to SHA-256 of the exact unredacted action: kind, arguments, repository,
worktree, scope, target and context (task ID and reviewed commit for integration).
Only the digest and redacted action are persisted. Changing even redacted-away
argument values changes the digest. A generic chat message saying approved has
no authority. Decide through the local operator API using the returned request
ID and exact fingerprint. Consuming a permit happens durably before execution;
crashes never restore it for automatic replay.

The reusable ApprovalGuard.execute boundary rejects pending, denied, consumed,
wrong-action or missing permits before invoking its callback. The production
runner uses it for integration execution. No generic remote shell, deployment,
or file-edit endpoint is introduced. Path/scope enforcement rejects unsafe task
writes even if an unrelated permit exists. Future executors must use this same
boundary; M3 cannot intercept commands run directly outside DevMesh.

Destructive reasons identify DROP/TRUNCATE, rm recursive/force forms, PowerShell
Remove-Item, directory/removal commands, reset/clean/force push, deployment,
.git/config targets, and targets outside the assigned worktree. Detection is
advisory explanation for a request that requires approval anyway. It cannot
analyze arbitrary languages, encoded commands, aliases, script contents, Git
custom drivers, subprocess trees or runtime behavior. Review the original action
locally, including the configured test program, before approving it. Approved
programs are trusted with normal user permissions; worktrees are not containers.

The append-only audit_logs table records pending requests, approved/denied
operator decisions, execution attempts (including blocked attempts), failures and
outcomes with timestamps and pre-persistence redaction. SQLite triggers reject
UPDATE and DELETE. The database owner can replace the DB/drop triggers; this is
not a tamper-proof external audit service. Pattern-based redaction retains M1/M2
false positives and cannot detect every unlabeled/encoded secret.

All identities are unauthenticated local labels. Any local HTTP client can claim
an agent role or invoke the operator API. Keep loopback-only; an approval record
is an explicit local operator decision, not proof of an authenticated human.
There is no ApprovalCenter frontend.

## REST and MCP schemas

Swagger /docs and /openapi.json contain the strict request schemas. Bodies reject
unknown fields. request_id is 1-80 ASCII letters/digits/hyphens; use a fresh ID for
a new operation and reuse it for transport retry. Reusing a persisted ID with a
different payload gives 409. Replay responses are original snapshots; GET the
task for current state. All responses/errors containing stored text are redacted;
validation errors never echo raw input. Use returned task IDs/revisions.

| Route | Request / result |
| --- | --- |
| POST /api/tasks | TaskCreate: request_id, title, description, category (optional backend), assigned_agent (optional), dependencies (default []), touched_files_scope, acceptance_criteria -> 201 full task |
| GET /api/tasks | {tasks: [...]} including terminal tasks |
| GET /api/tasks/{id} | Full task; invalidates approval if branch commit changed |
| POST /api/tasks/{id}/status | request_id, agent, expected_revision >=1, status -> full task |
| POST /api/tasks/{id}/workspace | request_id -> task with actual branch/worktree |
| POST /api/tasks/{id}/review | request_id -> task with exact commit/diff and durable peer message |
| POST /api/tasks/{id}/feedback | request_id, agent, commit (40-64 hex), verdict APPROVED or CHANGES_REQUESTED, feedback -> full task |
| POST /api/tasks/{id}/api-contract-challenge | request_id, agent, content -> task_id/message_id; durable API_CONTRACT_CHALLENGE to peer |
| POST /api/tasks/{id}/integration-action | request_id -> attempt_id/action; use this exact ID again for integrate |
| POST /api/tasks/{id}/integrate | request_id, approval_id -> task/promoted/candidate/test_log; a failed executed attempt returns 200 with promoted=false |
| POST /api/approvals | kind command/write/deploy, arguments[], repository, worktree, scope[], target (nullable), context (default {}) -> 201 id/fingerprint/state/action |
| GET /api/approvals | {requests: [...]} with redacted documents, including decided/consumed records |
| POST /api/approvals/{id}/decision | fingerprint (64 hex), decision approved or denied, reason -> id/state |
| GET /api/audit-logs?after_id=0 | {audit_logs: [...]} ordered, at most 100; page using the last ID |

404: unknown task or approval. 409: invalid/stale/terminal transitions, wrong
agent/reviewer/commit, incomplete dependencies, missing approval, unsafe worktree,
scope/overlap/dirty state, Git failure, unconfigured test command, or uncertain
integration. 422: outer schema validation and invalid scope syntax. A limit hit
returns the now-blocked task (integration adds promoted=false) and sends a human
notification. Failed review/test attempts preserve feedback and increment once;
preflight rejections do not consume a failure retry.

Either agent may raise API_CONTRACT_CHALLENGE. It is a durable escalation to the
peer, never an automatic edit or approval of API_CONTRACT.md. Agreed contract
changes still use the existing deliberation/operator workflow.
New task messages participate in inbox/ack/long-poll persistence; task operations
wake same-process pollers. There are no new task WebSocket events; recover task
state through REST and durable inbox reads.

New MCP tool: update_task_status(task_id, request_id, agent, expected_revision,
status), using the same REST schema. It does not bypass protected transitions.
Other M3 operations use REST. Restart/refresh devmesh-bridge to discover all 11
tools. If registration already points to C:\DevMesh\mcp_bridge\devmesh_mcp.py,
no registration edit is needed. If it still points to old staging, manually
replace only that server entry through Antigravity's own MCP settings:

```json
"devmesh-bridge": {
  "command": "C:\\DevMesh\\.venv\\Scripts\\python.exe",
  "args": ["C:\\DevMesh\\mcp_bridge\\devmesh_mcp.py"]
}
```

Do not read/copy credential-bearing MCP configs. Retain human reactivation when
Antigravity stops polling; indefinite polling remains unverified.

## Disposable-repository manual verification

Start this lab in a terminal. It creates a new directory under TEMP (or a new
explicit --directory), an isolated SQLite database, an initialized Git repository,
and a harmless configured assertion command. Existing directories are refused.
It does not populate the real DevMesh database. It leaves lab artifacts for
inspection, performs no cleanup/removal, and binds only to loopback port 8421.

```powershell
Set-Location C:\DevMesh
.\.venv\Scripts\python.exe scripts\manual_m3_lab.py
```

Copy the printed Repository path into $repo below. In another PowerShell terminal,
run this sequence. It records explicit local peer feedback for a manual test;
it is not evidence of an actual Antigravity/Codex review session.

```powershell
$api = 'http://127.0.0.1:8421'
$repo = Read-Host 'Paste the disposable Repository path printed by the lab'
function Post-Lab($path, $body) {
    Invoke-RestMethod "$api$path" -Method Post -ContentType 'application/json' -Body ($body | ConvertTo-Json -Depth 30)
}
function New-LabRequestId { [guid]::NewGuid().ToString() }
$t = Post-Lab '/api/tasks' @{
    request_id=(New-LabRequestId); title='Write greeting'; description='Create a greeting file'
    touched_files_scope=@('a.txt'); acceptance_criteria=@('a.txt contains hello')
}
foreach ($status in @('assigned', 'in_progress')) {
    $t = Post-Lab "/api/tasks/$($t.id)/status" @{
        request_id=(New-LabRequestId); agent='codex'; expected_revision=$t.revision; status=$status
    }
}
$t = Post-Lab "/api/tasks/$($t.id)/workspace" @{request_id=(New-LabRequestId)}
[System.IO.File]::WriteAllText((Join-Path $t.worktree_path 'a.txt'), "hello`r`n", [System.Text.UTF8Encoding]::new($false))
git -C $t.worktree_path add -- a.txt
git -C $t.worktree_path -c user.name='Manual lab operator' -c user.email='operator@localhost' commit -m 'Write greeting'
$t = Post-Lab "/api/tasks/$($t.id)/review" @{request_id=(New-LabRequestId)}
Invoke-RestMethod "$api/api/inbox/antigravity"
$t.review
$t = Post-Lab "/api/tasks/$($t.id)/feedback" @{
    request_id=(New-LabRequestId); agent='antigravity'; commit=$t.review.commit
    verdict='APPROVED'; feedback='Manual test peer feedback after inspecting the exact diff'
}
$integrationId = New-LabRequestId
$planned = Post-Lab "/api/tasks/$($t.id)/integration-action" @{request_id=$integrationId}
$approval = Post-Lab '/api/approvals' $planned.action
$approval | ConvertTo-Json -Depth 20
```

Inspect the displayed original action/fingerprint locally before deciding.
The pending request itself grants no permission. Use `denied` to reject it, or
this explicit operator decision after inspection:

```powershell
Post-Lab "/api/approvals/$($approval.id)/decision" @{
    fingerprint=$approval.fingerprint; decision='approved'; reason='Inspected this exact disposable lab action'
}
$result = Post-Lab "/api/tasks/$($t.id)/integrate" @{
    request_id=$integrationId; approval_id=$approval.id
}
$result | ConvertTo-Json -Depth 30
git -C $repo rev-parse main
Invoke-RestMethod "$api/api/context/TASKS.md"
Invoke-RestMethod "$api/api/audit-logs"
```

Expect promoted=true, task status complete, retry_count=0, and main equal to
result.candidate. Repeat the exact integrate request: the candidate, completion
and retry count must remain unchanged. Main remains unchecked out because the
lab root is on operator. The candidate merge lives in its own worktree.

Additional checks (use new labs/tasks/request IDs as appropriate):

1. Before approving, attempt integrate with the pending approval ID. Expect 409
   and unchanged main. Then approve explicitly and retry the same integration ID.
2. Submit peer feedback with agent codex (the author), or a different commit.
   Expect 409; generic submit_review messages must not authorize integration.
3. Commit another a.txt edit after peer APPROVED, then GET the task. Expect
   in_progress with review=null. A new review and exact-action permit are required.
4. Write a value other than hello so the lab's assertion fails. Review that
   commit explicitly; approve the exact test action. Expect promoted=false,
   redacted test_log, retry_count=1 and unchanged main. Repeat the same integration
   ID and verify no extra retry. Use fresh review/integration IDs to reach three
   failed attempts; expect blocked and a durable human notification.
5. Create a dependent planned task referencing the unfinished task. Assignment
   must fail with 409. With a completed predecessor, assignment must succeed.
6. Create another task/worktree editing a.txt. If its declared scope is b.txt,
   overlap checking must create a blocked serialization task; repeated detection
   must not create another. Both declared scopes containing a.txt still require
   explicit ordering rather than silent conflict resolution.
7. Before integration, inspect the exact command from integration-action; alter
   its arguments/scope/worktree in a separate approval request. That permit must
   not authorize the original integration action. Decide a request denied and
   verify it cannot execute. Audit logs show the decision and blocked attempt.
8. Stop the lab with Ctrl+C. To inspect persistence without recreating its files,
   restart using the printed lab paths in the following command. Review task state,
   history, durable inbox and regenerated TASKS.md. The lab helper deliberately
   refuses --directory pointing at an existing directory; use the explicit app
   startup below instead.

```powershell
Set-Location C:\DevMesh
$env:DEVMESH_EXECUTION_REPOSITORY = Read-Host 'Existing disposable Repository path'
$env:DEVMESH_DATABASE = Join-Path (Split-Path $env:DEVMESH_EXECUTION_REPOSITORY -Parent) 'state.sqlite'
$env:DEVMESH_TEST_COMMAND = ConvertTo-Json -InputObject @(
    'C:\DevMesh\.venv\Scripts\python.exe', '-c',
    'from pathlib import Path; assert Path("a.txt").read_text().strip() == "hello"; print("lab tests passed")'
) -Compress
.\.venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8421 --no-access-log
```

These environment settings apply to that PowerShell process. Use a fresh terminal
for ordinary DevMesh startup. Do not repoint the lab settings to C:\DevMesh.
Automated tests cover concurrent ref changes deterministically; do not race edits
or deployments against the implementation repository to reproduce them manually.

## Verification boundary

See MILESTONE3_VERIFICATION.md for the final automated result. Manual commands are
reproduction instructions, not claims that a live two-agent M3 session passed.
Real agent task execution, a real M3 Antigravity session, indefinite polling,
hostile-process isolation, and authenticated operator identity remain unverified
or intentionally outside this milestone. M1/M2 prior live/manual evidence remains
separate. No frontend, autonomous scheduler, Milestone 5 orchestration, deployment,
paid API integration or credential-config inspection is included.
