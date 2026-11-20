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
 17 | import re
 18 | import shutil
 19 | import sys
 20 | from dataclasses import dataclass
 21 | from pathlib import Path
 22 | 
 23 | from .sandbox import run_command
 24 | 
 25 | # Only rules that mean "this will crash": syntax errors, undefined names, etc.
 26 | RUFF_RULES = "E9,F63,F7,F82"
 27 | RUFF_FINDING = re.compile(r":\d+:\d+: [A-Z]+\d+ ")   # one finding: "file:line:col: CODE message"
 28 | 
 29 | 
```
- The docstring lists the layers. `importlib.util` tests whether a package exists; `json` parses JSON; `os` tells
  Windows from other systems; `re` recognises JSONC file names and trailing commas; `shutil.which` finds a language's
  program; `sys.executable` is this Python; `run_command` launches ruff, mypy and node.
- `RUFF_RULES`: `E9` syntax errors, `F63` invalid comparisons/asserts, `F7` misplaced statements, `F82` undefined
  names. Style rules are deliberately left out so the model is not nagged. `RUFF_FINDING` recognises one finding line
  of ruff's concise output (`file:line:column: CODE message`).

## `Report`
```python
 30 | @dataclass(frozen=True)
 31 | class Report:
 32 |     """Outcome of verifying one file."""
 33 | 
 34 |     ok: bool
 35 |     problems: tuple[str, ...] = ()
 36 | 
```
```python
 37 |     def render(self) -> str:
 38 |         """Text shown to the model (and the user)."""
 39 |         return "all checks passed" if self.ok else "\n".join(self.problems)
 40 | 
 41 | 
```
- `ok` (did it pass) and `problems` (tuple of error strings). `render` gives "all checks passed" or the problems joined
  by newlines — the text the model sees.

## Python layers
```python
 42 | def _installed(module: str) -> bool:
 43 |     """True when ``python -m <module>`` would work in this interpreter."""
 44 |     return importlib.util.find_spec(module) is not None
 45 | 
 46 | 
```
- `find_spec` returns `None` when the module is not installed: a cheap yes/no test.

```python
 47 | def _compile_stage(path: Path) -> list[str]:
 48 |     """Compile the file without running it; report syntax errors with line numbers.
 49 | 
 50 |     The raw bytes are compiled, so Python decodes them exactly as when the file runs: a
 51 |     byte-order mark (PowerShell writes one) and a ``# coding:`` line are understood.
 52 |     """
 53 |     try:
 54 |         compile(path.read_bytes(), str(path.name), "exec", dont_inherit=True)
 55 |     except SyntaxError as exc:
 56 |         return [f"{path.name}:{exc.lineno}:{exc.offset}: SyntaxError: {exc.msg}"]
 57 |     except (ValueError, UnicodeDecodeError) as exc:
 58 |         return [f"{path.name}: cannot compile: {exc}"]
 59 |     return []
 60 | 
 61 | 
```
- Calls the built-in `compile(...)` in `"exec"` mode — it *parses and analyses* but does **not run** anything.
  `dont_inherit=True` stops our own `__future__` flags leaking into the check. A `SyntaxError` becomes
  `file:line:column: SyntaxError: message`, the style editors use; other failures (null bytes) become a readable
  message.
- It compiles the file's **raw bytes**, so Python decodes them exactly as it does when the file runs: a leading
  byte-order mark and a `# -*- coding: latin-1 -*-` line are understood. Found in a live offline test (20 Nov 2026): a
  `calc.py` written by PowerShell 5.1 (which adds a byte-order mark) was read as text with the mark kept, and
  `compile` called the mark "invalid non-printable character U+FEFF" — so `run_python` was refused, the workspace check
  failed, and the local model spent 9 calls and 191 s trying to "fix" a file that Python runs perfectly well. After the
  fix the same request took 4 calls and 88 s.

