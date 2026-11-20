"""Tests for streaming, backends, context, undo, sessions, search and the eval harness."""
import json
import tempfile
import threading
import unittest
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from evals.run_evals import run_task
from evals.tasks import FIZZBUZZ
from localagent.agent import Agent
from localagent.backends import make_backend
from localagent.checkpoint import Checkpoints
from localagent.config import Config
from localagent.context import build_system_prompt
from localagent.doctor import run_doctor
from localagent.llm import LLMError, OllamaClient
from localagent.meter import Meter
from localagent.openai_compat import OpenAICompatClient, to_openai
from localagent.session import SessionLog, latest_session, load_session
from localagent.tools import ToolBox
from tests.test_agent import FakeBackend, tool_call

FIZZ_CODE = ("def fizzbuzz(n):\n    out = []\n    for i in range(1, n + 1):\n"
             "        s = ('Fizz' if i % 3 == 0 else '') + ('Buzz' if i % 5 == 0 else '')\n"
             "        out.append(s or str(i))\n    return out\n")


class FakeServer:
    """Local HTTP server answering with canned bytes; records what it was sent."""

    def __init__(self, status: int, body: bytes) -> None:
        outer = self
        self.received: list[dict] = []
        self.headers: list[dict] = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802 (name fixed by http.server)
                length = int(self.headers["Content-Length"])
                outer.received.append(json.loads(self.rfile.read(length)))
                outer.headers.append(dict(self.headers))
                self.send_response(status)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):  # noqa: N802 (name fixed by http.server)
                self.send_response(status)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self._server.server_port}"
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()


def ndjson(*objects: dict) -> bytes:
    return "".join(json.dumps(o) + "\n" for o in objects).encode()


class OllamaClientTests(unittest.TestCase):
    def test_streams_text_and_counts_tokens(self):
        body = ndjson(
            {"message": {"role": "assistant", "content": "Hel"}, "done": False},
            {"message": {"role": "assistant", "content": "lo"}, "done": False},
            {"message": {"role": "assistant", "content": ""}, "done": True,
             "prompt_eval_count": 7, "eval_count": 2})
        server = FakeServer(200, body)
        self.addCleanup(server.close)
        seen: list[str] = []
        reply = OllamaClient(replace(Config(), host=server.url)).chat(
            [{"role": "user", "content": "hi"}], [], seen.append)
        self.assertEqual(seen, ["Hel", "lo"])
        self.assertEqual(reply.message["content"], "Hello")
        self.assertEqual((reply.prompt_tokens, reply.completion_tokens), (7, 2))
        self.assertTrue(server.received[0]["stream"])

    def test_collects_tool_calls(self):
        call = {"function": {"name": "list_files", "arguments": {}}}
        body = ndjson({"message": {"role": "assistant", "content": "", "tool_calls": [call]},
                       "done": True, "eval_count": 1})
        server = FakeServer(200, body)
        self.addCleanup(server.close)
        reply = OllamaClient(replace(Config(), host=server.url)).chat([], [])
        self.assertEqual(reply.message["tool_calls"], [call])

    def test_http_error_becomes_llm_error(self):
        server = FakeServer(404, b'{"error": "model not found"}')
        self.addCleanup(server.close)
        with self.assertRaisesRegex(LLMError, "HTTP 404"):
            OllamaClient(replace(Config(), host=server.url)).chat([], [])

    def test_unreachable_server_becomes_llm_error(self):
        with self.assertRaisesRegex(LLMError, "Cannot reach"):
            OllamaClient(replace(Config(), host="http://127.0.0.1:9")).chat([], [])

    def test_error_line_in_stream(self):
        server = FakeServer(200, ndjson({"error": "out of memory"}))
        self.addCleanup(server.close)
        with self.assertRaisesRegex(LLMError, "out of memory"):
            OllamaClient(replace(Config(), host=server.url)).chat([], [])


