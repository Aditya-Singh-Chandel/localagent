"""Tests for the Claude-Code-like features: any-folder use, more languages, todo, shell, compaction."""
import importlib.util
import json
import os
import shutil
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from localagent.agent import COMPACT_PROMPT, Agent
from localagent.checkpoint import Checkpoints
from localagent.cli import INIT_PROMPT, build_config, build_parser
from localagent.config import Config
from localagent.context import project_tree
from localagent.meter import Meter
from localagent.runners import LocalRunner
from localagent.sandbox import walk_files
from localagent.tools import ToolBox
from localagent.verify import verify_file

from .test_agent import FakeBackend, tool_call


def box_for(tmp: str, **kw) -> ToolBox:
    """ToolBox on a temp folder that approves everything."""
    return ToolBox(replace(Config(), workspace=Path(tmp), **kw), approve=lambda _a: True)


class WalkTests(unittest.TestCase):
    def test_skipped_folders_are_never_entered(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ("src/app.py", "node_modules/pkg/index.js", ".localagent/x.json",
                         "venv/lib/a.py", "README.md"):
                (root / name).parent.mkdir(parents=True, exist_ok=True)
                (root / name).write_text("x", encoding="utf-8")
            found = [p.relative_to(root).as_posix() for p in walk_files(root)]
            self.assertEqual(found, ["README.md", "src/app.py"])          # sorted, pruned
            self.assertEqual(project_tree(root, 10), "README.md\nsrc/app.py")
            snap = Checkpoints(root)
            snap.take()
            (root / "node_modules/pkg/index.js").write_text("changed", encoding="utf-8")
            self.assertEqual(snap.undo(), "nothing to undo")              # not snapshotted at all

    def test_search_uses_glob_on_file_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            box = box_for(tmp)
            (Path(tmp) / "a.js").write_text("const needle = 1;\n", encoding="utf-8")
            (Path(tmp) / "b.py").write_text("needle = 2\n", encoding="utf-8")
            self.assertIn("a.js:1", box.call("search", {"pattern": "needle", "glob": "*.js"}))
            self.assertNotIn("a.js", box.call("search", {"pattern": "needle", "glob": "*.py"}))
            self.assertIn("a.js:1", box.call("search", {"pattern": "needle"}))   # default: all


class LanguageTests(unittest.TestCase):
    def check(self, name: str, text: str) -> bool:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / name
            path.write_text(text, encoding="utf-8")
            return verify_file(path, Path(tmp)).ok

    def test_json_and_toml(self):
        self.assertTrue(self.check("a.json", '{"a": 1}'))
        self.assertFalse(self.check("a.json", '{"a": 1,}'))
        self.assertTrue(self.check("p.toml", 'name = "x"\n'))
        if importlib.util.find_spec("tomllib"):          # Python 3.11+; on 3.10 TOML is not checked
            self.assertFalse(self.check("p.toml", "name = \n"))
        self.assertTrue(self.check("notes.md", "anything at all {"))           # unchecked type

    @unittest.skipUnless(shutil.which("node"), "Node.js not installed")
    def test_javascript_with_node(self):
        self.assertTrue(self.check("a.js", "const x = 1;\nmodule.exports = { x };\n"))
        self.assertFalse(self.check("a.js", "const x = ;\n"))

    def test_bad_json_write_is_rolled_back_and_workspace_check_sees_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            box = box_for(tmp)
            self.assertIn("REJECTED", box.call("write_file", {"path": "c.json", "content": "{oops"}))
            self.assertFalse((Path(tmp) / "c.json").exists())
            (Path(tmp) / "d.json").write_text("[1,", encoding="utf-8")            # broken behind our back
            self.assertEqual(len(box.check_workspace()), 1)


class TodoAndShellTests(unittest.TestCase):
    def test_todo_checklist(self):
        with tempfile.TemporaryDirectory() as tmp:
            box = box_for(tmp)
            out = box.call("todo", {"items": [{"text": "read", "done": True}, {"text": "write"}]})
            self.assertEqual(out.splitlines(), ["TODO 1/2 done", "  [x] read", "  [ ] write"])
            self.assertEqual(len(box.todo), 2)
            self.assertIn("ERROR", box.call("todo", {"items": []}))
            self.assertIn("[ ] plain text", box.call("todo", {"items": ["plain text"]}))

    def test_host_shell_is_opt_in_and_asks(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertNotIn("run_shell", [s["function"]["name"] for s in box_for(tmp).specs()])
            asked: list[str] = []
            cfg = replace(Config(), workspace=Path(tmp), allow_host_shell=True)
            box = ToolBox(cfg, approve=lambda action: asked.append(action) or True)
            self.assertIn("run_shell", [s["function"]["name"] for s in box.specs()])
            out = box.call("run_shell", {"command": "echo hello-shell"})
            self.assertIn("hello-shell", out)
            self.assertTrue(asked and "echo hello-shell" in asked[0])
            refused = LocalRunner(Config(), Path(tmp)).run("shell", ["echo x"])
            self.assertEqual(refused[0], 126)

    def test_todo_progress_is_shown_in_full(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = replace(Config(), workspace=Path(tmp))
            script = [tool_call("todo", items=[{"text": "step one"}, {"text": "step two"}]),
                      {"role": "assistant", "content": "ok"}]
            agent = Agent(cfg, FakeBackend(script), ToolBox(cfg, lambda _a: True), Meter())
            events: list[str] = []
            agent.run("Plan it", on_event=events.append)
            self.assertIn("     [ ] step two", events)


class CompactionTests(unittest.TestCase):
    def make(self, tmp, script, **kw):
        cfg = replace(Config(), workspace=Path(tmp), **kw)
        backend = FakeBackend(script)
        return Agent(cfg, backend, ToolBox(cfg, lambda _a: True), Meter()), backend

    def test_manual_compact_replaces_history_with_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            agent, _ = self.make(tmp, [{"role": "assistant", "content": "one"},
                                       {"role": "assistant", "content": "two"},
                                       {"role": "assistant", "content": "SUMMARY: user wants X"}])
            self.assertEqual(agent.compact(), "nothing to compact")
            agent.run("first question")
            agent.run("second question")
            result = agent.compact()
            self.assertIn("compacted", result)
            self.assertEqual([m["role"] for m in agent.messages], ["system", "user", "assistant"])
            self.assertIn("SUMMARY: user wants X", agent.messages[1]["content"])

    def test_empty_summary_keeps_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            agent, _ = self.make(tmp, [{"role": "assistant", "content": "a"},
                                       {"role": "assistant", "content": "b"},
                                       {"role": "assistant", "content": ""}])
            agent.run("q1")
            agent.run("q2")
            before = len(agent.messages)
            self.assertIn("skipped", agent.compact())
            self.assertEqual(len(agent.messages), before)

    def test_automatic_compaction_before_a_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            agent, _ = self.make(tmp, [{"role": "assistant", "content": "x" * 400}] * 3
                                 + [{"role": "assistant", "content": "SUM"},
                                    {"role": "assistant", "content": "answer"}],
                                 num_ctx=3000, compact_at=0.1)   # the system prompt must fit
            for question in ("q1", "q2", "q3"):
                agent.run(question)
            events: list[str] = []
            self.assertEqual(agent.run("q4", on_event=events.append), "answer")
            self.assertTrue(any("summarising" in e for e in events))
            self.assertIn("SUM", agent.messages[1]["content"])
            self.assertIn("300 words", COMPACT_PROMPT)

    def test_reset_and_refresh_reload_agent_notes(self):
        with tempfile.TemporaryDirectory() as tmp:
            agent, _ = self.make(tmp, [])
            self.assertNotIn("USE TABS", agent.messages[0]["content"])
            (Path(tmp) / "AGENT.md").write_text("USE TABS", encoding="utf-8")
            agent.refresh_system()
            self.assertIn("USE TABS", agent.messages[0]["content"])
            agent.reset()
            self.assertEqual(len(agent.messages), 1)
            self.assertIn("USE TABS", agent.messages[0]["content"])


class AnyFolderTests(unittest.TestCase):
    def test_defaults_work_in_current_folder_with_global_quota_file(self):
        self.assertEqual(Config().workspace, Path("."))
        self.assertEqual(Config().usage_file, Path.home() / ".localagent" / "usage.json")
        args = build_parser().parse_args(["--allow-shell"])
        self.assertTrue(build_config(args).allow_host_shell)
        self.assertTrue(INIT_PROMPT.startswith("Create AGENT.md"))

    def test_usage_file_can_be_moved_by_environment(self):
        old = os.environ.get("LOCALAGENT_USAGE_FILE")
        os.environ["LOCALAGENT_USAGE_FILE"] = "elsewhere.json"
        try:
            self.assertEqual(Config.from_env().usage_file, Path("elsewhere.json"))
        finally:
            if old is None:
                del os.environ["LOCALAGENT_USAGE_FILE"]
            else:
                os.environ["LOCALAGENT_USAGE_FILE"] = old
        self.assertTrue(json.dumps(str(Config().usage_file)))


if __name__ == "__main__":
    unittest.main()
