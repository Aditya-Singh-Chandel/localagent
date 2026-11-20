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
 17 | from . import __version__
 18 | from .backends import model_limits
 19 | from .config import Config
 20 | from .localserver import (SETUP_HINT, can_start, describe_local, find_llama_server, find_model_file,
 21 |                           running_local)
 22 | from .ratelimit import RateLimiter
 23 | from .runners import docker_available
 24 | from .sandbox import run_command
 25 | 
 26 | 
```
- `importlib.util` tells whether a package is installed; `urllib` performs simple HTTP GETs; `__version__` is shown
  first; `RateLimiter` reads today's quota use; `model_limits` gives per-model limits; the `localserver` functions find
  the local model; `docker_available` and `run_command` check Docker. `SETUP_DOC` is the guide named in local-model advice.

## Helpers
```python
 27 | def _get_json(url: str, headers: dict[str, str] | None = None) -> dict:
 28 |     """GET a URL and parse the JSON answer (5-second timeout keeps the check quick)."""
 29 |     request = urllib.request.Request(url, headers=headers or {})
 30 |     with urllib.request.urlopen(request, timeout=5) as response:
 31 |         return json.load(response)
 32 | 
 33 | 
```
- GET a URL (optionally with headers such as the API key) and parse the JSON reply. 5-second timeout: a dead server
  should not make the doctor hang.

```python
 34 | def _installed_models(host: str) -> list[str]:
 35 |     """Ask Ollama which models are pulled (GET /api/tags)."""
 36 |     return [entry["name"] for entry in _get_json(host + "/api/tags").get("models", [])]
 37 | 
 38 | 
```
- Ollama's `GET /api/tags` lists the models you have downloaded.

## The checks
```python
 39 | def _check_tools(lines: list[str]) -> None:
 40 |     for tool in ("ruff", "mypy"):
 41 |         if importlib.util.find_spec(tool):
 42 |             lines.append(f"ok    {tool} installed (verification layer active)")
 43 |         else:
 44 |             lines.append(f"warn  {tool} missing: pip install -e '.[dev]'")
 45 | 
 46 | 
```
- ruff and mypy are the second and third verification layers. Missing → a *warning* (the compile check still works).

```python
 47 | def _check_sandbox(config: Config, lines: list[str]) -> bool:
 48 |     if config.sandbox != "docker":
 49 |         lines.append("note  sandbox 'local': code runs where the agent runs (use --sandbox docker "
 50 |                      "for a separate locked-down container)")
 51 |         return True
 52 |     if not docker_available():
 53 |         lines.append("FAIL  Docker is not running: start Docker Desktop and retry")
 54 |         return False
 55 |     code, _ = run_command(["docker", "image", "inspect", config.sandbox_image],
 56 |                           Path.cwd(), 30, 200)
 57 |     if code != 0:
 58 |         lines.append(f"FAIL  sandbox image {config.sandbox_image} missing; build it with:\n"
 59 |                      "      docker build -t localagent-sandbox:latest -f docker/sandbox.Dockerfile docker")
 60 |         return False
 61 |     lines.append(f"ok    Docker running and image {config.sandbox_image} present")
 62 |     return True
 63 | 
 64 | 
```
- With the local sandbox there is nothing to check (just a note). With Docker: is the engine running, and has the sandbox
  image been built? Each failure prints the fix (start Docker Desktop / the exact `docker build` command).

```python
 65 | def _check_ollama(config: Config, lines: list[str]) -> bool:
 66 |     try:
 67 |         models = _installed_models(config.host)
 68 |     except (urllib.error.URLError, OSError, ValueError) as exc:
 69 |         lines.append(f"FAIL  Ollama not reachable at {config.host}: {exc}")
 70 |         lines.append("      start the Ollama app (or run `ollama serve`) and retry")
 71 |         return False
 72 |     lines.append(f"ok    Ollama reachable at {config.host}")
 73 |     wanted = config.model if ":" in config.model else config.model + ":latest"
 74 |     if wanted in models:
 75 |         lines.append(f"ok    model {config.model} is installed")
 76 |         return True
 77 |     lines.append(f"FAIL  model {config.model} is not installed; run: ollama pull {config.model}")
 78 |     lines.append(f"      installed models: {', '.join(models) or '(none)'}")
 79 |     return False
 80 | 
 81 | 
```
- Ollama reachable? Is the model downloaded? A name without `:tag` means `:latest` in Ollama, so that is added before
  comparing. Prints the exact `ollama pull` command and what *is* installed.

```python
 82 | def _check_openai(config: Config, lines: list[str]) -> bool:
 83 |     try:
 84 |         _get_json(config.api_base + "/models")
 85 |     except (urllib.error.URLError, OSError, ValueError) as exc:
 86 |         lines.append(f"FAIL  no OpenAI-compatible server at {config.api_base}: {exc}")
 87 |         lines.append("      start it first (for llama.cpp: .\\serve.ps1)")
 88 |         return False
 89 |     lines.append(f"ok    server reachable at {config.api_base}")
 90 |     return True
 91 | 
 92 | 
