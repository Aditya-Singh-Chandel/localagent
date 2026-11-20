# Changelog

All notable changes to localagent. The full story of every decision, with measurements, is in
`docs/03-build-log.md`.

## 0.6.0 — 20 November 2026

### Added
- **Online and offline.** Modes `auto` (default: Gemini, and the local model the moment Gemini cannot be reached),
  `online` and `offline`: `--online`, `--offline`, `--mode`, `LOCALAGENT_MODE`, and `/model` in the chat (the
  conversation is kept when switching).
- **The local model starts itself.** llama.cpp and the model are found and started when needed (graphics card fitted
  automatically), shown in progress, and stopped on exit — even when the terminal window is closed.
- **`localagent setup`**, a guided first run: Gemini key (checked, then saved), the offline model sized for the
  computer's memory, Docker.
- **`localagent models`**: list, `download` (resumable, checked against the publisher's checksum), `use`.
- **Portable Windows download**: one zip with Python included, `install.cmd`/`uninstall.cmd`; and a wheel for pip/pipx.
- Claude-Code habits: `-p` (one answer, for scripts), `-c`, `/resume`, `/clear`, `/permissions`, `/doctor`,
  `!command`, `# note`, `@file`, coloured diffs, `--version`, the answering model after every request.
- Tests run with the project's own tool (pytest, unittest, npm, go, cargo) and its own `.venv`; `test_command` setting.
- Settings files can set environment variables (`"env"`).
- A pre-commit guard against large files, keys, `.env` files and public author addresses; CI on Windows and Linux.
- MIT license and third-party notices.

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

## 0.5.0 — 5 November 2026
The first complete version: the agent loop with verify-before-write, Gemini with rate limits and fallbacks, the local
model through llama.cpp, the Docker sandbox, permission rules, MCP servers, hooks, `/plan`, `/undo`, the HTTP service,
15 eval tasks and documentation of every line.
