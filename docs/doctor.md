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
```python
  1 | """Health check behind ``--doctor``: what is working and what to fix.
  2 | 
  3 | Answers the questions that explain most "it does nothing" problems: are the
  4 | verification tools (ruff, mypy) installed, is the code sandbox (Docker) ready,
  5 | and can the chosen model service be reached with the chosen model or key?
  6 | Every failure line says how to fix it.
  7 | """
  8 | from __future__ import annotations
  9 | 
 10 | import importlib.util
 11 | import json
 12 | import sys
 13 | import urllib.error
 14 | import urllib.request
 15 | from pathlib import Path
 16 | 
 17 | from .backends import model_limits
 18 | from .config import Config
 19 | from .ratelimit import RateLimiter
 20 | from .runners import docker_available
 21 | from .sandbox import run_command
 22 | 
 23 | 
```
- `importlib.util` tells whether a package is installed; `urllib` performs simple HTTP GETs; `RateLimiter` reads today's
  quota use; `model_limits` gives per-model limits; `docker_available` and `run_command` check Docker.

## Helpers
```python
 24 | def _get_json(url: str, headers: dict[str, str] | None = None) -> dict:
 25 |     """GET a URL and parse the JSON answer (5-second timeout keeps the check quick)."""
 26 |     request = urllib.request.Request(url, headers=headers or {})
 27 |     with urllib.request.urlopen(request, timeout=5) as response:
 28 |         return json.load(response)
 29 | 
 30 | 
```
- GET a URL (optionally with headers such as the API key) and parse the JSON reply. 5-second timeout: a dead server
  should not make the doctor hang.

```python
 31 | def _installed_models(host: str) -> list[str]:
 32 |     """Ask Ollama which models are pulled (GET /api/tags)."""
 33 |     return [entry["name"] for entry in _get_json(host + "/api/tags").get("models", [])]
 34 | 
 35 | 
```
- Ollama's `GET /api/tags` lists the models you have downloaded.

## The checks
```python
 36 | def _check_tools(lines: list[str]) -> None:
 37 |     for tool in ("ruff", "mypy"):
 38 |         if importlib.util.find_spec(tool):
 39 |             lines.append(f"ok    {tool} installed (verification layer active)")
 40 |         else:
 41 |             lines.append(f"warn  {tool} missing: pip install -e '.[dev]'")
 42 | 
 43 | 
```
- ruff and mypy are the second and third verification layers. Missing → a *warning* (the compile check still works).

```python
 44 | def _check_sandbox(config: Config, lines: list[str]) -> bool:
 45 |     if config.sandbox != "docker":
 46 |         lines.append("note  sandbox 'local': code runs where the agent runs (use --sandbox docker "
 47 |                      "for a separate locked-down container)")
 48 |         return True
 49 |     if not docker_available():
 50 |         lines.append("FAIL  Docker is not running: start Docker Desktop and retry")
 51 |         return False
 52 |     code, _ = run_command(["docker", "image", "inspect", config.sandbox_image],
 53 |                           Path.cwd(), 30, 200)
 54 |     if code != 0:
 55 |         lines.append(f"FAIL  sandbox image {config.sandbox_image} missing; build it with:\n"
 56 |                      "      docker build -t localagent-sandbox:latest -f docker/sandbox.Dockerfile docker")
 57 |         return False
 58 |     lines.append(f"ok    Docker running and image {config.sandbox_image} present")
 59 |     return True
 60 | 
 61 | 
```
- With the local sandbox there is nothing to check (just a note). With Docker: is the engine running, and has the sandbox
  image been built? Each failure prints the fix (start Docker Desktop / the exact `docker build` command).

