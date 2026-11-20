# `localagent/openai_compat.py` — one client for every OpenAI-compatible service

**Role in the flow:** lets the *same agent* talk to anything that implements the common `/chat/completions` API: the
online services of `providers.md` (Gemini, OpenAI, Anthropic, OpenRouter, Groq, Mistral, DeepSeek, any custom address)
and local servers — LM Studio, llama.cpp's server, vLLM, Ollama's own `/v1` endpoint. When you outgrow the laptop, run
a larger model on a GPU machine and choose it in `localagent setup` ("a model server at another address"); no code
changes.

Internally the agent stores messages in Ollama's shape, so this module converts them out and back.

## Module header
```python
  1 | """Client for any OpenAI-compatible server: online services (OpenAI, Anthropic, OpenRouter,
  2 | Groq, Mistral, DeepSeek, Gemini...) and local ones (LM Studio, llama.cpp, vLLM, ...).
  3 | 
  4 | Internal messages use Ollama's shape, so they are converted on the way out and back in.
  5 | """
  6 | from __future__ import annotations
  7 | 
  8 | import json
  9 | import time
 10 | from typing import Iterable
 11 | 
 12 | from .config import Config
 13 | from .llm import LLMError, Reply, TokenSink, post
 14 | from .providers import Provider, key_headers
 15 | 
 16 | OPTIONAL_FIELDS = ("temperature", "stream_options", "reasoning_effort", "max_tokens")
 17 | 
 18 | # Google's documented placeholder for tool calls that have no signature of their own
 19 | # (e.g. made by another model before a fallback switch). Gemini 3 rejects them otherwise.
 20 | PLACEHOLDER_SIGNATURE = {"google": {"thought_signature": "skip_thought_signature_validator"}}
 21 | 
 22 | 
```
- `PLACEHOLDER_SIGNATURE` is Google's documented stand-in thought signature (`skip_thought_signature_validator`).
- Imports `json` (arguments need to be JSON text for this API), `time`, the shared `Config`, `Reply`, `TokenSink`,
  `post` and `LLMError` from `llm.py` (so error handling is identical for both backends), and `Provider`/`key_headers`
  from `providers.py`.
- `OPTIONAL_FIELDS`: request fields the agent can do without — the temperature, `stream_options` (token counts at the
  end of a streamed answer), `reasoning_effort` and `max_tokens`. If a server refuses one, it is left out (see
  `refused_field`).

## `to_openai` — message conversion
```python
 23 | def to_openai(messages: list[dict], gemini: bool = False) -> list[dict]:
 24 |     """Convert internal (Ollama-shaped) messages to OpenAI chat messages.
 25 | 
 26 |     For ``gemini``, every tool call carries a thought signature - its own, or Google's
 27 |     placeholder when another model made the call - so switching models mid-task works.
 28 |     Other servers never see Google's signatures (a conversation may move from Gemini to the
 29 |     local model at any time, and a strict server could reject the unknown field).
 30 |     """
 31 |     converted: list[dict] = []
 32 |     for message in messages:
 33 |         if message["role"] == "assistant" and message.get("tool_calls"):
 34 |             calls = []
 35 |             for call in message["tool_calls"]:
 36 |                 function = call["function"]
 37 |                 arguments = function.get("arguments", {})
 38 |                 entry = {
 39 |                     "id": call.get("id", "call_0"),
 40 |                     "type": "function",
 41 |                     "function": {
 42 |                         "name": function["name"],
 43 |                         "arguments": arguments if isinstance(arguments, str)
 44 |                         else json.dumps(arguments),
 45 |                     },
 46 |                 }
 47 |                 if gemini:                           # its own signature, else the placeholder
 48 |                     entry["extra_content"] = call.get("extra_content") or PLACEHOLDER_SIGNATURE
 49 |                 calls.append(entry)
 50 |             converted.append({"role": "assistant", "content": message.get("content") or None,
 51 |                               "tool_calls": calls})
 52 |         elif message["role"] == "tool":
 53 |             converted.append({"role": "tool", "tool_call_id": message.get("tool_call_id", ""),
 54 |                               "content": message["content"]})
 55 |         else:
 56 |             converted.append({"role": message["role"], "content": message["content"]})
 57 |     return converted
 58 | 
 59 | 
```
- Walks the history. An **assistant message with tool calls** needs, per call, an `id`, `"type": "function"`, and the
  arguments as a **JSON string** (Ollama uses a dict, hence `json.dumps` unless it is already a string).
  `call.get("id", "call_0")` is a safety default; the agent always assigns real ids.
