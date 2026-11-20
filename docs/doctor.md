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
 16 | from urllib.parse import urlsplit
 17 | 
 18 | from . import __version__
 19 | from .backends import model_limits
 20 | from .config import Config, online_problem
 21 | from .localserver import (SETUP_HINT, THIS_COMPUTER, can_start, describe_local, find_llama_server,
 22 |                           find_model_file, running_local)
 23 | from .providers import PROVIDERS, find_provider, key_headers
 24 | from .ratelimit import RateLimiter
 25 | from .runners import docker_available
 26 | from .sandbox import run_command
 27 | 
 28 | 
```
- `importlib.util` tells whether a package is installed; `urllib` performs simple HTTP GETs and `urlsplit` takes an
  address apart; `__version__` is shown first; `online_problem` says what the online model lacks; the provider table
  helpers send a service's key the way it expects; `RateLimiter` reads today's quota use; `model_limits` gives
  per-model limits; the `localserver` functions (and `THIS_COMPUTER`, the addresses that mean "this computer") find
  the local model; `docker_available` and `run_command` check Docker. `SETUP_DOC` is the guide named in local-model advice.

## Helpers
```python
 29 | def _get_json(url: str, headers: dict[str, str] | None = None) -> dict:
 30 |     """GET a URL and parse the JSON answer (5-second timeout keeps the check quick)."""
 31 |     request = urllib.request.Request(url, headers=headers or {})
 32 |     with urllib.request.urlopen(request, timeout=5) as response:
 33 |         return json.load(response)
 34 | 
 35 | 
```
- GET a URL (optionally with headers such as the API key) and parse the JSON reply. 5-second timeout: a dead server
  should not make the doctor hang.

```python
 36 | def _installed_models(host: str) -> list[str]:
 37 |     """Ask Ollama which models are pulled (GET /api/tags)."""
 38 |     return [entry["name"] for entry in _get_json(host + "/api/tags").get("models", [])]
 39 | 
 40 | 
```
- Ollama's `GET /api/tags` lists the models you have downloaded.

## The checks
```python
 41 | def _check_tools(lines: list[str]) -> None:
 42 |     for tool in ("ruff", "mypy"):
 43 |         if importlib.util.find_spec(tool):
 44 |             lines.append(f"ok    {tool} installed (verification layer active)")
 45 |         else:
 46 |             lines.append(f"warn  {tool} missing: pip install -e '.[dev]'")
 47 | 
 48 | 
```
- ruff and mypy are the second and third verification layers. Missing → a *warning* (the compile check still works).

```python
 49 | def _check_sandbox(config: Config, lines: list[str]) -> bool:
 50 |     if config.sandbox != "docker":
 51 |         lines.append("note  sandbox 'local': code runs where the agent runs (use --sandbox docker "
 52 |                      "for a separate locked-down container)")
 53 |         return True
 54 |     if not docker_available():
 55 |         lines.append("FAIL  Docker is not running: start Docker Desktop and retry")
 56 |         return False
 57 |     code, _ = run_command(["docker", "image", "inspect", config.sandbox_image],
 58 |                           Path.cwd(), 30, 200)
 59 |     if code != 0:
 60 |         lines.append(f"FAIL  sandbox image {config.sandbox_image} missing; build it with:\n"
 61 |                      "      docker build -t localagent-sandbox:latest -f docker/sandbox.Dockerfile docker")
 62 |         return False
 63 |     lines.append(f"ok    Docker running and image {config.sandbox_image} present")
 64 |     return True
 65 | 
 66 | 
```
- With the local sandbox there is nothing to check (just a note). With Docker: is the engine running, and has the sandbox
  image been built? Each failure prints the fix (start Docker Desktop / the exact `docker build` command).

```python
 67 | def _check_ollama(config: Config, lines: list[str]) -> bool:
 68 |     try:
 69 |         models = _installed_models(config.host)
 70 |     except (urllib.error.URLError, OSError, ValueError) as exc:
 71 |         lines.append(f"FAIL  Ollama not reachable at {config.host}: {exc}")
 72 |         lines.append("      start the Ollama app (or run `ollama serve`) and retry")
 73 |         return False
 74 |     lines.append(f"ok    Ollama reachable at {config.host}")
 75 |     wanted = config.model if ":" in config.model else config.model + ":latest"
 76 |     if wanted in models:
 77 |         lines.append(f"ok    model {config.model} is installed")
 78 |         return True
 79 |     lines.append(f"FAIL  model {config.model} is not installed; run: ollama pull {config.model}")
 80 |     lines.append(f"      installed models: {', '.join(models) or '(none)'}")
 81 |     return False
 82 | 
 83 | 
