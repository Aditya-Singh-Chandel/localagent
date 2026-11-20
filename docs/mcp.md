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
```python
  1 | """A minimal MCP (Model Context Protocol) client for local "stdio" servers.
  2 | 
  3 | MCP is the standard Claude Code uses to plug in extra tools (databases, browsers,
  4 | issue trackers, ...). A server is a program we start; we talk to it with JSON-RPC
  5 | 2.0 messages, one JSON object per line, over its stdin/stdout:
  6 | 
  7 |     -> {"jsonrpc":"2.0","id":1,"method":"initialize","params":{...}}
  8 |     <- {"jsonrpc":"2.0","id":1,"result":{...}}
  9 |     -> {"jsonrpc":"2.0","method":"notifications/initialized"}
 10 |     -> {"jsonrpc":"2.0","id":2,"method":"tools/list"}            (each tool: name, description, inputSchema)
 11 |     -> {"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":..., "arguments":{...}}}
 12 | 
 13 | A background thread reads the server's answers and hands each one to the request
 14 | waiting for its id, so a slow or silent server can never freeze the agent (every
 15 | request has a timeout).
 16 | """
 17 | from __future__ import annotations
 18 | 
 19 | import json
 20 | import os
 21 | import shutil
 22 | import subprocess
 23 | import threading
 24 | from pathlib import Path
 25 | 
 26 | PROTOCOL_VERSION = "2025-06-18"
 27 | SCHEMA_KEYS = {"type", "description", "properties", "required", "items", "enum", "format",
 28 |                "minimum", "maximum", "minItems", "maxItems", "nullable", "anyOf"}
 29 | 
 30 | 
```
- `json` encodes messages; `os` passes environment variables to the server; `shutil.which` finds the program (on
  Windows `npx` is really `npx.cmd`, which `which` resolves); `subprocess` starts it; `threading` runs the reader and
  provides the waiting mechanism.
- `PROTOCOL_VERSION` is the MCP revision we announce. `SCHEMA_KEYS` is the set of JSON-schema words every model API
  accepts in tool definitions.

## `MCPError`
```python
 31 | class MCPError(Exception):
 32 |     """The server failed to start, answered with an error, or did not answer in time."""
 33 | 
 34 | 
```
- One error type for "could not start", "the server reported an error", "no answer in time" and "server stopped".

## `clean_schema`
```python
 35 | def clean_schema(schema: object) -> dict:
 36 |     """Keep only JSON-schema keywords that every model API accepts (Gemini is strict)."""
 37 |     if not isinstance(schema, dict):
 38 |         return {"type": "object", "properties": {}}
 39 |     cleaned: dict = {}
 40 |     for key, value in schema.items():
 41 |         if key not in SCHEMA_KEYS:
 42 |             continue                                        # e.g. "$schema", "additionalProperties"
 43 |         if key == "type" and isinstance(value, list):       # ["string", "null"] -> string + nullable
 44 |             real = [t for t in value if t != "null"]
 45 |             cleaned["type"] = real[0] if real else "string"
 46 |             if "null" in value:
 47 |                 cleaned["nullable"] = True
 48 |         elif key == "properties" and isinstance(value, dict):
 49 |             cleaned["properties"] = {name: clean_schema(sub) for name, sub in value.items()}
 50 |         elif key == "items":
 51 |             cleaned["items"] = clean_schema(value)
 52 |         elif key == "anyOf" and isinstance(value, list):
 53 |             cleaned["anyOf"] = [clean_schema(sub) for sub in value]
 54 |         else:
 55 |             cleaned[key] = value
 56 |     if "type" not in cleaned and "anyOf" not in cleaned:
 57 |         cleaned["type"] = "object" if "properties" in cleaned else "string"
 58 |     return cleaned
 59 | 
 60 | 
```
- MCP servers describe their tool arguments with full JSON Schema; Gemini rejects tool definitions containing words such
  as `$schema` or `additionalProperties`. This keeps only `SCHEMA_KEYS`, recursing into `properties`, `items` and
  `anyOf`, and rewrites a type list like `["string", "null"]` into `type: string` + `nullable: true`. A missing type is
  filled in so every definition stays valid; a non-dict schema becomes "an object with no arguments".

