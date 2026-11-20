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
@@ localagent/tools.py header @@
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
@@ localagent/tools.py _todo_item @@
- Turns one checklist entry into `{text, done}`. Accepts `{"text": ..., "done": ...}` or plain text. Real models often
  write the state into the text instead of setting `done` (seen in the end-to-end test: every item said "(done)" while
  the header said `0/4 done`), so endings in `DONE_MARKS` such as `(done)` or `✓`, and a leading `[x]`, also mark the
  item done and are removed from the text. `DONE_MARKS` is defined just above (part of the header block).

## `Approval`
@@ localagent/tools.py Approval @@
@@ localagent/tools.py Approval.__new__ @@
- The question shown to you ("run python main.py") is an `Approval`: a normal string (so every approval function that
  expects text keeps working, and `"main.py" in question` works) that **also** carries `tool` and `subject`. The CLI's
  "always" answer uses those two to save an exact permission rule. A `str` subclass must set its text in `__new__`
  (strings cannot change after creation), so the extra attributes are attached there.

## `Tool`
@@ localagent/tools.py Tool @@
@@ localagent/tools.py Tool.spec @@
- A frozen record of: `name`, `description` (the model reads this to decide when to use the tool), `parameters` (a
  JSON schema describing the arguments), and `func`, the Python function that implements it.
- `spec()` builds the `{"type": "function", "function": {...}}` structure Ollama and OpenAI-style servers expect.

## `_schema`
@@ localagent/tools.py _schema @@
- Builds a JSON-schema object: `properties` lists the arguments, `required` the mandatory ones. It removes
  boilerplate from the tool table.

## `ToolBox` setup
@@ localagent/tools.py ToolBox @@
@@ localagent/tools.py ToolBox.__init__ @@
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
@@ localagent/tools.py ToolBox.close @@
@@ localagent/tools.py ToolBox.pop_diff @@
- `pop_diff` hands the last write's diff to the agent (for your screen) and forgets it.
- `close` releases resources: stops the sandbox container if one was started and every MCP server program. Called by the eval runner after each task and by
  the server when a session expires; the CLI relies on the exit-time clean-up.

@@ localagent/tools.py ToolBox.is_read_only @@
- Static helper: is this tool name in `READ_ONLY`? Unknown names are not, so they run sequentially.

@@ localagent/tools.py ToolBox.specs @@
- The list of tool definitions sent to the model on every request.

@@ localagent/tools.py ToolBox.check_workspace @@
- Verifies every checkable file found by `walk_files` — or, with `since`, only files modified after that moment (one
  second of slack for clock rounding) — and returns one entry per failing file (path plus errors). With `since` it is
  fast in big projects and never blames the agent for files that were already broken before it started. An empty list means everything is fine. Used by `_run_tests` and by the CLI's end-of-request check.

@@ localagent/tools.py ToolBox.call @@
- Looks the tool up; an unknown name (models sometimes invent tools) returns an error that lists the real ones.
- Runs the function and converts expected failures into strings: `SandboxError` (path escape) → `ERROR:`;
  `KeyError`/`TypeError`/`ValueError` (missing or wrong-typed argument) → "bad arguments"; `OSError` (permissions,
  disk) → `ERROR:`.

## The tool table
@@ localagent/tools.py ToolBox._build @@
- One `Tool(...)` per capability: `list_files`, `read_file` (optional `start`/`end` so the model can read only a
  slice), `search` (grep-like, far cheaper than reading files), `outline` (classes and functions with line numbers),
  `write_file`, `edit_file` (replace one unique snippet instead of rewriting a file), `multi_edit` (several such
  replacements in one call), `run_python`, `run_tests`, and — **only when the runner supports it (Docker)** —
  `run_shell`, plus `todo` (a checklist) and `fetch_url` (read a web page; always asked or ruled). The list is built as "base tools + optional shell tool", so the model
  never even sees a shell tool unless the runner offers one (Docker, or `--allow-shell` on this computer). The shell
  tool's description names where it runs (`shell_label`), e.g. "PowerShell on this Windows computer".
- The descriptions are part of the prompt, so they are written to tell the model *when* to use each tool.

## Read-only tools
@@ localagent/tools.py ToolBox._list_files @@
- Resolves the folder inside the sandbox; errors if it is not a folder; lists sorted names (folders get `/`), hiding
  `SKIP_DIRS`; says "(empty folder)" when appropriate.

@@ localagent/tools.py ToolBox._read_file @@
- Resolves and checks the file, reads it as text (undecodable bytes are replaced rather than crashing), clamps
  `start` (at least 1) and `end` (at most the file length), prefixes each line with its number so the model can quote
  exact locations, and truncates the result.

@@ localagent/tools.py ToolBox._search @@
- Compiles the regular expression; an invalid one returns a readable error instead of crashing.
- Walks the chosen folder with `walk_files` (stopping after `MAX_SEARCH_FILES` files with a note) and keeps files whose
  *name* matches `glob` (default `*.py`, e.g.
  `*.js` for JavaScript).
- Binary files raise `UnicodeDecodeError` and are skipped. Each matching line is reported as `path:line: text`
  (stripped and cut to 120 characters). After 40 hits it stops and says so; no hits returns "no matches".

@@ localagent/tools.py ToolBox._outline @@
- A *repo-map* tool for big projects: instead of reading whole files the model asks for the shape of the code.
  For a file or for every `.py` file under a folder (found with `walk_files`) it parses the source with `ast` and lists top-level classes (with
  their methods) and functions, each with its line number, so a following `read_file` can target just those lines.
  Unparseable files show `(cannot parse)` (the write gate normally prevents them), and the result is truncated.

