# `tests/` — proving the agent works without a model

**Role:** these tests run in seconds and need no Ollama. A scripted fake model plays the LLM, and a tiny local HTTP
server imitates Ollama / OpenAI responses, so the loop, the verification gates, the safety rules and the real network
code are all exercised deterministically.

Run: `python -m unittest discover -s tests -t .` (use the venv's Python so the ruff/mypy layers are tested too).

## `tests/__init__.py`
```python
1 | """Test package for localagent."""
```
- Makes `tests` a package so `discover -t .` can import it and tests can import each other's helpers.

---
# `tests/test_agent.py` — core behaviour

## Helpers
```python
  1 | """Tests for sandbox, verification, tools and the agent loop (no Ollama needed)."""
  2 | import importlib.util
  3 | import tempfile
  4 | import unittest
  5 | from dataclasses import replace
  6 | from pathlib import Path
  7 | 
  8 | from localagent.agent import NUDGE, Agent, failed
  9 | from localagent.config import Config
 10 | from localagent.llm import Reply
 11 | from localagent.meter import Meter
 12 | from localagent.sandbox import SandboxError, resolve_inside, truncate
 13 | from localagent.tools import ToolBox
 14 | from localagent.verify import verify_python
 15 | 
 16 | GOOD = "def add(a, b):\n    return a + b\n\nprint(add(2, 3))\n"
 17 | BAD_SYNTAX = "def add(a, b)\n    return a + b\n"
 18 | BAD_SCOPE = "return 5\n"
 19 | 
 20 | 
```
- Imports the code under test. `GOOD` is valid code that prints 5. `BAD_SYNTAX` has a missing colon. `BAD_SCOPE` is a
  `return` outside a function: it *parses* but fails to *compile*, proving the compile stage is stronger than parsing.

```python
 21 | def make_box(tmp: str, **kw) -> ToolBox:
 22 |     """ToolBox on a temp workspace that auto-approves execution."""
 23 |     cfg = replace(Config(), workspace=Path(tmp), **kw)
 24 |     return ToolBox(cfg, approve=lambda _a: True)
 25 | 
 26 | 
```
- Builds a `ToolBox` on a temporary folder that auto-approves running code; extra keyword arguments tweak the config.

```python
 27 | def tool_call(name: str, **args) -> dict:
 28 |     """Build the assistant message Ollama would send for one tool call."""
 29 |     return {"role": "assistant", "content": "",
 30 |             "tool_calls": [{"function": {"name": name, "arguments": args}}]}
 31 | 
 32 | 
```
- Builds the assistant message Ollama would send when the model asks for one tool.

```python
 33 | class FakeBackend:
 34 |     """Replays a fixed list of assistant messages, one per chat() call."""
 35 | 
```
```python
 36 |     def __init__(self, messages: list[dict]) -> None:
 37 |         self._messages = list(messages)
 38 | 
```
```python
 39 |     def chat(self, messages, tools, on_token=None) -> Reply:
 40 |         return Reply(self._messages.pop(0), prompt_tokens=10, completion_tokens=5, seconds=0.1)
 41 | 
 42 | 
```
- Replays a fixed list of assistant messages, one per `chat` call, with fixed token counts. It satisfies `ChatBackend`,
  which is why the agent accepts it.

## Sandbox
```python
 43 | class SandboxTests(unittest.TestCase):
```
```python
 44 |     def test_escape_is_blocked(self):
 45 |         with tempfile.TemporaryDirectory() as tmp:
 46 |             with self.assertRaises(SandboxError):
 47 |                 resolve_inside(Path(tmp), "../outside.txt")
 48 | 
```
```python
 49 |     def test_inside_is_allowed(self):
 50 |         with tempfile.TemporaryDirectory() as tmp:
 51 |             self.assertTrue(str(resolve_inside(Path(tmp), "a/b.py")).startswith(str(Path(tmp).resolve())))
 52 | 
```
```python
 53 |     def test_truncate_keeps_both_ends(self):
 54 |         out = truncate("A" * 100 + "Z" * 100, 60)
 55 |         self.assertTrue(out.startswith("A") and out.endswith("Z") and "omitted" in out)
 56 | 
 57 | 
```
- A `..` escape must raise `SandboxError`; a nested path must stay inside the root; `truncate` must keep both the start
  and the end and mention what was omitted.

## Verification layers
```python
 58 | class VerifyTests(unittest.TestCase):
```
```python
 59 |     def check(self, source: str):
 60 |         with tempfile.TemporaryDirectory() as tmp:
 61 |             path = Path(tmp) / "m.py"
 62 |             path.write_text(source, encoding="utf-8")
 63 |             return verify_python(path, Path(tmp))
 64 | 
```
```python
 65 |     def test_good_code_passes(self):
 66 |         self.assertTrue(self.check(GOOD).ok)
 67 | 
```
```python
 68 |     def test_syntax_error_reports_line(self):
 69 |         report = self.check(BAD_SYNTAX)
 70 |         self.assertFalse(report.ok)
 71 |         self.assertIn("m.py:1", report.render())
 72 | 
```
```python
 73 |     def test_scope_error_caught_by_compile(self):
 74 |         self.assertFalse(self.check(BAD_SCOPE).ok)
 75 | 
```
```python
 76 |     @unittest.skipUnless(importlib.util.find_spec("ruff"), "ruff not installed")
 77 |     def test_undefined_name_caught_by_ruff(self):
 78 |         report = self.check("print(missing_name)\n")
 79 |         self.assertFalse(report.ok)
 80 |         self.assertIn("F821", report.render())
 81 | 
 82 | 
```
- `check` writes source to a temp file and verifies it. Good code passes; a syntax error fails and names `m.py:1`; the
  scope error is caught by the compile stage; and — only when ruff is installed — `print(missing_name)` is rejected
  with rule `F821`, proving the second layer really runs.

## The write/run gates
```python
 83 | class ToolBoxTests(unittest.TestCase):
```
```python
 84 |     def test_bad_write_is_rejected_and_rolled_back(self):
 85 |         with tempfile.TemporaryDirectory() as tmp:
 86 |             box = make_box(tmp)
 87 |             result = box.call("write_file", {"path": "a.py", "content": BAD_SYNTAX})
 88 |             self.assertIn("REJECTED (1/3)", result)
 89 |             self.assertFalse((Path(tmp) / "a.py").exists())
 90 | 
```
```python
 91 |     def test_bad_overwrite_restores_previous_content(self):
 92 |         with tempfile.TemporaryDirectory() as tmp:
 93 |             box = make_box(tmp, auto_format=False)
 94 |             box.call("write_file", {"path": "a.py", "content": GOOD})
 95 |             box.call("write_file", {"path": "a.py", "content": BAD_SYNTAX})
 96 |             self.assertEqual((Path(tmp) / "a.py").read_text(encoding="utf-8"), GOOD)
 97 | 
```
```python
 98 |     def test_attempts_exhausted_says_stop(self):
 99 |         with tempfile.TemporaryDirectory() as tmp:
100 |             box = make_box(tmp)
101 |             for _ in range(2):
102 |                 box.call("write_file", {"path": "a.py", "content": BAD_SYNTAX})
103 |             self.assertIn("STOP", box.call("write_file", {"path": "a.py", "content": BAD_SYNTAX}))
104 | 
```
- A bad new file gives `REJECTED (1/3)` and does not exist afterwards (rollback). A bad *overwrite* restores the earlier
  good content. After three failures the message contains `STOP`.

```python
105 |     def test_good_write_then_run(self):
106 |         with tempfile.TemporaryDirectory() as tmp:
107 |             box = make_box(tmp)
108 |             self.assertIn("verification passed", box.call("write_file", {"path": "a.py", "content": GOOD}))
109 |             self.assertIn("5", box.call("run_python", {"path": "a.py"}))
110 | 
```
```python
111 |     def test_run_refuses_file_broken_behind_our_back(self):
112 |         with tempfile.TemporaryDirectory() as tmp:
113 |             box = make_box(tmp)
114 |             (Path(tmp) / "x.py").write_text(BAD_SYNTAX, encoding="utf-8")
115 |             self.assertIn("NOT RUN", box.call("run_python", {"path": "x.py"}))
116 | 
```
```python
117 |     def test_denied_when_user_says_no(self):
118 |         with tempfile.TemporaryDirectory() as tmp:
119 |             box = ToolBox(replace(Config(), workspace=Path(tmp)), approve=lambda _a: False)
120 |             box.call("write_file", {"path": "a.py", "content": GOOD})
121 |             self.assertIn("DENIED", box.call("run_python", {"path": "a.py"}))
122 | 
```
- A good write passes verification and running it prints `5`. A broken file created behind the agent's back is refused
  at run time (`NOT RUN`) — the second check. When the user declines, running returns `DENIED`.

```python
123 |     def test_edit_requires_unique_match(self):
124 |         with tempfile.TemporaryDirectory() as tmp:
125 |             box = make_box(tmp)
126 |             box.call("write_file", {"path": "a.py", "content": "x = 1\ny = 1\n"})
127 |             self.assertIn("matched 2", box.call("edit_file", {"path": "a.py", "old": "1", "new": "2"}))
128 |             self.assertIn("OK", box.call("edit_file", {"path": "a.py", "old": "x = 1", "new": "x = 9"}))
129 | 
```
```python
130 |     def test_path_escape_returns_error_string(self):
131 |         with tempfile.TemporaryDirectory() as tmp:
132 |             self.assertIn("outside the workspace", make_box(tmp).call("read_file", {"path": "../x"}))
133 | 
```
```python
134 |     def test_unknown_tool_and_bad_args(self):
135 |         with tempfile.TemporaryDirectory() as tmp:
136 |             box = make_box(tmp)
137 |             self.assertIn("unknown tool", box.call("nope", {}))
138 |             self.assertIn("bad arguments", box.call("read_file", {}))
139 | 
140 | 
```
- `edit_file` refuses an ambiguous match (2 hits) and accepts a unique one. Path escapes, unknown tools and missing
  arguments all come back as error *strings*, never exceptions.

## The loop
```python
141 | class AgentLoopTests(unittest.TestCase):
```
```python
142 |     def test_reject_fix_run_finish(self):
143 |         """Model writes bad code, sees the errors, fixes it, runs it, and finishes."""
144 |         with tempfile.TemporaryDirectory() as tmp:
145 |             cfg = replace(Config(), workspace=Path(tmp))
146 |             script = [
147 |                 tool_call("write_file", path="a.py", content=BAD_SYNTAX),
148 |                 tool_call("write_file", path="a.py", content=GOOD),
149 |                 tool_call("run_python", path="a.py"),
150 |                 {"role": "assistant", "content": "Done: add() works and prints 5."},
151 |             ]
152 |             meter = Meter()
153 |             agent = Agent(cfg, FakeBackend(script), ToolBox(cfg, lambda _a: True), meter)
154 |             answer = agent.run("make an add function")
155 |             self.assertIn("Done", answer)
156 |             self.assertEqual(meter.calls, 4)
157 |             tool_texts = [m["content"] for m in agent.messages if m["role"] == "tool"]
158 |             self.assertIn("REJECTED", tool_texts[0])
159 |             self.assertIn("verification passed", tool_texts[1])
160 |             self.assertIn("5", tool_texts[2])
161 | 
```
- **The key scenario:** the scripted model writes bad code, sees `REJECTED`, writes good code, runs it, and gives a
  final answer. Asserts 4 model calls and the content of each tool result in order.

```python
162 |     def test_max_turns_stops_endless_loop(self):
163 |         with tempfile.TemporaryDirectory() as tmp:
164 |             cfg = replace(Config(), workspace=Path(tmp), max_turns=3)
165 |             script = [tool_call("list_files")] * 3
166 |             agent = Agent(cfg, FakeBackend(script), ToolBox(cfg), Meter())
167 |             self.assertIn("Stopped after 3", agent.run("loop forever"))
168 | 
```
```python
169 |     def test_string_arguments_are_accepted(self):
170 |         with tempfile.TemporaryDirectory() as tmp:
171 |             cfg = replace(Config(), workspace=Path(tmp))
172 |             msg = {"role": "assistant", "content": "", "tool_calls": [
173 |                 {"function": {"name": "list_files", "arguments": '{"path": "."}'}}]}
174 |             agent = Agent(cfg, FakeBackend([msg, {"role": "assistant", "content": "ok"}]),
175 |                           ToolBox(cfg), Meter())
176 |             self.assertEqual(agent.run("ls"), "ok")
177 | 
178 | 
```
- A model that never stops is halted by `max_turns` with a "Stopped after 3" message. Arguments sent as a JSON *string*
  are accepted.

## Phase 3/4 tools
```python
179 | class Phase3ToolTests(unittest.TestCase):
```
```python
180 |     def test_outline_lists_classes_and_functions(self):
181 |         with tempfile.TemporaryDirectory() as tmp:
182 |             box = make_box(tmp)
183 |             box.call("write_file", {"path": "m.py", "content":
184 |                                     "class A:\n    def f(self):\n        return 1\n\n\ndef g():\n    return 2\n"})
185 |             out = box.call("outline", {})
186 |             self.assertIn("class A", out)
187 |             self.assertIn("def f", out)
188 |             self.assertIn("def g", out)
189 |             self.assertIn("ERROR", box.call("outline", {"path": "nope"}))
190 | 
```
```python
191 |     def test_multi_edit_applies_all_or_nothing(self):
192 |         with tempfile.TemporaryDirectory() as tmp:
193 |             box = make_box(tmp, auto_format=False)
194 |             box.call("write_file", {"path": "a.py", "content": "x = 1\ny = 2\n"})
195 |             ok = box.call("multi_edit", {"path": "a.py", "edits": [
196 |                 {"old": "x = 1", "new": "x = 10"}, {"old": "y = 2", "new": "y = 20"}]})
197 |             self.assertIn("OK", ok)
198 |             self.assertEqual((Path(tmp) / "a.py").read_text(encoding="utf-8"), "x = 10\ny = 20\n")
199 |             bad = box.call("multi_edit", {"path": "a.py", "edits": [
200 |                 {"old": "x = 10", "new": "x = 0"}, {"old": "missing", "new": "z"}]})
201 |             self.assertIn("edit 2", bad)
202 |             self.assertEqual((Path(tmp) / "a.py").read_text(encoding="utf-8"), "x = 10\ny = 20\n")
203 |             self.assertIn("non-empty", box.call("multi_edit", {"path": "a.py", "edits": []}))
204 | 
```
```python
205 |     def test_edit_file_all_renames_every_occurrence(self):
206 |         with tempfile.TemporaryDirectory() as tmp:
207 |             box = make_box(tmp, auto_format=False)
208 |             box.call("write_file", {"path": "a.py", "content": "from m import calc\nprint(calc())\n"})
209 |             refused = box.call("edit_file", {"path": "a.py", "old": "calc", "new": "total"})
210 |             self.assertIn("all=true", refused)
211 |             done = box.call("edit_file", {"path": "a.py", "old": "calc", "new": "total", "all": True})
212 |             self.assertIn("OK", done)
213 |             self.assertEqual((Path(tmp) / "a.py").read_text(encoding="utf-8"),
214 |                              "from m import total\nprint(total())\n")
215 | 
```
```python
216 |     @unittest.skipUnless(importlib.util.find_spec("ruff"), "ruff not installed")
217 |     def test_auto_format_rewrites_style(self):
218 |         with tempfile.TemporaryDirectory() as tmp:
219 |             box = make_box(tmp)
220 |             box.call("write_file", {"path": "a.py", "content": "x   =   1\n"})
221 |             self.assertEqual((Path(tmp) / "a.py").read_text(encoding="utf-8"), "x = 1\n")
222 | 
```
```python
223 |     def test_writes_counter_and_read_only_flags(self):
224 |         with tempfile.TemporaryDirectory() as tmp:
225 |             box = make_box(tmp)
226 |             self.assertEqual(box.writes, 0)
227 |             box.call("write_file", {"path": "n.txt", "content": "hi"})
228 |             box.call("write_file", {"path": "a.py", "content": BAD_SYNTAX})   # rejected: not counted
229 |             self.assertEqual(box.writes, 1)
230 |             self.assertTrue(box.is_read_only("search"))
231 |             self.assertFalse(box.is_read_only("write_file"))
232 | 
233 | 
```
- `outline` lists a class, its method and a top-level function, and reports a missing path as an error.
- `multi_edit` applies two edits in order; a later failing edit (`edit 2`) leaves the file untouched (all-or-nothing);
  an empty list is refused. Auto-format is switched off here so the exact text can be compared.
- `edit_file` with a name that occurs twice is refused with an `all=true` hint; with `all=true` both occurrences are
  renamed. (An earlier draft of this test renamed the letter `f`, which also broke `from`; the write gate rejected it -
  a handy live demonstration that the gate works.)
- Auto-format (only when ruff is installed): `x   =   1` is rewritten to `x = 1` after the write passes verification.
- The `writes` counter counts a plain text write but not a rejected Python write; `search` is flagged read-only and
  `write_file` is not.

```python
234 | class Phase3AgentTests(unittest.TestCase):
```
```python
235 |     def make_agent(self, script, tmp):
236 |         cfg = replace(Config(), workspace=Path(tmp))
237 |         return Agent(cfg, FakeBackend(script), ToolBox(cfg, approve=lambda _a: True), Meter())
238 | 
```
```python
239 |     def test_nudge_when_change_requested_but_nothing_written(self):
240 |         with tempfile.TemporaryDirectory() as tmp:
241 |             script = [{"role": "assistant", "content": "the bug is X"},
242 |                       tool_call("write_file", path="a.txt", content="fixed"),
243 |                       {"role": "assistant", "content": "done"}]
244 |             agent = self.make_agent(script, tmp)
245 |             self.assertEqual(agent.run("Fix the bug"), "done")
246 |             self.assertTrue(any(m.get("content") == NUDGE for m in agent.messages))
247 | 
```
```python
248 |     def test_no_nudge_for_questions(self):
249 |         with tempfile.TemporaryDirectory() as tmp:
250 |             agent = self.make_agent([{"role": "assistant", "content": "because"}], tmp)
251 |             self.assertEqual(agent.run("Why is the sky blue?"), "because")
252 | 
```
```python
253 |     def test_parallel_read_only_calls_keep_order(self):
254 |         with tempfile.TemporaryDirectory() as tmp:
255 |             (Path(tmp) / "a.txt").write_text("AAA", encoding="utf-8")
256 |             (Path(tmp) / "b.txt").write_text("BBB", encoding="utf-8")
257 |             two = {"role": "assistant", "content": "", "tool_calls": [
258 |                 {"function": {"name": "read_file", "arguments": {"path": "a.txt"}}},
259 |                 {"function": {"name": "read_file", "arguments": {"path": "b.txt"}}}]}
260 |             agent = self.make_agent([two, {"role": "assistant", "content": "ok"}], tmp)
261 |             events: list[str] = []
262 |             agent.run("show both", on_event=events.append)
263 |             results = [m["content"] for m in agent.messages if m["role"] == "tool"]
264 |             self.assertEqual(results, ["1: AAA", "1: BBB"])
265 |             self.assertEqual(sum(e.startswith("->") for e in events), 2)
266 | 
267 | 
```
```python
268 |     def test_repeated_identical_edit_is_refused_then_stopped(self):
269 |         with tempfile.TemporaryDirectory() as tmp:
270 |             (Path(tmp) / "a.txt").write_text("abc", encoding="utf-8")
271 |             same = tool_call("edit_file", path="a.txt", old="abc", new="abc")
272 |             agent = self.make_agent([same, same, same, same], tmp)
273 |             answer = agent.run("Edit a.txt")
274 |             self.assertIn("repeating", answer)
275 |             tool_results = [m["content"] for m in agent.messages if m["role"] == "tool"]
276 |             self.assertTrue(tool_results[0].startswith("OK"))
277 |             self.assertTrue(all(r.startswith("ERROR: you already made") for r in tool_results[1:]))
278 | 
279 | 
```
- `make_agent` builds an agent on a temp folder around a scripted fake model.
- **Nudge:** for "Fix the bug" the model first answers in text only; the agent injects the `NUDGE` message, the model
  then writes a file and the final answer is "done". For the question "Why is the sky blue?" there is no nudge and the
  first answer is returned.
- **Parallel reads:** two `read_file` calls in one turn produce results `1: AAA` and `1: BBB` in the original order, and
  two `->` progress events are reported.
- **Repeat guard:** a model that sends the identical `edit_file` four times gets one `OK`, then three
  `ERROR: you already made this exact call` results, and the request ends with the "repeating" stop message.

## The `finish` tool and escalation
```python
280 | class FinishToolTests(unittest.TestCase):
```
```python
281 |     def make_agent(self, script, tmp, backend=None):
282 |         cfg = replace(Config(), workspace=Path(tmp), auto_format=False)
283 |         return Agent(cfg, backend or FakeBackend(script), ToolBox(cfg, approve=lambda _a: True), Meter())
284 | 
```
```python
285 |     def test_finish_in_same_turn_saves_a_call(self):
286 |         with tempfile.TemporaryDirectory() as tmp:
287 |             turn = {"role": "assistant", "content": "", "tool_calls": [
288 |                 {"function": {"name": "write_file", "arguments": {"path": "a.py", "content": GOOD}}},
289 |                 {"function": {"name": "run_python", "arguments": {"path": "a.py"}}},
290 |                 {"function": {"name": "finish", "arguments": {"summary": "made a.py"}}}]}
291 |             meter = Meter()
292 |             cfg = replace(Config(), workspace=Path(tmp))
293 |             agent = Agent(cfg, FakeBackend([turn]), ToolBox(cfg, approve=lambda _a: True), meter)
294 |             self.assertEqual(agent.run("Create a.py"), "made a.py")
295 |             self.assertEqual(meter.calls, 1)                   # no separate final-answer call
296 |             self.assertEqual(agent.messages[-1]["content"], "OK: finished")   # history stays valid
297 | 
```
```python
298 |     def test_finish_refused_when_a_call_failed(self):
299 |         with tempfile.TemporaryDirectory() as tmp:
300 |             bad = {"role": "assistant", "content": "", "tool_calls": [
301 |                 {"function": {"name": "write_file", "arguments": {"path": "a.py", "content": BAD_SYNTAX}}},
302 |                 {"function": {"name": "finish", "arguments": {"summary": "done?"}}}]}
303 |             good = {"role": "assistant", "content": "", "tool_calls": [
304 |                 {"function": {"name": "write_file", "arguments": {"path": "a.py", "content": GOOD}}},
305 |                 {"function": {"name": "finish", "arguments": {"summary": "fixed"}}}]}
306 |             agent = self.make_agent([bad, good], tmp)
307 |             self.assertEqual(agent.run("Create a.py"), "fixed")
308 |             refusals = [m["content"] for m in agent.messages if m["role"] == "tool"
309 |                         and m["content"].startswith("NOT FINISHED")]
310 |             self.assertEqual(len(refusals), 1)
311 | 
```
```python
312 |     def test_finish_refused_when_nothing_was_changed(self):
313 |         with tempfile.TemporaryDirectory() as tmp:
314 |             lazy = tool_call("finish", summary="all good")
315 |             work = {"role": "assistant", "content": "", "tool_calls": [
316 |                 {"function": {"name": "write_file", "arguments": {"path": "n.txt", "content": "x"}}},
317 |                 {"function": {"name": "finish", "arguments": {"summary": "wrote n.txt"}}}]}
318 |             agent = self.make_agent([lazy, work], tmp)
319 |             self.assertEqual(agent.run("Create n.txt"), "wrote n.txt")
320 |             question = self.make_agent([tool_call("finish", summary="because")], tmp)
321 |             self.assertEqual(question.run("Why?"), "because")    # questions may finish at once
322 | 
```
```python
323 |     def test_finish_waits_until_what_was_read_has_been_seen(self):
324 |         with tempfile.TemporaryDirectory() as tmp:
325 |             Path(tmp, "report.js").write_text("console.log('Total: ' + 1);\n", encoding="utf-8")
326 |             hasty = {"role": "assistant", "content": "", "tool_calls": [
327 |                 {"function": {"name": "read_file", "arguments": {"path": "report.js"}}},
328 |                 {"function": {"name": "finish", "arguments": {"summary": "a monthly report"}}}]}
329 |             informed = tool_call("finish", summary="it prints the total")
330 |             agent = self.make_agent([hasty, informed], tmp)
331 |             self.assertEqual(agent.run("What does report.js do?"), "it prints the total")
332 |             refusals = [m["content"] for m in agent.messages if m["role"] == "tool"
333 |                         and m["content"].startswith("NOT FINISHED")]
334 |             self.assertEqual(len(refusals), 1)
335 |             self.assertIn("read_file", refusals[0])
336 | 
```
```python
337 |     def test_failure_detection(self):
338 |         for text in ("ERROR: x", "REJECTED (1/3): x", "NOT RUN: x", "DENIED: x", "exit code 1\nboom"):
339 |             self.assertTrue(failed(text), text)
340 |         for text in ("OK: wrote a.py", "exit code 0\nhi", "1: line", ""):
341 |             self.assertFalse(failed(text), text)
342 | 
```
```python
343 |     def test_repeated_failures_escalate_once(self):
344 |         class Escalating(FakeBackend):
345 |             def __init__(self, messages):
346 |                 super().__init__(messages)
347 |                 self.escalations = 0
348 |                 self.resets = 0
349 | 
350 |             def escalate(self):
351 |                 self.escalations += 1
352 |                 return self.escalations == 1
353 | 
354 |             def reset_route(self):
355 |                 self.resets += 1
356 | 
357 |         with tempfile.TemporaryDirectory() as tmp:
358 |             bad = tool_call("run_python", path="missing.py")             # ERROR each time
359 |             backend = Escalating([bad, bad, {"role": "assistant", "content": "gave up"}])
360 |             events: list[str] = []
361 |             agent = self.make_agent([], tmp, backend)
362 |             agent.run("Run it", on_event=events.append)
363 |             self.assertEqual(backend.resets, 1)
364 |             self.assertEqual(sum("stronger model" in e for e in events), 1)
365 | 
366 | 
```
- Write + run + finish in one response: the request ends after **one** model call with the summary, and the history
  ends with the `OK: finished` tool result (still a valid conversation).
- A finish next to a rejected write is refused once (`NOT FINISHED`); the corrected turn finishes.
- "Create n.txt" answered with an immediate finish is refused (nothing written); a question may finish at once.
- A question answered with `read_file` + `finish` in one response is refused once (the reason names `read_file`);
  the answer given after seeing the file is the one returned.
- `failed` recognises every failure form and accepts successes, `exit code 0` and empty text.
- A fake backend with `escalate`/`reset_route`: two failing runs trigger exactly one "stronger model" event, and the
  route is reset once at the start of the request.

```python
367 | if __name__ == "__main__":
368 |     unittest.main()
```
- Lets you run the file directly.

---
# `tests/test_phase2.py` — streaming, backends, context, undo, sessions, search, evals

## A fake model server
```python
  1 | """Tests for streaming, backends, context, undo, sessions, search and the eval harness."""
  2 | import json
  3 | import tempfile
  4 | import threading
  5 | import unittest
  6 | from dataclasses import replace
  7 | from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
  8 | from pathlib import Path
  9 | 
 10 | from evals.run_evals import run_task
 11 | from evals.tasks import FIZZBUZZ
 12 | from localagent.agent import Agent
 13 | from localagent.backends import make_backend
 14 | from localagent.checkpoint import Checkpoints
 15 | from localagent.config import Config
 16 | from localagent.context import build_system_prompt
 17 | from localagent.doctor import run_doctor
 18 | from localagent.llm import LLMError, OllamaClient
 19 | from localagent.meter import Meter
 20 | from localagent.openai_compat import OpenAICompatClient, to_openai
 21 | from localagent.session import SessionLog, latest_session, load_session
 22 | from localagent.tools import ToolBox
 23 | from tests.test_agent import FakeBackend, tool_call
 24 | 
 25 | FIZZ_CODE = ("def fizzbuzz(n):\n    out = []\n    for i in range(1, n + 1):\n"
 26 |              "        s = ('Fizz' if i % 3 == 0 else '') + ('Buzz' if i % 5 == 0 else '')\n"
 27 |              "        out.append(s or str(i))\n    return out\n")
 28 | 
 29 | 
```
- Imports the standard-library HTTP server pieces plus the modules under test. `FIZZ_CODE` is a correct fizzbuzz used by
  the eval-harness test.

```python
 30 | class FakeServer:
 31 |     """Local HTTP server answering with canned bytes; records what it was sent."""
 32 | 
```
```python
 33 |     def __init__(self, status: int, body: bytes) -> None:
 34 |         outer = self
 35 |         self.received: list[dict] = []
 36 |         self.headers: list[dict] = []
 37 | 
 38 |         class Handler(BaseHTTPRequestHandler):
 39 |             def do_POST(self):  # noqa: N802 (name fixed by http.server)
 40 |                 length = int(self.headers["Content-Length"])
 41 |                 outer.received.append(json.loads(self.rfile.read(length)))
 42 |                 outer.headers.append(dict(self.headers))
 43 |                 self.send_response(status)
 44 |                 self.send_header("Content-Length", str(len(body)))
 45 |                 self.end_headers()
 46 |                 self.wfile.write(body)
 47 | 
 48 |             def do_GET(self):  # noqa: N802 (name fixed by http.server)
 49 |                 self.send_response(status)
 50 |                 self.send_header("Content-Length", str(len(body)))
 51 |                 self.end_headers()
 52 |                 self.wfile.write(body)
 53 | 
 54 |             def log_message(self, *args):
 55 |                 pass
 56 | 
 57 |         self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
 58 |         self.url = f"http://127.0.0.1:{self._server.server_port}"
 59 |         threading.Thread(target=self._server.serve_forever, daemon=True).start()
 60 | 
```
- Starts a real HTTP server on a free port (`("127.0.0.1", 0)`) in a background thread. Its handler records each
  request's JSON body and headers and replies with a canned status and body — so `post()` and the clients are tested
  against genuine sockets. `log_message` is silenced to keep test output clean.

```python
 61 |     def close(self) -> None:
 62 |         self._server.shutdown()
 63 |         self._server.server_close()
 64 | 
 65 | 
```
```python
 66 | def ndjson(*objects: dict) -> bytes:
 67 |     return "".join(json.dumps(o) + "\n" for o in objects).encode()
 68 | 
 69 | 
```
- `close` stops the server. `ndjson` encodes dictionaries as one-JSON-object-per-line bytes, Ollama's streaming format.

## Ollama client
```python
 70 | class OllamaClientTests(unittest.TestCase):
```
```python
 71 |     def test_streams_text_and_counts_tokens(self):
 72 |         body = ndjson(
 73 |             {"message": {"role": "assistant", "content": "Hel"}, "done": False},
 74 |             {"message": {"role": "assistant", "content": "lo"}, "done": False},
 75 |             {"message": {"role": "assistant", "content": ""}, "done": True,
 76 |              "prompt_eval_count": 7, "eval_count": 2})
 77 |         server = FakeServer(200, body)
 78 |         self.addCleanup(server.close)
 79 |         seen: list[str] = []
 80 |         reply = OllamaClient(replace(Config(), host=server.url)).chat(
 81 |             [{"role": "user", "content": "hi"}], [], seen.append)
 82 |         self.assertEqual(seen, ["Hel", "lo"])
 83 |         self.assertEqual(reply.message["content"], "Hello")
 84 |         self.assertEqual((reply.prompt_tokens, reply.completion_tokens), (7, 2))
 85 |         self.assertTrue(server.received[0]["stream"])
 86 | 
```
```python
 87 |     def test_collects_tool_calls(self):
 88 |         call = {"function": {"name": "list_files", "arguments": {}}}
 89 |         body = ndjson({"message": {"role": "assistant", "content": "", "tool_calls": [call]},
 90 |                        "done": True, "eval_count": 1})
 91 |         server = FakeServer(200, body)
 92 |         self.addCleanup(server.close)
 93 |         reply = OllamaClient(replace(Config(), host=server.url)).chat([], [])
 94 |         self.assertEqual(reply.message["tool_calls"], [call])
 95 | 
```
```python
 96 |     def test_http_error_becomes_llm_error(self):
 97 |         server = FakeServer(404, b'{"error": "model not found"}')
 98 |         self.addCleanup(server.close)
 99 |         with self.assertRaisesRegex(LLMError, "HTTP 404"):
100 |             OllamaClient(replace(Config(), host=server.url)).chat([], [])
101 | 
```
```python
102 |     def test_unreachable_server_becomes_llm_error(self):
103 |         with self.assertRaisesRegex(LLMError, "Cannot reach"):
104 |             OllamaClient(replace(Config(), host="http://127.0.0.1:9")).chat([], [])
105 | 
```
```python
106 |     def test_error_line_in_stream(self):
107 |         server = FakeServer(200, ndjson({"error": "out of memory"}))
108 |         self.addCleanup(server.close)
109 |         with self.assertRaisesRegex(LLMError, "out of memory"):
110 |             OllamaClient(replace(Config(), host=server.url)).chat([], [])
111 | 
112 | 
```
- Text pieces reach the callback in order and are joined; token counts come from the final line; the request asked for
  streaming. Tool calls are collected. A 404, an unreachable port (9) and an `{"error": ...}` line each become `LLMError`.

## Doctor
```python
136 | class DoctorTests(unittest.TestCase):
```
```python
137 |     def tags(self, *names: str) -> FakeServer:
138 |         server = FakeServer(200, json.dumps({"models": [{"name": n} for n in names]}).encode())
139 |         self.addCleanup(server.close)
140 |         return server
141 | 
```
```python
142 |     def test_healthy_when_model_installed(self):
143 |         server = self.tags("qwen3:8b")
144 |         healthy, lines = run_doctor(replace(Config(), host=server.url))
145 |         self.assertTrue(healthy)
146 |         self.assertTrue(any("model qwen3:8b is installed" in line for line in lines))
147 | 
```
```python
148 |     def test_untagged_name_means_latest(self):
149 |         server = self.tags("tinyllama:latest")
150 |         healthy, _ = run_doctor(replace(Config(), host=server.url, model="tinyllama"))
151 |         self.assertTrue(healthy)
152 | 
```
```python
153 |     def test_missing_model_gives_pull_command(self):
154 |         server = self.tags("other:1b")
155 |         healthy, lines = run_doctor(replace(Config(), host=server.url))
156 |         self.assertFalse(healthy)
157 |         self.assertTrue(any("ollama pull qwen3:8b" in line for line in lines))
158 | 
```
```python
159 |     def test_unreachable_server_is_reported(self):
160 |         healthy, lines = run_doctor(replace(Config(), host="http://127.0.0.1:9"))
161 |         self.assertFalse(healthy)
162 |         self.assertTrue(any("not reachable" in line for line in lines))
163 | 
164 | 
```
- `tags` starts a fake server whose `/api/tags` lists the given models (the fake server also answers `GET` for this).
  The doctor is healthy when the model is installed, treats `tinyllama` as `tinyllama:latest`, prints the exact
  `ollama pull` command when the model is missing, and reports an unreachable server as a failure.

## OpenAI-compatible client and registry
```python
113 | class OpenAIClientTests(unittest.TestCase):
```
```python
114 |     def test_round_trip_with_tool_call_and_auth(self):
115 |         body = json.dumps({"choices": [{"message": {"content": "", "tool_calls": [
116 |             {"id": "c1", "function": {"name": "read_file", "arguments": "{\"path\": \"a.py\"}"}}]}}],
117 |             "usage": {"prompt_tokens": 11, "completion_tokens": 3}}).encode()
118 |         server = FakeServer(200, body)
119 |         self.addCleanup(server.close)
120 |         config = replace(Config(), backend="openai", api_base=server.url, api_key="secret",
121 |                          stream=False)
122 |         reply = OpenAICompatClient(config).chat([{"role": "user", "content": "x"}], [])
123 |         self.assertEqual(reply.message["tool_calls"][0]["id"], "c1")
124 |         self.assertEqual((reply.prompt_tokens, reply.completion_tokens), (11, 3))
125 |         self.assertEqual(server.headers[0]["Authorization"], "Bearer secret")
126 | 
```
```python
127 |     def test_message_conversion(self):
128 |         converted = to_openai([
129 |             {"role": "assistant", "content": "", "tool_calls": [
130 |                 {"id": "c1", "function": {"name": "f", "arguments": {"a": 1}}}]},
131 |             {"role": "tool", "tool_name": "f", "tool_call_id": "c1", "content": "ok"}])
132 |         self.assertEqual(converted[0]["tool_calls"][0]["function"]["arguments"], '{"a": 1}')
133 |         self.assertEqual(converted[1]["tool_call_id"], "c1")
134 | 
135 | 
```
```python
165 | class BackendRegistryTests(unittest.TestCase):
```
```python
166 |     def test_known_and_unknown(self):
167 |         self.assertIsInstance(make_backend(Config()), OllamaClient)
168 |         self.assertIsInstance(make_backend(replace(Config(), backend="openai")), OpenAICompatClient)
169 |         with self.assertRaisesRegex(ValueError, "unknown backend"):
170 |             make_backend(replace(Config(), backend="nope"))
171 | 
172 | 
```
- Tool calls and usage numbers parse correctly and the bearer token is sent. Conversion turns dict arguments into JSON
  text and keeps the tool-call id link. The registry builds the right class per name and rejects unknown names.

## Context, undo, sessions
```python
173 | class ContextTests(unittest.TestCase):
```
```python
174 |     def test_prompt_contains_tree_and_notes(self):
175 |         with tempfile.TemporaryDirectory() as tmp:
176 |             root = Path(tmp)
177 |             (root / "a.py").write_text("x = 1\n", encoding="utf-8")
178 |             (root / "AGENT.md").write_text("Use tabs never.", encoding="utf-8")
179 |             (root / "__pycache__").mkdir()
180 |             prompt = build_system_prompt(replace(Config(), workspace=root))
181 |             self.assertIn("a.py", prompt)
182 |             self.assertIn("Use tabs never.", prompt)
183 |             self.assertNotIn("__pycache__", prompt)
184 | 
```
```python
185 |     def test_tree_is_capped(self):
186 |         with tempfile.TemporaryDirectory() as tmp:
187 |             for n in range(10):
188 |                 (Path(tmp) / f"f{n}.txt").write_text("x", encoding="utf-8")
189 |             prompt = build_system_prompt(replace(Config(), workspace=Path(tmp), tree_entries=3))
190 |             self.assertIn("...", prompt)
191 |             self.assertNotIn("f9.txt", prompt)
192 | 
193 | 
```
- The system prompt includes files and `AGENT.md` text but not `__pycache__`; the tree is capped with `...`.

```python
194 | class CheckpointTests(unittest.TestCase):
```
```python
195 |     def test_undo_restores_deletes_and_recreates(self):
196 |         with tempfile.TemporaryDirectory() as tmp:
197 |             root = Path(tmp)
198 |             (root / "keep.txt").write_text("v1", encoding="utf-8")
199 |             (root / "gone.txt").write_text("bye", encoding="utf-8")
200 |             points = Checkpoints(root)
201 |             points.take()
202 |             (root / "keep.txt").write_text("v2", encoding="utf-8")   # modified
203 |             (root / "gone.txt").unlink()                              # deleted
204 |             (root / "new.txt").write_text("n", encoding="utf-8")      # created
205 |             message = points.undo()
206 |             self.assertEqual((root / "keep.txt").read_text(encoding="utf-8"), "v1")
207 |             self.assertEqual((root / "gone.txt").read_text(encoding="utf-8"), "bye")
208 |             self.assertFalse((root / "new.txt").exists())
209 |             self.assertIn("restored 2", message)
210 |             self.assertEqual(points.undo(), "nothing to undo")
211 | 
212 | 
```
- After modifying one file, deleting another and creating a third, `undo` restores the first two, removes the third,
  reports 2 restored, and a second `undo` says nothing to undo.

```python
213 | class SessionTests(unittest.TestCase):
```
```python
214 |     def test_log_and_resume(self):
215 |         with tempfile.TemporaryDirectory() as tmp:
216 |             cfg = replace(Config(), workspace=Path(tmp) / "ws")
217 |             log = SessionLog(Path(tmp) / "sessions")
218 |             agent = Agent(cfg, FakeBackend([{"role": "assistant", "content": "hi there"}]),
219 |                           ToolBox(cfg), Meter(), log)
220 |             agent.run("hello")
221 |             saved = load_session(latest_session(Path(tmp) / "sessions"))
222 |             self.assertEqual([m["role"] for m in saved], ["user", "assistant"])
223 |             fresh = Agent(cfg, FakeBackend([]), ToolBox(cfg), Meter())
224 |             fresh.restore(saved)
225 |             self.assertEqual(fresh.messages[-1]["content"], "hi there")
226 |             self.assertEqual(fresh.messages[0]["role"], "system")
227 | 
```
```python
228 |     def test_latest_of_empty_folder(self):
229 |         with tempfile.TemporaryDirectory() as tmp:
230 |             self.assertIsNone(latest_session(Path(tmp)))
231 | 
232 | 
```
- A run is logged as user + assistant messages; a fresh agent restores them and still starts with a system message. An
  empty folder has no latest session.

## Search, workspace check, ids
```python
233 | class SearchAndWorkspaceTests(unittest.TestCase):
```
```python
234 |     def test_search_finds_lines_and_reports_bad_regex(self):
235 |         with tempfile.TemporaryDirectory() as tmp:
236 |             box = ToolBox(replace(Config(), workspace=Path(tmp)), lambda _a: True)
237 |             box.call("write_file", {"path": "a.py", "content": "def hello():\n    return 1\n"})
238 |             self.assertIn("a.py:1: def hello():", box.call("search", {"pattern": "def \\w+"}))
239 |             self.assertEqual(box.call("search", {"pattern": "zzz"}), "no matches")
240 |             self.assertIn("invalid regular expression", box.call("search", {"pattern": "("}))
241 | 
```
```python
242 |     def test_check_workspace_lists_broken_files(self):
243 |         with tempfile.TemporaryDirectory() as tmp:
244 |             box = ToolBox(replace(Config(), workspace=Path(tmp)))
245 |             (Path(tmp) / "ok.py").write_text("x = 1\n", encoding="utf-8")
246 |             (Path(tmp) / "bad.py").write_text("def f(:\n", encoding="utf-8")
247 |             problems = box.check_workspace()
248 |             self.assertEqual(len(problems), 1)
249 |             self.assertIn("bad.py", problems[0])
250 | 
```
```python
251 |     def test_agent_assigns_tool_call_ids(self):
252 |         with tempfile.TemporaryDirectory() as tmp:
253 |             cfg = replace(Config(), workspace=Path(tmp))
254 |             script = [tool_call("list_files"), {"role": "assistant", "content": "done"}]
255 |             agent = Agent(cfg, FakeBackend(script), ToolBox(cfg), Meter())
256 |             agent.run("ls")
257 |             tool_message = next(m for m in agent.messages if m["role"] == "tool")
258 |             self.assertTrue(tool_message["tool_call_id"].startswith("call_"))
259 | 
260 | 
```
- `search` finds `file:line: text`, reports "no matches", and reports a bad regex politely. `check_workspace` returns
  exactly the broken file. The agent gives every tool call an id starting with `call_`.

## Eval harness
```python
261 | class EvalHarnessTests(unittest.TestCase):
```
```python
262 |     def test_passing_run_is_scored_pass(self):
263 |         script = [tool_call("write_file", path="fizzbuzz.py", content=FIZZ_CODE),
264 |                   {"role": "assistant", "content": "done"}]
265 |         result = run_task(FIZZBUZZ, Config(), FakeBackend(script))
266 |         self.assertTrue(result.passed)
267 |         self.assertEqual(result.calls, 2)
268 | 
```
```python
269 |     def test_do_nothing_run_is_scored_fail(self):
270 |         idk = {"role": "assistant", "content": "idk"}
271 |         result = run_task(FIZZBUZZ, Config(), FakeBackend([idk, idk]))   # 2nd reply answers the nudge
272 |         self.assertFalse(result.passed)
273 |         self.assertEqual(result.calls, 2)
274 | 
275 | 
```
- A scripted "model" that writes correct fizzbuzz is scored PASS (2 calls); one that does nothing is scored FAIL —
  proving the scoreboard can tell good runs from bad ones. The do-nothing model is asked twice (2 calls) because the
  first useless answer to a "Create ..." request triggers the nudge; the second one still changes nothing.

```python
276 | if __name__ == "__main__":
277 |     unittest.main()
```
- Lets you run the file directly.

---
# `tests/test_server.py` — the HTTP service

**Role:** proves the API, the token check and the per-session isolation using a real local socket (port 0 = any free
port) and a scripted fake model, so no model server is needed.

## Helpers
```python
  1 | """Tests for the HTTP service mode, using a real local socket and a scripted fake model."""
  2 | import json
  3 | import tempfile
  4 | import threading
  5 | import unittest
  6 | import urllib.error
  7 | import urllib.request
  8 | from dataclasses import replace
  9 | from pathlib import Path
 10 | 
 11 | from localagent.config import Config
 12 | from localagent.server import SessionManager, build_server
 13 | 
 14 | from .test_agent import FakeBackend, tool_call
 15 | 
 16 | 
```
- Imports the standard-library HTTP client, the server pieces, and the fake model helpers from `test_agent.py`.

```python
 17 | def call(base: str, method: str, path: str, body: dict | None = None, token: str = ""):
 18 |     """Send one request; return (status, parsed JSON) even for HTTP error codes."""
 19 |     data = None if body is None else json.dumps(body).encode("utf-8")
 20 |     request = urllib.request.Request(base + path, data=data, method=method)
 21 |     if token:
 22 |         request.add_header("Authorization", f"Bearer {token}")
 23 |     try:
 24 |         with urllib.request.urlopen(request, timeout=20) as reply:
 25 |             return reply.status, json.loads(reply.read())
 26 |     except urllib.error.HTTPError as exc:
 27 |         with exc:
 28 |             return exc.code, json.loads(exc.read())
 29 | 
 30 | 
```
- Sends one request (optionally with a JSON body and bearer token) and returns `(status, parsed JSON)`. HTTP error codes
  are returned instead of raised so tests can assert on 401/404/400.

## Tests
```python
 31 | class ServerTests(unittest.TestCase):
```
```python
 32 |     def start(self, script, token="secret"):
 33 |         tmp = tempfile.TemporaryDirectory()
 34 |         self.addCleanup(tmp.cleanup)
 35 |         manager = SessionManager(replace(Config(), auto_approve=True), Path(tmp.name),
 36 |                                  backend_factory=lambda _cfg: FakeBackend(script))
 37 |         server = build_server(manager, "127.0.0.1", 0, token)
 38 |         threading.Thread(target=server.serve_forever, daemon=True).start()
 39 |         self.addCleanup(server.server_close)
 40 |         self.addCleanup(server.shutdown)
 41 |         return f"http://127.0.0.1:{server.server_port}"
 42 | 
```
- `start` builds a manager whose backend factory always returns the same scripted fake, launches the server on a daemon
  thread, registers cleanup, and returns its base URL. A temp folder holds the session workspaces.

```python
 43 |     def test_health_needs_no_token(self):
 44 |         base = self.start([])
 45 |         self.assertEqual(call(base, "GET", "/health"), (200, {"status": "ok"}))
 46 | 
```
```python
 47 |     def test_requests_without_token_are_rejected(self):
 48 |         base = self.start([])
 49 |         self.assertEqual(call(base, "POST", "/sessions")[0], 401)
 50 |         self.assertEqual(call(base, "GET", "/sessions/x/files")[0], 401)
 51 |         self.assertEqual(call(base, "POST", "/sessions", token="wrong")[0], 401)
 52 | 
```
- `/health` works without a token; creating a session or listing files without a token, or with a wrong one, gives 401.

```python
 53 |     def test_full_conversation_creates_files(self):
 54 |         script = [tool_call("write_file", path="hello.txt", content="hi"),
 55 |                   {"role": "assistant", "content": "created"}]
 56 |         base = self.start(script)
 57 |         status, created = call(base, "POST", "/sessions", token="secret")
 58 |         self.assertEqual(status, 201)
 59 |         path = f"/sessions/{created['id']}"
 60 |         status, reply = call(base, "POST", path + "/messages", {"text": "Create hello.txt"}, "secret")
 61 |         self.assertEqual((status, reply["answer"], reply["calls"]), (200, "created", 2))
 62 |         self.assertTrue(any("write_file" in e for e in reply["events"]))
 63 |         self.assertEqual(call(base, "GET", path + "/files", token="secret")[1], {"files": ["hello.txt"]})
 64 | 
```
- Create a session (201), send "Create hello.txt" (the fake model writes the file, then answers "created"): the reply
  has the answer, 2 model calls and a `write_file` event, and the files endpoint lists `hello.txt`.

```python
 65 |     def test_bad_requests(self):
 66 |         base = self.start([])
 67 |         _, created = call(base, "POST", "/sessions", token="secret")
 68 |         path = f"/sessions/{created['id']}/messages"
 69 |         self.assertEqual(call(base, "POST", path, {"text": "  "}, "secret")[0], 400)
 70 |         self.assertEqual(call(base, "POST", path, {"nope": 1}, "secret")[0], 400)
 71 |         self.assertEqual(call(base, "POST", "/sessions/unknown/messages", {"text": "x"}, "secret")[0], 404)
 72 |         self.assertEqual(call(base, "GET", "/sessions/unknown/files", token="secret")[0], 404)
 73 |         self.assertEqual(call(base, "GET", "/elsewhere", token="secret")[0], 404)
 74 |         self.assertEqual(call(base, "POST", "/elsewhere", token="secret")[0], 404)
 75 | 
```
- Blank text and a missing `text` field → 400; unknown session ids → 404 for both messages and files; unknown paths → 404
  for GET and POST.

```python
 76 |     def test_sessions_have_separate_workspaces(self):
 77 |         script = [tool_call("write_file", path="only_a.txt", content="x"),
 78 |                   {"role": "assistant", "content": "ok"}]
 79 |         base = self.start(script, token="")
 80 |         _, a = call(base, "POST", "/sessions")
 81 |         _, b = call(base, "POST", "/sessions")
 82 |         call(base, "POST", f"/sessions/{a['id']}/messages", {"text": "Create only_a.txt"})
 83 |         self.assertEqual(call(base, "GET", f"/sessions/{a['id']}/files")[1], {"files": ["only_a.txt"]})
 84 |         self.assertEqual(call(base, "GET", f"/sessions/{b['id']}/files")[1], {"files": []})
 85 | 
 86 | 
```
- With no token configured, two sessions are created and only the first receives a file; the second session's file list is
  empty - isolation works.

```python
 87 | class SessionLimitTests(unittest.TestCase):
```
```python
 88 |     def manager(self, clock, **kw):
 89 |         tmp = tempfile.TemporaryDirectory()
 90 |         self.addCleanup(tmp.cleanup)
 91 |         return SessionManager(Config(), Path(tmp.name), backend_factory=lambda _c: FakeBackend([]),
 92 |                               clock=clock, **kw)
 93 | 
```
```python
 94 |     def test_idle_sessions_expire_and_activity_keeps_them(self):
 95 |         now = [0.0]
 96 |         manager = self.manager(lambda: now[0], ttl=100.0)
 97 |         kept, dropped = manager.create(), manager.create()
 98 |         now[0] = 80.0
 99 |         self.assertIsNotNone(manager.get(kept))        # touching it renews its lifetime
100 |         now[0] = 150.0
101 |         self.assertIsNotNone(manager.get(kept))        # 70 s since last use: alive
102 |         self.assertIsNone(manager.get(dropped))        # 150 s idle: expired
103 | 
```
```python
104 |     def test_close_all_closes_every_session(self):
105 |         manager = self.manager(lambda: 0.0)
106 |         ids = [manager.create(), manager.create()]
107 |         sessions = [manager.get(i) for i in ids]
108 |         closed: list[str] = []
109 |         for session_id, session in zip(ids, sessions, strict=True):
110 |             assert session is not None
111 |             session.toolbox.close = lambda sid=session_id: closed.append(sid)   # type: ignore
112 |         manager.close_all()
113 |         self.assertEqual(sorted(closed), sorted(ids))   # each sandbox would be stopped
114 |         self.assertIsNone(manager.get(ids[0]))         # and the sessions are gone
115 | 
```
```python
116 |     def test_session_limit_returns_429_and_frees_after_expiry(self):
117 |         now = [0.0]
118 |         tmp = tempfile.TemporaryDirectory()
119 |         self.addCleanup(tmp.cleanup)
120 |         manager = SessionManager(Config(), Path(tmp.name), backend_factory=lambda _c: FakeBackend([]),
121 |                                  max_sessions=1, ttl=10.0, clock=lambda: now[0])
122 |         server = build_server(manager, "127.0.0.1", 0, "")
123 |         threading.Thread(target=server.serve_forever, daemon=True).start()
124 |         self.addCleanup(server.server_close)
125 |         self.addCleanup(server.shutdown)
126 |         base = f"http://127.0.0.1:{server.server_port}"
127 |         self.assertEqual(call(base, "POST", "/sessions")[0], 201)
128 |         self.assertEqual(call(base, "POST", "/sessions")[0], 429)
129 |         now[0] = 11.0                                  # the first session is now idle too long
130 |         self.assertEqual(call(base, "POST", "/sessions")[0], 201)
131 | 
132 | 
```
- `manager` builds a `SessionManager` with a fake model and a clock the test controls (a one-item list).
- **Expiry:** two sessions with a 100 s TTL; at t=80 one is touched (renewing it); at t=150 the touched one is still alive
  (70 s idle) and the untouched one is gone (150 s idle).
- **Shutdown:** `close_all` closes both sessions' toolboxes (recorded by replacing `close` with a function that notes
  the session id; the `sid=session_id` default fixes each id at definition time) and forgets the sessions.
