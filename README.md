# localagent

A coding agent in the style of Claude Code. Open a terminal in any project, type `localagent`, and ask for work in
plain words. It plans (`/plan`, visible checklists), reads and edits files (every edit shown as a diff), runs code,
tests and commands, reads documentation pages, uses MCP tool servers, and fixes its own mistakes.

**Online or offline:** it thinks with Google Gemini (free tier) when you are online and with a model on your own
computer (llama.cpp, Ollama, LM Studio) when you are not — switching by itself when the internet goes away, or when
you say so (`/model offline`, `localagent --offline`). Offline, nothing leaves your computer.
**Safety:** every file it writes is verified before it is kept — Python, JavaScript/TypeScript, Go, Rust, C/C++, PHP,
Ruby, shell, PowerShell, JSON (with comments where allowed), TOML, YAML — and broken writes are rolled back byte for
byte; code runs in a locked-down **Docker sandbox**; nothing runs without your approval unless a permission rule allows it.
**Cost:** free — Gemini's free tier (cheap "lite" models first, strong models only when a task struggles, client-side
rate limiting, retries) and/or the local model.

**New here? Read `docs/04-start-from-zero.md`** — every step from an empty computer, no prior knowledge needed.
**Already set up? `docs/06-run-and-verify.md`** — how to run it every way, and how to check that everything works.

| Task | Command (PowerShell) |
|---|---|
| Install the `localagent` command (once) | `powershell -ExecutionPolicy Bypass -File .\install.ps1`, then open a new terminal |
| Work on a project | `cd C:\my\project` then `localagent` (setup is chosen automatically) |
| Only the local model / only Gemini | `localagent --offline` / `localagent --online` (or `/model` in the chat) |
| One answer, for scripts | `localagent -p "what does this project do?"` |
| Continue yesterday's conversation | `localagent -c` |
| Check the setup | `localagent --doctor` |
| Build the sandbox image (once) | `docker build -t localagent-sandbox:latest -f docker/sandbox.Dockerfile docker` |
| Agent in a container / as a service | see `docs/docker.md` (`docker compose up -d --build`) |
| How it works (explain it to anyone) | `docs/00-overview.md` |
| Tests (no model needed) | `.venv\Scripts\python -m unittest discover -s tests -t .` |
| Measure quality | `.venv\Scripts\python -m evals.run_evals --sandbox docker [--offline] [--trace]` |
| Rebuild + audit docs | `.venv\Scripts\python tools/build_docs.py` |
| Turn on the commit guard (once per clone) | `git config core.hooksPath .githooks` |

In the chat: `/plan <request>`, `/model [online|offline|auto]`, `/init` (write `AGENT.md` notes), `/compact`, `/undo`,
`/clear`, `/resume`, `/stats`, `/permissions`, `/doctor`, `/help`, `/quit`; `!command` runs a command yourself,
`# note` saves a note in `AGENT.md`, `@file` attaches a file; Ctrl+C stops a task; end a line with `\` or use `"""`
blocks for multi-line requests.
Permission rules, MCP servers, hooks, test command and settings: `.localagent\settings.json` (see `docs/settings.md`,
`docs/mcp.md`).

Everything the agent can do, in one place: `docs/05-user-manual.md`. What could come next: `docs/02-roadmap.md`.

Docs reading order: `04-start-from-zero` (set up) → `00-overview` (how it all works) → `06-run-and-verify` (run it) →
`05-user-manual` (reference) → the file pages in the order listed in `00-overview` → `03-build-log` → `02-roadmap`.
`docs/` is generated from `docs_src/` — edit `docs_src/`, never `docs/`.

Not included in the repository (see `.gitignore`): the local model (`models/`) and llama.cpp (`bin/`) — download them as
described in `docs/01-setup-and-models.md`.
