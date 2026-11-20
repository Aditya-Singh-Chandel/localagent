# `localagent/verify.py` — check code before it is kept or run

**Role in the flow:** this is your "verify, feed errors back, verify again" requirement. `tools.py` calls
`verify_file` (a) right after writing a file — failing code is rolled back — and (b) for every file at the end of a
request; `run_python` calls `verify_python` again right before running. The error text goes back to the model, which
fixes it, and the check repeats.

| File type | Check | Needs | Measured on this PC |
|---|---|---|---|
| `.py` | 1. Python's own compiler (syntax, scoping) → 2. ruff crash-level rules → 3. mypy types (with `--type-check`) | ruff/mypy optional | yes |
| `.json` | must parse; error shows line and column | nothing | yes |
| `.toml` (e.g. `pyproject.toml`) | must parse | Python 3.11+ | yes |
| `.yaml`, `.yml` | must parse | PyYAML (skipped without it) | — |
| `.js`, `.mjs`, `.cjs` | `node --check` | Node.js | yes |
| `.ts`, `.mts` | Node's own TypeScript type-stripper (`transform` mode, so `enum` works) | Node.js 22.13+ | yes |
| `.go` | `gofmt -e` | Go | yes |
| `.rs` | `rustfmt` (parses the file; formatting differences do not fail) | Rust | yes |
| `.c`, `.h` / `.cpp`, `.cc`, `.hpp` | `gcc -fsyntax-only` / `g++ -fsyntax-only` | a C/C++ compiler | yes |
| `.php` / `.rb` | `php -l` / `ruby -c` | PHP / Ruby | not installed here |
| `.sh` | `bash -n` (not on Windows, where `bash` may be WSL's) | bash | — |
| `.ps1` | PowerShell's own parser (Windows) | PowerShell | yes |
| anything else (`.tsx`, `.java`, `.md`, `.html` ...) | not checked (always passes) | — | — |

A checker whose program is **not installed is skipped**, never a failure — so the agent works on any computer, and gets
stricter as more language tools are installed. Nothing is ever *run*: every check only parses the file.

For Python the first failing layer stops the pipeline, because later tools give only noise on code that does not
parse.

## Module header
```python
  1 | """Pre-run verification of Python files.
  2 | 
  3 | Code is checked in layers, cheapest first. A layer that finds errors stops the
  4 | pipeline, because later tools give noise on code that does not even parse.
  5 | 
  6 |   1. compile  - the Python compiler itself (syntax + scoping errors). Always on.
  7 |   2. ruff     - undefined names and other hard errors. Used if installed.
  8 |   3. mypy     - type errors. Used only if installed AND ``type_check`` is true.
  9 | Other files: JSON, TOML (and YAML with PyYAML) must parse; JavaScript, TypeScript, Go, Rust,
 10 | C/C++, PHP, Ruby, shell and PowerShell are checked by their own tools when installed.
 11 | """
 12 | from __future__ import annotations
 13 | 
 14 | import importlib.util
 15 | import json
 16 | import os
 17 | import shutil
 18 | import sys
 19 | from dataclasses import dataclass
 20 | from pathlib import Path
 21 | 
 22 | from .sandbox import run_command
 23 | 
 24 | # Only rules that mean "this will crash": syntax errors, undefined names, etc.
 25 | RUFF_RULES = "E9,F63,F7,F82"
 26 | 
 27 | 
```
- The docstring lists the layers. `importlib.util` tests whether a package exists; `json` parses JSON; `os` tells
  Windows from other systems; `shutil.which` finds a language's program; `sys.executable` is this Python; `run_command` launches ruff, mypy and node.
- `RUFF_RULES`: `E9` syntax errors, `F63` invalid comparisons/asserts, `F7` misplaced statements, `F82` undefined
  names. Style rules are deliberately left out so the model is not nagged.

## `Report`
```python
 28 | @dataclass(frozen=True)
 29 | class Report:
 30 |     """Outcome of verifying one file."""
 31 | 
 32 |     ok: bool
 33 |     problems: tuple[str, ...] = ()
 34 | 
```
```python
 35 |     def render(self) -> str:
 36 |         """Text shown to the model (and the user)."""
 37 |         return "all checks passed" if self.ok else "\n".join(self.problems)
 38 | 
 39 | 
```
- `ok` (did it pass) and `problems` (tuple of error strings). `render` gives "all checks passed" or the problems joined
  by newlines — the text the model sees.

## Python layers
```python
 40 | def _installed(module: str) -> bool:
 41 |     """True when ``python -m <module>`` would work in this interpreter."""
 42 |     return importlib.util.find_spec(module) is not None
 43 | 
 44 | 
```
- `find_spec` returns `None` when the module is not installed: a cheap yes/no test.

```python
 45 | def _compile_stage(path: Path) -> list[str]:
 46 |     """Compile the file without running it; report syntax errors with line numbers."""
 47 |     try:
 48 |         compile(path.read_text(encoding="utf-8"), str(path.name), "exec", dont_inherit=True)
 49 |     except SyntaxError as exc:
 50 |         return [f"{path.name}:{exc.lineno}:{exc.offset}: SyntaxError: {exc.msg}"]
 51 |     except (ValueError, UnicodeDecodeError) as exc:
 52 |         return [f"{path.name}: cannot compile: {exc}"]
 53 |     return []
 54 | 
 55 | 
```
- Reads the file and calls the built-in `compile(...)` in `"exec"` mode — it *parses and analyses* but does **not run**
  anything. `dont_inherit=True` stops our own `__future__` flags leaking into the check. A `SyntaxError` becomes
  `file:line:column: SyntaxError: message`, the style editors use; other failures (null bytes, bad encoding) become a
  readable message.

```python
 56 | def _ruff_stage(path: Path, cwd: Path) -> list[str]:
 57 |     """Run ruff for hard errors; skipped (no problems) when ruff is not installed."""
 58 |     if not _installed("ruff"):
 59 |         return []
 60 |     code, output = run_command(
 61 |         [sys.executable, "-m", "ruff", "check", "--select", RUFF_RULES,
 62 |          "--output-format", "concise", "--no-cache", str(path)],
 63 |         cwd, timeout=60, limit=3000,
 64 |     )
 65 |     return [] if code == 0 else [output.strip()]
 66 | 
 67 | 
```
- Skipped when ruff is not installed. Runs `python -m ruff check` with the chosen rules, concise output and no cache
  files; `sys.executable` guarantees the same Python as the agent. Exit code 0 = clean; otherwise ruff's output is the
  problem text.

```python
 68 | def _mypy_stage(path: Path, cwd: Path) -> list[str]:
 69 |     """Run mypy for type errors; skipped when mypy is not installed."""
 70 |     if not _installed("mypy"):
 71 |         return []
 72 |     code, output = run_command(
 73 |         [sys.executable, "-m", "mypy", "--ignore-missing-imports",
 74 |          "--no-error-summary", "--no-color-output", str(path)],
 75 |         cwd, timeout=120, limit=3000,
 76 |     )
 77 |     return [] if code == 0 else [output.strip()]
 78 | 
 79 | 
```
- Same pattern. `--ignore-missing-imports` avoids noise about third-party packages without types; the other flags keep
  the output short and plain for the model.

## Other languages
```python
 80 | def _json_stage(path: Path) -> list[str]:
 81 |     """JSON must parse; report the line and column of the first error."""
 82 |     try:
 83 |         json.loads(path.read_text(encoding="utf-8"))
 84 |     except json.JSONDecodeError as exc:
 85 |         return [f"{path.name}:{exc.lineno}:{exc.colno}: invalid JSON: {exc.msg}"]
 86 |     except UnicodeDecodeError as exc:
 87 |         return [f"{path.name}: not valid UTF-8 text: {exc}"]
 88 |     return []
 89 | 
 90 | 
```
- `json.loads` either succeeds or raises `JSONDecodeError`, which carries the exact line and column; a file that is not
  UTF-8 text is reported too.

```python
 91 | def _toml_stage(path: Path) -> list[str]:
 92 |     """TOML (e.g. pyproject.toml) must parse; needs Python 3.11+ (skipped otherwise)."""
 93 |     try:
 94 |         import tomllib
 95 |     except ImportError:
 96 |         return []
 97 |     try:
 98 |         tomllib.loads(path.read_text(encoding="utf-8"))
 99 |     except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
100 |         return [f"{path.name}: invalid TOML: {exc}"]
101 |     return []
102 | 
103 | 
```
- `tomllib` is in the standard library from Python 3.11; on older Pythons the stage is skipped instead of failing.

```python
104 | def _yaml_stage(path: Path) -> list[str]:
105 |     """YAML must parse when PyYAML is installed (skipped otherwise)."""
106 |     try:
107 |         import yaml  # type: ignore[import-untyped]
108 |     except ImportError:
109 |         return []
110 |     try:
111 |         list(yaml.safe_load_all(path.read_text(encoding="utf-8")))
112 |     except (yaml.YAMLError, UnicodeDecodeError) as exc:
113 |         return [f"{path.name}: invalid YAML: {exc}"]
114 |     return []
115 | 
116 | 
117 | # TypeScript: Node.js (22.13+) can remove type annotations itself; a syntax error makes it fail
118 | # with the line and a caret. "transform" mode also accepts enums and namespaces. Older Node
119 | # versions lack the function, and then the check is skipped (exit 0) instead of failing.
```
- YAML (`docker-compose.yml`, CI files ...) is parsed with PyYAML when it is installed (`safe_load_all` handles files with
  several `---` documents); without PyYAML the check is skipped.

```python
120 | TS_CHECK = ("const m = require('node:module');"
121 |             "if (typeof m.stripTypeScriptTypes !== 'function') process.exit(0);"
122 |             "try { m.stripTypeScriptTypes(require('fs').readFileSync(process.argv[1], 'utf8'),"
123 |             " {mode: 'transform'}); }"
124 |             "catch (e) { console.error(e.message); process.exit(1); }")
125 | PS_CHECK = ("$errors = $null; [void][System.Management.Automation.Language.Parser]::ParseFile("
126 |             "'{path}', [ref]$null, [ref]$errors); if ($errors) { $errors | ForEach-Object { "
127 |             "\"line $($_.Extent.StartLineNumber): $($_.Message)\" }; exit 1 }")
128 | 
129 | 
```
- `TS_CHECK` is a tiny JavaScript program run by Node: it asks Node's built-in `stripTypeScriptTypes` (Node 22.13+) to
  remove the type annotations from the file in `transform` mode (which also understands `enum` and `namespace`). A
  syntax error makes it print Node's message — with the line and a caret under the problem — and exit 1. If the Node
  version lacks the function, it exits 0, so the check is skipped instead of wrongly failing. (`node --check` alone
  cannot be used: it parses plain JavaScript and rejects every type annotation.)
- `PS_CHECK` is a PowerShell command that runs PowerShell's own parser on the file and prints each error as
  `line N: message`. (`{path}` is filled in with the file name.)

## The language table
```python
130 | @dataclass(frozen=True)
131 | class Checker:
132 |     """An outside program that can check one language's syntax without running the code."""
133 | 
134 |     program: str                                # must be installed (found on PATH)
135 |     args: tuple[str, ...]                       # "{file}" is replaced by the file's path
136 |     windows: bool | None = None                 # True: Windows only; False: never on Windows
137 | 
138 | 
```
```python
139 | CHECKERS: dict[str, Checker] = {
140 |     ".js": Checker("node", ("--check", "{file}")),
141 |     ".mjs": Checker("node", ("--check", "{file}")),
142 |     ".cjs": Checker("node", ("--check", "{file}")),
143 |     ".ts": Checker("node", ("--no-warnings", "-e", TS_CHECK, "{file}")),
144 |     ".mts": Checker("node", ("--no-warnings", "-e", TS_CHECK, "{file}")),
145 |     ".go": Checker("gofmt", ("-e", "-l", "{file}")),
146 |     ".rs": Checker("rustfmt", ("--edition", "2021", "--emit", "stdout", "{file}")),
147 |     ".c": Checker("gcc", ("-fsyntax-only", "{file}")),
148 |     ".h": Checker("gcc", ("-fsyntax-only", "{file}")),
149 |     ".cpp": Checker("g++", ("-fsyntax-only", "{file}")),
150 |     ".cc": Checker("g++", ("-fsyntax-only", "{file}")),
151 |     ".hpp": Checker("g++", ("-fsyntax-only", "{file}")),
152 |     ".php": Checker("php", ("-l", "{file}")),
153 |     ".rb": Checker("ruby", ("-c", "{file}")),
154 |     ".sh": Checker("bash", ("-n", "{file}"), windows=False),   # Windows "bash" may be WSL's
155 |     ".ps1": Checker("powershell", ("-NoProfile", "-NonInteractive", "-Command", PS_CHECK),
156 |                     windows=True),
157 | }
158 | 
159 | 
```
- A `Checker` names the program that must be installed, its arguments (`{file}` is replaced by the file's path) and,
  optionally, a platform rule (`windows=True` only on Windows, `False` never on Windows).
- `CHECKERS` maps each file extension to its checker — see the table at the top. Adding a language is one line here.

```python
160 | def _external_stage(checker: Checker, path: Path, cwd: Path) -> list[str]:
161 |     """Run a language's own syntax checker; skipped when the program is not installed."""
162 |     on_windows = os.name == "nt"
163 |     if checker.windows is not None and checker.windows != on_windows:
164 |         return []
165 |     program = shutil.which(checker.program)
166 |     if program is None:
167 |         return []
168 |     quoted = str(path).replace("'", "''")         # for PowerShell's single-quoted string
169 |     argv = [program] + [a.replace("{file}", str(path)).replace("{path}", quoted)
170 |                         for a in checker.args]
171 |     code, output = run_command(argv, cwd, timeout=60, limit=3000)
172 |     if code == 0:
173 |         return []
174 |     if "No such file or directory" in output and output.count("error") <= 1:
175 |         return []                                 # a missing outside header: cannot check
176 |     return [output.strip() or f"{path.name}: {checker.program} reported a problem"]
177 | 
178 | 
```
- Skips the check when the platform rule excludes this system or the program is not installed (`shutil.which`).
- Builds the command (for PowerShell the path's single quotes are doubled, as its quoting rules require), runs it with a
  60-second limit, and treats exit code 0 as success.
- **Missing outside headers:** if the only problem is a C/C++ `#include` file that does not exist ("No such file or
  directory"), the file uses a library that is not installed here and cannot be checked — that is reported as a pass,
  not as broken code. (gcc stops at the first such error, so this check simply cannot help for those files.)
- Otherwise the checker's own error text goes back to the model.

```python
179 | CHECKED_SUFFIXES = frozenset({".py", ".json", ".toml", ".yaml", ".yml", *CHECKERS})
180 | 
181 | 
```
- `CHECKED_SUFFIXES`: Python, JSON, TOML, YAML and every extension in `CHECKERS`. `tools.py` uses it to decide which writes
  go through the verify/rollback gate and which files the workspace check looks at.

## Entry points
```python
182 | def verify_file(path: Path, cwd: Path, type_check: bool = False) -> Report:
183 |     """Verify any supported file by its extension; other files always pass."""
184 |     suffix = path.suffix.lower()
185 |     if suffix == ".py":
186 |         return verify_python(path, cwd, type_check)
187 |     if suffix == ".json":
188 |         problems = _json_stage(path)
189 |     elif suffix == ".toml":
190 |         problems = _toml_stage(path)
191 |     elif suffix in (".yaml", ".yml"):
192 |         problems = _yaml_stage(path)
193 |     elif suffix in CHECKERS:
194 |         problems = _external_stage(CHECKERS[suffix], path, cwd)
195 |     else:
196 |         problems = []
197 |     return Report(not problems, tuple(problems))
198 | 
199 | 
```
- Chooses the check by extension (lower-cased, so `.JSON` works too): Python goes to `verify_python`; JSON, TOML and
  YAML to their stage; extensions in `CHECKERS` to `_external_stage`; anything else passes.

```python
200 | def verify_python(path: Path, cwd: Path, type_check: bool = False) -> Report:
201 |     """Verify one Python file on disk and return a Report (never raises)."""
202 |     problems = _compile_stage(path)
203 |     if problems:                      # unparsable code: stop here
204 |         return Report(False, tuple(problems))
205 |     problems = _ruff_stage(path, cwd)
206 |     if problems:
207 |         return Report(False, tuple(problems))
208 |     if type_check:
209 |         problems = _mypy_stage(path, cwd)
210 |         if problems:
211 |             return Report(False, tuple(problems))
212 |     return Report(True)
```
- Compile first; if it fails, return immediately. Then ruff; then mypy if requested. Everything passed → `Report(True)`.
