# User manual — everything localagent can do, in one place

This is the complete reference: every command, flag, setting, tool and behaviour, with what it is for. New to all of
this? Do the installation in `04-start-from-zero.md` first. Curious *how* a part works inside? Each section names the
file; that file's page explains its code line by line.

---
## 1. In one minute
localagent is a coding agent like Claude Code. Open a terminal **in a project folder**, type `localagent`, and ask in
plain words. It reads your files, plans, edits (showing you a diff of every change), runs code and tests, reads
documentation pages, and fixes its own mistakes — checking every file it writes before keeping it, and asking you
before anything runs. It thinks with Gemini when you are online and with a model on your own computer when you are
not — switching by itself, or when you say so.

```
cd C:\my\project
localagent
you> /init                                            (first time only: writes AGENT.md project notes)
you> Why does the login test fail?                    (a question: it looks, then answers)
you> Explain @src/auth.py                             (@ attaches a file)
you> /plan Add a --verbose flag to the CLI with tests (bigger work: plan first, then you approve)
you> Rename parse_args to build_parser everywhere      (a task: it edits, verifies, runs tests)
you> /undo                                            (changed your mind? the last change back)
you> /model offline                                   (from now on the local model answers)
```

---
## 2. What you can ask
| Kind of request | Example | What happens |
|---|---|---|
| A question about anything | "What is a Python decorator?" | answered directly, usually in **1 model call**, no tools |
| A question about this project | "How does the quota work here?" | reads/searches what it needs, then answers with file names |
| A small task | "Fix the off-by-one in pages.py" | runs tests, reads, edits, re-runs tests, finishes |
| A bigger task | `/plan Add CSV export with tests` | read-only investigation → numbered plan → your yes → work |
| A task in another language | "Create greet.ts exporting greet(name)" | written and syntax-checked with that language's tool |
| A broad investigation | "Use a helper to map the payment flow" | a helper agent searches in its own context, reports back |
| Documentation lookup | "Check the requests docs for timeouts" | asks to open the page (`fetch_url`), then uses it |
| Your own shortcut | `/review src/api.py` | runs the text of `.localagent/commands/review.md` |

Tips: say *what* and *where* ("in `tests/test_api.py`"), mention how to verify ("all tests must pass"), and split very
large jobs into steps. If something is ambiguous in a way that matters, the agent asks one short question.

