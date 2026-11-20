"""Tests for permissions, web pages, MCP servers, plan mode, interrupts, diffs and the CLI helpers."""
import contextlib
import importlib.util
import io
import json
import os
import sys
import tempfile
import threading
import unittest
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from localagent.agent import INTERRUPTED, NOT_ALLOWED, PLAN_ONLY_ERROR, Agent
from localagent.cli import (Approver, auto_configure, build_parser, handle_request, read_request,
                            sessions_folder)
from localagent.config import GEMINI_CONTEXT, Config, gemini_history, overlay
from localagent.mcp import MCPError, MCPServer, clean_schema
from localagent.meter import Meter
from localagent.sandbox import shell_argv
from localagent.ratelimit import RateLimiter
from localagent.settings import Permissions, load_settings, matches, parse_rule, suggest_rule
from localagent.tools import TOGETHER_HINT, Approval, ToolBox
from localagent.web import fetch_text, html_to_text

from .test_agent import FakeBackend, tool_call

FAKE_MCP_SERVER = r'''
import json, sys

def send(message):
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()

for line in sys.stdin:
    message = json.loads(line)
    method, ident = message.get("method"), message.get("id")
    if method == "initialize":
        send({"jsonrpc": "2.0", "id": "s1", "method": "roots/list"})      # a question for the client
        send({"jsonrpc": "2.0", "id": ident, "result": {"protocolVersion": "2025-06-18",
              "capabilities": {"tools": {}}, "serverInfo": {"name": "fake", "version": "1"}}})
    elif method == "tools/list" and not message.get("params", {}).get("cursor"):
        schema = {"$schema": "x", "type": "object", "additionalProperties": False,
                  "properties": {"text": {"type": ["string", "null"]}}, "required": ["text"]}
        send({"jsonrpc": "2.0", "id": ident, "result": {"nextCursor": "page2", "tools": [
            {"name": "echo", "description": "Echo text back", "inputSchema": schema}]}})
    elif method == "tools/list":
        send({"jsonrpc": "2.0", "id": ident, "result": {"tools": [{"name": "fail"}]}})
    elif method == "tools/call":
        params = message["params"]
        if params["name"] == "echo":
            text = "echo: " + str(params["arguments"].get("text"))
            send({"jsonrpc": "2.0", "id": ident, "result": {"content": [{"type": "text", "text": text}]}})
        else:
            send({"jsonrpc": "2.0", "id": ident, "result": {"isError": True,
                  "content": [{"type": "text", "text": "it broke"}, {"type": "image"}]}})
'''


def write_server(folder: str) -> list[str]:
    """Save the fake MCP server; return the settings entry that starts it."""
    path = Path(folder) / "fake_mcp.py"
    path.write_text(FAKE_MCP_SERVER, encoding="utf-8")
    return [sys.executable, str(path)]