- **Limit:** with `max_sessions=1` over real HTTP, the second `POST /sessions` returns 429; after the clock passes the TTL
  the first session has expired, so creating a session succeeds again (201).

```python
133 | if __name__ == "__main__":
134 |     unittest.main()
```
- Lets you run the file directly.

---
# `tests/test_phase4.py` — Gemini limits, retries, fallback, and the Docker sandbox

**Role:** proves the hosted-model safety net and the sandbox without spending real quota: a fake clock replaces real
waiting, scripted fake models replace Gemini, and the local fake HTTP server checks the exact wire format. Only the
last test uses real Docker, and it is skipped automatically when Docker is not running.

## Helpers
```python
  1 | """Tests for Gemini support (rate limits, retries, fallback) and the Docker sandbox."""
  2 | import json
  3 | import tempfile
  4 | import unittest
  5 | from dataclasses import replace
  6 | from pathlib import Path
  7 | from unittest import mock
  8 | 
  9 | from localagent.backends import make_backend, model_limits
 10 | from localagent.cli import model_label
 11 | from localagent.config import Config
 12 | from localagent.doctor import run_doctor
 13 | from localagent.llm import LLMError, Reply
 14 | from localagent.openai_compat import OpenAICompatClient, to_openai
 15 | from localagent.ratelimit import QuotaExhausted, RateLimiter, pacific_day
 16 | from localagent.resilient import Entry, FallbackBackend, estimate_tokens, server_delay
 17 | from localagent.runners import DockerRunner, LocalRunner, docker_available
 18 | from localagent.tools import ToolBox
 19 | 
 20 | from .test_phase2 import FakeServer
 21 | 
 22 | 
```
- Imports the modules under test and `FakeServer` from `test_phase2.py`.

