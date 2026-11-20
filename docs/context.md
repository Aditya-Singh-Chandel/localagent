# `localagent/context.py` — what the model is told before the first word

**Role in the flow:** builds the *system prompt* once per session: fixed rules + your project notes + an overview of
the workspace's files. This is a call-saving optimisation: with the overview already in the prompt the model does not
spend its first 2–3 slow calls on `list_files`. Because the prompt is never edited afterwards, the model server can
reuse its cached representation of it on every later call.

## Module header
```python
  1 | """Builds the system prompt: fixed rules + project notes + a file overview.
  2 | 
  3 | Putting the file overview and AGENT.md in the prompt up front saves the model
  4 | several "look around" calls at the start of every task (each call costs many
  5 | seconds on a CPU). The prompt is built once per session and never edited, so
  6 | the model server can cache it.
  7 | """
  8 | from __future__ import annotations
  9 | 
 10 | from collections import Counter
 11 | from pathlib import Path
 12 | 
 13 | from .config import Config
 14 | from .sandbox import project_files, truncate
 15 | 
 16 | SYSTEM_PROMPT = """You are a careful coding agent working inside a sandboxed workspace folder.
 17 | Every response you send costs one slow, rate-limited model call, so do the work in as FEW
 18 | responses as possible.
 19 | Rules:
 20 | 1. The file overview below is already known: never call list_files for the workspace root.
 21 |    A folder shown as "name/ (N files: ...)" was too big to list; list it only if you need it.
 22 |    Read only files you need (use search, or read_file with start/end) and never read the same
 23 |    file twice unless it changed. A file the user attached ("Contents of X (attached by the
 24 |    user)") is current: never read it again.
 25 | 2. Put several tool calls in ONE response whenever you can. They run in order, so one response
 26 |    may read two files, or write a file, run it, and finish.
 27 | 3. Make small, focused changes. Prefer edit_file (or multi_edit) over rewriting a whole file.
 28 | 4. write_file and edit_file verify code. If a call is REJECTED, read the errors,
 29 |    fix exactly those problems, and call again. Never repeat identical code.
 30 | 5. After writing code, run it once (run_python or run_tests) in the same response. Do not run
 31 |    the same command twice, and do not run tests when no test files exist.
 32 | 6. If a tool says STOP, or you are stuck, explain the problem to the user instead.
 33 | 7. When the work is done and verified, call finish with a short summary IN THE SAME response as
 34 |    your last run. Do not send a separate final message.
 35 | 8. Act, do not just describe: a request to fix, add or create means you must change files with
 36 |    tools before you finish. Never search for names you are guessing; read the files listed below.
 37 | 9. To fix a bug: run_tests first to see the failure, read the code it points to, make the
 38 |    smallest edit_file change, then run_tests again (and finish in that same response).
 39 | 10. Check that every function, method or file named in the request exists exactly as asked.
 40 |     When adding something new, add it; do not change existing behaviour unless asked.
 41 |     Only create extra files (such as tests) when the request asks for them.
 42 | 11. To ADD a method or function: read the file, pick its LAST line (it must be unique), and call
 43 |     edit_file with old = that last line and new = that same line followed by the new code.
 44 |     Never put an existing 'def' line you are not changing into old.
 45 | 12. For a task with 3 or more steps, send a todo checklist in your first response (together with
 46 |     your first reads) and update it in the same responses as your work; never alone.
 47 | 13. Files of any language can be read and edited; many languages are syntax-checked on write.
 48 |     If a run_shell tool is offered, use it for git, pip, npm, node, go or cargo commands.
 49 | 14. If the user only asks a question (about this project, about code, or anything else), answer
 50 |     it directly and concisely, with a short example when useful. Use tools only if you must look
 51 |     at the project to answer (then answer in your NEXT response, after you have seen what they
 52 |     returned), never change files to answer a question, and put the full answer itself in your
 53 |     reply or finish summary (not "I explained ...").
 54 | 15. If a request is ambiguous in a way that changes the result, ask one short question instead
 55 |     of guessing."""
 56 | CODE_SUFFIXES = frozenset({".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".go", ".rs",
 57 |                            ".java", ".kt", ".c", ".h", ".cpp", ".hpp", ".cc", ".cs", ".rb", ".php",
 58 |                            ".swift", ".sh", ".ps1", ".vue", ".svelte", ".dart", ".lua", ".sql"})
 59 | 
 60 | 
```
- Imports `Counter` (counts file kinds), `Path`, `Config`, `truncate` (to cap `AGENT.md`) and `project_files` (the
  project's files without ignored or skipped folders).
- `SYSTEM_PROMPT` is the standing instruction text. It opens by telling the model that **every response costs one slow,
  rate-limited call**, then:
  1. never list the workspace root (the overview is in the prompt; a folder summarised as `name/ (N files: ...)` may be
     listed when needed), read only what is needed, never re-read a file — nor a file the user attached with `@`
     (added after a live run: Gemini read the attached file anyway; with the sentence the same request took 1 call
     instead of 2 and half the tokens);
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

- `CODE_SUFFIXES` are the file kinds counted as source code when choosing which folders to show first.

## The file overview
**Why it changed in 0.6.** The old overview listed the first 40 file paths alphabetically. On this very project those
were `.dockerignore`, `Dockerfile`, ... and then 32 llama.cpp `.dll` files from `bin/` — the actual code (`localagent/`,
`tests/`) never appeared, so the model had to call `list_files` anyway (a recorded session shows exactly that). Now
ignored files are left out (`project_files`), small folders are listed in full and crowded ones are summarised in one
line, with code folders listed first. For this project (50 lines): the 9 top-level files, `.github/workflows/ci.yml`,
`docker/sandbox.Dockerfile`, all 24 files of `localagent/`, all 9 of `tests/`, both of `tools/`, and one line each for
`docs/ (33 files: 33 .md)`, `docs_src/ (34 files: 34 .md)` and `evals/ (3 files: 3 .py)`.

```python
 61 | def _describe(files: list[str]) -> str:
 62 |     """A crowded folder in a few words: ``61 files: 40 .dll, 20 .exe, 1 other``."""
 63 |     kinds = Counter(Path(f).suffix.lower() or "other" for f in files)
 64 |     shown = ", ".join(f"{count} {kind}" for kind, count in kinds.most_common(3))
 65 |     rest = len(kinds) - 3
 66 |     return f"{len(files)} files: {shown}" + (f" and {rest} more kinds" if rest > 0 else "")
 67 | 
 68 | 
```
- A crowded folder in a few words: its file count and its three most common kinds, e.g. `61 files: 40 .dll, 20 .exe,
  1 other` (`other` = files without an extension).

```python
 69 | def _mostly_code(files: list[str]) -> bool:
 70 |     """True when at least half of a folder's files are source code (listed before docs/assets)."""
 71 |     return sum(Path(f).suffix.lower() in CODE_SUFFIXES for f in files) * 2 >= len(files)
 72 | 
 73 | 
```
- True when at least half of a folder's files are source code; such folders get the room first.

```python
 74 | def _overview(files: list[str], prefix: str, budget: int) -> list[str]:
 75 |     """At most ``budget`` lines for ``files`` (paths below ``prefix``): small folders listed in
 76 |     full, crowded ones summarised in one line each - code folders get the room first."""
 77 |     if len(files) <= budget:
 78 |         return [prefix + f for f in files]
 79 |     direct = [f for f in files if "/" not in f]
 80 |     folders: dict[str, list[str]] = {}
 81 |     for f in files:
 82 |         if "/" in f:
 83 |             head, rest = f.split("/", 1)
 84 |             folders.setdefault(head, []).append(rest)
 85 |     shown = {name: [f"{prefix}{name}/ ({_describe(sub)})"] for name, sub in folders.items()}
 86 |     if len(direct) + len(folders) > budget:               # even one line each is too many
 87 |         entries = [prefix + f for f in direct] + [shown[n][0] for n in sorted(shown)]
 88 |         return entries[:budget - 1] + [f"... and {len(entries) - budget + 1} more"]
 89 |     spare = budget - len(direct) - len(folders)
 90 |     order = sorted(folders, key=lambda n: (not _mostly_code(folders[n]), -len(folders[n])))
 91 |     for name in order:                                    # list whole folders while room lasts
 92 |         if len(folders[name]) - 1 <= spare:
 93 |             shown[name] = [f"{prefix}{name}/{f}" for f in folders[name]]
 94 |             spare -= len(folders[name]) - 1
 95 |     for name in order:                                    # else one level deeper, if it fits
 96 |         sub = folders[name]
 97 |         needed = len({f.split("/", 1)[0] for f in sub})   # its files and sub-folders, one line each
 98 |         if len(shown[name]) == 1 and any("/" in f for f in sub) and needed - 1 <= spare:
 99 |             shown[name] = _overview(sub, f"{prefix}{name}/", needed)
100 |             spare -= len(shown[name]) - 1
101 |     return [prefix + f for f in direct] + [line for name in sorted(shown) for line in shown[name]]
102 | 
103 | 
```
- Returns at most `budget` lines describing `files` (paths relative to `prefix`). When everything fits, it is a plain
  list. Otherwise the folder's direct files are listed and each sub-folder starts as one summary line.
- If even one line per entry is too many, the entries that fit are shown followed by `... and N more`.
- Spare lines are then spent **opening folders whole** — code folders first, the biggest first among them — while they
  fit. A folder that still does not fit but contains sub-folders may be opened **one level deeper** (its files and
  sub-folder summaries), if that fits.

```python
104 | def project_tree(root: Path, limit: int) -> str:
105 |     """The workspace's files as relative paths; when there are more than ``limit``, crowded
106 |     folders are summarised so the overview still fits ``limit`` lines (.gitignore is honoured)."""
107 |     root = root.resolve()
108 |     files = [path.relative_to(root).as_posix() for path in project_files(root)]
109 |     return "\n".join(_overview(files, "", limit)) or "(empty)"
110 | 
111 | 
```
- The overview of the whole workspace in at most `limit` lines (`tree_entries`, 50), or `(empty)`. `.gitignore` is
  honoured in git repositories (see `project_files` in `sandbox.md`).

## `build_system_prompt`
```python
112 | def build_system_prompt(config: Config) -> str:
113 |     """Assemble the full system prompt for a new session."""
114 |     root = config.workspace.resolve()
115 |     parts = [SYSTEM_PROMPT]
116 |     notes = root / "AGENT.md"
117 |     if notes.is_file():
118 |         text = notes.read_text(encoding="utf-8", errors="replace")
119 |         parts.append("Project notes (AGENT.md):\n" + truncate(text, config.memory_chars))
120 |     if root.is_dir():
121 |         parts.append("Workspace files at session start (may be stale; use list_files "
122 |                      "to refresh):\n" + project_tree(root, config.tree_entries))
123 |     return "\n\n".join(parts)
```
- Starts with the rules. If `AGENT.md` exists in the workspace, its text (truncated to `memory_chars`) is added as
  "Project notes" — this is where you record conventions such as "use pytest" or "line length 100".
- If the workspace exists, the file overview is added with a warning that it may become stale as files change.
- The parts are joined with blank lines into the final string.
