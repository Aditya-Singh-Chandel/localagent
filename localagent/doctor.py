"""Health check behind ``--doctor``: what is working and what to fix.

Answers the questions that explain most "it does nothing" problems: are the
verification tools (ruff, mypy) installed, is the code sandbox (Docker) ready,
and can the chosen model service be reached with the chosen model or key?
Every failure line says how to fix it.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

from . import __version__
from .backends import model_limits
from .config import Config, online_problem
from .localserver import (SETUP_HINT, THIS_COMPUTER, can_start, describe_local, find_llama_server,
                          find_model_file, running_local)
from .providers import PROVIDERS, find_provider, key_headers
from .ratelimit import RateLimiter
from .runners import docker_available
from .sandbox import run_command


def _get_json(url: str, headers: dict[str, str] | None = None) -> dict:
    """GET a URL and parse the JSON answer (5-second timeout keeps the check quick)."""
    request = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.load(response)


def _installed_models(host: str) -> list[str]:
    """Ask Ollama which models are pulled (GET /api/tags)."""
    return [entry["name"] for entry in _get_json(host + "/api/tags").get("models", [])]


def _check_tools(lines: list[str]) -> None:
    for tool in ("ruff", "mypy"):
        if importlib.util.find_spec(tool):
            lines.append(f"ok    {tool} installed (verification layer active)")
        else:
            lines.append(f"warn  {tool} missing: pip install -e '.[dev]'")


def _check_sandbox(config: Config, lines: list[str]) -> bool:
    if config.sandbox != "docker":
        lines.append("note  sandbox 'local': code runs where the agent runs (use --sandbox docker "
                     "for a separate locked-down container)")
        return True
    if not docker_available():
        lines.append("FAIL  Docker is not running: start Docker Desktop and retry")
        return False
    code, _ = run_command(["docker", "image", "inspect", config.sandbox_image],
                          Path.cwd(), 30, 200)
    if code != 0:
        lines.append(f"FAIL  sandbox image {config.sandbox_image} missing; build it with:\n"
                     "      docker build -t localagent-sandbox:latest -f docker/sandbox.Dockerfile docker")
        return False
    lines.append(f"ok    Docker running and image {config.sandbox_image} present")
    return True


def _check_ollama(config: Config, lines: list[str]) -> bool:
    try:
        models = _installed_models(config.host)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        lines.append(f"FAIL  Ollama not reachable at {config.host}: {exc}")
        lines.append("      start the Ollama app (or run `ollama serve`) and retry")
        return False
    lines.append(f"ok    Ollama reachable at {config.host}")
    wanted = config.model if ":" in config.model else config.model + ":latest"
    if wanted in models:
        lines.append(f"ok    model {config.model} is installed")
        return True
    lines.append(f"FAIL  model {config.model} is not installed; run: ollama pull {config.model}")
    lines.append(f"      installed models: {', '.join(models) or '(none)'}")
    return False


def _check_openai(config: Config, lines: list[str]) -> bool:
    try:
        _get_json(config.api_base + "/models")
    except (urllib.error.URLError, OSError, ValueError) as exc:
        lines.append(f"FAIL  no OpenAI-compatible server at {config.api_base}: {exc}")
        lines.append("      start it first (for llama.cpp: .\\serve.ps1)")
        return False
    lines.append(f"ok    server reachable at {config.api_base}")
    return True


def _check_local(config: Config, lines: list[str]) -> bool:
    """The local model: a server that answers, or llama.cpp plus a model file it can start."""
    if running_local(config):
        lines.append(f"ok    local model: {describe_local(config)}")
        return True
    if config.local_server == "ollama":                 # chosen in setup: only Ollama counts
        lines.append(f"FAIL  Ollama does not answer at {config.host}: start the Ollama app (or "
                     "`ollama serve`), or choose another local model with localagent setup")
        return False
    if config.local_server == "openai" and (urlsplit(config.api_base).hostname or "") not in THIS_COMPUTER:
        lines.append(f"FAIL  the model server at {config.api_base} does not answer: start it, or "
                     "choose another local model with localagent setup")
        return False
    program, model = find_llama_server(config), find_model_file(config)
    if program is not None and model is not None and can_start(config):
        lines.append(f"ok    local model: {model.name} ({model.stat().st_size // 1_000_000} MB), "
                     f"started with {program} when needed")
        return True
    if program is None:
        lines.append(f"FAIL  llama.cpp's llama-server was not found ({SETUP_HINT})")
    if model is None:
        lines.append(f"FAIL  no .gguf model file was found ({SETUP_HINT})")
    if program is not None and model is not None:
        lines.append(f"FAIL  nothing answers at {config.api_base}, and a server can only be "
                     "started for an address on this computer")
    return False


def _check_gemini(config: Config, lines: list[str]) -> bool:
    if not config.gemini_api_key:
        lines.append("FAIL  GEMINI_API_KEY is not set (see docs/04-start-from-zero.md, step 6)")
        return False
    try:
        listed = _get_json(config.gemini_base + "/models",
                           {"Authorization": f"Bearer {config.gemini_api_key}"})
    except urllib.error.HTTPError as exc:
        lines.append(f"FAIL  Gemini rejected the key (HTTP {exc.code}); create a new key in AI Studio")
        return False
    except (urllib.error.URLError, OSError, ValueError) as exc:
        lines.append(f"FAIL  cannot reach Gemini: {exc}")
        return False
    available = {str(m.get("id", "")).removeprefix("models/") for m in listed.get("data", [])}
    lines.append("ok    Gemini key accepted")
    healthy = True
    for model in config.gemini_models + config.gemini_strong_models:
        if model not in available:
            lines.append(f"FAIL  model {model} is not offered to this key")
            healthy = False
        rpm, rpd = model_limits(config, model)
        usage = RateLimiter(model, rpm, config.tpm, rpd, config.usage_file).status()
        lines.append(f"      {usage}")
    _check_backup(config, lines)
    return healthy


def _check_hosted(config: Config, lines: list[str]) -> bool:
    """Another online service: the key is accepted and the chosen model is offered."""
    provider = find_provider(config.provider) or PROVIDERS[-1]
    name = "the service" if provider.name == "custom" else provider.title
    problem = online_problem(config)
    if problem:
        lines.append(f"FAIL  {problem}")
        return False
    try:
        listed = _get_json(config.provider_base + "/models", key_headers(provider, config.provider_key))
    except urllib.error.HTTPError as exc:
        exc.close()                                     # release the connection
        lines.append(f"FAIL  {name} refused the key (HTTP {exc.code}); run localagent setup "
                     "with a new key")
        return False
    except (urllib.error.URLError, OSError, ValueError) as exc:
        lines.append(f"FAIL  cannot reach {name} at {config.provider_base}: {exc}")
        return False
    available = {str(m.get("id", "")).removeprefix("models/") for m in listed.get("data", [])
                 if isinstance(m, dict)}                 # Gemini's names start with "models/"
    lines.append(f"ok    key accepted by {name} at {config.provider_base}")
    healthy = config.provider_model in available or not available
    if healthy:
        lines.append(f"ok    model {config.provider_model}")
    else:
        lines.append(f"FAIL  model {config.provider_model} is not offered to this key; choose "
                     "another with localagent setup")
    _check_backup(config, lines)
    return healthy


def _check_backup(config: Config, lines: list[str]) -> None:
    """The offline backup of auto mode: optional, so a missing one is a note, never a FAIL."""
    backups = {"openai": _check_local, "ollama": _check_ollama, "local": _check_local}
    if config.fallback_backend in backups:              # an optional backup: never a FAIL
        found: list[str] = []
        if backups[config.fallback_backend](config, found):
            lines += found
        else:
            lines.append("note  the offline backup is not available (optional - the online "
                         "model works without it):")
            lines += ["      " + line[6:] for line in found]
    elif config.mode == "auto":
        lines.append(f"note  no offline backup: without internet nothing answers ({SETUP_HINT})")


def run_doctor(config: Config) -> tuple[bool, list[str]]:
    """Return (everything essential works, human-readable report lines)."""
    lines = [f"localagent {__version__}, python {sys.version.split()[0]}",
             f"mode  {config.mode} (backend {config.backend})"]
    _check_tools(lines)
    sandbox_ok = _check_sandbox(config, lines)
    checks = {"ollama": _check_ollama, "openai": _check_openai, "gemini": _check_gemini,
              "hosted": _check_hosted, "local": _check_local}
    backend_ok = checks[config.backend](config, lines) if config.backend in checks else True
    return sandbox_ok and backend_ok, lines