```python
 23 | class FakeClock:
 24 |     """A clock the test moves by hand; sleeping just advances it."""
 25 | 
```
```python
 26 |     def __init__(self, start: float = 1_700_000_000.0) -> None:
 27 |         self.now = start
 28 |         self.slept: list[float] = []
 29 | 
```
```python
 30 |     def __call__(self) -> float:
 31 |         return self.now
 32 | 
```
```python
 33 |     def sleep(self, seconds: float) -> None:
 34 |         self.slept.append(seconds)
 35 |         self.now += seconds
 36 | 
 37 | 
```
- A clock that only moves when told. Calling it returns the current fake time; `sleep(s)` records `s` and moves time
  forward by `s`, so a 60-second rate-limit wait finishes instantly but is still measured.

## Rate limiter
```python
 38 | class RateLimiterTests(unittest.TestCase):
```
```python
 39 |     def test_requests_per_minute_wait(self):
 40 |         clock = FakeClock()
 41 |         limiter = RateLimiter("m", rpm=2, tpm=10_000, rpd=100, clock=clock, sleep=clock.sleep)
 42 |         for _ in range(3):
 43 |             limiter.before_call(10)
 44 |         self.assertEqual(len(clock.slept), 1)                # third call had to wait
 45 |         self.assertAlmostEqual(sum(clock.slept), 60.0)
 46 | 
```
```python
 47 |     def test_tokens_per_minute_wait_and_too_big_request(self):
 48 |         clock = FakeClock()
 49 |         limiter = RateLimiter("m", rpm=100, tpm=1000, rpd=100, clock=clock, sleep=clock.sleep)
 50 |         limiter.before_call(600)
 51 |         limiter.after_call(600)
 52 |         limiter.before_call(600)                             # 600 + 600 > 1000: waits
 53 |         self.assertTrue(clock.slept)
 54 |         with self.assertRaises(LLMError):
 55 |             limiter.before_call(5000)                        # can never fit in one minute
 56 | 
```
```python
 57 |     def test_daily_quota_persists_and_resets_next_day(self):
 58 |         with tempfile.TemporaryDirectory() as tmp:
 59 |             usage = Path(tmp) / "usage.json"
 60 |             clock = FakeClock()
 61 |             first = RateLimiter("m", rpm=100, tpm=10_000, rpd=2, usage_file=usage, clock=clock)
 62 |             first.before_call(1)
 63 |             first.after_call(7)
 64 |             first.before_call(1)
 65 |             again = RateLimiter("m", rpm=100, tpm=10_000, rpd=2, usage_file=usage, clock=clock)
 66 |             self.assertTrue(again.exhausted())               # counted on disk, survives restart
 67 |             with self.assertRaises(QuotaExhausted):
 68 |                 again.before_call(1)
 69 |             self.assertIn("2/2 requests, 7 tokens", again.status())
 70 |             other = RateLimiter("x", rpm=100, tpm=10_000, rpd=2, usage_file=usage, clock=clock)
 71 |             self.assertIn("0/2 requests", other.status())    # each model has its own counter
 72 |             clock.now += 86_400                              # next day
 73 |             self.assertFalse(again.exhausted())
 74 | 
```
```python
 75 |     def test_mark_exhausted_and_pacific_day(self):
 76 |         limiter = RateLimiter("m", rpm=10, tpm=10_000, rpd=5)
 77 |         limiter.mark_exhausted()
 78 |         self.assertTrue(limiter.exhausted())
 79 |         self.assertEqual(pacific_day(0.0), "1969-12-31")     # midnight UTC is 4 pm in California
 80 | 
 81 | 
```
- With 2 requests/minute, the third request waits exactly 60 s.
- With 1000 tokens/minute, two 600-token requests cannot share a minute (the second waits); a 5000-token request is
  refused outright.
