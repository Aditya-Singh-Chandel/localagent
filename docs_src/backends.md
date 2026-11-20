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
@@ localagent/backends.py header @@
- Imports `sys` (to print notices on the error stream), `replace` (to copy a config with changed fields), the
  `ChatBackend` interface, the concrete clients, `reachable` (the internet check), `LocalModel`, `shared_limiter`, and
  the chain classes `Entry`/`FallbackBackend`.
- `GEMINI_KEY_HELP` is the message shown when the key is missing: where to get one and how to set it.
- `LOCAL_KINDS` are the accepted values of `fallback_backend`.

## `_notice`
@@ localagent/backends.py _notice @@
- Prints retry/fallback news like `[gemini-3.5-flash busy (503); retrying in 2s]` to *stderr*, so it never mixes with the
  model's streamed answer on *stdout*. `flush=True` shows it immediately. The local model and the rate limiters report
  through it too ("starting the local model ...", "waiting 41s for the per-minute limit").

## `model_limits`
@@ localagent/backends.py model_limits @@
- Looks for a per-model override such as `gemini-2.5-flash=5/20` in `config.gemini_limits` (from the
  `LOCALAGENT_GEMINI_LIMITS` environment variable) and returns `(requests per minute, requests per day)`; otherwise the
  general `rpm`/`rpd`. A malformed entry raises a `ValueError` that shows the correct format.

## `make_local`
@@ localagent/backends.py make_local @@
- The `local` backend: a `LocalModel` that finds a running local server or starts llama.cpp (see `localserver.md`),
  reporting through `_notice`.

## `make_gemini`
@@ localagent/backends.py make_gemini @@
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
@@ localagent/backends.py after_make_gemini @@
@@ localagent/backends.py make_backend @@
- `BACKENDS` maps each name to something callable with a `Config`: the two client classes and the `make_gemini` /
  `make_local` functions. `make_backend` looks the name up and calls it.
- An unknown name raises `KeyError`; it is converted to a `ValueError` that lists the valid names (`from None` hides the
  confusing original `KeyError` from the traceback).
