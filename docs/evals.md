# `evals/` — measuring quality instead of guessing

**Role:** you cannot improve what you cannot measure. The eval harness gives each model a fixed set of small coding
tasks in clean temporary workspaces and checks the *result* automatically (does the code actually behave correctly?).
Use it to compare models (`--model qwen3:8b` vs `qwen2.5-coder:7b`), to compare settings (`--type-check`), and to catch
regressions after changing the prompt or tools. It reports pass/fail, model calls, tokens and time per task.

Run: `python -m evals.run_evals --model qwen3:8b` (needs a running model server).

## `evals/__init__.py`
```python
1 | """Evaluation harness: measures how well a model+agent pair solves fixed tasks."""
```
- Marks the folder as a package (so `python -m evals.run_evals` works). The line is a docstring.

## `evals/tasks.py`
```python
  1 | """The fixed coding tasks used to score the agent, each with an automatic check.
  2 | 
  3 | A check is ordinary Python that inspects the finished workspace and returns
  4 | True/False, so scoring needs no human and no model.
  5 | """
  6 | from __future__ import annotations
  7 | 
  8 | import shutil
  9 | import subprocess
 10 | import sys
 11 | from dataclasses import dataclass, field
 12 | from pathlib import Path
 13 | from typing import Callable
 14 | 
 15 | from localagent.sandbox import walk_files
 16 | 
 17 | 
```
- Imports `shutil` (find Node/Go), `subprocess`/`sys` (to run checks in separate processes), `dataclass`/`field`,
  `Path`, `Callable`, and `walk_files` (list a workspace without caches).

```python
 18 | def python_ok(workspace: Path, code: str) -> bool:
 19 |     """Run ``code`` with the workspace as the working folder; True if it exits 0."""
 20 |     done = subprocess.run([sys.executable, "-c", code], cwd=workspace,
 21 |                           capture_output=True, text=True, timeout=60)
 22 |     return done.returncode == 0
 23 | 
 24 | 
```
- Runs a snippet of Python with the task's workspace as the working folder, so `import fizzbuzz` finds the agent's
  file. Returns `True` when the snippet exits with code 0 (all its `assert`s held). 60 s timeout.

```python
 25 | def tests_pass(workspace: Path) -> bool:
 26 |     """True if ``unittest discover`` succeeds in the workspace."""
 27 |     done = subprocess.run([sys.executable, "-m", "unittest", "discover"], cwd=workspace,
 28 |                           capture_output=True, text=True, timeout=60)
 29 |     return done.returncode == 0
 30 | 
 31 | 
```
- Runs `unittest discover` in the workspace; success means the agent fixed the failing tests.

```python
 32 | @dataclass(frozen=True)
 33 | class Task:
 34 |     """One evaluation: files to start from, the request, and how to judge the result."""
 35 | 
 36 |     name: str
 37 |     prompt: str
 38 |     check: Callable[[Path], bool]
 39 |     seed: dict[str, str] = field(default_factory=dict)
 40 |     answer_check: Callable[[str], bool] | None = None    # judges the reply text (questions)
 41 | 
 42 | 
```
- One evaluation: a `name`, the user `prompt`, a `check` function (workspace → bool), optional `seed` files created
  before the model starts, and an optional `answer_check` that judges the **reply text** — needed for questions, where
  the right outcome is an answer, not a file. `field(default_factory=dict)` gives each task its own empty dict.

