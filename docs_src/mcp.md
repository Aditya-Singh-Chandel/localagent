# `localagent/mcp.py` — plugging in external tools with MCP

**What MCP is:** the *Model Context Protocol* is the open standard Claude Code (and many other tools) use to add
abilities without changing the agent: a small program (an "MCP server") offers tools — read a database, control a
browser, search issues — and the agent can call them. Hundreds of ready-made servers exist (many start with
`npx -y @modelcontextprotocol/...`).

**How to use one here:** add it to a settings file (see `settings.md`), e.g.

```json
{"mcpServers": {"files": {"command": "npx",
                          "args": ["-y", "@modelcontextprotocol/server-filesystem", "."]}}}
```

When the agent starts, each server is launched; its tools appear to the model as `mcp__<server>__<tool>`; the banner
lists how many tools each server added (or why it was skipped). Calling one asks you first, unless a rule such as
`"allow": ["mcp__files__*"]` allows it. The servers are stopped when you quit.

**How it works:** we start the program and talk to it through its standard input/output, one JSON-RPC 2.0 message per
line. Handshake: `initialize` → `notifications/initialized`; then `tools/list` (may come in pages) and `tools/call`.
A background thread reads every answer and hands it to the request waiting for that `id`, so a stuck server can never
freeze the agent — every request has a timeout.

## Module header
@@ localagent/mcp.py header @@
- `json` encodes messages; `os` passes environment variables to the server; `shutil.which` finds the program (on
  Windows `npx` is really `npx.cmd`, which `which` resolves); `subprocess` starts it; `threading` runs the reader and
  provides the waiting mechanism; `__version__` is the version we announce to the server.
- `PROTOCOL_VERSION` is the MCP revision we announce. `SCHEMA_KEYS` is the set of JSON-schema words every model API
  accepts in tool definitions.

## `MCPError`
@@ localagent/mcp.py MCPError @@
- One error type for "could not start", "the server reported an error", "no answer in time" and "server stopped".

## `clean_schema`
@@ localagent/mcp.py clean_schema @@
- MCP servers describe their tool arguments with full JSON Schema; Gemini rejects tool definitions containing words such
  as `$schema` or `additionalProperties`. This keeps only `SCHEMA_KEYS`, recursing into `properties`, `items` and
  `anyOf`, and rewrites a type list like `["string", "null"]` into `type: string` + `nullable: true`. A missing type is
  filled in so every definition stays valid; a non-dict schema becomes "an object with no arguments".

## `MCPServer`
@@ localagent/mcp.py MCPServer @@
@@ localagent/mcp.py MCPServer.__init__ @@
- Stores the name, the command line (resolved with `which`), the environment (yours plus the server's own entries), the
  working folder (the project) and the default timeout. `_answers` holds answers by request id; `_ready` is a
  `Condition`: a lock plus "wait until something changes / notify waiters"; `_write_lock` stops two threads from mixing
  their messages; `_reader` will hold the background reading thread (so `close` can wait for it); `tools` is filled
  after the handshake.

## Lifecycle
@@ localagent/mcp.py MCPServer.start @@
- Starts the program with pipes for input and output (its error output is discarded so it cannot fill a pipe and
  block), starts the reader thread (kept in `_reader`), performs the handshake (our client name/version, no special capabilities), sends the
  `initialized` notification and loads the tool list. A program that cannot start becomes an `MCPError`.

@@ localagent/mcp.py MCPServer.close @@
- Shuts the server down the way the MCP specification advises for stdio servers: first **close its input** (under
  the write lock, so no message is cut in half) — a server exits when its input ends — and wait up to 2 seconds; if
  it is still running, ask the system to end it (`terminate`), and force it (`kill`) after 5 more seconds.
- Closing the input first matters on Windows: an `npx` server runs as a chain of programs (`cmd.exe` running
  `npx.cmd`, which starts `node.exe`), and `terminate` only reaches the first of them, while the end of input reaches
  the real server, which inherited that pipe.
- Finally it waits for the reader thread to see the end of the output and closes that pipe too. Found in the final
  verification: before this, each closed server left two open file handles behind (Python printed `ResourceWarning:
  unclosed file` during the tests) — harmless for one chat, but a long-running `server.py` closes a toolbox for every
  expired session. If the reader is still blocked (a stray child program keeps the output open), the pipe is left
  alone rather than risking a hang.

## JSON-RPC plumbing
@@ localagent/mcp.py MCPServer._send @@
- Encodes one message as a single line of UTF-8 JSON and writes it under the write lock; a broken pipe (server gone)
  becomes an `MCPError`, and so does a pipe we closed ourselves (Python reports that as a `ValueError`).

@@ localagent/mcp.py MCPServer._read_loop @@
- Runs in the background for the server's whole life, reading line by line. Lines that are not JSON objects are
  skipped (some servers print logs). An **answer** (has `id` and `result`/`error`) is stored and all waiters are woken.
  A **request from the server** (has `id` and `method`, e.g. `roots/list`) is answered politely with "method not
  supported" so the server is not left waiting (if we are shutting it down, that reply is simply dropped). When the
  output ends, the server is marked closed and waiters wake up.

@@ localagent/mcp.py MCPServer.request @@
- Takes the next id under the lock, sends the request, then waits (with the timeout) until the answer for that id has
  arrived or the server has closed. No answer → `MCPError` saying whether it stopped or timed out; an `error` answer
  → `MCPError` with the server's message; otherwise the `result`.

## Tools
@@ localagent/mcp.py MCPServer._list_tools @@
- Calls `tools/list` repeatedly while the server returns a `nextCursor` (pagination), keeping entries that have a name.

@@ localagent/mcp.py MCPServer.call_tool @@
- Calls `tools/call`. Text parts of the result are joined; other kinds (images, audio, resources) are named but not
  included, to keep tokens down. An empty result says `(no output)`; a result flagged `isError` is returned starting
  with `ERROR:` so the agent counts it as a failure.
