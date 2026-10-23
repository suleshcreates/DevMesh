# Security policy

DevMesh is experimental local software. The current development branch is the maintenance target; no stable release or security-support guarantee is offered.

## Deployment boundary

- Keep the API on `127.0.0.1` and the dashboard local. Role and operator labels are not authenticated.
- Treat worktree checks as cooperative constraints, not process isolation.
- Review the exact executable, arguments, repository, scope, commit, and approval fingerprint before approving a test action.
- Production agent editing is disabled; manual handoff is required.
- Never share credential files, active MCP configuration contents, `.env` values, database snapshots, or unredacted logs in public issues.

## Reporting a vulnerability

If the repository's Security tab offers private vulnerability reporting, use that channel. Otherwise, open a minimal issue requesting a private contact channel without exploit details or sensitive data. Do not publish credentials or an actionable exploit before a private channel is established.

Include affected commits, reproducible steps using disposable data, expected versus actual behavior, and the impact. The maintainer has not committed to a response-time SLA.
