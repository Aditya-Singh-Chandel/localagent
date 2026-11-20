"""Tests for the HTTP service mode, using a real local socket and a scripted fake model."""
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from dataclasses import replace
from pathlib import Path

from localagent.config import Config
from localagent.server import SessionManager, build_server

from .test_agent import FakeBackend, tool_call


def call(base: str, method: str, path: str, body: dict | None = None, token: str = ""):
    """Send one request; return (status, parsed JSON) even for HTTP error codes."""
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(base + path, data=data, method=method)
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=20) as reply:
            return reply.status, json.loads(reply.read())
    except urllib.error.HTTPError as exc:
        with exc:
            return exc.code, json.loads(exc.read())


class ServerTests(unittest.TestCase):
    def start(self, script, token="secret"):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        manager = SessionManager(replace(Config(), auto_approve=True), Path(tmp.name),
                                 backend_factory=lambda _cfg: FakeBackend(script))
        server = build_server(manager, "127.0.0.1", 0, token)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return f"http://127.0.0.1:{server.server_port}"

    def test_health_needs_no_token(self):
        base = self.start([])
        self.assertEqual(call(base, "GET", "/health"), (200, {"status": "ok"}))

    def test_requests_without_token_are_rejected(self):
        base = self.start([])
        self.assertEqual(call(base, "POST", "/sessions")[0], 401)
        self.assertEqual(call(base, "GET", "/sessions/x/files")[0], 401)
        self.assertEqual(call(base, "POST", "/sessions", token="wrong")[0], 401)

    def test_full_conversation_creates_files(self):
        script = [tool_call("write_file", path="hello.txt", content="hi"),
                  {"role": "assistant", "content": "created"}]
        base = self.start(script)
        status, created = call(base, "POST", "/sessions", token="secret")
        self.assertEqual(status, 201)
        path = f"/sessions/{created['id']}"
        status, reply = call(base, "POST", path + "/messages", {"text": "Create hello.txt"}, "secret")
        self.assertEqual((status, reply["answer"], reply["calls"]), (200, "created", 2))
        self.assertTrue(any("write_file" in e for e in reply["events"]))
        self.assertEqual(call(base, "GET", path + "/files", token="secret")[1], {"files": ["hello.txt"]})

    def test_bad_requests(self):
        base = self.start([])
        _, created = call(base, "POST", "/sessions", token="secret")
        path = f"/sessions/{created['id']}/messages"
        self.assertEqual(call(base, "POST", path, {"text": "  "}, "secret")[0], 400)
        self.assertEqual(call(base, "POST", path, {"nope": 1}, "secret")[0], 400)
        self.assertEqual(call(base, "POST", "/sessions/unknown/messages", {"text": "x"}, "secret")[0], 404)
        self.assertEqual(call(base, "GET", "/sessions/unknown/files", token="secret")[0], 404)
        self.assertEqual(call(base, "GET", "/elsewhere", token="secret")[0], 404)
        self.assertEqual(call(base, "POST", "/elsewhere", token="secret")[0], 404)

    def test_sessions_have_separate_workspaces(self):
        script = [tool_call("write_file", path="only_a.txt", content="x"),
                  {"role": "assistant", "content": "ok"}]
        base = self.start(script, token="")
        _, a = call(base, "POST", "/sessions")
        _, b = call(base, "POST", "/sessions")
        call(base, "POST", f"/sessions/{a['id']}/messages", {"text": "Create only_a.txt"})
        self.assertEqual(call(base, "GET", f"/sessions/{a['id']}/files")[1], {"files": ["only_a.txt"]})
        self.assertEqual(call(base, "GET", f"/sessions/{b['id']}/files")[1], {"files": []})


class SessionLimitTests(unittest.TestCase):
    def manager(self, clock, **kw):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        return SessionManager(Config(), Path(tmp.name), backend_factory=lambda _c: FakeBackend([]),
                              clock=clock, **kw)

    def test_idle_sessions_expire_and_activity_keeps_them(self):
        now = [0.0]
        manager = self.manager(lambda: now[0], ttl=100.0)
        kept, dropped = manager.create(), manager.create()
        now[0] = 80.0
        self.assertIsNotNone(manager.get(kept))        # touching it renews its lifetime
        now[0] = 150.0
        self.assertIsNotNone(manager.get(kept))        # 70 s since last use: alive
        self.assertIsNone(manager.get(dropped))        # 150 s idle: expired

    def test_close_all_closes_every_session(self):
        manager = self.manager(lambda: 0.0)
        ids = [manager.create(), manager.create()]
        sessions = [manager.get(i) for i in ids]
        closed: list[str] = []
        for session_id, session in zip(ids, sessions, strict=True):
            assert session is not None
            session.toolbox.close = lambda sid=session_id: closed.append(sid)   # type: ignore
        manager.close_all()
        self.assertEqual(sorted(closed), sorted(ids))   # each sandbox would be stopped
        self.assertIsNone(manager.get(ids[0]))         # and the sessions are gone

    def test_session_limit_returns_429_and_frees_after_expiry(self):
        now = [0.0]
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        manager = SessionManager(Config(), Path(tmp.name), backend_factory=lambda _c: FakeBackend([]),
                                 max_sessions=1, ttl=10.0, clock=lambda: now[0])
        server = build_server(manager, "127.0.0.1", 0, "")
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        base = f"http://127.0.0.1:{server.server_port}"
        self.assertEqual(call(base, "POST", "/sessions")[0], 201)
        self.assertEqual(call(base, "POST", "/sessions")[0], 429)
        now[0] = 11.0                                  # the first session is now idle too long
        self.assertEqual(call(base, "POST", "/sessions")[0], 201)


if __name__ == "__main__":
    unittest.main()