class PermissionTests(unittest.TestCase):
    def test_rules_match_tools_and_subjects(self):
        self.assertEqual(parse_rule("run_shell(git *)"), ("run_shell", "git *"))
        self.assertEqual(parse_rule(" run_tests "), ("run_tests", None))
        self.assertTrue(matches("run_shell(git *)", "run_shell", "git status"))
        self.assertFalse(matches("run_shell(git *)", "run_shell", "rm -rf x"))
        self.assertTrue(matches("mcp__files__*", "mcp__files__read", "{}"))
        rules = Permissions(allow=["run_shell(*)"], deny=["run_shell(*rm *)"])
        self.assertEqual(rules.decide("run_shell", "ls"), "allow")
        self.assertEqual(rules.decide("run_shell", "rm -rf /"), "deny")   # deny wins
        self.assertEqual(rules.decide("run_tests", ""), "ask")

    def test_suggested_rules_are_narrow(self):
        self.assertEqual(suggest_rule("run_shell", "git status"), "run_shell(git status)")
        self.assertEqual(suggest_rule("fetch_url", "https://docs.python.org/3/x.html"),
                         "fetch_url(https://docs.python.org/*)")
        self.assertEqual(suggest_rule("run_tests", ""), "run_tests")

    def test_settings_merge_and_always_allow_is_saved(self):
        with tempfile.TemporaryDirectory() as tmp:
            user = Path(tmp) / "user.json"
            user.write_text(json.dumps({"permissions": {"deny": ["run_shell(*rm *)"]},
                                        "mcpServers": {"a": {"command": "x"}}}), encoding="utf-8")
            project = Path(tmp) / ".localagent" / "settings.json"
            project.parent.mkdir()
            project.write_text(json.dumps({"permissions": {"allow": ["run_tests"]}}), encoding="utf-8")
            merged = load_settings(Path(tmp), user)
            self.assertEqual(merged["permissions"], {"allow": ["run_tests"], "deny": ["run_shell(*rm *)"]})
            self.assertIn("a", merged["mcpServers"])
            rules = Permissions.from_settings(merged, project)
            self.assertEqual(rules.always_allow("run_shell(ls)"), project)
            saved = json.loads(project.read_text(encoding="utf-8"))
            self.assertEqual(saved["permissions"]["allow"], ["run_tests", "run_shell(ls)"])
            project.write_text("{broken", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "not valid JSON"):
                load_settings(Path(tmp), user)

    def test_toolbox_applies_rules_before_asking(self):
        with tempfile.TemporaryDirectory() as tmp:
            asked: list[str] = []
            cfg = replace(Config(), workspace=Path(tmp))
            box = ToolBox(cfg, lambda q: asked.append(q) or False,
                          {"permissions": {"allow": ["run_tests"], "deny": ["run_python(*)"]}})
            box.call("write_file", {"path": "a.py", "content": "print(1)\n"})
            self.assertIn("deny rule", box.call("run_python", {"path": "a.py"}))
            self.assertIn("exit code", box.call("run_tests", {}))       # allowed: not asked
            self.assertEqual(asked, [])
            box.permissions.allow.clear()
            self.assertIn("DENIED", box.call("run_tests", {}))          # asked, user said no
            self.assertIsInstance(asked[0], Approval)
            self.assertEqual((asked[0].tool, asked[0].subject), ("run_tests", ""))


class WebTests(unittest.TestCase):
    PAGE = (b"<html><head><title>t</title><style>x{}</style></head><body><h1>Title</h1>"
            b"<p>Hello   <b>world</b></p><script>var hidden=1;</script><ul><li>one</li></ul></body></html>")

    def serve(self, status=200, body=PAGE, ctype="text/html; charset=utf-8"):
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return f"http://127.0.0.1:{server.server_port}/page"

    def test_html_becomes_visible_text(self):
        text = html_to_text(self.PAGE.decode())
        self.assertEqual(text, "Title\n\nHello world\n\none")
        self.assertNotIn("hidden", text)

    def test_fetch_and_errors(self):
        self.assertEqual(fetch_text(self.serve()), "Title\n\nHello world\n\none")
        self.assertEqual(fetch_text(self.serve(body=b"plain", ctype="text/plain")), "plain")
        with self.assertRaisesRegex(ValueError, "HTTP 404"):
            fetch_text(self.serve(status=404))
        with self.assertRaisesRegex(ValueError, "only http"):
            fetch_text("file:///etc/passwd")

    def test_fetch_tool_asks_and_obeys_rules(self):
        with tempfile.TemporaryDirectory() as tmp:
            url = self.serve()
            cfg = replace(Config(), workspace=Path(tmp))
            self.assertIn("Hello world", ToolBox(cfg, lambda _q: True).call("fetch_url", {"url": url}))
            self.assertIn("DENIED", ToolBox(cfg).call("fetch_url", {"url": url}))     # nobody approved
            denied = ToolBox(cfg, lambda _q: True, {"permissions": {"deny": ["fetch_url"]}})
            self.assertIn("deny rule", denied.call("fetch_url", {"url": url}))
            self.assertIn("ERROR", ToolBox(cfg, lambda _q: True).call("fetch_url", {"url": "ftp://x"}))


