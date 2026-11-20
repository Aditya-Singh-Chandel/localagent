# `localagent/context.py` — what the model is told before the first word

**Role in the flow:** builds the *system prompt* once per session: fixed rules + your project notes + the workspace
file tree. This is a call-saving optimisation: with the tree already in the prompt the model does not spend its first
2–3 slow calls on `list_files`. Because the prompt is never edited afterwards, Ollama can reuse its cached
representation of it on every later call.

## Module header
```python
 1 | """Builds the system prompt: fixed rules + project notes + a file tree.
 2 | 
 3 | Putting the file tree and AGENT.md in the prompt up front saves the model
 4 | several "look around" calls at the start of every task (each call costs many
 5 | seconds on a CPU). The prompt is built once per session and never edited, so
 6 | the model server can cache it.
 7 | """
 8 | from __future__ import annotations
 9 | 
10 | from pathlib import Path
11 | 
12 | from .config import Config
13 | from .sandbox import truncate
14 | from .sandbox import walk_files
15 | 
16 | SYSTEM_PROMPT = """You are a careful coding agent working inside a sandboxed workspace folder.
17 | Every response you send costs one slow, rate-limited model call, so do the work in as FEW
18 | responses as possible.
19 | Rules:
20 | 1. The file list below is already known: never call list_files for the workspace root. Read only
21 |    files you need (use search, or read_file with start/end) and never read the same file twice
22 |    unless it changed.
23 | 2. Put several tool calls in ONE response whenever you can. They run in order, so one response
24 |    may read two files, or write a file, run it, and finish.
25 | 3. Make small, focused changes. Prefer edit_file (or multi_edit) over rewriting a whole file.
26 | 4. write_file and edit_file verify Python code. If a call is REJECTED, read the errors,
27 |    fix exactly those problems, and call again. Never repeat identical code.
28 | 5. After writing code, run it once (run_python or run_tests) in the same response. Do not run
29 |    the same command twice, and do not run tests when no test files exist.
30 | 6. If a tool says STOP, or you are stuck, explain the problem to the user instead.
31 | 7. When the work is done and verified, call finish with a short summary IN THE SAME response as
32 |    your last run. Do not send a separate final message.
33 | 8. Act, do not just describe: a request to fix, add or create means you must change files with
34 |    tools before you finish. Never search for names you are guessing; read the files listed below.
35 | 9. To fix a bug: run_tests first to see the failure, read the code it points to, make the
36 |    smallest edit_file change, then run_tests again (and finish in that same response).
37 | 10. Check that every function, method or file named in the request exists exactly as asked.
38 |     When adding something new, add it; do not change existing behaviour unless asked.
39 |     Only create extra files (such as tests) when the request asks for them.
40 | 11. To ADD a method or function: read the file, pick its LAST line (it must be unique), and call
41 |     edit_file with old = that last line and new = that same line followed by the new code.
42 |     Never put an existing 'def' line you are not changing into old.
43 | 12. For a task with 3 or more steps, send a todo checklist in your first response (together with
44 |     your first reads) and update it in the same responses as your work; never alone.
45 | 13. Files of any language can be read and edited; many languages are syntax-checked on write.
46 |     If a run_shell tool is offered, use it for git, pip, npm, node, go or cargo commands.
47 | 14. If the user only asks a question (about this project, about code, or anything else), answer
48 |     it directly and concisely, with a short example when useful. Use tools only if you must look
49 |     at the project to answer (then answer in your NEXT response, after you have seen what they
50 |     returned), never change files to answer a question, and put the full answer itself in your
51 |     reply or finish summary (not "I explained ...").
52 | 15. If a request is ambiguous in a way that changes the result, ask one short question instead
53 |     of guessing."""
54 | 
55 | 
```
- Imports `Path`, `Config`, `truncate` (to cap `AGENT.md`) and `walk_files` (lists files, skipping folders such as
  `__pycache__` and `node_modules`).
- `SYSTEM_PROMPT` is the standing instruction text. It opens by telling the model that **every response costs one slow,
  rate-limited call**, then:
  1. never list the workspace root (the file list is in the prompt), read only what is needed, never re-read a file;
  2. put several tool calls in ONE response (they run in order: write, run, finish together);
  3. small edits (`edit_file`/`multi_edit`); 4. the reject → fix cycle; 5. run once, in the same response, never the
  same command twice, no tests when none exist; 6. explain when stuck; 7. **finish in the same response as the last
  run** — no separate final message; 8. act instead of describing, never search for guessed names; 9. the bug-fix
  recipe; 10. check every requested name exists, add without changing existing behaviour, no extra files unless asked;
  11. the recipe for adding a method (anchor on the last unique line); 12. for 3+ step tasks send a `todo` checklist
  together with real work, never alone; 13. any language may be edited (JSON/TOML/JS are checked) and the shell tool,
  when offered, is for git/pip/npm/node/go/cargo; 14. a **question** gets a direct answer (tools only if the project
  must be looked at — and then the answer comes in the *next* response, after the results were seen — never file
  changes, and the full answer itself in the reply); 15. a request that is ambiguous in a
  way that matters gets one short question instead of a guess.
  Rules 1, 2, 5, 7 and the "extra files" part of 10 were written from a traced run on `gemini-3.5-flash-lite` that showed
  exactly these wasted calls: listing the root, running tests before any existed, reading the same file twice, running
  the same script twice, creating unrequested test files, one tool per response, and a separate "done" message.
  Rules 8-11 come from the earlier local-model evals (see `03-build-log.md`).

## `project_tree`
```python
56 | def project_tree(root: Path, limit: int) -> str:
57 |     """List workspace files as relative paths (folders show in the paths), capped at ``limit``."""
58 |     rows: list[str] = []
59 |     for path in walk_files(root):
60 |         if len(rows) >= limit:
61 |             rows.append("...")
62 |             break
63 |         rows.append(path.relative_to(root).as_posix())
64 |     return "\n".join(rows) or "(empty)"
65 | 
66 | 
```
- Lists files from `walk_files` (sorted, never entering skipped folders such as `node_modules`) as relative paths;
  folders are visible inside the paths (`src/app.py`).
- Once `limit` entries are collected it appends `...` and stops, so a huge workspace cannot flood the prompt.
- An empty result shows `(empty)`.

## `build_system_prompt`
```python
67 | def build_system_prompt(config: Config) -> str:
68 |     """Assemble the full system prompt for a new session."""
69 |     root = config.workspace.resolve()
70 |     parts = [SYSTEM_PROMPT]
71 |     notes = root / "AGENT.md"
72 |     if notes.is_file():
73 |         text = notes.read_text(encoding="utf-8", errors="replace")
74 |         parts.append("Project notes (AGENT.md):\n" + truncate(text, config.memory_chars))
75 |     if root.is_dir():
76 |         parts.append("Workspace files at session start (may be stale; use list_files "
77 |                      "to refresh):\n" + project_tree(root, config.tree_entries))
78 |     return "\n\n".join(parts)
```
- Starts with the rules. If `AGENT.md` exists in the workspace, its text (truncated to `memory_chars`) is added as
  "Project notes" — this is where you record conventions such as "use pytest" or "line length 100".
- If the workspace exists, the file tree is added with a warning that it may become stale as files change.
- The parts are joined with blank lines into the final string.
