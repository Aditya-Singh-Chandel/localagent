# `localagent/backends.py` — choosing the model client

**Role in the flow:** one lookup table from a short name to a function that builds a client. The CLI, the server and the
eval runner call `make_backend(config)`; nothing else needs to know which service is in use. Which name is used is
normally decided by the **mode** (`cli.choose_mode`): `online` → `gemini` (or `hosted`, when another online service was
chosen in `localagent setup`); `offline` → `local`; `auto` → the online one with the local model as its last link, or
`local` when no online model is set up.

| Name | Builds | Model runs |
|---|---|---|
| `local` | `LocalModel` (`localserver.py`) | whichever local server runs (llama.cpp, LM Studio, Ollama), or llama.cpp started for you |
| `gemini` | `FallbackBackend` (`resilient.py`) over several Gemini models, optionally ending with the local model | Google's servers (free API key) |
| `hosted` | `FallbackBackend` over one model of another online service (OpenAI, Anthropic, OpenRouter, Groq, Mistral, DeepSeek, any OpenAI-compatible address), optionally ending with the local model | that service's servers (your key) |
| `ollama` | `OllamaClient` (`llm.py`) | locally in the Ollama app (chosen by hand with `--backend`) |
| `openai` | `OpenAICompatClient` (`openai_compat.py`) | any OpenAI-style server you name (chosen by hand) |

To add a new *kind* of client, write one class with a `chat` method and add one line to `BACKENDS`. A new online
service that speaks the OpenAI API needs no code at all here: one line in `providers.py`'s table.

## Module header
```python
  1 | """Backend registry: choose the model client by name.
  2 | 
  3 | Adding a new kind of model client means writing one class with a ``chat`` method and adding one
  4 | line to ``BACKENDS``; a new online service that speaks the OpenAI API needs only a line in
  5 | ``providers.py``.
  6 | """
  7 | from __future__ import annotations
  8 | 
  9 | import sys
 10 | from dataclasses import replace
 11 | from typing import Callable
 12 | 
 13 | from .config import Config, online_problem
 14 | from .llm import ChatBackend, OllamaClient, reachable
 15 | from .localserver import LocalModel
 16 | from .openai_compat import OpenAICompatClient
 17 | from .providers import PROVIDERS, find_provider, key_help
 18 | from .ratelimit import shared_limiter
 19 | from .resilient import Entry, FallbackBackend
 20 | 
 21 | GEMINI_KEY_HELP = key_help(PROVIDERS[0])
 22 | LOCAL_KINDS = ("local", "openai", "ollama")
 23 | 
 24 | 
```
- Imports `sys` (to print notices on the error stream), `replace` (to copy a config with changed fields), the
  `ChatBackend` interface, the concrete clients, `reachable` (the internet check), `online_problem` (what an online
  model still lacks), `LocalModel`, the provider table helpers, `shared_limiter`, and the chain classes
  `Entry`/`FallbackBackend`.
- `GEMINI_KEY_HELP` is the message shown when the Gemini key is missing: `key_help` of the table's first entry (Gemini)
  says where to make a key and that `localagent setup` checks and saves it.
- `LOCAL_KINDS` are the accepted values of `fallback_backend`.

## `_notice`
```python
 25 | def _notice(message: str) -> None:
 26 |     """Show retry/fallback news without mixing it into the model's answer."""
 27 |     print(f"[{message}]", file=sys.stderr, flush=True)
 28 | 
 29 | 
```
- Prints retry/fallback news like `[gemini-3.5-flash busy (503); retrying in 2s]` to *stderr*, so it never mixes with the
  model's streamed answer on *stdout*. `flush=True` shows it immediately. The local model and the rate limiters report
  through it too ("starting the local model ...", "waiting 41s for the per-minute limit").

