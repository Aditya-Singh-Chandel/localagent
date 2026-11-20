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
 20 | from .localserver import can_start, describe_local, find_llama_server, find_model_file, running_local
 21 | from .ratelimit import RateLimiter
 22 | from .runners import docker_available
 23 | from .sandbox import run_command
 24 | 
 25 | SETUP_DOC = "docs/01-setup-and-models.md"
 26 | 
 27 | 
```
- `importlib.util` tells whether a package is installed; `urllib` performs simple HTTP GETs; `__version__` is shown
  first; `RateLimiter` reads today's quota use; `model_limits` gives per-model limits; the `localserver` functions find
  the local model; `docker_available` and `run_command` check Docker. `SETUP_DOC` is the guide named in local-model advice.

## Helpers
```python
 28 | def _get_json(url: str, headers: dict[str, str] | None = None) -> dict:
 29 |     """GET a URL and parse the JSON answer (5-second timeout keeps the check quick)."""
 30 |     request = urllib.request.Request(url, headers=headers or {})
 31 |     with urllib.request.urlopen(request, timeout=5) as response:
 32 |         return json.load(response)
 33 | 
 34 | 
```
- GET a URL (optionally with headers such as the API key) and parse the JSON reply. 5-second timeout: a dead server
  should not make the doctor hang.

```python
 35 | def _installed_models(host: str) -> list[str]:
 36 |     """Ask Ollama which models are pulled (GET /api/tags)."""
 37 |     return [entry["name"] for entry in _get_json(host + "/api/tags").get("models", [])]
 38 | 
 39 | 
```
- Ollama's `GET /api/tags` lists the models you have downloaded.

## The checks
```python
 40 | def _check_tools(lines: list[str]) -> None:
 41 |     for tool in ("ruff", "mypy"):
 42 |         if importlib.util.find_spec(tool):
 43 |             lines.append(f"ok    {tool} installed (verification layer active)")
 44 |         else:
 45 |             lines.append(f"warn  {tool} missing: pip install -e '.[dev]'")
 46 | 
 47 | 
```
- ruff and mypy are the second and third verification layers. Missing → a *warning* (the compile check still works).

```python
 48 | def _check_sandbox(config: Config, lines: list[str]) -> bool:
 49 |     if config.sandbox != "docker":
 50 |         lines.append("note  sandbox 'local': code runs where the agent runs (use --sandbox docker "
 51 |                      "for a separate locked-down container)")
 52 |         return True
 53 |     if not docker_available():
 54 |         lines.append("FAIL  Docker is not running: start Docker Desktop and retry")
 55 |         return False
 56 |     code, _ = run_command(["docker", "image", "inspect", config.sandbox_image],
 57 |                           Path.cwd(), 30, 200)
 58 |     if code != 0:
 59 |         lines.append(f"FAIL  sandbox image {config.sandbox_image} missing; build it with:\n"
 60 |                      "      docker build -t localagent-sandbox:latest -f docker/sandbox.Dockerfile docker")
 61 |         return False
 62 |     lines.append(f"ok    Docker running and image {config.sandbox_image} present")
 63 |     return True
 64 | 
 65 | 
```
- With the local sandbox there is nothing to check (just a note). With Docker: is the engine running, and has the sandbox
  image been built? Each failure prints the fix (start Docker Desktop / the exact `docker build` command).

```python
 66 | def _check_ollama(config: Config, lines: list[str]) -> bool:
 67 |     try:
 68 |         models = _installed_models(config.host)
 69 |     except (urllib.error.URLError, OSError, ValueError) as exc:
 70 |         lines.append(f"FAIL  Ollama not reachable at {config.host}: {exc}")
 71 |         lines.append("      start the Ollama app (or run `ollama serve`) and retry")
 72 |         return False
 73 |     lines.append(f"ok    Ollama reachable at {config.host}")
 74 |     wanted = config.model if ":" in config.model else config.model + ":latest"
 75 |     if wanted in models:
 76 |         lines.append(f"ok    model {config.model} is installed")
 77 |         return True
 78 |     lines.append(f"FAIL  model {config.model} is not installed; run: ollama pull {config.model}")
 79 |     lines.append(f"      installed models: {', '.join(models) or '(none)'}")
 80 |     return False
 81 | 
 82 | 
```
- Ollama reachable? Is the model downloaded? A name without `:tag` means `:latest` in Ollama, so that is added before
  comparing. Prints the exact `ollama pull` command and what *is* installed.

```python
 83 | def _check_openai(config: Config, lines: list[str]) -> bool:
 84 |     try:
 85 |         _get_json(config.api_base + "/models")
 86 |     except (urllib.error.URLError, OSError, ValueError) as exc:
 87 |         lines.append(f"FAIL  no OpenAI-compatible server at {config.api_base}: {exc}")
 88 |         lines.append("      start it first (for llama.cpp: .\\serve.ps1)")
 89 |         return False
 90 |     lines.append(f"ok    server reachable at {config.api_base}")
 91 |     return True
 92 | 
 93 | 
