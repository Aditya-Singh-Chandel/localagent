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
  4 | JSON, TOML and JavaScript are checked; see verify.py).
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
 26 | from .sandbox import (SKIP_DIRS, SandboxError, resolve_inside, run_command, shell_argv, truncate,
 27 |                       walk_files)
 28 | from .settings import PROJECT_SETTINGS, Permissions
 29 | from .verify import CHECKED_SUFFIXES, _installed, verify_file, verify_python
 30 | from .web import fetch_text
 31 | 
 32 | __all__ = ["SKIP_DIRS", "READ_ONLY", "Approval", "Tool", "ToolBox"]   # SKIP_DIRS re-exported
 33 | MAX_SEARCH_HITS = 40
 34 | MAX_SEARCH_FILES = 20_000
 35 | TOGETHER_HINT = ("Hint: if this name is defined by another edit you have not made yet (for example "
 36 |                  "a rename), send ALL related edits together in ONE multi_edit call, or use "
 37 |                  "edit_file with all=true.")
 38 | READ_ONLY = frozenset({"list_files", "read_file", "search", "outline"})
 39 | 
 40 | 
 41 | DONE_MARKS = ("(done)", "[done]", "(completed)", "- done", "✓", "✔")
 42 | 
 43 | 
```
- Docstring states the core rule. Imports: `ast` (parse Python for `outline`), `difflib` (edit diffs for your
  screen), `json` (MCP arguments), `os` (Windows or not, for hooks), `time` (session start), `fnmatch` (file-name patterns for
  `search`), `re` (regular expressions for `search`), `sys` (the current Python's path,
  `sys.executable`), `dataclass`, `Path`, `Callable`, the `Config`, sandbox helpers, and `verify_python`.
- `SKIP_DIRS` and `walk_files` now live in `sandbox.py` (one shared definition); `__all__` re-exports `SKIP_DIRS` so
  older imports keep working. `verify_file`/`CHECKED_SUFFIXES` come from `verify.py`.
  `MAX_SEARCH_HITS`: a search stops after 40 matches to protect the context window; `MAX_SEARCH_FILES` stops it after
  20 000 files (with a hint to narrow the search) so a search in a giant folder cannot hang. `TOGETHER_HINT` is added to a
  rejection caused by an undefined name (ruff `F821`): it tells the model to send related edits — such as both halves
  of a rename — together in one `multi_edit`. Added after the `rename_function` eval showed the model making the two
  edits one by one, so the first was (correctly) rejected as inconsistent.
- Also imported: the MCP client (`mcp.py`), the permission rules (`settings.py`) and the web reader (`web.py`).
- `DONE_MARKS`: text endings that mean "this checklist item is finished" (see `_todo_item`).
- `READ_ONLY`: the names of tools that never change anything (`list_files`, `read_file`, `search`, `outline`); the agent
  runs batches made only of these in parallel. `_installed` (from `verify.py`) tells whether ruff can be used.

## `_todo_item`
```python
 44 | def _todo_item(item: object) -> dict:
 45 |     """One checklist entry from ``{"text", "done"}`` or plain text; "... (done)" also counts as done."""
 46 |     text = str(item.get("text", "")) if isinstance(item, dict) else str(item)
 47 |     done = bool(item.get("done", False)) if isinstance(item, dict) else False
 48 |     for mark in DONE_MARKS:                                # models often write the state into the text
 49 |         if text.lower().rstrip().endswith(mark):
 50 |             text, done = text.rstrip()[: -len(mark)].rstrip(), True
 51 |     if text.lstrip().lower().startswith("[x]"):
 52 |         text, done = text.lstrip()[3:].lstrip(), True
 53 |     return {"text": text, "done": done}
 54 | 
 55 | 
```
- Turns one checklist entry into `{text, done}`. Accepts `{"text": ..., "done": ...}` or plain text. Real models often
  write the state into the text instead of setting `done` (seen in the end-to-end test: every item said "(done)" while
  the header said `0/4 done`), so endings in `DONE_MARKS` such as `(done)` or `✓`, and a leading `[x]`, also mark the
  item done and are removed from the text. `DONE_MARKS` is defined just above (part of the header block).

## `Approval`
```python
 56 | class Approval(str):
 57 |     """The question shown to the user ("run python a.py") that also carries, for permission
 58 |     rules and "always allow" answers, which tool asks and about what (command, file, URL)."""
 59 | 
 60 |     tool: str
 61 |     subject: str
 62 | 
```
```python
 63 |     def __new__(cls, text: str, tool: str, subject: str) -> "Approval":
 64 |         question = super().__new__(cls, text)
 65 |         question.tool = tool
 66 |         question.subject = subject
 67 |         return question
 68 | 
 69 | 
