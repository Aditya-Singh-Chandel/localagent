# `localagent/openai_compat.py` — the scaling path to bigger models

**Role in the flow:** lets the *same agent* talk to any server that implements the common `/chat/completions` API:
LM Studio, llama.cpp's server, vLLM, Ollama's own `/v1` endpoint, or a hosted service. When you outgrow the laptop,
run a larger model on a GPU machine and start the agent with
`--backend openai --api-base http://that-machine:8000/v1 --model <name>`; no other code changes.

Internally the agent stores messages in Ollama's shape, so this module converts them out and back.

## Module header
```python
  1 | """Client for any OpenAI-compatible server (LM Studio, llama.cpp, vLLM, ...).
  2 | 
  3 | This is the scaling path: run the same agent against a bigger model on a GPU
  4 | box by pointing ``--backend openai --api-base http://host:port/v1`` at it.
  5 | Internal messages use Ollama's shape, so they are converted on the way out and
  6 | back in.
  7 | """
  8 | from __future__ import annotations
  9 | 
 10 | import json
 11 | import time
 12 | from typing import Iterable
 13 | 
 14 | from .config import Config
 15 | from .llm import LLMError, Reply, TokenSink, post
 16 | 
 17 | # Google's documented placeholder for tool calls that have no signature of their own
 18 | # (e.g. made by another model before a fallback switch). Gemini 3 rejects them otherwise.
 19 | PLACEHOLDER_SIGNATURE = {"google": {"thought_signature": "skip_thought_signature_validator"}}
 20 | 
 21 | 
```
- `PLACEHOLDER_SIGNATURE` is Google's documented stand-in thought signature (`skip_thought_signature_validator`).
- Imports `json` (arguments need to be JSON text for this API), `time`, the shared `Config`, and `Reply`, `TokenSink`
  and `post` from `llm.py` (so error handling is identical for both backends).

## `to_openai` — message conversion
```python
 22 | def to_openai(messages: list[dict], gemini: bool = False) -> list[dict]:
 23 |     """Convert internal (Ollama-shaped) messages to OpenAI chat messages.
 24 | 
 25 |     For ``gemini``, every tool call carries a thought signature - its own, or Google's
 26 |     placeholder when another model made the call - so switching models mid-task works.
 27 |     Other servers never see Google's signatures (a conversation may move from Gemini to the
 28 |     local model at any time, and a strict server could reject the unknown field).
 29 |     """
 30 |     converted: list[dict] = []
 31 |     for message in messages:
 32 |         if message["role"] == "assistant" and message.get("tool_calls"):
 33 |             calls = []
 34 |             for call in message["tool_calls"]:
 35 |                 function = call["function"]
 36 |                 arguments = function.get("arguments", {})
 37 |                 entry = {
 38 |                     "id": call.get("id", "call_0"),
 39 |                     "type": "function",
 40 |                     "function": {
 41 |                         "name": function["name"],
 42 |                         "arguments": arguments if isinstance(arguments, str)
 43 |                         else json.dumps(arguments),
 44 |                     },
 45 |                 }
 46 |                 if gemini:                           # its own signature, else the placeholder
 47 |                     entry["extra_content"] = call.get("extra_content") or PLACEHOLDER_SIGNATURE
 48 |                 calls.append(entry)
 49 |             converted.append({"role": "assistant", "content": message.get("content") or None,
 50 |                               "tool_calls": calls})
 51 |         elif message["role"] == "tool":
 52 |             converted.append({"role": "tool", "tool_call_id": message.get("tool_call_id", ""),
 53 |                               "content": message["content"]})
 54 |         else:
 55 |             converted.append({"role": message["role"], "content": message["content"]})
 56 |     return converted
 57 | 
 58 | 
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
 59 | def _kept_call(call: dict) -> dict:
 60 |     """A tool call in our internal shape; a thought signature is kept for the next turn."""
 61 |     kept = {"id": call.get("id"), "function": {
 62 |         "name": call["function"]["name"], "arguments": call["function"]["arguments"]}}
 63 |     if "extra_content" in call:                        # must be sent back unchanged next turn
 64 |         kept["extra_content"] = call["extra_content"]
 65 |     return kept
 66 | 
 67 | 
```
- Converts a tool call from the server's shape to ours (id, name, argument text), keeping a thought signature
  (`extra_content`) so it is sent back next turn. Used by both the streaming and the one-piece path.