@@ localagent/tools.py ToolBox._todo @@
- Like Claude Code's to-do list: the model sends its whole plan as a list (each item `{text, done}` or plain text,
  normalised by `_todo_item`); the tool stores it and returns `TODO 1/3 done` plus one `[x]`/`[ ]` line per item, which the agent shows in full. It
  costs no extra model call when sent together with real work (prompt rule 12).

## Write tools and the write gate
@@ localagent/tools.py ToolBox._write_file @@
- Simply forwards to `_commit`.

@@ localagent/tools.py ToolBox._edit_file @@
- Requires the file to exist, counts occurrences of `old`, and refuses unless there is **exactly one** (zero = nothing
  to replace, several = ambiguous; the error then hints at `all=true`). The replacement goes through the same verified
  `_commit`.
- **`all=true` (rename mode):** with this opt-in flag every occurrence is replaced (at least one must exist). It was added
  after the `rename_function` eval: the model had to rename a name used twice in `main.py`, the unique-match rule
  blocked it and it got stuck. The unique-match rule stays the default because it prevents accidental mass edits; the
  verification gate still checks the result (e.g. renaming `f` everywhere would break `from` and be rejected).

@@ localagent/tools.py ToolBox._multi_edit @@
- Several edits to one file in **one model call and one verification** (fewer calls than repeated `edit_file`).
  Edits are applied in order to an in-memory copy; every `old` must match exactly once at its turn. On the first problem
  it returns an error naming the edit number and *nothing is written* (all-or-nothing). Success goes through `_commit`,
  so the usual verify/rollback gate applies to the combined result. An empty or non-list `edits` is rejected.

@@ localagent/tools.py ToolBox._format @@
- Best-effort auto-format: if `auto_format` is on and ruff is installed, runs `ruff format <file>` in the workspace
  (30 s limit). It runs only **after** verification passed, so a format problem can never reject good code, and its
  output is ignored. Result: consistent style at zero model cost. Because formatting may change the file's text, the
  model should re-read before an exact-match `edit_file` if it is unsure.

@@ localagent/tools.py ToolBox._commit @@
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

@@ localagent/tools.py ToolBox._remember_diff @@
- Compares the file before (empty for a new file) and after (re-read from disk, so formatting is included) with
  `difflib.unified_diff`, one line of context, and drops the two `---`/`+++` header lines (the result line already names
  the file).

## Hooks — your own commands, run automatically
Like Claude Code's hooks. In `settings.json`:
```json
{"hooks": {"after_edit": "ruff check {file}", "after_request": "python -m pytest -q"}}
```
@@ localagent/tools.py ToolBox.run_hook @@
- Runs the named hook if it is set (`{file}` becomes the changed file), through `shell_argv` (PowerShell on Windows, `sh` elsewhere),
  in the project folder, with the normal time limit and output cap. No question is asked: you wrote the command.

@@ localagent/tools.py ToolBox._after_edit @@
- Runs `after_edit` after every successful write. A failing hook (exit code not 0) adds its output to the write's
  result — so the model reads, say, the linter's complaint and fixes it, the way Claude Code's PostToolUse hooks feed
  back. A passing or missing hook adds nothing (no tokens).

## The permission gate
@@ localagent/tools.py ToolBox._permitted @@
- Every action that runs code or reaches outside goes through here with its tool name, its subject (file, command,
  URL, MCP arguments) and the question text. A deny rule refuses without asking; an allow rule lets it run without
  asking; otherwise the approval function is asked with an `Approval`. Returns `None` (go ahead) or the refusal text the
  model will see.

## Execution tools and the run gate
@@ localagent/tools.py ToolBox._run_python @@
- Resolves the path, requires the file, **verifies again** (failure → `NOT RUN` plus errors), then passes the permission
  gate with subject = the file's workspace path (verification comes first so you are never asked about code that cannot
  run). It runs with the current Python
  through the runner (on this computer or in the container), using the path relative to the workspace so it is the
  same in both places, and returns the exit code and output.

@@ localagent/tools.py ToolBox._run_tests @@
- All checkable files changed this session (`since=self.started`) must pass `check_workspace` first. Then the permission gate (`run_tests`), then
  `python -m unittest discover` through the runner, returning exit code and output.

@@ localagent/tools.py ToolBox._run_shell @@
- Only exists when the runner offers a shell (Docker, or `--allow-shell`). Refuses an empty command, passes the
  permission gate with the command line as subject (question: `run (<where>): <command>`), then runs it through the
  runner.

## Reaching outside: web pages and MCP servers
@@ localagent/tools.py ToolBox._fetch_url @@
- Permission gate with the URL as subject, then `fetch_text` (see `web.md`); a fetch problem becomes `ERROR: could not
  fetch ...`; the text is cut to `max_output_chars`.

@@ localagent/tools.py ToolBox._start_mcp @@
- For each configured server: it must have a `command` (otherwise a note says it was skipped); it is started in the
  project folder with its arguments and environment; a server that fails to start is closed and noted.
- Each of its tools is added as `mcp__<server>__<tool>`, with characters other than letters, digits, `_` and `-`
  replaced and the name cut to 64 characters (limits model APIs impose), a description capped at 500 characters, and
  a cleaned argument schema (`clean_schema`). A note reports how many tools each server added.

@@ localagent/tools.py ToolBox._mcp_caller @@
- Builds the function behind one MCP tool. It shows the arguments as sorted JSON (the permission subject and part of the
  question), passes the permission gate, then forwards the call with a generous timeout (4× the command timeout);
  MCP problems come back as `ERROR:` text. There is no verification step because a shell command
  is not Python; the container's limits are the safety net.