```python
 62 | def _ruff_stage(path: Path, cwd: Path) -> list[str]:
 63 |     """Run ruff for hard errors; skipped (no problems) when ruff is not installed."""
 64 |     if not _installed("ruff"):
 65 |         return []
 66 |     code, output = run_command(
 67 |         [sys.executable, "-m", "ruff", "check", "--select", RUFF_RULES,
 68 |          "--output-format", "concise", "--no-cache", str(path)],
 69 |         cwd, timeout=60, limit=3000,
 70 |     )
 71 |     if code == 0:
 72 |         return []
 73 |     findings = [line for line in output.splitlines() if RUFF_FINDING.search(line)]
 74 |     if findings and all(" E902 " in line for line in findings):
 75 |         return []          # ruff could not read the file (an older encoding); compile() could
 76 |     return [output.strip()]
 77 | 
 78 | 
```
- Skipped when ruff is not installed. Runs `python -m ruff check` with the chosen rules, concise output and no cache
  files; `sys.executable` guarantees the same Python as the agent. Exit code 0 = clean; otherwise ruff's output is the
  problem text — unless every finding is `E902`: that is ruff saying it could not *read* the file (ruff reads only
  UTF-8, so a legacy `latin-1` file it cannot open), not a mistake in the code, which `compile` has already accepted.

```python
 79 | def _mypy_stage(path: Path, cwd: Path) -> list[str]:
 80 |     """Run mypy for type errors; skipped when mypy is not installed."""
 81 |     if not _installed("mypy"):
 82 |         return []
 83 |     code, output = run_command(
 84 |         [sys.executable, "-m", "mypy", "--ignore-missing-imports",
 85 |          "--no-error-summary", "--no-color-output", str(path)],
 86 |         cwd, timeout=120, limit=3000,
 87 |     )
 88 |     return [] if code == 0 else [output.strip()]
 89 | 
 90 | 
 91 | # Files that are "JSON with comments" (JSONC): their tools accept // and /* */ comments and
 92 | # trailing commas, so rejecting them as broken JSON would be a false alarm.
```
- Same pattern. `--ignore-missing-imports` avoids noise about third-party packages without types; the other flags keep
  the output short and plain for the model.

## Other languages
**JSON with comments.** Several popular files are "JSON with comments" (JSONC): `tsconfig.json` and `jsconfig.json`,
every file in `.vscode/` and `.devcontainer/`, `.eslintrc.json`, `deno.json` and `*.jsonc`. Their tools accept `//` and
`/* */` comments and a comma before a closing bracket. Before 0.6 the agent judged them as strict JSON, so writing a
perfectly valid `tsconfig.json` was **rejected and rolled back** — the model could not configure a TypeScript project.
Ordinary `.json` files (e.g. `package.json`) stay strict, because their tools reject comments too.

```python
 93 | JSONC_NAMES = re.compile(r"^(tsconfig|jsconfig)[\w.-]*\.json$|^(\.?devcontainer|deno|tslint|"
 94 |                          r"\.eslintrc|\.babelrc)\.json$", re.IGNORECASE)
 95 | JSONC_FOLDERS = frozenset({".vscode", ".devcontainer"})
 96 | 
 97 | 
```
- `JSONC_NAMES` matches the JSONC file names above; `JSONC_FOLDERS` are the folders whose JSON files are all JSONC.

```python
 98 | def is_jsonc(path: Path) -> bool:
 99 |     """True for files whose format allows comments: ``*.jsonc``, tsconfig.json, VS Code settings."""
100 |     return (path.suffix.lower() == ".jsonc" or bool(JSONC_NAMES.match(path.name))
101 |             or path.parent.name in JSONC_FOLDERS)
102 | 
103 | 
```
- True for a file whose format allows comments: by extension, by name, or by folder.

```python
104 | def strip_jsonc(text: str) -> str:
105 |     """Plain JSON from JSONC: comments become spaces (line breaks kept, so error positions
106 |     still match the file) and commas before a closing ``}`` or ``]`` are dropped."""
107 |     out: list[str] = []
108 |     i, quoted = 0, False
109 |     while i < len(text):
110 |         char = text[i]
111 |         if quoted:                                 # inside "...": copy, honouring \" escapes
112 |             out.append(text[i:i + 2] if char == "\\" else char)
113 |             quoted = char != '"'
114 |             i += 2 if char == "\\" else 1
115 |         elif char == '"':
116 |             quoted = True
117 |             out.append(char)
118 |             i += 1
119 |         elif text.startswith(("//", "/*"), i):
120 |             line_comment = text.startswith("//", i)
121 |             end = text.find("\n" if line_comment else "*/", i + 2)
122 |             end = len(text) if end == -1 else end + (0 if line_comment else 2)
123 |             out.append(re.sub(r"[^\n]", " ", text[i:end]))
124 |             i = end
125 |         elif char == "," and re.match(r"\s*[}\]]", _rest_without_comments(text, i + 1)):
126 |             out.append(" ")                        # a trailing comma
127 |             i += 1
128 |         else:
129 |             out.append(char)
130 |             i += 1
131 |     return "".join(out)
132 | 
133 | 
```
- Turns JSONC into plain JSON in one pass over the characters. Inside a string (`quoted`) everything is copied — an
  escaped character together with its backslash, so `\"` does not end the string — which keeps a URL such as
  `"http://x//y"` intact. Outside strings, `//` comments up to the end of the line and `/* */` comments become spaces
  (line breaks stay, so the line and column of a remaining error still point into your file), and a comma whose next
  real character is `}` or `]` becomes a space.