```
- Asks the OpenAI-style server for its model list (`GET /models`); any answer means it is running.

```python
 94 | def _check_local(config: Config, lines: list[str]) -> bool:
 95 |     """The local model: a server that answers, or llama.cpp plus a model file it can start."""
 96 |     if running_local(config):
 97 |         lines.append(f"ok    local model: {describe_local(config)}")
 98 |         return True
 99 |     program, model = find_llama_server(config), find_model_file(config)
100 |     if program is not None and model is not None and can_start(config):
101 |         lines.append(f"ok    local model: {model.name} ({model.stat().st_size // 1_000_000} MB), "
102 |                      f"started with {program} when needed")
103 |         return True
104 |     if program is None:
105 |         lines.append(f"FAIL  llama.cpp's llama-server was not found (see {SETUP_DOC})")
106 |     if model is None:
107 |         lines.append(f"FAIL  no .gguf model file was found (see {SETUP_DOC})")
108 |     if program is not None and model is not None:
109 |         lines.append(f"FAIL  nothing answers at {config.api_base}, and a server can only be "
110 |                      "started for an address on this computer")
111 |     return False
112 | 
113 | 
```
- The check for offline work (backend `local`, and the backup of `auto` mode). A running local server is fine. So are
  llama.cpp's server program plus a model file, when a server may be started for `api_base` — the line shows the model
  and its size and which program will start it, e.g. `ok    local model: Qwen3-8B-Q4_K_M.gguf (5027 MB), started with
  ...\bin\llamacpp-vulkan\llama-server.exe when needed`. Otherwise one `FAIL` line per missing piece, each pointing to the
  setup guide.

```python
114 | def _check_gemini(config: Config, lines: list[str]) -> bool:
115 |     if not config.gemini_api_key:
116 |         lines.append("FAIL  GEMINI_API_KEY is not set (see docs/04-start-from-zero.md, step 6)")
117 |         return False
118 |     try:
119 |         listed = _get_json(config.gemini_base + "/models",
120 |                            {"Authorization": f"Bearer {config.gemini_api_key}"})
121 |     except urllib.error.HTTPError as exc:
122 |         lines.append(f"FAIL  Gemini rejected the key (HTTP {exc.code}); create a new key in AI Studio")
123 |         return False
124 |     except (urllib.error.URLError, OSError, ValueError) as exc:
125 |         lines.append(f"FAIL  cannot reach Gemini: {exc}")
126 |         return False
127 |     available = {str(m.get("id", "")).removeprefix("models/") for m in listed.get("data", [])}
128 |     lines.append("ok    Gemini key accepted")
129 |     healthy = True
130 |     for model in config.gemini_models + config.gemini_strong_models:
131 |         if model not in available:
132 |             lines.append(f"FAIL  model {model} is not offered to this key")
133 |             healthy = False
134 |         rpm, rpd = model_limits(config, model)
135 |         usage = RateLimiter(model, rpm, config.tpm, rpd, config.usage_file).status()
136 |         lines.append(f"      {usage}")
137 |     backups = {"openai": _check_openai, "ollama": _check_ollama, "local": _check_local}
138 |     if config.fallback_backend in backups:              # an optional backup: never a FAIL
139 |         found: list[str] = []
140 |         if backups[config.fallback_backend](config, found):
141 |             lines += found
142 |         else:
143 |             lines.append("note  the offline backup is not available (optional - Gemini works "
144 |                          "without it):")
145 |             lines += ["      " + line[6:] for line in found]
146 |     elif config.mode == "auto":
147 |         lines.append(f"note  no offline backup: without internet nothing answers (see {SETUP_DOC})")
148 |     return healthy
149 | 
150 | 
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
151 | def run_doctor(config: Config) -> tuple[bool, list[str]]:
152 |     """Return (everything essential works, human-readable report lines)."""
153 |     lines = [f"localagent {__version__}, python {sys.version.split()[0]}",
154 |              f"mode  {config.mode} (backend {config.backend})"]
155 |     _check_tools(lines)
156 |     sandbox_ok = _check_sandbox(config, lines)
157 |     checks = {"ollama": _check_ollama, "openai": _check_openai, "gemini": _check_gemini,
158 |               "local": _check_local}
159 |     backend_ok = checks[config.backend](config, lines) if config.backend in checks else True
160 |     return sandbox_ok and backend_ok, lines
```
- Version and Python, the mode and backend, tools, sandbox, then the backend check chosen by name. Healthy = sandbox OK
  and backend OK. When no model can be set up at all, the CLI still runs the doctor and puts the reason first.