```
- The question shown to you ("run python main.py") is an `Approval`: a normal string (so every approval function that
  expects text keeps working, and `"main.py" in question` works) that **also** carries `tool` and `subject`. The CLI's
  "always" answer uses those two to save an exact permission rule. A `str` subclass must set its text in `__new__`
  (strings cannot change after creation), so the extra attributes are attached there.

## `Tool`
```python
 70 | @dataclass(frozen=True)
 71 | class Tool:
 72 |     """A callable plus the JSON schema the model sees."""
 73 | 
 74 |     name: str
 75 |     description: str
 76 |     parameters: dict
 77 |     func: Callable[[dict], str]
 78 | 
```
```python
 79 |     def spec(self) -> dict:
 80 |         """Ollama/OpenAI-style function definition."""
 81 |         return {"type": "function", "function": {
 82 |             "name": self.name, "description": self.description,
 83 |             "parameters": self.parameters}}
 84 | 
 85 | 
```
- A frozen record of: `name`, `description` (the model reads this to decide when to use the tool), `parameters` (a
  JSON schema describing the arguments), and `func`, the Python function that implements it.
- `spec()` builds the `{"type": "function", "function": {...}}` structure Ollama and OpenAI-style servers expect.

## `_schema`
```python
 86 | def _schema(properties: dict, required: list[str]) -> dict:
 87 |     """Build a JSON-schema object for tool parameters."""
 88 |     return {"type": "object", "properties": properties, "required": required}
 89 | 
 90 | 
```
- Builds a JSON-schema object: `properties` lists the arguments, `required` the mandatory ones. It removes
  boilerplate from the tool table.

## `ToolBox` setup
```python
 91 | class ToolBox:
 92 |     """Owns the workspace, the tool table and the per-file failure counters."""
 93 | 
```
```python
 94 |     def __init__(self, config: Config, approve: Callable[[str], bool] | None = None,
 95 |                  settings: dict | None = None) -> None:
 96 |         self._cfg = config
 97 |         config.workspace.mkdir(parents=True, exist_ok=True)
 98 |         self._root = config.workspace.resolve()
 99 |         self._approve = approve or (lambda _action: False)
100 |         self._failures: dict[str, int] = {}
101 |         self.writes = 0                           # successful file writes so far
102 |         self.started = time.time()                # files changed after this are "this session's"
103 |         self.runner = make_runner(config, self._root)   # where code runs: host or container
104 |         self.todo: list[dict] = []                # the model's visible task checklist
105 |         self.permissions = Permissions.from_settings(
106 |             settings or {}, self._root / PROJECT_SETTINGS if settings is not None else None)
107 |         self.notes: list[str] = []                # startup messages for the user (MCP servers)
108 |         self._diff: list[str] = []                # diff of the latest successful write
109 |         self._mcp: list[MCPServer] = []
110 |         self._tools = {t.name: t for t in self._build()}
111 |         self._start_mcp((settings or {}).get("mcpServers", {}))
112 |         self.hooks: dict = dict((settings or {}).get("hooks", {}))   # your own commands (see run_hook)
113 | 
114 |     # ----- public API used by the agent -------------------------------------
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

## Public API
```python
119 |     def close(self) -> None:
120 |         """Release resources: the Docker sandbox container and any MCP server programs."""
121 |         self.runner.stop()
122 |         for server in self._mcp:
123 |             server.close()
124 | 
```
```python
125 |     def pop_diff(self) -> list[str]:
126 |         """The diff of the latest write (for the user's screen only), then forget it."""
127 |         diff, self._diff = self._diff, []
128 |         return diff
129 | 
```
- `pop_diff` hands the last write's diff to the agent (for your screen) and forgets it.
- `close` releases resources: stops the sandbox container if one was started and every MCP server program. Called by the eval runner after each task and by
  the server when a session expires; the CLI relies on the exit-time clean-up.

```python
130 |     @staticmethod
131 |     def is_read_only(name: str) -> bool:
132 |         """True for tools that never change anything, so they may run in parallel."""
133 |         return name in READ_ONLY
134 | 
```
- Static helper: is this tool name in `READ_ONLY`? Unknown names are not, so they run sequentially.

```python
115 |     def specs(self) -> list[dict]:
116 |         """Tool definitions to send to the model."""
117 |         return [tool.spec() for tool in self._tools.values()]
118 | 
```
- The list of tool definitions sent to the model on every request.

