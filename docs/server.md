# `localagent/server.py` — service mode (many sessions over HTTP)

**Role in the flow:** everything else in the project is a single user at a terminal. This file puts the same `Agent`
behind a small JSON-over-HTTP API so several people or programs can each hold their own conversation at once. It is the
first step of "Phase 5: run it as a service" in the roadmap. It uses only the standard library, like the rest of the code.

```
client ── POST /sessions ───────────────► new Session (own workspace folder + own Agent + own Meter)
client ── POST /sessions/<id>/messages ─► Session.agent.run(text) ──► answer, events, calls, tokens
client ── GET  /sessions/<id>/files ────► what the agent has produced in that session's folder
anyone ── GET  /health ─────────────────► {"status": "ok"}  (the only call that needs no token)
```

Start it: `python -m localagent.server --token SECRET --backend openai --api-base http://127.0.0.1:8080/v1 --model qwen3-8b`
(add `--auto-approve` only on a locked-down machine; without it, sessions are never allowed to *run* code - they can still
write and verify files).

## Module header
```python
  1 | """HTTP service mode: many independent agent sessions behind a small JSON API.
  2 | 
  3 |     POST /sessions                     -> {"id": "..."}              create a session
  4 |     POST /sessions/<id>/messages       {"text": "..."} -> answer, events, calls, tokens
  5 |     GET  /sessions/<id>/files          -> {"files": [...]}           what the agent produced
  6 |     GET  /health                       -> {"status": "ok"}           (no token needed)
  7 | 
  8 | Every session gets its own workspace folder, so sessions cannot see each other's files.
  9 | When a token is configured, every call except /health needs ``Authorization: Bearer <token>``.
 10 | """
 11 | from __future__ import annotations
 12 | 
 13 | import argparse
 14 | import hmac
 15 | import json
 16 | import os
 17 | import threading
 18 | import time
 19 | import uuid
 20 | from dataclasses import replace
 21 | from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
 22 | from pathlib import Path
 23 | from typing import Callable
 24 | 
 25 | from .agent import Agent
 26 | from .backends import make_backend
 27 | from .cli import add_model_flags, model_flags
 28 | from .config import Config, gemini_history, overlay
 29 | from .llm import ChatBackend, LLMError
 30 | from .meter import Meter
 31 | from .sandbox import walk_files
 32 | from .tools import ToolBox
 33 | 
 34 | MAX_BODY = 1_000_000
 35 | MAX_SESSIONS = 20          # sessions alive at once
 36 | SESSION_TTL = 3600.0       # seconds of inactivity before a session is dropped
 37 | 
 38 | 
```
- The docstring is the API reference. `hmac.compare_digest` compares tokens in constant time; `threading` provides the
  locks; `uuid` makes session ids; `ThreadingHTTPServer` handles each request in its own thread so one slow model call
  does not block other sessions. `os` reads `LOCALAGENT_TOKEN`. `MAX_BODY` caps a request body at 1 MB. `MAX_SESSIONS` (20) and `SESSION_TTL`
  (3600 s) are the default limits; `time` supplies the monotonic clock used for expiry. The shared flags
  (`add_model_flags`, `model_flags`) come from `cli.py`, the shared rules (`overlay`, `gemini_history`) from `config.py`.

## `TooManySessions`
```python
 39 | class TooManySessions(Exception):
 40 |     """Raised by ``SessionManager.create`` when the server is full."""
 41 | 
 42 | 
```
- A tiny exception raised when the server already holds the maximum number of live sessions. The handler turns it
  into HTTP **429** ("too many requests"), so a flood of `POST /sessions` cannot exhaust memory or disk.

## `Session`
```python
 43 | class Session:
 44 |     """One agent with its own workspace; a lock keeps its requests one at a time."""
 45 | 
```
```python
 46 |     def __init__(self, config: Config, backend: ChatBackend) -> None:
 47 |         self.meter = Meter()
 48 |         self.toolbox = ToolBox(config, lambda _a: config.auto_approve)
 49 |         self.agent = Agent(config, backend, self.toolbox, self.meter)
 50 |         self.workspace = config.workspace
 51 |         self.lock = threading.Lock()
 52 |         self.last_used = 0.0                      # set by the manager on every access
 53 | 
 54 | 
```
- A session is one `Agent` with its own `Meter` and `ToolBox` (kept as `toolbox` so it can be closed). The toolbox's approval callback returns
  `config.auto_approve`, so running code is allowed only if the server was started with `--auto-approve`.