## `MCPServer`
```python
 61 | class MCPServer:
 62 |     """One running MCP server and the tools it offers."""
 63 | 
```
```python
 64 |     def __init__(self, name: str, command: str, args: list[str] | None = None,
 65 |                  env: dict[str, str] | None = None, cwd: Path | None = None,
 66 |                  timeout: float = 30.0) -> None:
 67 |         self.name = name
 68 |         self._argv = [shutil.which(command) or command, *(args or [])]   # finds npx.cmd etc.
 69 |         self._env = {**os.environ, **(env or {})}
 70 |         self._cwd = cwd
 71 |         self._timeout = timeout
 72 |         self._proc: subprocess.Popen | None = None
 73 |         self._next_id = 0
 74 |         self._answers: dict[int, dict] = {}
 75 |         self._ready = threading.Condition()
 76 |         self._write_lock = threading.Lock()
 77 |         self._closed = False
 78 |         self._reader: threading.Thread | None = None
 79 |         self.tools: list[dict] = []
 80 | 
 81 |     # ----- lifecycle -----------------------------------------------------------
```
- Stores the name, the command line (resolved with `which`), the environment (yours plus the server's own entries), the
  working folder (the project) and the default timeout. `_answers` holds answers by request id; `_ready` is a
  `Condition`: a lock plus "wait until something changes / notify waiters"; `_write_lock` stops two threads from mixing
  their messages; `_reader` will hold the background reading thread (so `close` can wait for it); `tools` is filled
  after the handshake.

## Lifecycle
```python
 82 |     def start(self) -> "MCPServer":
 83 |         """Launch the program, do the MCP handshake and fetch the tool list."""
 84 |         try:
 85 |             self._proc = subprocess.Popen(self._argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
 86 |                                           stderr=subprocess.DEVNULL, cwd=self._cwd, env=self._env)
 87 |         except OSError as exc:
 88 |             raise MCPError(f"cannot start MCP server '{self.name}': {exc}") from None
 89 |         self._reader = threading.Thread(target=self._read_loop, daemon=True)
 90 |         self._reader.start()
 91 |         self.request("initialize", {"protocolVersion": PROTOCOL_VERSION, "capabilities": {},
 92 |                                     "clientInfo": {"name": "localagent", "version": "0.5"}})
 93 |         self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})
 94 |         self.tools = self._list_tools()
 95 |         return self
 96 | 
```
- Starts the program with pipes for input and output (its error output is discarded so it cannot fill a pipe and
  block), starts the reader thread (kept in `_reader`), performs the handshake (our client name/version, no special capabilities), sends the
  `initialized` notification and loads the tool list. A program that cannot start becomes an `MCPError`.

```python
 97 |     def close(self) -> None:
 98 |         """Stop the server as the MCP spec advises: end its input, wait, terminate, kill."""
 99 |         self._closed = True
100 |         proc = self._proc
101 |         if proc is None:
102 |             return
103 |         with self._write_lock:                              # no message is half-written
104 |             try:
105 |                 if proc.stdin is not None:
106 |                     proc.stdin.close()                      # end of input: servers exit on it
107 |             except OSError:
108 |                 pass
109 |         try:
110 |             proc.wait(timeout=2)
111 |         except subprocess.TimeoutExpired:
112 |             proc.terminate()
113 |             try:
114 |                 proc.wait(timeout=5)
115 |             except subprocess.TimeoutExpired:
116 |                 proc.kill()
117 |                 proc.wait()
118 |         if self._reader is not None:
119 |             self._reader.join(timeout=2)                    # it stops at the end of the output
120 |             if not self._reader.is_alive() and proc.stdout is not None:
121 |                 proc.stdout.close()                         # no file handle is left open
122 | 
123 |     # ----- JSON-RPC plumbing ---------------------------------------------------
```
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
```python
124 |     def _send(self, message: dict) -> None:
125 |         if self._proc is None or self._proc.stdin is None:
126 |             raise MCPError(f"MCP server '{self.name}' is not running")
127 |         data = (json.dumps(message) + "\n").encode("utf-8")
128 |         with self._write_lock:
129 |             try:
130 |                 self._proc.stdin.write(data)
131 |                 self._proc.stdin.flush()
132 |             except (OSError, ValueError) as exc:            # ValueError: we closed the pipe
133 |                 raise MCPError(f"MCP server '{self.name}' stopped: {exc}") from None
134 | 
```
- Encodes one message as a single line of UTF-8 JSON and writes it under the write lock; a broken pipe (server gone)
  becomes an `MCPError`, and so does a pipe we closed ourselves (Python reports that as a `ValueError`).

```python
135 |     def _read_loop(self) -> None:
136 |         """Background thread: route each answer to the request waiting for it."""
137 |         assert self._proc is not None and self._proc.stdout is not None
138 |         for raw in self._proc.stdout:
139 |             try:
140 |                 message = json.loads(raw.decode("utf-8", errors="replace"))
141 |             except ValueError:
142 |                 continue                                    # not JSON (e.g. a stray log line)
143 |             if not isinstance(message, dict):
144 |                 continue
145 |             if "id" in message and ("result" in message or "error" in message):
146 |                 with self._ready:
147 |                     self._answers[message["id"]] = message
148 |                     self._ready.notify_all()
149 |             elif "id" in message and "method" in message:   # the server asks US something
150 |                 try:
151 |                     self._send({"jsonrpc": "2.0", "id": message["id"],
152 |                                 "error": {"code": -32601, "message": "not supported by localagent"}})
153 |                 except MCPError:
154 |                     pass                                    # we are shutting it down anyway
155 |         with self._ready:                                   # program ended
156 |             self._closed = True
157 |             self._ready.notify_all()
158 | 
```
- Runs in the background for the server's whole life, reading line by line. Lines that are not JSON objects are
  skipped (some servers print logs). An **answer** (has `id` and `result`/`error`) is stored and all waiters are woken.
  A **request from the server** (has `id` and `method`, e.g. `roots/list`) is answered politely with "method not
  supported" so the server is not left waiting (if we are shutting it down, that reply is simply dropped). When the
  output ends, the server is marked closed and waiters wake up.

```python
159 |     def request(self, method: str, params: dict | None = None,
160 |                 timeout: float | None = None) -> dict:
161 |         """Send one request and wait for its answer's ``result``."""
162 |         with self._ready:
163 |             self._next_id += 1
164 |             request_id = self._next_id
165 |         self._send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}})
166 |         with self._ready:
167 |             done = self._ready.wait_for(lambda: request_id in self._answers or self._closed,
168 |                                         timeout=timeout or self._timeout)
169 |             answer = self._answers.pop(request_id, None)
170 |         if answer is None:
171 |             reason = "stopped" if done else f"did not answer within {timeout or self._timeout:.0f}s"
172 |             raise MCPError(f"MCP server '{self.name}' {reason} ({method})")
173 |         if "error" in answer:
174 |             error = answer["error"]
175 |             raise MCPError(f"MCP server '{self.name}': {error.get('message', error)}")
176 |         return answer.get("result") or {}
177 | 
178 |     # ----- tools ---------------------------------------------------------------
```
- Takes the next id under the lock, sends the request, then waits (with the timeout) until the answer for that id has
  arrived or the server has closed. No answer → `MCPError` saying whether it stopped or timed out; an `error` answer
  → `MCPError` with the server's message; otherwise the `result`.

## Tools
```python
179 |     def _list_tools(self) -> list[dict]:
180 |         tools: list[dict] = []
181 |         cursor = None
182 |         while True:                                         # the list may come in pages
183 |             result = self.request("tools/list", {"cursor": cursor} if cursor else {})
184 |             tools += [t for t in result.get("tools", []) if isinstance(t, dict) and t.get("name")]
185 |             cursor = result.get("nextCursor")
186 |             if not cursor:
187 |                 return tools
188 | 
```
- Calls `tools/list` repeatedly while the server returns a `nextCursor` (pagination), keeping entries that have a name.

```python
189 |     def call_tool(self, name: str, arguments: dict, timeout: float | None = None) -> str:
190 |         """Run one tool on the server and return its result as text."""
191 |         result = self.request("tools/call", {"name": name, "arguments": arguments}, timeout)
192 |         parts = []
193 |         for item in result.get("content", []):
194 |             if item.get("type") == "text":
195 |                 parts.append(str(item.get("text", "")))
196 |             else:
197 |                 parts.append(f"[{item.get('type', 'data')} content not shown]")
198 |         text = "\n".join(parts).strip() or "(no output)"
199 |         return f"ERROR: {text}" if result.get("isError") else text
```
- Calls `tools/call`. Text parts of the result are joined; other kinds (images, audio, resources) are named but not
  included, to keep tokens down. An empty result says `(no output)`; a result flagged `isError` is returned starting
  with `ERROR:` so the agent counts it as a failure.