## `model_limits`
```python
 30 | def model_limits(config: Config, model: str) -> tuple[int, int]:
 31 |     """(requests per minute, requests per day) for a model: its override, else the defaults."""
 32 |     for item in config.gemini_limits:
 33 |         name, _, numbers = item.partition("=")
 34 |         if name.strip() == model:
 35 |             rpm, _, rpd = numbers.partition("/")
 36 |             try:
 37 |                 return int(rpm), int(rpd)
 38 |             except ValueError:
 39 |                 raise ValueError(f"bad limit '{item}': write it as model=rpm/rpd, e.g. {model}=5/20") from None
 40 |     return config.rpm, config.rpd
 41 | 
 42 | 
```
- Looks for a per-model override such as `gemini-2.5-flash=5/20` in `config.gemini_limits` (from the
  `LOCALAGENT_GEMINI_LIMITS` environment variable) and returns `(requests per minute, requests per day)`; otherwise the
  general `rpm`/`rpd`. A malformed entry raises a `ValueError` that shows the correct format.

## `make_local`
```python
 43 | def make_local(config: Config) -> ChatBackend:
 44 |     """The local model: a running llama.cpp / LM Studio / Ollama server, or llama.cpp started
 45 |     (``local_server`` may limit the choice to one kind)."""
 46 |     return LocalModel(config, config.local_server, on_notice=_notice)
 47 | 
 48 | 
```
- The `local` backend: a `LocalModel` that finds a running local server or starts llama.cpp (see `localserver.md`),
  reporting through `_notice`. `config.local_server` is passed as its kind: empty means "whichever answers"; `ollama`
  or `openai` — written by `localagent setup` when you pick one — means only that kind of server counts, so a choice
  you made is never silently replaced by another model that happens to be running.