- `last_used` is stamped by the manager every time the session is touched (used for expiry).
- `lock` makes requests to the *same* session run one at a time (an agent's history is not thread-safe); different
  sessions run in parallel.

## `SessionManager`
```python
 55 | class SessionManager:
 56 |     """Creates sessions and finds them by id."""
 57 | 
```
```python
 58 |     def __init__(self, config: Config, root: Path,
 59 |                  backend_factory: Callable[[Config], ChatBackend] = make_backend,
 60 |                  max_sessions: int = MAX_SESSIONS, ttl: float = SESSION_TTL,
 61 |                  clock: Callable[[], float] = time.monotonic) -> None:
 62 |         self._config = config
 63 |         self._root = root
 64 |         self._factory = backend_factory
 65 |         self._max = max_sessions
 66 |         self._ttl = ttl
 67 |         self._clock = clock
 68 |         self._sessions: dict[str, Session] = {}
 69 |         self._guard = threading.Lock()
 70 | 
```
- Holds the base config, the folder under which each session gets a sub-folder, and a `backend_factory` (default
  `make_backend`; tests pass a fake). `_guard` protects the dictionary of sessions. `max_sessions` and `ttl` are the
  limits; `clock` is injectable so tests can move time without waiting.

```python
 71 |     def create(self) -> str:
 72 |         """Start a new session in its own folder and return its id (raises when full)."""
 73 |         with self._guard:
 74 |             self._expire()
 75 |             if len(self._sessions) >= self._max:
 76 |                 raise TooManySessions(f"limit of {self._max} sessions reached")
 77 |             session_id = uuid.uuid4().hex[:12]
 78 |             config = replace(self._config, workspace=self._root / session_id)
 79 |             session = Session(config, self._factory(config))
 80 |             session.last_used = self._clock()
 81 |             self._sessions[session_id] = session
 82 |         return session_id
 83 | 
```
- Under the guard it first drops expired sessions, then refuses (`TooManySessions`) if the limit is reached.
- Makes a 12-character random id, copies the config with `workspace = <root>/<id>` (this is the isolation: the sandbox
  in `sandbox.py` confines each agent to its own folder), builds the backend and session, stamps `last_used`, stores it and returns the id.

```python
 84 |     def _expire(self) -> None:
 85 |         """Forget sessions idle longer than the TTL (caller holds the guard). Files stay on disk."""
 86 |         now = self._clock()
 87 |         for key in [k for k, v in self._sessions.items() if now - v.last_used > self._ttl]:
 88 |             self._sessions.pop(key).toolbox.close()     # also stops its sandbox container
 89 | 
```
- Deletes every session whose idle time (`now - last_used`) exceeds the TTL, closing its toolbox (which stops its
  sandbox container, if any). Only the in-memory object goes; the session's
  files stay on disk (delete the folder yourself if you want the space back).

```python
 90 |     def get(self, session_id: str) -> Session | None:
 91 |         """Look a session up; ``None`` when the id is unknown."""
 92 |         with self._guard:
 93 |             self._expire()
 94 |             session = self._sessions.get(session_id)
 95 |             if session is not None:
 96 |                 session.last_used = self._clock()
 97 |             return session
 98 | 
```
- Drops expired sessions, then returns the session (renewing its `last_used`, so active conversations never expire) or
  `None` for an unknown or expired id.

```python
 99 |     def close_all(self) -> None:
100 |         """Close every live session (stops their sandbox containers); used at shutdown."""
101 |         with self._guard:
102 |             sessions, self._sessions = list(self._sessions.values()), {}
103 |         for session in sessions:
104 |             session.toolbox.close()
105 | 
106 | 
```
- Empties the session table under the guard, then closes each session's toolbox outside it (closing can take a few
  seconds per session; holding the guard that long would block other requests). Closing stops each sandbox
  container and MCP server. Used when the server stops.

