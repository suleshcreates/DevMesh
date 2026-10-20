# Milestone 4 dashboard manual verification

Date: 20 October 2026 (Asia/Calcutta).
Repository: C:\DevMesh. Branch: milestone-4.
Implementation commit checked before this follow-up: 3885aad.

## Evidence and results

The user supplied screenshots of the dashboard at http://localhost:5173 after
receiving instructions to restart the normal backend on 127.0.0.1:8420. Codex
visually inspected those attached screenshots; this follow-up did not automate
or independently record the user's browser actions.

| Check | Result | Evidence |
| --- | --- | --- |
| Overview and connection | pass, screenshot | Overview loaded with API connected and dashboard socket Connected; agent badges correctly displayed Unknown with no heartbeat recorded |
| Conversation history | pass, screenshot | Persisted proposal and review rendered with sender/recipient, type, timestamps, IDs, acknowledged/pending badges, expanded Markdown and raw-content text |
| Recorded agreement | pass, screenshot | DEC-001 displayed RECORD/agreed, round 2, revision 3, proposal/counter-proposal and critique history |
| Human-resolved decision | pass, screenshot | DEC-002 displayed RECORD/human_resolved, round 3, revision 5, all four proposals and recorded critique/risk/tradeoff content |
| Task board | pass, screenshot | Empty task columns rendered for the normal database; the disposable M3 lab task was not shown as a normal project task |
| Approval Center | pass, screenshot | Displayed no escalated decisions awaiting resolution and no exact-action requests in the normal database |
| Reconnect without browser refresh | user-reported pass | User reported the requested reconnect check done; no independent stop/start trace or timing was captured |

The screenshots show real previously persisted verification records. They do not
prove live agent activity, automatic acknowledgement, project execution, or an
approval mutation. No such operation was requested or performed in this follow-up.

The conversation screenshot retains the previously documented redaction of
ordinary delivered=false prose. This is prior backend pattern-based redaction,
not a new claim that the browser redacted a secret or damaged an acknowledgement.

## Existing automated evidence

MILESTONE4.md records the implementation's checks: 119 backend tests, 3 frontend
tests, 10 Edge browser scenarios, a build with zero TypeScript errors, and zero
npm audit vulnerabilities at that verification time. Browser mutation coverage
used isolated fixtures. Those checks were not rerun for this documentation-only
manual-evidence update, and the audit result is not a guarantee about future
dependency vulnerability reports.

## Remaining boundaries

- The latest screenshots and user report supplement the original runbook's
  unperformed normal-backend restart/browser check. The restart and reconnect
  actions themselves were not independently observed by Codex.
- Real operator mutations were not exercised against the normal database;
  approval/denial, resolution and retry evidence remains from isolated tests.
- Unknown heartbeat badges are appropriate; a connected dashboard does not
  prove either agent is running or working.
- Goal entry remains a transient draft. No project-start orchestration, task
  executor loop or Milestone 5 lifecycle was added.
- Existing local-only unauthenticated identity, cooperative worktree, redaction,
  single-worker, notification replay and indefinite-polling limits remain.
- No Milestone 5 implementation, deployment, merge, config/credential inspection,
  or real-data mutation was performed in this follow-up.

The real DevMesh main ref remained b302df01e3b9d4f8dc30515958a7b50f4828e8ef
when checked before recording this documentation.
