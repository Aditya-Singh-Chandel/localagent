"""Pre-run verification of Python files.

Code is checked in layers, cheapest first. A layer that finds errors stops the
pipeline, because later tools give noise on code that does not even parse.

  1. compile  - the Python compiler itself (syntax + scoping errors). Always on.
  2. ruff     - undefined names and other hard errors. Used if installed.
  3. mypy     - type errors. Used only if installed AND ``type_check`` is true.
Other files: JSON, TOML (and YAML with PyYAML) must parse; JavaScript, TypeScript, Go, Rust,
C/C++, PHP, Ruby, shell and PowerShell are checked by their own tools when installed.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from .sandbox import run_command

# Only rules that mean "this will crash": syntax errors, undefined names, etc.
RUFF_RULES = "E9,F63,F7,F82"
RUFF_FINDING = re.compile(r":\d+:\d+: [A-Z]+\d+ ")   # one finding: "file:line:col: CODE message"


@dataclass(frozen=True)
class Report:
    """Outcome of verifying one file."""

    ok: bool
    problems: tuple[str, ...] = ()

    def render(self) -> str:
        """Text shown to the model (and the user)."""
        return "all checks passed" if self.ok else "\n".join(self.problems)


def _installed(module: str) -> bool:
    """True when ``python -m <module>`` would work in this interpreter."""
    return importlib.util.find_spec(module) is not None


def _compile_stage(path: Path) -> list[str]:
    """Compile the file without running it; report syntax errors with line numbers.

    The raw bytes are compiled, so Python decodes them exactly as when the file runs: a
    byte-order mark (PowerShell writes one) and a ``# coding:`` line are understood.
    """
    try:
        compile(path.read_bytes(), str(path.name), "exec", dont_inherit=True)
    except SyntaxError as exc:
        return [f"{path.name}:{exc.lineno}:{exc.offset}: SyntaxError: {exc.msg}"]
    except (ValueError, UnicodeDecodeError) as exc:
        return [f"{path.name}: cannot compile: {exc}"]
    return []


def _ruff_stage(path: Path, cwd: Path) -> list[str]:
    """Run ruff for hard errors; skipped (no problems) when ruff is not installed."""
    if not _installed("ruff"):
        return []
    code, output = run_command(
        [sys.executable, "-m", "ruff", "check", "--select", RUFF_RULES,
         "--output-format", "concise", "--no-cache", str(path)],
        cwd, timeout=60, limit=3000,
    )
    if code == 0:
        return []
    findings = [line for line in output.splitlines() if RUFF_FINDING.search(line)]
    if findings and all(" E902 " in line for line in findings):
        return []          # ruff could not read the file (an older encoding); compile() could
    return [output.strip()]


def _mypy_stage(path: Path, cwd: Path) -> list[str]:
    """Run mypy for type errors; skipped when mypy is not installed."""
    if not _installed("mypy"):
        return []
    code, output = run_command(
        [sys.executable, "-m", "mypy", "--ignore-missing-imports",
         "--no-error-summary", "--no-color-output", str(path)],
        cwd, timeout=120, limit=3000,
    )
    return [] if code == 0 else [output.strip()]


# Files that are "JSON with comments" (JSONC): their tools accept // and /* */ comments and
# trailing commas, so rejecting them as broken JSON would be a false alarm.
JSONC_NAMES = re.compile(r"^(tsconfig|jsconfig)[\w.-]*\.json$|^(\.?devcontainer|deno|tslint|"
                         r"\.eslintrc|\.babelrc)\.json$", re.IGNORECASE)
JSONC_FOLDERS = frozenset({".vscode", ".devcontainer"})


def is_jsonc(path: Path) -> bool:
    """True for files whose format allows comments: ``*.jsonc``, tsconfig.json, VS Code settings."""
    return (path.suffix.lower() == ".jsonc" or bool(JSONC_NAMES.match(path.name))
            or path.parent.name in JSONC_FOLDERS)


def strip_jsonc(text: str) -> str:
    """Plain JSON from JSONC: comments become spaces (line breaks kept, so error positions
    still match the file) and commas before a closing ``}`` or ``]`` are dropped."""
    out: list[str] = []
    i, quoted = 0, False
    while i < len(text):
        char = text[i]
        if quoted:                                 # inside "...": copy, honouring \" escapes
            out.append(text[i:i + 2] if char == "\\" else char)
            quoted = char != '"'
            i += 2 if char == "\\" else 1
        elif char == '"':
            quoted = True
            out.append(char)
            i += 1
        elif text.startswith(("//", "/*"), i):
            line_comment = text.startswith("//", i)
            end = text.find("\n" if line_comment else "*/", i + 2)
            end = len(text) if end == -1 else end + (0 if line_comment else 2)
            out.append(re.sub(r"[^\n]", " ", text[i:end]))
            i = end
        elif char == "," and re.match(r"\s*[}\]]", _rest_without_comments(text, i + 1)):
            out.append(" ")                        # a trailing comma
            i += 1
        else:
            out.append(char)
            i += 1
    return "".join(out)


def _rest_without_comments(text: str, start: int) -> str:
    """The text after ``start`` with leading whitespace and comments skipped (for comma checks)."""
    i = start
    while i < len(text):
        if text[i].isspace():
            i += 1
        elif text.startswith("//", i):
            end = text.find("\n", i)
            i = len(text) if end == -1 else end
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = len(text) if end == -1 else end + 2
        else:
            break
    return text[i:i + 1]


def _read_checked_text(path: Path) -> str:
    """A config file's text; a leading byte-order mark (common from Windows editors) is fine."""
    return path.read_text(encoding="utf-8-sig")


