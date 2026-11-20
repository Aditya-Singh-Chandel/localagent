# How localagent works — the whole system, explained

Read this page to understand the project well enough to **explain it to anyone**. It tells the story from the
outside in: what the agent is, its parts, one real request followed through every function it touches, and the
safety and cost rules. Every name in `code` font is a real file or function; its own page (see the map at the end)
shows its source code with every line explained. The audit tool (`tools/build_docs.py`) fails if any code line is
left unexplained, so those pages always match the code.

New to computers and terminals? Set it up with `04-start-from-zero.md` first. Want to just use it? `06-run-and-verify.md`.

---
## 1. The one-minute explanation

> localagent is a coding assistant that works in your project folder, like Claude Code. You type what you want in
> plain words. An AI model (Google Gemini, or one running on your own PC) decides what to do, but it cannot touch
> anything itself: it can only *ask* our program to use a tool — read a file, write a file, run the tests. Our
> program carries out each request, **checks every file before keeping it**, runs code inside a sealed Docker box,
> asks you before anything runs, and sends the result back. This repeats until the job is done and verified.

Three ideas carry the whole design:
1. **The model is a brain without hands.** All power is in the tools, and the tools enforce the rules.
2. **Nothing broken is kept.** Every write is verified; a bad write is undone and the exact errors go back to the
   model, which fixes them (verify → fix → re-verify).
3. **Calls are expensive.** Free Gemini allows a limited number of requests per day, so the agent is built to finish
   tasks in as few model calls as possible (a question: 1-2 calls; a small task: 3-8).

---
## 2. The parts, as layers

```
 ┌───────────────────────────────── ENTRY POINTS (how you start it) ─────────────────────────────────┐
 │  cli.py  — the chat in your terminal      server.py — HTTP API      evals/run_evals.py — scoreboard│
 └───────────────────────────────────────────────┬───────────────────────────────────────────────────┘
                                                 │ builds Config → backend → ToolBox → Agent
 ┌──────────────────────────────── THE LOOP (the "agent") ────────────────────────────────────────────┐
 │  agent.py   ask model → run the tools it asked for → send results back → repeat until `finish`     │
 │  context.py the system prompt (rules + AGENT.md + file list)      session.py / meter.py  log & count │
 └───────────────┬─────────────────────────────────────────────────────────────────┬─────────────────┘
                 │ "what next?"                                                     │ "run these tools"
 ┌───────────────▼──────────── THE BRAIN (model access) ───┐  ┌────────────────────▼──── THE HANDS ───────┐
 │ backends.py   picks the client(s) from Config           │  │ tools.py     every tool + write/run gates │
 │ resilient.py  chain of models, retries, escalation      │  │ verify.py    checks 14 file formats       │
 │ ratelimit.py  per-minute / per-day quotas, usage.json   │  │ runners.py   run here or in Docker        │
 │ openai_compat.py  Gemini / llama.cpp wire format        │  │ settings.py  allow/deny permission rules  │
 │ llm.py        HTTP helper, Ollama client                │  │ web.py, mcp.py  web pages, MCP tool servers│
 └─────────────────────────────────────────────────────────┘  │ checkpoint.py  /undo snapshots            │
                                                               └──────────────────────────────────────────┘
 ┌──────────────────────────────── FOUNDATION ────────────────────────────────────────────────────────┐
 │ config.py  every setting in one frozen object      sandbox.py  path confinement, safe subprocesses  │
 └────────────────────────────────────────────────────────────────────────────────────────────────────┘
```
Lower layers never import higher ones, so each piece can be tested alone (that is what `tests/` does with a *fake*
model). `doctor.py` checks the setup; `__main__.py` makes `python -m localagent` work.

---
## 3. One request, followed through the code

A real run (5 Nov 2026), in a folder holding a small expense tracker:
```
you> What does report.js do?
-> read_file {"path": "report.js"}
   1: const fs = require('fs');
-> finish {"summary": "report.js reads expenses.json, adds up all amounts and prints the total ..."}
[2 model calls | 4197 prompt tok | 99 output tok | 9.9s]
```
What happened inside, step by step:

**A. Start-up (once per session)** — `cli.main`
1. `build_parser` + `build_config`: defaults ← environment variables ← your flags, merged into one `Config`
   (`config.overlay`). `auto_configure` fills what you left open: Gemini when a key is set, the Docker sandbox when
   its image exists, a running local model as last fallback, and the 32K history budget (`config.gemini_history`).
2. `load_settings` reads permission rules, MCP servers and hooks from `settings.json` files.
3. `make_backend` builds the brain: for Gemini a `FallbackBackend` — a chain of models, each with its own
   `RateLimiter`. `ToolBox(...)` builds the hands and starts the Docker sandbox runner. `chat(...)` creates the
   `Agent`, whose first message is the system prompt from `context.build_system_prompt`.