```python
 43 | FIZZBUZZ = Task(
 44 |     name="fizzbuzz",
 45 |     prompt="Create fizzbuzz.py with a function fizzbuzz(n) that returns a list of strings "
 46 |            "for 1..n: 'Fizz' for multiples of 3, 'Buzz' for 5, 'FizzBuzz' for both, else the number.",
 47 |     check=lambda ws: python_ok(ws, (
 48 |         "from fizzbuzz import fizzbuzz; r = fizzbuzz(15); "
 49 |         "assert len(r) == 15 and r[2] == 'Fizz' and r[4] == 'Buzz' and r[14] == 'FizzBuzz' "
 50 |         "and r[0] == '1'")),
 51 | )
 52 | 
 53 | FIX_BUG = Task(
 54 |     name="fix_bug",
 55 |     prompt="The tests in test_calc.py fail. Find and fix the bug in calc.py.",
 56 |     seed={
 57 |         "calc.py": "def average(numbers):\n    return sum(numbers) / (len(numbers) + 1)\n",
 58 |         "test_calc.py": ("import unittest\nfrom calc import average\n\n\n"
 59 |                          "class T(unittest.TestCase):\n    def test_avg(self):\n"
 60 |                          "        self.assertEqual(average([2, 4, 6]), 4)\n"),
 61 |     },
 62 |     check=tests_pass,
 63 | )
 64 | 
 65 | ADD_METHOD = Task(
 66 |     name="add_method",
 67 |     prompt="Add a peek() method to the Stack class in stack.py. It returns the top item "
 68 |            "without removing it and raises IndexError when the stack is empty.",
 69 |     seed={"stack.py": ("class Stack:\n    def __init__(self):\n        self._items = []\n\n"
 70 |                        "    def push(self, item):\n        self._items.append(item)\n\n"
 71 |                        "    def pop(self):\n        return self._items.pop()\n")},
 72 |     check=lambda ws: python_ok(ws, (
 73 |         "from stack import Stack; s = Stack(); s.push(1); s.push(2); "
 74 |         "assert s.peek() == 2 and s.pop() == 2 and s.peek() == 1\n"
 75 |         "try:\n    Stack().peek()\nexcept IndexError:\n    pass\nelse:\n    raise SystemExit(1)")),
 76 | )
 77 | 
 78 | WORD_COUNT = Task(
 79 |     name="word_count",
 80 |     prompt="Write wordcount.py: a script that takes a file path as its first argument and "
 81 |            "prints only the number of words in that file.",
 82 |     seed={"sample.txt": "one two three four five\n"},
 83 |     check=lambda ws: subprocess.run(
 84 |         [sys.executable, "wordcount.py", "sample.txt"], cwd=ws, capture_output=True,
 85 |         text=True, timeout=60).stdout.strip() == "5",
 86 | )
 87 | 
 88 | COUNTER_CLASS = Task(
 89 |     name="counter_class",
 90 |     prompt="Create counter.py with a class Counter: increment() adds 1, decrement() subtracts 1, "
 91 |            "and the read-only property value returns the current count, starting at 0.",
 92 |     check=lambda ws: python_ok(ws, (
 93 |         "from counter import Counter; c = Counter(); assert c.value == 0; c.increment(); "
 94 |         "c.increment(); c.decrement(); assert c.value == 1")),
 95 | )
 96 | 
 97 | RENAME_FUNCTION = Task(
 98 |     name="rename_function",
 99 |     prompt="Rename the function calc_total to compute_total everywhere in the project.",
100 |     seed={
101 |         "shop.py": "def calc_total(prices):\n    return sum(prices)\n",
102 |         "main.py": "from shop import calc_total\n\nprint(calc_total([1, 2, 3]))\n",
103 |     },
104 |     check=lambda ws: (
105 |         "calc_total" not in (ws / "shop.py").read_text(encoding="utf-8")
106 |         and "calc_total" not in (ws / "main.py").read_text(encoding="utf-8")
107 |         and python_ok(ws, "from shop import compute_total; assert compute_total([1, 2]) == 3")
108 |         and subprocess.run([sys.executable, "main.py"], cwd=ws, capture_output=True,
109 |                            text=True, timeout=60).stdout.strip() == "6"),
110 | )
111 | 
112 | JSON_KEY = Task(
113 |     name="json_key",
114 |     prompt="Write getname.py: a script that reads config.json from the current folder and prints "
115 |            "the value of its 'name' key (only the value).",
116 |     seed={"config.json": '{"name": "orion", "version": 3}'},
117 |     check=lambda ws: subprocess.run(
118 |         [sys.executable, "getname.py"], cwd=ws, capture_output=True,
119 |         text=True, timeout=60).stdout.strip() == "orion",
120 | )
121 | 
122 | DEFAULT_ARG = Task(
123 |     name="default_arg",
124 |     prompt="In greet.py change the default value of the 'greeting' parameter from 'Hello' to 'Hi'. "
125 |            "Change nothing else.",
126 |     seed={"greet.py": ('def greet(name, greeting="Hello"):\n'
127 |                        '    return f"{greeting}, {name}!"\n\n\n'
128 |                        "def shout(name):\n    return greet(name).upper()\n")},
129 |     check=lambda ws: python_ok(ws, (
130 |         "from greet import greet, shout; assert greet('Bo') == 'Hi, Bo!'; "
131 |         "assert greet('Bo', 'Yo') == 'Yo, Bo!'; assert shout('bo') == 'HI, BO!'")),
132 | )
133 | 
134 | ADD_TESTS = Task(
135 |     name="add_tests",
136 |     prompt="Write test_mathx.py with unittest tests for the functions in mathx.py (at least one test "
137 |            "per function). The tests must pass.",
138 |     seed={"mathx.py": ("def double(x):\n    return x * 2\n\n\n"
139 |                        "def is_even(x):\n    return x % 2 == 0\n")},
140 |     check=lambda ws: (ws / "test_mathx.py").is_file() and tests_pass(ws)
141 |     and (ws / "test_mathx.py").read_text(encoding="utf-8").count("def test_") >= 2,
142 | )
143 | 
144 | PACKAGE_FEATURE = Task(
145 |     name="package_feature",
146 |     prompt="In the inventory package, add a method remove_item(name) to Store that removes the item "
147 |            "and raises KeyError for an unknown name. Add a test for both cases to "
148 |            "tests/test_store.py. All tests must pass.",
149 |     seed={
150 |         "inventory/__init__.py": "",
151 |         "inventory/models.py": ("from dataclasses import dataclass\n\n\n@dataclass\n"
152 |                                 "class Item:\n    name: str\n    quantity: int\n"),
153 |         "inventory/store.py": ("from inventory.models import Item\n\n\nclass Store:\n"
154 |                                "    def __init__(self):\n        self._items = {}\n\n"
155 |                                "    def add_item(self, name, quantity):\n"
156 |                                "        self._items[name] = Item(name, quantity)\n\n"
157 |                                "    def count(self):\n        return len(self._items)\n"),
158 |         "tests/__init__.py": "",
159 |         "tests/test_store.py": ("import unittest\n\nfrom inventory.store import Store\n\n\n"
160 |                                 "class StoreTests(unittest.TestCase):\n"
161 |                                 "    def test_add(self):\n        store = Store()\n"
162 |                                 "        store.add_item('apple', 3)\n"
163 |                                 "        self.assertEqual(store.count(), 1)\n"),
164 |     },
165 |     check=lambda ws: tests_pass(ws)
166 |     and (ws / "tests" / "test_store.py").read_text(encoding="utf-8").count("def test_") >= 2
167 |     and python_ok(ws, (
168 |         "from inventory.store import Store; s = Store(); s.add_item('a', 1); s.remove_item('a')\n"
169 |         "assert s.count() == 0\n"
170 |         "try:\n    s.remove_item('zzz')\nexcept KeyError:\n    pass\nelse:\n    raise SystemExit(1)")),
171 | )
172 | 
173 | 
```
```python
174 | def node_ok(workspace: Path, code: str) -> bool:
175 |     """Run a JavaScript snippet with Node.js in the workspace; True if it exits 0."""
176 |     node = shutil.which("node")
177 |     if node is None:
178 |         return False
179 |     done = subprocess.run([node, "-e", code], cwd=workspace, capture_output=True, text=True,
180 |                           timeout=60)
181 |     return done.returncode == 0
182 | 
183 | 
```
```python
184 | JS_SLUGIFY = Task(
185 |     name="js_slugify",
186 |     prompt="Create slugify.js (CommonJS) exporting a function slugify(text) that lower-cases the "
187 |            "text, turns every run of non-alphanumeric characters into one '-', and trims '-' from "
188 |            "both ends. Example: 'Hello, World!' -> 'hello-world'.",
189 |     check=lambda ws: node_ok(ws, (
190 |         "const m = require('./slugify.js');"
191 |         "const slugify = typeof m === 'function' ? m : m.slugify;"   # either export style is fine
192 |         "const ok = slugify('Hello, World!') === 'hello-world'"
193 |         " && slugify('  Many   spaces  ') === 'many-spaces' && slugify('a--b') === 'a-b';"
194 |         "process.exit(ok ? 0 : 1);")),
195 | )
196 | 
```
- The first eleven tasks:
  - `FIZZBUZZ`: create a module from scratch; check imports it and asserts the 1st, 3rd, 5th and 15th results.
  - `FIX_BUG`: `calc.py` divides by `len+1`; a seeded unittest fails; the check is that tests now pass.
  - `ADD_METHOD`: extend a seeded `Stack` class with `peek()`; check covers normal use and the `IndexError` on empty.
  - `WORD_COUNT`: write a script; check runs it on a seeded file with five words and expects exactly `5`.
  - `COUNTER_CLASS`: create a class from a spec (`increment`, `decrement`, read-only `value`); check exercises it.
  - `RENAME_FUNCTION`: a two-file project; the name `calc_total` must vanish from both files, the new name must work and
    `main.py` must still print `6` - tests that the agent edits *every* place.
  - `JSON_KEY`: write a script that reads a seeded `config.json` and prints only the `name` value (`orion`).
  - `DEFAULT_ARG`: a one-word change in an existing file ("change nothing else"); the check also proves the other
    behaviour is intact - guards against over-editing.
  - `ADD_TESTS`: write unittest tests for a seeded module; needs the file, at least two `def test_`, and passing tests.
  - `PACKAGE_FEATURE`: a small **multi-file package** (`inventory/` + `tests/`); add `Store.remove_item` with a
    `KeyError` for unknown names and a test for both cases; all tests must pass and the behaviour is checked directly.
  - `JS_SLUGIFY`: a **JavaScript** task, checked by running Node.js (`node_ok`). Either export style
    (`module.exports = slugify` or `{ slugify }`) is accepted, because the request allows both readings - the first
    version of this check accepted only one and failed a correct solution.