- Daily quota of 2: after two requests a *new* limiter reading the same file is exhausted (the count survived a
  "restart") and refuses with `QuotaExhausted`; the status line shows requests and tokens; another model has its own
  counter; one day later the quota is free again.
- `mark_exhausted` works, and timestamp 0 (midnight UTC, 1 Jan 1970) is still 31 Dec 1969 in California.

## Fallback chain
```python
 82 | class ScriptedBackend:
 83 |     """Returns or raises the given items in order, one per chat() call."""
 84 | 
```
```python
 85 |     def __init__(self, items):
 86 |         self.items = list(items)
 87 |         self.calls = 0
 88 | 
```
```python
 89 |     def chat(self, messages, tools, on_token=None):
 90 |         self.calls += 1
 91 |         item = self.items.pop(0)
 92 |         if isinstance(item, Exception):
 93 |             raise item
 94 |         return item
 95 | 
 96 | 
```
```python
 97 | def reply(text: str) -> Reply:
 98 |     return Reply({"role": "assistant", "content": text}, 10, 5, 0.1)
 99 | 
100 | 
```
- `ScriptedBackend` returns or raises prepared items in order and counts calls; `reply` makes a simple answer worth
  10 + 5 tokens.

```python
101 | class FallbackTests(unittest.TestCase):
```
```python
102 |     def run_chain(self, *entries, retries=2):
103 |         slept: list[float] = []
104 |         backend = FallbackBackend(list(entries), retries, sleep=slept.append)
105 |         return backend, slept
106 | 
```
```python
107 |     def test_retries_busy_model_then_succeeds(self):
108 |         first = ScriptedBackend([LLMError("busy", 503), reply("hi")])
109 |         backend, slept = self.run_chain(Entry("a", first))
110 |         self.assertEqual(backend.chat([], []).message["content"], "hi")
111 |         self.assertEqual((first.calls, slept, backend.last_model), (2, [2.0], "a"))
112 | 
```
```python
113 |     def test_server_retry_delay_is_respected(self):
114 |         error = LLMError('HTTP 429: {"retryDelay": "17s"}', 429)
115 |         self.assertEqual(server_delay(error), 17.0)
116 |         first = ScriptedBackend([error, reply("ok")])
117 |         backend, slept = self.run_chain(Entry("a", first))
118 |         backend.chat([], [])
119 |         self.assertEqual(slept, [18.0])
120 | 
```
```python
121 |     def test_falls_back_after_retries_and_on_daily_quota(self):
122 |         busy = ScriptedBackend([LLMError("x", 503)] * 3)
123 |         daily = ScriptedBackend([LLMError("429 quota: GenerateRequestsPerDay exceeded", 429)])
124 |         limiter = RateLimiter("b", rpm=100, tpm=10_000, rpd=10)
125 |         local = ScriptedBackend([reply("local")])
126 |         backend, _ = self.run_chain(Entry("a", busy), Entry("b", daily, limiter), Entry("c", local))
127 |         self.assertEqual(backend.chat([], []).message["content"], "local")
128 |         self.assertEqual(busy.calls, 3)                      # 1 try + 2 retries
129 |         self.assertTrue(limiter.exhausted())                 # server said "per day"
130 |         self.assertEqual(backend.last_model, "c")
131 | 
```
```python
132 |     def test_exhausted_model_is_skipped_without_a_call(self):
133 |         limiter = RateLimiter("a", rpm=100, tpm=10_000, rpd=1)
134 |         limiter.mark_exhausted()
135 |         skipped = ScriptedBackend([])
136 |         backend, _ = self.run_chain(Entry("a", skipped, limiter), Entry("b", ScriptedBackend([reply("b")])))
137 |         self.assertEqual(backend.chat([], []).message["content"], "b")
138 |         self.assertEqual(skipped.calls, 0)
139 |         self.assertEqual(len(backend.status()), 1)
140 | 
```
```python
141 |     def test_bad_request_is_not_hidden_and_all_failing_explains(self):
142 |         backend, _ = self.run_chain(Entry("a", ScriptedBackend([LLMError("bad", 400)])))
143 |         with self.assertRaises(LLMError) as caught:
144 |             backend.chat([], [])
145 |         self.assertEqual(caught.exception.status, 400)
146 |         down = ScriptedBackend([LLMError("no network")] * 3)
147 |         backend, _ = self.run_chain(Entry("a", down))
148 |         with self.assertRaisesRegex(LLMError, "every model failed"):
149 |             backend.chat([], [])
150 |         with self.assertRaises(ValueError):
151 |             FallbackBackend([])
152 | 
```
```python
153 |     def test_server_daily_limit_is_learned_and_announced(self):
154 |         notices: list[str] = []
155 |         error = LLMError('HTTP 429: {"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier", '
156 |                          '"quotaValue": "20"}', 429)
157 |         limiter = RateLimiter("a", rpm=100, tpm=10_000, rpd=1500)
158 |         backend = FallbackBackend([Entry("a", ScriptedBackend([error]), limiter),
159 |                                    Entry("b", ScriptedBackend([reply("b")]))],
160 |                                   sleep=lambda _s: None, on_notice=notices.append)
161 |         backend.chat([], [])
162 |         self.assertTrue(limiter.exhausted())
163 |         self.assertIn("1/20 requests", limiter.status())          # the real limit, not 1500
164 |         self.assertIn("used up", limiter.status())
165 |         self.assertTrue(any("Google's limit: 20/day" in n for n in notices))
166 | 
```
```python
167 |     def test_per_model_limit_overrides(self):
168 |         config = replace(Config(), gemini_limits=("m1=5/20", "m2 = 7/30"))
169 |         self.assertEqual(model_limits(config, "m1"), (5, 20))
170 |         self.assertEqual(model_limits(config, "m2"), (7, 30))
171 |         self.assertEqual(model_limits(config, "other"), (config.rpm, config.rpd))
172 |         with self.assertRaisesRegex(ValueError, "model=rpm/rpd"):
173 |             model_limits(replace(config, gemini_limits=("m1=fast",)), "m1")
174 | 
```
```python
175 |     def test_limiter_counts_tokens_of_successful_calls(self):
176 |         limiter = RateLimiter("a", rpm=100, tpm=10_000, rpd=10)
177 |         backend, _ = self.run_chain(Entry("a", ScriptedBackend([reply("x")]), limiter))
178 |         backend.chat([{"role": "user", "content": "hello"}], [])
179 |         self.assertIn("1/10 requests, 15 tokens", limiter.status())
180 |         self.assertGreater(estimate_tokens([{"content": "x" * 400}], []), 90)
181 | 
182 | 
```
- `run_chain` builds a chain whose sleeps are recorded instead of performed.
- A 503 then success: 2 calls, one 2-second pause, `last_model` set.
- A 429 saying `retryDelay: 17s` waits 18 s (17 + 1 safety second).
- Chain of three: the first model stays busy (1 try + 2 retries = 3 calls), the second reports a *per-day* 429 (its
  limiter becomes exhausted), the third (the "local" one) answers.
- An exhausted model is skipped with zero calls; `status()` lists the rate-limited model.
- An HTTP 400 is raised immediately (status preserved); when every model fails the error says "every model failed";
  an empty chain is refused.
- A per-day 429 carrying `"quotaValue": "20"` (the exact shape Google sent) marks the model exhausted, its status
  shows `1/20` and "used up", and a notice names Google's limit. Per-model overrides parse with or without spaces,
  unknown models get the defaults, and a malformed entry explains the format.
- A successful call records 15 tokens and 1 request; the size estimate is about 4 characters per token.

## Routing
```python
183 | class RoutingTests(unittest.TestCase):
```
```python
184 |     def test_escalation_puts_strong_models_first_until_reset(self):
185 |         cheap = ScriptedBackend([reply("cheap"), reply("cheap again")])
186 |         strong = ScriptedBackend([reply("strong")])
187 |         backend = FallbackBackend([Entry("cheap", cheap, RateLimiter("c", 100, 10_000, 100))],
188 |                                   strong=[Entry("strong", strong, RateLimiter("s", 100, 10_000, 100))])
189 |         self.assertEqual(backend.chat([], []).message["content"], "cheap")
190 |         self.assertTrue(backend.escalate())
191 |         self.assertFalse(backend.escalate())                 # already escalated
192 |         self.assertEqual(backend.chat([], []).message["content"], "strong")
193 |         backend.reset_route()
194 |         self.assertEqual(backend.chat([], []).message["content"], "cheap again")
195 |         self.assertEqual(len(backend.status()), 2)
196 |         self.assertFalse(FallbackBackend([Entry("x", cheap)]).escalate())   # nothing to escalate to
197 | 
```
```python
198 |     def test_strong_models_are_tried_after_cheap_ones_fail(self):
199 |         limiter = RateLimiter("c", 100, 10_000, 1)
200 |         limiter.mark_exhausted()
201 |         backend = FallbackBackend([Entry("c", ScriptedBackend([]), limiter)],
202 |                                   strong=[Entry("s", ScriptedBackend([reply("s")]))])
203 |         self.assertEqual(backend.chat([], []).message["content"], "s")
204 | 
```
```python
205 |     def test_missing_model_is_skipped_for_good(self):
206 |         gone = ScriptedBackend([LLMError("HTTP 404: model no longer available", 404)])
207 |         other = ScriptedBackend([reply("one"), reply("two")])
208 |         notices: list[str] = []
209 |         backend = FallbackBackend([Entry("gone", gone), Entry("other", other)],
210 |                                   sleep=lambda _s: None, on_notice=notices.append)
211 |         self.assertEqual(backend.chat([], []).message["content"], "one")
212 |         self.assertEqual(backend.chat([], []).message["content"], "two")
213 |         self.assertEqual(gone.calls, 1)                       # never asked again
214 |         self.assertTrue(any("HTTP 404" in n for n in notices))
215 | 
```
```python
216 |     def test_per_minute_limit_is_learned(self):
217 |         limiter = RateLimiter("a", rpm=12, tpm=10_000, rpd=100)
218 |         error = LLMError('HTTP 429: {"quotaId": "GenerateRequestsPerMinutePerProjectPerModel-FreeTier", '
219 |                          '"quotaValue": "5"}', 429)
220 |         backend = FallbackBackend([Entry("a", ScriptedBackend([error, reply("ok")]), limiter)],
221 |                                   sleep=lambda _s: None)
222 |         backend.chat([], [])
223 |         self.assertEqual(limiter._rpm, 5)
224 |         limiter.set_rpm(50)                                  # never raised above what we know
225 |         self.assertEqual(limiter._rpm, 5)
226 | 
227 | 
```
- Cheap answers first; after `escalate()` (which reports `True` once) the strong model answers; after `reset_route()`
  the cheap one again. Status covers both; a chain without reserve cannot escalate.
- When the cheap model is used up, the strong reserve is still tried before giving up.
- A model answering 404 is asked once, announced, and never asked again; the next model answers both requests.
- A per-minute 429 with `quotaValue` 5 lowers the limiter from 12 to 5; a higher value never raises it again.

