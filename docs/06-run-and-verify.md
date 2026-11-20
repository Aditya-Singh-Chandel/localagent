# How to run it, and how to check that it works

The short, practical page. If you are setting up a new computer, do `04-start-from-zero.md` first; this page assumes
Python, the `.venv`, Docker and your Gemini key are in place. (Commands checked on 20 Nov 2026 with version 0.6.)

---
## 1. Before you start (every time you sit down)
1. **Open Docker Desktop** (Start menu) and wait until it says it is running. It does not always start by itself
   after a restart. Without it the agent still works, but code runs on your computer instead of in the sealed box.
2. Open **PowerShell**.

## 2. Install the `localagent` command (once)
From the agent's folder:
```
powershell -ExecutionPolicy Bypass -File .\install.ps1
```
Then **open a new PowerShell window**. (Skipped it? Use the long form in section 3.) `localagent --version` should
print `localagent 0.6.0`.

## 3. Use it on a project (the normal way)
```
cd C:\path\to\your\project
localagent                  (or: localagent --offline / localagent --online)
```
Without the installer, from the agent's folder:
```
.\.venv\Scripts\python -m localagent --workspace C:\path\to\your\project
```
The first lines say what it chose, for example `auto | gemini:... > local:Qwen3-8B-Q4_K_M | sandbox=docker`. Then:
```
you> /init                                    first time in a project: writes AGENT.md notes
you> What does @report.js do?                 a question; @ attaches the file (saves a call)
you> Add a --json flag to the CLI with tests  a task: it edits, checks, runs the tests
you> /plan Split app.py into modules          bigger work: plan first, you approve
you> !git status                              run a command yourself (no model call)
you> # always use type hints                  a note the agent keeps in AGENT.md
you> /undo                                    take the last change back
you> /model offline                           switch to the local model (conversation kept)
you> /stats                                   calls, tokens, quota used today
you> /quit
```
Next day, continue with `localagent -c` (the latest conversation) or pick one with `/resume`. Each project keeps its
own history in `<project>\.localagent\sessions`.

Useful flags: `--auto-approve` (never ask before running code; sensible only with the Docker sandbox), `--network`
(internet inside the sandbox, for `pip install`), `--allow-shell` (the model may run commands on your computer,
asking each time).

## 4. One request from a script
```
localagent -p "List the TODO comments in this project with file:line"
localagent -p "Summarise @CHANGELOG.md in five lines" > summary.txt
type question.txt | localagent -p -
```
Only the answer goes to the output (progress goes to the error stream), so it can be saved or piped. Anything that
would need a question is refused — allow it with a rule (`settings.md`) or add `--auto-approve`. Exit code 0 means
answered and checked.

## 5. Run the whole agent inside Docker
```
docker build -t localagent:latest .
docker run -it --rm -e GEMINI_API_KEY -v "${PWD}:/app/workspace" -v "$HOME\.localagent:/home/agent/.localagent" localagent:latest
```
Run the second line from inside your project folder. Its doctor says `note the offline backup is not available` unless
a model server runs on your computer (`serve.ps1`); that is fine.

## 6. Run it as an HTTP service (for other programs)
Start (keep this window open; Ctrl+C stops it and cleans up its containers):
```
.\.venv\Scripts\python -m localagent.server --port 8765 --token my-long-secret --online --sandbox docker --auto-approve
```
Use it from a second PowerShell window:
```
$auth = @{ Authorization = "Bearer my-long-secret" }
$s = Invoke-RestMethod -Method Post http://127.0.0.1:8765/sessions -Headers $auth
$body = @{ text = "Create hello.py that prints Hello, then run it." } | ConvertTo-Json
$r = Invoke-RestMethod -Method Post "http://127.0.0.1:8765/sessions/$($s.id)/messages" -Headers $auth -ContentType "application/json" -Body $body
$r.answer; $r.events; $r.calls
Invoke-RestMethod "http://127.0.0.1:8765/sessions/$($s.id)/files" -Headers $auth
```
The session's files are in `sessions\<id>\`. Details: `server.md`; as a Docker service: `docker.md`.

