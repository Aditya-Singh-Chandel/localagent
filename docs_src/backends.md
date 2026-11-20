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
@@ localagent/backends.py header @@
- Imports `sys` (to print notices on the error stream), `replace` (to copy a config with changed fields), the
  `ChatBackend` interface, both concrete clients, `RateLimiter`, and the chain classes `Entry`/`FallbackBackend`.
- `GEMINI_KEY_HELP` is the message shown when the key is missing: where to get one and how to set it.

## `_notice`
@@ localagent/backends.py _notice @@
- Prints retry/fallback news like `[gemini-3.5-flash busy (503); retrying in 2s]` to *stderr*, so it never mixes with the
  model's streamed answer on *stdout*. `flush=True` shows it immediately.

## `model_limits`
@@ localagent/backends.py model_limits @@
- Looks for a per-model override such as `gemini-2.5-flash=5/20` in `config.gemini_limits` (from the
  `LOCALAGENT_GEMINI_LIMITS` environment variable) and returns `(requests per minute, requests per day)`; otherwise the
  general `rpm`/`rpd`. A malformed entry raises a `ValueError` that shows the correct format.

## `make_gemini`
@@ localagent/backends.py make_gemini @@
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
@@ localagent/backends.py after_make_gemini @@
@@ localagent/backends.py make_backend @@
- `BACKENDS` maps each name to something callable with a `Config`: the two client classes and the `make_gemini`
  function. `make_backend` looks the name up and calls it.
- An unknown name raises `KeyError`; it is converted to a `ValueError` that lists the valid names (`from None` hides the
  confusing original `KeyError` from the traceback).