```python
135 |     def check_workspace(self, since: float | None = None) -> list[str]:
136 |         """Verify checkable files (only those changed after ``since`` if given); list failures.
137 | 
138 |         Checking only changed files keeps this fast in big projects and never blames the
139 |         agent for files that were already broken before it started.
140 |         """
141 |         problems: list[str] = []
142 |         for path in walk_files(self._root):
143 |             if since is not None and path.stat().st_mtime < since - 1.0:   # 1 s clock slack
144 |                 continue
145 |             if path.suffix.lower() in CHECKED_SUFFIXES:
146 |                 report = verify_file(path, self._root, self._cfg.type_check)
147 |                 if not report.ok:
148 |                     problems.append(f"{path.relative_to(self._root).as_posix()}:\n{report.render()}")
149 |         return problems
150 | 
```
- Verifies every checkable file found by `walk_files` — or, with `since`, only files modified after that moment (one
  second of slack for clock rounding) — and returns one entry per failing file (path plus errors). With `since` it is
  fast in big projects and never blames the agent for files that were already broken before it started. An empty list means everything is fine. Used by `_run_tests` and by the CLI's end-of-request check.

```python
151 |     def call(self, name: str, args: dict) -> str:
152 |         """Run one tool by name; always returns a string."""
153 |         tool = self._tools.get(name)
154 |         if tool is None:
155 |             return f"ERROR: unknown tool '{name}'. Available: {', '.join(self._tools)}"
156 |         try:
157 |             return tool.func(args)
158 |         except SandboxError as exc:
159 |             return f"ERROR: {exc}"
160 |         except (KeyError, TypeError, ValueError) as exc:
161 |             return f"ERROR: bad arguments for {name}: {exc!r}"
162 |         except OSError as exc:
163 |             return f"ERROR: {exc}"
164 | 
165 |     # ----- tool table --------------------------------------------------------
```
- Looks the tool up; an unknown name (models sometimes invent tools) returns an error that lists the real ones.
- Runs the function and converts expected failures into strings: `SandboxError` (path escape) → `ERROR:`;
  `KeyError`/`TypeError`/`ValueError` (missing or wrong-typed argument) → "bad arguments"; `OSError` (permissions,
  disk) → `ERROR:`.

## The tool table
```python
166 |     def _build(self) -> list[Tool]:
167 |         return [
168 |             Tool("list_files", "List files and folders in a workspace folder.",
169 |                  _schema({"path": {"type": "string", "description": "folder, default '.'"}}, []),
170 |                  self._list_files),
171 |             Tool("read_file", "Read a file with line numbers. Use start/end to read part of it.",
172 |                  _schema({"path": {"type": "string"}, "start": {"type": "integer"},
173 |                           "end": {"type": "integer"}}, ["path"]),
174 |                  self._read_file),
175 |             Tool("search", "Find lines matching a regular expression in workspace files "
176 |                  "(like grep). Returns file:line: text. Much cheaper than reading files.",
177 |                  _schema({"pattern": {"type": "string"},
178 |                           "path": {"type": "string", "description": "folder, default '.'"},
179 |                           "glob": {"type": "string", "description": "file pattern, default '*.py'"}},
180 |                          ["pattern"]),
181 |                  self._search),
182 |             Tool("outline", "List the classes and functions (with line numbers) of one Python "
183 |                  "file, or of every Python file in a folder. Cheaper than reading whole files.",
184 |                  _schema({"path": {"type": "string", "description": "file or folder, default '.'"}},
185 |                          []),
186 |                  self._outline),
187 |             Tool("write_file", "Create or overwrite a file. Python files are verified first "
188 |                  "and rejected with the errors if they do not pass.",
189 |                  _schema({"path": {"type": "string"}, "content": {"type": "string"}},
190 |                          ["path", "content"]),
191 |                  self._write_file),
192 |             Tool("edit_file", "Replace one exact, unique piece of text in a file. To rename "
193 |                  "something that appears several times in the file, set all=true.",
194 |                  _schema({"path": {"type": "string"}, "old": {"type": "string"},
195 |                           "new": {"type": "string"},
196 |                           "all": {"type": "boolean", "description": "replace every occurrence"}},
197 |                          ["path", "old", "new"]),
198 |                  self._edit_file),
199 |             Tool("multi_edit", "Apply several exact, unique text replacements to ONE file in a "
200 |                  "single call (one verification). Each edit is {old, new}; they apply in order.",
201 |                  _schema({"path": {"type": "string"},
202 |                           "edits": {"type": "array", "items": {
203 |                               "type": "object",
204 |                               "properties": {"old": {"type": "string"}, "new": {"type": "string"}},
205 |                               "required": ["old", "new"]}}}, ["path", "edits"]),
206 |                  self._multi_edit),
207 |             Tool("run_python", "Verify then run a Python file. Refuses if verification fails.",
208 |                  _schema({"path": {"type": "string"},
209 |                           "args": {"type": "array", "items": {"type": "string"}}}, ["path"]),
210 |                  self._run_python),
211 |             Tool("run_tests", "Verify all Python files, then run unittest discovery.",
212 |                  _schema({}, []), self._run_tests),
213 |             Tool("todo", "Show the user your plan as a checklist. Send the whole list each "
214 |                  "time and set done=true on finished items. Use it for tasks with 3 or more steps.",
215 |                  _schema({"items": {"type": "array", "items": {
216 |                      "type": "object",
217 |                      "properties": {"text": {"type": "string"}, "done": {"type": "boolean"}},
218 |                      "required": ["text"]}}}, ["items"]),
219 |                  self._todo),
220 |             Tool("fetch_url", "Read a web page (for example library documentation) as plain "
221 |                  "text. The user is asked first.",
222 |                  _schema({"url": {"type": "string"}}, ["url"]), self._fetch_url),
223 |         ] + ([Tool("run_shell", f"Run a shell command ({self.runner.shell_label}), e.g. pip "
224 |                    "install, pytest, git, node. The workspace is the current folder.",
225 |                    _schema({"command": {"type": "string"}}, ["command"]), self._run_shell)]
226 |              if self.runner.supports_shell else [])
227 | 
228 |     # ----- read-only tools ---------------------------------------------------
```
- One `Tool(...)` per capability: `list_files`, `read_file` (optional `start`/`end` so the model can read only a
  slice), `search` (grep-like, far cheaper than reading files), `outline` (classes and functions with line numbers),
  `write_file`, `edit_file` (replace one unique snippet instead of rewriting a file), `multi_edit` (several such
  replacements in one call), `run_python`, `run_tests`, and — **only when the runner supports it (Docker)** —
  `run_shell`, plus `todo` (a checklist) and `fetch_url` (read a web page; always asked or ruled). The list is built as "base tools + optional shell tool", so the model
  never even sees a shell tool unless the runner offers one (Docker, or `--allow-shell` on this computer). The shell
  tool's description names where it runs (`shell_label`), e.g. "PowerShell on this Windows computer".