**B. Your line arrives** — `cli.chat` → `cli.handle_request`
4. `read_request` reads the line (or a multi-line block). Commands such as `/undo` are handled right there, costing no
   model call. A normal request: `snapshot` saves the folder for `/undo` (`checkpoint.Checkpoints.take`), then
   `handle_request` calls `Agent.run(text)`.

**C. The loop** — `agent.Agent.run`
5. The request is appended to the history (and to the session log, `session.SessionLog`). If the history is getting
   long it is summarised first (`compact`); old tool outputs are blanked when the budget is near (`_trim_history`).
6. **Ask the model:** `backend.chat(history, tool list, on_token)`. In `resilient.FallbackBackend.chat` the first model
   that is not used up is tried: `RateLimiter.before_call` waits if the per-minute limit is near and refuses if today's
   quota is gone; `openai_compat.OpenAICompatClient.chat` sends the request and streams the reply (`read_stream`). A
   busy server (429/503) is retried after the delay Google asks for; a used-up model is skipped for the day; a retired
   model (404) is skipped for good. `Meter.record` counts calls and tokens.
7. **The reply** asks for two tools: `read_file` and `finish`. Each call gets an id; `_execute` runs them
   (several read-only calls at once run in parallel; anything else runs in order and shows its diff).
8. `read_file` → `ToolBox.call` → `_read_file`: `sandbox.resolve_inside` makes sure the path stays inside the
   project, then the file is returned with line numbers (cut to `max_output_chars`).
9. **The finish rules** (`Agent.run`): `finish` is refused — the result is `NOT FINISHED: <reason>` — if another call
   in the same response failed, if it came together with a read (the model cannot know what it has not seen yet), or
   if you asked for a change and nothing was written. Here it came with `read_file`, so it was refused.
10. The results go into the history; back to step 6. The model now sees the file and sends `finish` with the real
   answer. Accepted: `run` returns the summary at once — no separate "final answer" call.

**D. After the answer** — `cli.handle_request`
11. Files changed during the request are verified once more (`ToolBox.check_workspace`), your `after_request` hook
   runs if you set one, and the meter line is printed.

For a **task** ("create add.py and test it") step 7 contains writes and runs:
- `write_file` → `_commit`: write the file → `verify.verify_file` (Python: compile, then `ruff`; other languages
  with their own checker) → **pass:** keep it, auto-format, show you the diff, run the `after_edit` hook; **fail:**
  restore the old content and answer `REJECTED (1/3)` with the exact errors. After 3 rejections of one file the answer
  says `STOP` and the model must explain instead of looping.
- `run_tests` / `run_python` → verify again → `_permitted`: a deny rule refuses, an allow rule lets it through,
  otherwise **you are asked** (`[y]es / [n]o / [a]lways`) → `runners.DockerRunner.run` executes it in the sealed
  container (no internet, 1 CPU, 1 GB, time limit) → `exit code 0` + output.
- The model may put `finish` in the same response as its last write and run: if all of them succeeded, the task ends
  without another call.
- Repeated failures make the agent switch to a stronger model for the rest of the request (`FallbackBackend.escalate`);
  the same identical edit sent twice is refused (repeat guard).

---
## 4. The safety rules, all in one place
| Rule | Where | What it stops |
|---|---|---|
| Paths must stay inside the project | `sandbox.resolve_inside` | reading or writing your other files |
| Every write is verified, bad writes rolled back | `tools._commit`, `verify.py` | broken code being kept |
| Code is verified again before it runs | `tools._run_python`, `_run_tests` | running broken code |
| Permission rules, then your approval | `settings.Permissions`, `tools._permitted` | anything running without your say |
| Docker sandbox | `runners.DockerRunner` | code reaching your files, the internet or all your memory |
| Shell on your PC only with `--allow-shell` | `runners.LocalRunner` | unasked commands on your computer |
| `/undo`, Ctrl+C keeps the history valid | `checkpoint.py`, `Agent.repair` | changes you did not want |

## 5. How calls and tokens are kept low
- Fixed rules in the prompt (`context.py`): batch several tool calls per response, never re-read a file, run once,
  finish in the same response as the last run.
- `finish` instead of a separate final message; plain questions answered without tools.
- Cheap "lite" models first, strong ones only when a request keeps failing (`resilient.py`).
- Line-range reads, `outline` instead of whole files, output caps, diffs shown only to you (never sent to the model).
- Old tool output blanked, long chats summarised (`agent._trim_history`, `compact`); append-only history so the
  provider can reuse its cache.
- Client-side rate limiting (`ratelimit.py`) so quota is not wasted on refused requests.
Measured: a question 1-2 calls; the 15-task test suite 48-64 calls in total.