def _json_stage(path: Path) -> list[str]:
    """JSON must parse (JSONC files may have comments); report the first error's position."""
    try:
        text = _read_checked_text(path)
    except UnicodeDecodeError as exc:
        return [f"{path.name}: not valid UTF-8 text: {exc}"]
    try:
        json.loads(strip_jsonc(text) if is_jsonc(path) else text)
    except json.JSONDecodeError as exc:
        return [f"{path.name}:{exc.lineno}:{exc.colno}: invalid JSON: {exc.msg}"]
    return []


def _toml_stage(path: Path) -> list[str]:
    """TOML (e.g. pyproject.toml) must parse; needs Python 3.11+ (skipped otherwise)."""
    try:
        import tomllib
    except ImportError:
        return []
    try:
        tomllib.loads(_read_checked_text(path))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        return [f"{path.name}: invalid TOML: {exc}"]
    return []


def _yaml_stage(path: Path) -> list[str]:
    """YAML must parse when PyYAML is installed (skipped otherwise)."""
    try:
        import yaml  # type: ignore[import-untyped]
    except ImportError:
        return []
    try:
        list(yaml.safe_load_all(_read_checked_text(path)))
    except (yaml.YAMLError, UnicodeDecodeError) as exc:
        return [f"{path.name}: invalid YAML: {exc}"]
    return []


# TypeScript: Node.js (22.13+) can remove type annotations itself; a syntax error makes it fail
# with the line and a caret. "transform" mode also accepts enums and namespaces. Older Node
# versions lack the function, and then the check is skipped (exit 0) instead of failing.
TS_CHECK = ("const m = require('node:module');"
            "if (typeof m.stripTypeScriptTypes !== 'function') process.exit(0);"
            "try { m.stripTypeScriptTypes(require('fs').readFileSync(process.argv[1], 'utf8'),"
            " {mode: 'transform'}); }"
            "catch (e) { console.error(e.message); process.exit(1); }")
PS_CHECK = ("$errors = $null; [void][System.Management.Automation.Language.Parser]::ParseFile("
            "'{path}', [ref]$null, [ref]$errors); if ($errors) { $errors | ForEach-Object { "
            "\"line $($_.Extent.StartLineNumber): $($_.Message)\" }; exit 1 }")


@dataclass(frozen=True)
class Checker:
    """An outside program that can check one language's syntax without running the code."""

    program: str                                # must be installed (found on PATH)
    args: tuple[str, ...]                       # "{file}" is replaced by the file's path
    windows: bool | None = None                 # True: Windows only; False: never on Windows
    stdin: bool = False                         # the file's text is sent as input instead
    alternative: "Checker | None" = None        # the file also passes if THIS check passes


