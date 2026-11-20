# Start from zero — a complete guide for someone who has never done this before

This page assumes **no knowledge at all**: not of Python, not of terminals, not of AI models, not of Docker. Follow
it top to bottom. Every command is shown exactly; copy it, paste it, press Enter. When something goes wrong, look in
the *Troubleshooting* table at the end.

**Time needed:** about 30–45 minutes for the first setup (most of it is downloads).
**Computer:** Windows 10/11, 8 GB RAM or more (16 GB if you also want the local model), ~10 GB free disk.
**Cost:** nothing. Everything here is free.

---
## Part 1 — What you are building, in plain words

You are setting up a **coding agent**: a program you can type requests to, such as *"create a script that renames all
my photos by date"*, and it writes the code, checks it, runs it, and fixes it — by itself.

It has three parts:

1. **The brain — an AI model.** It reads your request and decides what to do. We use **Google Gemini** (free, runs on
   Google's computers, fast) and, as a backup, an AI model running **on your own computer** (free, slower, works
   offline).
2. **The hands — tools.** The model cannot touch your computer directly. It can only *ask* for tools: "read this
   file", "write this file", "run this program". Our program performs the request and tells the model the result.
3. **The safety net — checks and a sandbox.** Before keeping any Python code, our program checks it for errors and
   **refuses broken code** (the model is told the exact errors and tries again). Code is run inside a **Docker
   container** — a sealed box that cannot reach your files or the internet.

```
 you type a request
        │
        ▼
 ┌──────────────┐  "please run tool X"   ┌──────────────┐   check, then do it   ┌─────────────────────┐
 │   AI model   │ ─────────────────────► │  the agent   │ ────────────────────► │ workspace folder +  │
 │ (Gemini/local)│ ◄───────────────────── │  (this code) │ ◄──────────────────── │ Docker sandbox box  │
 └──────────────┘   result of the tool    └──────────────┘        result         └─────────────────────┘
        │ repeats until the job is done
        ▼
 final answer for you
```

---
## Part 2 — Words you will meet (glossary)

| Word | Meaning |
|---|---|
| **Terminal / PowerShell** | A window where you type commands instead of clicking. On Windows: press the Windows key, type `PowerShell`, press Enter. |
| **Command** | A line of text you type into the terminal and run with Enter. |
| **Folder / path** | A location on disk, e.g. `C:\Users\you\Desktop\Agent`. |
| **Python** | The programming language this agent is written in (and writes code in). |
| **Virtual environment (venv)** | A private folder of Python add-ons for one project, so projects do not interfere. |
| **pip** | Python's installer for add-on packages. |
| **AI model / LLM** | The program that understands text and decides which tools to call. |
| **API key** | A secret password that lets a program use an online service (here: Gemini). Never share it or paste it into code. |
| **Environment variable** | A named setting stored by Windows that programs can read, e.g. `GEMINI_API_KEY`. |
| **Token** | A piece of text (~4 characters). Models read and write tokens; free plans limit how many you may use. |
| **Rate limit** | A rule like "at most 12 requests per minute" or "1500 per day". |
| **Docker / container / image** | Docker runs programs in sealed boxes (containers) made from templates (images). |
| **Workspace** | The one folder the agent is allowed to work in. |
| **Lint / type check** | Automatic error checks for code (we use the tools `ruff` and `mypy`). |

---
## Part 3 — Install the basics (once)

### Step 1. Open PowerShell
Press the **Windows key**, type `PowerShell`, press **Enter**. A blue/black window opens. All commands below are typed
here. Tip: right-click in the window pastes what you copied.

### Step 2. Install Python 3.12
```
winget install Python.Python.3.12
```
`winget` is Windows' built-in app installer. When it finishes, **close PowerShell and open a new one** (new programs
are only found by new windows). Check:
```
python --version
```
You should see `Python 3.12.x`. (If Windows opens the Microsoft Store instead, run the `winget` command again, then
open a new window.)

