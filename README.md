# localagent

A coding agent in the style of Claude Code. Open a terminal in any project, type `localagent`, and ask for work in
plain words. It plans (`/plan`, visible checklists), reads and edits files (every edit shown as a diff), runs code,
tests and commands, reads documentation pages, uses MCP tool servers, and fixes its own mistakes.

**Safety:** every file it writes is verified before it is kept — Python, JavaScript/TypeScript, Go, Rust, C/C++, PHP,
Ruby, shell, PowerShell, JSON, TOML, YAML — and broken writes are rolled back;
code runs in a locked-down **Docker sandbox**; nothing runs without your approval unless a permission rule allows it.
**Cost:** free — Gemini's free tier (cheap "lite" models first, strong models only when a task struggles, client-side
rate limiting, retries, fallback) and/or a **local model** (llama.cpp or Ollama).

**New here? Read `docs/04-start-from-zero.md`** — every step from an empty computer, no prior knowledge needed.
**Already set up? `docs/06-run-and-verify.md`** — how to run it every way, and how to check that everything works.

| Task | Command (PowerShell) |
|---|---|
| Install the `localagent` command (once) | `powershell -ExecutionPolicy Bypass -File .\install.ps1`, then open a new terminal |
| Work on a project | `cd C:\my\project` then `localagent` (setup is chosen automatically) |
| Check the setup | `localagent --doctor` |
| Optional local model (fallback) | `.\serve.ps1` in its own window |
| Build the sandbox image (once) | `docker build -t localagent-sandbox:latest -f docker/sandbox.Dockerfile docker` |
| Agent in a container / as a service | see `docs/docker.md` (`docker compose up -d --build`) |
| How it works (explain it to anyone) | `docs/00-overview.md` |
| Tests (no model needed) | `.venv\Scripts\python -m unittest discover -s tests -t .` |
| Measure quality | `.venv\Scripts\python -m evals.run_evals --backend gemini --sandbox docker [--trace]` |
| Rebuild + audit docs | `.venv\Scripts\python tools/build_docs.py` |

In the chat: `/plan <request>`, `/init` (write `AGENT.md` notes), `/compact`, `/undo`, `/reset`, `/stats`, `/help`,
`/quit`; Ctrl+C stops a task; end a line with `\` or use `"""` blocks for multi-line requests.
Permission rules and MCP servers: `.localagent\settings.json` (see `docs/settings.md`, `docs/mcp.md`).

Everything the agent can do, in one place: `docs/05-user-manual.md`.

Docs reading order: `04-start-from-zero` (set up) → `00-overview` (how it all works) → `06-run-and-verify` (run it) →
`05-user-manual` (reference) → the file pages in the order listed in `00-overview` → `03-build-log` → `02-roadmap`.
`docs/` is generated from `docs_src/` — edit `docs_src/`, never `docs/`.
