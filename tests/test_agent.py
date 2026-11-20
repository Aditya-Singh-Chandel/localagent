"""Tests for sandbox, verification, tools and the agent loop (no Ollama needed)."""
import importlib.util
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from localagent.agent import NUDGE, Agent, failed
from localagent.config import Config
from localagent.llm import Reply
from localagent.meter import Meter
from localagent.sandbox import SandboxError, resolve_inside, truncate
from localagent.tools import ToolBox
from localagent.verify import verify_python

GOOD = "def add(a, b):\n    return a + b\n\nprint(add(2, 3))\n"
BAD_SYNTAX = "def add(a, b)\n    return a + b\n"
BAD_SCOPE = "return 5\n"


def make_box(tmp: str, **kw) -> ToolBox:
    """ToolBox on a temp workspace that auto-approves execution."""
    cfg = replace(Config(), workspace=Path(tmp), **kw)
    return ToolBox(cfg, approve=lambda _a: True)


def tool_call(name: str, **args) -> dict:
    """Build the assistant message Ollama would send for one tool call."""
    return {"role": "assistant", "content": "",
            "tool_calls": [{"function": {"name": name, "arguments": args}}]}


class FakeBackend:
    """Replays a fixed list of assistant messages, one per chat() call."""

    def __init__(self, messages: list[dict]) -> None:
        self._messages = list(messages)

    def chat(self, messages, tools, on_token=None) -> Reply:
        return Reply(self._messages.pop(0), prompt_tokens=10, completion_tokens=5, seconds=0.1)