- `gemini=True` (only for requests to Google): every tool call carries `extra_content` — its own when it has one,
  else the placeholder. **Gemini 3 models** attach a *thought signature* there to every tool call and reject the next
  request (HTTP 400) unless it is sent back unchanged. A call **without** a signature gets the placeholder. Why: after a
  fallback switch, the history can contain tool calls made by a model that produces no signatures (e.g.
  `gemini-2.5-flash`), and a Gemini 3 model then refused the whole request with *"Function call is missing a
  thought_signature"* — a real failure seen in the eval. The placeholder was verified live: `gemini-3.5-flash-lite`
  accepted such a history and answered correctly.
- Every other server gets **no** `extra_content` at all. Before 0.6 a call's own signature was passed to any server;
  now that a conversation can move from Gemini to the local model at any moment (`/model offline`, or offline
  detection), Google-only fields must not reach a server that might reject unknown fields.
- `content: ... or None`: this API wants `null`, not an empty string, when a message only contains tool calls.
- A **tool-result message** becomes `{"role": "tool", "tool_call_id": ..., "content": ...}`; the id links the result to
  the request it answers.
- Everything else (system, user, plain assistant) passes through with just role and content.

## `_kept_call`
```python
 60 | def _kept_call(call: dict) -> dict:
 61 |     """A tool call in our internal shape; a thought signature is kept for the next turn."""
 62 |     kept = {"id": call.get("id"), "function": {
 63 |         "name": call["function"]["name"], "arguments": call["function"]["arguments"]}}
 64 |     if "extra_content" in call:                        # must be sent back unchanged next turn
 65 |         kept["extra_content"] = call["extra_content"]
 66 |     return kept
 67 | 
 68 | 
```
- Converts a tool call from the server's shape to ours (id, name, argument text), keeping a thought signature
  (`extra_content`) so it is sent back next turn. Used by both the streaming and the one-piece path.

## `read_stream` — answers that arrive in pieces
```python
 69 | def read_stream(lines: Iterable[bytes], on_token: TokenSink | None) -> tuple[dict, dict]:
 70 |     """Assemble a streamed answer: ``data: {...}`` lines (Server-Sent Events) until ``[DONE]``.
 71 | 
 72 |     Text pieces go to ``on_token`` as they arrive. Tool calls arrive in pieces too, keyed by
 73 |     their ``index``; names and argument text are concatenated. Returns (message, usage).
 74 |     """
 75 |     text: list[str] = []
 76 |     calls: dict[int, dict] = {}
 77 |     usage: dict = {}
 78 |     for raw in lines:
 79 |         line = raw.decode("utf-8", errors="replace").strip()
 80 |         if not line.startswith("data:"):
 81 |             continue                                    # blank lines and comments
 82 |         data = line[5:].strip()
 83 |         if data == "[DONE]":
 84 |             break
 85 |         try:
 86 |             chunk = json.loads(data)
 87 |         except ValueError:
 88 |             continue
 89 |         if not isinstance(chunk, dict):
 90 |             continue
 91 |         if chunk.get("error"):
 92 |             raise LLMError(f"Server error in stream: {chunk['error']}")
 93 |         usage = chunk.get("usage") or usage             # usually only in the last chunk
 94 |         for choice in chunk.get("choices") or []:
 95 |             delta = choice.get("delta") or {}
 96 |             piece = delta.get("content")
 97 |             if piece:
 98 |                 text.append(piece)
 99 |                 if on_token:
100 |                     on_token(piece)
101 |             for part in delta.get("tool_calls") or []:
102 |                 slot = calls.setdefault(part.get("index", len(calls)),
103 |                                         {"id": None, "function": {"name": "", "arguments": ""}})
104 |                 slot["id"] = part.get("id") or slot["id"]
105 |                 function = part.get("function") or {}
106 |                 slot["function"]["name"] += function.get("name") or ""
107 |                 slot["function"]["arguments"] += function.get("arguments") or ""
108 |                 if part.get("extra_content"):
109 |                     slot["extra_content"] = part["extra_content"]
110 |     message: dict = {"role": "assistant", "content": "".join(text)}
111 |     if calls:
112 |         message["tool_calls"] = [_kept_call(calls[i]) for i in sorted(calls)]
113 |     return message, usage
114 | 
115 | 
```
- With streaming the server sends many small `data: {...}` lines (the "Server-Sent Events" format) and finally
  `data: [DONE]`. Blank lines, comments and unparseable lines are skipped; an `error` object raises `LLMError`.
