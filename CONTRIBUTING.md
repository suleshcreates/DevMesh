# Contributing to DevMesh

Read [AGENTS.md](AGENTS.md), [FEASIBILITY.md](docs/FEASIBILITY.md), and the relevant milestone runbook before changing behavior.

## Workflow

1. Create a focused branch and describe the concrete problem.
2. Preserve SQLite as the source of truth and existing acknowledgement, review, approval, and execution-budget boundaries.
3. Verify external CLI/MCP interfaces against installed help, schemas, or official documentation.
4. Add meaningful regression coverage for changed behavior. Use disposable repositories and injected fake agents in automated tests; never invoke real inference from pytest.
5. Run the relevant checks from the README. For backend changes, run the full pytest suite; for frontend changes, run frontend tests and the production build.
6. Open a pull request with the resulting behavior, validation, and known limitations. Main requires peer approval and passing tests.

Never commit credentials, MCP configuration contents, `.env` files, SQLite databases, local runtime state, or dependency directories. Do not change another contributor's work or force-push shared branches.

Windows/PowerShell is the primary development environment. Keep runtime paths portable with `pathlib`. Report manual evidence separately from automated results; fake-adapter success is not proof of real-agent execution.

## Reporting problems

Include the version/commit, Windows/Python/Node versions, steps to reproduce, expected behavior, and redacted errors. Avoid uploading full environment dumps or configuration files. For security-sensitive reports, follow [SECURITY.md](SECURITY.md).

The project has not selected an open-source license. Discuss intended redistribution or licensing with the repository owner.
