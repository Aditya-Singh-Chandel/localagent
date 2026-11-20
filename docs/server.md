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
 27 | from .cli import add_model_flags, choose_mode, model_flags
 28 | from .config import Config, gemini_history, overlay
 29 | from .llm import ChatBackend, LLMError
 30 | from .localserver import stop_local_servers
 31 | from .meter import Meter
 32 | from .sandbox import walk_files
 33 | from .tools import ToolBox
 34 | 
 35 | MAX_BODY = 1_000_000
 36 | MAX_SESSIONS = 20          # sessions alive at once
 37 | SESSION_TTL = 3600.0       # seconds of inactivity before a session is dropped
 38 | 
 39 | 
```
- The docstring is the API reference. `hmac.compare_digest` compares tokens in constant time; `threading` provides the
  locks; `uuid` makes session ids; `ThreadingHTTPServer` handles each request in its own thread so one slow model call
  does not block other sessions. `os` reads `LOCALAGENT_TOKEN`. `MAX_BODY` caps a request body at 1 MB. `MAX_SESSIONS` (20) and `SESSION_TTL`
  (3600 s) are the default limits; `time` supplies the monotonic clock used for expiry. The shared flags
  (`add_model_flags`, `model_flags`) and the mode rule (`choose_mode`) come from `cli.py`, the shared rules (`overlay`,
  `gemini_history`) from `config.py`, and `stop_local_servers` from `localserver.py`.

## `TooManySessions`
```python
 40 | class TooManySessions(Exception):
 41 |     """Raised by ``SessionManager.create`` when the server is full."""
 42 | 
 43 | 
```
- A tiny exception raised when the server already holds the maximum number of live sessions. The handler turns it
  into HTTP **429** ("too many requests"), so a flood of `POST /sessions` cannot exhaust memory or disk.

## `Session`
```python
 44 | class Session:
 45 |     """One agent with its own workspace; a lock keeps its requests one at a time."""
 46 | 
```
```python
 47 |     def __init__(self, config: Config, backend: ChatBackend) -> None:
 48 |         self.meter = Meter()
 49 |         self.toolbox = ToolBox(config, lambda _a: config.auto_approve)
 50 |         self.agent = Agent(config, backend, self.toolbox, self.meter)
 51 |         self.workspace = config.workspace
 52 |         self.lock = threading.Lock()
 53 |         self.last_used = 0.0                      # set by the manager on every access
 54 | 
 55 | 
```
- A session is one `Agent` with its own `Meter` and `ToolBox` (kept as `toolbox` so it can be closed). The toolbox's approval callback returns
  `config.auto_approve`, so running code is allowed only if the server was started with `--auto-approve`.
- `last_used` is stamped by the manager every time the session is touched (used for expiry).
- `lock` makes requests to the *same* session run one at a time (an agent's history is not thread-safe); different
  sessions run in parallel.

## `SessionManager`
```python
 56 | class SessionManager:
 57 |     """Creates sessions and finds them by id."""
 58 | 
```
```python
 59 |     def __init__(self, config: Config, root: Path,
 60 |                  backend_factory: Callable[[Config], ChatBackend] = make_backend,
 61 |                  max_sessions: int = MAX_SESSIONS, ttl: float = SESSION_TTL,
 62 |                  clock: Callable[[], float] = time.monotonic) -> None:
 63 |         self._config = config
 64 |         self._root = root
 65 |         self._factory = backend_factory
 66 |         self._max = max_sessions
 67 |         self._ttl = ttl
 68 |         self._clock = clock
 69 |         self._sessions: dict[str, Session] = {}
 70 |         self._guard = threading.Lock()
 71 | 
```
- Holds the base config, the folder under which each session gets a sub-folder, and a `backend_factory` (default
  `make_backend`; tests pass a fake). `_guard` protects the dictionary of sessions. `max_sessions` and `ttl` are the
  limits; `clock` is injectable so tests can move time without waiting.

```python
 72 |     def create(self) -> str:
 73 |         """Start a new session in its own folder and return its id (raises when full)."""
 74 |         with self._guard:
 75 |             self._expire()
 76 |             if len(self._sessions) >= self._max:
 77 |                 raise TooManySessions(f"limit of {self._max} sessions reached")
 78 |             session_id = uuid.uuid4().hex[:12]
 79 |             config = replace(self._config, workspace=self._root / session_id)
 80 |             session = Session(config, self._factory(config))
 81 |             session.last_used = self._clock()
 82 |             self._sessions[session_id] = session
 83 |         return session_id
 84 | 
