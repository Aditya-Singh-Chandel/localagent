"""Tests for many languages, streaming, helper agents, custom commands, tiers and big folders."""
import contextlib
import io
import os
import shutil
import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from localagent.agent import DELEGATE_SPEC, Agent
from localagent.backends import make_gemini
from localagent.checkpoint import Checkpoints
from localagent.cli import handle_request
from localagent.config import Config
from localagent.meter import Meter
from localagent.openai_compat import read_stream
from localagent.settings import expand_command, load_commands
from localagent.tools import MAX_SEARCH_FILES, ToolBox
from localagent.verify import CHECKED_SUFFIXES, verify_file

from .test_agent import tool_call
from .test_phase8 import RecordingBackend

SAMPLES = {   # suffix: (program needed, valid text, broken text)
    ".ts": ("node", "enum C { A }\nconst x: number = 1;\nexport function f(a: string): string { return a; }\n",
            "function f(x: number { return x; }\n"),
    ".go": ("gofmt", "package main\n\nfunc main() {}\n", "package main\nfunc main( {}\n"),
    ".rs": ("rustfmt", "fn main() { let _x = 1; }\n", "fn main() { let x = ; }\n"),
    ".c": ("gcc", "int main(void) { return 0; }\n", "int main(void) { return 0 }\n"),
    ".cpp": ("g++", "int main() { return 0; }\n", "int main() { return 0 }\n"),
    ".ps1": ("powershell", 'Write-Output "hi"\n', "function f( {\n"),
}


def check(folder: str, name: str, text: str) -> bool:
    path = Path(folder) / name
    path.write_text(text, encoding="utf-8")
    return verify_file(path, Path(folder)).ok


class LanguageTests(unittest.TestCase):
    def test_each_installed_language_checker(self):
        for suffix, (program, good, bad) in SAMPLES.items():
            with self.subTest(suffix=suffix):
                if not shutil.which(program) or (suffix == ".ps1" and os.name != "nt"):
                    self.skipTest(f"{program} not installed")
                with tempfile.TemporaryDirectory() as tmp:
                    self.assertTrue(check(tmp, "good" + suffix, good))
                    self.assertFalse(check(tmp, "bad" + suffix, bad))

    def test_missing_outside_header_is_not_an_error(self):
        if not shutil.which("gcc"):
            self.skipTest("gcc not installed")
        with tempfile.TemporaryDirectory() as tmp:
            self.assertTrue(check(tmp, "uses_lib.c", '#include "not_here.h"\nint main(void){return 0;}\n'))

    def test_unknown_and_tool_less_files_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertTrue(check(tmp, "notes.txt", "anything {"))
            with mock.patch("localagent.verify.shutil.which", return_value=None):
                self.assertTrue(check(tmp, "x.go", "this is not go"))   # no gofmt -> skipped
        self.assertTrue({".ts", ".go", ".rs", ".c", ".ps1", ".yaml"} <= CHECKED_SUFFIXES)


class StreamTests(unittest.TestCase):
    def lines(self, *events: str) -> list[bytes]:
        return [f"data: {e}\n".encode() for e in events] + [b"\n", b"data: [DONE]\n"]

    def test_text_and_tool_calls_are_assembled(self):
        pieces: list[str] = []
        message, usage = read_stream(self.lines(
            '{"choices":[{"delta":{"content":"Hel"}}]}',
            ': keep-alive comment',
            '{"choices":[{"delta":{"content":"lo"}}]}',
            '{"choices":[{"delta":{"tool_calls":[{"index":0,"id":"c1","function":{"name":"read_",'
            '"arguments":"{\\"pa"}}]}}]}',
            '{"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"name":"file",'
            '"arguments":"th\\": \\"a\\"}"},"extra_content":{"google":{"thought_signature":"S"}}}]}}]}',
            '{"choices":[],"usage":{"prompt_tokens":9,"completion_tokens":4}}',
        ), pieces.append)
        self.assertEqual(pieces, ["Hel", "lo"])
        self.assertEqual(message["content"], "Hello")
        call = message["tool_calls"][0]
        self.assertEqual((call["id"], call["function"]["name"]), ("c1", "read_file"))
        self.assertEqual(call["function"]["arguments"], '{"path": "a"}')
        self.assertEqual(call["extra_content"]["google"]["thought_signature"], "S")
        self.assertEqual(usage["prompt_tokens"], 9)

    def test_error_inside_a_stream(self):
        from localagent.llm import LLMError
        with self.assertRaises(LLMError):
            read_stream(self.lines('{"error":{"message":"overloaded"}}'), None)

    def test_summary_is_printed_when_streamed_text_differs(self):
        class Streaming:
            def chat(self, messages, tools, on_token=None):
                on_token("Working on it.")
                from localagent.llm import Reply
                return Reply({"role": "assistant", "content": "Working on it.", "tool_calls": [
                    {"function": {"name": "finish", "arguments": {"summary": "All done: 3 files."}}}]},
                    1, 1, 0.0)

        with tempfile.TemporaryDirectory() as tmp:
            cfg = replace(Config(), workspace=Path(tmp))
            box = ToolBox(cfg, lambda _q: True)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                handle_request(Agent(cfg, Streaming(), box, Meter()), box, Meter(), "Why?")
            self.assertIn("Working on it.", out.getvalue())
            self.assertIn("All done: 3 files.", out.getvalue())

    def test_a_streamed_answer_is_not_printed_twice(self):
        from localagent.llm import Reply
        turns = iter([("Let me look.", [{"function": {"name": "outline", "arguments": {"path": "."}}}]),
                      ("The answer is 42.", [])])

        class TwoTurns:                                  # how local models often answer
            def chat(self, messages, tools, on_token=None):
                text, calls = next(turns)
                on_token(text)
                message = {"role": "assistant", "content": text}
                if calls:
                    message["tool_calls"] = calls
                return Reply(message, 1, 1, 0.0)

        with tempfile.TemporaryDirectory() as tmp:
            cfg = replace(Config(), workspace=Path(tmp))
            box = ToolBox(cfg, lambda _q: True)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                handle_request(Agent(cfg, TwoTurns(), box, Meter()), box, Meter(), "Why?")
        self.assertEqual(out.getvalue().count("The answer is 42."), 1)


