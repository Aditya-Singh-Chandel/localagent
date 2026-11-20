# `localagent/backends.py` — choosing the model client

**Role in the flow:** one lookup table from a short name to a function that builds a client. The CLI, the server and the
eval runner call `make_backend(config)`; nothing else needs to know which provider is in use.

| Name | Builds | Model runs |
|---|---|---|
| `ollama` | `OllamaClient` (`llm.py`) | locally in the Ollama app |
| `openai` | `OpenAICompatClient` (`openai_compat.py`) | any OpenAI-style server, e.g. llama.cpp started by `serve.ps1` |
| `gemini` | `FallbackBackend` (`resilient.py`) over several Gemini models | Google's servers (free API key) |

To add a provider, write one class with a `chat` method and add one line to `BACKENDS`.

## Module header
```python
 1 | """Backend registry: choose the model client by name.
 2 | 
 3 | Adding a new provider means writing one class with a ``chat`` method and adding
 4 | one line to ``BACKENDS``; nothing else in the program changes.
 5 | """
 6 | from __future__ import annotations
 7 | 
 8 | import sys
 9 | from dataclasses import replace
10 | from typing import Callable
11 | 
12 | from .config import Config
13 | from .llm import ChatBackend, OllamaClient
14 | from .openai_compat import OpenAICompatClient
15 | from .ratelimit import RateLimiter
16 | from .resilient import Entry, FallbackBackend
17 | 
18 | GEMINI_KEY_HELP = (
19 |     "GEMINI_API_KEY is not set. Create a free key at https://aistudio.google.com/apikey, "
20 |     "then run:  setx GEMINI_API_KEY \"your-key\"  and open a NEW terminal.")
21 | 
22 | 
```
- Imports `sys` (to print notices on the error stream), `replace` (to copy a config with changed fields), the
  `ChatBackend` interface, both concrete clients, `RateLimiter`, and the chain classes `Entry`/`FallbackBackend`.
- `GEMINI_KEY_HELP` is the message shown when the key is missing: where to get one and how to set it.

## `_notice`
```python
23 | def _notice(message: str) -> None:
24 |     """Show retry/fallback news without mixing it into the model's answer."""
25 |     print(f"[{message}]", file=sys.stderr, flush=True)
26 | 
27 | 
```
- Prints retry/fallback news like `[gemini-3.5-flash busy (503); retrying in 2s]` to *stderr*, so it never mixes with the
  model's streamed answer on *stdout*. `flush=True` shows it immediately.

## `model_limits`
```python
28 | def model_limits(config: Config, model: str) -> tuple[int, int]:
29 |     """(requests per minute, requests per day) for a model: its override, else the defaults."""
30 |     for item in config.gemini_limits:
31 |         name, _, numbers = item.partition("=")
32 |         if name.strip() == model:
33 |             rpm, _, rpd = numbers.partition("/")
34 |             try:
35 |                 return int(rpm), int(rpd)
36 |             except ValueError:
37 |                 raise ValueError(f"bad limit '{item}': write it as model=rpm/rpd, e.g. {model}=5/20") from None
38 |     return config.rpm, config.rpd
39 | 
40 | 
```
- Looks for a per-model override such as `gemini-2.5-flash=5/20` in `config.gemini_limits` (from the
  `LOCALAGENT_GEMINI_LIMITS` environment variable) and returns `(requests per minute, requests per day)`; otherwise the
  general `rpm`/`rpd`. A malformed entry raises a `ValueError` that shows the correct format.

## `make_gemini`
```python
41 | def make_gemini(config: Config) -> ChatBackend:
42 |     """Gemini models in order (each rate-limited), then an optional local fallback."""
43 |     if not config.gemini_api_key:
44 |         raise ValueError(GEMINI_KEY_HELP)
45 |     usage = config.usage_file
46 | 
47 |     def entry(model: str) -> Entry:
48 |         client_config = replace(config, api_base=config.gemini_base,
49 |                                 api_key=config.gemini_api_key, model=model)
50 |         rpm, rpd = model_limits(config, model)
51 |         return Entry(model, OpenAICompatClient(client_config),
52 |                      RateLimiter(model, rpm, config.tpm, rpd, usage))
53 | 
54 |     cheap = [entry(model) for model in config.gemini_models]
55 |     strong = [entry(model) for model in config.gemini_strong_models
56 |               if model not in config.gemini_models]
57 |     if config.gemini_tier == "paid":                 # billing on: best quality first, no reserve
58 |         entries, strong = strong + cheap, []
59 |     else:                                            # free: cheap first, strong kept in reserve
60 |         entries = cheap
61 |     if config.fallback_backend:
62 |         if config.fallback_backend not in ("ollama", "openai"):
63 |             raise ValueError("fallback_backend must be 'ollama' or 'openai'")
64 |         local = BACKENDS[config.fallback_backend](config)
65 |         entries.append(Entry(f"{config.fallback_backend}:{config.model}", local))
66 |     return FallbackBackend(entries, config.max_retries, on_notice=_notice, strong=strong)
67 | 
68 | 
```
- No key → `ValueError` with `GEMINI_KEY_HELP` (the CLI prints it and exits cleanly).
- All limiters share one usage file, `.localagent/usage.json`.
- For each model in `gemini_models` (in order) it makes a copy of the config pointing at Google's OpenAI-compatible
  address (`gemini_base`) with your key and that model name. The ordinary `OpenAICompatClient` is reused: Gemini
  understands the same request format, so no Gemini-specific HTTP code was needed. Each model gets its own `RateLimiter` with its own limits (`model_limits`).
- **Tier:** on the free tier the lite models form the chain and the strong models are the reserve used after an
  escalation; on the paid tier (`LOCALAGENT_GEMINI_TIER=paid`) the strong models come first and there is no reserve —
  with billing on, quality is worth more than saving requests.
- If `fallback_backend` is set (`--fallback openai` or `ollama`), a last entry is added that uses your local server with
  `config.model` / `config.api_base` / `config.host`. It has no limiter (local models have no quota).
- Returns the chain wrapped in `FallbackBackend` with `max_retries` and the notice printer.

## The table and `make_backend`
```python
69 | BACKENDS: dict[str, Callable[[Config], ChatBackend]] = {
70 |     "ollama": OllamaClient,
71 |     "openai": OpenAICompatClient,
72 |     "gemini": make_gemini,
73 | }
74 | 
75 | 
```
```python
76 | def make_backend(config: Config) -> ChatBackend:
77 |     """Build the client named by ``config.backend`` or explain the valid names."""
78 |     try:
79 |         return BACKENDS[config.backend](config)
80 |     except KeyError:
81 |         raise ValueError(
82 |             f"unknown backend '{config.backend}'. Choose one of: {', '.join(BACKENDS)}"
83 |         ) from None
```
- `BACKENDS` maps each name to something callable with a `Config`: the two client classes and the `make_gemini`
  function. `make_backend` looks the name up and calls it.
- An unknown name raises `KeyError`; it is converted to a `ValueError` that lists the valid names (`from None` hides the
  confusing original `KeyError` from the traceback).
