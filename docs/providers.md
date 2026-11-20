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
```python
  1 | """Who can answer: online services you use with your own key, and model servers you run yourself.
  2 | 
  3 | Each one is a line in a table, so supporting another service means adding a line. They all speak
  4 | the "OpenAI-compatible" API (``/models`` lists the models, ``/chat/completions`` answers), which
  5 | nearly every service and local model server offers. Gemini gets extra care elsewhere (its free
  6 | quota and its chain of models, see ``backends.py``); here it is simply the first choice.
  7 | """
  8 | from __future__ import annotations
  9 | 
 10 | import re
 11 | from dataclasses import dataclass
 12 | 
 13 | from .fetch import DownloadError, get_json
 14 | 
 15 | GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta/openai"
 16 | CUSTOM_KEY = "LOCALAGENT_PROVIDER_KEY"              # the key variable of the "custom" entry
 17 | NOT_CHAT = ("embed", "whisper", "tts", "dall-e", "image", "audio", "transcribe", "realtime",
 18 |             "moderation", "rerank", "guard", "search", "ocr", "babbage", "davinci")
 19 | LOCAL_FAVOURITES = ("coder", "qwen3", "devstral", "gpt-oss", "qwen2.5", "llama3")
 20 | 
 21 | 
```
- `re` matches dated model names; `dataclass` builds the two record types; `get_json` and `DownloadError` (from
  `fetch.py`) read a JSON answer with a time limit and turn every failure into one error type.
- `GEMINI_BASE`: Google's OpenAI-compatible address (the config's default too). `CUSTOM_KEY`: the key variable of the
  `custom` entry.
- `NOT_CHAT`: words in model names that cannot chat (embeddings, speech, images, moderation...) — they are never
  suggested. `LOCAL_FAVOURITES`: local model families known to use tools well, best first.

## `Provider` and the table
```python
 22 | @dataclass(frozen=True)
 23 | class Provider:
 24 |     """An online service that answers OpenAI-style chat requests, tool calls included."""
 25 | 
 26 |     name: str                                  # what you type: --provider openai
 27 |     title: str                                 # what the setup menu shows
 28 |     base: str                                  # its API address ("" = you give it: "custom")
 29 |     key_env: str                               # the environment variable that holds your key
 30 |     key_page: str                              # where a key is made
 31 |     note: str                                  # its price, in a few words
 32 |     suggested: tuple[str, ...] = ()            # good models for this agent, best first
 33 |     key_header: str = ""                       # a second header that carries the key
 34 |     headers: tuple[tuple[str, str], ...] = ()  # fixed extra headers the service expects
 35 |     max_tokens: int = 0                        # answer length to ask for (0: the service decides)
 36 | 
 37 | 
```
- One online service: `name` (what you type), `title` (what the menu shows), `base` (its API address; empty only for
  `custom`), `key_env` (the variable that holds your key — each service's usual name, so a key you already set for
  other tools just works), `key_page` (where keys are made), `note` (price in a few words), `suggested` (good models
  for this agent, best first), `key_header` (a second header carrying the key), `headers` (fixed extra headers) and
  `max_tokens` (an answer length to ask for; 0 lets the service decide). `frozen=True`: nobody can change an entry.

```python
 38 | PROVIDERS = (
 39 |     Provider("gemini", "Google Gemini", GEMINI_BASE, "GEMINI_API_KEY",
 40 |              "https://aistudio.google.com/apikey", "free daily quota, no card needed"),
 41 |     Provider("openai", "OpenAI", "https://api.openai.com/v1", "OPENAI_API_KEY",
 42 |              "https://platform.openai.com/api-keys", "pay per use",
 43 |              ("gpt-5-mini", "gpt-5", "gpt-4.1-mini", "gpt-4.1", "gpt-4o-mini")),
 44 |     Provider("anthropic", "Anthropic (Claude)", "https://api.anthropic.com/v1", "ANTHROPIC_API_KEY",
 45 |              "https://console.anthropic.com/settings/keys", "pay per use",
 46 |              ("claude-sonnet-5-5", "claude-haiku-5-5", "claude-opus-5-5", "claude-sonnet-4-5",
 47 |               "claude-haiku-4-5"),
 48 |              key_header="x-api-key", headers=(("anthropic-version", "2023-06-01"),), max_tokens=16384),
 49 |     Provider("openrouter", "OpenRouter", "https://openrouter.ai/api/v1", "OPENROUTER_API_KEY",
 50 |              "https://openrouter.ai/keys", "one key, hundreds of models, some free",
 51 |              ("qwen/qwen3-coder:free", "openai/gpt-oss-120b:free", "z-ai/glm-4.5-air:free",
 52 |               "deepseek/deepseek-chat-v3.1:free", "openai/gpt-5-mini", "anthropic/claude-sonnet-4.5")),
 53 |     Provider("groq", "Groq", "https://api.groq.com/openai/v1", "GROQ_API_KEY",
 54 |              "https://console.groq.com/keys", "free tier, very fast",
 55 |              ("openai/gpt-oss-120b", "moonshotai/kimi-k2-instruct-0905", "qwen/qwen3-32b",
 56 |               "llama-3.3-70b-versatile")),
 57 |     Provider("mistral", "Mistral", "https://api.mistral.ai/v1", "MISTRAL_API_KEY",
 58 |              "https://console.mistral.ai/api-keys", "free tier",
 59 |              ("devstral-medium-latest", "mistral-medium-latest", "devstral-small-latest",
 60 |               "mistral-small-latest")),
 61 |     Provider("deepseek", "DeepSeek", "https://api.deepseek.com/v1", "DEEPSEEK_API_KEY",
 62 |              "https://platform.deepseek.com/api_keys", "very low prices", ("deepseek-chat",)),
 63 |     Provider("custom", "another OpenAI-compatible service", "", CUSTOM_KEY, "",
 64 |              "any address you give"),
 65 | )
 66 | 
 67 | 
```
- `PROVIDERS`: the table above. Gemini comes first because it is the only one with a free quota that needs no card.
  Anthropic's entry sends `x-api-key` and `anthropic-version` besides the usual `Authorization: Bearer` and asks for
  answers of up to 16 384 tokens, so a long file is never cut off.