- `node_ok` runs a JavaScript snippet with Node in the workspace (False when Node is missing).

```python
197 | def only_seed_files(workspace: Path, seed: dict[str, str]) -> bool:
198 |     """True when the agent created no files (answering a question must not change the project)."""
199 |     found = {p.relative_to(workspace).as_posix() for p in walk_files(workspace)}
200 |     return found == set(seed)
201 | 
202 | 
```
- Lists the workspace with `walk_files` (so caches like `.ruff_cache` do not count) and compares it with the seed: a
  question must not create or delete files.

```python
203 | def go_prints(workspace: Path, expected: str) -> bool:
204 |     """Run ``go run main.go`` in the workspace; True if it prints ``expected`` (False without Go)."""
205 |     go = shutil.which("go")
206 |     if go is None:
207 |         return False
208 |     done = subprocess.run([go, "run", "main.go"], cwd=workspace, capture_output=True, text=True,
209 |                           timeout=120)
210 |     return done.returncode == 0 and done.stdout.strip() == expected
211 | 
212 | 
```
- Runs `go run main.go` in the workspace (two-minute limit; the first Go build is slow) and compares the output.

```python
213 | def ts_ok(workspace: Path, code: str) -> bool:
214 |     """Run a JavaScript module snippet that may import ``.ts`` files (Node strips the types)."""
215 |     node = shutil.which("node")
216 |     if node is None:
217 |         return False
218 |     done = subprocess.run([node, "--no-warnings", "--experimental-strip-types", "--input-type=module",
219 |                            "-e", code], cwd=workspace, capture_output=True, text=True, timeout=60)
220 |     return done.returncode == 0
221 | 
222 | 
223 | # Correct answers word it differently ("not found", "cannot find the requested resource", ...);
224 | # the first version accepted only "not found" and failed a correct answer.
```
- Runs a JavaScript **module** snippet with Node's type stripping on, so it can `await import('./x.ts')` and test a
  TypeScript file directly.