```python
134 | def _rest_without_comments(text: str, start: int) -> str:
135 |     """The text after ``start`` with leading whitespace and comments skipped (for comma checks)."""
136 |     i = start
137 |     while i < len(text):
138 |         if text[i].isspace():
139 |             i += 1
140 |         elif text.startswith("//", i):
141 |             end = text.find("\n", i)
142 |             i = len(text) if end == -1 else end
143 |         elif text.startswith("/*", i):
144 |             end = text.find("*/", i + 2)
145 |             i = len(text) if end == -1 else end + 2
146 |         else:
147 |             break
148 |     return text[i:i + 1]
149 | 
150 | 
```
- The next real character after a position, skipping spaces and comments — used to recognise a trailing comma even
  when a comment sits between it and the bracket.

```python
151 | def _read_checked_text(path: Path) -> str:
152 |     """A config file's text; a leading byte-order mark (common from Windows editors) is fine."""
153 |     return path.read_text(encoding="utf-8-sig")
154 | 
155 | 
```
- Reads a config file as UTF-8 and drops a leading byte-order mark. Windows tools (for example PowerShell 5.1's
  `Out-File -Encoding utf8`) add one; `json.loads` rejected such files although every tool that reads them accepts them.

```python
156 | def _json_stage(path: Path) -> list[str]:
157 |     """JSON must parse (JSONC files may have comments); report the first error's position."""
158 |     try:
159 |         text = _read_checked_text(path)
160 |     except UnicodeDecodeError as exc:
161 |         return [f"{path.name}: not valid UTF-8 text: {exc}"]
162 |     try:
163 |         json.loads(strip_jsonc(text) if is_jsonc(path) else text)
164 |     except json.JSONDecodeError as exc:
165 |         return [f"{path.name}:{exc.lineno}:{exc.colno}: invalid JSON: {exc.msg}"]
166 |     return []
167 | 
168 | 
```
- `json.loads` (after `strip_jsonc` for JSONC files) either succeeds or raises `JSONDecodeError`, which carries the
  exact line and column; a file that is not UTF-8 text is reported too.

```python
169 | def _toml_stage(path: Path) -> list[str]:
170 |     """TOML (e.g. pyproject.toml) must parse; needs Python 3.11+ (skipped otherwise)."""
171 |     try:
172 |         import tomllib
173 |     except ImportError:
174 |         return []
175 |     try:
176 |         tomllib.loads(_read_checked_text(path))
177 |     except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
178 |         return [f"{path.name}: invalid TOML: {exc}"]
179 |     return []
180 | 
181 | 
```
- `tomllib` is in the standard library from Python 3.11; on older Pythons the stage is skipped instead of failing.

```python
182 | def _yaml_stage(path: Path) -> list[str]:
183 |     """YAML must parse when PyYAML is installed (skipped otherwise)."""
184 |     try:
185 |         import yaml  # type: ignore[import-untyped]
186 |     except ImportError:
187 |         return []
188 |     try:
189 |         list(yaml.safe_load_all(_read_checked_text(path)))
190 |     except (yaml.YAMLError, UnicodeDecodeError) as exc:
191 |         return [f"{path.name}: invalid YAML: {exc}"]
192 |     return []
193 | 
194 | 
195 | # TypeScript: Node.js (22.13+) can remove type annotations itself; a syntax error makes it fail
196 | # with the line and a caret. "transform" mode also accepts enums and namespaces. Older Node
197 | # versions lack the function, and then the check is skipped (exit 0) instead of failing.
```
- YAML (`docker-compose.yml`, CI files ...) is parsed with PyYAML when it is installed (`safe_load_all` handles files with
  several `---` documents); without PyYAML the check is skipped.