class MCPTests(unittest.TestCase):
    def test_schema_cleaning(self):
        cleaned = clean_schema({"$schema": "x", "type": "object", "additionalProperties": False,
                                "properties": {"a": {"type": ["integer", "null"]},
                                               "b": {"items": {"type": "string"}}}})
        self.assertEqual(cleaned, {"type": "object", "properties": {
            "a": {"type": "integer", "nullable": True},
            "b": {"items": {"type": "string"}, "type": "string"}}})
        self.assertEqual(clean_schema(None), {"type": "object", "properties": {}})

    def test_server_tools_through_the_toolbox(self):
        with tempfile.TemporaryDirectory() as tmp:
            command = write_server(tmp)
            settings = {"mcpServers": {"fake": {"command": command[0], "args": command[1:]},
                                       "broken": {"command": "no-such-program-xyz"},
                                       "empty": {}}}
            box = ToolBox(replace(Config(), workspace=Path(tmp)), lambda _q: True, settings)
            try:
                names = [s["function"]["name"] for s in box.specs()]
                self.assertIn("mcp__fake__echo", names)
                self.assertIn("mcp__fake__fail", names)                  # second page of the list
                echo = next(s for s in box.specs() if s["function"]["name"] == "mcp__fake__echo")
                self.assertNotIn("$schema", json.dumps(echo))
                self.assertEqual(box.call("mcp__fake__echo", {"text": "hi"}), "echo: hi")
                self.assertTrue(box.call("mcp__fake__fail", {}).startswith("ERROR: it broke"))
                self.assertTrue(any("2 tool(s)" in n for n in box.notes))
                self.assertTrue(any("broken" in n and "skipped" in n for n in box.notes))
                self.assertTrue(any("empty" in n and "command" in n for n in box.notes))
                box.permissions.deny.append("mcp__fake__*")
                self.assertIn("deny rule", box.call("mcp__fake__echo", {"text": "x"}))
            finally:
                box.close()

    def test_timeout_and_stopped_server(self):
        with tempfile.TemporaryDirectory() as tmp:
            command = write_server(tmp)
            server = MCPServer("fake", command[0], command[1:], timeout=5).start()
            with self.assertRaisesRegex(MCPError, "did not answer"):
                server.request("never/answered", timeout=0.5)
            server.close()
            proc = server._proc
            assert proc is not None and proc.stdin is not None and proc.stdout is not None
            self.assertIsNotNone(proc.poll())                               # the program has ended
            self.assertTrue(proc.stdin.closed and proc.stdout.closed)       # no handle left open
            with self.assertRaises(MCPError):
                server.call_tool("echo", {"text": "late"})


class RecordingBackend(FakeBackend):
    """Also remembers which tool names were offered on each call."""

    def __init__(self, messages):
        super().__init__(messages)
        self.offered: list[list[str]] = []

    def chat(self, messages, tools, on_token=None):
        self.offered.append([t["function"]["name"] for t in tools])
        return super().chat(messages, tools, on_token)


