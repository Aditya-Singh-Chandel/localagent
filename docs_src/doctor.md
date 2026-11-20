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
- `importlib.util` tells whether a package is installed; `urllib` performs simple HTTP GETs; `RateLimiter` reads today's
  quota use; `model_limits` gives per-model limits; `docker_available` and `run_command` check Docker.

## Helpers
@@ localagent/doctor.py _get_json @@
- GET a URL (optionally with headers such as the API key) and parse the JSON reply. 5-second timeout: a dead server
  should not make the doctor hang.

@@ localagent/doctor.py _installed_models @@
- Ollama's `GET /api/tags` lists the models you have downloaded.

## The checks
@@ localagent/doctor.py _check_tools @@
- ruff and mypy are the second and third verification layers. Missing → a *warning* (the compile check still works).

@@ localagent/doctor.py _check_sandbox @@
- With the local sandbox there is nothing to check (just a note). With Docker: is the engine running, and has the sandbox
  image been built? Each failure prints the fix (start Docker Desktop / the exact `docker build` command).

@@ localagent/doctor.py _check_ollama @@
- Ollama reachable? Is the model downloaded? A name without `:tag` means `:latest` in Ollama, so that is added before
  comparing. Prints the exact `ollama pull` command and what *is* installed.

@@ localagent/doctor.py _check_openai @@
- Asks the OpenAI-style server for its model list (`GET /models`); any answer means it is running.

@@ localagent/doctor.py _check_gemini @@
- No key → FAIL with a pointer to the beginner guide.
- Lists the models offered to your key (this does not use generation quota). HTTP error → the key was rejected.
- Each model in the chain must be in that list; for each it prints today's usage from `usage.json` (with that model's
  own limits, see `model_limits`).
- If a local fallback is configured, that server is checked too — into a separate list `found`. Reachable → its `ok`
  lines are shown. Not reachable → one `note` line ("the backup model is not reachable (optional - Gemini works
  without it)") with the details indented below it (`line[6:]` cuts the 6-character `FAIL  ` prefix), and the
  result stays healthy. Found in the final verification: the agent's Docker image always configures the local
  backup, so its doctor printed `FAIL` whenever `serve.ps1` was not running — although the agent works without it.

## `run_doctor`
@@ localagent/doctor.py run_doctor @@
- Python version, tools, sandbox, then the backend check chosen by name. Healthy = sandbox OK and backend OK.
