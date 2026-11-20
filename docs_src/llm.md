# `localagent/llm.py` — talking to the local model

**Role in the flow:** the network layer for Ollama. It sends the conversation plus tool definitions to
`http://localhost:11434/api/chat`, **streams** the answer back (so you see text as it is produced — important on a
slow CPU), and returns a `Reply` with token counts. Only `urllib` is used, so nothing extra to install.

**Swappable:** `agent.py` depends on the `ChatBackend` protocol, not on `OllamaClient`; see `openai_compat.md` and
`backends.md` for the second implementation.

## Module header
@@ localagent/llm.py header @@
- `json` encodes requests/decodes replies; `socket` recognises a failed name lookup and opens quick test connections;
  `time` measures duration; `urllib.error` / `urllib.request` are the standard HTTP client; `urlsplit` takes URLs
  apart; `Callable`/`Protocol` are typing helpers.
- `TokenSink = Callable[[str], None]` names the type "a function that accepts a piece of text and returns nothing"
  (used for live output).
- `REMOVED` replaces an old tool output and `SHORTENED` an old tool call's long argument when the history must shrink
  (see `fit_history`). `LOCAL_HOSTS` are the names that mean "this computer" (`host.docker.internal` is this computer
  as seen from inside a container).

## `LLMError`
@@ localagent/llm.py LLMError @@
- One exception type for every model-server problem, so callers need a single `except LLMError`.

@@ localagent/llm.py LLMError.__init__ @@
- Besides the message it stores `status`: the HTTP status code when the server answered with an error (e.g. 429 "too
  many requests", 503 "overloaded", 400 "bad request"), or `None` when the server could not be reached. `resilient.py`
  uses it to decide between *retry*, *switch model* and *give up*.
- `dns` is True when the server's *name* could not be looked up. Without internet that is what happens first, so
  `resilient.py` treats it as "offline" at once instead of retrying three times.
- `final` is True when retrying cannot help, e.g. "no local model is installed": the model chain moves on immediately.

## Helpers for the network and the history
@@ localagent/llm.py is_local_url @@
- True for addresses on this computer, e.g. `http://127.0.0.1:8080/v1`. Used to pick the right advice when a server
  cannot be reached.

@@ localagent/llm.py reachable @@
- Opens (and immediately closes) a plain network connection to the server of a URL, giving up after 3 seconds. It sends
  no request, so it costs no quota; `resilient.py` uses it to notice that the internet is back (or still gone).
  Port 443 is assumed for `https`, 80 for `http`.

@@ localagent/llm.py message_size @@
- How many characters one message adds to the prompt: its text **plus the arguments of its tool calls**. Before 0.6
  only the text was counted, so a `write_file` of a 300-line file (all of it in the arguments) counted as almost
  nothing and a small local context could overflow without the agent noticing (measured: 50 KB of arguments counted as
  3 KB).

@@ localagent/llm.py _shorten_call @@
- Makes a copy of an old tool call in which every argument longer than 200 characters (file contents, edits) becomes
  `SHORTENED`; short ones such as `path` stay, so the history still says what was done to which file. Arguments sent as
  a JSON string are parsed first. The thought signature (`extra_content`) is left out of the copy because it belonged to
  the original arguments; the Gemini client then adds Google's placeholder.

@@ localagent/llm.py fit_history @@
- Returns a **copy** of the conversation that fits `budget` characters, losing the cheapest things first:
  1. old tool outputs become `REMOVED` (the model already acted on them);
  2. old tool calls are shortened (`_shorten_call`);
  3. only if that is still not enough, the oldest whole exchanges are dropped — always from one user message to the
     next, so every remaining tool result still follows the call that asked for it (APIs reject the conversation
     otherwise).
- The system prompt (index 0) and the newest `keep_last` (4) messages are never touched. Used in three places: the
  agent's own trimming, the request that asks for a compaction summary (which must fit to be sent at all), and the
  local model, which fits a conversation built for Gemini's 32K budget into its 8K window.

@@ localagent/llm.py _reach_hint @@
- The advice added to "Cannot reach ...": for an address on this computer, how to start the local model server; for a
  remote one (Gemini), to check the internet or switch to the local model. (It used to recommend starting *Ollama*
  even when Gemini was unreachable.)

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
- `urlopen(..., timeout=...)` sends it. The default 15 minutes allows for slow CPU generation; Gemini clients pass a
  shorter `gemini_timeout` (2 minutes) so a stalled connection fails sooner.
- `HTTPError` (the server answered with an error code) is caught **first** because it is a subclass of `URLError`.
  We read the server's explanation, `exc.close()` releases the connection, and raise `LLMError` carrying the
  status code.
- `URLError`/`TimeoutError`/`ConnectionError` (server not running, refused, timed out) become `LLMError` with
  advice from `_reach_hint`. When the underlying reason is a `socket.gaierror` (the name lookup failed), `dns=True` is
  set. `from exc` keeps the original error attached for debugging.

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
