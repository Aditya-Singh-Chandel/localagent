# `localagent/context.py` — what the model is told before the first word

**Role in the flow:** builds the *system prompt* once per session: fixed rules + your project notes + the workspace
file tree. This is a call-saving optimisation: with the tree already in the prompt the model does not spend its first
2–3 slow calls on `list_files`. Because the prompt is never edited afterwards, Ollama can reuse its cached
representation of it on every later call.

## Module header
@@ localagent/context.py header @@
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
@@ localagent/context.py project_tree @@
- Lists files from `walk_files` (sorted, never entering skipped folders such as `node_modules`) as relative paths;
  folders are visible inside the paths (`src/app.py`).
- Once `limit` entries are collected it appends `...` and stops, so a huge workspace cannot flood the prompt.
- An empty result shows `(empty)`.

## `build_system_prompt`
@@ localagent/context.py build_system_prompt @@
- Starts with the rules. If `AGENT.md` exists in the workspace, its text (truncated to `memory_chars`) is added as
  "Project notes" — this is where you record conventions such as "use pytest" or "line length 100".
- If the workspace exists, the file tree is added with a warning that it may become stale as files change.
- The parts are joined with blank lines into the final string.
