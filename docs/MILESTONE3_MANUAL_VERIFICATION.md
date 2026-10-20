# Milestone 3 operator lab verification

Date: 20 October 2026 (Asia/Calcutta).
Implementation: C:\DevMesh, branch milestone-3, commit 91dbb1e.

## Scope and provenance

The user executed the documented REST/operator workflow in a disposable lab:

- Lab API: http://127.0.0.1:8421.
- Lab directory: C:\Users\sules\AppData\Local\Temp\devmesh-m3-90908e507f294d5195f4729e08901648.
- Execution repository: the lab directory's repository subdirectory.

Peer feedback was explicitly operator-simulated, using the antigravity role
after inspecting the exact greeting diff. This is not a real two-agent M3 review
or execution session. The manual fixture is isolated from real DevMesh task data.

## User-supplied workflow results

1. Created TASK-001 with scope a.txt, assigned it to codex, and moved it through
   assigned and in_progress.
2. Created feat/codex-TASK-001 in its assigned disposable worktree.
3. Committed a.txt containing hello at d70422e01d22007a14c8674cf580fb99100800f8.
4. Requested review. The snapshot captured that exact commit, the greeting-only
   diff, reviewer antigravity, and initially no verdict.
5. Submitted structured operator-simulated peer APPROVED feedback for that commit.
6. Requested an exact integration action and created pending approval
   9bfaf217-1aa6-4f85-8582-714ba90c2c12, fingerprint
   e6f11f1d20a85e78b745993116953024e58a72474b984557787819d0f06348bd.
7. Attempted integration while pending: HTTP 409, disposable main unchanged.
8. Inspected the action: Python asserts a.txt contains hello, inside the exact
   disposable integration worktree, with a.txt scope and the reviewed commit.
9. Explicitly approved that fingerprint through the local operator route.
10. Integrated using the original request ID and approval. Tests printed
    lab tests passed; promoted=true, status complete, retry_count 0.
11. Disposable main matched tested candidate 4bfc66542e94026b5c6ef602aab33228c3e61c78.
12. Replayed the identical integration request and approval. All seven user
    checks passed: same candidate, unchanged main, still complete, unchanged
    retry count, revision, tool calls and history count.

## Independent read-only checks by Codex

After replay, direct lab GET requests and Git ref reads confirmed:

- TASK-001 complete, revision 8, retry_count 0, tool_calls 6.
- Review remained bound to d70422e01d22007a14c8674cf580fb99100800f8.
- The exact-action approval state was consumed.
- Its audit events were pending, execution_attempt (blocked), approved,
  execution_attempt (allowed), execution_outcome.
- Exactly one blocked attempt, one allowed execution attempt, one execution
  outcome and one complete history entry existed for this workflow. Replay
  added no second allowed execution or completion.
- Disposable main was 4bfc66542e94026b5c6ef602aab33228c3e61c78.
- Real C:\DevMesh main remained b302df01e3b9d4f8dc30515958a7b50f4828e8ef.

Selected-field evidence is saved in m3-manual-lab-result.json. The independent
checks did not approve actions, execute integration, acknowledge messages, alter
Git refs, or change task state. No credential files or MCP configs were read.

## Verification boundary

The core manual operator flow passed: worktree/review snapshot, pending approval
blocking, exact-action human approval, passing-test promotion and replay.

The implementation's existing report records 117 passing tests in 201.82 seconds.
Those tests were not rerun for this documentation-only follow-up. The new check
was read-only inspection of the user's completed live lab, not a suite rerun.

Additional failure paths retain existing automated coverage; no new manual pass
is claimed for overlap serialization, failing-test retries, concurrent ref
changes, approval invalidation, interrupted integration or tool-budget limits.
The M3 update_task_status MCP tool and real two-agent M3 execution were not tested
in this operator lab. Prior M1/M2 Antigravity evidence remains separate.

Remaining limits are unchanged: unauthenticated local identities, cooperative
worktree boundaries rather than OS isolation, single-worker operation,
redaction limitations, interrupted-integration operator inspection and unverified
indefinite polling. No frontend or Milestone 5 orchestration was implemented.

The user may stop the lab server with Ctrl+C. Lab files and branches remain for
inspection; no recursive deletion or other cleanup was performed.