### Step 3. Get the project folder
Copy the whole `Agent` project folder onto your computer, for example to your Desktop. Then move the terminal into it
(`cd` means "change directory"):
```
cd $HOME\Desktop\Agent
```
Run `dir` — you should see folders such as `localagent`, `docs`, `tests`, and files such as `README.md`.

### Step 4. Create the virtual environment and install the agent
```
python -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[dev]"
```
- The first line creates the private Python folder `.venv`.
- The second installs the agent itself into it (`-e` = "editable": changes to the code apply at once) together with
  its `dev` extras, `ruff` and `mypy` (the error checkers), as listed in `pyproject.toml`.
From now on **always** start Python as `.\.venv\Scripts\python` so this private Python is used.

### Step 5. Prove the program works (no AI needed)
```
.\.venv\Scripts\python -m unittest discover -s tests -t .
```
After about 30–40 seconds it should end with `Ran 201 tests ... OK`. These are automatic tests that use a *pretend* AI
model, so they work without any setup. If you see `OK`, the program itself is healthy.

---
## Part 4 — Give the agent a brain

You need **at least one** of these. Option A is recommended (fast and smart). Option B is a backup that works offline.

### Step 6 (Option A, recommended). Gemini — free API key
1. Open <https://aistudio.google.com/apikey> in your browser and sign in with a Google account.
2. Click **Create API key**, then **copy** the key.
3. Store it in Windows (replace the text in quotes with your key; keep the quotes):
   ```
   setx GEMINI_API_KEY "paste-your-key-here"
   ```
4. **Close PowerShell and open a new one**, then `cd` into the project folder again (Step 3). `setx` only affects
   windows opened *after* it ran.

What the free key allows (Google can change these; check the "Rate limits" page in AI Studio): a few requests per
minute and a **daily limit per model** - measured on our key: only about **20 per day** for the strongest models
(`gemini-3.5-flash`, `gemini-2.5-flash`), much more for the lighter "flash-lite" models. So the agent does everyday
work with the flash-lite models and keeps the strong ones in reserve: it only switches to them when a task keeps
failing (you will see `[trouble detected: switching to a stronger model]`). A question takes 1-2 requests, a typical small task 3-8. The agent counts
for you and slows down or switches model automatically (see `ratelimit.md`, `resilient.md`).
**Privacy:** on the free plan Google may use what you send to improve its products. Do not send private or secret code.