```python
225 | NOT_FOUND_PHRASES = ("not found", "cannot find", "can't find", "could not find", "couldn't find",
226 |                      "does not exist", "doesn't exist", "not be found")
227 | 
228 | QUESTION_GENERAL = Task(
229 |     name="question_general",
230 |     prompt="In one or two sentences: what does the HTTP status code 404 mean?",
231 |     check=lambda ws: only_seed_files(ws, {}),
232 |     answer_check=lambda answer: any(phrase in answer.lower() for phrase in NOT_FOUND_PHRASES),
233 | )
234 | 
235 | SHOP_SEED = {
236 |     "shop/__init__.py": "",
237 |     "shop/pricing.py": ("def compute_total(prices, tax_rate):\n"
238 |                         "    return round(sum(prices) * (1 + tax_rate), 2)\n"),
239 |     "shop/cli.py": ("from shop.pricing import compute_total\n\n"
240 |                     "print(compute_total([10, 20], 0.1))\n"),
241 | }
242 | 
243 | QUESTION_PROJECT = Task(
244 |     name="question_project",
245 |     prompt="Which function in this project calculates the total price including tax, and in which "
246 |            "file is it? Answer briefly.",
247 |     seed=SHOP_SEED,
248 |     check=lambda ws: only_seed_files(ws, SHOP_SEED),
249 |     answer_check=lambda answer: "compute_total" in answer and "pricing.py" in answer,
250 | )
251 | 
252 | GO_ADD = Task(
253 |     name="go_add",
254 |     prompt="Create main.go (package main) with a function Add(a, b int) int and a main function that "
255 |            "prints Add(2, 3) using fmt.Println.",
256 |     check=lambda ws: go_prints(ws, "5"),
257 | )
258 | 
259 | TS_TEMPERATURE = Task(
260 |     name="ts_temperature",
261 |     prompt="Create temperature.ts that exports a function cToF(celsius: number): number converting "
262 |            "Celsius to Fahrenheit.",
263 |     check=lambda ws: ts_ok(ws, (
264 |         "const m = await import('./temperature.ts');"
265 |         "if (m.cToF(100) !== 212 || m.cToF(0) !== 32) process.exit(1);")),
266 | )
267 | 
268 | TASKS = [FIZZBUZZ, FIX_BUG, ADD_METHOD, WORD_COUNT, COUNTER_CLASS, RENAME_FUNCTION, JSON_KEY,
269 |          DEFAULT_ARG, ADD_TESTS, PACKAGE_FEATURE, JS_SLUGIFY, QUESTION_GENERAL, QUESTION_PROJECT,
270 |          GO_ADD, TS_TEMPERATURE]
```
- The four newest tasks, measuring the two things a coding agent must also do well:
  - `QUESTION_GENERAL`: a general-knowledge question (what HTTP 404 means). Passes if the reply says the resource was
    not found — in any of the usual wordings (`NOT_FOUND_PHRASES`) — and no file was created. The first version
    accepted only the words "not found" and failed the correct answer "the server cannot find the requested resource".
  - `QUESTION_PROJECT`: a question about a seeded mini shop project; the reply must name `compute_total` and
    `pricing.py`, and the project must be unchanged.
  - `GO_ADD`: write a Go program; `go run` must print `5`.
  - `TS_TEMPERATURE`: write a TypeScript module; importing it must give `cToF(100) == 212` and `cToF(0) == 32`.
