# `localagent/providers.py` — who can answer: online services and local model servers

**Role:** two small tables and the helpers around them. The first lists the **online services** the agent can use with
your own key; the second lists the **model servers people run on their own computers**. `localagent setup`
(`wizard.md`) offers everything in both tables, the configuration (`config.md`) reads your choice from them, and
`make_hosted` (`backends.md`) and the client (`openai_compat.md`) use the details a service needs.

| `--provider` | Service | Key variable | Price (Nov 2026) |
|---|---|---|---|
| `gemini` | Google Gemini | `GEMINI_API_KEY` | free daily quota, no card needed — the default |
| `openai` | OpenAI | `OPENAI_API_KEY` | pay per use |
| `anthropic` | Anthropic (Claude) | `ANTHROPIC_API_KEY` | pay per use |
| `openrouter` | OpenRouter | `OPENROUTER_API_KEY` | one key, hundreds of models, some free |
| `groq` | Groq | `GROQ_API_KEY` | free tier, very fast |
| `mistral` | Mistral | `MISTRAL_API_KEY` | free tier |
| `deepseek` | DeepSeek | `DEEPSEEK_API_KEY` | very low prices |
| `custom` | any OpenAI-compatible address you give | `LOCALAGENT_PROVIDER_KEY` | — |

**Why one table is enough.** All of these speak the "OpenAI-compatible" API: `GET /models` lists the models and
`POST /chat/completions` answers, tool calls included. The agent already talks that API to Gemini and to local
servers, so a new service is one line, not new code. The few differences are data in the table: Anthropic wants the
key in a second header and an explicit answer length; anything else a service refuses is detected and left out by the
client itself (`refused_field` in `openai_compat.md`).

**How it was tested.** Gemini (through both the Gemini chain and the generic `custom` path) and a llama.cpp server were
checked live; every other service is tested against a small local server that behaves like them
(`tests/test_providers.py`). The model names suggested for the paid services come from their published lists; setup
always shows what your key really offers, so an outdated suggestion is simply not offered.

## Module header
@@ localagent/providers.py header @@
- `re` matches dated model names; `dataclass` builds the two record types; `get_json` and `DownloadError` (from
  `fetch.py`) read a JSON answer with a time limit and turn every failure into one error type.
- `GEMINI_BASE`: Google's OpenAI-compatible address (the config's default too). `CUSTOM_KEY`: the key variable of the
  `custom` entry.
- `NOT_CHAT`: words in model names that cannot chat (embeddings, speech, images, moderation...) — they are never
  suggested. `LOCAL_FAVOURITES`: local model families known to use tools well, best first.

## `Provider` and the table
@@ localagent/providers.py Provider @@
- One online service: `name` (what you type), `title` (what the menu shows), `base` (its API address; empty only for
  `custom`), `key_env` (the variable that holds your key — each service's usual name, so a key you already set for
  other tools just works), `key_page` (where keys are made), `note` (price in a few words), `suggested` (good models
  for this agent, best first), `key_header` (a second header carrying the key), `headers` (fixed extra headers) and
  `max_tokens` (an answer length to ask for; 0 lets the service decide). `frozen=True`: nobody can change an entry.

@@ localagent/providers.py after_Provider @@
- `PROVIDERS`: the table above. Gemini comes first because it is the only one with a free quota that needs no card.
  Anthropic's entry sends `x-api-key` and `anthropic-version` besides the usual `Authorization: Bearer` and asks for
  answers of up to 16 384 tokens, so a long file is never cut off.

## `LocalServer` and the second table
@@ localagent/providers.py LocalServer @@
- A model server on your own computer: its `title`, the address it uses when installed normally, and its `kind`:
  `ollama` (Ollama's own API, which lets the agent set the context size — its OpenAI-style API cannot, and a too-small
  context silently cuts conversations) or `openai`.

@@ localagent/providers.py after_LocalServer @@
- `LOCAL_SERVERS`: Ollama (11434), LM Studio (1234), llama.cpp (8080), vLLM (8000) and Jan (1337) at their default
  ports. Ollama's address is `localhost`, exactly the config's default, so choosing it saves nothing extra.

## Looking up and sending keys
@@ localagent/providers.py find_provider @@
- The entry called `name` in any letter case (spaces ignored), or `None`.

@@ localagent/providers.py key_help @@
- The "no key" message: which variable is missing, where to make a key, and that `localagent setup` checks and saves it.

@@ localagent/providers.py key_headers @@
- The HTTP headers for a request: the service's fixed ones, then `Authorization: Bearer <key>` and, where the table
  says so, the key once more in its own header. No key, no key headers (a `custom` server may need none).

## Asking a service what it offers
@@ localagent/providers.py list_models @@
- `GET <base>/models` with the given headers. The answer's `data` list holds one entry per model; their `id`s are
  returned, with Gemini's `models/` prefix removed. Anything that is not the expected shape gives an empty list
  instead of a crash. Listing models costs no quota on any service, which is why it is the key check.

@@ localagent/providers.py check_key @@
- A key's state: `ok` with the models offered; `rejected` when the service refused it — HTTP 401 or 403, or 400, which
  is how Google answers a malformed key; or `unreachable` for everything else (no internet, a wrong address, not a
  model service). The error text comes back too, so the setup can show it.

## Suggesting a model
@@ localagent/providers.py chat_models @@
- Drops the models that cannot chat (any `NOT_CHAT` word in the name, in any letter case).

@@ localagent/providers.py pick_model @@
- The first suggested model that the service offers — exactly, or as a dated version (`claude-sonnet-4-5-20250929`
  matches `claude-sonnet-4-5`: Anthropic lists dated names). The regular expression allows only an optional
  `-` + 8 digits after the name, so `gpt-5` never matches `gpt-5-mini`. With no suggestion offered: the first chat
  model; with nothing offered: empty text.

@@ localagent/providers.py pick_local_model @@
- For local servers: the first model of a favourite family (a `coder` model beats a plain `qwen3`), else the first
  chat model, else the first model at all.

## Finding local servers
@@ localagent/providers.py server_models @@
- The models one local server offers: Ollama's `/api/tags` list (`models[].name`) or the OpenAI-style `/models` list.
  `None` means nothing answered there — different from an empty list, which means "running, but no model loaded yet".
  The short time limit keeps the scan quick; a refused connection on your own computer answers at once anyway.

@@ localagent/providers.py scan_local_servers @@
- Asks every address in turn — your own addresses (`extra`, from `OLLAMA_HOST` and `LOCALAGENT_API_BASE`) first, then
  the table — and returns the servers that answered with their models. An address is asked once, even when it
  appears in both lists or with a trailing `/`.
