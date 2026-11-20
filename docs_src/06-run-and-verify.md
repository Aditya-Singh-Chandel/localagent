# How to run it, and how to check that it works

The short, practical page. Every command here was run on 5 Nov 2026 and worked. If you are setting up a new computer,
do `04-start-from-zero.md` first; this page assumes Python, the `.venv`, Docker and your Gemini key are in place.

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
Then **open a new PowerShell window**. (Skipped it? Use the long form in section 3.)

## 3. Use it on a project (the normal way)
```
cd C:\path\to\your\project
localagent
```
Without the installer, from the agent's folder:
```
.\.venv\Scripts\python -m localagent --workspace C:\path\to\your\project
```
The first lines say what it chose, for example `gemini:... | sandbox=docker`. Then type requests:
```
you> /init                                    first time in a project: writes AGENT.md notes
you> What does report.js do?                  a question: it looks, then answers (1-2 calls)
you> Add a --json flag to the CLI with tests  a task: it edits, checks, runs the tests
you> /plan Split app.py into modules          bigger work: plan first, you approve
you> /undo                                    take the last request's changes back
you> /stats                                   calls, tokens, quota used today
you> /quit
```
Next day, continue the same conversation with `localagent --resume` (each project keeps its own history in
`<project>\.localagent\sessions`).

Useful flags: `--auto-approve` (never ask before running code; sensible only with the Docker sandbox), `--network`
(internet inside the sandbox, for `pip install`), `--allow-shell` (commands on your computer, asking each time).

## 4. One-off requests from a script
Put requests in a text file, one per line, ending with `/quit`, and feed it in:
```
cmd /c "localagent --auto-approve < requests.txt"
```
Every answer and every step is printed, as in the chat.

## 5. Run the whole agent inside Docker
```
docker build -t localagent:latest .
docker run -it --rm -e GEMINI_API_KEY -v "${PWD}:/app/workspace" -v "$HOME\.localagent:/home/agent/.localagent" localagent:latest
```
Run the second line from inside your project folder. Its doctor says `note the backup model is not reachable` unless
`serve.ps1` runs; that is fine.

## 6. Run it as an HTTP service (for other programs)
Start (keep this window open; Ctrl+C stops it and cleans up its containers):
```
.\.venv\Scripts\python -m localagent.server --port 8765 --token my-long-secret --backend gemini --sandbox docker --auto-approve
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

## 7. The optional offline backup model
In its own window: `.\serve.ps1`. It needs about 6 GB of free memory. While it runs, `localagent` adds it as the last
fallback by itself; `localagent --backend openai` uses only it (slower, works offline).

---
## 8. Check that everything works (cheapest first)
| # | Command (from the agent's folder) | Costs | Good result |
|---|---|---|---|
| 1 | `.\.venv\Scripts\python -m localagent --doctor` | nothing | lines start with `ok` or `note`, no `FAIL` |
| 2 | `.\.venv\Scripts\python -m unittest discover -s tests -t .` | nothing, ~30 s | `Ran 141 tests ... OK` |
| 3 | `.\.venv\Scripts\python tools/build_docs.py` | nothing | `docs OK: ... every code line documented exactly once` |
| 4 | `localagent` in an empty folder, ask it to build something small with tests | ~5-12 Gemini calls | it finishes and the tests it ran pass |
| 5 | `.\.venv\Scripts\python -m evals.run_evals --backend gemini --sandbox docker` | ~50-65 calls, ~7 min | `15/15 tasks passed` |

Results on 5-6 Nov 2026: doctor clean, 141 tests OK, docs OK, a fresh-folder build (expense tracker + Node.js report,
12 calls) worked and was re-checked by hand, `--resume`, the Docker image and the HTTP server worked, evals 15/15.

## 9. If something does not work
| You see | Do |
|---|---|
| `failed to connect to the docker API` / `Docker is not running` | open Docker Desktop, wait, try again |
| `GEMINI_API_KEY is not set` | `setx GEMINI_API_KEY "your-key"`, then open a **new** window |
| `localagent` is not recognised | section 2, then a new window, or use the long form in section 3 |
| `daily quota used up` | quotas reset at midnight US Pacific (13:30 India time); the lite models carry most work |
| `busy (503)` / `retrying` lines | nothing: it waits and retries by itself |
| leftover `localagent-...` containers in `docker ps` | `docker rm -f <name>` |
The full table is in `04-start-from-zero.md`, Part 9.
