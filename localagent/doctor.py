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

from .backends import model_limits
from .config import Config
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
    if config.fallback_backend in ("openai", "ollama"):    # an optional backup: never a FAIL
        found: list[str] = []
        check = _check_openai if config.fallback_backend == "openai" else _check_ollama
        if check(config, found):
            lines += found
        else:
            lines.append("note  the backup model is not reachable (optional - Gemini works without it):")
            lines += ["      " + line[6:] for line in found]
    return healthy


def run_doctor(config: Config) -> tuple[bool, list[str]]:
    """Return (everything essential works, human-readable report lines)."""
    lines = [f"python {sys.version.split()[0]}"]
    _check_tools(lines)
    sandbox_ok = _check_sandbox(config, lines)
    checks = {"ollama": _check_ollama, "openai": _check_openai, "gemini": _check_gemini}
    backend_ok = checks[config.backend](config, lines) if config.backend in checks else True
    return sandbox_ok and backend_ok, lines