- The descriptions are part of the prompt, so they are written to tell the model *when* to use each tool.

## Read-only tools
```python
229 |     def _list_files(self, args: dict) -> str:
230 |         folder = resolve_inside(self._root, args.get("path", "."))
231 |         if not folder.is_dir():
232 |             return f"ERROR: '{args.get('path', '.')}' is not a folder"
233 |         rows = [p.name + ("/" if p.is_dir() else "")
234 |                 for p in sorted(folder.iterdir()) if p.name not in SKIP_DIRS]
235 |         return "\n".join(rows) or "(empty folder)"
236 | 
```
- Resolves the folder inside the sandbox; errors if it is not a folder; lists sorted names (folders get `/`), hiding
  `SKIP_DIRS`; says "(empty folder)" when appropriate.

```python
237 |     def _read_file(self, args: dict) -> str:
238 |         path = resolve_inside(self._root, args["path"])
239 |         if not path.is_file():
240 |             return f"ERROR: file '{args['path']}' does not exist"
241 |         lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
242 |         start = max(int(args.get("start", 1)), 1)
243 |         end = min(int(args.get("end", len(lines))), len(lines))
244 |         body = "\n".join(f"{n}: {lines[n - 1]}" for n in range(start, end + 1))
245 |         return truncate(body, self._cfg.max_output_chars) or "(empty file)"
246 | 
```
- Resolves and checks the file, reads it as text (undecodable bytes are replaced rather than crashing), clamps
  `start` (at least 1) and `end` (at most the file length), prefixes each line with its number so the model can quote
  exact locations, and truncates the result.

```python
247 |     def _search(self, args: dict) -> str:
248 |         try:
249 |             pattern = re.compile(args["pattern"])
250 |         except re.error as exc:
251 |             return f"ERROR: invalid regular expression: {exc}"
252 |         folder = resolve_inside(self._root, args.get("path", "."))
253 |         hits: list[str] = []
254 |         glob = args.get("glob", "*.py")
255 |         for scanned, path in enumerate(walk_files(folder), start=1):
256 |             if scanned > MAX_SEARCH_FILES:
257 |                 hits.append(f"... stopped after looking at {MAX_SEARCH_FILES} files; "
258 |                             "narrow 'path' or 'glob'")
259 |                 break
260 |             relative = path.relative_to(self._root)
261 |             if not fnmatch.fnmatch(path.name, glob):
262 |                 continue
263 |             try:
264 |                 lines = path.read_text(encoding="utf-8").splitlines()
265 |             except UnicodeDecodeError:
266 |                 continue                              # binary file: skip
267 |             for number, line in enumerate(lines, start=1):
268 |                 if pattern.search(line):
269 |                     hits.append(f"{relative.as_posix()}:{number}: {line.strip()[:120]}")
270 |                     if len(hits) >= MAX_SEARCH_HITS:
271 |                         return "\n".join(hits) + f"\n... stopped at {MAX_SEARCH_HITS} matches"
272 |         return "\n".join(hits) or "no matches"
273 | 
```
- Compiles the regular expression; an invalid one returns a readable error instead of crashing.
- Walks the chosen folder with `walk_files` (stopping after `MAX_SEARCH_FILES` files with a note) and keeps files whose
  *name* matches `glob` (default `*.py`, e.g.
  `*.js` for JavaScript).
