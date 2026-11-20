# `tools/build_docs.py` — keeps these docs honest

**Role:** you asked for documentation of every line. Hand-written docs rot, so this script ties them to the code. Doc
sources live in `docs_src/` and contain explanations plus marker lines; the script pastes the *real* code into `docs/`
and refuses to finish if any line of any project `.py` file is undocumented or documented twice. Markers refer to
code by **symbol name** (`Config.from_env`), so editing code does not break the docs — it just changes the pasted text
and tells you which new block needs an explanation.

Run after any code change: `python tools/build_docs.py` (`--check` in CI; `--list FILE` shows block names).

## Module header
```python
  1 | """Build docs/*.md from docs_src/*.md and prove every code line is documented.
  2 | 
  3 | A source doc contains explanation text plus marker lines of the form
  4 | ``@@ <file> <block> @@``. The builder replaces each marker with the real code of
  5 | that block (with line numbers), so the docs can never disagree with the code.
  6 | ``<block>`` is a symbol name - ``header``, ``Config``, ``Config.from_env``,
  7 | ``main``, ``footer`` - or a plain line range such as ``7-13``. Symbol blocks
  8 | move with the code, so editing a file does not break its docs. The builder then
  9 | checks that, for every Python file in the project, the markers cover each line
 10 | exactly once.
 11 | 
 12 | Usage:
 13 |     python tools/build_docs.py              # regenerate docs/
 14 |     python tools/build_docs.py --check      # fail if docs/ is stale or incomplete
 15 |     python tools/build_docs.py --list FILE  # show the block names of FILE
 16 | """
 17 | from __future__ import annotations
 18 | 
 19 | import ast
 20 | import re
 21 | import sys
 22 | from pathlib import Path
 23 | 
 24 | ROOT = Path(__file__).resolve().parent.parent
 25 | MARKER = re.compile(r"^@@ (\S+) (\S+) @@$")
 26 | RANGE = re.compile(r"^(\d+)-(\d+)$")
 27 | CODE_GLOBS = ("localagent/*.py", "tests/*.py", "tools/*.py", "evals/*.py")
 28 | DEFS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
 29 | 
 30 | 
```
- Imports `ast` (Python's own parser, used to find where each function/class starts and ends), `re`, `sys`, `Path`.
- `ROOT` is the project folder. `MARKER` matches a whole line `@@ file block @@`; `RANGE` matches `7-13` style
  specs. `CODE_GLOBS` lists which files must be fully documented. `DEFS` names the statement types that start a block.

## `read_lines`
```python
 31 | def read_lines(relative: str) -> list[str]:
 32 |     """Return the lines of a project file."""
 33 |     return (ROOT / relative).read_text(encoding="utf-8").splitlines()
 34 | 
 35 | 
```
- Reads a project file and splits it into lines (no newline characters).

## `first_line`
```python
 36 | def first_line(node: ast.stmt) -> int:
 37 |     """First line of a statement, including decorators written above it."""
 38 |     decorators = getattr(node, "decorator_list", [])
 39 |     return min([node.lineno] + [d.lineno for d in decorators])
 40 | 
 41 | 
```
- A decorated function's `lineno` points at `def`, not at the `@decorator` above it; this returns the earlier line so
  decorators belong to the right block.

## `blocks`
```python
 42 | def blocks(relative: str) -> dict[str, tuple[int, int]]:
 43 |     """Split a file into named, contiguous, non-overlapping line blocks."""
 44 |     lines = read_lines(relative)
 45 |     starts: list[tuple[int, str]] = []
 46 |     for node in ast.parse("\n".join(lines)).body:
 47 |         if isinstance(node, DEFS):
 48 |             starts.append((first_line(node), node.name))
 49 |             if isinstance(node, ast.ClassDef):
 50 |                 starts += [(first_line(m), f"{node.name}.{m.name}")
 51 |                            for m in node.body if isinstance(m, DEFS)]
 52 |         elif starts and not starts[-1][1].startswith("after_"):
 53 |             previous = starts[-1][1].split(".")[0]           # top-level name before this code
 54 |             starts.append((first_line(node), f"after_{previous}"))
 55 |     if starts and starts[-1][1].startswith("after_"):
 56 |         starts[-1] = (starts[-1][0], "footer")               # code after the LAST def
 57 |     table: dict[str, tuple[int, int]] = {}
 58 |     begin = starts[0][0] if starts else len(lines) + 1
 59 |     if begin > 1:
 60 |         table["header"] = (1, begin - 1)
 61 |     for index, (start, name) in enumerate(starts):
 62 |         end = starts[index + 1][0] - 1 if index + 1 < len(starts) else len(lines)
 63 |         if name in table:
 64 |             raise SystemExit(f"{relative}: block name '{name}' is not unique; use a line range")
 65 |         table[name] = (start, end)
 66 |     return table
 67 | 
 68 | 
```
- Parses the file and records a start line for every top-level function/class, every method inside a class
  (`Class.method`), code *between* definitions (`after_<name of the definition before it>`, e.g. a table of
  constants), and code after the last definition (`footer`, e.g. the `if __name__` guard).
- `header` is everything before the first definition. Each block runs from its start to the line before the next
  start, so blank lines and comments attach to the block above them and every line belongs to exactly one block.
- Duplicate names are rejected with a hint to use a line range.

## `resolve`
```python
 69 | def resolve(relative: str, spec: str) -> tuple[int, int]:
 70 |     """Turn a marker's block spec into (first line, last line)."""
 71 |     match = RANGE.match(spec)
 72 |     if match:
 73 |         return int(match.group(1)), int(match.group(2))
 74 |     table = blocks(relative)
 75 |     if spec not in table:
 76 |         raise SystemExit(f"unknown block '{spec}' in {relative}; available: {', '.join(table)}")
 77 |     return table[spec]
 78 | 
 79 | 
```
- A numeric spec returns that range directly; a symbol is looked up in `blocks`. An unknown symbol raises an error
  listing the valid names.

## `expand`
```python
 80 | def expand(text: str, covered: dict[str, list[int]]) -> str:
 81 |     """Replace every marker in ``text`` with a numbered code block."""
 82 |     out: list[str] = []
 83 |     for line in text.splitlines():
 84 |         match = MARKER.match(line)
 85 |         if not match:
 86 |             out.append(line)
 87 |             continue
 88 |         relative = match.group(1)
 89 |         code = read_lines(relative)
 90 |         first, last = resolve(relative, match.group(2))
 91 |         if not 1 <= first <= last <= len(code):
 92 |             raise SystemExit(f"bad range in marker: {line}")
 93 |         covered.setdefault(relative, []).extend(range(first, last + 1))
 94 |         width = len(str(len(code)))
 95 |         out.append("```python")
 96 |         out.extend(f"{n:>{width}} | {code[n - 1]}" for n in range(first, last + 1))
 97 |         out.append("```")
 98 |     return "\n".join(out) + "\n"
 99 | 
100 | 
```
- Copies ordinary lines unchanged. For each marker it loads the file, resolves the range, validates it, records the
  covered line numbers, and emits a fenced code block with right-aligned line numbers (`  12 | code`).

## `coverage_problems`
```python
101 | def coverage_problems(covered: dict[str, list[int]]) -> list[str]:
102 |     """List every file whose lines are not documented exactly once."""
103 |     problems: list[str] = []
104 |     for path in sorted(p for pattern in CODE_GLOBS for p in ROOT.glob(pattern)):
105 |         relative = path.relative_to(ROOT).as_posix()
106 |         wanted = list(range(1, len(read_lines(relative)) + 1))
107 |         got = sorted(covered.get(relative, []))
108 |         if got != wanted:
109 |             missing = sorted(set(wanted) - set(got))
110 |             doubled = sorted({n for n in got if got.count(n) > 1})
111 |             names = [name for name, (a, b) in blocks(relative).items()
112 |                      if any(n in missing for n in range(a, b + 1))]
113 |             problems.append(f"{relative}: undocumented lines {missing}, duplicated {doubled}; "
114 |                             f"blocks needing a marker: {names}")
115 |     return problems
116 | 
117 | 
```
- For each project Python file compares the lines that *should* be documented (all of them) with those the markers
  covered. On a mismatch it reports missing lines, duplicated lines, and which blocks still need a marker.

## `main`
```python
118 | def main(argv: list[str]) -> int:
119 |     """Build (or with --check, verify) the docs; return a process exit code."""
120 |     if argv[:1] == ["--list"]:
121 |         for name, (first, last) in blocks(argv[1]).items():
122 |             print(f"{name:<32} lines {first}-{last}")
123 |         return 0
124 |     covered: dict[str, list[int]] = {}
125 |     outputs = {
126 |         ROOT / "docs" / src.name: expand(src.read_text(encoding="utf-8"), covered)
127 |         for src in sorted((ROOT / "docs_src").glob("*.md"))
128 |     }
129 |     problems = coverage_problems(covered)
130 |     if "--check" in argv:
131 |         problems += [f"{p.relative_to(ROOT)} is stale" for p, text in outputs.items()
132 |                      if not p.exists() or p.read_text(encoding="utf-8") != text]
133 |     elif not problems:
134 |         (ROOT / "docs").mkdir(exist_ok=True)
135 |         for path, text in outputs.items():
136 |             path.write_text(text, encoding="utf-8")
137 |     for problem in problems:
138 |         print("DOCS PROBLEM:", problem)
139 |     if not problems:
140 |         print(f"docs OK: {len(outputs)} pages, every code line documented exactly once")
141 |     return 1 if problems else 0
142 | 
143 | 
```
- `--list FILE` prints block names and exits. Otherwise every page in `docs_src/` is expanded in memory and coverage is
  checked. With `--check`, pages whose saved output differs are reported as stale; otherwise, if there are no problems,
  `docs/` is written. Problems are printed; the exit code is 1 if there were any.

## Entry point
```python
144 | if __name__ == "__main__":
145 |     raise SystemExit(main(sys.argv[1:]))
```
- Runs `main` only when executed as a script, passing command-line arguments, and exits with its return code.
