# `localagent/llm.py` — talking to the local model

**Role in the flow:** the network layer for Ollama. It sends the conversation plus tool definitions to
`http://localhost:11434/api/chat`, **streams** the answer back (so you see text as it is produced — important on a
slow CPU), and returns a `Reply` with token counts. Only `urllib` is used, so nothing extra to install.

**Swappable:** `agent.py` depends on the `ChatBackend` protocol, not on `OllamaClient`; see `openai_compat.md` and
`backends.md` for the second implementation.

## Module header
@@ localagent/llm.py header @@
- `json` encodes requests/decodes replies; `time` measures duration; `urllib.error` / `urllib.request` are the
  standard HTTP client; `Callable`/`Protocol` are typing helpers.
- `TokenSink = Callable[[str], None]` names the type "a function that accepts a piece of text and returns nothing"
  (used for live output).

## `LLMError`
@@ localagent/llm.py LLMError @@
- One exception type for every model-server problem, so callers need a single `except LLMError`.

@@ localagent/llm.py LLMError.__init__ @@
- Besides the message it stores `status`: the HTTP status code when the server answered with an error (e.g. 429 "too
  many requests", 503 "overloaded", 400 "bad request"), or `None` when the server could not be reached. `resilient.py`
  uses it to decide between *retry*, *switch model* and *give up*.

## `Reply`
@@ localagent/llm.py Reply @@
- An immutable record of one model answer: the `message` dict (text and/or `tool_calls`), tokens read
  (`prompt_tokens`), tokens written (`completion_tokens`), and wall-clock `seconds`.

## `ChatBackend`
@@ localagent/llm.py ChatBackend @@
@@ localagent/llm.py ChatBackend.chat @@
- A `Protocol` is a structural interface: any object with this `chat` method qualifies, with no inheritance. That is
  how the real clients and the test fakes are interchangeable. `on_token` is an optional live-text callback.
- The `...` body means "no implementation here".

## `post` — shared HTTP helper
@@ localagent/llm.py post @@
- Builds a JSON `POST` request (`json.dumps(payload).encode()` turns the dict into bytes; the `Content-Type` header
  announces JSON; extra headers such as `Authorization` are merged in with `**`).
- `urlopen(..., timeout=900)` sends it; the 15-minute timeout allows for slow CPU generation.
- `HTTPError` (the server answered with an error code) is caught **first** because it is a subclass of `URLError`.
  We read the server's explanation, `exc.close()` releases the connection, and raise `LLMError` carrying the
  status code.
- `URLError`/`TimeoutError`/`ConnectionError` (server not running, refused, timed out) become `LLMError` with
  advice. `from exc` keeps the original error attached for debugging.

## `OllamaClient`
@@ localagent/llm.py OllamaClient @@
@@ localagent/llm.py OllamaClient.__init__ @@
- Stores the `Config` privately.

@@ localagent/llm.py OllamaClient.chat @@
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