```
- Under the guard it first drops expired sessions, then refuses (`TooManySessions`) if the limit is reached.
- Makes a 12-character random id, copies the config with `workspace = <root>/<id>` (this is the isolation: the sandbox
  in `sandbox.py` confines each agent to its own folder), builds the backend and session, stamps `last_used`, stores it and returns the id.

```python
 85 |     def _expire(self) -> None:
 86 |         """Forget sessions idle longer than the TTL (caller holds the guard). Files stay on disk."""
 87 |         now = self._clock()
 88 |         for key in [k for k, v in self._sessions.items() if now - v.last_used > self._ttl]:
 89 |             self._sessions.pop(key).toolbox.close()     # also stops its sandbox container
 90 | 
```
- Deletes every session whose idle time (`now - last_used`) exceeds the TTL, closing its toolbox (which stops its
  sandbox container, if any). Only the in-memory object goes; the session's
  files stay on disk (delete the folder yourself if you want the space back).

```python
 91 |     def get(self, session_id: str) -> Session | None:
 92 |         """Look a session up; ``None`` when the id is unknown."""
 93 |         with self._guard:
 94 |             self._expire()
 95 |             session = self._sessions.get(session_id)
 96 |             if session is not None:
 97 |                 session.last_used = self._clock()
 98 |             return session
 99 | 
