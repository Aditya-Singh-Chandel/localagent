"""Who can answer: online services you use with your own key, and model servers you run yourself.

Each one is a line in a table, so supporting another service means adding a line. They all speak
the "OpenAI-compatible" API (``/models`` lists the models, ``/chat/completions`` answers), which
nearly every service and local model server offers. Gemini gets extra care elsewhere (its free
quota and its chain of models, see ``backends.py``); here it is simply the first choice.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .fetch import DownloadError, get_json

GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta/openai"
CUSTOM_KEY = "LOCALAGENT_PROVIDER_KEY"              # the key variable of the "custom" entry
NOT_CHAT = ("embed", "whisper", "tts", "dall-e", "image", "audio", "transcribe", "realtime",
            "moderation", "rerank", "guard", "search", "ocr", "babbage", "davinci")
LOCAL_FAVOURITES = ("coder", "qwen3", "devstral", "gpt-oss", "qwen2.5", "llama3")


@dataclass(frozen=True)
class Provider:
    """An online service that answers OpenAI-style chat requests, tool calls included."""

    name: str                                  # what you type: --provider openai
    title: str                                 # what the setup menu shows
    base: str                                  # its API address ("" = you give it: "custom")
    key_env: str                               # the environment variable that holds your key
    key_page: str                              # where a key is made
    note: str                                  # its price, in a few words
    suggested: tuple[str, ...] = ()            # good models for this agent, best first
    key_header: str = ""                       # a second header that carries the key
    headers: tuple[tuple[str, str], ...] = ()  # fixed extra headers the service expects
    max_tokens: int = 0                        # answer length to ask for (0: the service decides)


PROVIDERS = (
    Provider("gemini", "Google Gemini", GEMINI_BASE, "GEMINI_API_KEY",
             "https://aistudio.google.com/apikey", "free daily quota, no card needed"),
    Provider("openai", "OpenAI", "https://api.openai.com/v1", "OPENAI_API_KEY",
             "https://platform.openai.com/api-keys", "pay per use",
             ("gpt-5-mini", "gpt-5", "gpt-4.1-mini", "gpt-4.1", "gpt-4o-mini")),
    Provider("anthropic", "Anthropic (Claude)", "https://api.anthropic.com/v1", "ANTHROPIC_API_KEY",
             "https://console.anthropic.com/settings/keys", "pay per use",
             ("claude-sonnet-5-5", "claude-haiku-5-5", "claude-opus-5-5", "claude-sonnet-4-5",
              "claude-haiku-4-5"),
             key_header="x-api-key", headers=(("anthropic-version", "2023-06-01"),), max_tokens=16384),
    Provider("openrouter", "OpenRouter", "https://openrouter.ai/api/v1", "OPENROUTER_API_KEY",
             "https://openrouter.ai/keys", "one key, hundreds of models, some free",
             ("qwen/qwen3-coder:free", "openai/gpt-oss-120b:free", "z-ai/glm-4.5-air:free",
              "deepseek/deepseek-chat-v3.1:free", "openai/gpt-5-mini", "anthropic/claude-sonnet-4.5")),
    Provider("groq", "Groq", "https://api.groq.com/openai/v1", "GROQ_API_KEY",
             "https://console.groq.com/keys", "free tier, very fast",
             ("openai/gpt-oss-120b", "moonshotai/kimi-k2-instruct-0905", "qwen/qwen3-32b",
              "llama-3.3-70b-versatile")),
    Provider("mistral", "Mistral", "https://api.mistral.ai/v1", "MISTRAL_API_KEY",
             "https://console.mistral.ai/api-keys", "free tier",
             ("devstral-medium-latest", "mistral-medium-latest", "devstral-small-latest",
              "mistral-small-latest")),
    Provider("deepseek", "DeepSeek", "https://api.deepseek.com/v1", "DEEPSEEK_API_KEY",
             "https://platform.deepseek.com/api_keys", "very low prices", ("deepseek-chat",)),
    Provider("custom", "another OpenAI-compatible service", "", CUSTOM_KEY, "",
             "any address you give"),
)


@dataclass(frozen=True)
class LocalServer:
    """A model server that people run on their own computer (no key needed)."""

    title: str                                 # "Ollama"
    base: str                                  # where it answers when installed normally
    kind: str                                  # "ollama" (its own API) or "openai"


LOCAL_SERVERS = (
    LocalServer("Ollama", "http://localhost:11434", "ollama"),
    LocalServer("LM Studio", "http://127.0.0.1:1234/v1", "openai"),
    LocalServer("llama.cpp", "http://127.0.0.1:8080/v1", "openai"),
    LocalServer("vLLM", "http://127.0.0.1:8000/v1", "openai"),
    LocalServer("Jan", "http://127.0.0.1:1337/v1", "openai"),
)


def find_provider(name: str) -> Provider | None:
    """The provider called ``name`` (any letter case), or None."""
    wanted = name.strip().lower()
    return next((provider for provider in PROVIDERS if provider.name == wanted), None)


def key_help(provider: Provider) -> str:
    """How to give the agent a key for ``provider`` (one sentence or two)."""
    where = f" (create one at {provider.key_page})" if provider.key_page else ""
    return (f"{provider.key_env} is not set{where}. The easiest way: localagent setup - it checks "
            f"the key and saves it; or set the variable yourself and open a NEW terminal.")


def key_headers(provider: Provider, key: str) -> dict[str, str]:
    """The HTTP headers that carry ``key``, plus any the service always expects."""
    headers = dict(provider.headers)
    if key:
        headers["Authorization"] = f"Bearer {key}"
        if provider.key_header:
            headers[provider.key_header] = key
    return headers


def list_models(base: str, headers: dict[str, str], timeout: float = 15) -> list[str]:
    """The model names a service or server offers (its ``/models`` list; costs no quota).
    Raises DownloadError when it cannot be read; the message names the HTTP status."""
    listed = get_json(base.rstrip("/") + "/models", headers, timeout=timeout)
    items = listed.get("data", []) if isinstance(listed, dict) else []
    return [str(item["id"]).removeprefix("models/") for item in items
            if isinstance(item, dict) and item.get("id")]


def check_key(base: str, headers: dict[str, str]) -> tuple[str, list[str], str]:
    """(state, models, detail) for a key: "ok" with the models offered, "rejected" (the service
    refused it), or "unreachable" (no answer, or not a model service at that address)."""
    try:
        return "ok", list_models(base, headers), ""
    except DownloadError as exc:
        detail = str(exc)
        refused = re.search(r"HTTP (400|401|403)\b", detail) is not None
        return ("rejected" if refused else "unreachable"), [], detail


def chat_models(offered: list[str]) -> list[str]:
    """``offered`` without the models that cannot chat (embeddings, speech, images...)."""
    return [name for name in offered if not any(word in name.lower() for word in NOT_CHAT)]


def pick_model(provider: Provider, offered: list[str]) -> str:
    """The model to suggest: the first of ``provider.suggested`` that is offered - exactly, or as
    a dated version such as ``claude-sonnet-4-5-20250929`` - else the first chat model offered."""
    for wanted in provider.suggested:
        dated = re.compile(re.escape(wanted) + r"(-\d{8})?")
        for name in offered:
            if dated.fullmatch(name):
                return name
    chat = chat_models(offered)
    return chat[0] if chat else ""


def pick_local_model(offered: list[str]) -> str:
    """The local model to suggest: the first that is known to use tools well, else the first."""
    chat = chat_models(offered) or offered
    for word in LOCAL_FAVOURITES:
        for name in chat:
            if word in name.lower():
                return name
    return chat[0] if chat else ""


def server_models(server: LocalServer, timeout: float = 1.0) -> list[str] | None:
    """The models a local server offers, or None when nothing answers at its address."""
    try:
        if server.kind == "ollama":
            listed = get_json(server.base.rstrip("/") + "/api/tags", timeout=timeout)
            items = listed.get("models", []) if isinstance(listed, dict) else []
            return [str(item["name"]) for item in items if isinstance(item, dict) and item.get("name")]
        return list_models(server.base, {}, timeout)
    except DownloadError:
        return None


def scan_local_servers(extra: tuple[LocalServer, ...] = (),
                       timeout: float = 1.0) -> list[tuple[LocalServer, list[str]]]:
    """The local model servers that answer right now, each with the models it offers. ``extra``
    (your own addresses) are tried first; an address is only tried once."""
    found: list[tuple[LocalServer, list[str]]] = []
    tried: set[str] = set()
    for server in extra + LOCAL_SERVERS:
        address = server.base.rstrip("/").lower()
        if address in tried:
            continue
        tried.add(address)
        models = server_models(server, timeout)
        if models is not None:
            found.append((server, models))
    return found
