# Antigravity activation

Copy the current project's activation prompt from the dashboard. Your agent role
is antigravity; the prompt contains the real project ID.

- Loop on devmesh-bridge await_next_task(agent_name="antigravity",
  timeout_seconds=45). no_task means poll again.
- For DEVMESH assignments, fetch get_assignment_context with the exact project,
  correlation and agent. Ignore obsolete/wrong-project assignments. Acknowledge
  accepted messages explicitly; reading alone never acknowledges work.
- Analyze and review read-only. Do not retrieve peer analysis before both
  independent analyses are collected. Critiques must match the typed schema.
- Use the task's actual worktree, branch, file scope, acceptance criteria and
  locked memory version. Production execution is a manual operator handoff;
  do not automatically edit files or run commands. Never run integration,
  deployment, deletion, or out-of-scope operations through another tool.
- Return results with report_project_result and all exact correlation/phase/
  task/decision/reviewed-commit fields. Structured task review is protected M3
  feedback; old submit_review is message-only and cannot authorize integration.
- Report progress, observable tool identifiers, errors and stalls through
  report_project_usage. Preserve 3 critique rounds, 3 failed task attempts,
  40 observable tools per task and 600 seconds per execution attempt. Do not
  impersonate the operator or approve test commands.
- Stop on PROJECT_COMPLETE, cancellation, or a safety/human pause. Never claim
  background execution without an actual running operation.

If the IDE stops or exhausts its step budget, the human must re-paste the current
activation prompt. Bounded polling success does not prove indefinite execution.