## `read_stream` — answers that arrive in pieces
```python
 68 | def read_stream(lines: Iterable[bytes], on_token: TokenSink | None) -> tuple[dict, dict]:
 69 |     """Assemble a streamed answer: ``data: {...}`` lines (Server-Sent Events) until ``[DONE]``.
 70 | 
 71 |     Text pieces go to ``on_token`` as they arrive. Tool calls arrive in pieces too, keyed by
 72 |     their ``index``; names and argument text are concatenated. Returns (message, usage).
 73 |     """
 74 |     text: list[str] = []
 75 |     calls: dict[int, dict] = {}
 76 |     usage: dict = {}
 77 |     for raw in lines:
 78 |         line = raw.decode("utf-8", errors="replace").strip()
 79 |         if not line.startswith("data:"):
 80 |             continue                                    # blank lines and comments
 81 |         data = line[5:].strip()
 82 |         if data == "[DONE]":
 83 |             break
 84 |         try:
 85 |             chunk = json.loads(data)
 86 |         except ValueError:
 87 |             continue
 88 |         if not isinstance(chunk, dict):
 89 |             continue
 90 |         if chunk.get("error"):
 91 |             raise LLMError(f"Server error in stream: {chunk['error']}")
 92 |         usage = chunk.get("usage") or usage             # usually only in the last chunk
 93 |         for choice in chunk.get("choices") or []:
 94 |             delta = choice.get("delta") or {}
 95 |             piece = delta.get("content")
 96 |             if piece:
 97 |                 text.append(piece)
 98 |                 if on_token:
 99 |                     on_token(piece)
100 |             for part in delta.get("tool_calls") or []:
101 |                 slot = calls.setdefault(part.get("index", len(calls)),
102 |                                         {"id": None, "function": {"name": "", "arguments": ""}})
103 |                 slot["id"] = part.get("id") or slot["id"]
104 |                 function = part.get("function") or {}
105 |                 slot["function"]["name"] += function.get("name") or ""
106 |                 slot["function"]["arguments"] += function.get("arguments") or ""
107 |                 if part.get("extra_content"):
108 |                     slot["extra_content"] = part["extra_content"]
109 |     message: dict = {"role": "assistant", "content": "".join(text)}
110 |     if calls:
111 |         message["tool_calls"] = [_kept_call(calls[i]) for i in sorted(calls)]
112 |     return message, usage
113 | 
114 | 
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

## `OpenAICompatClient`
```python
115 | class OpenAICompatClient:
116 |     """Chat client for ``/chat/completions`` servers (streaming by default)."""
117 | 
```
```python
118 |     def __init__(self, config: Config) -> None:
119 |         self._config = config
120 | 
```
- Same constructor shape as `OllamaClient`: keep the `Config`.

```python
121 |     def chat(self, messages: list[dict], tools: list[dict],
122 |              on_token: TokenSink | None = None) -> Reply:
123 |         """Send the conversation and return the model's next message."""
124 |         payload: dict = {
125 |             "model": self._config.model,
126 |             "messages": to_openai(messages, gemini=self._config.api_base == self._config.gemini_base),
127 |             "temperature": self._config.temperature,
128 |             "stream": self._config.stream,
129 |         }
130 |         if self._config.stream:                      # ask for token counts in the last chunk
131 |             payload["stream_options"] = {"include_usage": True}
132 |         if tools:                                    # some servers reject an empty list
133 |             payload["tools"] = tools
134 |         if self._config.reasoning_effort:            # e.g. "low": less hidden thinking
135 |             payload["reasoning_effort"] = self._config.reasoning_effort
136 |         headers = {"Authorization": f"Bearer {self._config.api_key}"} if self._config.api_key else {}
137 |         started = time.monotonic()
138 |         with post(self._config.api_base + "/chat/completions", payload, headers,
139 |                   timeout=self._config.request_timeout) as response:
140 |             if self._config.stream:
141 |                 try:
142 |                     message, usage = read_stream(response, on_token)
143 |                 except (TimeoutError, ConnectionError) as exc:
144 |                     raise LLMError(f"The connection broke during the answer: {exc}") from exc
145 |             else:
146 |                 try:
147 |                     body = json.load(response)
148 |                     raw = body["choices"][0]["message"]
149 |                 except (ValueError, KeyError, IndexError, TypeError) as exc:
150 |                     raise LLMError(f"The server's answer had an unexpected format ({exc!r})") from exc
151 |                 message = {"role": "assistant", "content": raw.get("content") or ""}
152 |                 if raw.get("tool_calls"):
153 |                     message["tool_calls"] = [_kept_call(call) for call in raw["tool_calls"]]
154 |                 if on_token and message["content"]:
155 |                     on_token(message["content"])     # whole text at once
156 |                 usage = body.get("usage") or {}
157 |         prompt = int(usage.get("prompt_tokens", 0))
158 |         # total can exceed prompt + completion when the model "thinks" in hidden tokens;
159 |         # those still count against quotas, so they are added to the completion side.
160 |         completion = max(int(usage.get("completion_tokens", 0)),
161 |                          int(usage.get("total_tokens", 0)) - prompt)
162 |         return Reply(
163 |             message=message,
164 |             prompt_tokens=prompt,
165 |             completion_tokens=completion,
166 |             seconds=time.monotonic() - started,
167 |         )
```
- The client sends signatures only when it talks to Google (`api_base == gemini_base`).
- Streaming is on by default (`stream`), with `stream_options` asking for token counts at the end.
- `payload` has the model name, converted messages, tools, temperature and `"stream": False` (this client reads one
  complete answer; streaming is a roadmap item).
- `tools` is added only when there are tools (some servers reject an empty list).
- `reasoning_effort` (e.g. `low`) is added only when set (`--reasoning`); it limits hidden "thinking", which costs
  time and quota tokens on thinking models.
- `headers` adds `Authorization: Bearer <key>` only when an API key is configured.
- `post(self._config.api_base + "/chat/completions", ..., timeout=request_timeout)` sends it (Gemini clients get the
  shorter `gemini_timeout`). Streaming: `read_stream` assembles the answer (a connection lost half-way becomes
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