## Gemini wire format
```python
228 | class GeminiWireTests(unittest.TestCase):
```
```python
229 |     def test_thought_signature_round_trip_reasoning_and_hidden_tokens(self):
230 |         body = {"choices": [{"message": {"content": None, "tool_calls": [{
231 |             "id": "c1", "type": "function",
232 |             "function": {"name": "read_file", "arguments": "{}"},
233 |             "extra_content": {"google": {"thought_signature": "SIG"}}}]}}],
234 |             "usage": {"prompt_tokens": 60, "completion_tokens": 20, "total_tokens": 150}}
235 |         server = FakeServer(200, json.dumps(body).encode())
236 |         try:
237 |             config = replace(Config(), api_base=server.url, reasoning_effort="low", stream=False)
238 |             got = OpenAICompatClient(config).chat([{"role": "user", "content": "hi"}], [])
239 |             self.assertEqual(server.received[0]["reasoning_effort"], "low")
240 |         finally:
241 |             server.close()
242 |         call = got.message["tool_calls"][0]
243 |         self.assertEqual(call["extra_content"]["google"]["thought_signature"], "SIG")
244 |         self.assertEqual((got.prompt_tokens, got.completion_tokens), (60, 90))   # 70 hidden + 20
245 |         sent = to_openai([{"role": "assistant", "content": "", "tool_calls": [call]}])
246 |         self.assertEqual(sent[0]["tool_calls"][0]["extra_content"], call["extra_content"])
247 | 
```
```python
248 |     def test_missing_signatures_get_placeholder_only_for_gemini(self):
249 |         history = [{"role": "assistant", "content": "", "tool_calls": [
250 |             {"id": "c1", "function": {"name": "read_file", "arguments": {}}}]}]
251 |         plain = to_openai(history)[0]["tool_calls"][0]
252 |         self.assertNotIn("extra_content", plain)                  # local servers: unchanged
253 |         filled = to_openai(history, fill_signatures=True)[0]["tool_calls"][0]
254 |         self.assertEqual(filled["extra_content"]["google"]["thought_signature"],
255 |                          "skip_thought_signature_validator")
256 | 
```
```python
257 |     def test_gemini_backend_needs_key_and_labels(self):
258 |         with self.assertRaisesRegex(ValueError, "GEMINI_API_KEY"):
259 |             make_backend(replace(Config(), backend="gemini"))
260 |         config = replace(Config(), backend="gemini", gemini_api_key="k", gemini_models=("m1",),
261 |                          gemini_strong_models=("m2",), fallback_backend="openai", model="q")
262 |         self.assertIsInstance(make_backend(config), FallbackBackend)
263 |         self.assertEqual(model_label(config), "gemini:m1 > m2 > openai:q")
264 |         self.assertEqual(model_label(Config()), "ollama:qwen3:8b")
265 |         with self.assertRaises(ValueError):
266 |             make_backend(replace(config, fallback_backend="nope"))
267 | 
```
```python
268 |     def test_doctor_reports_missing_key(self):
269 |         healthy, lines = run_doctor(replace(Config(), backend="gemini", gemini_api_key=""))
270 |         self.assertFalse(healthy)
271 |         self.assertTrue(any("GEMINI_API_KEY" in line for line in lines))
272 | 
```
```python
273 |     def test_doctor_treats_a_missing_backup_as_a_note(self):
274 |         def fake_get(url, headers=None):
275 |             if url.startswith(Config().gemini_base):
276 |                 return {"data": [{"id": "models/m1"}, {"id": "models/m2"}]}
277 |             raise OSError("connection refused")             # the backup server is not running
278 |         with tempfile.TemporaryDirectory() as tmp, \
279 |                 mock.patch("localagent.doctor._get_json", side_effect=fake_get):
280 |             config = replace(Config(), backend="gemini", gemini_api_key="k", gemini_models=("m1",),
281 |                              gemini_strong_models=("m2",), fallback_backend="openai",
282 |                              usage_file=Path(tmp) / "usage.json")
283 |             healthy, lines = run_doctor(config)
284 |         self.assertTrue(healthy)                             # Gemini works: the backup is optional
285 |         self.assertFalse(any(line.startswith("FAIL") for line in lines))
286 |         self.assertTrue(any(line.startswith("note  the backup model") for line in lines))
287 | 
288 | 
```
- Against the fake server: `reasoning_effort` is sent; the tool call's `extra_content` (thought signature) is kept on
  the stored message and sent back by `to_openai`; with 60 prompt + 20 completion but 150 total tokens, 90 are counted
  as completion (70 hidden thinking + 20).
- A tool call without a signature is sent unchanged to ordinary servers, but gets Google's placeholder signature when
  `fill_signatures` is on.
- The Gemini backend refuses to start without a key, builds a `FallbackBackend` with one, labels the chain correctly,
  and rejects an unknown fallback name. The doctor reports a missing key as a failure.
- With Gemini answering (its model list is faked with `mock.patch`) but the backup server unreachable, the doctor
  stays healthy, prints no `FAIL`, and prints a `note` about the backup.

## Runners
```python
289 | class RunnerTests(unittest.TestCase):
```
```python
290 |     def test_local_runner_refuses_shell_and_toolbox_hides_tool(self):
291 |         with tempfile.TemporaryDirectory() as tmp:
292 |             runner = LocalRunner(Config(), Path(tmp))
293 |             self.assertEqual(runner.run("shell", ["ls"])[0], 126)
294 |             box = ToolBox(replace(Config(), workspace=Path(tmp)), lambda _a: True)
295 |             names = [spec["function"]["name"] for spec in box.specs()]
296 |             self.assertNotIn("run_shell", names)
297 |             box.close()
298 | 
```
```python
299 |     def test_docker_toolbox_offers_shell(self):
300 |         with tempfile.TemporaryDirectory() as tmp:
301 |             box = ToolBox(replace(Config(), workspace=Path(tmp), sandbox="docker"), lambda _a: True)
302 |             self.assertIn("run_shell", [s["function"]["name"] for s in box.specs()])
303 |             self.assertIn("ERROR", box.call("run_shell", {"command": "  "}))
304 |             box.close()                                      # never started: nothing to stop
305 | 
```
```python
306 |     @unittest.skipUnless(docker_available(), "Docker is not running")
307 |     def test_docker_sandbox_isolation_end_to_end(self):
308 |         with tempfile.TemporaryDirectory() as tmp:
309 |             config = replace(Config(), workspace=Path(tmp), sandbox="docker", command_timeout=20)
310 |             box = ToolBox(config, lambda _a: True)
311 |             try:
312 |                 box.call("write_file", {"path": "hi.py", "content": "print('from container')\n"})
313 |                 self.assertIn("from container", box.call("run_python", {"path": "hi.py"}))
314 |                 who = box.call("run_shell", {"command": "whoami && pwd"})
315 |                 self.assertIn("agent", who)                  # not root
316 |                 self.assertIn("/work", who)
317 |                 net = box.call("run_shell", {"command": "python -c \"import urllib.request as u; "
318 |                                              "u.urlopen('http://example.com', timeout=5)\""})
319 |                 self.assertNotIn("exit code 0", net)         # no network by default
320 |                 slow = replace(config, command_timeout=2)
321 |                 runner = DockerRunner(slow, Path(tmp))
322 |                 code, _ = runner.run("shell", ["sleep 30"])
323 |                 runner.stop()
324 |                 self.assertEqual(code, 124)                  # timeout kills the command
325 |             finally:
326 |                 box.close()
327 | 
328 | 
```
- Locally the shell is refused (code 126) and the `run_shell` tool is not even offered to the model.
- With `sandbox="docker"` the tool is offered and an empty command is rejected — without starting a container.
- **Real Docker (skipped if Docker is off):** a written file runs in the container and prints `from container`;
  `whoami && pwd` shows the non-root user `agent` in `/work`; opening a web page fails (no network); `sleep 30` with a
  2-second limit returns the timeout code 124. The container is removed in `finally`.

```python
329 | if __name__ == "__main__":
330 |     unittest.main()
```
- Lets you run the file directly.

---
# `tests/test_phase7.py` — the Claude-Code-like features

**Role:** proves the features that make the agent usable in any real project folder: fast folder walking, checks for
more languages, the `todo` checklist, the opt-in shell on this computer, compaction, `/init` and the defaults.
No model and no network are needed (scripted fakes again); the JavaScript test runs only when Node.js is installed.

## Helpers
```python
  1 | """Tests for the Claude-Code-like features: any-folder use, more languages, todo, shell, compaction."""
  2 | import json
  3 | import os
  4 | import shutil
  5 | import tempfile
  6 | import unittest
  7 | from dataclasses import replace
  8 | from pathlib import Path
  9 | 
 10 | from localagent.agent import COMPACT_PROMPT, Agent
 11 | from localagent.checkpoint import Checkpoints
 12 | from localagent.cli import INIT_PROMPT, build_config, build_parser
 13 | from localagent.config import Config
 14 | from localagent.context import project_tree
 15 | from localagent.meter import Meter
 16 | from localagent.runners import LocalRunner
 17 | from localagent.sandbox import walk_files
 18 | from localagent.tools import ToolBox
 19 | from localagent.verify import verify_file
 20 | 
 21 | from .test_agent import FakeBackend, tool_call
 22 | 
 23 | 
```
- Imports the pieces under test and the fake model helpers from `test_agent.py`.

```python
 24 | def box_for(tmp: str, **kw) -> ToolBox:
 25 |     """ToolBox on a temp folder that approves everything."""
 26 |     return ToolBox(replace(Config(), workspace=Path(tmp), **kw), approve=lambda _a: True)
 27 | 
 28 | 
```
- A toolbox on a temporary folder that approves everything; extra settings can be passed.

## Folder walking
```python
 29 | class WalkTests(unittest.TestCase):
```
```python
 30 |     def test_skipped_folders_are_never_entered(self):
 31 |         with tempfile.TemporaryDirectory() as tmp:
 32 |             root = Path(tmp)
 33 |             for name in ("src/app.py", "node_modules/pkg/index.js", ".localagent/x.json",
 34 |                          "venv/lib/a.py", "README.md"):
 35 |                 (root / name).parent.mkdir(parents=True, exist_ok=True)
 36 |                 (root / name).write_text("x", encoding="utf-8")
 37 |             found = [p.relative_to(root).as_posix() for p in walk_files(root)]
 38 |             self.assertEqual(found, ["README.md", "src/app.py"])          # sorted, pruned
 39 |             self.assertEqual(project_tree(root, 10), "README.md\nsrc/app.py")
 40 |             snap = Checkpoints(root)
 41 |             snap.take()
 42 |             (root / "node_modules/pkg/index.js").write_text("changed", encoding="utf-8")
 43 |             self.assertIn("restored 0", snap.undo())                      # not snapshotted at all
 44 | 
```
```python
 45 |     def test_search_uses_glob_on_file_names(self):
 46 |         with tempfile.TemporaryDirectory() as tmp:
 47 |             box = box_for(tmp)
 48 |             (Path(tmp) / "a.js").write_text("const needle = 1;\n", encoding="utf-8")
 49 |             (Path(tmp) / "b.py").write_text("needle = 2\n", encoding="utf-8")
 50 |             self.assertIn("a.js:1", box.call("search", {"pattern": "needle", "glob": "*.js"}))
 51 |             self.assertNotIn("a.js", box.call("search", {"pattern": "needle"}))   # default *.py
 52 | 
 53 | 
```
- A project with `node_modules`, `.localagent` and `venv` folders: only `README.md` and `src/app.py` are found (sorted),
  the prompt's file list shows exactly those two, and a change inside `node_modules` is invisible to `/undo` snapshots.
- `search` with `glob: *.js` finds a match in a JavaScript file; the default (`*.py`) does not look at it.

## Languages
```python
 54 | class LanguageTests(unittest.TestCase):
```
```python
 55 |     def check(self, name: str, text: str) -> bool:
 56 |         with tempfile.TemporaryDirectory() as tmp:
 57 |             path = Path(tmp) / name
 58 |             path.write_text(text, encoding="utf-8")
 59 |             return verify_file(path, Path(tmp)).ok
 60 | 
```
```python
 61 |     def test_json_and_toml(self):
 62 |         self.assertTrue(self.check("a.json", '{"a": 1}'))
 63 |         self.assertFalse(self.check("a.json", '{"a": 1,}'))
 64 |         self.assertTrue(self.check("p.toml", 'name = "x"\n'))
 65 |         self.assertFalse(self.check("p.toml", "name = \n"))
 66 |         self.assertTrue(self.check("notes.md", "anything at all {"))           # unchecked type
 67 | 
```
```python
 68 |     @unittest.skipUnless(shutil.which("node"), "Node.js not installed")
 69 |     def test_javascript_with_node(self):
 70 |         self.assertTrue(self.check("a.js", "const x = 1;\nmodule.exports = { x };\n"))
 71 |         self.assertFalse(self.check("a.js", "const x = ;\n"))
 72 | 
```
```python
 73 |     def test_bad_json_write_is_rolled_back_and_workspace_check_sees_it(self):
 74 |         with tempfile.TemporaryDirectory() as tmp:
 75 |             box = box_for(tmp)
 76 |             self.assertIn("REJECTED", box.call("write_file", {"path": "c.json", "content": "{oops"}))
 77 |             self.assertFalse((Path(tmp) / "c.json").exists())
 78 |             (Path(tmp) / "d.json").write_text("[1,", encoding="utf-8")            # broken behind our back
 79 |             self.assertEqual(len(box.check_workspace()), 1)
 80 | 
 81 | 
```
- `check` writes a file and runs `verify_file` on it.
- Valid JSON/TOML pass; a trailing comma in JSON and a missing TOML value fail; a Markdown file is never checked.
- With Node.js: valid JavaScript passes, `const x = ;` fails.
- Writing broken JSON is rejected and rolled back (the file does not exist); a broken JSON file made outside the agent is
  reported by the end-of-request workspace check.

## Todo and shell
```python
 82 | class TodoAndShellTests(unittest.TestCase):
```
```python
 83 |     def test_todo_checklist(self):
 84 |         with tempfile.TemporaryDirectory() as tmp:
 85 |             box = box_for(tmp)
 86 |             out = box.call("todo", {"items": [{"text": "read", "done": True}, {"text": "write"}]})
 87 |             self.assertEqual(out.splitlines(), ["TODO 1/2 done", "  [x] read", "  [ ] write"])
 88 |             self.assertEqual(len(box.todo), 2)
 89 |             self.assertIn("ERROR", box.call("todo", {"items": []}))
 90 |             self.assertIn("[ ] plain text", box.call("todo", {"items": ["plain text"]}))
 91 | 
```
```python
 92 |     def test_host_shell_is_opt_in_and_asks(self):
 93 |         with tempfile.TemporaryDirectory() as tmp:
 94 |             self.assertNotIn("run_shell", [s["function"]["name"] for s in box_for(tmp).specs()])
 95 |             asked: list[str] = []
 96 |             cfg = replace(Config(), workspace=Path(tmp), allow_host_shell=True)
 97 |             box = ToolBox(cfg, approve=lambda action: asked.append(action) or True)
 98 |             self.assertIn("run_shell", [s["function"]["name"] for s in box.specs()])
 99 |             out = box.call("run_shell", {"command": "echo hello-shell"})
100 |             self.assertIn("hello-shell", out)
101 |             self.assertTrue(asked and "echo hello-shell" in asked[0])
102 |             refused = LocalRunner(Config(), Path(tmp)).run("shell", ["echo x"])
103 |             self.assertEqual(refused[0], 126)
104 | 
```
```python
105 |     def test_todo_progress_is_shown_in_full(self):
106 |         with tempfile.TemporaryDirectory() as tmp:
107 |             cfg = replace(Config(), workspace=Path(tmp))
108 |             script = [tool_call("todo", items=[{"text": "step one"}, {"text": "step two"}]),
109 |                       {"role": "assistant", "content": "ok"}]
110 |             agent = Agent(cfg, FakeBackend(script), ToolBox(cfg, lambda _a: True), Meter())
111 |             events: list[str] = []
112 |             agent.run("Plan it", on_event=events.append)
113 |             self.assertIn("     [ ] step two", events)
114 | 
115 | 
```
- The checklist renders as `TODO 1/2 done` + `[x]`/`[ ]` lines; an empty list is an error; plain-text items work too
  (the real model sent plain strings the first time).
- Without `--allow-shell` there is no `run_shell` tool. With it, `echo hello-shell` runs (through PowerShell on
  Windows), and the approval question contained the command. A runner without the flag refuses with code 126.
- When the model sends a checklist, every line of it reaches the progress display.