RUST_CHECK = Checker("rustfmt", ("--edition", "2021", "--emit", "stdout"), stdin=True)
CPP_CHECK = Checker("g++", ("-fsyntax-only", "{file}"))
CHECKERS: dict[str, Checker] = {
    ".js": Checker("node", ("--check", "{file}")),
    ".mjs": Checker("node", ("--check", "{file}")),
    ".cjs": Checker("node", ("--check", "{file}")),
    ".ts": Checker("node", ("--no-warnings", "-e", TS_CHECK, "{file}")),
    ".mts": Checker("node", ("--no-warnings", "-e", TS_CHECK, "{file}")),
    ".go": Checker("gofmt", ("-e", "-l", "{file}")),
    ".rs": RUST_CHECK,          # via input: rustfmt then ignores `mod x;` files not written yet
    ".c": Checker("gcc", ("-fsyntax-only", "{file}")),
    ".h": Checker("gcc", ("-fsyntax-only", "{file}"), alternative=CPP_CHECK),   # C or C++
    ".cpp": CPP_CHECK,
    ".cc": CPP_CHECK,
    ".hpp": CPP_CHECK,
    ".php": Checker("php", ("-l", "{file}")),
    ".rb": Checker("ruby", ("-c", "{file}")),
    ".sh": Checker("bash", ("-n", "{file}"), windows=False),   # Windows "bash" may be WSL's
    ".ps1": Checker("powershell", ("-NoProfile", "-NonInteractive", "-Command", PS_CHECK),
                    windows=True),
}


def _external_stage(checker: Checker, path: Path, cwd: Path) -> list[str]:
    """Run a language's own syntax checker; skipped when the program is not installed."""
    on_windows = os.name == "nt"
    if checker.windows is not None and checker.windows != on_windows:
        return []
    program = shutil.which(checker.program)
    if program is None:
        return []
    quoted = str(path).replace("'", "''")         # for PowerShell's single-quoted string
    argv = [program] + [a.replace("{file}", str(path)).replace("{path}", quoted)
                        for a in checker.args]
    text = path.read_text(encoding="utf-8", errors="replace") if checker.stdin else None
    code, output = run_command(argv, cwd, timeout=60, limit=3000, input_text=text)
    if code == 0:
        return []
    if "No such file or directory" in output and output.count("error") <= 1:
        return []                                 # a missing outside header: cannot check
    problem = output.replace("<stdin>", path.name).strip() or \
        f"{path.name}: {checker.program} reported a problem"
    if checker.alternative is not None:
        second = _external_stage(checker.alternative, path, cwd)
        if not second:
            return []                             # valid as the other language (a C++ header)
        return [f"{checker.program}:\n{problem}\n{checker.alternative.program}:\n{second[0]}"]
    return [problem]


CHECKED_SUFFIXES = frozenset({".py", ".json", ".jsonc", ".toml", ".yaml", ".yml", *CHECKERS})


def verify_file(path: Path, cwd: Path, type_check: bool = False) -> Report:
    """Verify any supported file by its extension; other files always pass."""
    suffix = path.suffix.lower()
    if suffix == ".py":
        return verify_python(path, cwd, type_check)
    if suffix in (".json", ".jsonc"):
        problems = _json_stage(path)
    elif suffix == ".toml":
        problems = _toml_stage(path)
    elif suffix in (".yaml", ".yml"):
        problems = _yaml_stage(path)
    elif suffix in CHECKERS:
        problems = _external_stage(CHECKERS[suffix], path, cwd)
    else:
        problems = []
    return Report(not problems, tuple(problems))


def verify_python(path: Path, cwd: Path, type_check: bool = False) -> Report:
    """Verify one Python file on disk and return a Report (never raises)."""
    problems = _compile_stage(path)
    if problems:                      # unparsable code: stop here
        return Report(False, tuple(problems))
    problems = _ruff_stage(path, cwd)
    if problems:
        return Report(False, tuple(problems))
    if type_check:
        problems = _mypy_stage(path, cwd)
        if problems:
            return Report(False, tuple(problems))
    return Report(True)
