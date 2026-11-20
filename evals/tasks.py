"""The fixed coding tasks used to score the agent, each with an automatic check.

A check is ordinary Python that inspects the finished workspace and returns
True/False, so scoring needs no human and no model.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from localagent.sandbox import walk_files


def python_ok(workspace: Path, code: str) -> bool:
    """Run ``code`` with the workspace as the working folder; True if it exits 0."""
    done = subprocess.run([sys.executable, "-c", code], cwd=workspace,
                          capture_output=True, text=True, timeout=60)
    return done.returncode == 0


def tests_pass(workspace: Path) -> bool:
    """True if ``unittest discover`` succeeds in the workspace."""
    done = subprocess.run([sys.executable, "-m", "unittest", "discover"], cwd=workspace,
                          capture_output=True, text=True, timeout=60)
    return done.returncode == 0


@dataclass(frozen=True)
class Task:
    """One evaluation: files to start from, the request, and how to judge the result."""

    name: str
    prompt: str
    check: Callable[[Path], bool]
    seed: dict[str, str] = field(default_factory=dict)
    answer_check: Callable[[str], bool] | None = None    # judges the reply text (questions)


FIZZBUZZ = Task(
    name="fizzbuzz",
    prompt="Create fizzbuzz.py with a function fizzbuzz(n) that returns a list of strings "
           "for 1..n: 'Fizz' for multiples of 3, 'Buzz' for 5, 'FizzBuzz' for both, else the number.",
    check=lambda ws: python_ok(ws, (
        "from fizzbuzz import fizzbuzz; r = fizzbuzz(15); "
        "assert len(r) == 15 and r[2] == 'Fizz' and r[4] == 'Buzz' and r[14] == 'FizzBuzz' "
        "and r[0] == '1'")),
)

FIX_BUG = Task(
    name="fix_bug",
    prompt="The tests in test_calc.py fail. Find and fix the bug in calc.py.",
    seed={
        "calc.py": "def average(numbers):\n    return sum(numbers) / (len(numbers) + 1)\n",
        "test_calc.py": ("import unittest\nfrom calc import average\n\n\n"
                         "class T(unittest.TestCase):\n    def test_avg(self):\n"
                         "        self.assertEqual(average([2, 4, 6]), 4)\n"),
    },
    check=tests_pass,
)

ADD_METHOD = Task(
    name="add_method",
    prompt="Add a peek() method to the Stack class in stack.py. It returns the top item "
           "without removing it and raises IndexError when the stack is empty.",
    seed={"stack.py": ("class Stack:\n    def __init__(self):\n        self._items = []\n\n"
                       "    def push(self, item):\n        self._items.append(item)\n\n"
                       "    def pop(self):\n        return self._items.pop()\n")},
    check=lambda ws: python_ok(ws, (
        "from stack import Stack; s = Stack(); s.push(1); s.push(2); "
        "assert s.peek() == 2 and s.pop() == 2 and s.peek() == 1\n"
        "try:\n    Stack().peek()\nexcept IndexError:\n    pass\nelse:\n    raise SystemExit(1)")),
)

WORD_COUNT = Task(
    name="word_count",
    prompt="Write wordcount.py: a script that takes a file path as its first argument and "
           "prints only the number of words in that file.",
    seed={"sample.txt": "one two three four five\n"},
    check=lambda ws: subprocess.run(
        [sys.executable, "wordcount.py", "sample.txt"], cwd=ws, capture_output=True,
        text=True, timeout=60).stdout.strip() == "5",
)

COUNTER_CLASS = Task(
    name="counter_class",
    prompt="Create counter.py with a class Counter: increment() adds 1, decrement() subtracts 1, "
           "and the read-only property value returns the current count, starting at 0.",
    check=lambda ws: python_ok(ws, (
        "from counter import Counter; c = Counter(); assert c.value == 0; c.increment(); "
        "c.increment(); c.decrement(); assert c.value == 1")),
)

RENAME_FUNCTION = Task(
    name="rename_function",
    prompt="Rename the function calc_total to compute_total everywhere in the project.",
    seed={
        "shop.py": "def calc_total(prices):\n    return sum(prices)\n",
        "main.py": "from shop import calc_total\n\nprint(calc_total([1, 2, 3]))\n",
    },
    check=lambda ws: (
        "calc_total" not in (ws / "shop.py").read_text(encoding="utf-8")
        and "calc_total" not in (ws / "main.py").read_text(encoding="utf-8")
        and python_ok(ws, "from shop import compute_total; assert compute_total([1, 2]) == 3")
        and subprocess.run([sys.executable, "main.py"], cwd=ws, capture_output=True,
                           text=True, timeout=60).stdout.strip() == "6"),
)

JSON_KEY = Task(
    name="json_key",
    prompt="Write getname.py: a script that reads config.json from the current folder and prints "
           "the value of its 'name' key (only the value).",
    seed={"config.json": '{"name": "orion", "version": 3}'},
    check=lambda ws: subprocess.run(
        [sys.executable, "getname.py"], cwd=ws, capture_output=True,
        text=True, timeout=60).stdout.strip() == "orion",
)

DEFAULT_ARG = Task(
    name="default_arg",
    prompt="In greet.py change the default value of the 'greeting' parameter from 'Hello' to 'Hi'. "
           "Change nothing else.",
    seed={"greet.py": ('def greet(name, greeting="Hello"):\n'
                       '    return f"{greeting}, {name}!"\n\n\n'
                       "def shout(name):\n    return greet(name).upper()\n")},
    check=lambda ws: python_ok(ws, (
        "from greet import greet, shout; assert greet('Bo') == 'Hi, Bo!'; "
        "assert greet('Bo', 'Yo') == 'Yo, Bo!'; assert shout('bo') == 'HI, BO!'")),
)

ADD_TESTS = Task(
    name="add_tests",
    prompt="Write test_mathx.py with unittest tests for the functions in mathx.py (at least one test "
           "per function). The tests must pass.",
    seed={"mathx.py": ("def double(x):\n    return x * 2\n\n\n"
                       "def is_even(x):\n    return x % 2 == 0\n")},
    check=lambda ws: (ws / "test_mathx.py").is_file() and tests_pass(ws)
    and (ws / "test_mathx.py").read_text(encoding="utf-8").count("def test_") >= 2,
)

PACKAGE_FEATURE = Task(
    name="package_feature",
    prompt="In the inventory package, add a method remove_item(name) to Store that removes the item "
           "and raises KeyError for an unknown name. Add a test for both cases to "
           "tests/test_store.py. All tests must pass.",
    seed={
        "inventory/__init__.py": "",
        "inventory/models.py": ("from dataclasses import dataclass\n\n\n@dataclass\n"
                                "class Item:\n    name: str\n    quantity: int\n"),
        "inventory/store.py": ("from inventory.models import Item\n\n\nclass Store:\n"
                               "    def __init__(self):\n        self._items = {}\n\n"
                               "    def add_item(self, name, quantity):\n"
                               "        self._items[name] = Item(name, quantity)\n\n"
                               "    def count(self):\n        return len(self._items)\n"),
        "tests/__init__.py": "",
        "tests/test_store.py": ("import unittest\n\nfrom inventory.store import Store\n\n\n"
                                "class StoreTests(unittest.TestCase):\n"
                                "    def test_add(self):\n        store = Store()\n"
                                "        store.add_item('apple', 3)\n"
                                "        self.assertEqual(store.count(), 1)\n"),
    },
    check=lambda ws: tests_pass(ws)
    and (ws / "tests" / "test_store.py").read_text(encoding="utf-8").count("def test_") >= 2
    and python_ok(ws, (
        "from inventory.store import Store; s = Store(); s.add_item('a', 1); s.remove_item('a')\n"
        "assert s.count() == 0\n"
        "try:\n    s.remove_item('zzz')\nexcept KeyError:\n    pass\nelse:\n    raise SystemExit(1)")),
)


def node_ok(workspace: Path, code: str) -> bool:
    """Run a JavaScript snippet with Node.js in the workspace; True if it exits 0."""
    node = shutil.which("node")
    if node is None:
        return False
    done = subprocess.run([node, "-e", code], cwd=workspace, capture_output=True, text=True,
                          timeout=60)
    return done.returncode == 0


JS_SLUGIFY = Task(
    name="js_slugify",
    prompt="Create slugify.js (CommonJS) exporting a function slugify(text) that lower-cases the "
           "text, turns every run of non-alphanumeric characters into one '-', and trims '-' from "
           "both ends. Example: 'Hello, World!' -> 'hello-world'.",
    check=lambda ws: node_ok(ws, (
        "const m = require('./slugify.js');"
        "const slugify = typeof m === 'function' ? m : m.slugify;"   # either export style is fine
        "const ok = slugify('Hello, World!') === 'hello-world'"
        " && slugify('  Many   spaces  ') === 'many-spaces' && slugify('a--b') === 'a-b';"
        "process.exit(ok ? 0 : 1);")),
)

def only_seed_files(workspace: Path, seed: dict[str, str]) -> bool:
    """True when the agent created no files (answering a question must not change the project)."""
    found = {p.relative_to(workspace).as_posix() for p in walk_files(workspace)}
    return found == set(seed)


def go_prints(workspace: Path, expected: str) -> bool:
    """Run ``go run main.go`` in the workspace; True if it prints ``expected`` (False without Go)."""
    go = shutil.which("go")
    if go is None:
        return False
    done = subprocess.run([go, "run", "main.go"], cwd=workspace, capture_output=True, text=True,
                          timeout=120)
    return done.returncode == 0 and done.stdout.strip() == expected


def ts_ok(workspace: Path, code: str) -> bool:
    """Run a JavaScript module snippet that may import ``.ts`` files (Node strips the types)."""
    node = shutil.which("node")
    if node is None:
        return False
    done = subprocess.run([node, "--no-warnings", "--experimental-strip-types", "--input-type=module",
                           "-e", code], cwd=workspace, capture_output=True, text=True, timeout=60)
    return done.returncode == 0


# Correct answers word it differently ("not found", "cannot find the requested resource", ...);
# the first version accepted only "not found" and failed a correct answer.
NOT_FOUND_PHRASES = ("not found", "cannot find", "can't find", "could not find", "couldn't find",
                     "does not exist", "doesn't exist", "not be found")

QUESTION_GENERAL = Task(
    name="question_general",
    prompt="In one or two sentences: what does the HTTP status code 404 mean?",
    check=lambda ws: only_seed_files(ws, {}),
    answer_check=lambda answer: any(phrase in answer.lower() for phrase in NOT_FOUND_PHRASES),
)

SHOP_SEED = {
    "shop/__init__.py": "",
    "shop/pricing.py": ("def compute_total(prices, tax_rate):\n"
                        "    return round(sum(prices) * (1 + tax_rate), 2)\n"),
    "shop/cli.py": ("from shop.pricing import compute_total\n\n"
                    "print(compute_total([10, 20], 0.1))\n"),
}

QUESTION_PROJECT = Task(
    name="question_project",
    prompt="Which function in this project calculates the total price including tax, and in which "
           "file is it? Answer briefly.",
    seed=SHOP_SEED,
    check=lambda ws: only_seed_files(ws, SHOP_SEED),
    answer_check=lambda answer: "compute_total" in answer and "pricing.py" in answer,
)

GO_ADD = Task(
    name="go_add",
    prompt="Create main.go (package main) with a function Add(a, b int) int and a main function that "
           "prints Add(2, 3) using fmt.Println.",
    check=lambda ws: go_prints(ws, "5"),
)

TS_TEMPERATURE = Task(
    name="ts_temperature",
    prompt="Create temperature.ts that exports a function cToF(celsius: number): number converting "
           "Celsius to Fahrenheit.",
    check=lambda ws: ts_ok(ws, (
        "const m = await import('./temperature.ts');"
        "if (m.cToF(100) !== 212 || m.cToF(0) !== 32) process.exit(1);")),
)

TASKS = [FIZZBUZZ, FIX_BUG, ADD_METHOD, WORD_COUNT, COUNTER_CLASS, RENAME_FUNCTION, JSON_KEY,
         DEFAULT_ARG, ADD_TESTS, PACKAGE_FEATURE, JS_SLUGIFY, QUESTION_GENERAL, QUESTION_PROJECT,
         GO_ADD, TS_TEMPERATURE]