- Text pieces (`delta.content`) are collected and passed to `on_token` immediately — that is what makes the answer
  appear on screen while it is being written.
- Tool calls arrive in pieces as well, each piece carrying its call's `index`: the name and the argument text are glued
  together per index, the id and a thought signature are kept when they appear.
- The token counts come in the last chunk (we ask for them with `stream_options`).
- Verified live: llama.cpp streamed a tool call and a 16-piece answer; Gemini streamed a tool call **with** its thought
  signature, and text answers.

## `refused_field`
```python
116 | def refused_field(error: LLMError, payload: dict) -> str:
117 |     """The optional request field a server refused - named in its HTTP 400/422 answer - or ""."""
118 |     if error.status not in (400, 422):
119 |         return ""
120 |     text = str(error)
121 |     return next((name for name in OPTIONAL_FIELDS if name in payload and name in text), "")
122 | 
123 | 
```
- Reads a refusal: for HTTP 400 or 422 ("bad request", "cannot process") it returns the first optional field that
  the request contains *and* the error text names — OpenAI's newest models allow only their default temperature
  (`'temperature' does not support 0.0`), strict services such as Mistral answer 422 to fields they do not know
  (`stream_options`). Anything else returns `""`: the error is real and is reported as it is.

## `OpenAICompatClient`
```python
124 | class OpenAICompatClient:
125 |     """Chat client for ``/chat/completions`` servers (streaming by default).
126 | 
127 |     ``provider`` (an online service from providers.py) adds the headers and request fields that
128 |     service expects. An optional field a server refuses is left out from then on.
129 |     """
130 | 
```
```python
131 |     def __init__(self, config: Config, provider: Provider | None = None) -> None:
132 |         self._config = config
133 |         self._provider = provider
134 |         self.dropped: set[str] = set()               # optional fields this server refused
135 | 
```
- Keeps the `Config` and, for an online service, its `Provider` entry. `dropped` collects the optional fields this
  server refused; it lives as long as the client, so a field is refused once, not on every request.

```python
136 |     def chat(self, messages: list[dict], tools: list[dict],
137 |              on_token: TokenSink | None = None) -> Reply:
138 |         """Send the conversation and return the model's next message."""
139 |         payload: dict = {
140 |             "model": self._config.model,
141 |             "messages": to_openai(messages, gemini=self._config.api_base == self._config.gemini_base),
142 |             "temperature": self._config.temperature,
143 |             "stream": self._config.stream,
144 |         }
145 |         if self._config.stream:                      # ask for token counts in the last chunk
146 |             payload["stream_options"] = {"include_usage": True}
147 |         if tools:                                    # some servers reject an empty list
148 |             payload["tools"] = tools
149 |         if self._config.reasoning_effort:            # e.g. "low": less hidden thinking
150 |             payload["reasoning_effort"] = self._config.reasoning_effort
151 |         if self._provider is not None and self._provider.max_tokens:
152 |             payload["max_tokens"] = self._provider.max_tokens
153 |         for name in self.dropped:
154 |             payload.pop(name, None)
155 |         if self._provider is not None:
156 |             headers = key_headers(self._provider, self._config.api_key)
157 |         else:
158 |             headers = {"Authorization": f"Bearer {self._config.api_key}"} if self._config.api_key else {}
159 |         started = time.monotonic()
160 |         with self._send(payload, headers) as response:
161 |             if self._config.stream:
162 |                 try:
163 |                     message, usage = read_stream(response, on_token)
164 |                 except (TimeoutError, ConnectionError) as exc:
165 |                     raise LLMError(f"The connection broke during the answer: {exc}") from exc
166 |             else:
167 |                 try:
168 |                     body = json.load(response)
169 |                     raw = body["choices"][0]["message"]
170 |                 except (ValueError, KeyError, IndexError, TypeError) as exc:
171 |                     raise LLMError(f"The server's answer had an unexpected format ({exc!r})") from exc
172 |                 message = {"role": "assistant", "content": raw.get("content") or ""}
173 |                 if raw.get("tool_calls"):
174 |                     message["tool_calls"] = [_kept_call(call) for call in raw["tool_calls"]]
175 |                 if on_token and message["content"]:
176 |                     on_token(message["content"])     # whole text at once
177 |                 usage = body.get("usage") or {}
178 |         prompt = int(usage.get("prompt_tokens", 0))
179 |         # total can exceed prompt + completion when the model "thinks" in hidden tokens;
180 |         # those still count against quotas, so they are added to the completion side.
181 |         completion = max(int(usage.get("completion_tokens", 0)),
182 |                          int(usage.get("total_tokens", 0)) - prompt)
183 |         return Reply(
184 |             message=message,
185 |             prompt_tokens=prompt,
186 |             completion_tokens=completion,
187 |             seconds=time.monotonic() - started,
188 |         )
189 | 
```
- The client sends signatures only when it talks to Google (`api_base == gemini_base`).
- Streaming is on by default (`stream`), with `stream_options` asking for token counts at the end.
- `payload` has the model name, converted messages, tools, temperature and `"stream": False` (this client reads one
  complete answer; streaming is a roadmap item).
