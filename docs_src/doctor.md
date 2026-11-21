# `localagent/doctor.py` — `--doctor`, the setup checker

**Role in the flow:** run `python -m localagent --doctor` (with the same flags you will use, e.g.
`--backend gemini --sandbox docker`) first, and again whenever something misbehaves. It checks each part and prints one
line per check; every problem line starts with `FAIL` and says how to fix it. Exit code 0 means everything essential works.

Example (this PC, healthy):
```
python 3.14.0b1
ok    ruff installed (verification layer active)
ok    mypy installed (verification layer active)
ok    Docker running and image localagent-sandbox:latest present
ok    Gemini key accepted
      gemini-3.5-flash: 22/1500 requests, 34288 tokens today
ok    server reachable at http://127.0.0.1:8080/v1
```

## Module header
@@ localagent/doctor.py header @@
- `importlib.util` tells whether a package is installed; `urllib` performs simple HTTP GETs and `urlsplit` takes an
  address apart; `__version__` is shown first; `online_problem` says what the online model lacks; the provider table
  helpers send a service's key the way it expects; `RateLimiter` reads today's quota use; `model_limits` gives
  per-model limits; the `localserver` functions (and `THIS_COMPUTER`, the addresses that mean "this computer") find
  the local model; `docker_available` and `run_command` check Docker. `SETUP_DOC` is the guide named in local-model advice.

## Helpers
@@ localagent/doctor.py _get_json @@
- GET a URL (optionally with headers such as the API key) and parse the JSON reply. 5-second timeout: a dead server
  should not make the doctor hang.

@@ localagent/doctor.py _installed_models @@
- Ollama's `GET /api/tags` lists the models you have downloaded.

## The checks
@@ localagent/doctor.py _check_tools @@
- ruff is the second verification layer of every Python write: missing → a *warning* (the compile check still works).
  mypy, the third, runs only with `--type-check`: missing → just a *note*. The Windows download leaves mypy out to stay
  half the size, so its doctor would otherwise warn about something nobody asked for.

@@ localagent/doctor.py _check_sandbox @@
- With the local sandbox there is nothing to check (just a note). With Docker: is the engine running, and has the sandbox
  image been built? Each failure prints the fix (start Docker Desktop / the exact `docker build` command).

@@ localagent/doctor.py _check_ollama @@
- Ollama reachable? Is the model downloaded? A name without `:tag` means `:latest` in Ollama, so that is added before
  comparing. Prints the exact `ollama pull` command and what *is* installed.

@@ localagent/doctor.py _check_openai @@
- Asks the OpenAI-style server for its model list (`GET /models`); any answer means it is running.

@@ localagent/doctor.py _check_local @@
- The check for offline work (backend `local`, and the backup of `auto` mode). A running local server is fine. So are
  llama.cpp's server program plus a model file, when a server may be started for `api_base` — the line shows the model
  and its size and which program will start it, e.g. `ok    local model: Qwen3-8B-Q4_K_M.gguf (5027 MB), started with
  ...\bin\llamacpp-vulkan\llama-server.exe when needed`. Otherwise one `FAIL` line per missing piece, each pointing to the
  setup guide.
- A server chosen in `localagent setup` is judged as itself: Ollama chosen but not answering → one `FAIL` that says to
  start Ollama (or choose again); a server at another computer's address that does not answer → one `FAIL` naming it
  — not advice about llama.cpp, which could not be started for that address anyway.

@@ localagent/doctor.py _check_gemini @@
- No key → FAIL with a pointer to the beginner guide.
- Lists the models offered to your key (this does not use generation quota). HTTP error → the key was rejected.
- Each model in the chain must be in that list; for each it prints today's usage from `usage.json` (with that model's
  own limits, see `model_limits`).
- Then the offline backup (`_check_backup`).

@@ localagent/doctor.py _check_hosted @@
- Another online service: first anything `online_problem` reports (no key, no model, no address) is one `FAIL` with
  the fix. Then the service's model list is asked with your key, sent the way the service expects (`key_headers`): an
  HTTP error means the key was refused (the error is closed, freeing the connection); no answer means the service
  cannot be reached. The chosen model must be in the list — compared without Gemini's `models/` prefix, because the
  live test pointed a `custom` service at Gemini's address and the check wrongly said "not offered". A service that
  lists no models at all is trusted. Then the offline backup.
- The service is called by its title, or "the service" for `custom` (its title, "another OpenAI-compatible
  service", reads badly in a sentence).

@@ localagent/doctor.py _check_backup @@
- The local fallback of `auto` mode (`local`, `openai` or `ollama`) is checked into a separate list `found`; `local`
  and `openai` both use `_check_local`, which also accepts a model that *can be started*. Available → its `ok` lines
  are shown. Not available → one `note` line ("the offline backup is not available (optional - the online model works
  without it)") with the details indented below it (`line[6:]` cuts the 6-character `FAIL  ` prefix), and the result
  stays healthy. Found in the final verification of 0.5: the agent's Docker image always configures the local backup,
  so its doctor printed `FAIL` whenever `scripts\serve.ps1` was not running — although the agent works without it. In `auto`
  mode without any backup, a `note` says that nothing will answer without internet.

## `run_doctor`
@@ localagent/doctor.py run_doctor @@
- Version and Python, the mode and backend, tools, sandbox, then the backend check chosen by name (`hosted` is any
  online service other than Gemini). Healthy = sandbox OK
  and backend OK. When no model can be set up at all, the CLI still runs the doctor and puts the reason first.