- Binary files raise `UnicodeDecodeError` and are skipped. Each matching line is reported as `path:line: text`
  (stripped and cut to 120 characters). After 40 hits it stops and says so; no hits returns "no matches".

```python
274 |     def _outline(self, args: dict) -> str:
275 |         target = resolve_inside(self._root, args.get("path", "."))
276 |         if target.is_file():
277 |             files = [target]
278 |         elif target.is_dir():
279 |             files = [p for p in walk_files(target) if p.suffix == ".py"]
280 |         else:
281 |             return f"ERROR: '{args.get('path', '.')}' does not exist"
282 |         rows: list[str] = []
283 |         for path in files:
284 |             rows.append(path.relative_to(self._root).as_posix() + ":")
285 |             try:
286 |                 tree = ast.parse(path.read_text(encoding="utf-8"))
287 |             except (SyntaxError, UnicodeDecodeError):
288 |                 rows.append("  (cannot parse)")
289 |                 continue
290 |             for node in tree.body:
291 |                 if isinstance(node, ast.ClassDef):
292 |                     rows.append(f"  class {node.name}  line {node.lineno}")
293 |                     rows += [f"    def {m.name}  line {m.lineno}" for m in node.body
294 |                              if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))]
295 |                 elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
296 |                     rows.append(f"  def {node.name}  line {node.lineno}")
297 |         return truncate("\n".join(rows), self._cfg.max_output_chars) or "(no Python files)"
298 | 
```
- A *repo-map* tool for big projects: instead of reading whole files the model asks for the shape of the code.
  For a file or for every `.py` file under a folder (found with `walk_files`) it parses the source with `ast` and lists top-level classes (with
  their methods) and functions, each with its line number, so a following `read_file` can target just those lines.
  Unparseable files show `(cannot parse)` (the write gate normally prevents them), and the result is truncated.

```python
299 |     def _todo(self, args: dict) -> str:
300 |         items = args["items"]
301 |         if not isinstance(items, list) or not items:
302 |             return "ERROR: 'items' must be a non-empty list of {text, done}"
303 |         self.todo = [_todo_item(i) for i in items]
304 |         finished = sum(item["done"] for item in self.todo)
305 |         lines = [f"TODO {finished}/{len(self.todo)} done"]
306 |         lines += [f"  [{'x' if item['done'] else ' '}] {item['text']}" for item in self.todo]
307 |         return "\n".join(lines)
308 | 
309 |     # ----- write tools (verified, rolled back on failure) -------------------
```
- Like Claude Code's to-do list: the model sends its whole plan as a list (each item `{text, done}` or plain text,
  normalised by `_todo_item`); the tool stores it and returns `TODO 1/3 done` plus one `[x]`/`[ ]` line per item, which the agent shows in full. It
  costs no extra model call when sent together with real work (prompt rule 12).

## Write tools and the write gate
```python
310 |     def _write_file(self, args: dict) -> str:
311 |         return self._commit(args["path"], args["content"])
312 | 
```
- Simply forwards to `_commit`.

```python
313 |     def _edit_file(self, args: dict) -> str:
314 |         path = resolve_inside(self._root, args["path"])
315 |         if not path.is_file():
316 |             return f"ERROR: file '{args['path']}' does not exist"
317 |         text = path.read_text(encoding="utf-8")
318 |         count = text.count(args["old"])
319 |         if args.get("all") is True and count >= 1:
320 |             return self._commit(args["path"], text.replace(args["old"], args["new"]))
321 |         if count != 1:
322 |             return (f"ERROR: 'old' text must match exactly once, but matched {count} times"
323 |                     + (" (use all=true to replace every occurrence)" if count > 1 else ""))
324 |         return self._commit(args["path"], text.replace(args["old"], args["new"]))
325 | 
```
- Requires the file to exist, counts occurrences of `old`, and refuses unless there is **exactly one** (zero = nothing
  to replace, several = ambiguous; the error then hints at `all=true`). The replacement goes through the same verified
  `_commit`.
- **`all=true` (rename mode):** with this opt-in flag every occurrence is replaced (at least one must exist). It was added
  after the `rename_function` eval: the model had to rename a name used twice in `main.py`, the unique-match rule
  blocked it and it got stuck. The unique-match rule stays the default because it prevents accidental mass edits; the
  verification gate still checks the result (e.g. renaming `f` everywhere would break `from` and be rejected).