```
- Asks the OpenAI-style server for its model list (`GET /models`); any answer means it is running.

```python
 93 | def _check_local(config: Config, lines: list[str]) -> bool:
 94 |     """The local model: a server that answers, or llama.cpp plus a model file it can start."""
 95 |     if running_local(config):
 96 |         lines.append(f"ok    local model: {describe_local(config)}")
 97 |         return True
 98 |     program, model = find_llama_server(config), find_model_file(config)
 99 |     if program is not None and model is not None and can_start(config):
100 |         lines.append(f"ok    local model: {model.name} ({model.stat().st_size // 1_000_000} MB), "
101 |                      f"started with {program} when needed")
102 |         return True
103 |     if program is None:
104 |         lines.append(f"FAIL  llama.cpp's llama-server was not found ({SETUP_HINT})")
105 |     if model is None:
106 |         lines.append(f"FAIL  no .gguf model file was found ({SETUP_HINT})")
107 |     if program is not None and model is not None:
108 |         lines.append(f"FAIL  nothing answers at {config.api_base}, and a server can only be "
109 |                      "started for an address on this computer")
110 |     return False
111 | 
112 | 
```
- The check for offline work (backend `local`, and the backup of `auto` mode). A running local server is fine. So are
  llama.cpp's server program plus a model file, when a server may be started for `api_base` — the line shows the model
  and its size and which program will start it, e.g. `ok    local model: Qwen3-8B-Q4_K_M.gguf (5027 MB), started with
  ...\bin\llamacpp-vulkan\llama-server.exe when needed`. Otherwise one `FAIL` line per missing piece, each pointing to the
  setup guide.

```python
113 | def _check_gemini(config: Config, lines: list[str]) -> bool:
114 |     if not config.gemini_api_key:
115 |         lines.append("FAIL  GEMINI_API_KEY is not set (see docs/04-start-from-zero.md, step 6)")
116 |         return False
117 |     try:
118 |         listed = _get_json(config.gemini_base + "/models",
119 |                            {"Authorization": f"Bearer {config.gemini_api_key}"})
120 |     except urllib.error.HTTPError as exc:
121 |         lines.append(f"FAIL  Gemini rejected the key (HTTP {exc.code}); create a new key in AI Studio")
122 |         return False
123 |     except (urllib.error.URLError, OSError, ValueError) as exc:
124 |         lines.append(f"FAIL  cannot reach Gemini: {exc}")
125 |         return False
126 |     available = {str(m.get("id", "")).removeprefix("models/") for m in listed.get("data", [])}
127 |     lines.append("ok    Gemini key accepted")
128 |     healthy = True
129 |     for model in config.gemini_models + config.gemini_strong_models:
130 |         if model not in available:
131 |             lines.append(f"FAIL  model {model} is not offered to this key")
132 |             healthy = False
133 |         rpm, rpd = model_limits(config, model)
134 |         usage = RateLimiter(model, rpm, config.tpm, rpd, config.usage_file).status()
135 |         lines.append(f"      {usage}")
136 |     backups = {"openai": _check_openai, "ollama": _check_ollama, "local": _check_local}
137 |     if config.fallback_backend in backups:              # an optional backup: never a FAIL
138 |         found: list[str] = []
139 |         if backups[config.fallback_backend](config, found):
140 |             lines += found
141 |         else:
142 |             lines.append("note  the offline backup is not available (optional - Gemini works "
143 |                          "without it):")
144 |             lines += ["      " + line[6:] for line in found]
145 |     elif config.mode == "auto":
146 |         lines.append(f"note  no offline backup: without internet nothing answers ({SETUP_HINT})")
147 |     return healthy
148 | 
149 | 
```
- No key → FAIL with a pointer to the beginner guide.
- Lists the models offered to your key (this does not use generation quota). HTTP error → the key was rejected.
- Each model in the chain must be in that list; for each it prints today's usage from `usage.json` (with that model's
  own limits, see `model_limits`).
- If a local fallback is configured (`local`, `openai` or `ollama`), it is checked too — into a separate list `found`.
  Available → its `ok` lines are shown. Not available → one `note` line ("the offline backup is not available
  (optional - Gemini works without it)") with the details indented below it (`line[6:]` cuts the 6-character `FAIL  `
  prefix), and the result stays healthy. Found in the final verification of 0.5: the agent's Docker image always
  configures the local backup, so its doctor printed `FAIL` whenever `serve.ps1` was not running — although the agent
  works without it. In `auto` mode without any backup, a `note` says that nothing will answer without internet.

## `run_doctor`
```python
150 | def run_doctor(config: Config) -> tuple[bool, list[str]]:
151 |     """Return (everything essential works, human-readable report lines)."""
152 |     lines = [f"localagent {__version__}, python {sys.version.split()[0]}",
153 |              f"mode  {config.mode} (backend {config.backend})"]
154 |     _check_tools(lines)
155 |     sandbox_ok = _check_sandbox(config, lines)
156 |     checks = {"ollama": _check_ollama, "openai": _check_openai, "gemini": _check_gemini,
157 |               "local": _check_local}
158 |     backend_ok = checks[config.backend](config, lines) if config.backend in checks else True
159 |     return sandbox_ok and backend_ok, lines
```
- Version and Python, the mode and backend, tools, sandbox, then the backend check chosen by name. Healthy = sandbox OK
  and backend OK. When no model can be set up at all, the CLI still runs the doctor and puts the reason first.