class SandboxTests(unittest.TestCase):
    def test_escape_is_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(SandboxError):
                resolve_inside(Path(tmp), "../outside.txt")

    def test_inside_is_allowed(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertTrue(str(resolve_inside(Path(tmp), "a/b.py")).startswith(str(Path(tmp).resolve())))

    def test_truncate_keeps_both_ends(self):
        out = truncate("A" * 100 + "Z" * 100, 60)
        self.assertTrue(out.startswith("A") and out.endswith("Z") and "omitted" in out)


class VerifyTests(unittest.TestCase):
    def check(self, source: str):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "m.py"
            path.write_text(source, encoding="utf-8")
            return verify_python(path, Path(tmp))

    def test_good_code_passes(self):
        self.assertTrue(self.check(GOOD).ok)

    def test_syntax_error_reports_line(self):
        report = self.check(BAD_SYNTAX)
        self.assertFalse(report.ok)
        self.assertIn("m.py:1", report.render())

    def test_scope_error_caught_by_compile(self):
        self.assertFalse(self.check(BAD_SCOPE).ok)

    @unittest.skipUnless(importlib.util.find_spec("ruff"), "ruff not installed")
    def test_undefined_name_caught_by_ruff(self):
        report = self.check("print(missing_name)\n")
        self.assertFalse(report.ok)
        self.assertIn("F821", report.render())


class ToolBoxTests(unittest.TestCase):
    def test_bad_write_is_rejected_and_rolled_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            box = make_box(tmp)
            result = box.call("write_file", {"path": "a.py", "content": BAD_SYNTAX})
            self.assertIn("REJECTED (1/3)", result)
            self.assertFalse((Path(tmp) / "a.py").exists())

    def test_bad_overwrite_restores_previous_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            box = make_box(tmp, auto_format=False)
            box.call("write_file", {"path": "a.py", "content": GOOD})
            box.call("write_file", {"path": "a.py", "content": BAD_SYNTAX})
            self.assertEqual((Path(tmp) / "a.py").read_text(encoding="utf-8"), GOOD)

    def test_attempts_exhausted_says_stop(self):
        with tempfile.TemporaryDirectory() as tmp:
            box = make_box(tmp)
            for _ in range(2):
                box.call("write_file", {"path": "a.py", "content": BAD_SYNTAX})
            self.assertIn("STOP", box.call("write_file", {"path": "a.py", "content": BAD_SYNTAX}))

    def test_good_write_then_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            box = make_box(tmp)
            self.assertIn("verification passed", box.call("write_file", {"path": "a.py", "content": GOOD}))
            self.assertIn("5", box.call("run_python", {"path": "a.py"}))

    def test_run_refuses_file_broken_behind_our_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            box = make_box(tmp)
            (Path(tmp) / "x.py").write_text(BAD_SYNTAX, encoding="utf-8")
            self.assertIn("NOT RUN", box.call("run_python", {"path": "x.py"}))

    def test_denied_when_user_says_no(self):
        with tempfile.TemporaryDirectory() as tmp:
            box = ToolBox(replace(Config(), workspace=Path(tmp)), approve=lambda _a: False)
            box.call("write_file", {"path": "a.py", "content": GOOD})
            self.assertIn("DENIED", box.call("run_python", {"path": "a.py"}))

    def test_edit_requires_unique_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            box = make_box(tmp)
            box.call("write_file", {"path": "a.py", "content": "x = 1\ny = 1\n"})
            self.assertIn("matched 2", box.call("edit_file", {"path": "a.py", "old": "1", "new": "2"}))
            self.assertIn("OK", box.call("edit_file", {"path": "a.py", "old": "x = 1", "new": "x = 9"}))

    def test_path_escape_returns_error_string(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIn("outside the workspace", make_box(tmp).call("read_file", {"path": "../x"}))

    def test_unknown_tool_and_bad_args(self):
        with tempfile.TemporaryDirectory() as tmp:
            box = make_box(tmp)
            self.assertIn("unknown tool", box.call("nope", {}))
            self.assertIn("bad arguments", box.call("read_file", {}))


class AgentLoopTests(unittest.TestCase):
    def test_reject_fix_run_finish(self):
        """Model writes bad code, sees the errors, fixes it, runs it, and finishes."""
        with tempfile.TemporaryDirectory() as tmp:
            cfg = replace(Config(), workspace=Path(tmp))
            script = [
                tool_call("write_file", path="a.py", content=BAD_SYNTAX),
                tool_call("write_file", path="a.py", content=GOOD),
                tool_call("run_python", path="a.py"),
                {"role": "assistant", "content": "Done: add() works and prints 5."},
            ]
            meter = Meter()
            agent = Agent(cfg, FakeBackend(script), ToolBox(cfg, lambda _a: True), meter)
            answer = agent.run("make an add function")
            self.assertIn("Done", answer)
            self.assertEqual(meter.calls, 4)
            tool_texts = [m["content"] for m in agent.messages if m["role"] == "tool"]
            self.assertIn("REJECTED", tool_texts[0])
            self.assertIn("verification passed", tool_texts[1])
            self.assertIn("5", tool_texts[2])

    def test_max_turns_stops_endless_loop(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = replace(Config(), workspace=Path(tmp), max_turns=3)
            script = [tool_call("list_files")] * 3
            agent = Agent(cfg, FakeBackend(script), ToolBox(cfg), Meter())
            self.assertIn("Stopped after 3", agent.run("loop forever"))

    def test_string_arguments_are_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = replace(Config(), workspace=Path(tmp))
            msg = {"role": "assistant", "content": "", "tool_calls": [
                {"function": {"name": "list_files", "arguments": '{"path": "."}'}}]}
            agent = Agent(cfg, FakeBackend([msg, {"role": "assistant", "content": "ok"}]),
                          ToolBox(cfg), Meter())
            self.assertEqual(agent.run("ls"), "ok")


class Phase3ToolTests(unittest.TestCase):
    def test_outline_lists_classes_and_functions(self):
        with tempfile.TemporaryDirectory() as tmp:
            box = make_box(tmp)
            box.call("write_file", {"path": "m.py", "content":
                                    "class A:\n    def f(self):\n        return 1\n\n\ndef g():\n    return 2\n"})
            out = box.call("outline", {})
            self.assertIn("class A", out)
            self.assertIn("def f", out)
            self.assertIn("def g", out)
            self.assertIn("ERROR", box.call("outline", {"path": "nope"}))

    def test_multi_edit_applies_all_or_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            box = make_box(tmp, auto_format=False)
            box.call("write_file", {"path": "a.py", "content": "x = 1\ny = 2\n"})
            ok = box.call("multi_edit", {"path": "a.py", "edits": [
                {"old": "x = 1", "new": "x = 10"}, {"old": "y = 2", "new": "y = 20"}]})
            self.assertIn("OK", ok)
            self.assertEqual((Path(tmp) / "a.py").read_text(encoding="utf-8"), "x = 10\ny = 20\n")
            bad = box.call("multi_edit", {"path": "a.py", "edits": [
                {"old": "x = 10", "new": "x = 0"}, {"old": "missing", "new": "z"}]})
            self.assertIn("edit 2", bad)
            self.assertEqual((Path(tmp) / "a.py").read_text(encoding="utf-8"), "x = 10\ny = 20\n")
            self.assertIn("non-empty", box.call("multi_edit", {"path": "a.py", "edits": []}))

    def test_edit_file_all_renames_every_occurrence(self):
        with tempfile.TemporaryDirectory() as tmp:
            box = make_box(tmp, auto_format=False)
            box.call("write_file", {"path": "a.py", "content": "from m import calc\nprint(calc())\n"})
            refused = box.call("edit_file", {"path": "a.py", "old": "calc", "new": "total"})
            self.assertIn("all=true", refused)
            done = box.call("edit_file", {"path": "a.py", "old": "calc", "new": "total", "all": True})
            self.assertIn("OK", done)
            self.assertEqual((Path(tmp) / "a.py").read_text(encoding="utf-8"),
                             "from m import total\nprint(total())\n")

    @unittest.skipUnless(importlib.util.find_spec("ruff"), "ruff not installed")
    def test_auto_format_rewrites_style(self):
        with tempfile.TemporaryDirectory() as tmp:
            box = make_box(tmp)
            box.call("write_file", {"path": "a.py", "content": "x   =   1\n"})
            self.assertEqual((Path(tmp) / "a.py").read_text(encoding="utf-8"), "x = 1\n")

    def test_writes_counter_and_read_only_flags(self):
        with tempfile.TemporaryDirectory() as tmp:
            box = make_box(tmp)
            self.assertEqual(box.writes, 0)
            box.call("write_file", {"path": "n.txt", "content": "hi"})
            box.call("write_file", {"path": "a.py", "content": BAD_SYNTAX})   # rejected: not counted
            self.assertEqual(box.writes, 1)
            self.assertTrue(box.is_read_only("search"))
            self.assertFalse(box.is_read_only("write_file"))


class Phase3AgentTests(unittest.TestCase):
    def make_agent(self, script, tmp):
        cfg = replace(Config(), workspace=Path(tmp))
        return Agent(cfg, FakeBackend(script), ToolBox(cfg, approve=lambda _a: True), Meter())

    def test_nudge_when_change_requested_but_nothing_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = [{"role": "assistant", "content": "the bug is X"},
                      tool_call("write_file", path="a.txt", content="fixed"),
                      {"role": "assistant", "content": "done"}]
            agent = self.make_agent(script, tmp)
            self.assertEqual(agent.run("Fix the bug"), "done")
            self.assertTrue(any(m.get("content") == NUDGE for m in agent.messages))

    def test_no_nudge_for_questions(self):
        with tempfile.TemporaryDirectory() as tmp:
            agent = self.make_agent([{"role": "assistant", "content": "because"}], tmp)
            self.assertEqual(agent.run("Why is the sky blue?"), "because")

    def test_parallel_read_only_calls_keep_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "a.txt").write_text("AAA", encoding="utf-8")
            (Path(tmp) / "b.txt").write_text("BBB", encoding="utf-8")
            two = {"role": "assistant", "content": "", "tool_calls": [
                {"function": {"name": "read_file", "arguments": {"path": "a.txt"}}},
                {"function": {"name": "read_file", "arguments": {"path": "b.txt"}}}]}
            agent = self.make_agent([two, {"role": "assistant", "content": "ok"}], tmp)
            events: list[str] = []
            agent.run("show both", on_event=events.append)
            results = [m["content"] for m in agent.messages if m["role"] == "tool"]
            self.assertEqual(results, ["1: AAA", "1: BBB"])
            self.assertEqual(sum(e.startswith("->") for e in events), 2)


    def test_repeated_identical_edit_is_refused_then_stopped(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "a.txt").write_text("abc", encoding="utf-8")
            same = tool_call("edit_file", path="a.txt", old="abc", new="abc")
            agent = self.make_agent([same, same, same, same], tmp)
            answer = agent.run("Edit a.txt")
            self.assertIn("repeating", answer)
            tool_results = [m["content"] for m in agent.messages if m["role"] == "tool"]
            self.assertTrue(tool_results[0].startswith("OK"))
            self.assertTrue(all(r.startswith("ERROR: you already made") for r in tool_results[1:]))


class FinishToolTests(unittest.TestCase):
    def make_agent(self, script, tmp, backend=None):
        cfg = replace(Config(), workspace=Path(tmp), auto_format=False)
        return Agent(cfg, backend or FakeBackend(script), ToolBox(cfg, approve=lambda _a: True), Meter())

    def test_finish_in_same_turn_saves_a_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            turn = {"role": "assistant", "content": "", "tool_calls": [
                {"function": {"name": "write_file", "arguments": {"path": "a.py", "content": GOOD}}},
                {"function": {"name": "run_python", "arguments": {"path": "a.py"}}},
                {"function": {"name": "finish", "arguments": {"summary": "made a.py"}}}]}
            meter = Meter()
            cfg = replace(Config(), workspace=Path(tmp))
            agent = Agent(cfg, FakeBackend([turn]), ToolBox(cfg, approve=lambda _a: True), meter)
            self.assertEqual(agent.run("Create a.py"), "made a.py")
            self.assertEqual(meter.calls, 1)                   # no separate final-answer call
            self.assertEqual(agent.messages[-1]["content"], "OK: finished")   # history stays valid

    def test_finish_refused_when_a_call_failed(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = {"role": "assistant", "content": "", "tool_calls": [
                {"function": {"name": "write_file", "arguments": {"path": "a.py", "content": BAD_SYNTAX}}},
                {"function": {"name": "finish", "arguments": {"summary": "done?"}}}]}
            good = {"role": "assistant", "content": "", "tool_calls": [
                {"function": {"name": "write_file", "arguments": {"path": "a.py", "content": GOOD}}},
                {"function": {"name": "finish", "arguments": {"summary": "fixed"}}}]}
            agent = self.make_agent([bad, good], tmp)
            self.assertEqual(agent.run("Create a.py"), "fixed")
            refusals = [m["content"] for m in agent.messages if m["role"] == "tool"
                        and m["content"].startswith("NOT FINISHED")]
            self.assertEqual(len(refusals), 1)

    def test_finish_refused_when_nothing_was_changed(self):
        with tempfile.TemporaryDirectory() as tmp:
            lazy = tool_call("finish", summary="all good")
            work = {"role": "assistant", "content": "", "tool_calls": [
                {"function": {"name": "write_file", "arguments": {"path": "n.txt", "content": "x"}}},
                {"function": {"name": "finish", "arguments": {"summary": "wrote n.txt"}}}]}
            agent = self.make_agent([lazy, work], tmp)
            self.assertEqual(agent.run("Create n.txt"), "wrote n.txt")
            question = self.make_agent([tool_call("finish", summary="because")], tmp)
            self.assertEqual(question.run("Why?"), "because")    # questions may finish at once

    def test_finish_waits_until_what_was_read_has_been_seen(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "report.js").write_text("console.log('Total: ' + 1);\n", encoding="utf-8")
            hasty = {"role": "assistant", "content": "", "tool_calls": [
                {"function": {"name": "read_file", "arguments": {"path": "report.js"}}},
                {"function": {"name": "finish", "arguments": {"summary": "a monthly report"}}}]}
            informed = tool_call("finish", summary="it prints the total")
            agent = self.make_agent([hasty, informed], tmp)
            self.assertEqual(agent.run("What does report.js do?"), "it prints the total")
            refusals = [m["content"] for m in agent.messages if m["role"] == "tool"
                        and m["content"].startswith("NOT FINISHED")]
            self.assertEqual(len(refusals), 1)
            self.assertIn("read_file", refusals[0])

    def test_failure_detection(self):
        for text in ("ERROR: x", "REJECTED (1/3): x", "NOT RUN: x", "DENIED: x", "exit code 1\nboom"):
            self.assertTrue(failed(text), text)
        for text in ("OK: wrote a.py", "exit code 0\nhi", "1: line", ""):
            self.assertFalse(failed(text), text)

    def test_repeated_failures_escalate_once(self):
        class Escalating(FakeBackend):
            def __init__(self, messages):
                super().__init__(messages)
                self.escalations = 0
                self.resets = 0

            def escalate(self):
                self.escalations += 1
                return self.escalations == 1

            def reset_route(self):
                self.resets += 1

        with tempfile.TemporaryDirectory() as tmp:
            bad = tool_call("run_python", path="missing.py")             # ERROR each time
            backend = Escalating([bad, bad, {"role": "assistant", "content": "gave up"}])
            events: list[str] = []
            agent = self.make_agent([], tmp, backend)
            agent.run("Run it", on_event=events.append)
            self.assertEqual(backend.resets, 1)
            self.assertEqual(sum("stronger model" in e for e in events), 1)


if __name__ == "__main__":
    unittest.main()