## Compaction and notes
```python
116 | class CompactionTests(unittest.TestCase):
```
```python
117 |     def make(self, tmp, script, **kw):
118 |         cfg = replace(Config(), workspace=Path(tmp), **kw)
119 |         backend = FakeBackend(script)
120 |         return Agent(cfg, backend, ToolBox(cfg, lambda _a: True), Meter()), backend
121 | 
```
```python
122 |     def test_manual_compact_replaces_history_with_summary(self):
123 |         with tempfile.TemporaryDirectory() as tmp:
124 |             agent, _ = self.make(tmp, [{"role": "assistant", "content": "one"},
125 |                                        {"role": "assistant", "content": "two"},
126 |                                        {"role": "assistant", "content": "SUMMARY: user wants X"}])
127 |             self.assertEqual(agent.compact(), "nothing to compact")
128 |             agent.run("first question")
129 |             agent.run("second question")
130 |             result = agent.compact()
131 |             self.assertIn("compacted", result)
132 |             self.assertEqual([m["role"] for m in agent.messages], ["system", "user", "assistant"])
133 |             self.assertIn("SUMMARY: user wants X", agent.messages[1]["content"])
134 | 
```
```python
135 |     def test_empty_summary_keeps_history(self):
136 |         with tempfile.TemporaryDirectory() as tmp:
137 |             agent, _ = self.make(tmp, [{"role": "assistant", "content": "a"},
138 |                                        {"role": "assistant", "content": "b"},
139 |                                        {"role": "assistant", "content": ""}])
140 |             agent.run("q1")
141 |             agent.run("q2")
142 |             before = len(agent.messages)
143 |             self.assertIn("skipped", agent.compact())
144 |             self.assertEqual(len(agent.messages), before)
145 | 
```
```python
146 |     def test_automatic_compaction_before_a_request(self):
147 |         with tempfile.TemporaryDirectory() as tmp:
148 |             agent, _ = self.make(tmp, [{"role": "assistant", "content": "x" * 400}] * 3
149 |                                  + [{"role": "assistant", "content": "SUM"},
150 |                                     {"role": "assistant", "content": "answer"}],
151 |                                  num_ctx=1000, compact_at=0.1)
152 |             for question in ("q1", "q2", "q3"):
153 |                 agent.run(question)
154 |             events: list[str] = []
155 |             self.assertEqual(agent.run("q4", on_event=events.append), "answer")
156 |             self.assertTrue(any("summarising" in e for e in events))
157 |             self.assertIn("SUM", agent.messages[1]["content"])
158 |             self.assertIn("300 words", COMPACT_PROMPT)
159 | 
```
```python
160 |     def test_reset_and_refresh_reload_agent_notes(self):
161 |         with tempfile.TemporaryDirectory() as tmp:
162 |             agent, _ = self.make(tmp, [])
163 |             self.assertNotIn("USE TABS", agent.messages[0]["content"])
164 |             (Path(tmp) / "AGENT.md").write_text("USE TABS", encoding="utf-8")
165 |             agent.refresh_system()
166 |             self.assertIn("USE TABS", agent.messages[0]["content"])
167 |             agent.reset()
168 |             self.assertEqual(len(agent.messages), 1)
169 |             self.assertIn("USE TABS", agent.messages[0]["content"])
170 | 
171 | 
```
- A fresh agent has nothing to compact. After two requests, `compact()` leaves exactly system + summary + "Understood",
  with the model's summary inside.
- An empty summary changes nothing.
- With a tiny context (1000 tokens) and `compact_at=0.1`, the fourth request is preceded by an automatic summary (a
  progress line says so) and still gets its answer.
- After `AGENT.md` appears, `refresh_system` puts its text in the system prompt; `reset` keeps it and clears the rest.

## Any-folder defaults
```python
172 | class AnyFolderTests(unittest.TestCase):
```
```python
173 |     def test_defaults_work_in_current_folder_with_global_quota_file(self):
174 |         self.assertEqual(Config().workspace, Path("."))
175 |         self.assertEqual(Config().usage_file, Path.home() / ".localagent" / "usage.json")
176 |         args = build_parser().parse_args(["--allow-shell"])
177 |         self.assertTrue(build_config(args).allow_host_shell)
178 |         self.assertTrue(INIT_PROMPT.startswith("Create AGENT.md"))
179 | 
```
```python
180 |     def test_usage_file_can_be_moved_by_environment(self):
181 |         old = os.environ.get("LOCALAGENT_USAGE_FILE")
182 |         os.environ["LOCALAGENT_USAGE_FILE"] = "elsewhere.json"
183 |         try:
184 |             self.assertEqual(Config.from_env().usage_file, Path("elsewhere.json"))
185 |         finally:
186 |             if old is None:
187 |                 del os.environ["LOCALAGENT_USAGE_FILE"]
188 |             else:
189 |                 os.environ["LOCALAGENT_USAGE_FILE"] = old
190 |         self.assertTrue(json.dumps(str(Config().usage_file)))
191 | 
192 | 
```
- The workspace defaults to the current folder; the quota file lives in the home folder; `--allow-shell` reaches the
  config; `/init`'s request asks for `AGENT.md`.
- `LOCALAGENT_USAGE_FILE` moves the quota file (the test restores the environment afterwards).

```python
193 | if __name__ == "__main__":
194 |     unittest.main()
```
- Lets you run the file directly.

---
# `tests/test_phase8.py` — permissions, web pages, MCP, plan mode, interrupts, diffs, CLI helpers

**Role:** proves the Claude-Code-style features of the latest phase without a model or the internet: a local web server
stands in for websites, and a tiny MCP server written inside this file stands in for real MCP servers.

## Helpers
```python
  1 | """Tests for permissions, web pages, MCP servers, plan mode, interrupts, diffs and the CLI helpers."""
  2 | import contextlib
  3 | import importlib.util
  4 | import io
  5 | import json
  6 | import os
  7 | import sys
  8 | import tempfile
  9 | import threading
 10 | import unittest
 11 | from dataclasses import replace
 12 | from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
 13 | from pathlib import Path
 14 | from unittest import mock
 15 | 
 16 | from localagent.agent import INTERRUPTED, NOT_ALLOWED, PLAN_ONLY_ERROR, Agent
 17 | from localagent.cli import (Approver, auto_configure, build_parser, handle_request, read_request,
 18 |                             sessions_folder)
 19 | from localagent.config import GEMINI_CONTEXT, Config, gemini_history, overlay
 20 | from localagent.mcp import MCPError, MCPServer, clean_schema
 21 | from localagent.meter import Meter
 22 | from localagent.sandbox import shell_argv
 23 | from localagent.ratelimit import RateLimiter
 24 | from localagent.settings import Permissions, load_settings, matches, parse_rule, suggest_rule
 25 | from localagent.tools import TOGETHER_HINT, Approval, ToolBox
 26 | from localagent.web import fetch_text, html_to_text
 27 | 
 28 | from .test_agent import FakeBackend, tool_call
 29 | 
 30 | FAKE_MCP_SERVER = r'''
 31 | import json, sys
 32 | 
 33 | def send(message):
 34 |     sys.stdout.write(json.dumps(message) + "\n")
 35 |     sys.stdout.flush()
 36 | 
 37 | for line in sys.stdin:
 38 |     message = json.loads(line)
 39 |     method, ident = message.get("method"), message.get("id")
 40 |     if method == "initialize":
 41 |         send({"jsonrpc": "2.0", "id": "s1", "method": "roots/list"})      # a question for the client
 42 |         send({"jsonrpc": "2.0", "id": ident, "result": {"protocolVersion": "2025-06-18",
 43 |               "capabilities": {"tools": {}}, "serverInfo": {"name": "fake", "version": "1"}}})
 44 |     elif method == "tools/list" and not message.get("params", {}).get("cursor"):
 45 |         schema = {"$schema": "x", "type": "object", "additionalProperties": False,
 46 |                   "properties": {"text": {"type": ["string", "null"]}}, "required": ["text"]}
 47 |         send({"jsonrpc": "2.0", "id": ident, "result": {"nextCursor": "page2", "tools": [
 48 |             {"name": "echo", "description": "Echo text back", "inputSchema": schema}]}})
 49 |     elif method == "tools/list":
 50 |         send({"jsonrpc": "2.0", "id": ident, "result": {"tools": [{"name": "fail"}]}})
 51 |     elif method == "tools/call":
 52 |         params = message["params"]
 53 |         if params["name"] == "echo":
 54 |             text = "echo: " + str(params["arguments"].get("text"))
 55 |             send({"jsonrpc": "2.0", "id": ident, "result": {"content": [{"type": "text", "text": text}]}})
 56 |         else:
 57 |             send({"jsonrpc": "2.0", "id": ident, "result": {"isError": True,
 58 |                   "content": [{"type": "text", "text": "it broke"}, {"type": "image"}]}})
 59 | '''
 60 | 
 61 | 
```
- Imports the pieces under test. `FAKE_MCP_SERVER` is the full source of a minimal MCP server: it answers the
  handshake (first sending the client a question of its own, `roots/list`, to prove the client copes), lists its tools
  in **two pages** (`echo` with a deliberately messy schema, then `fail`), echoes text, and reports an error result
  for `fail` (with an image part, to test that non-text content is named, not dumped).

```python
 62 | def write_server(folder: str) -> list[str]:
 63 |     """Save the fake MCP server; return the settings entry that starts it."""
 64 |     path = Path(folder) / "fake_mcp.py"
 65 |     path.write_text(FAKE_MCP_SERVER, encoding="utf-8")
 66 |     return [sys.executable, str(path)]
 67 | 
 68 | 
```
- Saves that source to a temporary file and returns the command that starts it with this Python.

## Permissions
```python
 69 | class PermissionTests(unittest.TestCase):
```
```python
 70 |     def test_rules_match_tools_and_subjects(self):
 71 |         self.assertEqual(parse_rule("run_shell(git *)"), ("run_shell", "git *"))
 72 |         self.assertEqual(parse_rule(" run_tests "), ("run_tests", None))
 73 |         self.assertTrue(matches("run_shell(git *)", "run_shell", "git status"))
 74 |         self.assertFalse(matches("run_shell(git *)", "run_shell", "rm -rf x"))
 75 |         self.assertTrue(matches("mcp__files__*", "mcp__files__read", "{}"))
 76 |         rules = Permissions(allow=["run_shell(*)"], deny=["run_shell(*rm *)"])
 77 |         self.assertEqual(rules.decide("run_shell", "ls"), "allow")
 78 |         self.assertEqual(rules.decide("run_shell", "rm -rf /"), "deny")   # deny wins
 79 |         self.assertEqual(rules.decide("run_tests", ""), "ask")
 80 | 
```
```python
 81 |     def test_suggested_rules_are_narrow(self):
 82 |         self.assertEqual(suggest_rule("run_shell", "git status"), "run_shell(git status)")
 83 |         self.assertEqual(suggest_rule("fetch_url", "https://docs.python.org/3/x.html"),
 84 |                          "fetch_url(https://docs.python.org/*)")
 85 |         self.assertEqual(suggest_rule("run_tests", ""), "run_tests")
 86 | 
```
```python
 87 |     def test_settings_merge_and_always_allow_is_saved(self):
 88 |         with tempfile.TemporaryDirectory() as tmp:
 89 |             user = Path(tmp) / "user.json"
 90 |             user.write_text(json.dumps({"permissions": {"deny": ["run_shell(*rm *)"]},
 91 |                                         "mcpServers": {"a": {"command": "x"}}}), encoding="utf-8")
 92 |             project = Path(tmp) / ".localagent" / "settings.json"
 93 |             project.parent.mkdir()
 94 |             project.write_text(json.dumps({"permissions": {"allow": ["run_tests"]}}), encoding="utf-8")
 95 |             merged = load_settings(Path(tmp), user)
 96 |             self.assertEqual(merged["permissions"], {"allow": ["run_tests"], "deny": ["run_shell(*rm *)"]})
 97 |             self.assertIn("a", merged["mcpServers"])
 98 |             rules = Permissions.from_settings(merged, project)
 99 |             self.assertEqual(rules.always_allow("run_shell(ls)"), project)
100 |             saved = json.loads(project.read_text(encoding="utf-8"))
101 |             self.assertEqual(saved["permissions"]["allow"], ["run_tests", "run_shell(ls)"])
102 |             project.write_text("{broken", encoding="utf-8")
103 |             with self.assertRaisesRegex(ValueError, "not valid JSON"):
104 |                 load_settings(Path(tmp), user)
105 | 
```
```python
106 |     def test_toolbox_applies_rules_before_asking(self):
107 |         with tempfile.TemporaryDirectory() as tmp:
108 |             asked: list[str] = []
109 |             cfg = replace(Config(), workspace=Path(tmp))
110 |             box = ToolBox(cfg, lambda q: asked.append(q) or False,
111 |                           {"permissions": {"allow": ["run_tests"], "deny": ["run_python(*)"]}})
112 |             box.call("write_file", {"path": "a.py", "content": "print(1)\n"})
113 |             self.assertIn("deny rule", box.call("run_python", {"path": "a.py"}))
114 |             self.assertIn("exit code", box.call("run_tests", {}))       # allowed: not asked
115 |             self.assertEqual(asked, [])
116 |             box.permissions.allow.clear()
117 |             self.assertIn("DENIED", box.call("run_tests", {}))          # asked, user said no
118 |             self.assertIsInstance(asked[0], Approval)
119 |             self.assertEqual((asked[0].tool, asked[0].subject), ("run_tests", ""))
120 | 
121 | 
```
- Rule parsing and matching, wildcard tool names (`mcp__files__*`), and **deny beats allow**; unmatched → ask.
- "Always" suggestions: the exact command, the whole website, the bare `run_tests`.
- A user file (deny + an MCP server) and a project file (allow) merge; `always_allow` appends to the project file on
  disk; a broken project file is reported as invalid JSON.
- In the toolbox: a denied `run_python` is refused without asking, an allowed `run_tests` runs without asking, and with
  the allow rule removed the question arrives as an `Approval` carrying tool and subject; a "no" gives `DENIED`.

## Web pages
```python
122 | class WebTests(unittest.TestCase):
123 |     PAGE = (b"<html><head><title>t</title><style>x{}</style></head><body><h1>Title</h1>"
124 |             b"<p>Hello   <b>world</b></p><script>var hidden=1;</script><ul><li>one</li></ul></body></html>")
125 | 
```
```python
126 |     def serve(self, status=200, body=PAGE, ctype="text/html; charset=utf-8"):
127 |         class Handler(BaseHTTPRequestHandler):
128 |             def do_GET(self):
129 |                 self.send_response(status)
130 |                 self.send_header("Content-Type", ctype)
131 |                 self.end_headers()
132 |                 self.wfile.write(body)
133 | 
134 |             def log_message(self, *args):
135 |                 pass
136 | 
137 |         server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
138 |         threading.Thread(target=server.serve_forever, daemon=True).start()
139 |         self.addCleanup(server.server_close)
140 |         self.addCleanup(server.shutdown)
141 |         return f"http://127.0.0.1:{server.server_port}/page"
142 | 
```
```python
143 |     def test_html_becomes_visible_text(self):
144 |         text = html_to_text(self.PAGE.decode())
145 |         self.assertEqual(text, "Title\n\nHello world\n\none")
146 |         self.assertNotIn("hidden", text)
147 | 
```
```python
148 |     def test_fetch_and_errors(self):
149 |         self.assertEqual(fetch_text(self.serve()), "Title\n\nHello world\n\none")
150 |         self.assertEqual(fetch_text(self.serve(body=b"plain", ctype="text/plain")), "plain")
151 |         with self.assertRaisesRegex(ValueError, "HTTP 404"):
152 |             fetch_text(self.serve(status=404))
153 |         with self.assertRaisesRegex(ValueError, "only http"):
154 |             fetch_text("file:///etc/passwd")
155 | 
```
```python
156 |     def test_fetch_tool_asks_and_obeys_rules(self):
157 |         with tempfile.TemporaryDirectory() as tmp:
158 |             url = self.serve()
159 |             cfg = replace(Config(), workspace=Path(tmp))
160 |             self.assertIn("Hello world", ToolBox(cfg, lambda _q: True).call("fetch_url", {"url": url}))
161 |             self.assertIn("DENIED", ToolBox(cfg).call("fetch_url", {"url": url}))     # nobody approved
162 |             denied = ToolBox(cfg, lambda _q: True, {"permissions": {"deny": ["fetch_url"]}})
163 |             self.assertIn("deny rule", denied.call("fetch_url", {"url": url}))
164 |             self.assertIn("ERROR", ToolBox(cfg, lambda _q: True).call("fetch_url", {"url": "ftp://x"}))
165 | 
166 | 
```
- `serve` starts a local web server that answers every request with a chosen status, body and content type.
- A page with a title, a style block, bold text, a script and a list becomes exactly
  `Title / Hello world / one` (the script text never appears).
- Fetching HTML and plain text works; a 404 and a `file://` address raise readable errors.
- Through the tool: approved → page text; nobody to approve → `DENIED`; a deny rule → refused; an `ftp://` address →
  `ERROR`.

## MCP
```python
167 | class MCPTests(unittest.TestCase):
```
```python
168 |     def test_schema_cleaning(self):
169 |         cleaned = clean_schema({"$schema": "x", "type": "object", "additionalProperties": False,
170 |                                 "properties": {"a": {"type": ["integer", "null"]},
171 |                                                "b": {"items": {"type": "string"}}}})
172 |         self.assertEqual(cleaned, {"type": "object", "properties": {
173 |             "a": {"type": "integer", "nullable": True},
174 |             "b": {"items": {"type": "string"}, "type": "string"}}})
175 |         self.assertEqual(clean_schema(None), {"type": "object", "properties": {}})
176 | 
```
```python
177 |     def test_server_tools_through_the_toolbox(self):
178 |         with tempfile.TemporaryDirectory() as tmp:
179 |             command = write_server(tmp)
180 |             settings = {"mcpServers": {"fake": {"command": command[0], "args": command[1:]},
181 |                                        "broken": {"command": "no-such-program-xyz"},
182 |                                        "empty": {}}}
183 |             box = ToolBox(replace(Config(), workspace=Path(tmp)), lambda _q: True, settings)
184 |             try:
185 |                 names = [s["function"]["name"] for s in box.specs()]
186 |                 self.assertIn("mcp__fake__echo", names)
187 |                 self.assertIn("mcp__fake__fail", names)                  # second page of the list
188 |                 echo = next(s for s in box.specs() if s["function"]["name"] == "mcp__fake__echo")
189 |                 self.assertNotIn("$schema", json.dumps(echo))
190 |                 self.assertEqual(box.call("mcp__fake__echo", {"text": "hi"}), "echo: hi")
191 |                 self.assertTrue(box.call("mcp__fake__fail", {}).startswith("ERROR: it broke"))
192 |                 self.assertTrue(any("2 tool(s)" in n for n in box.notes))
193 |                 self.assertTrue(any("broken" in n and "skipped" in n for n in box.notes))
194 |                 self.assertTrue(any("empty" in n and "command" in n for n in box.notes))
195 |                 box.permissions.deny.append("mcp__fake__*")
196 |                 self.assertIn("deny rule", box.call("mcp__fake__echo", {"text": "x"}))
197 |             finally:
198 |                 box.close()
199 | 
```
```python
200 |     def test_timeout_and_stopped_server(self):
201 |         with tempfile.TemporaryDirectory() as tmp:
202 |             command = write_server(tmp)
203 |             server = MCPServer("fake", command[0], command[1:], timeout=5).start()
204 |             with self.assertRaisesRegex(MCPError, "did not answer"):
205 |                 server.request("never/answered", timeout=0.5)
206 |             server.close()
207 |             proc = server._proc
208 |             assert proc is not None and proc.stdin is not None and proc.stdout is not None
209 |             self.assertIsNotNone(proc.poll())                               # the program has ended
210 |             self.assertTrue(proc.stdin.closed and proc.stdout.closed)       # no handle left open
211 |             with self.assertRaises(MCPError):
212 |                 server.call_tool("echo", {"text": "late"})
213 | 
214 | 
```
- Schema cleaning drops `$schema`/`additionalProperties`, turns `["integer","null"]` into integer + nullable, fills
  missing types, and replaces a missing schema with "no arguments".