## The request handler
```python
107 | def make_handler(manager: SessionManager, token: str) -> type[BaseHTTPRequestHandler]:
108 |     """Build the request-handler class bound to one manager and token."""
109 | 
110 |     class Handler(BaseHTTPRequestHandler):
111 |         def log_message(self, format: str, *args: object) -> None:
112 |             """Silence the default per-request console logging."""
113 | 
114 |         def _send(self, status: int, payload: dict) -> None:
115 |             body = json.dumps(payload).encode("utf-8")
116 |             self.send_response(status)
117 |             self.send_header("Content-Type", "application/json")
118 |             self.send_header("Content-Length", str(len(body)))
119 |             self.end_headers()
120 |             self.wfile.write(body)
121 | 
122 |         def _authorized(self) -> bool:
123 |             if not token:
124 |                 return True
125 |             given = self.headers.get("Authorization", "")
126 |             return hmac.compare_digest(given, f"Bearer {token}")
127 | 
128 |         def _read_json(self) -> dict | None:
129 |             try:
130 |                 length = int(self.headers.get("Content-Length", "0"))
131 |                 if length > MAX_BODY:
132 |                     return None
133 |                 value = json.loads(self.rfile.read(length) or b"{}")
134 |             except (ValueError, OSError):
135 |                 return None
136 |             return value if isinstance(value, dict) else None
137 | 
138 |         def do_GET(self) -> None:
139 |             parts = self.path.strip("/").split("/")
140 |             if parts == ["health"]:
141 |                 self._send(200, {"status": "ok"})
142 |             elif not self._authorized():
143 |                 self._send(401, {"error": "unauthorized"})
144 |             elif len(parts) == 3 and parts[0] == "sessions" and parts[2] == "files":
145 |                 session = manager.get(parts[1])
146 |                 if session is None:
147 |                     self._send(404, {"error": "unknown session"})
148 |                     return
149 |                 files = [p.relative_to(session.workspace).as_posix()
150 |                          for p in walk_files(session.workspace)]
151 |                 self._send(200, {"files": files})
152 |             else:
153 |                 self._send(404, {"error": "not found"})
154 | 
155 |         def do_POST(self) -> None:
156 |             parts = self.path.strip("/").split("/")
157 |             if not self._authorized():
158 |                 self._send(401, {"error": "unauthorized"})
159 |             elif parts == ["sessions"]:
160 |                 try:
161 |                     self._send(201, {"id": manager.create()})
162 |                 except TooManySessions as exc:
163 |                     self._send(429, {"error": str(exc)})
164 |             elif len(parts) == 3 and parts[0] == "sessions" and parts[2] == "messages":
165 |                 self._message(parts[1])
166 |             else:
167 |                 self._send(404, {"error": "not found"})
168 | 
169 |         def _message(self, session_id: str) -> None:
170 |             session = manager.get(session_id)
171 |             body = self._read_json()
172 |             if session is None:
173 |                 self._send(404, {"error": "unknown session"})
174 |             elif body is None or not isinstance(body.get("text"), str) or not body["text"].strip():
175 |                 self._send(400, {"error": "body must be JSON like {\"text\": \"...\"}"})
176 |             else:
177 |                 events: list[str] = []
178 |                 with session.lock:                      # one request at a time per session
179 |                     try:
180 |                         answer = session.agent.run(body["text"], on_event=events.append)
181 |                     except LLMError as exc:
182 |                         self._send(502, {"error": f"model error: {exc}"})
183 |                         return
184 |                 self._send(200, {"answer": answer, "events": events,
185 |                                  "calls": session.meter.calls,
186 |                                  "tokens": session.meter.prompt_tokens + session.meter.completion_tokens})
187 | 
188 |     return Handler
189 | 
190 | 
```
- `make_handler` builds a handler *class* bound to one manager and token (the standard-library server instantiates the
  class per request). Inside it:
  * `log_message` is silenced so the console is not flooded.
  * `_send` writes a JSON response with the right headers.
  * `_authorized` is true when no token is configured or the `Authorization` header equals `Bearer <token>`.
  * `_read_json` reads the body (refusing > 1 MB or invalid JSON) and returns a dict or `None`.
  * `do_GET`: `/health` is answered first, before authentication. Everything else needs the token (401 otherwise).
    `/sessions/<id>/files` lists the session's files (via `walk_files`, skipping cache folders); unknown session → 404.
  * `do_POST`: unauthorised → 401; `/sessions` creates a session (201, or **429** when the server is full); `/sessions/<id>/messages` calls `_message`;
    anything else → 404.
  * `_message`: unknown session → 404; a missing/blank `text` → 400; otherwise, holding the session lock, it runs the
    agent and collects its progress lines. A model-server problem (`LLMError`) becomes 502. Success returns the answer,
    the events (`-> tool args` lines), the session's model-call count and total tokens.

