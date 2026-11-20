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
@@ localagent/verify.py header @@
- The docstring lists the layers. `importlib.util` tests whether a package exists; `json` parses JSON; `os` tells
  Windows from other systems; `shutil.which` finds a language's program; `sys.executable` is this Python; `run_command` launches ruff, mypy and node.
- `RUFF_RULES`: `E9` syntax errors, `F63` invalid comparisons/asserts, `F7` misplaced statements, `F82` undefined
  names. Style rules are deliberately left out so the model is not nagged.

## `Report`
@@ localagent/verify.py Report @@
@@ localagent/verify.py Report.render @@
- `ok` (did it pass) and `problems` (tuple of error strings). `render` gives "all checks passed" or the problems joined
  by newlines — the text the model sees.

## Python layers
@@ localagent/verify.py _installed @@
- `find_spec` returns `None` when the module is not installed: a cheap yes/no test.

@@ localagent/verify.py _compile_stage @@
- Reads the file and calls the built-in `compile(...)` in `"exec"` mode — it *parses and analyses* but does **not run**
  anything. `dont_inherit=True` stops our own `__future__` flags leaking into the check. A `SyntaxError` becomes
  `file:line:column: SyntaxError: message`, the style editors use; other failures (null bytes, bad encoding) become a
  readable message.

@@ localagent/verify.py _ruff_stage @@
- Skipped when ruff is not installed. Runs `python -m ruff check` with the chosen rules, concise output and no cache
  files; `sys.executable` guarantees the same Python as the agent. Exit code 0 = clean; otherwise ruff's output is the
  problem text.

@@ localagent/verify.py _mypy_stage @@
- Same pattern. `--ignore-missing-imports` avoids noise about third-party packages without types; the other flags keep
  the output short and plain for the model.

## Other languages
@@ localagent/verify.py _json_stage @@
- `json.loads` either succeeds or raises `JSONDecodeError`, which carries the exact line and column; a file that is not
  UTF-8 text is reported too.

@@ localagent/verify.py _toml_stage @@
- `tomllib` is in the standard library from Python 3.11; on older Pythons the stage is skipped instead of failing.

@@ localagent/verify.py _yaml_stage @@
- YAML (`docker-compose.yml`, CI files ...) is parsed with PyYAML when it is installed (`safe_load_all` handles files with
  several `---` documents); without PyYAML the check is skipped.

@@ localagent/verify.py after__yaml_stage @@
- `TS_CHECK` is a tiny JavaScript program run by Node: it asks Node's built-in `stripTypeScriptTypes` (Node 22.13+) to
  remove the type annotations from the file in `transform` mode (which also understands `enum` and `namespace`). A
  syntax error makes it print Node's message — with the line and a caret under the problem — and exit 1. If the Node
  version lacks the function, it exits 0, so the check is skipped instead of wrongly failing. (`node --check` alone
  cannot be used: it parses plain JavaScript and rejects every type annotation.)
- `PS_CHECK` is a PowerShell command that runs PowerShell's own parser on the file and prints each error as
  `line N: message`. (`{path}` is filled in with the file name.)

## The language table
@@ localagent/verify.py Checker @@
@@ localagent/verify.py after_Checker @@
- A `Checker` names the program that must be installed, its arguments (`{file}` is replaced by the file's path) and,
  optionally, a platform rule (`windows=True` only on Windows, `False` never on Windows).
- `CHECKERS` maps each file extension to its checker — see the table at the top. Adding a language is one line here.

@@ localagent/verify.py _external_stage @@
- Skips the check when the platform rule excludes this system or the program is not installed (`shutil.which`).
- Builds the command (for PowerShell the path's single quotes are doubled, as its quoting rules require), runs it with a
  60-second limit, and treats exit code 0 as success.
- **Missing outside headers:** if the only problem is a C/C++ `#include` file that does not exist ("No such file or
  directory"), the file uses a library that is not installed here and cannot be checked — that is reported as a pass,
  not as broken code. (gcc stops at the first such error, so this check simply cannot help for those files.)
- Otherwise the checker's own error text goes back to the model.

@@ localagent/verify.py after__external_stage @@
- `CHECKED_SUFFIXES`: Python, JSON, TOML, YAML and every extension in `CHECKERS`. `tools.py` uses it to decide which writes
  go through the verify/rollback gate and which files the workspace check looks at.

## Entry points
@@ localagent/verify.py verify_file @@
- Chooses the check by extension (lower-cased, so `.JSON` works too): Python goes to `verify_python`; JSON, TOML and
  YAML to their stage; extensions in `CHECKERS` to `_external_stage`; anything else passes.

@@ localagent/verify.py verify_python @@
- Compile first; if it fails, return immediately. Then ruff; then mypy if requested. Everything passed → `Report(True)`.
