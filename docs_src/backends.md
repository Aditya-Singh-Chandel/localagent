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
@@ localagent/backends.py header @@
- Imports `sys` (to print notices on the error stream), `replace` (to copy a config with changed fields), the
  `ChatBackend` interface, the concrete clients, `reachable` (the internet check), `online_problem` (what an online
  model still lacks), `LocalModel`, the provider table helpers, `shared_limiter`, and the chain classes
  `Entry`/`FallbackBackend`.
- `GEMINI_KEY_HELP` is the message shown when the Gemini key is missing: `key_help` of the table's first entry (Gemini)
  says where to make a key and that `localagent setup` checks and saves it.
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
  reporting through `_notice`. `config.local_server` is passed as its kind: empty means "whichever answers"; `ollama`
  or `openai` — written by `localagent setup` when you pick one — means only that kind of server counts, so a choice
  you made is never silently replaced by another model that happens to be running.

## `add_backup`
@@ localagent/backends.py add_backup @@
- Shared by both online chains. Without a `fallback_backend` there is nothing to add. Otherwise the value must be one of
  `LOCAL_KINDS`, and a last entry is appended: always a `LocalModel`, so the conversation is fitted into the local
  context window before it is sent (a history built for an online model's 32K budget would not fit 8K), and Ollama is
  asked for `local_ctx` rather than 32K (which would take gigabytes of extra memory). `local` uses whatever is
  available, starting llama.cpp when needed; `openai`/`ollama` insist on that kind of server. Its display name is
  `local`, or `kind:model` for a chosen kind. It has no limiter (local models have no quota).

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
- `add_backup` appends the local model of auto mode.
- Returns the chain wrapped in `FallbackBackend` with `max_retries`, the notice printer and the **probe**: a quick
  connection check to Gemini's address, used to notice that the internet is gone or back (see `resilient.md`).

## `make_hosted`
@@ localagent/backends.py make_hosted @@
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
@@ localagent/backends.py after_make_hosted @@
@@ localagent/backends.py make_backend @@
- `BACKENDS` maps each name to something callable with a `Config`: the two client classes and the `make_gemini` /
  `make_hosted` / `make_local` functions. `make_backend` looks the name up and calls it.
- An unknown name raises `KeyError`; it is converted to a `ValueError` that lists the valid names (`from None` hides the
  confusing original `KeyError` from the traceback).
