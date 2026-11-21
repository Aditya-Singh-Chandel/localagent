# Roadmap — from a laptop agent to a scalable system

Legend: ✅ built and tested · 🔜 next · 📋 planned. Status is honest: only ✅ items exist in the code today.

## Phases

### Phase 1 ✅ Core loop with verification gates
Agent loop, the first tools (list, read, write, edit, run), sandboxed paths, write gate with rollback, run gate, approval prompt, usage meter, tests with a
fake model, and the self-auditing docs system (`tools/build_docs.py`).

### Phase 2 ✅ Usability, fewer calls, and a way to scale
| Feature | File | Why it matters |
|---|---|---|
| Streaming output | `llm.py` | Text appears as generated; on a CPU the wait is otherwise silent for minutes |
| `search` tool | `tools.py` | One call finds code instead of reading files (fewer calls and tokens) |
| File tree + `AGENT.md` in the prompt | `context.py` | Removes the first 2–3 "look around" calls of every task |
| Several tool calls per turn | `context.py`, `agent.py` | Independent reads/searches share one model call |
| `/undo` checkpoints | `checkpoint.py` | Experiment freely; revert a bad request in one command |
| Session log + `--resume` | `session.py` | Audit trail and continuation across restarts |
| OpenAI-compatible backend | `openai_compat.py`, `backends.py` | Same agent can drive LM Studio, llama.cpp, vLLM, or a GPU box |
| End-of-request workspace check | `cli.py`, `tools.py` | Whole-project verification at zero model cost |
| ruff + mypy layers | `verify.py` | Real lint/type errors fed back to the model (installed with `pip install -e ".[dev]"`) |
| Eval harness | `evals/` | Measure pass-rate, calls, tokens, time per model and per change |