```python
326 |     def _multi_edit(self, args: dict) -> str:
327 |         path = resolve_inside(self._root, args["path"])
328 |         if not path.is_file():
329 |             return f"ERROR: file '{args['path']}' does not exist"
330 |         text = path.read_text(encoding="utf-8")
331 |         edits = args["edits"]
332 |         if not isinstance(edits, list) or not edits:
333 |             return "ERROR: 'edits' must be a non-empty list of {old, new}"
334 |         for number, edit in enumerate(edits, start=1):
335 |             count = text.count(edit["old"])
336 |             if count != 1:
337 |                 return (f"ERROR: edit {number}: 'old' text must match exactly once, "
338 |                         f"but matched {count} times. Nothing was changed.")
339 |             text = text.replace(edit["old"], edit["new"])
340 |         return self._commit(args["path"], text)
341 | 
```
- Several edits to one file in **one model call and one verification** (fewer calls than repeated `edit_file`).
  Edits are applied in order to an in-memory copy; every `old` must match exactly once at its turn. On the first problem
  it returns an error naming the edit number and *nothing is written* (all-or-nothing). Success goes through `_commit`,
  so the usual verify/rollback gate applies to the combined result. An empty or non-list `edits` is rejected.

```python
342 |     def _format(self, path: Path) -> None:
343 |         """Best-effort ``ruff format`` of a file that already passed verification."""
344 |         if self._cfg.auto_format and _installed("ruff"):
345 |             run_command([sys.executable, "-m", "ruff", "format", str(path)], self._root, 30, 500)
346 | 
```
- Best-effort auto-format: if `auto_format` is on and ruff is installed, runs `ruff format <file>` in the workspace
  (30 s limit). It runs only **after** verification passed, so a format problem can never reject good code, and its
  output is ignored. Result: consistent style at zero model cost. Because formatting may change the file's text, the
  model should re-read before an exact-match `edit_file` if it is unsure.

