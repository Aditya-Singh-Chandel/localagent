"""Backend registry: choose the model client by name.

Adding a new kind of model client means writing one class with a ``chat`` method and adding one
line to ``BACKENDS``; a new online service that speaks the OpenAI API needs only a line in
``providers.py``.
"""
from __future__ import annotations

import sys
from dataclasses import replace
from typing import Callable

from .config import Config, online_problem
from .llm import ChatBackend, OllamaClient, reachable
from .localserver import LocalModel
from .openai_compat import OpenAICompatClient
from .providers import PROVIDERS, find_provider, key_help
from .ratelimit import shared_limiter
from .resilient import Entry, FallbackBackend

GEMINI_KEY_HELP = key_help(PROVIDERS[0])
LOCAL_KINDS = ("local", "openai", "ollama")


def _notice(message: str) -> None:
    """Show retry/fallback news without mixing it into the model's answer."""
    print(f"[{message}]", file=sys.stderr, flush=True)


def model_limits(config: Config, model: str) -> tuple[int, int]:
    """(requests per minute, requests per day) for a model: its override, else the defaults."""
    for item in config.gemini_limits:
        name, _, numbers = item.partition("=")
        if name.strip() == model:
            rpm, _, rpd = numbers.partition("/")
            try:
                return int(rpm), int(rpd)
            except ValueError:
                raise ValueError(f"bad limit '{item}': write it as model=rpm/rpd, e.g. {model}=5/20") from None
    return config.rpm, config.rpd


def make_local(config: Config) -> ChatBackend:
    """The local model: a running llama.cpp / LM Studio / Ollama server, or llama.cpp started
    (``local_server`` may limit the choice to one kind)."""
    return LocalModel(config, config.local_server, on_notice=_notice)


def add_backup(config: Config, entries: list[Entry]) -> None:
    """Append auto mode's local backup model (``fallback_backend``) to a chain of online models."""
    if not config.fallback_backend:
        return
    if config.fallback_backend not in LOCAL_KINDS:
        raise ValueError("fallback_backend must be 'local', 'openai' or 'ollama'")
    kind = "" if config.fallback_backend == "local" else config.fallback_backend
    name = "local" if not kind else f"{kind}:{config.model}"
    entries.append(Entry(name, LocalModel(config, kind, on_notice=_notice)))


def make_gemini(config: Config) -> ChatBackend:
    """Gemini models in order (each rate-limited), then an optional local fallback."""
    if not config.gemini_api_key:
        raise ValueError(GEMINI_KEY_HELP)
    usage = config.usage_file

    def entry(model: str) -> Entry:
        client_config = replace(config, api_base=config.gemini_base, api_key=config.gemini_api_key,
                                model=model, request_timeout=config.gemini_timeout)
        rpm, rpd = model_limits(config, model)
        return Entry(model, OpenAICompatClient(client_config),
                     shared_limiter(model, rpm, config.tpm, rpd, usage, on_wait=_notice))

    cheap = [entry(model) for model in config.gemini_models]
    strong = [entry(model) for model in config.gemini_strong_models
              if model not in config.gemini_models]
    if config.gemini_tier == "paid":                 # billing on: best quality first, no reserve
        entries, strong = strong + cheap, []
    else:                                            # free: cheap first, strong kept in reserve
        entries = cheap
    add_backup(config, entries)
    return FallbackBackend(entries, config.max_retries, on_notice=_notice, strong=strong,
                           probe=lambda: reachable(config.gemini_base))


def make_hosted(config: Config) -> ChatBackend:
    """Another online service (OpenAI, Anthropic, OpenRouter...): its model, then an optional
    local backup. Paid services have no free quota to share out, so there is no rate limiter;
    a "slow down" answer is still waited out and retried."""
    problem = online_problem(config)
    if problem:
        raise ValueError(problem)
    provider = find_provider(config.provider) or PROVIDERS[-1]
    client_config = replace(config, api_base=config.provider_base, api_key=config.provider_key,
                            model=config.provider_model, request_timeout=config.gemini_timeout)
    entries = [Entry(f"{provider.name}:{config.provider_model}",
                     OpenAICompatClient(client_config, provider))]
    add_backup(config, entries)
    return FallbackBackend(entries, config.max_retries, on_notice=_notice,
                           probe=lambda: reachable(config.provider_base))


BACKENDS: dict[str, Callable[[Config], ChatBackend]] = {
    "ollama": OllamaClient,
    "openai": OpenAICompatClient,
    "gemini": make_gemini,
    "hosted": make_hosted,
    "local": make_local,
}


def make_backend(config: Config) -> ChatBackend:
    """Build the client named by ``config.backend`` or explain the valid names."""
    try:
        return BACKENDS[config.backend](config)
    except KeyError:
        raise ValueError(
            f"unknown backend '{config.backend}'. Choose one of: {', '.join(BACKENDS)}"
        ) from None
