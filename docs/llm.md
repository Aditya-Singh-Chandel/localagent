# `localagent/llm.py` — talking to the local model

**Role in the flow:** the network layer for Ollama. It sends the conversation plus tool definitions to
`http://localhost:11434/api/chat`, **streams** the answer back (so you see text as it is produced — important on a
slow CPU), and returns a `Reply` with token counts. Only `urllib` is used, so nothing extra to install.

**Swappable:** `agent.py` depends on the `ChatBackend` protocol, not on `OllamaClient`; see `openai_compat.md` and
`backends.md` for the second implementation.

## Module header
```python
  1 | """Shared model-server plumbing plus the streaming Ollama client.
  2 | 
  3 | Ollama exposes ``POST /api/chat``. We send the conversation plus the tool
  4 | definitions and read back a stream of small JSON lines; text pieces are shown
  5 | to the user as they arrive and joined into one assistant message at the end.
  6 | """
  7 | from __future__ import annotations
  8 | 
  9 | import json
 10 | import socket
 11 | import time
 12 | import urllib.error
 13 | import urllib.request
 14 | from dataclasses import dataclass
 15 | from typing import Callable, Protocol
 16 | from urllib.parse import urlsplit
 17 | 
 18 | from .config import Config
 19 | 
 20 | TokenSink = Callable[[str], None]
 21 | REMOVED = "[old tool output removed to save context]"
 22 | SHORTENED = "[removed to save context]"
 23 | LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "0.0.0.0", "host.docker.internal"})
 24 | 
 25 | 
```
- `json` encodes requests/decodes replies; `socket` recognises a failed name lookup and opens quick test connections;
  `time` measures duration; `urllib.error` / `urllib.request` are the standard HTTP client; `urlsplit` takes URLs
  apart; `Callable`/`Protocol` are typing helpers.
- `TokenSink = Callable[[str], None]` names the type "a function that accepts a piece of text and returns nothing"
  (used for live output).
- `REMOVED` replaces an old tool output and `SHORTENED` an old tool call's long argument when the history must shrink
  (see `fit_history`). `LOCAL_HOSTS` are the names that mean "this computer" (`host.docker.internal` is this computer
  as seen from inside a container).

## `LLMError`
```python
 26 | class LLMError(RuntimeError):
 27 |     """Raised when the model server cannot be reached or rejects a request.
 28 | 
 29 |     ``status`` is the HTTP status code when the server answered with an error, and
 30 |     ``None`` when it could not be reached at all (network problem or timeout). ``dns``
 31 |     is True when even the server's name could not be looked up - almost always: no internet.
 32 |     ``final`` is True when retrying cannot help (for example: no local model is installed).
 33 |     """
 34 | 
```
- One exception type for every model-server problem, so callers need a single `except LLMError`.