---
## 3. Chat commands
| Command | Does | File |
|---|---|---|
| `/plan <request>` | look first (read-only), show a numbered plan, carry it out only if you answer `y` | `cli.py`, `agent.py` |
| `/model` | show the mode, who answered last, and what online and offline would use | `cli.py` |
| `/model online\|offline\|auto` (or `/online`, `/offline`, `/auto`) | switch who answers; the conversation and files stay ([01](01-setup-and-models.md)) | `cli.py`, `localserver.py` |
| `/init` | write/update `AGENT.md` — project notes read at the start of every session | `cli.py` |
| `/compact` | replace the conversation with a short summary (saves tokens; also automatic) | `agent.py` |
| `/undo` | revert the files the last request changed — a file you edited since is left alone (off for huge folders — use git) | `checkpoint.py` |
| `/clear` (or `/reset`) | forget the conversation; files stay | `agent.py` |
| `/resume` / `/resume 3` | list this project's last 10 conversations / continue number 3 | `cli.py`, `session.py` |
| `/stats` | model calls, tokens, time, each Gemini model's use today, and whether you are offline | `meter.py`, `ratelimit.py` |
| `/permissions` | the allow and deny rules in force | `cli.py` |
| `/doctor` | the setup check, inside the chat | `doctor.py` |
| `/help` | list commands, including your custom ones | `cli.py` |
| `/quit`, `/exit` (or Ctrl+D) | leave | `cli.py` |
| `/<your command>` | a custom command from `.localagent/commands/<name>.md` (section 7) | `settings.py` |
| `!command` | run a command yourself in the project folder — no model call, no question; its output goes with your next request | `cli.py` |
| `# note` | add a note to `AGENT.md` (used from the next answer on) | `cli.py` |
| `@path` in a request | attach that file's contents (saves the model a read) | `cli.py` |
| Ctrl+C | stop the current task; the conversation stays usable | `cli.py`, `agent.py` |
| `\` at line end, or `"""` lines | type or paste a multi-line request | `cli.py` |

A mistyped command (`/hepl`) is answered locally — it never costs a model call. After every request the last line
shows calls, tokens, time and **which model answered**.

---
## 4. Start-up flags
| Flag | Meaning |
|---|---|
| *(none)* | automatic setup (mode `auto`): Gemini if `GEMINI_API_KEY` is set, with the local model as the offline backup (started when needed); the local model alone without a key; Docker sandbox if ready; 32K history budget with Gemini |
| `--online` / `--offline` / `--mode auto\|online\|offline` | who answers: only Gemini / only the local model / automatic |
| `-p "request"` (`--print`) | answer one request, print only the answer, exit (`-p -` reads the request from input) |
| `-c` (`--continue`, `--resume`) | continue the newest conversation of this folder |
| `--version` | print the version |
| `--backend gemini\|local\|openai\|ollama` | which model service, by hand (the mode is then ignored) |
| `--model NAME` | model name for `openai`/`ollama` (e.g. `qwen3-8b`) |
| `--api-base URL` | address of an OpenAI-style server (default `http://127.0.0.1:8080/v1`, where `serve.ps1` listens) |
| `--fallback local\|openai\|ollama` | with `--backend gemini`: a local model to use when every Gemini model fails |
| `--sandbox local\|docker` | where code runs: this computer, or a locked-down container |
| `--network` | allow internet inside the Docker sandbox (e.g. `pip install`) |
| `--allow-shell` | give the model a shell **on this computer** (asks before every command) |
| `--auto-approve` | never ask before running things (deny rules still apply) — sensible only with the sandbox |
| `--workspace PATH` | work on another folder than the current one |
| `--num-ctx N` | history budget in tokens |
| `--reasoning low` | less hidden "thinking" on models that support it (faster, fewer tokens) |
| `--type-check` | also run mypy on Python files |
| `--doctor` | check the whole setup and exit |
| `--no-auto` | no automatic choices |

Service mode (`python -m localagent.server`) has its own flags: `--host`, `--port`, `--token`, `--root`,
`--max-sessions`, `--session-ttl`, plus the shared `--mode`/`--online`/`--offline`, `--backend`, `--model`,
`--api-base`, `--sandbox`, and `--auto-approve` (see `server.md`).

---
## 5. Environment variables (set once with `setx NAME "value"`, then open a new terminal)
| Variable | Meaning |
|---|---|
| `GEMINI_API_KEY` | your Gemini key (never put it in code or in a project's settings file) |
| `LOCALAGENT_MODE` | `auto` (default), `online` or `offline` |
| `LOCALAGENT_MODEL_FILE`, `LOCALAGENT_LLAMA_SERVER` | the local model (`.gguf`) and llama.cpp's `llama-server` (default: looked for, see `01-setup-and-models.md`) |
| `LOCALAGENT_GPU_LAYERS` | model layers on the graphics card (default: as many as fit; `0` = processor only) |
| `LOCALAGENT_LOCAL_CTX`, `LOCALAGENT_THREADS` | the local model's context window (default 8192) and CPU threads (default: automatic) |
| `LOCALAGENT_GEMINI_TIER` | `free` (default) or `paid` — billing enabled: strong models first, higher limits |
| `LOCALAGENT_GEMINI_MODELS` | everyday model chain, comma-separated (default `gemini-3.5-flash-lite,gemini-3.1-flash-lite`) |
| `LOCALAGENT_GEMINI_STRONG` | reserve/strong models (default `gemini-3.5-flash,gemini-2.5-flash`) |
| `LOCALAGENT_GEMINI_LIMITS` | per-model limits `model=rpm/rpd`, comma-separated, e.g. `gemini-3.5-flash=5/20` |
| `LOCALAGENT_RPM` / `LOCALAGENT_RPD` / `LOCALAGENT_TPM` | default requests/minute, requests/day, tokens/minute per model |
| `LOCALAGENT_BACKEND`, `LOCALAGENT_MODEL`, `LOCALAGENT_API_BASE`, `LOCALAGENT_API_KEY` | same as the flags |
| `LOCALAGENT_FALLBACK`, `LOCALAGENT_SANDBOX`, `LOCALAGENT_WORKSPACE`, `LOCALAGENT_NUM_CTX`, `LOCALAGENT_REASONING` | same as the flags |
| `LOCALAGENT_USAGE_FILE` | where daily quota counts are kept (default `%USERPROFILE%\.localagent\usage.json`) |
| `OLLAMA_HOST` | address of Ollama (default `http://localhost:11434`) |
| `LOCALAGENT_TOKEN` | the bearer token of the HTTP service |

Order of strength: flag > environment variable > a settings file's `"env"` > automatic choice > built-in default.
A typo (`LOCALAGENT_NUM_CTX=8k`) is reported as one `setup problem` line naming the variable.

---
## 6. Settings files and permission rules (`settings.md`)
`%USERPROFILE%\.localagent\settings.json` (all projects) and `<project>\.localagent\settings.json` (one project):
```json
{
  "permissions": {
    "allow": ["run_tests", "run_python(*)", "run_shell(git status*)", "fetch_url(https://docs.python.org/*)"],
    "deny":  ["run_shell(*rm *)", "run_shell(*--force*)"]
  },
  "mcpServers": {
    "files": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "."]}
  },
  "hooks": {"after_edit": "ruff check {file}", "after_request": "python -m pytest -q"},
  "test_command": "npm test",
  "env": {"LOCALAGENT_MODE": "offline"}
}
```
- **deny** wins over everything (refused without asking); **allow** runs without asking; anything else is asked.
- Answering `a` (always) to a question saves a narrow allow rule in the project file — exactly that command line.
- Rule names: `run_python(file)`, `run_tests`, `run_shell(command)`, `fetch_url(url)`, `mcp__<server>__<tool>`; `*` is a
  wildcard. A shell line of several commands (`a && b`, `a | b`) is allowed only when **every** command is; lines with
  `$(...)`, backticks, parentheses or `<`/`>` are always asked.
- **mcpServers** adds tools from MCP servers (`mcp.md`); the banner says how many tools each server added.
- **hooks** run your own commands automatically: `after_edit` after every write (`{file}` = the file, quoted safely —
  add no quotes yourself; a failure is shown to the model so it fixes the problem), `after_request` after every request
  (e.g. your tests; shown to you).
- **test_command** is what `run_tests` runs. Without it the agent picks pytest (if installed) or unittest for Python,
  `npm test`, `go test ./...` or `cargo test` — using the project's own `.venv` for Python.
- **env** sets any variable from section 5 unless it is already set (only put a key in the *user* file: a project file
  may be committed to git).

## 7. Custom commands
A file `<project>\.localagent\commands\review.md` (or in `%USERPROFILE%\.localagent\commands\`) becomes `/review`.
Its text is the request; `$ARGUMENTS` is replaced by what you type after the command; the first line is shown in
`/help`. Project commands replace user commands with the same name; built-in commands cannot be replaced.

---
## 8. The tools the model can use (`tools.py`, `agent.py`)
| Tool | Does | Changes files? | Asks you? |
|---|---|---|---|
| `list_files`, `read_file`, `search`, `outline` | look at the project (read-only ones run in parallel; `search` covers every text file and honours `.gitignore`; binary files are refused) | no | no |
| `write_file`, `edit_file` (`all=true` renames), `multi_edit` | change files — **verified, rolled back if broken**, diff shown to you | yes | no (undo with `/undo`) |
| `run_python`, `run_tests` | run code with the project's own Python — verified again first; tests with the project's tool | maybe | yes, unless a rule allows |
| `run_shell` | any command (Docker sandbox, or this PC with `--allow-shell`) | maybe | yes, unless a rule allows |
| `fetch_url` | read a web page as text (2 MB cap) | no | yes, unless a rule allows |
| `todo` | show its plan as a checklist | no | no |
| `delegate` | start a read-only helper agent with a fresh context | no | no (costs model calls) |
| `finish` | end the task with the answer/summary in the same response | no | no |
| `mcp__<server>__<tool>` | tools from your MCP servers | depends | yes, unless a rule allows |

---
## 9. Models, quotas and API calls (`backends.py`, `resilient.py`, `ratelimit.py`)
- **Free tier (default):** everyday chain `gemini-3.5-flash-lite` → `gemini-3.1-flash-lite` (large daily quotas), then
  the strong reserve `gemini-3.5-flash` → `gemini-2.5-flash` (about **20 requests/day each** on a free key), then your
  local model (in auto mode; started when needed). After **2 failed steps** in one request the strong reserve goes first
  for the rest of that request ("escalation").
- **No internet:** when Gemini's name cannot be looked up, or a network error repeats, every Gemini model is skipped
  for a minute and the local model answers at once (before 0.6: 28 seconds of retries on every single step). A 3-second
  connection check decides when to try Gemini again; you see `Gemini is unreachable ...` and later `back online`.
- **Paid tier** (`LOCALAGENT_GEMINI_TIER=paid`): strong models first, limits 1000/minute and 10 000/day per model.
- **Before every request** the limiter waits if the per-minute request or token budget is used, and skips a model whose
  daily quota is used. Counts are saved in `usage.json` (shared by all projects and containers) and reset at midnight
  US Pacific time.
- **When Google refuses:** overload (503) and rate limit (429) are retried — waiting exactly as long as Google asks;
  a daily-quota refusal marks the model used up (with Google's real number, shown in `/stats`); a retired model (404)
  is skipped; anything else is reported.
- **One project, one key.** Google enforces limits per project and treats several projects operated as one as quota
  circumvention (projects have been set to 0 requests). For more quota, enable billing on your project and set the
  paid tier.

## 10. Safety (`verify.py`, `tools.py`, `settings.py`, `runners.py`)
1. **Write gate:** every Python/JSON (also with comments, like `tsconfig.json`)/TOML/YAML/JavaScript/TypeScript/Go/
   Rust/C/C++/PHP/Ruby/shell/PowerShell file is checked after writing (with the language's own tool when installed);
   broken → restored byte for byte, errors to the model, three strikes → it stops and explains. Every file keeps its
   own line breaks (LF or CRLF) and byte-order mark.
2. **Run gate:** code is verified again before running; files changed this session must all pass before tests run.
3. **Permission rules** and **approval questions** before anything runs or reaches the internet; a rule can never be
   stretched to a second command hidden behind `;` or `&&`.
4. **Sandbox:** with Docker, code runs as a normal user in a container that sees only the project folder, has no
   internet (unless `--network`), 1 CPU, 1 GB, 256 processes, and a hard time limit. On this computer too, a time
   limit stops the program **and everything it started**, and programs never read your keyboard.
5. **Hooks are injection-safe:** a file name the model chose is quoted before it goes into your hook command.
6. **Undo, interrupts, logs:** `/undo` per request (your own later edits are kept), Ctrl+C keeps the history valid,
   every message is logged in `<project>\.localagent\sessions`.

## 11. How tokens and calls are saved (measured where possible)
| Technique | Saves |
|---|---|
| `finish` in the same response as the last step | one call per request |
| Batching several tool calls per response; read-only calls in parallel | calls and waiting |
| File overview (crowded folders summarised, `.gitignore` honoured) + `AGENT.md` in the system prompt | 2–3 "look around" calls per task |
| `@file` attachments | the read call (measured: 1 call instead of 2, half the tokens) |
| Offline detection (no retry waits without internet) | 28 s of waiting per step |
| `search`, `outline`, line-range reads, `edit_file`, `multi_edit` | tokens read and written |
| Output truncation (start + end kept), diffs shown only to you | tokens |
| Cheap-first routing, escalation only on trouble | strong-model quota |
| Temperature 0, the "you changed nothing" nudge, the repeat guard | wasted retries and loops |
| Compaction (`/compact`, automatic at 60 % of the budget) | tokens on every later call |
| Helper agents for broad searches | main-context tokens |
| Client-side rate limiting, retries with the server's delay | failed requests |
| Unknown commands answered locally | calls |
| Append-only history (provider prompt caching) | provider-side token processing |

Measured: the same 9 tasks went from 66 to 34 calls (−48 %) and from ~103k to ~55k tokens (−46 %) after the call
optimisations; the final 15-task suite (Python, JavaScript, Go, TypeScript, two questions) took 48 calls and ~111k
tokens; a plain question takes 1 call. Each call carries a fixed
~1 900 tokens (rules ~730, tool definitions ~1 190) plus the conversation.

## 12. Files the agent creates
| Path | What |
|---|---|
| `<project>\AGENT.md` | project notes (`/init`); read every session — edit freely |
| `<project>\.localagent\sessions\*.jsonl` | conversation logs (used by `-c` and `/resume`) |
| `<project>\.localagent\settings.json` | project permission rules / MCP servers ("always" answers land here) |
| `<project>\.localagent\commands\*.md` | project custom commands |
| `%USERPROFILE%\.localagent\usage.json` | daily quota counts per model |
| `%USERPROFILE%\.localagent\settings.json`, `commands\` | your rules / commands for every project |
| `%USERPROFILE%\.localagent\bin\localagent.cmd` | the launcher written by `install.ps1` |
| `%USERPROFILE%\.localagent\llama\`, `models\` | where an installed copy keeps llama.cpp and the local model (`01-setup-and-models.md`) |
| `%USERPROFILE%\.localagent\llama-server.log` | what the local model server printed (read it when it will not start) |

## 13. Ways to run it
Chat, one request with `-p`, inside Docker, as an HTTP service, online or offline, and the evals — each with exact,
tested commands: `06-run-and-verify.md`. Publishing the project to GitHub safely: section 8 there.

## 14. Limits (honest)
- The free lite models are good at everyday tasks (15/15 on the eval suite) but weaker than Claude on large, subtle
  changes; the strong models have tiny free quotas; the local 8B model is slower and weaker still.
- Checks prove code *parses* and passes the tests you have — not that it does what you meant. Review the diffs.
- The local model needs ~6 GB of free memory; with less, it loads slowly or Windows stops it.
- Not built: images as input, git-based checkpoints, remote (HTTP) MCP servers, web search, an IDE extension
  (`02-roadmap.md` lists everything planned).

## 15. FAQ
- **It asks too often.** Answer `a` once, or add allow rules (section 6).
- **It says the daily quota is used up.** `/stats` shows which model; the chain continues with the next one and, in
  auto mode, ends with the local model; quotas reset at midnight Pacific time.
- **A pasted request became several requests.** Wrap it in `"""` lines.
- **It changed something I did not want.** `/undo`, then ask more precisely (or use `/plan` first).
- **It is slow.** Google's speed varies (1–30 s per call); check `/stats`; questions are 1 call, tasks 2–8. The last
  line after each request shows which model answered — the local model is 10-30 times slower.
- **Can it work offline?** Yes: `localagent --offline` or `/model offline`; in auto mode it switches by itself when the
  internet goes away (`01-setup-and-models.md`).
- **How do I use it from a script?** `localagent -p "your request"` prints only the answer.