## `LocalServer` and the second table
```python
 68 | @dataclass(frozen=True)
 69 | class LocalServer:
 70 |     """A model server that people run on their own computer (no key needed)."""
 71 | 
 72 |     title: str                                 # "Ollama"
 73 |     base: str                                  # where it answers when installed normally
 74 |     kind: str                                  # "ollama" (its own API) or "openai"
 75 | 
 76 | 
```
- A model server on your own computer: its `title`, the address it uses when installed normally, and its `kind`:
  `ollama` (Ollama's own API, which lets the agent set the context size — its OpenAI-style API cannot, and a too-small
  context silently cuts conversations) or `openai`.

```python
 77 | LOCAL_SERVERS = (
 78 |     LocalServer("Ollama", "http://localhost:11434", "ollama"),
 79 |     LocalServer("LM Studio", "http://127.0.0.1:1234/v1", "openai"),
 80 |     LocalServer("llama.cpp", "http://127.0.0.1:8080/v1", "openai"),
 81 |     LocalServer("vLLM", "http://127.0.0.1:8000/v1", "openai"),
 82 |     LocalServer("Jan", "http://127.0.0.1:1337/v1", "openai"),
 83 | )
 84 | 
 85 | 
```
- `LOCAL_SERVERS`: Ollama (11434), LM Studio (1234), llama.cpp (8080), vLLM (8000) and Jan (1337) at their default
  ports. Ollama's address is `localhost`, exactly the config's default, so choosing it saves nothing extra.

## Looking up and sending keys
```python
 86 | def find_provider(name: str) -> Provider | None:
 87 |     """The provider called ``name`` (any letter case), or None."""
 88 |     wanted = name.strip().lower()
 89 |     return next((provider for provider in PROVIDERS if provider.name == wanted), None)
 90 | 
 91 | 
```
- The entry called `name` in any letter case (spaces ignored), or `None`.

```python
 92 | def key_help(provider: Provider) -> str:
 93 |     """How to give the agent a key for ``provider`` (one sentence or two)."""
 94 |     where = f" (create one at {provider.key_page})" if provider.key_page else ""
 95 |     return (f"{provider.key_env} is not set{where}. The easiest way: localagent setup - it checks "
 96 |             f"the key and saves it; or set the variable yourself and open a NEW terminal.")
 97 | 
 98 | 
```
- The "no key" message: which variable is missing, where to make a key, and that `localagent setup` checks and saves it.

```python
 99 | def key_headers(provider: Provider, key: str) -> dict[str, str]:
100 |     """The HTTP headers that carry ``key``, plus any the service always expects."""
101 |     headers = dict(provider.headers)
102 |     if key:
103 |         headers["Authorization"] = f"Bearer {key}"
104 |         if provider.key_header:
105 |             headers[provider.key_header] = key
106 |     return headers
107 | 
108 | 
```
- The HTTP headers for a request: the service's fixed ones, then `Authorization: Bearer <key>` and, where the table
  says so, the key once more in its own header. No key, no key headers (a `custom` server may need none).

## Asking a service what it offers
```python
109 | def list_models(base: str, headers: dict[str, str], timeout: float = 15) -> list[str]:
110 |     """The model names a service or server offers (its ``/models`` list; costs no quota).
111 |     Raises DownloadError when it cannot be read; the message names the HTTP status."""
112 |     listed = get_json(base.rstrip("/") + "/models", headers, timeout=timeout)
113 |     items = listed.get("data", []) if isinstance(listed, dict) else []
114 |     return [str(item["id"]).removeprefix("models/") for item in items
115 |             if isinstance(item, dict) and item.get("id")]
116 | 
117 | 
```
- `GET <base>/models` with the given headers. The answer's `data` list holds one entry per model; their `id`s are
  returned, with Gemini's `models/` prefix removed. Anything that is not the expected shape gives an empty list
  instead of a crash. Listing models costs no quota on any service, which is why it is the key check.