```python
 35 |     def __init__(self, message: str, status: int | None = None, dns: bool = False,
 36 |                  final: bool = False) -> None:
 37 |         super().__init__(message)
 38 |         self.status = status
 39 |         self.dns = dns
 40 |         self.final = final
 41 | 
 42 | 
```
- Besides the message it stores `status`: the HTTP status code when the server answered with an error (e.g. 429 "too
  many requests", 503 "overloaded", 400 "bad request"), or `None` when the server could not be reached. `resilient.py`
  uses it to decide between *retry*, *switch model* and *give up*.
- `dns` is True when the server's *name* could not be looked up. Without internet that is what happens first, so
  `resilient.py` treats it as "offline" at once instead of retrying three times.
- `final` is True when retrying cannot help, e.g. "no local model is installed": the model chain moves on immediately.

## Helpers for the network and the history
```python
 43 | def is_local_url(url: str) -> bool:
 44 |     """True when ``url`` points at this computer (a local model server)."""
 45 |     return (urlsplit(url).hostname or "") in LOCAL_HOSTS
 46 | 
 47 | 
```
- True for addresses on this computer, e.g. `http://127.0.0.1:8080/v1`. Used to pick the right advice when a server
  cannot be reached.

```python
 48 | def reachable(url: str, timeout: float = 3.0) -> bool:
 49 |     """True when a network connection to ``url``'s server opens within ``timeout`` seconds.
 50 | 
 51 |     Much cheaper than a model request, so it is used to check whether the internet is back.
 52 |     """
 53 |     parts = urlsplit(url)
 54 |     port = parts.port or (443 if parts.scheme == "https" else 80)
 55 |     try:
 56 |         with socket.create_connection((parts.hostname or "", port), timeout=timeout):
 57 |             return True
 58 |     except (OSError, ValueError):
 59 |         return False
 60 | 
 61 | 
```
- Opens (and immediately closes) a plain network connection to the server of a URL, giving up after 3 seconds. It sends
  no request, so it costs no quota; `resilient.py` uses it to notice that the internet is back (or still gone).
  Port 443 is assumed for `https`, 80 for `http`.

```python
 62 | def message_size(message: dict) -> int:
 63 |     """Characters a message adds to the prompt: its text plus its tool calls' arguments."""
 64 |     size = len(str(message.get("content") or ""))
 65 |     for call in message.get("tool_calls") or []:
 66 |         arguments = call.get("function", {}).get("arguments", "")
 67 |         size += len(arguments if isinstance(arguments, str) else json.dumps(arguments))
 68 |     return size
 69 | 
 70 | 
```
- How many characters one message adds to the prompt: its text **plus the arguments of its tool calls**. Before 0.6
  only the text was counted, so a `write_file` of a 300-line file (all of it in the arguments) counted as almost
  nothing and a small local context could overflow without the agent noticing (measured: 50 KB of arguments counted as
  3 KB).

```python
 71 | def _shorten_call(call: dict) -> dict:
 72 |     """A copy of an old tool call whose long arguments (file contents, edits) are replaced.
 73 | 
 74 |     Short ones such as ``path`` stay, so the history still says which file was written.
 75 |     The thought signature is dropped: it belonged to the original arguments.
 76 |     """
 77 |     function = call.get("function", {})
 78 |     arguments = function.get("arguments", {})
 79 |     if isinstance(arguments, str):
 80 |         try:
 81 |             arguments = json.loads(arguments)
 82 |         except ValueError:
 83 |             arguments = {}
 84 |     if not isinstance(arguments, dict):
 85 |         arguments = {}
 86 |     short = {key: value if len(json.dumps(value)) <= 200 else SHORTENED
 87 |              for key, value in arguments.items()}
 88 |     kept = {key: value for key, value in call.items() if key != "extra_content"}
 89 |     return {**kept, "function": {**function, "arguments": short}}
 90 | 
 91 | 
```
- Makes a copy of an old tool call in which every argument longer than 200 characters (file contents, edits) becomes
  `SHORTENED`; short ones such as `path` stay, so the history still says what was done to which file. Arguments sent as
  a JSON string are parsed first. The thought signature (`extra_content`) is left out of the copy because it belonged to
  the original arguments; the Gemini client then adds Google's placeholder.

```python
 92 | def fit_history(messages: list[dict], budget: int, keep_last: int = 4) -> list[dict]:
 93 |     """A copy of ``messages`` that fits ``budget`` characters (about 4 per token).
 94 | 
 95 |     Cheapest losses first: the oldest tool outputs are blanked, then the bulky arguments of
 96 |     old tool calls are shortened, and only then are the oldest whole exchanges dropped (from
 97 |     one user message to the next, so every remaining tool result still follows its call).
 98 |     The system prompt and the newest ``keep_last`` messages are never changed.
 99 |     """
100 |     result = list(messages)
101 |     size = sum(message_size(m) for m in result)
102 |     protected = max(len(result) - keep_last, 1)
103 |     for index in range(1, protected):                       # 1. old tool outputs
104 |         if size <= budget:
105 |             return result
106 |         message = result[index]
107 |         if message.get("role") == "tool" and message.get("content") != REMOVED:
108 |             size -= len(str(message.get("content") or "")) - len(REMOVED)
109 |             result[index] = {**message, "content": REMOVED}
110 |     for index in range(1, protected):                       # 2. old tool-call arguments
111 |         if size <= budget:
112 |             return result
113 |         message = result[index]
114 |         if message.get("tool_calls"):
115 |             shorter = {**message, "tool_calls": [_shorten_call(c) for c in message["tool_calls"]]}
116 |             size -= message_size(message) - message_size(shorter)
117 |             result[index] = shorter
118 |     while size > budget:                                    # 3. the oldest exchanges
119 |         starts = [i for i in range(2, len(result)) if result[i].get("role") == "user"]
120 |         if not starts:
121 |             break                                           # one huge request: nothing to drop
122 |         size -= sum(message_size(m) for m in result[1:starts[0]])
123 |         result = [result[0]] + result[starts[0]:]
124 |     return result
125 | 
126 | 
```
- Returns a **copy** of the conversation that fits `budget` characters, losing the cheapest things first:
  1. old tool outputs become `REMOVED` (the model already acted on them);
  2. old tool calls are shortened (`_shorten_call`);
  3. only if that is still not enough, the oldest whole exchanges are dropped — always from one user message to the
     next, so every remaining tool result still follows the call that asked for it (APIs reject the conversation
     otherwise).
- The system prompt (index 0) and the newest `keep_last` (4) messages are never touched. Used in three places: the
  agent's own trimming, the request that asks for a compaction summary (which must fit to be sent at all), and the
  local model, which fits a conversation built for Gemini's 32K budget into its 8K window.

```python
127 | def _reach_hint(url: str) -> str:
128 |     """What to try when ``url`` cannot be reached: start the local server, or check the internet."""
129 |     if is_local_url(url):
130 |         return ("Is the local model server running? In the chat, /model offline starts it "
131 |                 "(llama.cpp); for Ollama or LM Studio, open the app.")
132 |     return "Check your internet connection, or work offline with the local model: /model offline"
133 | 
134 | 
```
- The advice added to "Cannot reach ...": for an address on this computer, how to start the local model server; for a
  remote one (Gemini), to check the internet or switch to the local model. (It used to recommend starting *Ollama*
  even when Gemini was unreachable.)

## `Reply`
```python
135 | @dataclass(frozen=True)
136 | class Reply:
137 |     """One model answer plus the numbers we need for cost/speed tracking."""
138 | 
139 |     message: dict          # {"role": "assistant", "content": ..., "tool_calls": [...]}
140 |     prompt_tokens: int     # tokens the model had to read this call
141 |     completion_tokens: int  # tokens the model generated this call
142 |     seconds: float         # wall-clock time of the call
143 | 
144 | 
```
- An immutable record of one model answer: the `message` dict (text and/or `tool_calls`), tokens read
  (`prompt_tokens`), tokens written (`completion_tokens`), and wall-clock `seconds`.

## `ChatBackend`
```python
145 | class ChatBackend(Protocol):
146 |     """Anything with this ``chat`` method can drive the agent (servers, test fakes)."""
147 | 
```
```python
148 |     def chat(self, messages: list[dict], tools: list[dict],
149 |              on_token: TokenSink | None = None) -> Reply: ...
150 | 
151 | 
```
- A `Protocol` is a structural interface: any object with this `chat` method qualifies, with no inheritance. That is
  how the real clients and the test fakes are interchangeable. `on_token` is an optional live-text callback.
- The `...` body means "no implementation here".

## `post` — shared HTTP helper
```python
152 | def post(url: str, payload: dict, headers: dict[str, str] | None = None,
153 |          timeout: int = 900):
154 |     """POST JSON and return the open response; turn every failure into LLMError."""
155 |     request = urllib.request.Request(
156 |         url,
157 |         data=json.dumps(payload).encode("utf-8"),
158 |         headers={"Content-Type": "application/json", **(headers or {})},
159 |         method="POST",
160 |     )
161 |     try:
162 |         return urllib.request.urlopen(request, timeout=timeout)
163 |     except urllib.error.HTTPError as exc:  # must come before URLError (subclass)
164 |         detail = exc.read().decode("utf-8", errors="replace")
165 |         exc.close()                        # release the connection
166 |         raise LLMError(f"Server returned HTTP {exc.code}: {detail}", status=exc.code) from exc
167 |     except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
168 |         dns = isinstance(getattr(exc, "reason", None), socket.gaierror)   # name lookup failed
169 |         raise LLMError(f"Cannot reach the model server at {url} ({exc}). {_reach_hint(url)}",
170 |                        dns=dns) from exc
171 | 
172 | 
```
- Builds a JSON `POST` request (`json.dumps(payload).encode()` turns the dict into bytes; the `Content-Type` header
  announces JSON; extra headers such as `Authorization` are merged in with `**`).
- `urlopen(..., timeout=...)` sends it. The default 15 minutes allows for slow CPU generation; Gemini clients pass a
  shorter `gemini_timeout` (2 minutes) so a stalled connection fails sooner.
- `HTTPError` (the server answered with an error code) is caught **first** because it is a subclass of `URLError`.
  We read the server's explanation, `exc.close()` releases the connection, and raise `LLMError` carrying the
  status code.
- `URLError`/`TimeoutError`/`ConnectionError` (server not running, refused, timed out) become `LLMError` with
  advice from `_reach_hint`. When the underlying reason is a `socket.gaierror` (the name lookup failed), `dns=True` is
  set. `from exc` keeps the original error attached for debugging.

## `OllamaClient`
```python
173 | class OllamaClient:
174 |     """Streams chat requests to Ollama and converts failures into LLMError."""
175 | 
```
```python
176 |     def __init__(self, config: Config) -> None:
177 |         self._config = config
178 | 
```
- Stores the `Config` privately.

```python
179 |     def chat(self, messages: list[dict], tools: list[dict],
180 |              on_token: TokenSink | None = None) -> Reply:
181 |         """Send the conversation and return the model's next message."""
182 |         payload = {
183 |             "model": self._config.model,
184 |             "messages": messages,
185 |             "tools": tools,
186 |             "stream": True,           # receive text as it is produced
187 |             "keep_alive": "30m",      # keep the model in RAM between calls (saves load time)
188 |             "options": {
189 |                 "num_ctx": self._config.num_ctx,
190 |                 "temperature": self._config.temperature,
191 |             },
192 |         }
193 |         started = time.monotonic()
194 |         text_parts: list[str] = []
195 |         tool_calls: list[dict] = []
196 |         final: dict = {}
197 |         with post(self._config.host + "/api/chat", payload) as response:
198 |             try:
199 |                 for raw in response:          # one JSON object per line
200 |                     if not raw.strip():
201 |                         continue
202 |                     chunk = json.loads(raw)
203 |                     if "error" in chunk:
204 |                         raise LLMError(f"Ollama error: {chunk['error']}")
205 |                     piece = chunk.get("message", {})
206 |                     text = piece.get("content") or ""
207 |                     if text:
208 |                         text_parts.append(text)
209 |                         if on_token:
210 |                             on_token(text)
211 |                     tool_calls.extend(piece.get("tool_calls") or [])
212 |                     if chunk.get("done"):
213 |                         final = chunk         # last line carries the token counts
214 |             except (TimeoutError, ConnectionError) as exc:
215 |                 raise LLMError(f"Connection to Ollama was lost: {exc}") from exc
216 |         message: dict = {"role": "assistant", "content": "".join(text_parts)}
217 |         if tool_calls:
218 |             message["tool_calls"] = tool_calls
219 |         return Reply(
220 |             message=message,
221 |             prompt_tokens=int(final.get("prompt_eval_count", 0)),
222 |             completion_tokens=int(final.get("eval_count", 0)),
223 |             seconds=time.monotonic() - started,
224 |         )
```
- `payload`: the model tag, the full history, tool specs, `"stream": True` (get partial results), `"keep_alive": "30m"`
  (keep the model loaded in RAM — reloading a multi-GB model costs many seconds), and the context/temperature options.
- `started` begins the timer; `text_parts`, `tool_calls`, `final` accumulate the answer.
- `with post(...) as response:` opens the connection and guarantees it is closed.
- `for raw in response:` reads one line at a time; Ollama sends one JSON object per line. Blank lines are skipped.
- `json.loads(raw)` parses a chunk. A chunk containing `"error"` (e.g. out of memory mid-stream) raises `LLMError`.
- `piece["content"]` is a fragment of text: it is stored and, if the caller gave `on_token`, shown immediately.
- `tool_calls.extend(...)` collects tool requests, which Ollama delivers whole inside a chunk.
- `if chunk.get("done"): final = chunk` — the last line carries `prompt_eval_count` and `eval_count`.
- The inner `except (TimeoutError, ConnectionError)` covers the connection dropping mid-stream.
- After the loop, the pieces are joined into one assistant message (adding `tool_calls` only if there were some) and
  wrapped in a `Reply` with the token counts and elapsed time.