class HelperTests(unittest.TestCase):
    def test_delegate_runs_a_read_only_helper_and_returns_its_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "a.py").write_text("def invoice_total():\n    return 1\n", encoding="utf-8")
            script = [tool_call("delegate", task="find invoice code"),             # main agent
                      tool_call("write_file", path="x.py", content="x = 1\n"),     # helper tries
                      tool_call("search", pattern="invoice"),                       # helper reads
                      tool_call("finish", summary="invoice_total is in a.py line 1"),
                      tool_call("finish", summary="Found it")]                     # main agent
            backend = RecordingBackend(script)
            cfg = replace(Config(), workspace=Path(tmp))
            meter = Meter()
            agent = Agent(cfg, backend, ToolBox(cfg, lambda _q: True), meter)
            events: list[str] = []
            self.assertEqual(agent.run("Where is the invoice code?", on_event=events.append), "Found it")
            self.assertIn("delegate", backend.offered[0])                 # main agent may delegate
            self.assertNotIn("delegate", backend.offered[1])              # a helper may not
            self.assertNotIn("write_file", backend.offered[1])            # helpers only read
            self.assertFalse((Path(tmp) / "x.py").exists())
            report = [m["content"] for m in agent.messages if m["role"] == "tool"][0]
            self.assertIn("HELPER REPORT:", report)
            self.assertIn("invoice_total is in a.py", report)
            self.assertEqual(meter.calls, 5)                              # helper calls are counted
            self.assertTrue(any(e.startswith("   | ") for e in events))
            self.assertEqual(DELEGATE_SPEC["function"]["name"], "delegate")

    def test_helper_leaves_the_main_routing_alone(self):
        class Routing(RecordingBackend):
            def __init__(self, messages):
                super().__init__(messages)
                self.resets = self.escalations = 0

            def reset_route(self):
                self.resets += 1

            def escalate(self):
                self.escalations += 1
                return True

        with tempfile.TemporaryDirectory() as tmp:
            bad = tool_call("read_file", path="missing.txt")              # fails: no such file
            backend = Routing([tool_call("delegate", task="look"), bad, bad,
                               tool_call("finish", summary="nothing found"),
                               tool_call("finish", summary="done")])
            cfg = replace(Config(), workspace=Path(tmp))
            Agent(cfg, backend, ToolBox(cfg, lambda _q: True), Meter()).run("Where is X?")
            self.assertEqual(backend.resets, 1)                           # only the main agent
            self.assertEqual(backend.escalations, 0)                      # helper trouble ignored

    def test_helpers_can_be_switched_off(self):
        with tempfile.TemporaryDirectory() as tmp:
            backend = RecordingBackend([{"role": "assistant", "content": "ok"}])
            cfg = replace(Config(), workspace=Path(tmp), helpers=False)
            Agent(cfg, backend, ToolBox(cfg, lambda _q: True), Meter()).run("hi")
            self.assertNotIn("delegate", backend.offered[0])


