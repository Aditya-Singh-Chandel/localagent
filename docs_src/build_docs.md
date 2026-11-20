# `tools/build_docs.py` — keeps these docs honest

**Role:** you asked for documentation of every line. Hand-written docs rot, so this script ties them to the code. Doc
sources live in `docs_src/` and contain explanations plus marker lines; the script pastes the *real* code into `docs/`
and refuses to finish if any line of any project `.py` file is undocumented or documented twice. Markers refer to
code by **symbol name** (`Config.from_env`), so editing code does not break the docs — it just changes the pasted text
and tells you which new block needs an explanation.

Run after any code change: `python tools/build_docs.py` (`--check` in CI; `--list FILE` shows block names).

## Module header
@@ tools/build_docs.py header @@
- Imports `ast` (Python's own parser, used to find where each function/class starts and ends), `re`, `sys`, `Path`.
- `ROOT` is the project folder. `MARKER` matches a whole line `@@ file block @@`; `RANGE` matches `7-13` style
  specs. `CODE_GLOBS` lists which files must be fully documented. `DEFS` names the statement types that start a block.

## `read_lines`
@@ tools/build_docs.py read_lines @@
- Reads a project file and splits it into lines (no newline characters).

## `first_line`
@@ tools/build_docs.py first_line @@
- A decorated function's `lineno` points at `def`, not at the `@decorator` above it; this returns the earlier line so
  decorators belong to the right block.

## `blocks`
@@ tools/build_docs.py blocks @@
- Parses the file and records a start line for every top-level function/class, every method inside a class
  (`Class.method`), code *between* definitions (`after_<name of the definition before it>`, e.g. a table of
  constants), and code after the last definition (`footer`, e.g. the `if __name__` guard).
- `header` is everything before the first definition. Each block runs from its start to the line before the next
  start, so blank lines and comments attach to the block above them and every line belongs to exactly one block.
- Duplicate names are rejected with a hint to use a line range.

## `resolve`
@@ tools/build_docs.py resolve @@
- A numeric spec returns that range directly; a symbol is looked up in `blocks`. An unknown symbol raises an error
  listing the valid names.

## `expand`
@@ tools/build_docs.py expand @@
- Copies ordinary lines unchanged. For each marker it loads the file, resolves the range, validates it, records the
  covered line numbers, and emits a fenced code block with right-aligned line numbers (`  12 | code`).

## `coverage_problems`
@@ tools/build_docs.py coverage_problems @@
- For each project Python file compares the lines that *should* be documented (all of them) with those the markers
  covered. On a mismatch it reports missing lines, duplicated lines, and which blocks still need a marker.

## `main`
@@ tools/build_docs.py main @@
- `--list FILE` prints block names and exits. Otherwise every page in `docs_src/` is expanded in memory and coverage is
  checked. With `--check`, pages whose saved output differs are reported as stale; otherwise, if there are no problems,
  `docs/` is written. Problems are printed; the exit code is 1 if there were any.

## Entry point
@@ tools/build_docs.py footer @@
- Runs `main` only when executed as a script, passing command-line arguments, and exits with its return code.