### Phase 3 ✅ Reliability and speed on a real model (built in this session)
| Feature | File | Result |
|---|---|---|
| Real model running (llama.cpp + Qwen3-8B), thinking off, Vulkan GPU offload | `serve.ps1` | +45 % tokens/s at 11 GPU layers |
| `--trace` and measured evals | `evals/run_evals.py` | exposed the real failures (see `03-build-log.md`) |
| Prompt rules 8-10 (act, don't describe; bug-fix recipe; verify what was asked) | `context.py` | fix_bug and add_method started passing |
| Temperature 0 | `config.py` | repeatable runs |
| "You changed nothing" nudge (once per request) | `agent.py` | rescues replies that explain instead of editing |
| Parallel read-only tool calls | `agent.py`, `tools.py` | less waiting when several files are read |
| `ruff format` after every verified write | `tools.py` | consistent style, zero model calls |
| `multi_edit` (several edits, one call, one verification) | `tools.py` | fewer calls than repeated `edit_file` |

### Phase 4 (part) ✅ Smarter context
`outline` tool (classes/functions with line numbers via `ast`) so big projects fit a small context.

### Phase 5 (part) ✅ Service mode
`localagent/server.py`: authenticated HTTP API, one isolated workspace and agent per session, concurrent sessions,
a session cap (HTTP 429) and idle expiry.

### Phase 5 (more) ✅ Hosted model + real isolation
| Feature | File | Why |
|---|---|---|
| Gemini backend through its OpenAI-compatible API (thought signatures kept) | `backends.py`, `openai_compat.py` | 10-20x faster per call than the local 8B model, much stronger |
| Client-side limiter: RPM, TPM, RPD (persisted, Pacific-time reset) | `ratelimit.py` | stay inside the free tier instead of hitting 429s |
| Retries (server `retryDelay`, else exponential backoff) and model chain with local fallback | `resilient.py` | 503 "high demand" and 429 happen in normal use |
| Docker code sandbox (no network, CPU/memory/process caps, non-root, timeout kill) | `runners.py`, `docker/` | run model-written code safely |
| `run_shell` tool, only inside the sandbox | `tools.py` | pip/pytest/git like Claude Code, without risking the host |
| Agent image + compose service | `Dockerfile`, `docker-compose.yml` | run everything isolated, or as a service |
| `--doctor` covers Docker and Gemini (key, models, quota use) | `doctor.py` | beginners see exactly what is missing |

### Phase 6 ✅ Fewer API calls, cheap-first routing (measured: −48 % calls, −46 % tokens, still 9/9)
| Feature | File |
|---|---|
| `finish` tool (no separate final-answer call; refused on failures) | `agent.py` |
| Call-saving prompt (batching, no root listing, no re-reads/re-runs, no unrequested files) | `context.py` |
| Lite models first, strong models as reserve, escalation after 2 failures | `config.py`, `backends.py`, `resilient.py`, `agent.py` |
| Per-minute limit learned from Google's 429 | `ratelimit.py`, `resilient.py` |

### Phase 7 ✅ Claude-Code-like daily use
| Feature | Where |
|---|---|
| `localagent` command usable in any folder (`install.ps1`); current folder is the workspace | `install.ps1`, `config.py` |
| Fast folder walking that never enters `node_modules`, `.venv`, `.localagent` ... | `sandbox.py` (`walk_files`) |
| One quota file per user (`~/.localagent/usage.json`), shared by all projects and containers | `config.py` |
| Verification for JSON, TOML and JavaScript (`node --check`) besides Python | `verify.py` |
| `todo` checklist tool (shown in full, no extra calls) | `tools.py`, `agent.py` |
| Shell on this computer, opt-in (`--allow-shell`), approval for every command; git + node in the sandbox | `runners.py`, `docker/` |
| Compaction: `/compact` and automatic summaries of long conversations | `agent.py`, `cli.py` |
| `/init` writes `AGENT.md` project notes; notes reload immediately | `cli.py`, `agent.py` |
| Retired models (HTTP 404) are skipped automatically | `resilient.py` |
| Multi-file package and JavaScript eval tasks | `evals/tasks.py` |

### Phase 8 ✅ The rest of the Claude Code experience
| Feature | Where |
|---|---|
| Automatic setup: Gemini / Docker sandbox / local fallback chosen from what is available | `cli.py` |
| Permission rules in `settings.json` (allow / deny / ask), "always" answers saved | `settings.py`, `tools.py`, `cli.py` |
| Diff of every edit on screen (never sent to the model) | `tools.py`, `agent.py` |
| `/plan`: read-only investigation, plan, then execution on approval | `agent.py`, `cli.py` |
| `fetch_url` web-page reader (approval or rule per website) | `web.py`, `tools.py` |
| MCP client (stdio servers from settings, tools as `mcp__server__tool`) | `mcp.py`, `tools.py` |
| Ctrl+C stops a task and repairs the history; resume repairs crashed sessions | `agent.py`, `cli.py` |
| Multi-line input, byte-order-mark-safe input | `cli.py` |
| `/init` restricted to writing (cannot "fix things" instead), checked afterwards | `agent.py`, `cli.py` |
| Learned per-minute limits saved for other runs; "edit together" hint on undefined names | `ratelimit.py`, `tools.py` |

### Phase 9 ✅ Breadth: every common language, any question, real-size projects
| Feature | Where |
|---|---|
| Syntax checks for TypeScript (Node's own stripper), Go, Rust, C/C++, PHP, Ruby, shell, PowerShell, YAML | `verify.py` |
| Sandbox image runs Go, C/C++, Rust and Node code, not only Python | `docker/sandbox.Dockerfile` |
| Questions answered directly (1 call), with the full answer in the reply | `context.py`, `agent.py` |
| Streaming answers (Gemini and llama.cpp, tool calls and thought signatures included) | `openai_compat.py`, `cli.py` |
| Helper agents (`delegate`): read-only investigations in a fresh context | `agent.py` |
| Custom slash commands (`.localagent/commands/*.md`); unknown commands cost no call | `settings.py`, `cli.py` |
| Paid-tier preset and `LOCALAGENT_RPM/RPD/TPM` overrides | `config.py`, `backends.py` |
| Big folders: checks only of changed files, snapshot limits, search limit | `tools.py`, `checkpoint.py`, `cli.py` |
| Eval suite: 15 tasks incl. two questions, Go and TypeScript | `evals/tasks.py` |
| Hooks (`after_edit` feeds failures back to the model, `after_request`) | `tools.py`, `cli.py`, `settings.py` |
| Complete user manual | `05-user-manual.md` |

### Phase 10 ✅ Version 0.6: a product you can rely on — fixes, online/offline, Claude-Code habits
Every item started from a problem that was **reproduced first** (see `03-build-log.md`, section 6g).

| Area | Built | Where |
|---|---|---|
| **Online / offline** | Modes `auto` / `online` / `offline`; `/model` (+ `/online`, `/offline`, `/auto`) switches mid-conversation; `--online`, `--offline`, `--mode`, `LOCALAGENT_MODE` | `cli.py`, `agent.py` |
| | The local model as a backend: uses a running llama.cpp / LM Studio / Ollama, or **starts llama.cpp itself** (GPU fitted automatically), stops it on exit — even when the window is closed | `localserver.py` |
| | Offline detection: no internet → Gemini skipped for 60 s, local model answers at once (was 28 s of retries per step); "back online" by itself | `resilient.py`, `llm.py` |
| | Conversations fitted to the local 8K window; Gemini-only fields never sent to other servers | `llm.py`, `openai_compat.py` |
| **Security** | Hook file names quoted (a file name could run a command); permission rules checked per command of a `&&`/`;`/`|` chain; saved rules exact; no keyboard for programs | `sandbox.py`, `settings.py`, `tools.py` |
| **Data integrity** | Line breaks and byte-order marks kept on every edit and rollback; empty `old` refused; exact-byte rollback | `tools.py` |
| **Correctness** | `search` covers all languages and honours `.gitignore`; binary files refused; JSONC (`tsconfig.json`) accepted; C++ `.h` headers accepted; Rust checked without its modules; tests found in plain `tests/` folders; pytest / npm / go / cargo test detection; the project's own `.venv` used; history counts tool arguments | `tools.py`, `verify.py`, `runners.py`, `agent.py` |
| **Robustness** | Timeouts kill the whole process tree; empty replies retried; cut-off replies explained (and answers rescued); `--resume` respects `/clear` and `/compact`, survives a broken log line; atomic usage file; one rate limiter per model across server sessions; correct Pacific midnight on Windows; friendly errors for typos in variables | `sandbox.py`, `agent.py`, `session.py`, `ratelimit.py`, `config.py` |
| **Claude-Code habits** | `-p` (one answer, for scripts), `-c`, `/resume` list, `/clear`, `/permissions`, `/doctor`, `/exit`; `!command`; `# note` → `AGENT.md`; `@file` attachments; coloured diffs; `--version`; who answered after every request | `cli.py` |
| **Undo** | Per-request steps, quiet requests skipped, your later edits kept, contents stored once | `checkpoint.py` |
| **Context** | Overview summarises crowded folders (this project: from 32 DLL names to the real code) | `context.py`, `sandbox.py` |
| **Publishing** | `.gitignore`, `.gitattributes`, commit guard (size, keys, `.env`, private author e-mail), version in one place | `tools/check_commit.py`, `.githooks/`, `pyproject.toml` |

Result: 201 tests (60 new); with Phase 11: 216, lint and types clean, docs audit 35 pages, live checks with Gemini and the real
`llama-server.exe`.

### Phase 11 ✅ Ready to download (product readiness)
| Built | Where |
|---|---|
| MIT license, third-party notices, package metadata (the wheel carries them) | `LICENSE`, `THIRD_PARTY_NOTICES.md`, `pyproject.toml` |
| `localagent setup`: the guided first run (key checked then saved, model sized to memory, Docker) | `models.py` |
| `localagent models`: list / download / use, with a catalog of three Qwen3 sizes | `models.py` |
| Verified, resumable downloads; safe unpacking of zip and tar.gz | `fetch.py` |
| Portable Windows zip with Python 3.14 included, `install.cmd`/`uninstall.cmd`; wheel; `SHA256SUMS.txt` | `tools/build_release.py` |
| `CHANGELOG.md`, `SECURITY.md`, `CONTRIBUTING.md`; CI on Python 3.10-3.14 and Windows | repository root, `ci.yml` |
| Advice points to `localagent setup` instead of a docs file a download does not contain | `localserver.py`, `doctor.py`, `cli.py` |

Tested as a user would: the zip unzipped into an empty folder ran `--version`, found its own ruff and mypy, rejected
broken code, reported correctly in `--doctor`, and answered a real request in 1 call.

---
## Everything that could come next (the full backlog)
Collected from a review of the whole project. ⭐ = high value for its cost. Ordered within each area by priority.

### A. Before the download website (product readiness)
1. ✅ A license — MIT (Phase 11).
2. ✅ One-step Windows download — the portable zip with Python included (Phase 11). Still open: a signed `.exe`
   installer (Windows SmartScreen warns about unsigned downloads; a code-signing certificate costs money).
3. ✅ First-run setup — `localagent setup` (Phase 11).
4. ✅ Model manager — `localagent models` (Phase 11).
5. Publish to PyPI so `pipx install localagent` works on every system; then winget / Scoop packages.
6. `install.sh` for macOS and Linux; test there (CI already runs Linux).
7. Releases on GitHub with notes (✅ `CHANGELOG.md`, `SECURITY.md`, `CONTRIBUTING.md` exist; the release itself
   needs a public place for the files — the repository is private, see B).
8. ✅ CI on Windows too, and Python 3.14 (Phases 10-11).
9. Update check (`localagent --version` says when a newer release exists), uninstaller.

### B. The website (next)
0. ⭐ **Where the files live** — a decision: the repository is private, so its releases cannot be downloaded by the
   public. Options: make it public; a second, public repository only for releases and the site; or a host such as
   Cloudflare Pages / Netlify for the site plus a storage bucket for the zip.
1. Landing page: what it does, a short demo (recorded terminal), online vs offline, privacy.
2. Download page per operating system, with checksums and install steps.
3. Documentation site generated from `docs/` (it is already Markdown); search.
4. Changelog and FAQ pages; a contact or issue link.

### C. Claude Code parity, still missing
1. ⭐ **Git workflow commands**: `/commit` (the agent writes the message from the diff), `/diff`, `/review` of the
   current changes, `/pr` through the `gh` tool.
2. ⭐ **Interactive terminal polish**: a spinner while the model thinks, a status line (mode, model, tokens), Esc to
   stop, syntax-highlighted code, accept/reject per diff hunk.
3. **More hook points**: `before_tool` (may block an action), `after_tool`, `on_start`, `notification`.
4. **Permission modes** switchable in the chat (plan / accept-edits / ask-everything), like Claude Code's Shift+Tab.
5. **Subagents from files** (`.localagent/agents/*.md` with their own instructions and tools).
6. **Web search** tool (needs a search service; `fetch_url` covers known addresses today).
7. **Images as input** (screenshots of an error) for models that accept them.
8. **Remote MCP servers** (HTTP/SSE) and MCP resources and prompts.
9. Git-based checkpoints for projects too big for in-memory `/undo`.
10. Session export to Markdown, search across past sessions, `/cost` with a per-request budget limit.
11. Background tasks: start a long command, keep chatting, check on it later.

### D. Smarter agent
1. ⭐ **A self-review step** (opt-in): before finishing a task, a cheap model reads the diff and looks for mistakes.
2. ⭐ **Outlines for every language** (JavaScript, TypeScript, Go, Rust, Java) — today `outline` reads Python only.
3. A unified-diff `apply_patch` tool; an AST-aware "rename symbol everywhere" for Python.
4. Planner/worker split: the local model summarises and searches, Gemini edits.
5. Per-model prompt tuning, measured with the evals.
6. Java and C# checks (they need a whole project to compile).
7. An import smoke test after Python writes (`python -c "import module"`).

### E. Models and providers
1. ⭐ **More providers** through the existing OpenAI-style client: Claude (Anthropic API), OpenAI, OpenRouter, Groq,
   Mistral — a provider table instead of Gemini-only settings.
2. Choose one model by name (`/model gemini-3.5-flash`), not only a mode.
3. Gemini's explicit context caching (cheaper long sessions on the paid tier).
4. Two local models (a small fast one, a bigger careful one); speculative decoding for speed.
5. Quota history and charts (`/stats` over days).

### F. Safety
1. Network allow-list inside the sandbox (only `pypi.org`, `registry.npmjs.org` ...) instead of all-or-nothing.
2. Warn before the model writes something that looks like a key into a file (the commit guard's patterns).
3. A Windows Sandbox or WSL runner for people without Docker.
4. Changes across several files kept or undone together (transactions).
5. `--debug` with full traces written to a log for bug reports.

### G. Integrations
1. VS Code extension (a chat panel talking to `localagent.server`).
2. A GitHub Action that runs the agent on a failing build and opens a pull request.
3. HTTP server: streamed answers (SSE), sessions kept across restarts, a small web page to chat in.
4. Slack or Discord bot on top of the server.

### H. Measuring quality
1. More eval tasks: larger projects, refactors across files, JavaScript/Go/Rust projects with dependencies.
2. Run the evals on a schedule and keep a table of pass-rate, calls and tokens per model over time.
3. Property tests for the JSONC stripper and the shell-command splitter.
4. Re-measure the local model (12/15 in 0.5) with 0.6's fitted context and offline detection.

## Where the optimisations come from (and where each one lives)
| Technique | Saves | Status |
|---|---|---|
| Model kept loaded (`keep_alive`) | seconds of reload per call | ✅ `llm.py` |
| Append-only history → server prefix cache | re-reading the whole prompt each call | ✅ `agent.py` |
| File tree + notes preloaded | 2–3 model calls per task | ✅ `context.py` |
| Multiple tool calls per turn | model calls | ✅ prompt rule + loop |
| `search`, line-range reads, `edit_file` | tokens read and written | ✅ `tools.py` |
| Output truncation (keep head and tail) | tokens | ✅ `sandbox.truncate` |
| Old tool output blanked near the limit | avoids context overflow | ✅ `agent._trim_history` |
| Verification before keeping/running | wasted runs, debugging calls | ✅ `tools.py`/`verify.py` |
| Fix-attempt limit with STOP | runaway loops | ✅ `tools.py` |
| Hard turn limit | runaway loops | ✅ `agent.py` |
| Faster CPU settings (flash attention, q8 KV cache, smaller `num_ctx`) | memory and time | ✅ documented in setup |
| Parallel read-only tool execution | wall-clock time | ✅ `agent.py` |
| `multi_edit`, `outline` | model calls, tokens | ✅ `tools.py` |
| GPU layer offload | seconds per token | ✅ `serve.ps1` |
| Temperature 0 + nudge | wasted retries | ✅ `config.py`, `agent.py` |
| Model-written summaries / subagents | context size | 📋 Phase 4 |
| Client-side rate limiting + retries + model fallback | wasted/failed requests, daily quota | ✅ `ratelimit.py`, `resilient.py` |
| `finish` in the same response + batching prompt | ~1 call per request, −48 % calls measured | ✅ `agent.py`, `context.py` |
| Cheap-first routing with escalation | scarce strong-model quota | ✅ `resilient.py` |
| Hidden-thinking control (`--reasoning low`) | tokens and time on thinking models | ✅ `openai_compat.py` |
| Server-side prompt caching (Gemini implicit caching benefits from our append-only history) | tokens | ✅ by design |

## Principles that keep it scalable
1. **Everything behind small interfaces** — `ChatBackend`, `Tool`, the verifier functions. New providers, tools and
   languages are additions, not rewrites.
2. **One `Config` object** — no hidden globals, so a server can run many differently-configured agents.
3. **State is plain files** — conversation logs (JSONL) in each project's `.localagent` folder, quota counts in
   `%USERPROFILE%\.localagent` — easy to read, back up, or move to a database later.
4. **Measure before changing** — the eval harness is the gate for every optimisation.
5. **Docs are generated from code and audited** — no drift, so new contributors can learn from the docs alone.