class CommandAndTierTests(unittest.TestCase):
    def test_custom_commands_load_and_expand(self):
        with tempfile.TemporaryDirectory() as tmp:
            user = Path(tmp) / "user"
            user.mkdir()
            (user / "review.md").write_text("Review $ARGUMENTS for bugs.", encoding="utf-8")
            (user / "Bad Name!.md").write_text("ignored", encoding="utf-8")
            project = Path(tmp) / ".localagent" / "commands"
            project.mkdir(parents=True)
            (project / "review.md").write_text("Project review of $ARGUMENTS.", encoding="utf-8")
            (project / "docs.md").write_text("Write docs.", encoding="utf-8")
            commands = load_commands(Path(tmp), user)
            self.assertEqual(sorted(commands), ["docs", "review"])           # project wins
            self.assertEqual(expand_command(commands["review"], "app.py"), "Project review of app.py.")
            self.assertEqual(expand_command(commands["docs"], "for api.py"), "Write docs.\n\nfor api.py")
            self.assertEqual(expand_command(commands["docs"], ""), "Write docs.")

    def test_paid_tier_puts_strong_models_first_with_higher_limits(self):
        with mock.patch.dict(os.environ, {"LOCALAGENT_GEMINI_TIER": "paid", "GEMINI_API_KEY": "k"}):
            cfg = Config.from_env()
        self.assertEqual((cfg.gemini_tier, cfg.rpm, cfg.rpd), ("paid", 1000, 10_000))
        backend = make_gemini(cfg)
        order = [e.name for e in backend._order()]
        self.assertEqual(order[: len(cfg.gemini_strong_models)], list(cfg.gemini_strong_models))
        self.assertFalse(backend.escalate())                                 # no reserve on paid
        with mock.patch.dict(os.environ, {"LOCALAGENT_RPM": "7"}):
            self.assertEqual(Config.from_env().rpm, 7)


class HookTests(unittest.TestCase):
    def test_after_edit_failure_reaches_the_model_and_after_request_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            hooks = {"after_edit": 'echo "checked {file}"; exit 3', "after_request": 'echo "all tests ok"'}
            box = ToolBox(replace(Config(), workspace=Path(tmp)), lambda _q: True, {"hooks": hooks})
            result = box.call("write_file", {"path": "n.txt", "content": "hi"})
            self.assertTrue(result.startswith("OK: wrote n.txt"))
            self.assertIn("HOOK after_edit failed (exit code 3)", result)
            self.assertIn("checked n.txt", result)
            code, output = box.run_hook("after_request")
            self.assertEqual((code, output.strip()), (0, "all tests ok"))
            self.assertIsNone(ToolBox(replace(Config(), workspace=Path(tmp))).run_hook("after_edit"))

    def test_hooks_merge_from_settings_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            user = Path(tmp) / "user.json"
            user.write_text('{"hooks": {"after_edit": "a", "after_request": "b"}}', encoding="utf-8")
            project = Path(tmp) / ".localagent" / "settings.json"
            project.parent.mkdir()
            project.write_text('{"hooks": {"after_edit": "c"}}', encoding="utf-8")
            from localagent.settings import load_settings
            self.assertEqual(load_settings(Path(tmp), user)["hooks"],
                             {"after_edit": "c", "after_request": "b"})


class BigFolderTests(unittest.TestCase):
    def test_snapshots_switch_off_once_for_huge_folders(self):
        with tempfile.TemporaryDirectory() as tmp:
            for i in range(5):
                (Path(tmp) / f"f{i}.txt").write_text("x", encoding="utf-8")
            snaps = Checkpoints(Path(tmp), max_files=3)
            first = snaps.take()
            self.assertIn("/undo is off", first or "")
            self.assertIsNone(snaps.take())                                  # said only once
            self.assertTrue(snaps.undo().startswith("undo is off"))
            self.assertIsNone(Checkpoints(Path(tmp)).take())                 # normal size: fine

    def test_workspace_check_can_look_only_at_recent_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            old = Path(tmp) / "old.json"
            old.write_text("{broken", encoding="utf-8")
            past = time.time() - 3600
            os.utime(old, (past, past))                                      # broken long ago
            box = ToolBox(replace(Config(), workspace=Path(tmp)), lambda _q: True)
            self.assertEqual(len(box.check_workspace()), 1)
            self.assertEqual(box.check_workspace(since=time.time() - 60), [])
            (Path(tmp) / "new.json").write_text("{also broken", encoding="utf-8")
            self.assertEqual(len(box.check_workspace(since=box.started)), 1)
            self.assertIn("NOT RUN", box.call("run_tests", {}))             # broken this session
            (Path(tmp) / "new.json").unlink()
            self.assertIn("exit code", box.call("run_tests", {}))           # old file does not block

    def test_search_stops_on_enormous_folders(self):
        self.assertGreaterEqual(MAX_SEARCH_FILES, 1000)
        with tempfile.TemporaryDirectory() as tmp:
            for i in range(3):
                (Path(tmp) / f"m{i}.py").write_text("x = 1\n", encoding="utf-8")
            box = ToolBox(replace(Config(), workspace=Path(tmp)), lambda _q: True)
            with mock.patch("localagent.tools.MAX_SEARCH_FILES", 2):
                self.assertIn("stopped after looking at 2 files", box.call("search", {"pattern": "zzz"}))


if __name__ == "__main__":
    unittest.main()