## `build_server` and `main`
```python
191 | def build_server(manager: SessionManager, host: str, port: int, token: str) -> ThreadingHTTPServer:
192 |     """Create (but do not start) the HTTP server; port 0 picks a free port."""
193 |     return ThreadingHTTPServer((host, port), make_handler(manager, token))
194 | 
195 | 
```
- Creates the HTTP server without starting it. Port `0` asks the OS for a free port (used by the tests).

```python
196 | def main(argv: list[str] | None = None) -> int:
197 |     """Command line: ``python -m localagent.server --token SECRET --backend openai ...``."""
198 |     parser = argparse.ArgumentParser(prog="localagent.server")
199 |     parser.add_argument("--host", default="127.0.0.1")
200 |     parser.add_argument("--port", type=int, default=8000)
201 |     parser.add_argument("--token", default=os.environ.get("LOCALAGENT_TOKEN", ""),
202 |                         help="require this bearer token (default: $LOCALAGENT_TOKEN)")
203 |     parser.add_argument("--root", default="sessions", help="folder holding one workspace per session")
204 |     add_model_flags(parser)
205 |     parser.add_argument("--max-sessions", type=int, default=MAX_SESSIONS)
206 |     parser.add_argument("--session-ttl", type=float, default=SESSION_TTL,
207 |                         help="seconds of inactivity before a session is dropped")
208 |     parser.add_argument("--auto-approve", action="store_true",
209 |                         help="let sessions run code without asking (only on a locked-down host)")
210 |     args = parser.parse_args(argv)
211 |     config = overlay(Config.from_env(), **model_flags(args), auto_approve=args.auto_approve)
212 |     config = gemini_history(config)                          # the chat's history budget too
213 |     manager = SessionManager(config, Path(args.root), max_sessions=args.max_sessions,
214 |                              ttl=args.session_ttl)
215 |     server = build_server(manager, args.host, args.port, args.token)
216 |     print(f"localagent server on http://{args.host}:{server.server_port} "
217 |           f"(token {'required' if args.token else 'NOT set'})")
218 |     try:
219 |         server.serve_forever()
220 |     except KeyboardInterrupt:
221 |         pass
222 |     finally:
223 |         server.server_close()
224 |         manager.close_all()                    # no sandbox container outlives the server
225 |     return 0
226 | 
227 | 
```
- Command-line entry (the shared `--backend/--model/--api-base/--sandbox`, also `--max-sessions`, `--session-ttl`; `--token` defaults to the
  `LOCALAGENT_TOKEN` environment variable so the token need not appear in a command line): host (default `127.0.0.1`, i.e. this machine only), port, token, session root folder, backend,
  model, API base and `--auto-approve`. It overlays those on the environment config, prints the address and whether a
  token is required, and serves until Ctrl+C.
- For Gemini it raises the history budget to 32K tokens with `gemini_history` — the same rule as the chat. Without it, every session used the 8K budget meant for local models and
  long conversations were summarised (an extra model call each time) far earlier than needed.
- When it stops (Ctrl+C), it releases the port (`server_close`) and closes every live session (`close_all`), so no
  sandbox container is left running. Found in the final verification: before, each session started with
  `--sandbox docker` left its container behind. (A process killed outright cannot clean up; then remove leftovers with
  `docker rm -f localagent-...`.)
- Each reply carries `answer`, `events` (the progress lines), `calls` and `tokens` (the session's totals so far).

```python
228 | if __name__ == "__main__":
229 |     raise SystemExit(main())
```
- Standard script entry point (`python -m localagent.server`).

## Security notes (read before exposing it)
* Always set `--token` and keep the default `127.0.0.1` unless it sits behind HTTPS and a firewall; this server speaks
  plain HTTP.
* Sessions get separate folders. Code that *runs* (`--auto-approve`) executes with the server's user rights unless you
  add `--sandbox docker`, which gives every session its own locked-down container. Use that for anyone you do not
  fully trust.
* Sessions are capped (`--max-sessions`, default 20) and expire after `--session-ttl` seconds idle (default 3600). There
  is still no per-client request-rate limit, no HTTPS and no persistence across restarts (roadmap).
