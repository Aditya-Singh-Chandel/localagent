# `localagent/backends.py` — choosing the model client

**Role in the flow:** one lookup table from a short name to a function that builds a client. The CLI, the server and the
eval runner call `make_backend(config)`; nothing else needs to know which provider is in use. Which name is used is
normally decided by the **mode** (`cli.choose_mode`): `online` → `gemini`; `offline` → `local`; `auto` → `gemini` with
the local model as its last link, or `local` when there is no key.

| Name | Builds | Model runs |
|---|---|---|
| `local` | `LocalModel` (`localserver.py`) | whichever local server runs (llama.cpp, LM Studio, Ollama), or llama.cpp started for you |
| `gemini` | `FallbackBackend` (`resilient.py`) over several Gemini models, optionally ending with the local model | Google's servers (free API key) |
| `ollama` | `OllamaClient` (`llm.py`) | locally in the Ollama app (chosen by hand with `--backend`) |
| `openai` | `OpenAICompatClient` (`openai_compat.py`) | any OpenAI-style server you name (chosen by hand) |

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
13 | from .llm import ChatBackend, OllamaClient, reachable
14 | from .localserver import LocalModel
15 | from .openai_compat import OpenAICompatClient
16 | from .ratelimit import shared_limiter
17 | from .resilient import Entry, FallbackBackend
18 | 
19 | GEMINI_KEY_HELP = (
20 |     "GEMINI_API_KEY is not set. Create a free key at https://aistudio.google.com/apikey, "
21 |     "then run:  setx GEMINI_API_KEY \"your-key\"  and open a NEW terminal.")
22 | LOCAL_KINDS = ("local", "openai", "ollama")
23 | 
24 | 
```
- Imports `sys` (to print notices on the error stream), `replace` (to copy a config with changed fields), the
  `ChatBackend` interface, the concrete clients, `reachable` (the internet check), `LocalModel`, `shared_limiter`, and
  the chain classes `Entry`/`FallbackBackend`.
- `GEMINI_KEY_HELP` is the message shown when the key is missing: where to get one and how to set it.
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
44 |     """The local model: a running llama.cpp / LM Studio / Ollama server, or llama.cpp started."""
45 |     return LocalModel(config, on_notice=_notice)
46 | 
47 | 
```
- The `local` backend: a `LocalModel` that finds a running local server or starts llama.cpp (see `localserver.md`),
  reporting through `_notice`.

## `make_gemini`
```python
48 | def make_gemini(config: Config) -> ChatBackend:
49 |     """Gemini models in order (each rate-limited), then an optional local fallback."""
50 |     if not config.gemini_api_key:
51 |         raise ValueError(GEMINI_KEY_HELP)
52 |     usage = config.usage_file
53 | 
54 |     def entry(model: str) -> Entry:
55 |         client_config = replace(config, api_base=config.gemini_base, api_key=config.gemini_api_key,
56 |                                 model=model, request_timeout=config.gemini_timeout)
57 |         rpm, rpd = model_limits(config, model)
58 |         return Entry(model, OpenAICompatClient(client_config),
59 |                      shared_limiter(model, rpm, config.tpm, rpd, usage, on_wait=_notice))
60 | 
61 |     cheap = [entry(model) for model in config.gemini_models]
62 |     strong = [entry(model) for model in config.gemini_strong_models
63 |               if model not in config.gemini_models]
64 |     if config.gemini_tier == "paid":                 # billing on: best quality first, no reserve
65 |         entries, strong = strong + cheap, []
66 |     else:                                            # free: cheap first, strong kept in reserve
67 |         entries = cheap
68 |     if config.fallback_backend:
69 |         if config.fallback_backend not in LOCAL_KINDS:
70 |             raise ValueError("fallback_backend must be 'local', 'openai' or 'ollama'")
71 |         kind = "" if config.fallback_backend == "local" else config.fallback_backend
72 |         name = "local" if not kind else f"{kind}:{config.model}"
73 |         entries.append(Entry(name, LocalModel(config, kind, on_notice=_notice)))
74 |     return FallbackBackend(entries, config.max_retries, on_notice=_notice, strong=strong,
75 |                            probe=lambda: reachable(config.gemini_base))
76 | 
77 | 
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
- If `fallback_backend` is set, a last entry is added: always a `LocalModel`, so the conversation is fitted into the
  local context window before it is sent (a history built for Gemini's 32K budget would not fit 8K) and Ollama is asked
  for `local_ctx` rather than Gemini's 32K (which would have taken gigabytes of extra memory). `local` uses whatever is
  available, starting llama.cpp when needed; `openai`/`ollama` insist on that kind of server. It has no limiter (local
  models have no quota) — that is also how the chain knows it is not hosted.
- Returns the chain wrapped in `FallbackBackend` with `max_retries`, the notice printer and the **probe**: a quick
  connection check to Gemini's address, used to notice that the internet is gone or back (see `resilient.md`).

## The table and `make_backend`
```python
78 | BACKENDS: dict[str, Callable[[Config], ChatBackend]] = {
79 |     "ollama": OllamaClient,
80 |     "openai": OpenAICompatClient,
81 |     "gemini": make_gemini,
82 |     "local": make_local,
83 | }
84 | 
85 | 
```
```python
86 | def make_backend(config: Config) -> ChatBackend:
87 |     """Build the client named by ``config.backend`` or explain the valid names."""
88 |     try:
89 |         return BACKENDS[config.backend](config)
90 |     except KeyError:
91 |         raise ValueError(
92 |             f"unknown backend '{config.backend}'. Choose one of: {', '.join(BACKENDS)}"
93 |         ) from None
```
- `BACKENDS` maps each name to something callable with a `Config`: the two client classes and the `make_gemini` /
  `make_local` functions. `make_backend` looks the name up and calls it.
- An unknown name raises `KeyError`; it is converted to a `ValueError` that lists the valid names (`from None` hides the
  confusing original `KeyError` from the traceback).