### Step 7 (Option B, optional). A model on your own computer
This needs ~5 GB of downloads and 16 GB of RAM. It is the backup when Gemini is unavailable — no internet, or the daily
quota used up — and the only brain in offline mode (nothing then leaves your computer).
1. Download the model file **Qwen3-8B-Q4_K_M.gguf** from <https://huggingface.co/Qwen/Qwen3-8B-GGUF> (Files tab) into
   `%USERPROFILE%\.localagent\models\` (create the folder; inside the project, a `models` folder works too).
2. Download the **llama.cpp** Windows zip for your PC from <https://github.com/ggml-org/llama.cpp/releases>: the file
   ending `bin-win-vulkan-x64.zip` (uses your graphics card when it can) or `bin-win-cpu-x64.zip` (any PC). Unzip it into
   `%USERPROFILE%\.localagent\llama\` (inside the project, `bin\llamacpp-vulkan` or `bin\llamacpp` work too).
3. That is all: you do **not** start anything yourself. When the local model is needed, the agent starts it, waits
   until it has loaded (10-60 seconds), and stops it when you leave. Try it with
   `.\.venv\Scripts\python -m localagent --offline -p "say hello"` (close big programs first: it needs ~6 GB of free
   memory). Everything about online and offline: `01-setup-and-models.md`.

---
## Part 5 — The safety box (Docker)

### Step 8. Install and start Docker Desktop
```
winget install Docker.DockerDesktop
```
Restart the computer if asked, then open **Docker Desktop** from the Start menu and wait until it says it is running.
Check in PowerShell:
```
docker info
```
It should print information instead of an error.

### Step 9. Build the sandbox image (once, ~10 seconds after the first download)
```
docker build -t localagent-sandbox:latest -f docker/sandbox.Dockerfile docker
```

---
## Part 6 — Check everything, then use it

### Step 10. Run the doctor
```
.\.venv\Scripts\python -m localagent --doctor
```
It checks the setup the agent would choose by itself (first lines say what it chose: Gemini when the key is set, with
your local model as the offline backup when Step 7 is done; the Docker sandbox when it is ready). Every line should
start with `ok`, `note` or one of those choices. A line starting with `FAIL` tells you exactly what to fix.

### Step 11. Install the `localagent` command (once) — use it in ANY folder, like `claude`
From the project folder:
```
powershell -ExecutionPolicy Bypass -File .\install.ps1
```
It installs the agent into its `.venv`, writes a tiny launcher `%USERPROFILE%\.localagent\bin\localagent.cmd`, and adds
that folder to **your user PATH** (nothing else is changed; uninstall = delete that folder and remove it from PATH).
**Open a new PowerShell window** afterwards.

### Step 12. Start the agent in your project
```
cd C:\path\to\your\project
localagent
```
That is all. The agent works on the folder you started it in — exactly like Claude Code — and picks the best setup it
finds, telling you what it chose (banner of version 0.6, 20 Nov 2026):
```
localagent 0.6.0 | auto | gemini:gemini-3.5-flash-lite > gemini-3.1-flash-lite > gemini-3.5-flash > gemini-2.5-flash > local:Qwen3-8B-Q4_K_M | sandbox=docker | folder=...\demo-project
  mode auto: Gemini answers (GEMINI_API_KEY is set)
  offline backup: Qwen3-8B-Q4_K_M.gguf with llama.cpp (started when needed)
  sandbox: docker (Docker and the sandbox image are ready)
  history budget: 32768 tokens (Gemini handles long conversations)