class OpenAIClientTests(unittest.TestCase):
    def test_round_trip_with_tool_call_and_auth(self):
        body = json.dumps({"choices": [{"message": {"content": "", "tool_calls": [
            {"id": "c1", "function": {"name": "read_file", "arguments": "{\"path\": \"a.py\"}"}}]}}],
            "usage": {"prompt_tokens": 11, "completion_tokens": 3}}).encode()
        server = FakeServer(200, body)
        self.addCleanup(server.close)
        config = replace(Config(), backend="openai", api_base=server.url, api_key="secret",
                         stream=False)
        reply = OpenAICompatClient(config).chat([{"role": "user", "content": "x"}], [])
        self.assertEqual(reply.message["tool_calls"][0]["id"], "c1")
        self.assertEqual((reply.prompt_tokens, reply.completion_tokens), (11, 3))
        self.assertEqual(server.headers[0]["Authorization"], "Bearer secret")

    def test_message_conversion(self):
        converted = to_openai([
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "c1", "function": {"name": "f", "arguments": {"a": 1}}}]},
            {"role": "tool", "tool_name": "f", "tool_call_id": "c1", "content": "ok"}])
        self.assertEqual(converted[0]["tool_calls"][0]["function"]["arguments"], '{"a": 1}')
        self.assertEqual(converted[1]["tool_call_id"], "c1")


class DoctorTests(unittest.TestCase):
    def tags(self, *names: str) -> FakeServer:
        server = FakeServer(200, json.dumps({"models": [{"name": n} for n in names]}).encode())
        self.addCleanup(server.close)
        return server

    def test_healthy_when_model_installed(self):
        server = self.tags("qwen3:8b")
        healthy, lines = run_doctor(replace(Config(), host=server.url))
        self.assertTrue(healthy)
        self.assertTrue(any("model qwen3:8b is installed" in line for line in lines))

    def test_untagged_name_means_latest(self):
        server = self.tags("tinyllama:latest")
        healthy, _ = run_doctor(replace(Config(), host=server.url, model="tinyllama"))
        self.assertTrue(healthy)

    def test_missing_model_gives_pull_command(self):
        server = self.tags("other:1b")
        healthy, lines = run_doctor(replace(Config(), host=server.url))
        self.assertFalse(healthy)
        self.assertTrue(any("ollama pull qwen3:8b" in line for line in lines))

    def test_unreachable_server_is_reported(self):
        healthy, lines = run_doctor(replace(Config(), host="http://127.0.0.1:9"))
        self.assertFalse(healthy)
        self.assertTrue(any("not reachable" in line for line in lines))


class BackendRegistryTests(unittest.TestCase):
    def test_known_and_unknown(self):
        self.assertIsInstance(make_backend(Config()), OllamaClient)
        self.assertIsInstance(make_backend(replace(Config(), backend="openai")), OpenAICompatClient)
        with self.assertRaisesRegex(ValueError, "unknown backend"):
            make_backend(replace(Config(), backend="nope"))


class ContextTests(unittest.TestCase):
    def test_prompt_contains_tree_and_notes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.py").write_text("x = 1\n", encoding="utf-8")
            (root / "AGENT.md").write_text("Use tabs never.", encoding="utf-8")
            (root / "__pycache__").mkdir()
            prompt = build_system_prompt(replace(Config(), workspace=root))
            self.assertIn("a.py", prompt)
            self.assertIn("Use tabs never.", prompt)
            self.assertNotIn("__pycache__", prompt)

    def test_tree_is_capped(self):
        with tempfile.TemporaryDirectory() as tmp:
            for n in range(10):
                (Path(tmp) / f"f{n}.txt").write_text("x", encoding="utf-8")
            prompt = build_system_prompt(replace(Config(), workspace=Path(tmp), tree_entries=3))
            self.assertIn("...", prompt)
            self.assertNotIn("f9.txt", prompt)