## 7. Online and offline
`localagent --offline` (or `/model offline` in the chat) answers with the local model, starting it when needed; it
needs about 6 GB of free memory. In the default auto mode it takes over by itself when the internet goes away.
Everything about it: `01-setup-and-models.md`.

## 8. Publishing the project safely (git and GitHub)
The repository is set up so that only source, tests, docs and scripts are ever committed:
- `.gitignore` keeps out the model (`models/`, `*.gguf`), the llama.cpp programs (`bin/`), conversation logs
  (`.localagent/`), virtual environments, caches, build output, `.env` files and keys.
- `.gitattributes` keeps line endings consistent between Windows and Linux.
- **The commit guard** (`tools/check_commit.py`, see `check_commit.md`) refuses a commit containing a file over 20 MB,
  a model/key/`.env` file, anything that looks like an API key or token, or an author e-mail that would be public.
  Turn it on once in each copy of the repository:
  ```
  git config core.hooksPath .githooks
  ```
- Commits use GitHub's private address, so your e-mail never appears. Set it once per copy:
  `git config user.email "<id>+<username>@users.noreply.github.com"` (your address is on
  https://github.com/settings/emails; tick "Keep my email addresses private" and "Block command line pushes that expose
  my email" there too).
- Then: `git add -A`, `git commit -m "..."`, `git push`.

---
## 9. Check that everything works (cheapest first)
| # | Command (from the agent's folder) | Costs | Good result |
|---|---|---|---|
| 1 | `.\.venv\Scripts\python -m localagent --doctor` | nothing | lines start with `ok` or `note`, no `FAIL` |
| 2 | `.\.venv\Scripts\python -m unittest discover -s tests -t .` | nothing, ~40 s | `Ran 200 tests ... OK` |
| 3 | `.\.venv\Scripts\python tools/build_docs.py` | nothing | `docs OK: 35 pages, every code line documented exactly once` |
| 4 | `localagent -p "In one sentence: what does @calc.py compute?"` in a folder with a `calc.py` | 1 Gemini call | a one-sentence answer, `[1 model calls ...]` |
| 5 | `localagent` in an empty folder, ask it to build something small with tests | ~5-12 Gemini calls | it finishes and the tests it ran pass |
| 6 | `.\.venv\Scripts\python -m evals.run_evals --sandbox docker` | ~50-65 calls, ~7 min | `15/15 tasks passed` |

Results on 20 Nov 2026 (version 0.6): doctor clean (local model found), 200 tests OK, docs OK, check 4 answered
correctly in 1 call (5.6 s).

## 10. If something does not work
| You see | Do |
|---|---|
| `failed to connect to the docker API` / `Docker is not running` | open Docker Desktop, wait, try again |
| `GEMINI_API_KEY is not set` / `No model is available yet` | `setx GEMINI_API_KEY "your-key"`, then open a **new** window; or set up offline (`01-setup-and-models.md`) |
| `setup problem: LOCALAGENT_... must be ...` | a typo in that variable: fix it with `setx`, or remove it in "Edit environment variables" |
| `localagent` is not recognised | section 2, then a new window, or use the long form in section 3 |
| `Gemini is unreachable (no internet?)` | nothing: the local model answers; Gemini is tried again every minute |
| `the local model server stopped while starting` | read the lines below it (from `%USERPROFILE%\.localagent\llama-server.log`); usually too little free memory - close programs |
| `daily quota used up` | quotas reset at midnight US Pacific (13:30 India time); the lite models, then the local model, carry the work |
| `busy (503)` / `retrying` / `waiting ...s for the per-minute limit` | nothing: it waits and retries by itself |
| `COMMIT BLOCKED: ...` | the message says what and how to fix it (section 8) |
| leftover `localagent-...` containers in `docker ps` | `docker rm -f <name>` |
The full table is in `04-start-from-zero.md`, Part 9.
