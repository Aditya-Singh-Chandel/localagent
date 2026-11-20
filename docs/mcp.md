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
 26 | from . import __version__
 27 | 
 28 | PROTOCOL_VERSION = "2025-06-18"
 29 | SCHEMA_KEYS = {"type", "description", "properties", "required", "items", "enum", "format",
 30 |                "minimum", "maximum", "minItems", "maxItems", "nullable", "anyOf"}
 31 | 
 32 | 
```
- `json` encodes messages; `os` passes environment variables to the server; `shutil.which` finds the program (on
  Windows `npx` is really `npx.cmd`, which `which` resolves); `subprocess` starts it; `threading` runs the reader and
  provides the waiting mechanism; `__version__` is the version we announce to the server.
- `PROTOCOL_VERSION` is the MCP revision we announce. `SCHEMA_KEYS` is the set of JSON-schema words every model API
  accepts in tool definitions.

## `MCPError`
```python
 33 | class MCPError(Exception):
 34 |     """The server failed to start, answered with an error, or did not answer in time."""
 35 | 
 36 | 
```
- One error type for "could not start", "the server reported an error", "no answer in time" and "server stopped".

## `clean_schema`
```python
 37 | def clean_schema(schema: object) -> dict:
 38 |     """Keep only JSON-schema keywords that every model API accepts (Gemini is strict)."""
 39 |     if not isinstance(schema, dict):
 40 |         return {"type": "object", "properties": {}}
 41 |     cleaned: dict = {}
 42 |     for key, value in schema.items():
 43 |         if key not in SCHEMA_KEYS:
 44 |             continue                                        # e.g. "$schema", "additionalProperties"
 45 |         if key == "type" and isinstance(value, list):       # ["string", "null"] -> string + nullable
 46 |             real = [t for t in value if t != "null"]
 47 |             cleaned["type"] = real[0] if real else "string"
 48 |             if "null" in value:
 49 |                 cleaned["nullable"] = True
 50 |         elif key == "properties" and isinstance(value, dict):
 51 |             cleaned["properties"] = {name: clean_schema(sub) for name, sub in value.items()}
 52 |         elif key == "items":
 53 |             cleaned["items"] = clean_schema(value)
 54 |         elif key == "anyOf" and isinstance(value, list):
 55 |             cleaned["anyOf"] = [clean_schema(sub) for sub in value]
 56 |         else:
 57 |             cleaned[key] = value
 58 |     if "type" not in cleaned and "anyOf" not in cleaned:
 59 |         cleaned["type"] = "object" if "properties" in cleaned else "string"
 60 |     return cleaned
 61 | 
 62 | 
```
- MCP servers describe their tool arguments with full JSON Schema; Gemini rejects tool definitions containing words such
  as `$schema` or `additionalProperties`. This keeps only `SCHEMA_KEYS`, recursing into `properties`, `items` and
  `anyOf`, and rewrites a type list like `["string", "null"]` into `type: string` + `nullable: true`. A missing type is
  filled in so every definition stays valid; a non-dict schema becomes "an object with no arguments".

## `MCPServer`
```python
 63 | class MCPServer:
 64 |     """One running MCP server and the tools it offers."""
 65 | 
```
```python
 66 |     def __init__(self, name: str, command: str, args: list[str] | None = None,
 67 |                  env: dict[str, str] | None = None, cwd: Path | None = None,
 68 |                  timeout: float = 30.0) -> None:
 69 |         self.name = name
 70 |         self._argv = [shutil.which(command) or command, *(args or [])]   # finds npx.cmd etc.
 71 |         self._env = {**os.environ, **(env or {})}
 72 |         self._cwd = cwd
 73 |         self._timeout = timeout
 74 |         self._proc: subprocess.Popen | None = None
 75 |         self._next_id = 0
 76 |         self._answers: dict[int, dict] = {}
 77 |         self._ready = threading.Condition()
 78 |         self._write_lock = threading.Lock()
 79 |         self._closed = False
 80 |         self._reader: threading.Thread | None = None
 81 |         self.tools: list[dict] = []
 82 | 
 83 |     # ----- lifecycle -----------------------------------------------------------