- `TASKS` is the list the runner iterates over (15 tasks). Add new tasks here; seed file names may include folders.

## `evals/run_evals.py`
```python
 1 | """Run the evaluation tasks against a real model and print a scoreboard.
 2 | 
 3 | Usage:  python -m evals.run_evals --model qwen3:8b [--only fizzbuzz] [--type-check]
 4 | Each task gets a fresh temporary workspace, so tasks cannot affect each other.
 5 | """
 6 | from __future__ import annotations
 7 | 
 8 | import argparse
 9 | import tempfile
10 | from dataclasses import dataclass, replace
11 | from pathlib import Path
12 | 
13 | from localagent.agent import Agent
14 | from localagent.backends import make_backend
15 | from localagent.cli import add_model_flags, model_flags, model_label
16 | from localagent.config import Config, gemini_history, overlay
17 | from localagent.llm import ChatBackend, LLMError
18 | from localagent.meter import Meter
19 | from localagent.tools import ToolBox
20 | 
21 | from .tasks import TASKS, Task
22 | 
23 | 
```
- Imports the agent pieces (agent, backend registry, the shared flags and `model_label` from `cli.py`, config with its
  shared rules, `LLMError`, meter, toolbox) and the task
  definitions.

```python
24 | @dataclass(frozen=True)
25 | class Result:
26 |     """Outcome of one task."""
27 | 
28 |     name: str
29 |     passed: bool
30 |     calls: int
31 |     tokens: int
32 |     seconds: float
33 |     note: str = ""
34 | 
35 | 
```
- Immutable scoreboard row: name, pass/fail, model calls, total tokens, seconds, and a note for errors.