- With three configured servers — the fake one, one whose program does not exist, one without a command — the toolbox
  offers `mcp__fake__echo` and `mcp__fake__fail` (from both pages), the echo works, the failing tool returns `ERROR`,
  the notes report 2 tools and both skipped servers, and a deny rule on `mcp__fake__*` blocks calls.
- A request the server never answers times out with a clear message; after `close` the program has ended, both of
  its pipes are closed (no leaked file handles), and calls fail with `MCPError`.

## Agent features
```python
215 | class RecordingBackend(FakeBackend):
216 |     """Also remembers which tool names were offered on each call."""
217 | 
```
```python
218 |     def __init__(self, messages):
219 |         super().__init__(messages)
220 |         self.offered: list[list[str]] = []
221 | 
```
```python
222 |     def chat(self, messages, tools, on_token=None):
223 |         self.offered.append([t["function"]["name"] for t in tools])
224 |         return super().chat(messages, tools, on_token)
225 | 
226 | 
```
- A fake model that also records which tool names it was offered on each call.

```python
227 | class AgentFeatureTests(unittest.TestCase):
```
```python
228 |     def make(self, tmp, backend, **kw):
229 |         cfg = replace(Config(), workspace=Path(tmp), auto_format=False, **kw)
230 |         return Agent(cfg, backend, ToolBox(cfg, lambda _q: True), Meter())
231 | 
```
```python
232 |     def test_plan_mode_is_read_only(self):
233 |         with tempfile.TemporaryDirectory() as tmp:
234 |             backend = RecordingBackend([tool_call("write_file", path="x.py", content="x = 1\n"),
235 |                                         tool_call("finish", summary="1. create x.py")])
236 |             agent = self.make(tmp, backend)
237 |             self.assertEqual(agent.run("Create x.py", plan_only=True), "1. create x.py")
238 |             self.assertFalse((Path(tmp) / "x.py").exists())
239 |             self.assertNotIn("write_file", backend.offered[0])
240 |             self.assertIn("read_file", backend.offered[0])
241 |             self.assertIn(PLAN_ONLY_ERROR, [m["content"] for m in agent.messages])
242 |             self.assertTrue(agent.messages[1]["content"].startswith("PLAN MODE"))
243 | 
```
```python
244 |     def test_restricted_request_offers_only_allowed_tools(self):
245 |         with tempfile.TemporaryDirectory() as tmp:
246 |             backend = RecordingBackend([tool_call("run_tests"),
247 |                                         tool_call("write_file", path="AGENT.md", content="notes"),
248 |                                         tool_call("finish", summary="wrote notes")])
249 |             agent = self.make(tmp, backend)
250 |             answer = agent.run("Create AGENT.md", allowed=frozenset({"write_file"}))
251 |             self.assertEqual(answer, "wrote notes")
252 |             self.assertIn("write_file", backend.offered[0])
253 |             self.assertNotIn("run_tests", backend.offered[0])
254 |             self.assertIn(NOT_ALLOWED, [m["content"] for m in agent.messages])
255 |             self.assertTrue((Path(tmp) / "AGENT.md").is_file())
256 |             planner = RecordingBackend([tool_call("finish", summary="plan")])
257 |             self.make(tmp, planner).run("Plan it", plan_only=True)
258 |             self.assertIn("todo", planner.offered[0])                    # a checklist changes nothing
259 | 
```
```python
260 |     def test_repair_answers_unanswered_calls(self):
261 |         with tempfile.TemporaryDirectory() as tmp:
262 |             agent = self.make(tmp, FakeBackend([]))
263 |             agent.messages.append({"role": "assistant", "content": "", "tool_calls": [
264 |                 {"id": "c1", "function": {"name": "run_tests"}},
265 |                 {"id": "c2", "function": {"name": "read_file"}}]})
266 |             agent.messages.append({"role": "tool", "tool_call_id": "c1", "content": "done"})
267 |             self.assertEqual(agent.repair(), 1)
268 |             self.assertEqual(agent.messages[-1]["tool_call_id"], "c2")
269 |             self.assertEqual(agent.messages[-1]["content"], INTERRUPTED)
270 |             self.assertEqual(agent.repair(), 0)
271 |             fresh = self.make(tmp, FakeBackend([]))
272 |             fresh.restore(agent.messages[1:-1])                         # a "crashed" log
273 |             self.assertEqual(fresh.messages[-1]["content"], INTERRUPTED)
274 | 
```
```python
275 |     def test_edits_are_shown_as_a_diff(self):
276 |         with tempfile.TemporaryDirectory() as tmp:
277 |             script = [tool_call("write_file", path="a.txt", content="one\ntwo\n"),
278 |                       tool_call("edit_file", path="a.txt", old="two", new="TWO"),
279 |                       {"role": "assistant", "content": "ok"}]
280 |             events: list[str] = []
281 |             agent = self.make(tmp, FakeBackend(script))
282 |             agent.run("Make a.txt", on_event=events.append)
283 |             for line in ("     +one", "     -two", "     +TWO", "     @@ -1,2 +1,2 @@"):
284 |                 self.assertIn(line, events)
285 |             self.assertFalse(any(e.strip().startswith("+++") for e in events))   # header dropped
286 |             tool_text = " ".join(m["content"] for m in agent.messages if m["role"] == "tool")
287 |             self.assertNotIn("+TWO", tool_text)                          # the model never sees it
288 |             short: list[str] = []
289 |             capped = self.make(tmp, FakeBackend([tool_call("write_file", path="b.txt",
290 |                                                            content="1\n2\n3\n4\n"),
291 |                                                  {"role": "assistant", "content": "ok"}]),
292 |                                diff_lines=2)
293 |             capped.run("Make b.txt", on_event=short.append)
294 |             self.assertIn("     ... 3 more diff lines", short)
295 | 
296 | 
```
- **Plan mode:** `write_file` is not offered (but `read_file` is); when the model calls it anyway it gets
  `PLAN_ONLY_ERROR`, no file appears, the request was stored with the plan prefix, and the plan is returned.
- **Restricted request** (how `/init` works): with `allowed={write_file}` the model is offered `write_file` but not
  `run_tests`; calling `run_tests` anyway gets `NOT_ALLOWED`; writing `AGENT.md` and finishing works. Plan mode now offers
  the `todo` checklist.
- **Repair:** of two calls only one has a result; `repair` adds `INTERRUPTED` for the other, a second repair finds
  nothing; restoring a "crashed" log repairs it automatically.
- **Diffs:** writing then editing a file shows `+one`, `-two`, `+TWO` and the hunk header on screen, without the
  `+++` header, and none of it reaches the model's tool results; with `diff_lines=2` a 4-line new file shows
  `... 3 more diff lines`.