class AgentFeatureTests(unittest.TestCase):
    def make(self, tmp, backend, **kw):
        cfg = replace(Config(), workspace=Path(tmp), auto_format=False, **kw)
        return Agent(cfg, backend, ToolBox(cfg, lambda _q: True), Meter())

    def test_plan_mode_is_read_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            backend = RecordingBackend([tool_call("write_file", path="x.py", content="x = 1\n"),
                                        tool_call("finish", summary="1. create x.py")])
            agent = self.make(tmp, backend)
            self.assertEqual(agent.run("Create x.py", plan_only=True), "1. create x.py")
            self.assertFalse((Path(tmp) / "x.py").exists())
            self.assertNotIn("write_file", backend.offered[0])
            self.assertIn("read_file", backend.offered[0])
            self.assertIn(PLAN_ONLY_ERROR, [m["content"] for m in agent.messages])
            self.assertTrue(agent.messages[1]["content"].startswith("PLAN MODE"))

    def test_restricted_request_offers_only_allowed_tools(self):
        with tempfile.TemporaryDirectory() as tmp:
            backend = RecordingBackend([tool_call("run_tests"),
                                        tool_call("write_file", path="AGENT.md", content="notes"),
                                        tool_call("finish", summary="wrote notes")])
            agent = self.make(tmp, backend)
            answer = agent.run("Create AGENT.md", allowed=frozenset({"write_file"}))
            self.assertEqual(answer, "wrote notes")
            self.assertIn("write_file", backend.offered[0])
            self.assertNotIn("run_tests", backend.offered[0])
            self.assertIn(NOT_ALLOWED, [m["content"] for m in agent.messages])
            self.assertTrue((Path(tmp) / "AGENT.md").is_file())
            planner = RecordingBackend([tool_call("finish", summary="plan")])
            self.make(tmp, planner).run("Plan it", plan_only=True)
            self.assertIn("todo", planner.offered[0])                    # a checklist changes nothing

    def test_repair_answers_unanswered_calls(self):
        with tempfile.TemporaryDirectory() as tmp:
            agent = self.make(tmp, FakeBackend([]))
            agent.messages.append({"role": "assistant", "content": "", "tool_calls": [
                {"id": "c1", "function": {"name": "run_tests"}},
                {"id": "c2", "function": {"name": "read_file"}}]})
            agent.messages.append({"role": "tool", "tool_call_id": "c1", "content": "done"})
            self.assertEqual(agent.repair(), 1)
            self.assertEqual(agent.messages[-1]["tool_call_id"], "c2")
            self.assertEqual(agent.messages[-1]["content"], INTERRUPTED)
            self.assertEqual(agent.repair(), 0)
            fresh = self.make(tmp, FakeBackend([]))
            fresh.restore(agent.messages[1:-1])                         # a "crashed" log
            self.assertEqual(fresh.messages[-1]["content"], INTERRUPTED)

    def test_edits_are_shown_as_a_diff(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = [tool_call("write_file", path="a.txt", content="one\ntwo\n"),
                      tool_call("edit_file", path="a.txt", old="two", new="TWO"),
                      {"role": "assistant", "content": "ok"}]
            events: list[str] = []
            agent = self.make(tmp, FakeBackend(script))
            agent.run("Make a.txt", on_event=events.append)
            for line in ("     +one", "     -two", "     +TWO", "     @@ -1,2 +1,2 @@"):
                self.assertIn(line, events)
            self.assertFalse(any(e.strip().startswith("+++") for e in events))   # header dropped
            tool_text = " ".join(m["content"] for m in agent.messages if m["role"] == "tool")
            self.assertNotIn("+TWO", tool_text)                          # the model never sees it
            short: list[str] = []
            capped = self.make(tmp, FakeBackend([tool_call("write_file", path="b.txt",
                                                           content="1\n2\n3\n4\n"),
                                                 {"role": "assistant", "content": "ok"}]),
                               diff_lines=2)
            capped.run("Make b.txt", on_event=short.append)
            self.assertIn("     ... 3 more diff lines", short)


class CliHelperTests(unittest.TestCase):
    def test_multiline_input(self):
        lines = iter(["first \\", "second"])
        self.assertEqual(read_request(lambda _p: next(lines)), "first \nsecond")
        block = iter(['"""', "a", "", "b", '"""'])
        self.assertEqual(read_request(lambda _p: next(block)), "a\n\nb")
        self.assertEqual(read_request(lambda _p: "﻿/init"), "/init")   # piped byte-order mark

    def test_session_logs_belong_to_the_project(self):
        project = Path("C:/work/shop") if os.name == "nt" else Path("/work/shop")
        config = replace(Config(), workspace=project)
        self.assertEqual(sessions_folder(config), project / ".localagent" / "sessions")
        elsewhere = project.parent / "logs"                            # an absolute choice wins
        self.assertEqual(sessions_folder(replace(config, state_dir=elsewhere)), elsewhere / "sessions")

    def test_shared_setup_helpers(self):
        base = Config()
        self.assertEqual(overlay(base, model=None, sandbox="docker"), replace(base, sandbox="docker"))
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("LOCALAGENT_NUM_CTX", None)
            gemini = replace(base, backend="gemini")
            self.assertEqual(gemini_history(gemini).num_ctx, GEMINI_CONTEXT)
            self.assertEqual(gemini_history(gemini, chosen=True).num_ctx, base.num_ctx)
            self.assertEqual(gemini_history(base).num_ctx, base.num_ctx)       # local models keep 8K
            os.environ["LOCALAGENT_NUM_CTX"] = "4096"                         # set by the user
            self.assertEqual(gemini_history(gemini).num_ctx, base.num_ctx)
        self.assertEqual(shell_argv("x")[-1], "x")
        self.assertEqual(shell_argv("x")[0], "powershell" if os.name == "nt" else "sh")

    def test_todo_understands_done_written_in_the_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            box = ToolBox(replace(Config(), workspace=Path(tmp)), lambda _q: True)
            out = box.call("todo", {"items": ["read files (done)", "[x] write code",
                                              {"text": "run tests", "done": True}, "finish"]})
            self.assertEqual(out.splitlines()[0], "TODO 3/4 done")
            self.assertIn("  [x] read files", out)
            self.assertIn("  [x] write code", out)
            self.assertIn("  [ ] finish", out)

    def test_approver_always_saves_a_rule(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "settings.json"
            approver = Approver(read=lambda _p: "a")
            approver.permissions = Permissions(project_file=project)
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertTrue(approver(Approval("run x", "run_shell", "git status")))
            self.assertEqual(approver.permissions.allow, ["run_shell(git status)"])
            self.assertIn("run_shell(git status)", project.read_text(encoding="utf-8"))
            self.assertFalse(Approver(read=lambda _p: "")("anything"))
            self.assertTrue(Approver(read=lambda _p: "YES")("anything"))

    def test_auto_configure_uses_what_is_available(self):
        args = build_parser().parse_args([])
        clean = {k: v for k, v in os.environ.items()
                 if k not in ("LOCALAGENT_BACKEND", "LOCALAGENT_SANDBOX", "LOCALAGENT_NUM_CTX")}
        with mock.patch.dict(os.environ, clean, clear=True), \
                mock.patch("localagent.cli.sandbox_image_ready", return_value=True), \
                mock.patch("localagent.cli.server_up", return_value=True):
            config, notes = auto_configure(replace(Config(), gemini_api_key="k"), args)
        self.assertEqual((config.backend, config.sandbox, config.fallback_backend, config.num_ctx),
                         ("gemini", "docker", "openai", 32_768))
        self.assertEqual(len(notes), 4)
        explicit = build_parser().parse_args(["--backend", "ollama", "--sandbox", "local"])
        with mock.patch("localagent.cli.sandbox_image_ready", return_value=True):
            config, notes = auto_configure(replace(Config(), gemini_api_key="k"), explicit)
        self.assertEqual((config.backend, config.sandbox, notes), ("ollama", "local", []))

    def test_ctrl_c_keeps_the_conversation_usable(self):
        class Interrupting:
            def chat(self, messages, tools, on_token=None):
                raise KeyboardInterrupt

        with tempfile.TemporaryDirectory() as tmp:
            cfg = replace(Config(), workspace=Path(tmp))
            box = ToolBox(cfg, lambda _q: True)
            agent = Agent(cfg, Interrupting(), box, Meter())
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(handle_request(agent, box, Meter(), "hello"), "")
            self.assertIn("stopped", out.getvalue())


class LimitsAndHintsTests(unittest.TestCase):
    def test_learned_rpm_is_saved_for_other_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            usage = Path(tmp) / "usage.json"
            RateLimiter("m", rpm=12, tpm=10_000, rpd=100, usage_file=usage).set_rpm(4)
            later = RateLimiter("m", rpm=12, tpm=10_000, rpd=100, usage_file=usage)
            later.before_call(10)
            self.assertEqual(later._rpm, 4)

    @unittest.skipUnless(importlib.util.find_spec("ruff"), "ruff not installed")
    def test_undefined_name_rejection_suggests_editing_together(self):
        with tempfile.TemporaryDirectory() as tmp:
            box = ToolBox(replace(Config(), workspace=Path(tmp)), lambda _q: True)
            result = box.call("write_file", {"path": "m.py", "content": "print(missing)\n"})
            self.assertIn(TOGETHER_HINT, result)


if __name__ == "__main__":
    unittest.main()
