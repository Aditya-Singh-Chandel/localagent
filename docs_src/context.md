# `localagent/context.py` — what the model is told before the first word

**Role in the flow:** builds the *system prompt* once per session: fixed rules + your project notes + an overview of
the workspace's files. This is a call-saving optimisation: with the overview already in the prompt the model does not
spend its first 2–3 slow calls on `list_files`. Because the prompt is never edited afterwards, the model server can
reuse its cached representation of it on every later call.

## Module header
@@ localagent/context.py header @@
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

@@ localagent/context.py _describe @@
- A crowded folder in a few words: its file count and its three most common kinds, e.g. `61 files: 40 .dll, 20 .exe,
  1 other` (`other` = files without an extension).

@@ localagent/context.py _mostly_code @@
- True when at least half of a folder's files are source code; such folders get the room first.

@@ localagent/context.py _overview @@
- Returns at most `budget` lines describing `files` (paths relative to `prefix`). When everything fits, it is a plain
  list. Otherwise the folder's direct files are listed and each sub-folder starts as one summary line.
- If even one line per entry is too many, the entries that fit are shown followed by `... and N more`.
- Spare lines are then spent **opening folders whole** — code folders first, the biggest first among them — while they
  fit. A folder that still does not fit but contains sub-folders may be opened **one level deeper** (its files and
  sub-folder summaries), if that fits.

@@ localagent/context.py project_tree @@
- The overview of the whole workspace in at most `limit` lines (`tree_entries`, 50), or `(empty)`. `.gitignore` is
  honoured in git repositories (see `project_files` in `sandbox.md`).

## `build_system_prompt`
@@ localagent/context.py build_system_prompt @@
- Starts with the rules. If `AGENT.md` exists in the workspace, its text (truncated to `memory_chars`) is added as
  "Project notes" — this is where you record conventions such as "use pytest" or "line length 100".
- If the workspace exists, the file overview is added with a warning that it may become stale as files change.
- The parts are joined with blank lines into the final string.
