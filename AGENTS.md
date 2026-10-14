# DevMesh: standing rules for Codex

DevMesh is a local Windows orchestrator that lets Codex and Google Antigravity
exchange proposals/critiques and build software together, with a human approving
risky decisions.

## Hard rules
- No paid API keys. Never use OPENAI_API_KEY or GEMINI_API_KEY.
- Never read, parse, copy, or log `~/.codex/auth.json` or any credential file.
  Let the `codex` CLI authenticate itself by inheriting the user environment.
- Do not guess external interfaces (Codex CLI flags, Antigravity config paths,
  MCP behaviour). Verify with `--help`, schemas, or docs; if unverifiable, say so
  in docs/FEASIBILITY.md instead of inventing.
- No placeholders, TODO stubs, or mock chat data in production code.
- SQLite (aiosqlite, WAL mode) is the single source of truth. Markdown files in
  `.devmesh/` are generated from the DB, never edited back.
- Redact secrets (API keys, JWTs, private keys, .env values) before anything is
  stored, logged, or broadcast.
- Limits: MAX_CRITIQUE_ROUNDS=3, MAX_TASK_RETRIES=3, MAX_TOOL_CALLS_PER_TASK=40,
  TASK_TIMEOUT_SECONDS=600. Hitting a limit escalates to the human; it never loops.
- Neither agent may mark a decision "agreed" alone: needs a proposal from one and
  an explicit AGREE with zero blocking risks from the other.
- Never merge to main unless peer review == APPROVED and tests exit 0.
- Destructive actions (DROP, rm -rf, edits to .git/config, writes outside the
  assigned worktree, deploys) require human approval.

## Stack
Python 3.11+, FastAPI, uvicorn, aiosqlite, Pydantic v2, official `mcp` SDK
(FastMCP, stdio), pytest + pytest-asyncio, httpx. Frontend: Vite + React +
TypeScript + Tailwind. Use the `git` CLI via asyncio subprocess (no GitPython).
Server: http://127.0.0.1:8420. UI: http://localhost:5173.

## Working style
- Windows-first: pathlib, no bash-isms, handle CRLF and long paths.
- Plan briefly, implement, then RUN the tests and fix failures before reporting.
- Keep each milestone scoped to what is asked. Don't pre-build later milestones.
- Finish with: files created, commands to run, test results, known limitations.
- Commit after each green milestone.