```
- Stores the name, the command line (resolved with `which`), the environment (yours plus the server's own entries), the
  working folder (the project) and the default timeout. `_answers` holds answers by request id; `_ready` is a
  `Condition`: a lock plus "wait until something changes / notify waiters"; `_write_lock` stops two threads from mixing
  their messages; `_reader` will hold the background reading thread (so `close` can wait for it); `tools` is filled
  after the handshake.

## Lifecycle
```python
 84 |     def start(self) -> "MCPServer":
 85 |         """Launch the program, do the MCP handshake and fetch the tool list."""
 86 |         try:
 87 |             self._proc = subprocess.Popen(self._argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
 88 |                                           stderr=subprocess.DEVNULL, cwd=self._cwd, env=self._env)
 89 |         except OSError as exc:
 90 |             raise MCPError(f"cannot start MCP server '{self.name}': {exc}") from None
 91 |         self._reader = threading.Thread(target=self._read_loop, daemon=True)
 92 |         self._reader.start()
 93 |         self.request("initialize", {"protocolVersion": PROTOCOL_VERSION, "capabilities": {},
 94 |                                     "clientInfo": {"name": "localagent", "version": __version__}})
 95 |         self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})
 96 |         self.tools = self._list_tools()
 97 |         return self
 98 | 
```
- Starts the program with pipes for input and output (its error output is discarded so it cannot fill a pipe and
  block), starts the reader thread (kept in `_reader`), performs the handshake (our client name/version, no special capabilities), sends the
  `initialized` notification and loads the tool list. A program that cannot start becomes an `MCPError`.

```python
 99 |     def close(self) -> None:
100 |         """Stop the server as the MCP spec advises: end its input, wait, terminate, kill."""
101 |         self._closed = True
102 |         proc = self._proc
103 |         if proc is None:
104 |             return
105 |         with self._write_lock:                              # no message is half-written
106 |             try:
107 |                 if proc.stdin is not None:
108 |                     proc.stdin.close()                      # end of input: servers exit on it
109 |             except OSError:
110 |                 pass
111 |         try:
112 |             proc.wait(timeout=2)
113 |         except subprocess.TimeoutExpired:
114 |             proc.terminate()
115 |             try:
116 |                 proc.wait(timeout=5)
117 |             except subprocess.TimeoutExpired:
118 |                 proc.kill()
119 |                 proc.wait()
120 |         if self._reader is not None:
121 |             self._reader.join(timeout=2)                    # it stops at the end of the output
122 |             if not self._reader.is_alive() and proc.stdout is not None:
123 |                 proc.stdout.close()                         # no file handle is left open
124 | 
125 |     # ----- JSON-RPC plumbing ---------------------------------------------------
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
126 |     def _send(self, message: dict) -> None:
127 |         if self._proc is None or self._proc.stdin is None:
128 |             raise MCPError(f"MCP server '{self.name}' is not running")
129 |         data = (json.dumps(message) + "\n").encode("utf-8")
130 |         with self._write_lock:
131 |             try:
132 |                 self._proc.stdin.write(data)
133 |                 self._proc.stdin.flush()
134 |             except (OSError, ValueError) as exc:            # ValueError: we closed the pipe
135 |                 raise MCPError(f"MCP server '{self.name}' stopped: {exc}") from None
136 | 
```
- Encodes one message as a single line of UTF-8 JSON and writes it under the write lock; a broken pipe (server gone)
  becomes an `MCPError`, and so does a pipe we closed ourselves (Python reports that as a `ValueError`).

```python
137 |     def _read_loop(self) -> None:
138 |         """Background thread: route each answer to the request waiting for it."""
139 |         assert self._proc is not None and self._proc.stdout is not None
140 |         for raw in self._proc.stdout:
141 |             try:
142 |                 message = json.loads(raw.decode("utf-8", errors="replace"))
143 |             except ValueError:
144 |                 continue                                    # not JSON (e.g. a stray log line)
145 |             if not isinstance(message, dict):
146 |                 continue
147 |             if "id" in message and ("result" in message or "error" in message):
148 |                 with self._ready:
149 |                     self._answers[message["id"]] = message
150 |                     self._ready.notify_all()
151 |             elif "id" in message and "method" in message:   # the server asks US something
152 |                 try:
153 |                     self._send({"jsonrpc": "2.0", "id": message["id"],
154 |                                 "error": {"code": -32601, "message": "not supported by localagent"}})
155 |                 except MCPError:
156 |                     pass                                    # we are shutting it down anyway
157 |         with self._ready:                                   # program ended
158 |             self._closed = True
159 |             self._ready.notify_all()
160 | 
```
- Runs in the background for the server's whole life, reading line by line. Lines that are not JSON objects are
  skipped (some servers print logs). An **answer** (has `id` and `result`/`error`) is stored and all waiters are woken.
  A **request from the server** (has `id` and `method`, e.g. `roots/list`) is answered politely with "method not
  supported" so the server is not left waiting (if we are shutting it down, that reply is simply dropped). When the
  output ends, the server is marked closed and waiters wake up.

```python
161 |     def request(self, method: str, params: dict | None = None,
162 |                 timeout: float | None = None) -> dict:
163 |         """Send one request and wait for its answer's ``result``."""
164 |         with self._ready:
165 |             self._next_id += 1
166 |             request_id = self._next_id
167 |         self._send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}})
168 |         with self._ready:
169 |             done = self._ready.wait_for(lambda: request_id in self._answers or self._closed,
170 |                                         timeout=timeout or self._timeout)
171 |             answer = self._answers.pop(request_id, None)
172 |         if answer is None:
173 |             reason = "stopped" if done else f"did not answer within {timeout or self._timeout:.0f}s"
174 |             raise MCPError(f"MCP server '{self.name}' {reason} ({method})")
175 |         if "error" in answer:
176 |             error = answer["error"]
177 |             raise MCPError(f"MCP server '{self.name}': {error.get('message', error)}")
178 |         return answer.get("result") or {}
179 | 
180 |     # ----- tools ---------------------------------------------------------------
```
- Takes the next id under the lock, sends the request, then waits (with the timeout) until the answer for that id has
  arrived or the server has closed. No answer → `MCPError` saying whether it stopped or timed out; an `error` answer
  → `MCPError` with the server's message; otherwise the `result`.

## Tools
```python
181 |     def _list_tools(self) -> list[dict]:
182 |         tools: list[dict] = []
183 |         cursor = None
184 |         while True:                                         # the list may come in pages
185 |             result = self.request("tools/list", {"cursor": cursor} if cursor else {})
186 |             tools += [t for t in result.get("tools", []) if isinstance(t, dict) and t.get("name")]
187 |             cursor = result.get("nextCursor")
188 |             if not cursor:
189 |                 return tools
190 | 
```
- Calls `tools/list` repeatedly while the server returns a `nextCursor` (pagination), keeping entries that have a name.

```python
191 |     def call_tool(self, name: str, arguments: dict, timeout: float | None = None) -> str:
192 |         """Run one tool on the server and return its result as text."""
193 |         result = self.request("tools/call", {"name": name, "arguments": arguments}, timeout)
194 |         parts = []
195 |         for item in result.get("content", []):
196 |             if item.get("type") == "text":
197 |                 parts.append(str(item.get("text", "")))
198 |             else:
199 |                 parts.append(f"[{item.get('type', 'data')} content not shown]")
200 |         text = "\n".join(parts).strip() or "(no output)"
201 |         return f"ERROR: {text}" if result.get("isError") else text
```
- Calls `tools/call`. Text parts of the result are joined; other kinds (images, audio, resources) are named but not
  included, to keep tokens down. An empty result says `(no output)`; a result flagged `isError` is returned starting
  with `ERROR:` so the agent counts it as a failure.
