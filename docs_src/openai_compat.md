# `localagent/openai_compat.py` — the scaling path to bigger models

**Role in the flow:** lets the *same agent* talk to any server that implements the common `/chat/completions` API:
LM Studio, llama.cpp's server, vLLM, Ollama's own `/v1` endpoint, or a hosted service. When you outgrow the laptop,
run a larger model on a GPU machine and start the agent with
`--backend openai --api-base http://that-machine:8000/v1 --model <name>`; no other code changes.

Internally the agent stores messages in Ollama's shape, so this module converts them out and back.

## Module header
@@ localagent/openai_compat.py header @@
- `PLACEHOLDER_SIGNATURE` is Google's documented stand-in thought signature (`skip_thought_signature_validator`).
- Imports `json` (arguments need to be JSON text for this API), `time`, the shared `Config`, and `Reply`, `TokenSink`
  and `post` from `llm.py` (so error handling is identical for both backends).

## `to_openai` — message conversion
@@ localagent/openai_compat.py to_openai @@
- Walks the history. An **assistant message with tool calls** needs, per call, an `id`, `"type": "function"`, and the
  arguments as a **JSON string** (Ollama uses a dict, hence `json.dumps` unless it is already a string).
  `call.get("id", "call_0")` is a safety default; the agent always assigns real ids.
- `extra_content` is copied through when present. **Gemini 3 models** attach a *thought signature* there to every tool
  call and reject the next request (HTTP 400) unless it is sent back unchanged; other servers simply ignore it.
- `fill_signatures=True` (only for requests to Gemini): a tool call **without** a signature gets the placeholder. Why:
  after a fallback switch, the history can contain tool calls made by a model that produces no signatures (e.g.
  `gemini-2.5-flash`), and a Gemini 3 model then refused the whole request with *"Function call is missing a
  thought_signature"* — a real failure seen in the eval. The placeholder was verified live: `gemini-3.5-flash-lite`
  accepted such a history and answered correctly. Local servers never get it.
- `content: ... or None`: this API wants `null`, not an empty string, when a message only contains tool calls.
- A **tool-result message** becomes `{"role": "tool", "tool_call_id": ..., "content": ...}`; the id links the result to
  the request it answers.
- Everything else (system, user, plain assistant) passes through with just role and content.

## `_kept_call`
@@ localagent/openai_compat.py _kept_call @@
- Converts a tool call from the server's shape to ours (id, name, argument text), keeping a thought signature
  (`extra_content`) so it is sent back next turn. Used by both the streaming and the one-piece path.

## `read_stream` — answers that arrive in pieces
@@ localagent/openai_compat.py read_stream @@
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
@@ localagent/openai_compat.py OpenAICompatClient @@
@@ localagent/openai_compat.py OpenAICompatClient.__init__ @@
- Same constructor shape as `OllamaClient`: keep the `Config`.

@@ localagent/openai_compat.py OpenAICompatClient.chat @@
- The client fills placeholder signatures only when it talks to Google (`api_base == gemini_base`).
- Streaming is on by default (`stream`), with `stream_options` asking for token counts at the end.
- `payload` has the model name, converted messages, tools, temperature and `"stream": False` (this client reads one
  complete answer; streaming is a roadmap item).
- `tools` is added only when there are tools (some servers reject an empty list).
- `reasoning_effort` (e.g. `low`) is added only when set (`--reasoning`); it limits hidden "thinking", which costs
  time and quota tokens on thinking models.
- `headers` adds `Authorization: Bearer <key>` only when an API key is configured.
- `post(self._config.api_base + "/chat/completions", ...)` sends it. Streaming: `read_stream` assembles the answer
  (a connection lost half-way becomes `LLMError`, so the retry/fallback logic can handle it). One-piece: `json.load`
  parses the reply.
- The answer is at `choices[0].message`. Text goes into `content`; each tool call is normalised to
  `{"id", "function": {"name", "arguments"}}` — here `arguments` stays a JSON *string*, which `agent._parse_args`
  already knows how to decode. A call's `extra_content` (thought signature) is kept on the stored message so
  `to_openai` can send it back next turn.
- `on_token(...)` is called once with the whole text, so the CLI's live display still works (all at once).
- Token counts come from the `usage` object; missing values default to 0. Thinking models report
  `total_tokens` larger than prompt + completion (the difference is hidden thinking, which still counts against quotas),
  so the completion figure is raised to `total - prompt` when that is larger. Measured on Gemini: 66 prompt + 28 answer
  = 179 total, i.e. 85 hidden tokens.