```python
347 |     def _commit(self, relative: str, text: str) -> str:
348 |         """Write ``text``; verify checkable files and roll back on failure."""
349 |         path = resolve_inside(self._root, relative)
350 |         key = path.relative_to(self._root).as_posix()
351 |         previous = path.read_text(encoding="utf-8") if path.exists() else None
352 |         path.parent.mkdir(parents=True, exist_ok=True)
353 |         path.write_text(text, encoding="utf-8")
354 |         if path.suffix.lower() not in CHECKED_SUFFIXES:
355 |             self.writes += 1
356 |             self._remember_diff(key, previous, path)
357 |             return f"OK: wrote {key} ({len(text.splitlines())} lines)" + self._after_edit(key)
358 |         report = verify_file(path, self._root, self._cfg.type_check)
359 |         if report.ok:
360 |             self._failures.pop(key, None)
361 |             self.writes += 1
362 |             if path.suffix == ".py":
363 |                 self._format(path)
364 |             self._remember_diff(key, previous, path)
365 |             return (f"OK: wrote {key} ({len(text.splitlines())} lines); verification passed"
366 |                     + self._after_edit(key))
367 |         if previous is None:                      # roll back: undo the bad write
368 |             path.unlink()
369 |         else:
370 |             path.write_text(previous, encoding="utf-8")
371 |         count = self._failures.get(key, 0) + 1
372 |         self._failures[key] = count
373 |         if count >= self._cfg.max_fix_attempts:
374 |             return (f"REJECTED ({count}/{self._cfg.max_fix_attempts}): {key} NOT written.\n"
375 |                     f"{report.render()}\nSTOP: attempts exhausted. Explain the problem to "
376 |                     "the user instead of retrying.")
377 |         hint = f"\n{TOGETHER_HINT}" if "F821" in report.render() else ""   # F821 = undefined name
378 |         return (f"REJECTED ({count}/{self._cfg.max_fix_attempts}): {key} NOT written. "
379 |                 f"Fix these errors and call write_file again:\n{report.render()}{hint}")
380 | 
```
- Resolves the path safely and computes `key`, the workspace-relative name used to count failures per file.
- Remembers the previous content (`None` for a new file) so the write can be undone, creates parent folders, writes.
- Files with no check (not in `CHECKED_SUFFIXES`) are just written. Checkable files (Python, JSON, TOML, YAML and the
  languages in `verify.py`'s `CHECKERS`) go through `verify_file` on the file on disk.
- **Pass:** clear this file's failure counter, count the write, run `_format` (Python only), remember the diff for
  your screen (`_remember_diff`), run the `after_edit` hook, and report success. Non-checkable files also get a diff
  and the hook. Non-Python files are
  just written and counted.
- **Fail (rollback):** delete a brand-new file, or restore an overwritten file's previous content. Increment the
  counter. An undefined-name error gets `TOGETHER_HINT`. When the counter reaches `max_fix_attempts` the message ends
  with `STOP`, steering the model to explain the problem
  instead of looping; otherwise it returns the exact errors and asks for a fix.

```python
381 |     def _remember_diff(self, key: str, previous: str | None, path: Path) -> None:
382 |         """Keep a unified diff of the change so the user can see what was edited."""
383 |         try:
384 |             now = path.read_text(encoding="utf-8").splitlines()
385 |         except (OSError, UnicodeDecodeError):
386 |             now = []
387 |         before = previous.splitlines() if previous is not None else []
388 |         diff = list(difflib.unified_diff(before, now, f"a/{key}", f"b/{key}", n=1, lineterm=""))
389 |         self._diff = diff[2:]                     # drop the ---/+++ header: the file is named already
390 | 
391 |     # ----- hooks: your own commands, run automatically ----------------------
```
- Compares the file before (empty for a new file) and after (re-read from disk, so formatting is included) with
  `difflib.unified_diff`, one line of context, and drops the two `---`/`+++` header lines (the result line already names
  the file).

## Hooks — your own commands, run automatically
Like Claude Code's hooks. In `settings.json`:
```json
{"hooks": {"after_edit": "ruff check {file}", "after_request": "python -m pytest -q"}}
```
```python
392 |     def run_hook(self, name: str, file: str = "") -> tuple[int, str] | None:
393 |         """Run the hook ``name`` from the settings (``{file}`` = the changed file); None if unset.
394 | 
395 |         Hooks are commands YOU wrote in settings.json, so they run without a question, on this
396 |         computer, in the project folder, with the normal time limit.
397 |         """
398 |         command = self.hooks.get(name)
399 |         if not command:
400 |             return None
401 |         argv = shell_argv(str(command).replace("{file}", file))
402 |         return run_command(argv, self._root, self._cfg.command_timeout, self._cfg.max_output_chars)
403 | 
```
- Runs the named hook if it is set (`{file}` becomes the changed file), through `shell_argv` (PowerShell on Windows, `sh` elsewhere),
  in the project folder, with the normal time limit and output cap. No question is asked: you wrote the command.

```python
404 |     def _after_edit(self, key: str) -> str:
405 |         """Run the after_edit hook; a failure is added to the result so the model fixes it."""
406 |         outcome = self.run_hook("after_edit", key)
407 |         if outcome is None or outcome[0] == 0:
408 |             return ""
409 |         return f"\nHOOK after_edit failed (exit code {outcome[0]}):\n{outcome[1].strip()}"
410 | 
411 |     # ----- permission gate for everything that runs or reaches out ---------
```
- Runs `after_edit` after every successful write. A failing hook (exit code not 0) adds its output to the write's
  result — so the model reads, say, the linter's complaint and fixes it, the way Claude Code's PostToolUse hooks feed
  back. A passing or missing hook adds nothing (no tokens).

## The permission gate
```python
412 |     def _permitted(self, tool: str, subject: str, question: str) -> str | None:
413 |         """None when the action may go ahead; otherwise the refusal text for the model."""
414 |         decision = self.permissions.decide(tool, subject)
415 |         if decision == "deny":
416 |             return f"DENIED by a deny rule in the settings: {tool}({subject})"
417 |         if decision == "allow" or self._approve(Approval(question, tool, subject)):
418 |             return None
419 |         return "DENIED: the user did not approve this."
420 | 
421 |     # ----- execution tools (verified again, then user-approved) -------------
```
- Every action that runs code or reaches outside goes through here with its tool name, its subject (file, command,
  URL, MCP arguments) and the question text. A deny rule refuses without asking; an allow rule lets it run without
  asking; otherwise the approval function is asked with an `Approval`. Returns `None` (go ahead) or the refusal text the
  model will see.

## Execution tools and the run gate
```python
422 |     def _run_python(self, args: dict) -> str:
423 |         path = resolve_inside(self._root, args["path"])
424 |         if not path.is_file():
425 |             return f"ERROR: file '{args['path']}' does not exist"
426 |         report = verify_python(path, self._root, self._cfg.type_check)
427 |         if not report.ok:
428 |             return f"NOT RUN: verification failed:\n{report.render()}"
429 |         extra = [str(a) for a in args.get("args", [])]
430 |         relative = path.relative_to(self._root).as_posix()
431 |         denied = self._permitted("run_python", relative, f"run python {relative} {' '.join(extra)}")
432 |         if denied:
433 |             return denied
434 |         code, output = self.runner.run("python", [relative, *extra])
435 |         return f"exit code {code}\n{output}"
436 | 
```
- Resolves the path, requires the file, **verifies again** (failure → `NOT RUN` plus errors), then passes the permission
  gate with subject = the file's workspace path (verification comes first so you are never asked about code that cannot
  run). It runs with the current Python
  through the runner (on this computer or in the container), using the path relative to the workspace so it is the
  same in both places, and returns the exit code and output.

