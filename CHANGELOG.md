# Changelog

All notable changes to localagent. The full story of every decision, with measurements, is in
`docs/03-build-log.md`.

## 0.6.0 — 21 November 2026

### Added
- **Any brain you like.** `localagent setup` offers Google Gemini (free key), your own key for OpenAI, Anthropic,
  OpenRouter, Groq, Mistral, DeepSeek or any OpenAI-compatible address (checked by listing the service's models, a
  good model suggested), a model server you already run (Ollama, LM Studio, llama.cpp, vLLM, Jan — found by itself),
  or a free model it downloads and runs for you. `--provider NAME[:MODEL]`, `LOCALAGENT_PROVIDER`,
  `LOCALAGENT_LOCAL_SERVER`. An optional request field a service refuses is left out by itself.
- **Plug and play:** the first `localagent` with nothing set up offers the setup at once; the Windows installer runs
  it right after adding the folder to PATH.
- **A product website** for any static host (`tools/build_site.py`): a home page with a player that replays real
  recorded sessions, a download page with every checksum, a setup builder that turns your choices into exact steps,
  user guides (Get started, Choose a model, Troubleshooting) and all documentation with search, light and dark themes,
  phone layouts. Self-hosted fonts, nothing loaded from other sites, a strict security policy, and a build that checks
  every page, link and section — and that no page names the repository's owner.
- **Releases from a tag:** pushing `v0.6.0` builds the downloads on a clean Windows machine and publishes them with
  the notes from this file.
- **Online and offline.** Modes `auto` (default: Gemini, and the local model the moment Gemini cannot be reached),
  `online` and `offline`: `--online`, `--offline`, `--mode`, `LOCALAGENT_MODE`, and `/model` in the chat (the
  conversation is kept when switching).
- **The local model starts itself.** llama.cpp and the model are found and started when needed (graphics card fitted
  automatically), shown in progress, and stopped on exit — even when the terminal window is closed.
- **`localagent setup`**, a guided first run: the online model and its key (checked, then saved), the offline model,
  Docker.
- **`localagent models`**: list, `download` (resumable, checked against the publisher's checksum), `use`.
- **Portable Windows download**: one zip with Python included, `install.cmd`/`uninstall.cmd`; and a wheel for pip/pipx.
- Claude-Code habits: `-p` (one answer, for scripts), `-c`, `/resume`, `/clear`, `/permissions`, `/doctor`,
  `!command`, `# note`, `@file`, coloured diffs, `--version`, the answering model after every request.
- Tests run with the project's own tool (pytest, unittest, npm, go, cargo) and its own `.venv`; `test_command` setting.
- Settings files can set environment variables (`"env"`).
- A pre-commit guard against large files, keys, `.env` files and public author addresses; CI on Windows and Linux
  (Python 3.10 to 3.14) and a job that builds and runs the Docker sandbox for real.
- MIT license and third-party notices; a code of conduct, issue forms and a pull-request template.

### Changed
- The Windows download leaves out mypy, which only `--type-check` uses: 22.8 MB instead of 35.7 MB.
- On Linux and macOS the user settings file (it can hold keys) is readable by its owner only.
- The repository root is tidier: the developer scripts moved to `scripts\`, the community files to `.github\`.

### Fixed
Every item was reproduced before it was fixed and has a test.
- **Security:** a model-chosen file name could run a command through a hook; an allow rule for one command also allowed
  a second one chained with `;` or `&&`; an "always" answer saved a wildcard rule.
- **Files:** every edit on Windows rewrote the whole file with CRLF line endings (even rejected ones); an edit with
  empty search text could scramble a file; `/undo` could erase your own later edits; Python files starting with a
  byte-order mark (as PowerShell writes them) were called syntax errors.
- **Wrong results:** `search` only looked at Python files; valid `tsconfig.json`, C++ `.h` headers and Rust files naming
  unwritten modules were rejected; tests in a plain `tests/` folder were not found; binary files were read as text.
- **Robustness:** without internet every step waited 28 s of retries; a timeout could hang forever when the program
  had started another; programs could read the keyboard; empty and cut-off model replies; `--resume` after `/reset`;
  a broken log line crashed `--resume`; the history size ignored tool arguments; usage counts could be lost; the HTTP
  server's sessions each had the whole rate limit; quota days were an hour off on Windows in summer; a typo in a
  variable crashed the program.
- **Found by CI and live tests:** `/undo` could miss a same-size edit made within one clock tick (Windows); a folder's
  short 8.3 name broke file listing; the Docker sandbox could not read a workspace on Linux (it now runs as your user
  id); answers piped into `localagent setup` from Windows PowerShell were refused (byte-order mark), and a scripted
  setup waited forever for a key; a local model's answer was printed twice when it spoke before using a tool.

## 0.5.0 — 5 November 2026
The first complete version: the agent loop with verify-before-write, Gemini with rate limits and fallbacks, the local
model through llama.cpp, the Docker sandbox, permission rules, MCP servers, hooks, `/plan`, `/undo`, the HTTP service,
15 eval tasks and documentation of every line.