```python
 62 | def _check_ollama(config: Config, lines: list[str]) -> bool:
 63 |     try:
 64 |         models = _installed_models(config.host)
 65 |     except (urllib.error.URLError, OSError, ValueError) as exc:
 66 |         lines.append(f"FAIL  Ollama not reachable at {config.host}: {exc}")
 67 |         lines.append("      start the Ollama app (or run `ollama serve`) and retry")
 68 |         return False
 69 |     lines.append(f"ok    Ollama reachable at {config.host}")
 70 |     wanted = config.model if ":" in config.model else config.model + ":latest"
 71 |     if wanted in models:
 72 |         lines.append(f"ok    model {config.model} is installed")
 73 |         return True
 74 |     lines.append(f"FAIL  model {config.model} is not installed; run: ollama pull {config.model}")
 75 |     lines.append(f"      installed models: {', '.join(models) or '(none)'}")
 76 |     return False
 77 | 
 78 | 
```
- Ollama reachable? Is the model downloaded? A name without `:tag` means `:latest` in Ollama, so that is added before
  comparing. Prints the exact `ollama pull` command and what *is* installed.

```python
 79 | def _check_openai(config: Config, lines: list[str]) -> bool:
 80 |     try:
 81 |         _get_json(config.api_base + "/models")
 82 |     except (urllib.error.URLError, OSError, ValueError) as exc:
 83 |         lines.append(f"FAIL  no OpenAI-compatible server at {config.api_base}: {exc}")
 84 |         lines.append("      start it first (for llama.cpp: .\\serve.ps1)")
 85 |         return False
 86 |     lines.append(f"ok    server reachable at {config.api_base}")
 87 |     return True
 88 | 
 89 | 
```
- Asks the OpenAI-style server for its model list (`GET /models`); any answer means it is running.

```python
 90 | def _check_gemini(config: Config, lines: list[str]) -> bool:
 91 |     if not config.gemini_api_key:
 92 |         lines.append("FAIL  GEMINI_API_KEY is not set (see docs/04-start-from-zero.md, step 6)")
 93 |         return False
 94 |     try:
 95 |         listed = _get_json(config.gemini_base + "/models",
 96 |                            {"Authorization": f"Bearer {config.gemini_api_key}"})
 97 |     except urllib.error.HTTPError as exc:
 98 |         lines.append(f"FAIL  Gemini rejected the key (HTTP {exc.code}); create a new key in AI Studio")
 99 |         return False
100 |     except (urllib.error.URLError, OSError, ValueError) as exc:
101 |         lines.append(f"FAIL  cannot reach Gemini: {exc}")
102 |         return False
103 |     available = {str(m.get("id", "")).removeprefix("models/") for m in listed.get("data", [])}
104 |     lines.append("ok    Gemini key accepted")
105 |     healthy = True
106 |     for model in config.gemini_models + config.gemini_strong_models:
107 |         if model not in available:
108 |             lines.append(f"FAIL  model {model} is not offered to this key")
109 |             healthy = False
110 |         rpm, rpd = model_limits(config, model)
111 |         usage = RateLimiter(model, rpm, config.tpm, rpd, config.usage_file).status()
112 |         lines.append(f"      {usage}")
113 |     if config.fallback_backend in ("openai", "ollama"):    # an optional backup: never a FAIL
114 |         found: list[str] = []
115 |         check = _check_openai if config.fallback_backend == "openai" else _check_ollama
116 |         if check(config, found):
117 |             lines += found
118 |         else:
119 |             lines.append("note  the backup model is not reachable (optional - Gemini works without it):")
120 |             lines += ["      " + line[6:] for line in found]
121 |     return healthy
122 | 
123 | 
```
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
```python
124 | def run_doctor(config: Config) -> tuple[bool, list[str]]:
125 |     """Return (everything essential works, human-readable report lines)."""
126 |     lines = [f"python {sys.version.split()[0]}"]
127 |     _check_tools(lines)
128 |     sandbox_ok = _check_sandbox(config, lines)
129 |     checks = {"ollama": _check_ollama, "openai": _check_openai, "gemini": _check_gemini}
130 |     backend_ok = checks[config.backend](config, lines) if config.backend in checks else True
131 |     return sandbox_ok and backend_ok, lines
```
- Python version, tools, sandbox, then the backend check chosen by name. Healthy = sandbox OK and backend OK.