## `add_backup`
```python
 49 | def add_backup(config: Config, entries: list[Entry]) -> None:
 50 |     """Append auto mode's local backup model (``fallback_backend``) to a chain of online models."""
 51 |     if not config.fallback_backend:
 52 |         return
 53 |     if config.fallback_backend not in LOCAL_KINDS:
 54 |         raise ValueError("fallback_backend must be 'local', 'openai' or 'ollama'")
 55 |     kind = "" if config.fallback_backend == "local" else config.fallback_backend
 56 |     name = "local" if not kind else f"{kind}:{config.model}"
 57 |     entries.append(Entry(name, LocalModel(config, kind, on_notice=_notice)))
 58 | 
 59 | 
```
- Shared by both online chains. Without a `fallback_backend` there is nothing to add. Otherwise the value must be one of
  `LOCAL_KINDS`, and a last entry is appended: always a `LocalModel`, so the conversation is fitted into the local
  context window before it is sent (a history built for an online model's 32K budget would not fit 8K), and Ollama is
  asked for `local_ctx` rather than 32K (which would take gigabytes of extra memory). `local` uses whatever is
  available, starting llama.cpp when needed; `openai`/`ollama` insist on that kind of server. Its display name is
  `local`, or `kind:model` for a chosen kind. It has no limiter (local models have no quota).

## `make_gemini`
```python
 60 | def make_gemini(config: Config) -> ChatBackend:
 61 |     """Gemini models in order (each rate-limited), then an optional local fallback."""
 62 |     if not config.gemini_api_key:
 63 |         raise ValueError(GEMINI_KEY_HELP)
 64 |     usage = config.usage_file
 65 | 
 66 |     def entry(model: str) -> Entry:
 67 |         client_config = replace(config, api_base=config.gemini_base, api_key=config.gemini_api_key,
 68 |                                 model=model, request_timeout=config.gemini_timeout)
 69 |         rpm, rpd = model_limits(config, model)
 70 |         return Entry(model, OpenAICompatClient(client_config),
 71 |                      shared_limiter(model, rpm, config.tpm, rpd, usage, on_wait=_notice))
 72 | 
 73 |     cheap = [entry(model) for model in config.gemini_models]
 74 |     strong = [entry(model) for model in config.gemini_strong_models
 75 |               if model not in config.gemini_models]
 76 |     if config.gemini_tier == "paid":                 # billing on: best quality first, no reserve
 77 |         entries, strong = strong + cheap, []
 78 |     else:                                            # free: cheap first, strong kept in reserve
 79 |         entries = cheap
 80 |     add_backup(config, entries)
 81 |     return FallbackBackend(entries, config.max_retries, on_notice=_notice, strong=strong,
 82 |                            probe=lambda: reachable(config.gemini_base))
 83 | 
 84 | 
```
- No key → `ValueError` with `GEMINI_KEY_HELP` (the CLI prints it and exits cleanly).
- For each model in `gemini_models` (in order) it makes a copy of the config pointing at Google's OpenAI-compatible
  address (`gemini_base`) with your key, that model name and the shorter `gemini_timeout`. The ordinary
  `OpenAICompatClient` is reused: Gemini understands the same request format, so no Gemini-specific HTTP code was
  needed. Each model gets **the shared limiter** for its name and limits (`shared_limiter`), so all sessions of the HTTP
  server count against one per-minute window; all limiters share one usage file, `%USERPROFILE%\.localagent\usage.json`.
- **Tier:** on the free tier the lite models form the chain and the strong models are the reserve used after an
  escalation; on the paid tier (`LOCALAGENT_GEMINI_TIER=paid`) the strong models come first and there is no reserve —
  with billing on, quality is worth more than saving requests.
- `add_backup` appends the local model of auto mode.
- Returns the chain wrapped in `FallbackBackend` with `max_retries`, the notice printer and the **probe**: a quick
  connection check to Gemini's address, used to notice that the internet is gone or back (see `resilient.md`).

## `make_hosted`
```python
 85 | def make_hosted(config: Config) -> ChatBackend:
 86 |     """Another online service (OpenAI, Anthropic, OpenRouter...): its model, then an optional
 87 |     local backup. Paid services have no free quota to share out, so there is no rate limiter;
 88 |     a "slow down" answer is still waited out and retried."""
 89 |     problem = online_problem(config)
 90 |     if problem:
 91 |         raise ValueError(problem)
 92 |     provider = find_provider(config.provider) or PROVIDERS[-1]
 93 |     client_config = replace(config, api_base=config.provider_base, api_key=config.provider_key,
 94 |                             model=config.provider_model, request_timeout=config.gemini_timeout)
 95 |     entries = [Entry(f"{provider.name}:{config.provider_model}",
 96 |                      OpenAICompatClient(client_config, provider))]
 97 |     add_backup(config, entries)
 98 |     return FallbackBackend(entries, config.max_retries, on_notice=_notice,
 99 |                            probe=lambda: reachable(config.provider_base))
100 | 
101 | 
```
- Any other online service. First `online_problem` must have nothing to say: a missing key, model or (for `custom`)
  address stops here with a `ValueError` that says how to fix it — `localagent setup` in every case.
- The client gets a copy of the config pointing at the service (`provider_base`, `provider_key`, `provider_model`, and
  `gemini_timeout`, which is the timeout of every online request), plus the service's `Provider` entry, so it sends
  the headers and request fields that service expects (see `openai_compat.md`).
- One entry named like `openai:gpt-5-mini`, then the local backup. There is no rate limiter: paid services have no
  free daily quota that has to be shared out, and when one does answer "slow down" (HTTP 429), the chain still waits
  and retries (`max_retries`).
- The probe checks the service's own address, so going offline is noticed for it exactly as for Gemini.

## The table and `make_backend`
```python
102 | BACKENDS: dict[str, Callable[[Config], ChatBackend]] = {
103 |     "ollama": OllamaClient,
104 |     "openai": OpenAICompatClient,
105 |     "gemini": make_gemini,
106 |     "hosted": make_hosted,
107 |     "local": make_local,
108 | }
109 | 
110 | 
```
```python
111 | def make_backend(config: Config) -> ChatBackend:
112 |     """Build the client named by ``config.backend`` or explain the valid names."""
113 |     try:
114 |         return BACKENDS[config.backend](config)
115 |     except KeyError:
116 |         raise ValueError(
117 |             f"unknown backend '{config.backend}'. Choose one of: {', '.join(BACKENDS)}"
118 |         ) from None
```
- `BACKENDS` maps each name to something callable with a `Config`: the two client classes and the `make_gemini` /
  `make_hosted` / `make_local` functions. `make_backend` looks the name up and calls it.
- An unknown name raises `KeyError`; it is converted to a `ValueError` that lists the valid names (`from None` hides the
  confusing original `KeyError` from the traceback).