```python
198 | TS_CHECK = ("const m = require('node:module');"
199 |             "if (typeof m.stripTypeScriptTypes !== 'function') process.exit(0);"
200 |             "try { m.stripTypeScriptTypes(require('fs').readFileSync(process.argv[1], 'utf8'),"
201 |             " {mode: 'transform'}); }"
202 |             "catch (e) { console.error(e.message); process.exit(1); }")
203 | PS_CHECK = ("$errors = $null; [void][System.Management.Automation.Language.Parser]::ParseFile("
204 |             "'{path}', [ref]$null, [ref]$errors); if ($errors) { $errors | ForEach-Object { "
205 |             "\"line $($_.Extent.StartLineNumber): $($_.Message)\" }; exit 1 }")
206 | 
207 | 
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
208 | @dataclass(frozen=True)
209 | class Checker:
210 |     """An outside program that can check one language's syntax without running the code."""
211 | 
212 |     program: str                                # must be installed (found on PATH)
213 |     args: tuple[str, ...]                       # "{file}" is replaced by the file's path
214 |     windows: bool | None = None                 # True: Windows only; False: never on Windows
215 |     stdin: bool = False                         # the file's text is sent as input instead
216 |     alternative: "Checker | None" = None        # the file also passes if THIS check passes
217 | 
218 | 
```
```python
219 | RUST_CHECK = Checker("rustfmt", ("--edition", "2021", "--emit", "stdout"), stdin=True)
220 | CPP_CHECK = Checker("g++", ("-fsyntax-only", "{file}"))
221 | CHECKERS: dict[str, Checker] = {
222 |     ".js": Checker("node", ("--check", "{file}")),
223 |     ".mjs": Checker("node", ("--check", "{file}")),
224 |     ".cjs": Checker("node", ("--check", "{file}")),
225 |     ".ts": Checker("node", ("--no-warnings", "-e", TS_CHECK, "{file}")),
226 |     ".mts": Checker("node", ("--no-warnings", "-e", TS_CHECK, "{file}")),
227 |     ".go": Checker("gofmt", ("-e", "-l", "{file}")),
228 |     ".rs": RUST_CHECK,          # via input: rustfmt then ignores `mod x;` files not written yet
229 |     ".c": Checker("gcc", ("-fsyntax-only", "{file}")),
230 |     ".h": Checker("gcc", ("-fsyntax-only", "{file}"), alternative=CPP_CHECK),   # C or C++
231 |     ".cpp": CPP_CHECK,
232 |     ".cc": CPP_CHECK,
233 |     ".hpp": CPP_CHECK,
234 |     ".php": Checker("php", ("-l", "{file}")),
235 |     ".rb": Checker("ruby", ("-c", "{file}")),
236 |     ".sh": Checker("bash", ("-n", "{file}"), windows=False),   # Windows "bash" may be WSL's
237 |     ".ps1": Checker("powershell", ("-NoProfile", "-NonInteractive", "-Command", PS_CHECK),
238 |                     windows=True),
239 | }
240 | 
241 | 
```
- A `Checker` names the program that must be installed, its arguments (`{file}` is replaced by the file's path) and,
  optionally, a platform rule (`windows=True` only on Windows, `False` never on Windows), `stdin` (the file's text is
  sent as the program's input instead of naming the file) and an `alternative` check that also makes the file pass.
- `CHECKERS` maps each file extension to its checker — see the table at the top. Adding a language is one line here.
- **Rust** (`RUST_CHECK`) is checked through its input. Given a *file*, `rustfmt` also opens every module the file
  declares (`mod util;` → `util.rs`) and fails when it does not exist yet — so a model writing `main.rs` before
  `util.rs` was rejected although `main.rs` was fine. Through its input rustfmt checks only that text (verified: a
  valid `main.rs` with a missing module passes, a broken one still fails).
- **`.h` headers** are C *or* C++: C++ projects very often use `.h` too, and checking them as C rejected every
  `class` and `namespace`. A header now passes when either `gcc` (C) or `g++` (C++, `CPP_CHECK`) accepts it.

```python
242 | def _external_stage(checker: Checker, path: Path, cwd: Path) -> list[str]:
243 |     """Run a language's own syntax checker; skipped when the program is not installed."""
244 |     on_windows = os.name == "nt"
245 |     if checker.windows is not None and checker.windows != on_windows:
246 |         return []
247 |     program = shutil.which(checker.program)
248 |     if program is None:
249 |         return []
250 |     quoted = str(path).replace("'", "''")         # for PowerShell's single-quoted string
251 |     argv = [program] + [a.replace("{file}", str(path)).replace("{path}", quoted)
252 |                         for a in checker.args]
253 |     text = path.read_text(encoding="utf-8", errors="replace") if checker.stdin else None
254 |     code, output = run_command(argv, cwd, timeout=60, limit=3000, input_text=text)
255 |     if code == 0:
256 |         return []
257 |     if "No such file or directory" in output and output.count("error") <= 1:
258 |         return []                                 # a missing outside header: cannot check
259 |     problem = output.replace("<stdin>", path.name).strip() or \
260 |         f"{path.name}: {checker.program} reported a problem"
261 |     if checker.alternative is not None:
262 |         second = _external_stage(checker.alternative, path, cwd)
263 |         if not second:
264 |             return []                             # valid as the other language (a C++ header)
265 |         return [f"{checker.program}:\n{problem}\n{checker.alternative.program}:\n{second[0]}"]
266 |     return [problem]
267 | 
268 | 
```
- Skips the check when the platform rule excludes this system or the program is not installed (`shutil.which`).
- Builds the command (for PowerShell the path's single quotes are doubled, as its quoting rules require), runs it with a
  60-second limit — feeding the file's text as input for `stdin` checkers — and treats exit code 0 as success.
- **Missing outside headers:** if the only problem is a C/C++ `#include` file that does not exist ("No such file or
  directory"), the file uses a library that is not installed here and cannot be checked — that is reported as a pass,
  not as broken code. (gcc stops at the first such error, so this check simply cannot help for those files.)
- Otherwise the checker's own error text goes back to the model (with `<stdin>` replaced by the file's name). When the
  checker has an alternative, that one is tried; if it passes, so does the file, and if both fail the model sees both
  programs' errors, each under the program's name.

```python
269 | CHECKED_SUFFIXES = frozenset({".py", ".json", ".jsonc", ".toml", ".yaml", ".yml", *CHECKERS})
270 | 
271 | 
```
- `CHECKED_SUFFIXES`: Python, JSON, JSONC, TOML, YAML and every extension in `CHECKERS`. `tools.py` uses it to decide which
  writes go through the verify/rollback gate and which files the workspace check looks at.

## Entry points
```python
272 | def verify_file(path: Path, cwd: Path, type_check: bool = False) -> Report:
273 |     """Verify any supported file by its extension; other files always pass."""
274 |     suffix = path.suffix.lower()
275 |     if suffix == ".py":
276 |         return verify_python(path, cwd, type_check)
277 |     if suffix in (".json", ".jsonc"):
278 |         problems = _json_stage(path)
279 |     elif suffix == ".toml":
280 |         problems = _toml_stage(path)
281 |     elif suffix in (".yaml", ".yml"):
282 |         problems = _yaml_stage(path)
283 |     elif suffix in CHECKERS:
284 |         problems = _external_stage(CHECKERS[suffix], path, cwd)
285 |     else:
286 |         problems = []
287 |     return Report(not problems, tuple(problems))
288 | 
289 | 
```
- Chooses the check by extension (lower-cased, so `.JSON` works too): Python goes to `verify_python`; JSON/JSONC, TOML
  and YAML to their stage; extensions in `CHECKERS` to `_external_stage`; anything else passes.

```python
290 | def verify_python(path: Path, cwd: Path, type_check: bool = False) -> Report:
291 |     """Verify one Python file on disk and return a Report (never raises)."""
292 |     problems = _compile_stage(path)
293 |     if problems:                      # unparsable code: stop here
294 |         return Report(False, tuple(problems))
295 |     problems = _ruff_stage(path, cwd)
296 |     if problems:
297 |         return Report(False, tuple(problems))
298 |     if type_check:
299 |         problems = _mypy_stage(path, cwd)
300 |         if problems:
301 |             return Report(False, tuple(problems))
302 |     return Report(True)
```
- Compile first; if it fails, return immediately. Then ruff; then mypy if requested. Everything passed → `Report(True)`.
