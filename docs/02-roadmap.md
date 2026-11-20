# Roadmap — from this laptop agent to a scalable system

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

### Next 🔜
1. **Git-based checkpoints** (commit before each request) for projects too large for in-memory snapshots.
2. **More hook points** (before a tool runs, able to block it).
3. **MCP over HTTP** (remote servers) in addition to local stdio servers.
4. **Images** (screenshots) as input, for models that accept them.
5. **Java and C# checks** (they need a whole project to compile, so a per-file check gives false alarms today).

### Phase 3 leftovers 🔜 Measure, then tune
1. Run `python -m evals.run_evals` for each candidate model; keep a table of pass-rate / time. **Pick the model with
   data, not hunches.**
2. Nine eval tasks exist now; keep adding tasks from your real work (the harness takes any seed files plus a check function).
3. Tune using the evals: `num_ctx`, `temperature`, system-prompt wording, tool descriptions, `max_fix_attempts`.
4. An import smoke-test (`python -c "import module"`) after writes.
5. A unified-diff `apply_patch` tool (`multi_edit` already covers the common case).

### Phase 4 📋 Smarter context
* Model-written summaries when history nears the limit (instead of just blanking old tool output).
* Planner/worker split: a small fast model for search and summarising, a bigger one for editing (subagents).
* Hooks (run a command before/after a tool) and an MCP client so tools can be added without code changes.

### Phase 5 📋 Run it as a service
* Streaming (WebSocket/SSE) replies, per-client request-rate limits, HTTPS and persistence across restarts for the HTTP server.
* A job queue; per-session sandbox containers are possible already (`--sandbox docker` on the server).
* Model serving on a GPU machine with vLLM or llama.cpp via the existing `openai` backend; request batching there.
* Telemetry (tokens, latency, pass-rate over time) and dashboards; log retention policy.

### Phase 6 📋 Breadth
* Language plug-ins for the verifier: TypeScript (`tsc`, `eslint`), Go (`go vet`), Rust (`cargo check`).
* IDE extension, CI integration (run the agent on failing builds), team-shared `AGENT.md` conventions.
* Optional: fine-tune a small model on your own *successful* logged sessions (`session.py` already records them).

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
