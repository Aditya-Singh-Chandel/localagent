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
 42 |     """ruff checks every Python write; mypy is used only with --type-check."""
 43 |     if importlib.util.find_spec("ruff"):
 44 |         lines.append("ok    ruff installed (verification layer active)")
 45 |     else:
 46 |         lines.append("warn  ruff missing: pip install ruff (the compile check still works)")
 47 |     if importlib.util.find_spec("mypy"):
 48 |         lines.append("ok    mypy installed (used with --type-check)")
 49 |     else:
 50 |         lines.append("note  mypy not installed: only --type-check needs it (pip install mypy)")
 51 | 
 52 | 
```
- ruff is the second verification layer of every Python write: missing → a *warning* (the compile check still works).
  mypy, the third, runs only with `--type-check`: missing → just a *note*. The Windows download leaves mypy out to stay
  half the size, so its doctor would otherwise warn about something nobody asked for.

```python
 53 | def _check_sandbox(config: Config, lines: list[str]) -> bool:
 54 |     if config.sandbox != "docker":
 55 |         lines.append("note  sandbox 'local': code runs where the agent runs (use --sandbox docker "
 56 |                      "for a separate locked-down container)")
 57 |         return True
 58 |     if not docker_available():
 59 |         lines.append("FAIL  Docker is not running: start Docker Desktop and retry")
 60 |         return False
 61 |     code, _ = run_command(["docker", "image", "inspect", config.sandbox_image],
 62 |                           Path.cwd(), 30, 200)
 63 |     if code != 0:
 64 |         lines.append(f"FAIL  sandbox image {config.sandbox_image} missing; build it with:\n"
 65 |                      "      docker build -t localagent-sandbox:latest -f docker/sandbox.Dockerfile docker")
 66 |         return False
 67 |     lines.append(f"ok    Docker running and image {config.sandbox_image} present")
 68 |     return True
 69 | 
 70 | 
```
- With the local sandbox there is nothing to check (just a note). With Docker: is the engine running, and has the sandbox
  image been built? Each failure prints the fix (start Docker Desktop / the exact `docker build` command).

```python
 71 | def _check_ollama(config: Config, lines: list[str]) -> bool:
 72 |     try:
 73 |         models = _installed_models(config.host)
 74 |     except (urllib.error.URLError, OSError, ValueError) as exc:
 75 |         lines.append(f"FAIL  Ollama not reachable at {config.host}: {exc}")
 76 |         lines.append("      start the Ollama app (or run `ollama serve`) and retry")
 77 |         return False
 78 |     lines.append(f"ok    Ollama reachable at {config.host}")
 79 |     wanted = config.model if ":" in config.model else config.model + ":latest"
 80 |     if wanted in models:
 81 |         lines.append(f"ok    model {config.model} is installed")
 82 |         return True
 83 |     lines.append(f"FAIL  model {config.model} is not installed; run: ollama pull {config.model}")
 84 |     lines.append(f"      installed models: {', '.join(models) or '(none)'}")
 85 |     return False
 86 | 
 87 | 
```
- Ollama reachable? Is the model downloaded? A name without `:tag` means `:latest` in Ollama, so that is added before
  comparing. Prints the exact `ollama pull` command and what *is* installed.

```python
 88 | def _check_openai(config: Config, lines: list[str]) -> bool:
 89 |     try:
 90 |         _get_json(config.api_base + "/models")
 91 |     except (urllib.error.URLError, OSError, ValueError) as exc:
 92 |         lines.append(f"FAIL  no OpenAI-compatible server at {config.api_base}: {exc}")
 93 |         lines.append("      start it first (for llama.cpp: .\\serve.ps1)")
 94 |         return False
 95 |     lines.append(f"ok    server reachable at {config.api_base}")
 96 |     return True
 97 | 
 98 | 
```
- Asks the OpenAI-style server for its model list (`GET /models`); any answer means it is running.

```python
 99 | def _check_local(config: Config, lines: list[str]) -> bool:
100 |     """The local model: a server that answers, or llama.cpp plus a model file it can start."""
101 |     if running_local(config):
102 |         lines.append(f"ok    local model: {describe_local(config)}")
103 |         return True
104 |     if config.local_server == "ollama":                 # chosen in setup: only Ollama counts
105 |         lines.append(f"FAIL  Ollama does not answer at {config.host}: start the Ollama app (or "
106 |                      "`ollama serve`), or choose another local model with localagent setup")
107 |         return False
108 |     if config.local_server == "openai" and (urlsplit(config.api_base).hostname or "") not in THIS_COMPUTER:
109 |         lines.append(f"FAIL  the model server at {config.api_base} does not answer: start it, or "
110 |                      "choose another local model with localagent setup")
111 |         return False
112 |     program, model = find_llama_server(config), find_model_file(config)
113 |     if program is not None and model is not None and can_start(config):
114 |         lines.append(f"ok    local model: {model.name} ({model.stat().st_size // 1_000_000} MB), "
115 |                      f"started with {program} when needed")
116 |         return True
117 |     if program is None:
118 |         lines.append(f"FAIL  llama.cpp's llama-server was not found ({SETUP_HINT})")
119 |     if model is None:
120 |         lines.append(f"FAIL  no .gguf model file was found ({SETUP_HINT})")
121 |     if program is not None and model is not None:
122 |         lines.append(f"FAIL  nothing answers at {config.api_base}, and a server can only be "
123 |                      "started for an address on this computer")
124 |     return False
125 | 
126 | 
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
127 | def _check_gemini(config: Config, lines: list[str]) -> bool:
128 |     if not config.gemini_api_key:
129 |         lines.append("FAIL  GEMINI_API_KEY is not set (see docs/04-start-from-zero.md, step 6)")
130 |         return False
131 |     try:
132 |         listed = _get_json(config.gemini_base + "/models",
133 |                            {"Authorization": f"Bearer {config.gemini_api_key}"})
134 |     except urllib.error.HTTPError as exc:
135 |         lines.append(f"FAIL  Gemini rejected the key (HTTP {exc.code}); create a new key in AI Studio")
136 |         return False
137 |     except (urllib.error.URLError, OSError, ValueError) as exc:
138 |         lines.append(f"FAIL  cannot reach Gemini: {exc}")
139 |         return False
140 |     available = {str(m.get("id", "")).removeprefix("models/") for m in listed.get("data", [])}
141 |     lines.append("ok    Gemini key accepted")
142 |     healthy = True
143 |     for model in config.gemini_models + config.gemini_strong_models:
144 |         if model not in available:
145 |             lines.append(f"FAIL  model {model} is not offered to this key")
146 |             healthy = False
147 |         rpm, rpd = model_limits(config, model)
148 |         usage = RateLimiter(model, rpm, config.tpm, rpd, config.usage_file).status()
149 |         lines.append(f"      {usage}")
150 |     _check_backup(config, lines)
151 |     return healthy
152 | 
153 | 
```
- No key → FAIL with a pointer to the beginner guide.
- Lists the models offered to your key (this does not use generation quota). HTTP error → the key was rejected.
- Each model in the chain must be in that list; for each it prints today's usage from `usage.json` (with that model's
  own limits, see `model_limits`).
- Then the offline backup (`_check_backup`).

```python
154 | def _check_hosted(config: Config, lines: list[str]) -> bool:
155 |     """Another online service: the key is accepted and the chosen model is offered."""
156 |     provider = find_provider(config.provider) or PROVIDERS[-1]
157 |     name = "the service" if provider.name == "custom" else provider.title
158 |     problem = online_problem(config)
159 |     if problem:
160 |         lines.append(f"FAIL  {problem}")
161 |         return False
162 |     try:
163 |         listed = _get_json(config.provider_base + "/models", key_headers(provider, config.provider_key))
164 |     except urllib.error.HTTPError as exc:
165 |         exc.close()                                     # release the connection
166 |         lines.append(f"FAIL  {name} refused the key (HTTP {exc.code}); run localagent setup "
167 |                      "with a new key")
168 |         return False
169 |     except (urllib.error.URLError, OSError, ValueError) as exc:
170 |         lines.append(f"FAIL  cannot reach {name} at {config.provider_base}: {exc}")
171 |         return False
172 |     available = {str(m.get("id", "")).removeprefix("models/") for m in listed.get("data", [])
173 |                  if isinstance(m, dict)}                 # Gemini's names start with "models/"
174 |     lines.append(f"ok    key accepted by {name} at {config.provider_base}")
175 |     healthy = config.provider_model in available or not available
176 |     if healthy:
177 |         lines.append(f"ok    model {config.provider_model}")
178 |     else:
179 |         lines.append(f"FAIL  model {config.provider_model} is not offered to this key; choose "
180 |                      "another with localagent setup")
181 |     _check_backup(config, lines)
182 |     return healthy
183 | 
184 | 
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
185 | def _check_backup(config: Config, lines: list[str]) -> None:
186 |     """The offline backup of auto mode: optional, so a missing one is a note, never a FAIL."""
187 |     backups = {"openai": _check_local, "ollama": _check_ollama, "local": _check_local}
188 |     if config.fallback_backend in backups:              # an optional backup: never a FAIL
189 |         found: list[str] = []
190 |         if backups[config.fallback_backend](config, found):
191 |             lines += found
192 |         else:
193 |             lines.append("note  the offline backup is not available (optional - the online "
194 |                          "model works without it):")
195 |             lines += ["      " + line[6:] for line in found]
196 |     elif config.mode == "auto":
197 |         lines.append(f"note  no offline backup: without internet nothing answers ({SETUP_HINT})")
198 | 
199 | 
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
200 | def run_doctor(config: Config) -> tuple[bool, list[str]]:
201 |     """Return (everything essential works, human-readable report lines)."""
202 |     lines = [f"localagent {__version__}, python {sys.version.split()[0]}",
203 |              f"mode  {config.mode} (backend {config.backend})"]
204 |     _check_tools(lines)
205 |     sandbox_ok = _check_sandbox(config, lines)
206 |     checks = {"ollama": _check_ollama, "openai": _check_openai, "gemini": _check_gemini,
207 |               "hosted": _check_hosted, "local": _check_local}
208 |     backend_ok = checks[config.backend](config, lines) if config.backend in checks else True
209 |     return sandbox_ok and backend_ok, lines
```
- Version and Python, the mode and backend, tools, sandbox, then the backend check chosen by name (`hosted` is any
  online service other than Gemini). Healthy = sandbox OK
  and backend OK. When no model can be set up at all, the CLI still runs the doctor and puts the reason first.