```python
118 | def check_key(base: str, headers: dict[str, str]) -> tuple[str, list[str], str]:
119 |     """(state, models, detail) for a key: "ok" with the models offered, "rejected" (the service
120 |     refused it), or "unreachable" (no answer, or not a model service at that address)."""
121 |     try:
122 |         return "ok", list_models(base, headers), ""
123 |     except DownloadError as exc:
124 |         detail = str(exc)
125 |         refused = re.search(r"HTTP (400|401|403)\b", detail) is not None
126 |         return ("rejected" if refused else "unreachable"), [], detail
127 | 
128 | 
```
- A key's state: `ok` with the models offered; `rejected` when the service refused it — HTTP 401 or 403, or 400, which
  is how Google answers a malformed key; or `unreachable` for everything else (no internet, a wrong address, not a
  model service). The error text comes back too, so the setup can show it.

## Suggesting a model
```python
129 | def chat_models(offered: list[str]) -> list[str]:
130 |     """``offered`` without the models that cannot chat (embeddings, speech, images...)."""
131 |     return [name for name in offered if not any(word in name.lower() for word in NOT_CHAT)]
132 | 
133 | 
```
- Drops the models that cannot chat (any `NOT_CHAT` word in the name, in any letter case).

```python
134 | def pick_model(provider: Provider, offered: list[str]) -> str:
135 |     """The model to suggest: the first of ``provider.suggested`` that is offered - exactly, or as
136 |     a dated version such as ``claude-sonnet-4-5-20250929`` - else the first chat model offered."""
137 |     for wanted in provider.suggested:
138 |         dated = re.compile(re.escape(wanted) + r"(-\d{8})?")
139 |         for name in offered:
140 |             if dated.fullmatch(name):
141 |                 return name
142 |     chat = chat_models(offered)
143 |     return chat[0] if chat else ""
144 | 
145 | 
```
- The first suggested model that the service offers — exactly, or as a dated version (`claude-sonnet-4-5-20250929`
  matches `claude-sonnet-4-5`: Anthropic lists dated names). The regular expression allows only an optional
  `-` + 8 digits after the name, so `gpt-5` never matches `gpt-5-mini`. With no suggestion offered: the first chat
  model; with nothing offered: empty text.

```python
146 | def pick_local_model(offered: list[str]) -> str:
147 |     """The local model to suggest: the first that is known to use tools well, else the first."""
148 |     chat = chat_models(offered) or offered
149 |     for word in LOCAL_FAVOURITES:
150 |         for name in chat:
151 |             if word in name.lower():
152 |                 return name
153 |     return chat[0] if chat else ""
154 | 
155 | 
```
- For local servers: the first model of a favourite family (a `coder` model beats a plain `qwen3`), else the first
  chat model, else the first model at all.

## Finding local servers
```python
156 | def server_models(server: LocalServer, timeout: float = 1.0) -> list[str] | None:
157 |     """The models a local server offers, or None when nothing answers at its address."""
158 |     try:
159 |         if server.kind == "ollama":
160 |             listed = get_json(server.base.rstrip("/") + "/api/tags", timeout=timeout)
161 |             items = listed.get("models", []) if isinstance(listed, dict) else []
162 |             return [str(item["name"]) for item in items if isinstance(item, dict) and item.get("name")]
163 |         return list_models(server.base, {}, timeout)
164 |     except DownloadError:
165 |         return None
166 | 
167 | 
```
- The models one local server offers: Ollama's `/api/tags` list (`models[].name`) or the OpenAI-style `/models` list.
  `None` means nothing answered there — different from an empty list, which means "running, but no model loaded yet".
  The short time limit keeps the scan quick; a refused connection on your own computer answers at once anyway.

```python
168 | def scan_local_servers(extra: tuple[LocalServer, ...] = (),
169 |                        timeout: float = 1.0) -> list[tuple[LocalServer, list[str]]]:
170 |     """The local model servers that answer right now, each with the models it offers. ``extra``
171 |     (your own addresses) are tried first; an address is only tried once."""
172 |     found: list[tuple[LocalServer, list[str]]] = []
173 |     tried: set[str] = set()
174 |     for server in extra + LOCAL_SERVERS:
175 |         address = server.base.rstrip("/").lower()
176 |         if address in tried:
177 |             continue
178 |         tried.add(address)
179 |         models = server_models(server, timeout)
180 |         if models is not None:
181 |             found.append((server, models))
182 |     return found
```
- Asks every address in turn — your own addresses (`extra`, from `OLLAMA_HOST` and `LOCALAGENT_API_BASE`) first, then
  the table — and returns the servers that answered with their models. An address is asked once, even when it
  appears in both lists or with a trailing `/`.