```
- Ollama reachable? Is the model downloaded? A name without `:tag` means `:latest` in Ollama, so that is added before
  comparing. Prints the exact `ollama pull` command and what *is* installed.

```python
 84 | def _check_openai(config: Config, lines: list[str]) -> bool:
 85 |     try:
 86 |         _get_json(config.api_base + "/models")
 87 |     except (urllib.error.URLError, OSError, ValueError) as exc:
 88 |         lines.append(f"FAIL  no OpenAI-compatible server at {config.api_base}: {exc}")
 89 |         lines.append("      start it first (for llama.cpp: .\\serve.ps1)")
 90 |         return False
 91 |     lines.append(f"ok    server reachable at {config.api_base}")
 92 |     return True
 93 | 
 94 | 
```
- Asks the OpenAI-style server for its model list (`GET /models`); any answer means it is running.

```python
 95 | def _check_local(config: Config, lines: list[str]) -> bool:
 96 |     """The local model: a server that answers, or llama.cpp plus a model file it can start."""
 97 |     if running_local(config):
 98 |         lines.append(f"ok    local model: {describe_local(config)}")
 99 |         return True
100 |     if config.local_server == "ollama":                 # chosen in setup: only Ollama counts
101 |         lines.append(f"FAIL  Ollama does not answer at {config.host}: start the Ollama app (or "
102 |                      "`ollama serve`), or choose another local model with localagent setup")
103 |         return False
104 |     if config.local_server == "openai" and (urlsplit(config.api_base).hostname or "") not in THIS_COMPUTER:
105 |         lines.append(f"FAIL  the model server at {config.api_base} does not answer: start it, or "
106 |                      "choose another local model with localagent setup")
107 |         return False
108 |     program, model = find_llama_server(config), find_model_file(config)
109 |     if program is not None and model is not None and can_start(config):
110 |         lines.append(f"ok    local model: {model.name} ({model.stat().st_size // 1_000_000} MB), "
111 |                      f"started with {program} when needed")
112 |         return True
113 |     if program is None:
114 |         lines.append(f"FAIL  llama.cpp's llama-server was not found ({SETUP_HINT})")
115 |     if model is None:
116 |         lines.append(f"FAIL  no .gguf model file was found ({SETUP_HINT})")
117 |     if program is not None and model is not None:
118 |         lines.append(f"FAIL  nothing answers at {config.api_base}, and a server can only be "
119 |                      "started for an address on this computer")
120 |     return False
121 | 
122 | 
```
- The check for offline work (backend `local`, and the backup of `auto` mode). A running local server is fine. So are
  llama.cpp's server program plus a model file, when a server may be started for `api_base` — the line shows the model
  and its size and which program will start it, e.g. `ok    local model: Qwen3-8B-Q4_K_M.gguf (5027 MB), started with
  ...\bin\llamacpp-vulkan\llama-server.exe when needed`. Otherwise one `FAIL` line per missing piece, each pointing to the
  setup guide.
- A server chosen in `localagent setup` is judged as itself: Ollama chosen but not answering → one `FAIL` that says to
  start Ollama (or choose again); a server at another computer's address that does not answer → one `FAIL` naming it
  — not advice about llama.cpp, which could not be started for that address anyway.

```python
123 | def _check_gemini(config: Config, lines: list[str]) -> bool:
124 |     if not config.gemini_api_key:
125 |         lines.append("FAIL  GEMINI_API_KEY is not set (see docs/04-start-from-zero.md, step 6)")
126 |         return False
127 |     try:
128 |         listed = _get_json(config.gemini_base + "/models",
129 |                            {"Authorization": f"Bearer {config.gemini_api_key}"})
130 |     except urllib.error.HTTPError as exc:
131 |         lines.append(f"FAIL  Gemini rejected the key (HTTP {exc.code}); create a new key in AI Studio")
132 |         return False
133 |     except (urllib.error.URLError, OSError, ValueError) as exc:
134 |         lines.append(f"FAIL  cannot reach Gemini: {exc}")
135 |         return False
136 |     available = {str(m.get("id", "")).removeprefix("models/") for m in listed.get("data", [])}
137 |     lines.append("ok    Gemini key accepted")
138 |     healthy = True
139 |     for model in config.gemini_models + config.gemini_strong_models:
140 |         if model not in available:
141 |             lines.append(f"FAIL  model {model} is not offered to this key")
142 |             healthy = False
143 |         rpm, rpd = model_limits(config, model)
144 |         usage = RateLimiter(model, rpm, config.tpm, rpd, config.usage_file).status()
145 |         lines.append(f"      {usage}")
146 |     _check_backup(config, lines)
147 |     return healthy
148 | 
149 | 
```
- No key → FAIL with a pointer to the beginner guide.
- Lists the models offered to your key (this does not use generation quota). HTTP error → the key was rejected.
- Each model in the chain must be in that list; for each it prints today's usage from `usage.json` (with that model's
  own limits, see `model_limits`).
- Then the offline backup (`_check_backup`).

```python
150 | def _check_hosted(config: Config, lines: list[str]) -> bool:
151 |     """Another online service: the key is accepted and the chosen model is offered."""
152 |     provider = find_provider(config.provider) or PROVIDERS[-1]
153 |     name = "the service" if provider.name == "custom" else provider.title
154 |     problem = online_problem(config)
155 |     if problem:
156 |         lines.append(f"FAIL  {problem}")
157 |         return False
158 |     try:
159 |         listed = _get_json(config.provider_base + "/models", key_headers(provider, config.provider_key))
160 |     except urllib.error.HTTPError as exc:
161 |         exc.close()                                     # release the connection
162 |         lines.append(f"FAIL  {name} refused the key (HTTP {exc.code}); run localagent setup "
163 |                      "with a new key")
164 |         return False
165 |     except (urllib.error.URLError, OSError, ValueError) as exc:
166 |         lines.append(f"FAIL  cannot reach {name} at {config.provider_base}: {exc}")
167 |         return False
168 |     available = {str(m.get("id", "")).removeprefix("models/") for m in listed.get("data", [])
169 |                  if isinstance(m, dict)}                 # Gemini's names start with "models/"
170 |     lines.append(f"ok    key accepted by {name} at {config.provider_base}")
171 |     healthy = config.provider_model in available or not available
172 |     if healthy:
173 |         lines.append(f"ok    model {config.provider_model}")
174 |     else:
175 |         lines.append(f"FAIL  model {config.provider_model} is not offered to this key; choose "
176 |                      "another with localagent setup")
177 |     _check_backup(config, lines)
178 |     return healthy
179 | 
180 | 
```
- Another online service: first anything `online_problem` reports (no key, no model, no address) is one `FAIL` with
  the fix. Then the service's model list is asked with your key, sent the way the service expects (`key_headers`): an
  HTTP error means the key was refused (the error is closed, freeing the connection); no answer means the service
  cannot be reached. The chosen model must be in the list — compared without Gemini's `models/` prefix, because the
  live test pointed a `custom` service at Gemini's address and the check wrongly said "not offered". A service that
  lists no models at all is trusted. Then the offline backup.
- The service is called by its title, or "the service" for `custom` (its title, "another OpenAI-compatible
  service", reads badly in a sentence).

```python
181 | def _check_backup(config: Config, lines: list[str]) -> None:
182 |     """The offline backup of auto mode: optional, so a missing one is a note, never a FAIL."""
183 |     backups = {"openai": _check_local, "ollama": _check_ollama, "local": _check_local}
184 |     if config.fallback_backend in backups:              # an optional backup: never a FAIL
185 |         found: list[str] = []
186 |         if backups[config.fallback_backend](config, found):
187 |             lines += found
188 |         else:
189 |             lines.append("note  the offline backup is not available (optional - the online "
190 |                          "model works without it):")
191 |             lines += ["      " + line[6:] for line in found]
192 |     elif config.mode == "auto":
193 |         lines.append(f"note  no offline backup: without internet nothing answers ({SETUP_HINT})")
194 | 
195 | 
```
- The local fallback of `auto` mode (`local`, `openai` or `ollama`) is checked into a separate list `found`; `local`
  and `openai` both use `_check_local`, which also accepts a model that *can be started*. Available → its `ok` lines
  are shown. Not available → one `note` line ("the offline backup is not available (optional - the online model works
  without it)") with the details indented below it (`line[6:]` cuts the 6-character `FAIL  ` prefix), and the result
  stays healthy. Found in the final verification of 0.5: the agent's Docker image always configures the local backup,
  so its doctor printed `FAIL` whenever `serve.ps1` was not running — although the agent works without it. In `auto`
  mode without any backup, a `note` says that nothing will answer without internet.

## `run_doctor`
```python
196 | def run_doctor(config: Config) -> tuple[bool, list[str]]:
197 |     """Return (everything essential works, human-readable report lines)."""
198 |     lines = [f"localagent {__version__}, python {sys.version.split()[0]}",
199 |              f"mode  {config.mode} (backend {config.backend})"]
200 |     _check_tools(lines)
201 |     sandbox_ok = _check_sandbox(config, lines)
202 |     checks = {"ollama": _check_ollama, "openai": _check_openai, "gemini": _check_gemini,
203 |               "hosted": _check_hosted, "local": _check_local}
204 |     backend_ok = checks[config.backend](config, lines) if config.backend in checks else True
205 |     return sandbox_ok and backend_ok, lines
```
- Version and Python, the mode and backend, tools, sandbox, then the backend check chosen by name (`hosted` is any
  online service other than Gemini). Healthy = sandbox OK
  and backend OK. When no model can be set up at all, the CLI still runs the doctor and puts the reason first.