## 6. Where things are stored
| Path | What |
|---|---|
| `<project>\AGENT.md` | project notes from `/init`, read at the start of every session |
| `<project>\.localagent\sessions\*.jsonl` | the conversation logs (`--resume`) |
| `<project>\.localagent\settings.json`, `commands\` | the project's permission rules, MCP servers, hooks, custom commands |
| `%USERPROFILE%\.localagent\usage.json` | today's quota use per model (shared by every run) |
| `%USERPROFILE%\.localagent\settings.json`, `bin\` | your rules for every project; the `localagent` launcher |
The agent's own folders (`.localagent`, `.git`, `.venv`, caches) are never listed, searched or checked (`SKIP_DIRS`).

---
## 7. Reading the code — in an order that makes sense
Read the pages in this order; each builds only on the ones before it.
| # | Page | Source | What you learn |
|---|---|---|---|
| 1 | `config.md` | `localagent/config.py` | every setting; how flags, environment and defaults combine |
| 2 | `sandbox.md` | `localagent/sandbox.py` | the safety basics: paths, output caps, running programs |
| 3 | `llm.md` | `localagent/llm.py` | how one HTTP call to a model server works |
| 4 | `openai_compat.md` | `localagent/openai_compat.py` | the message format, tool calls, streaming |
| 5 | `ratelimit.md` | `localagent/ratelimit.py` | counting requests per minute and per day |
| 6 | `resilient.md` | `localagent/resilient.py` | retries, the model chain, escalation |
| 7 | `backends.md` | `localagent/backends.py` | turning `Config` into a ready brain |
| 8 | `verify.md` | `localagent/verify.py` | how each language is checked |
| 9 | `runners.md` | `localagent/runners.py` | where code runs: here or in Docker |
| 10 | `settings.md` | `localagent/settings.py` | permission rules, MCP servers, hooks, custom commands |
| 11 | `web.md`, `mcp.md` | `web.py`, `mcp.py` | the two outside tool sources |
| 12 | `tools.md` | `localagent/tools.py` | every tool and the write/run gates |
| 13 | `context.md` | `localagent/context.py` | the rules the model is given |
| 14 | `meter.md`, `session.md`, `checkpoint.md` | small helpers | counting, logging, undo |
| 15 | `agent.md` | `localagent/agent.py` | **the loop** — the heart of the project |
| 16 | `doctor.md`, `cli.md` | `doctor.py`, `cli.py` | the setup check and the chat |
| 17 | `server.md`, `package.md` | `server.py`, `__init__/__main__` | the HTTP service; `python -m` |
| 18 | `evals.md`, `tests.md` | `evals/*`, `tests/*` | how quality is measured and proven |
| 19 | `build_docs.md`, `docker.md` | `tools/`, Docker files | how these docs and the images are built |

The other pages: `04-start-from-zero` (setup from nothing), `06-run-and-verify` (every way to run it, and checks),
`05-user-manual` (every command, flag and setting), `01-setup-and-models` (the optional offline model),
`02-roadmap` (what is built, what could come next), `03-build-log` (every decision and measurement, and why).

---
## 8. Explaining it to someone — questions you will get
- **"Is it just ChatGPT writing code?"** No. The model only *proposes* actions. The program checks and executes them,
  rejects broken code, and asks before running anything. The model also sees real results (errors, test output) and
  corrects itself — that loop is what makes it an *agent*.
- **"How does it know the code works?"** Three layers: every file is syntax/lint-checked when written; the tests run in
  a sandbox; and the model only finishes when those succeeded. It cannot prove the code does what you *meant* —
  review the diffs.
- **"Is it safe to let an AI run code?"** Code runs in a Docker container that sees only the project folder, has no
  internet and limited memory, and you approve each run unless you wrote a rule allowing it.
- **"What does it cost?"** Nothing: Gemini's free tier plus a local model as backup. The price is limited requests per
  day, which is why the design counts every call.
- **"How is it like Claude Code?"** Same way of working: start it in a folder, ask in plain words, it reads, plans
  (`/plan`), edits with diffs, runs tests, asks permission (`allow`/`deny` rules, "always"), keeps project notes
  (`AGENT.md` ~ `CLAUDE.md`), supports MCP tool servers, hooks, custom slash commands, `/undo`, `--resume`. The
  difference is the model: free Gemini lite models are weaker than Claude on big, subtle changes.
- **"How do you know it all works?"** 141 automated tests with a fake model (no internet needed), 15 real coding tasks
  scored automatically (15/15 on 5 Nov 2026), and docs that are machine-checked against the code.

## 9. Honest limits
- Free lite models handle everyday tasks well but are weaker than Claude on large, subtle changes; the strong Gemini
  models allow only ~20 requests a day on a free key; the local 8B model is slower and weaker still.
- Checks prove code *parses* and passes the tests you have — not that it does what you meant.
- Without Docker (`--sandbox local`) code runs on your computer with your rights (still only after your approval).
- On Gemini's free tier Google may use what you send to improve its products: do not send secret code.