- `tools` is added only when there are tools (some servers reject an empty list).
- `reasoning_effort` (e.g. `low`) is added only when set (`--reasoning`); it limits hidden "thinking", which costs
  time and quota tokens on thinking models.
- A service whose table entry sets `max_tokens` (Anthropic) gets it in every request. Fields in `dropped` are taken out.
- `headers`: for an online service, `key_headers` (its key in the header or headers it expects); otherwise
  `Authorization: Bearer <key>` only when an API key is configured.
- `_send` sends it to `api_base + "/chat/completions"` with `request_timeout` (online clients get the shorter
  `gemini_timeout`). Streaming: `read_stream` assembles the answer (a connection lost half-way becomes
  `LLMError`, so the retry/fallback logic can handle it). One-piece: `json.load` parses the reply; a reply that is not
  JSON or has no `choices[0].message` becomes an `LLMError` ("unexpected format") instead of crashing the program with a
  `KeyError`.
- The answer is at `choices[0].message`. Text goes into `content`; each tool call is normalised to
  `{"id", "function": {"name", "arguments"}}` — here `arguments` stays a JSON *string*, which `agent._parse_args`
  already knows how to decode. A call's `extra_content` (thought signature) is kept on the stored message so
  `to_openai` can send it back next turn.
- `on_token(...)` is called once with the whole text, so the CLI's live display still works (all at once).
- Token counts come from the `usage` object; missing values default to 0. Thinking models report
  `total_tokens` larger than prompt + completion (the difference is hidden thinking, which still counts against quotas),
  so the completion figure is raised to `total - prompt` when that is larger. Measured on Gemini: 66 prompt + 28 answer
  = 179 total, i.e. 85 hidden tokens.

```python
190 |     def _send(self, payload: dict, headers: dict[str, str]):
191 |         """POST the request. When the server refuses an optional field (a model that only allows
192 |         its default temperature, a service that does not know ``stream_options``...), that
193 |         field is left out and the request sent again - and from then on."""
194 |         while True:
195 |             try:
196 |                 return post(self._config.api_base + "/chat/completions", payload, headers,
197 |                             timeout=self._config.request_timeout)
198 |             except LLMError as exc:
199 |                 field = refused_field(exc, payload)
200 |                 if not field:
201 |                     raise
202 |                 self.dropped.add(field)
203 |                 del payload[field]
```
- Posts the request. When `refused_field` finds an optional field in the refusal, that field is remembered in
  `dropped`, removed from this request, and the request sent again at once — so any OpenAI-compatible service works
  without a setting per service. The loop ends: every round removes one field that is in the request, and other
  errors are raised. Tested with a fake server that refuses `temperature`: the first request is refused, the second
  succeeds, and the next conversation is right at once (`tests/test_providers.py`).