```
(`localagent --offline` uses only the local model, `--online` only Gemini; `/model` switches later. Flags such as
`--backend`, `--sandbox` or `--no-auto` override the automatic choices. Without the installer:
`.\.venv\Scripts\python -m localagent --workspace C:\path\to\project` from the agent's folder.)

**First time in a project, type `/init`.** The agent looks around and writes `AGENT.md` — notes on how to run and test
the project that it reads at the start of every session (like Claude Code's `CLAUDE.md`). Edit it freely. Real run:
```
you> /init
-> outline {}
-> read_file {"end": 100, "start": 1, "path": "README.md"}
-> list_files {"path": "."}
-> write_file {"content": "# AGENT.md: Notes for AI Coding Agent\n\n### Purpose\nA tiny task list application ...
   OK: wrote AGENT.md (22 lines)
     @@ -0,0 +1,22 @@
     +# AGENT.md: Notes for AI Coding Agent
     ...
[3 model calls | 4625 prompt tok | 322 output tok | 48.7s]
[AGENT.md written]
```

**Then ask for work in plain words.** For anything bigger than a small change, use `/plan` first: the agent only
looks (it cannot change anything while planning), shows a numbered plan, and asks before carrying it out. Real run on
a small to-do app (shortened):
```
you> /plan Fix the failing test, then add a "done N" command to the CLI ... and a --json flag ... Add tests ...
-> read_file {"start": 1, "path": "todo_app/cli.py", "end": 100}
-> read_file {"end": 100, "path": "tests/test_store.py", "start": 1}
-> finish {"summary": "Plan for adding CLI features and tests: ...
Plan for adding CLI features and tests:
2. Modify `todo_app/cli.py`: support a `--json` flag ... parse `done N` ...
3. Add tests in `tests/test_cli.py` ...
4. Verification: run `python -m unittest discover` to ensure all tests pass.
Carry out this plan? [y/N] y
-> todo {"items": ["Implement CLI parsing for add, done N, and --json in todo_app/cli.py", ...
   TODO 0/3 done
     [ ] Implement CLI parsing for add, done N, and --json in todo_app/cli.py
     [ ] Add unit tests for CLI functionality in tests/test_cli.py
     [ ] Run all tests and verify success
-> write_file {"content": "\"\"\"Tiny command line: ...
   OK: wrote todo_app/cli.py (53 lines); verification passed
     @@ -8,8 +10,41 @@
     -    if argv and argv[0] == "add":
     +    json_output = False
     ...
-> write_file {"content": "import io\nimport json\n ...
   OK: wrote tests/test_cli.py (28 lines); verification passed
-> run_tests {}
   exit code 0
-> finish {"summary": "Fixed failing store test, implemented CLI support for `done N` and `--json` flag, added ...
[workspace check: all checked files OK]
```
How to read it:
- Lines starting with `->` are the tools the model asked for; the line below each is the result.
- The indented `@@ ... +/-` lines are a **diff**: what the edit changed (`-` removed, `+` added). It is shown only to
  you, so it costs no tokens.
- `TODO ...` is the model's checklist for multi-step work.
- The `[check of changed files ...]` line: every file changed during the request is checked once more.

**Approvals.** Before anything runs (a script, the tests, a shell command, a web page, an MCP tool) you are asked:
```
  allow: run python main.py? [y]es / [n]o / [a]lways:
```
`y` = once, Enter = no, `a` = yes and never ask again for exactly this (the rule is saved in
`.localagent\settings.json` of the project). You can also write rules yourself — see **Part 6b**.

Commands inside the chat:

| Type | Does |
|---|---|
| `/plan <request>` | look first, show a plan, carry it out only if you say yes |
| `/model`, `/online`, `/offline`, `/auto` | see who answers; switch between Gemini and your local model |
| `/init` | write or update `AGENT.md` (project notes the agent always reads) |
| `/compact` | replace the long conversation with a short summary (saves tokens; also happens automatically) |
| `/undo` | undo the file changes of your last request that changed something (your own later edits are kept) |
| `/clear` | forget the conversation (files stay) |
| `/resume` | list earlier conversations of this project; `/resume 2` continues one |
| `/stats` | model calls, tokens, time, and today's Gemini quota use per model |
| `/permissions`, `/doctor` | the rules in force; the setup check |
| `!git status` | run a command yourself (no AI call); the AI sees the output with your next request |
| `# use pytest` | save a note in `AGENT.md` |
| `@file.py` in a request | attach that file |
| `/help`, `/quit` | help; exit |
| **Ctrl+C** | stop the current task (the conversation is kept); at the prompt it exits |
| line ending in `\` or a `"""` block | type or paste a multi-line request |

Useful flags when starting: `--online` / `--offline` (only Gemini / only your local model), `-p "request"` (answer
once and exit), `-c` (continue the last conversation), `--auto-approve` (never ask — only sensible with the Docker
sandbox), `--network` (allow internet inside the sandbox, e.g. for `pip install`), `--allow-shell` (let the model run
commands such as `git status` or `npm test` **on your computer**, asking each time), `--type-check`, `--doctor` (check
the setup), `--version`, `--no-auto` (no automatic choices).

### Part 6b — Permission rules and extra tools (optional)
Create `.localagent\settings.json` in a project (or `%USERPROFILE%\.localagent\settings.json` for all projects):
```json
{
  "permissions": {
    "allow": ["run_tests", "run_python(*)", "run_shell(git status*)", "fetch_url(https://docs.python.org/*)"],
    "deny":  ["run_shell(*rm *)", "run_shell(*--force*)"]
  },
  "mcpServers": {
    "files": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "."]}
  }
}
```
- `allow`: these run without asking. `deny`: these are refused without asking (deny always wins). Everything else is
  asked. `*` matches any text. Details: `settings.md`.
- `mcpServers`: extra tools from MCP servers (the standard Claude Code uses). Each server's tools appear as
  `mcp__<name>__<tool>`; the banner says how many tools each server added. Details: `mcp.md`.
- The model can also read web pages (`fetch_url`, e.g. library documentation) — you approve each website.

### Step 13 (optional). Measure how good it is
```
.\.venv\Scripts\python -m evals.run_evals --backend gemini --sandbox docker
```
Runs 15 tasks (Python fixes and features, a multi-file package, JavaScript, Go, TypeScript, and two plain questions)
in throw-away folders and prints PASS/FAIL, model calls, tokens and time for each. Uses roughly 50 Gemini requests
(the last full run: 15/15 passed, 48 requests).

### Step 14 (optional). Run it in Docker, or as a service
- Whole agent in a container: `docker build -t localagent:latest .` then see `docker.md`.
- HTTP service for other programs: `docker compose up -d --build` (needs `LOCALAGENT_TOKEN`), see `docker.md` and
  `server.md`.

---
## Part 7 — How it works inside
The whole workflow — one request followed through every function, the safety rules, how calls are saved, and how
to explain it to others — is in **`00-overview.md`**. Read it next.

---
## Part 8 — Rebuilding the project from scratch, file by file

Every page in `docs/` shows the **complete source code** of its file, block by block, with each block explained. The
audit tool guarantees no line is missing. To rebuild the whole project by hand, create the folders `localagent`,
`tests`, `evals`, `tools`, `docker`, then create the files in this order (each only needs the ones before it), copying
the code blocks from each page in order:

1. `config.md` → `localagent/config.py` (settings)
2. `llm.md` → `localagent/llm.py` (talking to model servers)
3. `openai_compat.md` → `localagent/openai_compat.py`
4. `ratelimit.md` → `localagent/ratelimit.py`
5. `resilient.md` → `localagent/resilient.py`
6. `sandbox.md` → `localagent/sandbox.py`
7. `localserver.md` → `localagent/localserver.py` (the offline model)
8. `backends.md` → `localagent/backends.py`
9. `meter.md` → `localagent/meter.py`
10. `verify.md` → `localagent/verify.py`; `runners.md` → `localagent/runners.py`
11. `settings.md` → `localagent/settings.py` (permission rules)
12. `web.md` → `localagent/web.py` (reading web pages)
13. `mcp.md` → `localagent/mcp.py` (MCP servers)
14. `tools.md` → `localagent/tools.py`
15. `context.md` → `localagent/context.py`
16. `session.md` → `localagent/session.py`
17. `agent.md` → `localagent/agent.py`
18. `checkpoint.md` → `localagent/checkpoint.py`
19. `doctor.md` → `localagent/doctor.py`
20. `cli.md` → `localagent/cli.py`
21. `server.md` → `localagent/server.py`
22. `package.md` → `localagent/__init__.py`, `localagent/__main__.py`
23. `evals.md` → `evals/*`; `tests.md` → `tests/*`; `build_docs.md` → `tools/build_docs.py`;
    `check_commit.md` → `tools/check_commit.py` (and `.githooks/pre-commit`, shown in the project folder)
24. `docker.md` → `docker/sandbox.Dockerfile`, `Dockerfile`, `.dockerignore`, `docker-compose.yml`
25. `install.ps1` is in the project folder (36 lines, with a comment block explaining each step).
26. Also create `pyproject.toml` (the package description: name, version read from `localagent/__init__.py`, `dev`
    extras, the `localagent` command), `.gitignore` and `.gitattributes` (in the project folder, each line commented),
    and `serve.ps1` (shown in `01-setup-and-models.md`/the project folder) if you want to start the local model by hand.

Then run Step 5 (tests). If they pass, your rebuild is identical in behaviour.

---
## Part 9 — Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `python` opens the Microsoft Store | Python not installed / old window | Step 2, then open a **new** PowerShell |
| `setup problem: GEMINI_API_KEY is not set` | key missing, or window older than `setx` | Step 6, then open a **new** PowerShell |
| `FAIL Gemini rejected the key (HTTP 400/401/403)` | key copied wrong or deleted | create a new key, repeat Step 6 |
| `[gemini-... busy (503); retrying in 2s]` | Google is overloaded | nothing — the agent retries / switches model by itself |
| `[gemini-... busy (429); retrying in 18s]` | per-minute limit | nothing — it waits as long as Google asks |
| `every model failed: ... daily quota used up` | every model's daily quota is used (strong models: ~20/day) | wait until midnight US Pacific time, or set up the local model (Step 7): auto mode then uses it |
| `setup problem: No model is available yet` | no Gemini key and no local model | Step 6 or Step 7 |
| `setup problem: LOCALAGENT_... must be ...` | a typo in that environment variable | fix it with `setx`, or delete it in "Edit environment variables for your account" |
| `[Gemini is unreachable (no internet?): the local model answers for now ...]` | no internet | nothing — it switches back by itself; `/model offline` to stay offline |
| `the local model server stopped while starting` + log lines | usually too little free memory | close big programs (browsers), try again; the log is `%USERPROFILE%\.localagent\llama-server.log` |
| `No local model is available` | Step 7 not done (or files in other folders) | Step 7, or set `LOCALAGENT_MODEL_FILE` / `LOCALAGENT_LLAMA_SERVER` |
| `FAIL Docker is not running` | Docker Desktop closed | open Docker Desktop, wait until it is running |
| `sandbox image ... missing` | Step 9 not done | run the `docker build` command from Step 9 |
| `REJECTED (1/3) ...` in the output | the model wrote broken code | normal: it reads the errors and fixes them |
| `STOP: attempts exhausted` | the model failed 3 times on one file | rephrase the request more precisely, or split it |
| `Stopped after 20 model calls` | task too big for one request | ask for smaller steps |
| `exit code 124 ... timed out` | the program ran longer than 30 s | ask for a faster approach, or raise `command_timeout` in `config.py` |
| `no OpenAI-compatible server at http://127.0.0.1:8080/v1` | you chose `--backend openai` but no server runs | leave out `--backend` (the agent starts the model itself), or run `.\serve.ps1` in a separate window |
| `localagent` is not recognised | installer not run, or old window | Step 11, then open a **new** PowerShell |
| `[...: not available (HTTP 404); skipping it]` | Google retired that model | nothing — the chain moves on; remove it from `LOCALAGENT_GEMINI_MODELS` if you set that |
| Very slow answers, `busy (network)` lines | Google is overloaded right now | nothing — it retries; add the local fallback for bad days |
| Leftover containers named `localagent-...` | agent was killed abruptly | `docker rm -f <name>` (list with `docker ps`) |
| `setup problem: ...settings.json: not valid JSON` | a typo in a settings file | fix the file (a missing comma or quote is typical) |
| `MCP server '...': ... skipped` in the banner | the server program could not start | check its `command` (e.g. Node.js installed for `npx`); the agent works without it |
| `DENIED by a deny rule in the settings` | one of your deny rules matched | intended; change the rule if it is too broad |
| `[AGENT.md was NOT written - try /init again]` | the model finished without writing it | run `/init` again |
| `unknown command /xyz` | a typo, or a custom command file is missing | `/help` lists all commands, yours included |
| A pasted multi-line request became several requests | each pasted line was sent separately | wrap it in `"""` lines, or end lines with `\` |
| `.\.venv\Scripts\python` not found | Step 4 skipped or wrong folder | `cd` into the project folder; do Step 4 |

---
## Part 10 — What to read next
`00-overview.md` (how it all works) → `06-run-and-verify.md` (every way to run it) → `05-user-manual.md` (everything the agent can do, as one reference) → `03-build-log.md` (why every decision was made, with measurements)
→ `02-roadmap.md` (what is done, what is next) → any file page you are curious about.
