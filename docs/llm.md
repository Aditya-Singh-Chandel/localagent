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
 10 | import time
 11 | import urllib.error
 12 | import urllib.request
 13 | from dataclasses import dataclass
 14 | from typing import Callable, Protocol
 15 | 
 16 | from .config import Config
 17 | 
 18 | TokenSink = Callable[[str], None]
 19 | 
 20 | 
```
- `json` encodes requests/decodes replies; `time` measures duration; `urllib.error` / `urllib.request` are the
  standard HTTP client; `Callable`/`Protocol` are typing helpers.
- `TokenSink = Callable[[str], None]` names the type "a function that accepts a piece of text and returns nothing"
  (used for live output).

## `LLMError`
```python
 21 | class LLMError(RuntimeError):
 22 |     """Raised when the model server cannot be reached or rejects a request.
 23 | 
 24 |     ``status`` is the HTTP status code when the server answered with an error, and
 25 |     ``None`` when it could not be reached at all (network problem or timeout).
 26 |     """
 27 | 
```
- One exception type for every model-server problem, so callers need a single `except LLMError`.

```python
 28 |     def __init__(self, message: str, status: int | None = None) -> None:
 29 |         super().__init__(message)
 30 |         self.status = status
 31 | 
 32 | 
```
- Besides the message it stores `status`: the HTTP status code when the server answered with an error (e.g. 429 "too
  many requests", 503 "overloaded", 400 "bad request"), or `None` when the server could not be reached. `resilient.py`
  uses it to decide between *retry*, *switch model* and *give up*.

## `Reply`
```python
 33 | @dataclass(frozen=True)
 34 | class Reply:
 35 |     """One model answer plus the numbers we need for cost/speed tracking."""
 36 | 
 37 |     message: dict          # {"role": "assistant", "content": ..., "tool_calls": [...]}
 38 |     prompt_tokens: int     # tokens the model had to read this call
 39 |     completion_tokens: int  # tokens the model generated this call
 40 |     seconds: float         # wall-clock time of the call
 41 | 
 42 | 
```
- An immutable record of one model answer: the `message` dict (text and/or `tool_calls`), tokens read
  (`prompt_tokens`), tokens written (`completion_tokens`), and wall-clock `seconds`.

## `ChatBackend`
```python
 43 | class ChatBackend(Protocol):
 44 |     """Anything with this ``chat`` method can drive the agent (servers, test fakes)."""
 45 | 
```
```python
 46 |     def chat(self, messages: list[dict], tools: list[dict],
 47 |              on_token: TokenSink | None = None) -> Reply: ...
 48 | 
 49 | 
```
- A `Protocol` is a structural interface: any object with this `chat` method qualifies, with no inheritance. That is
  how the real clients and the test fakes are interchangeable. `on_token` is an optional live-text callback.
- The `...` body means "no implementation here".

## `post` — shared HTTP helper
```python
 50 | def post(url: str, payload: dict, headers: dict[str, str] | None = None,
 51 |          timeout: int = 900):
 52 |     """POST JSON and return the open response; turn every failure into LLMError."""
 53 |     request = urllib.request.Request(
 54 |         url,
 55 |         data=json.dumps(payload).encode("utf-8"),
 56 |         headers={"Content-Type": "application/json", **(headers or {})},
 57 |         method="POST",
 58 |     )
 59 |     try:
 60 |         return urllib.request.urlopen(request, timeout=timeout)
 61 |     except urllib.error.HTTPError as exc:  # must come before URLError (subclass)
 62 |         detail = exc.read().decode("utf-8", errors="replace")
 63 |         exc.close()                        # release the connection
 64 |         raise LLMError(f"Server returned HTTP {exc.code}: {detail}", status=exc.code) from exc
 65 |     except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
 66 |         raise LLMError(
 67 |             f"Cannot reach the model server at {url} ({exc}). Is it running? "
 68 |             "For Ollama: open the Ollama app or run `ollama serve`."
 69 |         ) from exc
 70 | 
 71 | 
```
- Builds a JSON `POST` request (`json.dumps(payload).encode()` turns the dict into bytes; the `Content-Type` header
  announces JSON; extra headers such as `Authorization` are merged in with `**`).
- `urlopen(..., timeout=900)` sends it; the 15-minute timeout allows for slow CPU generation.
- `HTTPError` (the server answered with an error code) is caught **first** because it is a subclass of `URLError`.
  We read the server's explanation, `exc.close()` releases the connection, and raise `LLMError` carrying the
  status code.
- `URLError`/`TimeoutError`/`ConnectionError` (server not running, refused, timed out) become `LLMError` with
  advice. `from exc` keeps the original error attached for debugging.

## `OllamaClient`
```python
 72 | class OllamaClient:
 73 |     """Streams chat requests to Ollama and converts failures into LLMError."""
 74 | 
```
```python
 75 |     def __init__(self, config: Config) -> None:
 76 |         self._config = config
 77 | 
```
- Stores the `Config` privately.

```python
 78 |     def chat(self, messages: list[dict], tools: list[dict],
 79 |              on_token: TokenSink | None = None) -> Reply:
 80 |         """Send the conversation and return the model's next message."""
 81 |         payload = {
 82 |             "model": self._config.model,
 83 |             "messages": messages,
 84 |             "tools": tools,
 85 |             "stream": True,           # receive text as it is produced
 86 |             "keep_alive": "30m",      # keep the model in RAM between calls (saves load time)
 87 |             "options": {
 88 |                 "num_ctx": self._config.num_ctx,
 89 |                 "temperature": self._config.temperature,
 90 |             },
 91 |         }
 92 |         started = time.monotonic()
 93 |         text_parts: list[str] = []
 94 |         tool_calls: list[dict] = []
 95 |         final: dict = {}
 96 |         with post(self._config.host + "/api/chat", payload) as response:
 97 |             try:
 98 |                 for raw in response:          # one JSON object per line
 99 |                     if not raw.strip():
100 |                         continue
101 |                     chunk = json.loads(raw)
102 |                     if "error" in chunk:
103 |                         raise LLMError(f"Ollama error: {chunk['error']}")
104 |                     piece = chunk.get("message", {})
105 |                     text = piece.get("content") or ""
106 |                     if text:
107 |                         text_parts.append(text)
108 |                         if on_token:
109 |                             on_token(text)
110 |                     tool_calls.extend(piece.get("tool_calls") or [])
111 |                     if chunk.get("done"):
112 |                         final = chunk         # last line carries the token counts
113 |             except (TimeoutError, ConnectionError) as exc:
114 |                 raise LLMError(f"Connection to Ollama was lost: {exc}") from exc
115 |         message: dict = {"role": "assistant", "content": "".join(text_parts)}
116 |         if tool_calls:
117 |             message["tool_calls"] = tool_calls
118 |         return Reply(
119 |             message=message,
120 |             prompt_tokens=int(final.get("prompt_eval_count", 0)),
121 |             completion_tokens=int(final.get("eval_count", 0)),
122 |             seconds=time.monotonic() - started,
123 |         )
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