## CLI helpers
```python
297 | class CliHelperTests(unittest.TestCase):
```
```python
298 |     def test_multiline_input(self):
299 |         lines = iter(["first \\", "second"])
300 |         self.assertEqual(read_request(lambda _p: next(lines)), "first \nsecond")
301 |         block = iter(['"""', "a", "", "b", '"""'])
302 |         self.assertEqual(read_request(lambda _p: next(block)), "a\n\nb")
303 |         self.assertEqual(read_request(lambda _p: "﻿/init"), "/init")   # piped byte-order mark
304 | 
```
```python
305 |     def test_session_logs_belong_to_the_project(self):
306 |         project = Path("C:/work/shop") if os.name == "nt" else Path("/work/shop")
307 |         config = replace(Config(), workspace=project)
308 |         self.assertEqual(sessions_folder(config), project / ".localagent" / "sessions")
309 |         elsewhere = project.parent / "logs"                            # an absolute choice wins
310 |         self.assertEqual(sessions_folder(replace(config, state_dir=elsewhere)), elsewhere / "sessions")
311 | 
```
```python
312 |     def test_shared_setup_helpers(self):
313 |         base = Config()
314 |         self.assertEqual(overlay(base, model=None, sandbox="docker"), replace(base, sandbox="docker"))
315 |         with mock.patch.dict(os.environ, {}, clear=False):
316 |             os.environ.pop("LOCALAGENT_NUM_CTX", None)
317 |             gemini = replace(base, backend="gemini")
318 |             self.assertEqual(gemini_history(gemini).num_ctx, GEMINI_CONTEXT)
319 |             self.assertEqual(gemini_history(gemini, chosen=True).num_ctx, base.num_ctx)
320 |             self.assertEqual(gemini_history(base).num_ctx, base.num_ctx)       # local models keep 8K
321 |             os.environ["LOCALAGENT_NUM_CTX"] = "4096"                         # set by the user
322 |             self.assertEqual(gemini_history(gemini).num_ctx, base.num_ctx)
323 |         self.assertEqual(shell_argv("x")[-1], "x")
324 |         self.assertEqual(shell_argv("x")[0], "powershell" if os.name == "nt" else "sh")
325 | 
```
```python
326 |     def test_todo_understands_done_written_in_the_text(self):
327 |         with tempfile.TemporaryDirectory() as tmp:
328 |             box = ToolBox(replace(Config(), workspace=Path(tmp)), lambda _q: True)
329 |             out = box.call("todo", {"items": ["read files (done)", "[x] write code",
330 |                                               {"text": "run tests", "done": True}, "finish"]})
331 |             self.assertEqual(out.splitlines()[0], "TODO 3/4 done")
332 |             self.assertIn("  [x] read files", out)
333 |             self.assertIn("  [x] write code", out)
334 |             self.assertIn("  [ ] finish", out)
335 | 
```
```python
336 |     def test_approver_always_saves_a_rule(self):
337 |         with tempfile.TemporaryDirectory() as tmp:
338 |             project = Path(tmp) / "settings.json"
339 |             approver = Approver(read=lambda _p: "a")
340 |             approver.permissions = Permissions(project_file=project)
341 |             with contextlib.redirect_stdout(io.StringIO()):
342 |                 self.assertTrue(approver(Approval("run x", "run_shell", "git status")))
343 |             self.assertEqual(approver.permissions.allow, ["run_shell(git status)"])
344 |             self.assertIn("run_shell(git status)", project.read_text(encoding="utf-8"))
345 |             self.assertFalse(Approver(read=lambda _p: "")("anything"))
346 |             self.assertTrue(Approver(read=lambda _p: "YES")("anything"))
347 | 
```
```python
348 |     def test_auto_configure_uses_what_is_available(self):
349 |         args = build_parser().parse_args([])
350 |         clean = {k: v for k, v in os.environ.items()
351 |                  if k not in ("LOCALAGENT_BACKEND", "LOCALAGENT_SANDBOX", "LOCALAGENT_NUM_CTX")}
352 |         with mock.patch.dict(os.environ, clean, clear=True), \
353 |                 mock.patch("localagent.cli.sandbox_image_ready", return_value=True), \
354 |                 mock.patch("localagent.cli.server_up", return_value=True):
355 |             config, notes = auto_configure(replace(Config(), gemini_api_key="k"), args)
356 |         self.assertEqual((config.backend, config.sandbox, config.fallback_backend, config.num_ctx),
357 |                          ("gemini", "docker", "openai", 32_768))
358 |         self.assertEqual(len(notes), 4)
359 |         explicit = build_parser().parse_args(["--backend", "ollama", "--sandbox", "local"])
360 |         with mock.patch("localagent.cli.sandbox_image_ready", return_value=True):
361 |             config, notes = auto_configure(replace(Config(), gemini_api_key="k"), explicit)
362 |         self.assertEqual((config.backend, config.sandbox, notes), ("ollama", "local", []))
363 | 
```
```python
364 |     def test_ctrl_c_keeps_the_conversation_usable(self):
365 |         class Interrupting:
366 |             def chat(self, messages, tools, on_token=None):
367 |                 raise KeyboardInterrupt
368 | 
369 |         with tempfile.TemporaryDirectory() as tmp:
370 |             cfg = replace(Config(), workspace=Path(tmp))
371 |             box = ToolBox(cfg, lambda _q: True)
372 |             agent = Agent(cfg, Interrupting(), box, Meter())
373 |             out = io.StringIO()
374 |             with contextlib.redirect_stdout(out):
375 |                 self.assertEqual(handle_request(agent, box, Meter(), "hello"), "")
376 |             self.assertIn("stopped", out.getvalue())
377 | 
378 | 
```
- A `\`-continued line and a `"""` block are read as multi-line requests; a leading byte-order mark is removed.
- Conversation logs go to `<project>/.localagent/sessions` whatever folder the agent was started from; an absolute
  `state_dir` is used as given.
- The shared helpers: `overlay` skips `None`; `gemini_history` gives Gemini 32K but keeps 8K when the size was chosen
  (flag or `LOCALAGENT_NUM_CTX`, set inside `mock.patch.dict` so the real environment is restored) or the model is
  local; `shell_argv` picks PowerShell on Windows and `sh` elsewhere.
- Checklist items marked `(done)` in the text or starting with `[x]`, or with `done: true`, count as done (`3/4`).
- Answering `a` saves `run_shell(git status)` in memory and in the project file; Enter refuses; `YES` allows.
- With a key, a ready sandbox image and a running local server (simulated with `mock.patch`), automatic setup picks
  gemini + docker + the local fallback + the 32K history budget with four notes; explicit flags are never overridden.
- A model call interrupted by Ctrl+C returns an empty answer and prints the "stopped" message instead of crashing.

## Limits and hints
```python
379 | class LimitsAndHintsTests(unittest.TestCase):
```
```python
380 |     def test_learned_rpm_is_saved_for_other_runs(self):
381 |         with tempfile.TemporaryDirectory() as tmp:
382 |             usage = Path(tmp) / "usage.json"
383 |             RateLimiter("m", rpm=12, tpm=10_000, rpd=100, usage_file=usage).set_rpm(4)
384 |             later = RateLimiter("m", rpm=12, tpm=10_000, rpd=100, usage_file=usage)
385 |             later.before_call(10)
386 |             self.assertEqual(later._rpm, 4)
387 | 
```
```python
388 |     @unittest.skipUnless(importlib.util.find_spec("ruff"), "ruff not installed")
389 |     def test_undefined_name_rejection_suggests_editing_together(self):
390 |         with tempfile.TemporaryDirectory() as tmp:
391 |             box = ToolBox(replace(Config(), workspace=Path(tmp)), lambda _q: True)
392 |             result = box.call("write_file", {"path": "m.py", "content": "print(missing)\n"})
393 |             self.assertIn(TOGETHER_HINT, result)
394 | 
395 | 
```
- A per-minute limit learned by one limiter is used by a new limiter reading the same usage file.
- With ruff installed, a write rejected for an undefined name carries the "edit together" hint.

```python
396 | if __name__ == "__main__":
397 |     unittest.main()
```
- Lets you run the file directly.

---
# `tests/test_phase9.py` — many languages, streaming, helpers, custom commands, tiers, big folders

**Role:** proves the breadth features. Language tests run each checker only when its program is installed (this PC has
Node, Go, Rust, gcc/g++ and PowerShell, so they all ran); everything else uses fakes and temporary folders.

## Helpers
```python
  1 | """Tests for many languages, streaming, helper agents, custom commands, tiers and big folders."""
  2 | import contextlib
  3 | import io
  4 | import os
  5 | import shutil
  6 | import tempfile
  7 | import time
  8 | import unittest
  9 | from dataclasses import replace
 10 | from pathlib import Path
 11 | from unittest import mock
 12 | 
 13 | from localagent.agent import DELEGATE_SPEC, Agent
 14 | from localagent.backends import make_gemini
 15 | from localagent.checkpoint import Checkpoints
 16 | from localagent.cli import handle_request
 17 | from localagent.config import Config
 18 | from localagent.meter import Meter
 19 | from localagent.openai_compat import read_stream
 20 | from localagent.settings import expand_command, load_commands
 21 | from localagent.tools import MAX_SEARCH_FILES, ToolBox
 22 | from localagent.verify import CHECKED_SUFFIXES, verify_file
 23 | 
 24 | from .test_agent import tool_call
 25 | from .test_phase8 import RecordingBackend
 26 | 
 27 | SAMPLES = {   # suffix: (program needed, valid text, broken text)
 28 |     ".ts": ("node", "enum C { A }\nconst x: number = 1;\nexport function f(a: string): string { return a; }\n",
 29 |             "function f(x: number { return x; }\n"),
 30 |     ".go": ("gofmt", "package main\n\nfunc main() {}\n", "package main\nfunc main( {}\n"),
 31 |     ".rs": ("rustfmt", "fn main() { let _x = 1; }\n", "fn main() { let x = ; }\n"),
 32 |     ".c": ("gcc", "int main(void) { return 0; }\n", "int main(void) { return 0 }\n"),
 33 |     ".cpp": ("g++", "int main() { return 0; }\n", "int main() { return 0 }\n"),
 34 |     ".ps1": ("powershell", 'Write-Output "hi"\n', "function f( {\n"),
 35 | }
 36 | 
 37 | 
```
- `SAMPLES` holds, per extension, the program needed, a valid file and a broken one (TypeScript with an `enum`, Go,
  Rust, C, C++, PowerShell).

```python
 38 | def check(folder: str, name: str, text: str) -> bool:
 39 |     path = Path(folder) / name
 40 |     path.write_text(text, encoding="utf-8")
 41 |     return verify_file(path, Path(folder)).ok
 42 | 
 43 | 
```
- Writes a file into a folder and returns whether `verify_file` accepts it.

## Languages
```python
 44 | class LanguageTests(unittest.TestCase):
```
```python
 45 |     def test_each_installed_language_checker(self):
 46 |         for suffix, (program, good, bad) in SAMPLES.items():
 47 |             with self.subTest(suffix=suffix):
 48 |                 if not shutil.which(program) or (suffix == ".ps1" and os.name != "nt"):
 49 |                     self.skipTest(f"{program} not installed")
 50 |                 with tempfile.TemporaryDirectory() as tmp:
 51 |                     self.assertTrue(check(tmp, "good" + suffix, good))
 52 |                     self.assertFalse(check(tmp, "bad" + suffix, bad))
 53 | 
```
```python
 54 |     def test_missing_outside_header_is_not_an_error(self):
 55 |         if not shutil.which("gcc"):
 56 |             self.skipTest("gcc not installed")
 57 |         with tempfile.TemporaryDirectory() as tmp:
 58 |             self.assertTrue(check(tmp, "uses_lib.c", '#include "not_here.h"\nint main(void){return 0;}\n'))
 59 | 
```
```python
 60 |     def test_unknown_and_tool_less_files_pass(self):
 61 |         with tempfile.TemporaryDirectory() as tmp:
 62 |             self.assertTrue(check(tmp, "notes.txt", "anything {"))
 63 |             with mock.patch("localagent.verify.shutil.which", return_value=None):
 64 |                 self.assertTrue(check(tmp, "x.go", "this is not go"))   # no gofmt -> skipped
 65 |         self.assertTrue({".ts", ".go", ".rs", ".c", ".ps1", ".yaml"} <= CHECKED_SUFFIXES)
 66 | 
 67 | 
```
- For every installed language: the valid file passes, the broken one fails (`subTest` reports each language
  separately; missing programs are skipped).
- A C file that includes a header which does not exist here passes (it cannot be checked without that library).
- An unknown file type passes; with the program "uninstalled" (a mock), even a nonsense `.go` file passes — checks are
  skipped, never failed, when their tool is missing. The checked-extension list includes the new languages.

## Streaming
```python
 68 | class StreamTests(unittest.TestCase):
```
```python
 69 |     def lines(self, *events: str) -> list[bytes]:
 70 |         return [f"data: {e}\n".encode() for e in events] + [b"\n", b"data: [DONE]\n"]
 71 | 
```
```python
 72 |     def test_text_and_tool_calls_are_assembled(self):
 73 |         pieces: list[str] = []
 74 |         message, usage = read_stream(self.lines(
 75 |             '{"choices":[{"delta":{"content":"Hel"}}]}',
 76 |             ': keep-alive comment',
 77 |             '{"choices":[{"delta":{"content":"lo"}}]}',
 78 |             '{"choices":[{"delta":{"tool_calls":[{"index":0,"id":"c1","function":{"name":"read_",'
 79 |             '"arguments":"{\\"pa"}}]}}]}',
 80 |             '{"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"name":"file",'
 81 |             '"arguments":"th\\": \\"a\\"}"},"extra_content":{"google":{"thought_signature":"S"}}}]}}]}',
 82 |             '{"choices":[],"usage":{"prompt_tokens":9,"completion_tokens":4}}',
 83 |         ), pieces.append)
 84 |         self.assertEqual(pieces, ["Hel", "lo"])
 85 |         self.assertEqual(message["content"], "Hello")
 86 |         call = message["tool_calls"][0]
 87 |         self.assertEqual((call["id"], call["function"]["name"]), ("c1", "read_file"))
 88 |         self.assertEqual(call["function"]["arguments"], '{"path": "a"}')
 89 |         self.assertEqual(call["extra_content"]["google"]["thought_signature"], "S")
 90 |         self.assertEqual(usage["prompt_tokens"], 9)
 91 | 
```
```python
 92 |     def test_error_inside_a_stream(self):
 93 |         from localagent.llm import LLMError
 94 |         with self.assertRaises(LLMError):
 95 |             read_stream(self.lines('{"error":{"message":"overloaded"}}'), None)
 96 | 
```
```python
 97 |     def test_summary_is_printed_when_streamed_text_differs(self):
 98 |         class Streaming:
 99 |             def chat(self, messages, tools, on_token=None):
100 |                 on_token("Working on it.")
101 |                 from localagent.llm import Reply
102 |                 return Reply({"role": "assistant", "content": "Working on it.", "tool_calls": [
103 |                     {"function": {"name": "finish", "arguments": {"summary": "All done: 3 files."}}}]},
104 |                     1, 1, 0.0)
105 | 
106 |         with tempfile.TemporaryDirectory() as tmp:
107 |             cfg = replace(Config(), workspace=Path(tmp))
108 |             box = ToolBox(cfg, lambda _q: True)
109 |             out = io.StringIO()
110 |             with contextlib.redirect_stdout(out):
111 |                 handle_request(Agent(cfg, Streaming(), box, Meter()), box, Meter(), "Why?")
112 |             self.assertIn("Working on it.", out.getvalue())
113 |             self.assertIn("All done: 3 files.", out.getvalue())
114 | 
115 | 
```
- `lines` turns event texts into `data: ...` lines ending with `[DONE]`, as a server sends them.
- Two text pieces become "Hello" and reach the display in order; a comment line is ignored; a tool call split over two
  chunks (name `read_` + `file`, argument text in two halves) is glued back together with its id and thought
  signature; the token counts from the last chunk are returned.
- An error object inside the stream raises `LLMError`.
- When the model streams "Working on it." but finishes with the summary "All done: 3 files.", both appear on screen.

## Helper agents
```python
116 | class HelperTests(unittest.TestCase):
```
```python
117 |     def test_delegate_runs_a_read_only_helper_and_returns_its_report(self):
118 |         with tempfile.TemporaryDirectory() as tmp:
119 |             (Path(tmp) / "a.py").write_text("def invoice_total():\n    return 1\n", encoding="utf-8")
120 |             script = [tool_call("delegate", task="find invoice code"),             # main agent
121 |                       tool_call("write_file", path="x.py", content="x = 1\n"),     # helper tries
122 |                       tool_call("search", pattern="invoice"),                       # helper reads
123 |                       tool_call("finish", summary="invoice_total is in a.py line 1"),
124 |                       tool_call("finish", summary="Found it")]                     # main agent
125 |             backend = RecordingBackend(script)
126 |             cfg = replace(Config(), workspace=Path(tmp))
127 |             meter = Meter()
128 |             agent = Agent(cfg, backend, ToolBox(cfg, lambda _q: True), meter)
129 |             events: list[str] = []
130 |             self.assertEqual(agent.run("Where is the invoice code?", on_event=events.append), "Found it")
131 |             self.assertIn("delegate", backend.offered[0])                 # main agent may delegate
132 |             self.assertNotIn("delegate", backend.offered[1])              # a helper may not
133 |             self.assertNotIn("write_file", backend.offered[1])            # helpers only read
134 |             self.assertFalse((Path(tmp) / "x.py").exists())
135 |             report = [m["content"] for m in agent.messages if m["role"] == "tool"][0]
136 |             self.assertIn("HELPER REPORT:", report)
137 |             self.assertIn("invoice_total is in a.py", report)
138 |             self.assertEqual(meter.calls, 5)                              # helper calls are counted
139 |             self.assertTrue(any(e.startswith("   | ") for e in events))
140 |             self.assertEqual(DELEGATE_SPEC["function"]["name"], "delegate")
141 | 
```
```python
142 |     def test_helper_leaves_the_main_routing_alone(self):
143 |         class Routing(RecordingBackend):
144 |             def __init__(self, messages):
145 |                 super().__init__(messages)
146 |                 self.resets = self.escalations = 0
147 | 
148 |             def reset_route(self):
149 |                 self.resets += 1
150 | 
151 |             def escalate(self):
152 |                 self.escalations += 1
153 |                 return True
154 | 
155 |         with tempfile.TemporaryDirectory() as tmp:
156 |             bad = tool_call("read_file", path="missing.txt")              # fails: no such file
157 |             backend = Routing([tool_call("delegate", task="look"), bad, bad,
158 |                                tool_call("finish", summary="nothing found"),
159 |                                tool_call("finish", summary="done")])
160 |             cfg = replace(Config(), workspace=Path(tmp))
161 |             Agent(cfg, backend, ToolBox(cfg, lambda _q: True), Meter()).run("Where is X?")
162 |             self.assertEqual(backend.resets, 1)                           # only the main agent
163 |             self.assertEqual(backend.escalations, 0)                      # helper trouble ignored
164 | 
```
```python
165 |     def test_helpers_can_be_switched_off(self):
166 |         with tempfile.TemporaryDirectory() as tmp:
167 |             backend = RecordingBackend([{"role": "assistant", "content": "ok"}])
168 |             cfg = replace(Config(), workspace=Path(tmp), helpers=False)
169 |             Agent(cfg, backend, ToolBox(cfg, lambda _q: True), Meter()).run("hi")
170 |             self.assertNotIn("delegate", backend.offered[0])
171 | 
172 | 
```
- The main agent calls `delegate`; the helper is offered neither `delegate` nor `write_file`, its write attempt is
  refused, it searches and finishes; the main agent receives `HELPER REPORT: ...` and finishes. All 5 model calls are
  counted by the shared meter and the helper's steps are shown with `|`.
- A helper whose reads keep failing neither resets nor escalates the shared model chain: only the main agent's one
  reset at the start of its request is seen.
- With `helpers=False` the tool is not offered.

## Commands and tiers
```python
173 | class CommandAndTierTests(unittest.TestCase):
```
```python
174 |     def test_custom_commands_load_and_expand(self):
175 |         with tempfile.TemporaryDirectory() as tmp:
176 |             user = Path(tmp) / "user"
177 |             user.mkdir()
178 |             (user / "review.md").write_text("Review $ARGUMENTS for bugs.", encoding="utf-8")
179 |             (user / "Bad Name!.md").write_text("ignored", encoding="utf-8")
180 |             project = Path(tmp) / ".localagent" / "commands"
181 |             project.mkdir(parents=True)
182 |             (project / "review.md").write_text("Project review of $ARGUMENTS.", encoding="utf-8")
183 |             (project / "docs.md").write_text("Write docs.", encoding="utf-8")
184 |             commands = load_commands(Path(tmp), user)
185 |             self.assertEqual(sorted(commands), ["docs", "review"])           # project wins
186 |             self.assertEqual(expand_command(commands["review"], "app.py"), "Project review of app.py.")
187 |             self.assertEqual(expand_command(commands["docs"], "for api.py"), "Write docs.\n\nfor api.py")
188 |             self.assertEqual(expand_command(commands["docs"], ""), "Write docs.")
189 | 
```
```python
190 |     def test_paid_tier_puts_strong_models_first_with_higher_limits(self):
191 |         with mock.patch.dict(os.environ, {"LOCALAGENT_GEMINI_TIER": "paid", "GEMINI_API_KEY": "k"}):
192 |             cfg = Config.from_env()
193 |         self.assertEqual((cfg.gemini_tier, cfg.rpm, cfg.rpd), ("paid", 1000, 10_000))
194 |         backend = make_gemini(cfg)
195 |         order = [e.name for e in backend._order()]
196 |         self.assertEqual(order[: len(cfg.gemini_strong_models)], list(cfg.gemini_strong_models))
197 |         self.assertFalse(backend.escalate())                                 # no reserve on paid
198 |         with mock.patch.dict(os.environ, {"LOCALAGENT_RPM": "7"}):
199 |             self.assertEqual(Config.from_env().rpm, 7)
200 | 
201 | 
```
- A user and a project `review.md`: the project one wins; a file with an invalid name is ignored; `$ARGUMENTS` is
  replaced, or the words are appended when the file has no placeholder.
- `LOCALAGENT_GEMINI_TIER=paid` gives 1000 requests/minute and 10 000/day, puts the strong models first and leaves no
  reserve to escalate to; `LOCALAGENT_RPM` overrides the limit directly.

## Big folders
```python
202 | class HookTests(unittest.TestCase):
```
```python
203 |     def test_after_edit_failure_reaches_the_model_and_after_request_runs(self):
204 |         with tempfile.TemporaryDirectory() as tmp:
205 |             hooks = {"after_edit": 'echo "checked {file}"; exit 3', "after_request": 'echo "all tests ok"'}
206 |             box = ToolBox(replace(Config(), workspace=Path(tmp)), lambda _q: True, {"hooks": hooks})
207 |             result = box.call("write_file", {"path": "n.txt", "content": "hi"})
208 |             self.assertTrue(result.startswith("OK: wrote n.txt"))
209 |             self.assertIn("HOOK after_edit failed (exit code 3)", result)
210 |             self.assertIn("checked n.txt", result)
211 |             code, output = box.run_hook("after_request")
212 |             self.assertEqual((code, output.strip()), (0, "all tests ok"))
213 |             self.assertIsNone(ToolBox(replace(Config(), workspace=Path(tmp))).run_hook("after_edit"))
214 | 
```
```python
215 |     def test_hooks_merge_from_settings_files(self):
216 |         with tempfile.TemporaryDirectory() as tmp:
217 |             user = Path(tmp) / "user.json"
218 |             user.write_text('{"hooks": {"after_edit": "a", "after_request": "b"}}', encoding="utf-8")
219 |             project = Path(tmp) / ".localagent" / "settings.json"
220 |             project.parent.mkdir()
221 |             project.write_text('{"hooks": {"after_edit": "c"}}', encoding="utf-8")
222 |             from localagent.settings import load_settings
223 |             self.assertEqual(load_settings(Path(tmp), user)["hooks"],
224 |                              {"after_edit": "c", "after_request": "b"})
225 | 
226 | 
```
- A failing `after_edit` hook (it prints the file name and exits 3) leaves the write in place but appends
  `HOOK after_edit failed (exit code 3)` and its output to the result; `after_request` runs and returns its output;
  without hooks nothing runs. (The test command quotes its text so PowerShell and `sh` print it identically.)
- Hooks merge from the user and project settings files, the project winning per hook.

```python
227 | class BigFolderTests(unittest.TestCase):
```
```python
228 |     def test_snapshots_switch_off_once_for_huge_folders(self):
229 |         with tempfile.TemporaryDirectory() as tmp:
230 |             for i in range(5):
231 |                 (Path(tmp) / f"f{i}.txt").write_text("x", encoding="utf-8")
232 |             snaps = Checkpoints(Path(tmp), max_files=3)
233 |             first = snaps.take()
234 |             self.assertIn("/undo is off", first or "")
235 |             self.assertIsNone(snaps.take())                                  # said only once
236 |             self.assertTrue(snaps.undo().startswith("undo is off"))
237 |             self.assertIsNone(Checkpoints(Path(tmp)).take())                 # normal size: fine
238 | 
```
```python
239 |     def test_workspace_check_can_look_only_at_recent_changes(self):
240 |         with tempfile.TemporaryDirectory() as tmp:
241 |             old = Path(tmp) / "old.json"
242 |             old.write_text("{broken", encoding="utf-8")
243 |             past = time.time() - 3600
244 |             os.utime(old, (past, past))                                      # broken long ago
245 |             box = ToolBox(replace(Config(), workspace=Path(tmp)), lambda _q: True)
246 |             self.assertEqual(len(box.check_workspace()), 1)
247 |             self.assertEqual(box.check_workspace(since=time.time() - 60), [])
248 |             (Path(tmp) / "new.json").write_text("{also broken", encoding="utf-8")
249 |             self.assertEqual(len(box.check_workspace(since=box.started)), 1)
250 |             self.assertIn("NOT RUN", box.call("run_tests", {}))             # broken this session
251 |             (Path(tmp) / "new.json").unlink()
252 |             self.assertIn("exit code", box.call("run_tests", {}))           # old file does not block
253 | 
```
```python
254 |     def test_search_stops_on_enormous_folders(self):
255 |         self.assertGreaterEqual(MAX_SEARCH_FILES, 1000)
256 |         with tempfile.TemporaryDirectory() as tmp:
257 |             for i in range(3):
258 |                 (Path(tmp) / f"m{i}.py").write_text("x = 1\n", encoding="utf-8")
259 |             box = ToolBox(replace(Config(), workspace=Path(tmp)), lambda _q: True)
260 |             with mock.patch("localagent.tools.MAX_SEARCH_FILES", 2):
261 |                 self.assertIn("stopped after looking at 2 files", box.call("search", {"pattern": "zzz"}))
262 | 
263 | 
```
- With a 3-file limit, a 5-file folder switches `/undo` off with one message (the next snapshot is silent and `undo`
  explains why); a normal folder snapshots fine.
- A file broken an hour ago is found by a full check but not by a check of recent changes; a file broken now blocks
  `run_tests`; once it is gone, the old broken file does not block the tests.
- With the file limit lowered to 2 (a mock), a search stops with the "narrow the search" note.

```python
264 | if __name__ == "__main__":
265 |     unittest.main()
```
- Lets you run the file directly.

