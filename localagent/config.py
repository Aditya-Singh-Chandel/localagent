"""Central configuration for the local coding agent.

Every tunable number or name lives here, so the rest of the code contains no
"magic values". Values can be overridden with environment variables
(``Config.from_env``) or with command-line flags (see ``cli.py``).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any


def _normalize_host(raw: str) -> str:
    """Turn ``127.0.0.1:11434`` (Ollama's own style) into a full URL."""
    if raw.startswith(("http://", "https://")):
        return raw.rstrip("/")
    return "http://" + raw.rstrip("/")


PAID_RPM, PAID_RPD, PAID_TPM = 1000, 10_000, 1_000_000   # paid-tier presets (well under Tier 1)
MODES = ("auto", "online", "offline")


def _split(raw: str) -> tuple[str, ...]:
    """Turn ``"a, b,c"`` into ``("a", "b", "c")``; empty text gives ``()``."""
    return tuple(part.strip() for part in raw.split(",") if part.strip())


def _int(name: str, default: int) -> int:
    """A whole-number environment variable, or ``default``; a typo is explained, not a crash."""
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        raise ValueError(f"{name} must be a whole number such as {default} (it is '{raw}')") from None


@dataclass(frozen=True)
class Config:
    """Immutable settings object that is handed to every component."""

    backend: str = "ollama"                  # "ollama", "openai", "gemini" or "local" (see modes)
    model: str = "qwen3:8b"                  # model tag/name understood by the backend
    host: str = "http://localhost:11434"     # Ollama server address
    api_base: str = "http://127.0.0.1:8080/v1"  # OpenAI-compatible server (llama.cpp serve.ps1;
    #                                             LM Studio uses http://localhost:1234/v1)
    api_key: str = ""                        # bearer token for the OpenAI-compatible server
    workspace: Path = Path(".")              # the ONLY folder the agent may touch (default: here)
    state_dir: Path = Path(".localagent")    # session logs: <workspace>/.localagent/sessions
    num_ctx: int = 8192                      # history budget (tokens); Ollama's context window
    temperature: float = 0.0                 # 0 = greedy: the most repeatable code
    max_turns: int = 20                      # model calls allowed per user request
    max_fix_attempts: int = 3                # rejected writes allowed per file
    command_timeout: int = 30                # seconds before a run is killed
    test_timeout: int = 120                  # seconds before run_tests is killed (builds are slow)
    max_output_chars: int = 4000             # cap on any single tool result
    memory_chars: int = 2000                 # cap on AGENT.md text put in the prompt
    tree_entries: int = 50                   # cap on file-overview lines put in the prompt
    type_check: bool = False                 # also run mypy during verification
    auto_approve: bool = False               # skip the "may I run this?" prompt
    allow_host_shell: bool = False           # offer run_shell on this computer (asks every time)
    usage_file: Path = Path.home() / ".localagent" / "usage.json"   # quota counts, all projects
    compact_at: float = 0.6                  # summarise history above this share of the budget
    diff_lines: int = 40                     # lines of each edit's diff shown to the user
    auto_format: bool = True                 # run `ruff format` on every verified Python write
    max_nudges: int = 1                      # "you changed nothing" reminders per request
    reasoning_effort: str = ""               # sent to OpenAI-style servers when set ("low", ...)
    stream: bool = True                      # show OpenAI-style answers while they are written
    helpers: bool = True                     # offer the delegate tool (read-only helper agents)
    helper_turns: int = 8                    # model calls a helper may use
    request_timeout: int = 900               # seconds to wait for an answer (local CPUs are slow)
    # ---- online / offline ------------------------------------------------------------
    mode: str = "auto"                       # "auto": Gemini, local model when offline/out of quota
    #                                          "online": Gemini only; "offline": local model only
    local_ctx: int = 8192                    # context window of the local model (tokens)
    llama_server: str = ""                   # path of llama.cpp's llama-server ("" = look for it)
    model_file: str = ""                     # the local model, a .gguf file ("" = look for it)
    gpu_layers: int = -1                     # model layers on the GPU (-1 = as many as fit)
    local_threads: int = 0                   # CPU threads for the local model (0 = automatic)
    # ---- Gemini (hosted) -----------------------------------------------------------
    gemini_api_key: str = ""                 # from the GEMINI_API_KEY environment variable
    gemini_tier: str = "free"                # "free" or "paid" (billing on: higher limits)
    gemini_base: str = "https://generativelanguage.googleapis.com/v1beta/openai"
    gemini_models: tuple[str, ...] = ("gemini-3.5-flash-lite", "gemini-3.1-flash-lite")
    # ^ everyday chain, tried in order: fast "lite" models with large daily quotas
    gemini_strong_models: tuple[str, ...] = ("gemini-3.5-flash", "gemini-2.5-flash")
    # ^ reserve: small daily quota (~20 on a free key), used first only after an escalation
    gemini_timeout: int = 120                # seconds before a stalled Gemini request fails
    escalate_after: int = 2                  # failed tool results in one request before escalating
    rpm: int = 12                            # requests per minute allowed per model
    tpm: int = 250_000                       # tokens per minute allowed per model
    rpd: int = 1500                          # requests per day allowed per model
    gemini_limits: tuple[str, ...] = ()      # per-model overrides "model=rpm/rpd", e.g. "m=5/20"
    max_retries: int = 3                     # retries of one model on 429/5xx/network errors
    fallback_backend: str = ""               # "local", "openai" or "ollama": used when Gemini fails
    # ---- where code runs --------------------------------------------------------------
    sandbox: str = "local"                   # "local" (this machine) or "docker" (container)
    sandbox_image: str = "localagent-sandbox:latest"
    sandbox_network: bool = False            # allow internet inside the container (pip install)
    sandbox_memory: str = "1g"               # container memory cap
    sandbox_cpus: str = "1.0"                # container CPU cap

    @classmethod
    def from_env(cls) -> "Config":
        """Build a Config from defaults, then apply environment overrides."""
        base = cls()
        tier = os.environ.get("LOCALAGENT_GEMINI_TIER", base.gemini_tier).strip().lower()
        paid = tier == "paid"
        mode = os.environ.get("LOCALAGENT_MODE", base.mode).strip().lower()
        if mode not in MODES:
            raise ValueError(f"LOCALAGENT_MODE must be one of {', '.join(MODES)} (it is '{mode}')")
        return replace(
            base,
            backend=os.environ.get("LOCALAGENT_BACKEND", base.backend),
            model=os.environ.get("LOCALAGENT_MODEL", base.model),
            host=_normalize_host(os.environ.get("OLLAMA_HOST", base.host)),
            api_base=os.environ.get("LOCALAGENT_API_BASE", base.api_base).rstrip("/"),
            api_key=os.environ.get("LOCALAGENT_API_KEY", base.api_key),
            workspace=Path(os.environ.get("LOCALAGENT_WORKSPACE", str(base.workspace))),
            num_ctx=_int("LOCALAGENT_NUM_CTX", base.num_ctx),
            mode=mode,
            local_ctx=_int("LOCALAGENT_LOCAL_CTX", base.local_ctx),
            llama_server=os.environ.get("LOCALAGENT_LLAMA_SERVER", base.llama_server),
            model_file=os.environ.get("LOCALAGENT_MODEL_FILE", base.model_file),
            gpu_layers=_int("LOCALAGENT_GPU_LAYERS", base.gpu_layers),
            local_threads=_int("LOCALAGENT_THREADS", base.local_threads),
            gemini_api_key=os.environ.get("GEMINI_API_KEY", base.gemini_api_key).strip(),
            gemini_models=_split(os.environ.get("LOCALAGENT_GEMINI_MODELS", ""))
            or base.gemini_models,
            gemini_strong_models=_split(os.environ.get("LOCALAGENT_GEMINI_STRONG", ""))
            or base.gemini_strong_models,
            gemini_limits=_split(os.environ.get("LOCALAGENT_GEMINI_LIMITS", "")),
            gemini_tier=tier,
            rpm=_int("LOCALAGENT_RPM", PAID_RPM if paid else base.rpm),
            rpd=_int("LOCALAGENT_RPD", PAID_RPD if paid else base.rpd),
            tpm=_int("LOCALAGENT_TPM", PAID_TPM if paid else base.tpm),
            reasoning_effort=os.environ.get("LOCALAGENT_REASONING", base.reasoning_effort),
            fallback_backend=os.environ.get("LOCALAGENT_FALLBACK", base.fallback_backend),
            sandbox=os.environ.get("LOCALAGENT_SANDBOX", base.sandbox),
            usage_file=Path(os.environ.get("LOCALAGENT_USAGE_FILE", str(base.usage_file))),
        )


GEMINI_CONTEXT = 32_768   # history budget for Gemini (built for long prompts; 8K suits local models)


def overlay(config: Config, **given: Any) -> Config:
    """Replace only the settings that were actually given (``None`` = not given)."""
    return replace(config, **{key: value for key, value in given.items() if value is not None})


def gemini_history(config: Config, chosen: bool = False) -> Config:
    """Give Gemini the 32K history budget, unless a size was chosen (flag or environment)."""
    chosen = chosen or "LOCALAGENT_NUM_CTX" in os.environ
    if config.backend == "gemini" and not chosen and config.num_ctx < GEMINI_CONTEXT:
        return replace(config, num_ctx=GEMINI_CONTEXT)
    return config