```python
36 | def run_task(task: Task, config: Config, backend: ChatBackend, trace: bool = False) -> Result:
37 |     """Run one task in a throw-away workspace and judge the result with its check."""
38 |     with tempfile.TemporaryDirectory() as folder:
39 |         workspace = Path(folder)
40 |         for name, content in task.seed.items():
41 |             (workspace / name).parent.mkdir(parents=True, exist_ok=True)   # seeds may be in folders
42 |             (workspace / name).write_text(content, encoding="utf-8")
43 |         run_config = replace(config, workspace=workspace, auto_approve=True)
44 |         meter = Meter()
45 |         toolbox = ToolBox(run_config, lambda _a: True)
46 |         agent = Agent(run_config, backend, toolbox, meter)
47 |         note = ""
48 |         answer = ""
49 |         try:
50 |             answer = agent.run(task.prompt, on_event=print if trace else (lambda _m: None))
51 |         except LLMError as exc:
52 |             note = f"model error: {exc}"
53 |         finally:
54 |             toolbox.close()                         # stop the sandbox before the folder is deleted
55 |         passed = False if note else task.check(workspace) and (
56 |             task.answer_check is None or task.answer_check(answer))
57 |         return Result(task.name, passed, meter.calls,
58 |                       meter.prompt_tokens + meter.completion_tokens, meter.seconds, note)
59 | 
60 | 
```
- Creates a temporary workspace (deleted afterwards), writes the seed files (creating their folders), and builds a config pointing at it with
  `auto_approve=True` (unattended). Builds a fresh `Agent` with a fresh meter, runs the prompt, and records any
  `LLMError` as a note. The toolbox is **closed in a `finally` block** before the temporary folder is deleted, so a Docker sandbox container
  is always removed. With `trace=True` each tool call and its first result line are printed live (`on_event`). The task's `check` judges the final workspace and, when present,
  `answer_check` judges the reply text (questions); a model error counts as a fail. The backend is a parameter so
  tests can inject a fake.

```python
61 | def main(argv: list[str] | None = None) -> int:
62 |     """Run all (or selected) tasks and print one line per task plus a summary."""
63 |     parser = argparse.ArgumentParser(prog="evals.run_evals")
64 |     add_model_flags(parser)
65 |     parser.add_argument("--only", action="append", help="task name (repeatable)")
66 |     parser.add_argument("--type-check", action="store_true")
67 |     parser.add_argument("--trace", action="store_true", help="print every tool call")
68 |     args = parser.parse_args(argv)
69 |     config = overlay(Config.from_env(), **model_flags(args), type_check=args.type_check)
70 |     config = gemini_history(config)                     # measure what the chat really uses
71 |     backend = make_backend(config)
72 |     tasks = [t for t in TASKS if not args.only or t.name in args.only]
73 |     results = []
74 |     for task in tasks:
75 |         result = run_task(task, config, backend, args.trace)
76 |         results.append(result)
77 |         status = "PASS" if result.passed else "FAIL"
78 |         print(f"{status}  {result.name:<12} calls={result.calls:<3} tokens={result.tokens:<6} "
79 |               f"time={result.seconds:.0f}s {result.note}")
80 |     passed = sum(r.passed for r in results)
81 |     print(f"\n{passed}/{len(results)} tasks passed with {model_label(config)}")
82 |     return 0 if passed == len(results) else 1
83 | 
84 | 
```
- Parses the shared flags (`add_model_flags`), repeated `--only NAME` filters, `--type-check` and `--trace` (print every
  tool call); overlays them on the environment config and applies `gemini_history`, so the evals measure exactly
  the setup the chat uses; the summary names the model chain via `model_label`; builds the backend; runs each selected task and prints one line each, then a summary. Exit code is
  0 only if every task passed, so scripts can gate on it.

```python
85 | if __name__ == "__main__":
86 |     raise SystemExit(main())
```
- Standard script entry point.