```python
437 |     def _run_tests(self, args: dict) -> str:
438 |         problems = self.check_workspace(since=self.started)   # files changed this session
439 |         if problems:
440 |             return "NOT RUN: verification failed:\n" + "\n".join(problems)
441 |         denied = self._permitted("run_tests", "", "run the tests (unittest discovery)")
442 |         if denied:
443 |             return denied
444 |         code, output = self.runner.run("tests", [])
445 |         return f"exit code {code}\n{output}"
446 | 
```
- All checkable files changed this session (`since=self.started`) must pass `check_workspace` first. Then the permission gate (`run_tests`), then
  `python -m unittest discover` through the runner, returning exit code and output.

```python
447 |     def _run_shell(self, args: dict) -> str:
448 |         command = str(args["command"]).strip()
449 |         if not command:
450 |             return "ERROR: empty command"
451 |         denied = self._permitted("run_shell", command, f"run ({self.runner.shell_label}): {command}")
452 |         if denied:
453 |             return denied
454 |         code, output = self.runner.run("shell", [command])
455 |         return f"exit code {code}\n{output}"
456 | 
457 |     # ----- reaching outside: web pages and MCP servers ----------------------
```
- Only exists when the runner offers a shell (Docker, or `--allow-shell`). Refuses an empty command, passes the
  permission gate with the command line as subject (question: `run (<where>): <command>`), then runs it through the
  runner.

## Reaching outside: web pages and MCP servers
```python
458 |     def _fetch_url(self, args: dict) -> str:
459 |         url = str(args["url"]).strip()
460 |         denied = self._permitted("fetch_url", url, f"fetch the web page {url}")
461 |         if denied:
462 |             return denied
463 |         try:
464 |             text = fetch_text(url)
465 |         except ValueError as exc:
466 |             return f"ERROR: could not fetch {url}: {exc}"
467 |         return truncate(text, self._cfg.max_output_chars) or "(the page has no text)"
468 | 
```
- Permission gate with the URL as subject, then `fetch_text` (see `web.md`); a fetch problem becomes `ERROR: could not
  fetch ...`; the text is cut to `max_output_chars`.

```python
469 |     def _start_mcp(self, servers: dict) -> None:
470 |         """Start each configured MCP server and add its tools as ``mcp__<server>__<tool>``."""
471 |         for name, spec in servers.items():
472 |             if not isinstance(spec, dict) or not spec.get("command"):
473 |                 self.notes.append(f"MCP server '{name}': needs a \"command\"; skipped")
474 |                 continue
475 |             server = MCPServer(name, str(spec["command"]), [str(a) for a in spec.get("args", [])],
476 |                                {str(k): str(v) for k, v in spec.get("env", {}).items()}, self._root)
477 |             try:
478 |                 server.start()
479 |             except MCPError as exc:
480 |                 server.close()
481 |                 self.notes.append(f"{exc}; skipped")
482 |                 continue
483 |             self._mcp.append(server)
484 |             for tool in server.tools:
485 |                 full = re.sub(r"[^A-Za-z0-9_-]", "_", f"mcp__{name}__{tool['name']}")[:64]
486 |                 description = str(tool.get("description", ""))[:500] or f"MCP tool from {name}"
487 |                 self._tools[full] = Tool(full, description, clean_schema(tool.get("inputSchema")),
488 |                                          self._mcp_caller(server, str(tool["name"]), full))
489 |             self.notes.append(f"MCP server '{name}': {len(server.tools)} tool(s)")
490 | 
```
- For each configured server: it must have a `command` (otherwise a note says it was skipped); it is started in the
  project folder with its arguments and environment; a server that fails to start is closed and noted.
- Each of its tools is added as `mcp__<server>__<tool>`, with characters other than letters, digits, `_` and `-`
  replaced and the name cut to 64 characters (limits model APIs impose), a description capped at 500 characters, and
  a cleaned argument schema (`clean_schema`). A note reports how many tools each server added.

```python
491 |     def _mcp_caller(self, server: MCPServer, tool: str, full: str) -> Callable[[dict], str]:
492 |         """The function behind one MCP tool: ask permission, then forward the call."""
493 |         def call(args: dict) -> str:
494 |             shown = json.dumps(args, sort_keys=True)
495 |             denied = self._permitted(full, shown, f"use MCP tool {server.name}/{tool} {shown[:200]}")
496 |             if denied:
497 |                 return denied
498 |             try:
499 |                 return server.call_tool(tool, args, timeout=self._cfg.command_timeout * 4)
500 |             except MCPError as exc:
501 |                 return f"ERROR: {exc}"
502 |         return call
```
- Builds the function behind one MCP tool. It shows the arguments as sorted JSON (the permission subject and part of the
  question), passes the permission gate, then forwards the call with a generous timeout (4× the command timeout);
  MCP problems come back as `ERROR:` text. There is no verification step because a shell command
  is not Python; the container's limits are the safety net.