```
- Drops expired sessions, then returns the session (renewing its `last_used`, so active conversations never expire) or
  `None` for an unknown or expired id.

```python
100 |     def close_all(self) -> None:
101 |         """Close every live session (stops their sandbox containers); used at shutdown."""
102 |         with self._guard:
103 |             sessions, self._sessions = list(self._sessions.values()), {}
104 |         for session in sessions:
105 |             session.toolbox.close()
106 | 
107 | 
```
- Empties the session table under the guard, then closes each session's toolbox outside it (closing can take a few
  seconds per session; holding the guard that long would block other requests). Closing stops each sandbox
  container and MCP server. Used when the server stops.

## The request handler
```python
108 | def make_handler(manager: SessionManager, token: str) -> type[BaseHTTPRequestHandler]:
109 |     """Build the request-handler class bound to one manager and token."""
110 | 
111 |     class Handler(BaseHTTPRequestHandler):
112 |         def log_message(self, format: str, *args: object) -> None:
113 |             """Silence the default per-request console logging."""
114 | 
115 |         def _send(self, status: int, payload: dict) -> None:
116 |             body = json.dumps(payload).encode("utf-8")
117 |             self.send_response(status)
118 |             self.send_header("Content-Type", "application/json")
119 |             self.send_header("Content-Length", str(len(body)))
120 |             self.end_headers()
121 |             self.wfile.write(body)
122 | 
123 |         def _authorized(self) -> bool:
124 |             if not token:
125 |                 return True
126 |             given = self.headers.get("Authorization", "")
127 |             return hmac.compare_digest(given, f"Bearer {token}")
128 | 
129 |         def _read_json(self) -> dict | None:
130 |             try:
131 |                 length = int(self.headers.get("Content-Length", "0"))
132 |                 if length > MAX_BODY:
133 |                     return None
134 |                 value = json.loads(self.rfile.read(length) or b"{}")
135 |             except (ValueError, OSError):
136 |                 return None
137 |             return value if isinstance(value, dict) else None
138 | 
139 |         def do_GET(self) -> None:
140 |             parts = self.path.strip("/").split("/")
141 |             if parts == ["health"]:
142 |                 self._send(200, {"status": "ok"})
143 |             elif not self._authorized():
144 |                 self._send(401, {"error": "unauthorized"})
145 |             elif len(parts) == 3 and parts[0] == "sessions" and parts[2] == "files":
146 |                 session = manager.get(parts[1])
147 |                 if session is None:
148 |                     self._send(404, {"error": "unknown session"})
149 |                     return
150 |                 files = [p.relative_to(session.workspace).as_posix()
151 |                          for p in walk_files(session.workspace)]
152 |                 self._send(200, {"files": files})
153 |             else:
154 |                 self._send(404, {"error": "not found"})
155 | 
156 |         def do_POST(self) -> None:
157 |             parts = self.path.strip("/").split("/")
158 |             if not self._authorized():
159 |                 self._send(401, {"error": "unauthorized"})
160 |             elif parts == ["sessions"]:
161 |                 try:
162 |                     self._send(201, {"id": manager.create()})
163 |                 except TooManySessions as exc:
164 |                     self._send(429, {"error": str(exc)})
165 |             elif len(parts) == 3 and parts[0] == "sessions" and parts[2] == "messages":
166 |                 self._message(parts[1])
167 |             else:
168 |                 self._send(404, {"error": "not found"})
169 | 
170 |         def _message(self, session_id: str) -> None:
171 |             session = manager.get(session_id)
172 |             body = self._read_json()
173 |             if session is None:
174 |                 self._send(404, {"error": "unknown session"})
175 |             elif body is None or not isinstance(body.get("text"), str) or not body["text"].strip():
176 |                 self._send(400, {"error": "body must be JSON like {\"text\": \"...\"}"})
177 |             else:
178 |                 events: list[str] = []
179 |                 with session.lock:                      # one request at a time per session
180 |                     try:
181 |                         answer = session.agent.run(body["text"], on_event=events.append)
182 |                     except LLMError as exc:
183 |                         self._send(502, {"error": f"model error: {exc}"})
184 |                         return
185 |                 self._send(200, {"answer": answer, "events": events,
186 |                                  "calls": session.meter.calls,
187 |                                  "tokens": session.meter.prompt_tokens + session.meter.completion_tokens})
188 | 
189 |     return Handler
190 | 
191 | 
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
192 | def build_server(manager: SessionManager, host: str, port: int, token: str) -> ThreadingHTTPServer:
193 |     """Create (but do not start) the HTTP server; port 0 picks a free port."""
194 |     return ThreadingHTTPServer((host, port), make_handler(manager, token))
195 | 
196 | 
```
- Creates the HTTP server without starting it. Port `0` asks the OS for a free port (used by the tests).

```python
197 | def main(argv: list[str] | None = None) -> int:
198 |     """Command line: ``python -m localagent.server --token SECRET --backend openai ...``."""
199 |     parser = argparse.ArgumentParser(prog="localagent.server")
200 |     parser.add_argument("--host", default="127.0.0.1")
201 |     parser.add_argument("--port", type=int, default=8000)
202 |     parser.add_argument("--token", default=os.environ.get("LOCALAGENT_TOKEN", ""),
203 |                         help="require this bearer token (default: $LOCALAGENT_TOKEN)")
204 |     parser.add_argument("--root", default="sessions", help="folder holding one workspace per session")
205 |     add_model_flags(parser)
206 |     parser.add_argument("--max-sessions", type=int, default=MAX_SESSIONS)
207 |     parser.add_argument("--session-ttl", type=float, default=SESSION_TTL,
208 |                         help="seconds of inactivity before a session is dropped")
209 |     parser.add_argument("--auto-approve", action="store_true",
210 |                         help="let sessions run code without asking (only on a locked-down host)")
211 |     args = parser.parse_args(argv)
212 |     try:
213 |         config = overlay(Config.from_env(), **model_flags(args), auto_approve=args.auto_approve)
214 |         if args.backend is None and "LOCALAGENT_BACKEND" not in os.environ:
215 |             config, _ = choose_mode(config, config.mode)      # the same modes as the chat
216 |     except ValueError as exc:
217 |         print(f"setup problem: {exc}")
218 |         return 2
219 |     config = gemini_history(config)                          # the chat's history budget too
220 |     manager = SessionManager(config, Path(args.root), max_sessions=args.max_sessions,
221 |                              ttl=args.session_ttl)
222 |     server = build_server(manager, args.host, args.port, args.token)
223 |     print(f"localagent server on http://{args.host}:{server.server_port} "
224 |           f"(token {'required' if args.token else 'NOT set'}; mode {config.mode})")
225 |     try:
226 |         server.serve_forever()
227 |     except KeyboardInterrupt:
228 |         pass
229 |     finally:
230 |         server.server_close()
231 |         manager.close_all()                    # no sandbox container outlives the server
232 |         stop_local_servers()                   # nor a local model it started
233 |     return 0
234 | 
235 | 
```
- Command-line entry (the shared `--mode/--online/--offline/--backend/--model/--api-base/--sandbox`, also
  `--max-sessions`, `--session-ttl`; `--token` defaults to the `LOCALAGENT_TOKEN` environment variable so the token need
  not appear in a command line): host (default `127.0.0.1`, i.e. this machine only), port, token, session root folder,
  backend, model, API base and `--auto-approve`. It overlays those on the environment config and — unless a backend
  was chosen by hand — applies the same modes as the chat (`choose_mode`; a mode that cannot work is printed as a setup
  problem). It prints the address, whether a token is required and the mode, and serves until Ctrl+C.
- All sessions share one rate limiter per Gemini model (`shared_limiter`) and at most one local model server.
- For Gemini it raises the history budget to 32K tokens with `gemini_history` — the same rule as the chat. Without it, every session used the 8K budget meant for local models and
  long conversations were summarised (an extra model call each time) far earlier than needed.
- When it stops (Ctrl+C), it releases the port (`server_close`), closes every live session (`close_all`), so no
  sandbox container is left running, and stops a local model server it started. Found in the final verification: before, each session started with
  `--sandbox docker` left its container behind. (A process killed outright cannot clean up; then remove leftovers with
  `docker rm -f localagent-...`.)
- Each reply carries `answer`, `events` (the progress lines), `calls` and `tokens` (the session's totals so far).

```python
236 | if __name__ == "__main__":
237 |     raise SystemExit(main())
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
