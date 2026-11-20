"""HTTP service mode: many independent agent sessions behind a small JSON API.

    POST /sessions                     -> {"id": "..."}              create a session
    POST /sessions/<id>/messages       {"text": "..."} -> answer, events, calls, tokens
    GET  /sessions/<id>/files          -> {"files": [...]}           what the agent produced
    GET  /health                       -> {"status": "ok"}           (no token needed)

Every session gets its own workspace folder, so sessions cannot see each other's files.
When a token is configured, every call except /health needs ``Authorization: Bearer <token>``.
"""
from __future__ import annotations

import argparse
import hmac
import json
import os
import threading
import time
import uuid
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable

from .agent import Agent
from .backends import make_backend
from .cli import add_model_flags, model_flags
from .config import Config, gemini_history, overlay
from .llm import ChatBackend, LLMError
from .meter import Meter
from .sandbox import walk_files
from .tools import ToolBox

MAX_BODY = 1_000_000
MAX_SESSIONS = 20          # sessions alive at once
SESSION_TTL = 3600.0       # seconds of inactivity before a session is dropped


class TooManySessions(Exception):
    """Raised by ``SessionManager.create`` when the server is full."""


class Session:
    """One agent with its own workspace; a lock keeps its requests one at a time."""

    def __init__(self, config: Config, backend: ChatBackend) -> None:
        self.meter = Meter()
        self.toolbox = ToolBox(config, lambda _a: config.auto_approve)
        self.agent = Agent(config, backend, self.toolbox, self.meter)
        self.workspace = config.workspace
        self.lock = threading.Lock()
        self.last_used = 0.0                      # set by the manager on every access


class SessionManager:
    """Creates sessions and finds them by id."""

    def __init__(self, config: Config, root: Path,
                 backend_factory: Callable[[Config], ChatBackend] = make_backend,
                 max_sessions: int = MAX_SESSIONS, ttl: float = SESSION_TTL,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._config = config
        self._root = root
        self._factory = backend_factory
        self._max = max_sessions
        self._ttl = ttl
        self._clock = clock
        self._sessions: dict[str, Session] = {}
        self._guard = threading.Lock()

    def create(self) -> str:
        """Start a new session in its own folder and return its id (raises when full)."""
        with self._guard:
            self._expire()
            if len(self._sessions) >= self._max:
                raise TooManySessions(f"limit of {self._max} sessions reached")
            session_id = uuid.uuid4().hex[:12]
            config = replace(self._config, workspace=self._root / session_id)
            session = Session(config, self._factory(config))
            session.last_used = self._clock()
            self._sessions[session_id] = session
        return session_id

    def _expire(self) -> None:
        """Forget sessions idle longer than the TTL (caller holds the guard). Files stay on disk."""
        now = self._clock()
        for key in [k for k, v in self._sessions.items() if now - v.last_used > self._ttl]:
            self._sessions.pop(key).toolbox.close()     # also stops its sandbox container

    def get(self, session_id: str) -> Session | None:
        """Look a session up; ``None`` when the id is unknown."""
        with self._guard:
            self._expire()
            session = self._sessions.get(session_id)
            if session is not None:
                session.last_used = self._clock()
            return session

    def close_all(self) -> None:
        """Close every live session (stops their sandbox containers); used at shutdown."""
        with self._guard:
            sessions, self._sessions = list(self._sessions.values()), {}
        for session in sessions:
            session.toolbox.close()


def make_handler(manager: SessionManager, token: str) -> type[BaseHTTPRequestHandler]:
    """Build the request-handler class bound to one manager and token."""

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            """Silence the default per-request console logging."""

        def _send(self, status: int, payload: dict) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _authorized(self) -> bool:
            if not token:
                return True
            given = self.headers.get("Authorization", "")
            return hmac.compare_digest(given, f"Bearer {token}")

        def _read_json(self) -> dict | None:
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length > MAX_BODY:
                    return None
                value = json.loads(self.rfile.read(length) or b"{}")
            except (ValueError, OSError):
                return None
            return value if isinstance(value, dict) else None

        def do_GET(self) -> None:
            parts = self.path.strip("/").split("/")
            if parts == ["health"]:
                self._send(200, {"status": "ok"})
            elif not self._authorized():
                self._send(401, {"error": "unauthorized"})
            elif len(parts) == 3 and parts[0] == "sessions" and parts[2] == "files":
                session = manager.get(parts[1])
                if session is None:
                    self._send(404, {"error": "unknown session"})
                    return
                files = [p.relative_to(session.workspace).as_posix()
                         for p in walk_files(session.workspace)]
                self._send(200, {"files": files})
            else:
                self._send(404, {"error": "not found"})

        def do_POST(self) -> None:
            parts = self.path.strip("/").split("/")
            if not self._authorized():
                self._send(401, {"error": "unauthorized"})
            elif parts == ["sessions"]:
                try:
                    self._send(201, {"id": manager.create()})
                except TooManySessions as exc:
                    self._send(429, {"error": str(exc)})
            elif len(parts) == 3 and parts[0] == "sessions" and parts[2] == "messages":
                self._message(parts[1])
            else:
                self._send(404, {"error": "not found"})

        def _message(self, session_id: str) -> None:
            session = manager.get(session_id)
            body = self._read_json()
            if session is None:
                self._send(404, {"error": "unknown session"})
            elif body is None or not isinstance(body.get("text"), str) or not body["text"].strip():
                self._send(400, {"error": "body must be JSON like {\"text\": \"...\"}"})
            else:
                events: list[str] = []
                with session.lock:                      # one request at a time per session
                    try:
                        answer = session.agent.run(body["text"], on_event=events.append)
                    except LLMError as exc:
                        self._send(502, {"error": f"model error: {exc}"})
                        return
                self._send(200, {"answer": answer, "events": events,
                                 "calls": session.meter.calls,
                                 "tokens": session.meter.prompt_tokens + session.meter.completion_tokens})

    return Handler


def build_server(manager: SessionManager, host: str, port: int, token: str) -> ThreadingHTTPServer:
    """Create (but do not start) the HTTP server; port 0 picks a free port."""
    return ThreadingHTTPServer((host, port), make_handler(manager, token))


def main(argv: list[str] | None = None) -> int:
    """Command line: ``python -m localagent.server --token SECRET --backend openai ...``."""
    parser = argparse.ArgumentParser(prog="localagent.server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--token", default=os.environ.get("LOCALAGENT_TOKEN", ""),
                        help="require this bearer token (default: $LOCALAGENT_TOKEN)")
    parser.add_argument("--root", default="sessions", help="folder holding one workspace per session")
    add_model_flags(parser)
    parser.add_argument("--max-sessions", type=int, default=MAX_SESSIONS)
    parser.add_argument("--session-ttl", type=float, default=SESSION_TTL,
                        help="seconds of inactivity before a session is dropped")
    parser.add_argument("--auto-approve", action="store_true",
                        help="let sessions run code without asking (only on a locked-down host)")
    args = parser.parse_args(argv)
    config = overlay(Config.from_env(), **model_flags(args), auto_approve=args.auto_approve)
    config = gemini_history(config)                          # the chat's history budget too
    manager = SessionManager(config, Path(args.root), max_sessions=args.max_sessions,
                             ttl=args.session_ttl)
    server = build_server(manager, args.host, args.port, args.token)
    print(f"localagent server on http://{args.host}:{server.server_port} "
          f"(token {'required' if args.token else 'NOT set'})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        manager.close_all()                    # no sandbox container outlives the server
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
