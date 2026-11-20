"""Build docs/*.md from docs_src/*.md and prove every code line is documented.

A source doc contains explanation text plus marker lines of the form
``@@ <file> <block> @@``. The builder replaces each marker with the real code of
that block (with line numbers), so the docs can never disagree with the code.
``<block>`` is a symbol name - ``header``, ``Config``, ``Config.from_env``,
``main``, ``footer`` - or a plain line range such as ``7-13``. Symbol blocks
move with the code, so editing a file does not break its docs. The builder then
checks that, for every Python file in the project, the markers cover each line
exactly once.

Usage:
    python tools/build_docs.py              # regenerate docs/
    python tools/build_docs.py --check      # fail if docs/ is stale or incomplete
    python tools/build_docs.py --list FILE  # show the block names of FILE
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MARKER = re.compile(r"^@@ (\S+) (\S+) @@$")
RANGE = re.compile(r"^(\d+)-(\d+)$")
CODE_GLOBS = ("localagent/*.py", "tests/*.py", "tools/*.py", "evals/*.py")
DEFS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


def read_lines(relative: str) -> list[str]:
    """Return the lines of a project file."""
    return (ROOT / relative).read_text(encoding="utf-8").splitlines()


def first_line(node: ast.stmt) -> int:
    """First line of a statement, including decorators written above it."""
    decorators = getattr(node, "decorator_list", [])
    return min([node.lineno] + [d.lineno for d in decorators])


def blocks(relative: str) -> dict[str, tuple[int, int]]:
    """Split a file into named, contiguous, non-overlapping line blocks."""
    lines = read_lines(relative)
    starts: list[tuple[int, str]] = []
    for node in ast.parse("\n".join(lines)).body:
        if isinstance(node, DEFS):
            starts.append((first_line(node), node.name))
            if isinstance(node, ast.ClassDef):
                starts += [(first_line(m), f"{node.name}.{m.name}")
                           for m in node.body if isinstance(m, DEFS)]
        elif starts and not starts[-1][1].startswith("after_"):
            previous = starts[-1][1].split(".")[0]           # top-level name before this code
            starts.append((first_line(node), f"after_{previous}"))
    if starts and starts[-1][1].startswith("after_"):
        starts[-1] = (starts[-1][0], "footer")               # code after the LAST def
    table: dict[str, tuple[int, int]] = {}
    begin = starts[0][0] if starts else len(lines) + 1
    if begin > 1:
        table["header"] = (1, begin - 1)
    for index, (start, name) in enumerate(starts):
        end = starts[index + 1][0] - 1 if index + 1 < len(starts) else len(lines)
        if name in table:
            raise SystemExit(f"{relative}: block name '{name}' is not unique; use a line range")
        table[name] = (start, end)
    return table


def resolve(relative: str, spec: str) -> tuple[int, int]:
    """Turn a marker's block spec into (first line, last line)."""
    match = RANGE.match(spec)
    if match:
        return int(match.group(1)), int(match.group(2))
    table = blocks(relative)
    if spec not in table:
        raise SystemExit(f"unknown block '{spec}' in {relative}; available: {', '.join(table)}")
    return table[spec]


def expand(text: str, covered: dict[str, list[int]]) -> str:
    """Replace every marker in ``text`` with a numbered code block."""
    out: list[str] = []
    for line in text.splitlines():
        match = MARKER.match(line)
        if not match:
            out.append(line)
            continue
        relative = match.group(1)
        code = read_lines(relative)
        first, last = resolve(relative, match.group(2))
        if not 1 <= first <= last <= len(code):
            raise SystemExit(f"bad range in marker: {line}")
        covered.setdefault(relative, []).extend(range(first, last + 1))
        width = len(str(len(code)))
        out.append("```python")
        out.extend(f"{n:>{width}} | {code[n - 1]}" for n in range(first, last + 1))
        out.append("```")
    return "\n".join(out) + "\n"


def coverage_problems(covered: dict[str, list[int]]) -> list[str]:
    """List every file whose lines are not documented exactly once."""
    problems: list[str] = []
    for path in sorted(p for pattern in CODE_GLOBS for p in ROOT.glob(pattern)):
        relative = path.relative_to(ROOT).as_posix()
        wanted = list(range(1, len(read_lines(relative)) + 1))
        got = sorted(covered.get(relative, []))
        if got != wanted:
            missing = sorted(set(wanted) - set(got))
            doubled = sorted({n for n in got if got.count(n) > 1})
            names = [name for name, (a, b) in blocks(relative).items()
                     if any(n in missing for n in range(a, b + 1))]
            problems.append(f"{relative}: undocumented lines {missing}, duplicated {doubled}; "
                            f"blocks needing a marker: {names}")
    return problems


def main(argv: list[str]) -> int:
    """Build (or with --check, verify) the docs; return a process exit code."""
    if argv[:1] == ["--list"]:
        for name, (first, last) in blocks(argv[1]).items():
            print(f"{name:<32} lines {first}-{last}")
        return 0
    covered: dict[str, list[int]] = {}
    outputs = {
        ROOT / "docs" / src.name: expand(src.read_text(encoding="utf-8"), covered)
        for src in sorted((ROOT / "docs_src").glob("*.md"))
    }
    problems = coverage_problems(covered)
    if "--check" in argv:
        problems += [f"{p.relative_to(ROOT)} is stale" for p, text in outputs.items()
                     if not p.exists() or p.read_text(encoding="utf-8") != text]
    elif not problems:
        (ROOT / "docs").mkdir(exist_ok=True)
        for path, text in outputs.items():
            path.write_text(text, encoding="utf-8")
    for problem in problems:
        print("DOCS PROBLEM:", problem)
    if not problems:
        print(f"docs OK: {len(outputs)} pages, every code line documented exactly once")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
