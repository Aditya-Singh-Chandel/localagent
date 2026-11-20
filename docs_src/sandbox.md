# `localagent/sandbox.py` — safety helpers

**Role in the flow:** the model is untrusted. Every file path it names passes through `resolve_inside`;
every program it makes us run passes through `run_command`; every long result passes through `truncate`; and every
"look at all files" operation (search, outline, workspace check, snapshots, the file list in the prompt) goes through
`walk_files`, which never enters folders such as `node_modules` or `.venv`.

## Module header and `SKIP_DIRS`
@@ localagent/sandbox.py header @@
- The docstring states the rule. `os` provides `os.walk`; `subprocess` launches programs; `Path` is the path type;
  `Iterator` types `walk_files`.
- `SKIP_DIRS` lists folders that are never shown, searched, snapshotted or verified: version control (`.git`), Python
  caches (`__pycache__`, `.mypy_cache`, `.ruff_cache`, `.pytest_cache`, `.tox`), virtual environments (`.venv`,
  `venv`), installed JavaScript packages (`node_modules`) and the agent's own state folder (`.localagent`). It is a
  `frozenset` so no code can change it by accident. Hiding them saves tokens and keeps the model out of files it must
  not edit.

## `walk_files`
@@ localagent/sandbox.py walk_files @@
- `os.walk` visits one folder at a time and gives the sub-folder names in a list; assigning a filtered list back to
  `dirnames[:]` tells it **not to enter** the skipped ones. That is the speed difference on real projects: a plain
  `rglob("*")` would list all ~100 000 files in a typical `node_modules` before filtering them out.
- Folders and files are sorted, so results are stable (same order every time — good for tests and for the prompt).

## `SandboxError`
@@ localagent/sandbox.py SandboxError @@
- Raised when a path escapes the workspace. Tools turn it into an `ERROR:` string for the model.

## `resolve_inside`
@@ localagent/sandbox.py resolve_inside @@
- Makes the workspace root absolute, joins the requested path to it and calls `resolve()`: this collapses `..`,
  follows symlinks, and — if the request was absolute like `C:\Windows` — discards the root entirely, which is
  exactly what the next check catches.
- The result is allowed only if it *is* the root or the root is one of its parent folders; otherwise it raises.

## `truncate`
@@ localagent/sandbox.py truncate @@
- Short text is returned unchanged. Otherwise the head gets two thirds of the budget, the tail the rest, with a marker
  saying how many characters were dropped. Errors and summaries usually appear at the end of output, so keeping the
  tail matters.

## `shell_argv`
@@ localagent/sandbox.py shell_argv @@
- The one place that decides how a shell command runs on this computer: PowerShell (`-NoProfile` = skip your profile
  script, `-NonInteractive` = never wait for typing, `-Command` = run this text) on Windows, `sh -c` elsewhere. Used by
  the host runner (`run_shell` with `--allow-shell`) and by hooks.

## `run_command`
@@ localagent/sandbox.py run_command @@
- Takes a list `argv` (program + arguments), the folder to run in, a timeout and an output cap.
- `subprocess.run` with a **list** (never `shell=True`, so no shell-injection), output captured as text, UTF-8 with
  undecodable bytes replaced, and a timeout.
- On timeout the child is killed and we return exit code 124 (the `timeout` convention); program not found → 127 (the
  shell convention). Otherwise the exit code and the combined, truncated output.

> Note: this limits *where* files are touched and *how long* programs run, but a program run on your machine can still
> do anything your user account can. That is why `tools.py` asks for approval before running anything, and why
> `--sandbox docker` exists (see `runners.md`).