class CheckpointTests(unittest.TestCase):
    def test_undo_restores_deletes_and_recreates(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "keep.txt").write_text("v1", encoding="utf-8")
            (root / "gone.txt").write_text("bye", encoding="utf-8")
            points = Checkpoints(root)
            points.take()
            (root / "keep.txt").write_text("v2", encoding="utf-8")   # modified
            (root / "gone.txt").unlink()                              # deleted
            (root / "new.txt").write_text("n", encoding="utf-8")      # created
            message = points.undo()
            self.assertEqual((root / "keep.txt").read_text(encoding="utf-8"), "v1")
            self.assertEqual((root / "gone.txt").read_text(encoding="utf-8"), "bye")
            self.assertFalse((root / "new.txt").exists())
            self.assertIn("restored 2", message)
            self.assertEqual(points.undo(), "nothing to undo")


class SessionTests(unittest.TestCase):
    def test_log_and_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = replace(Config(), workspace=Path(tmp) / "ws")
            log = SessionLog(Path(tmp) / "sessions")
            agent = Agent(cfg, FakeBackend([{"role": "assistant", "content": "hi there"}]),
                          ToolBox(cfg), Meter(), log)
            agent.run("hello")
            saved = load_session(latest_session(Path(tmp) / "sessions"))
            self.assertEqual([m["role"] for m in saved], ["user", "assistant"])
            fresh = Agent(cfg, FakeBackend([]), ToolBox(cfg), Meter())
            fresh.restore(saved)
            self.assertEqual(fresh.messages[-1]["content"], "hi there")
            self.assertEqual(fresh.messages[0]["role"], "system")

    def test_latest_of_empty_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(latest_session(Path(tmp)))


class SearchAndWorkspaceTests(unittest.TestCase):
    def test_search_finds_lines_and_reports_bad_regex(self):
        with tempfile.TemporaryDirectory() as tmp:
            box = ToolBox(replace(Config(), workspace=Path(tmp)), lambda _a: True)
            box.call("write_file", {"path": "a.py", "content": "def hello():\n    return 1\n"})
            self.assertIn("a.py:1: def hello():", box.call("search", {"pattern": "def \\w+"}))
            self.assertEqual(box.call("search", {"pattern": "zzz"}), "no matches")
            self.assertIn("invalid regular expression", box.call("search", {"pattern": "("}))

    def test_check_workspace_lists_broken_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            box = ToolBox(replace(Config(), workspace=Path(tmp)))
            (Path(tmp) / "ok.py").write_text("x = 1\n", encoding="utf-8")
            (Path(tmp) / "bad.py").write_text("def f(:\n", encoding="utf-8")
            problems = box.check_workspace()
            self.assertEqual(len(problems), 1)
            self.assertIn("bad.py", problems[0])

    def test_agent_assigns_tool_call_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = replace(Config(), workspace=Path(tmp))
            script = [tool_call("list_files"), {"role": "assistant", "content": "done"}]
            agent = Agent(cfg, FakeBackend(script), ToolBox(cfg), Meter())
            agent.run("ls")
            tool_message = next(m for m in agent.messages if m["role"] == "tool")
            self.assertTrue(tool_message["tool_call_id"].startswith("call_"))


class EvalHarnessTests(unittest.TestCase):
    def test_passing_run_is_scored_pass(self):
        script = [tool_call("write_file", path="fizzbuzz.py", content=FIZZ_CODE),
                  {"role": "assistant", "content": "done"}]
        result = run_task(FIZZBUZZ, Config(), FakeBackend(script))
        self.assertTrue(result.passed)
        self.assertEqual(result.calls, 2)

    def test_do_nothing_run_is_scored_fail(self):
        idk = {"role": "assistant", "content": "idk"}
        result = run_task(FIZZBUZZ, Config(), FakeBackend([idk, idk]))   # 2nd reply answers the nudge
        self.assertFalse(result.passed)
        self.assertEqual(result.calls, 2)


if __name__ == "__main__":
    unittest.main()
