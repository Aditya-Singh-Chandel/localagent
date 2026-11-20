# `localagent/sandbox.py` — safety helpers

**Role in the flow:** the model is untrusted. Every file path it names passes through `resolve_inside`;
every program it makes us run passes through `run_command`; every long result passes through `truncate`; and every
"look at all files" operation (search, outline, workspace check, snapshots, the file list in the prompt) goes through
`walk_files`, which never enters folders such as `node_modules` or `.venv`.

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
 9 | import subprocess
10 | from pathlib import Path
11 | from typing import Iterator
12 | 
13 | # Folders never shown to the model, searched, snapshotted or verified: version control, caches,
14 | # virtual environments, installed JavaScript packages, and the agent's own state folder.
15 | SKIP_DIRS = frozenset({".git", "__pycache__", ".venv", "venv", ".mypy_cache", ".ruff_cache",
16 |                        ".pytest_cache", ".tox", "node_modules", ".localagent"})
17 | 
18 | 
```
- The docstring states the rule. `os` provides `os.walk`; `subprocess` launches programs; `Path` is the path type;
  `Iterator` types `walk_files`.
- `SKIP_DIRS` lists folders that are never shown, searched, snapshotted or verified: version control (`.git`), Python
  caches (`__pycache__`, `.mypy_cache`, `.ruff_cache`, `.pytest_cache`, `.tox`), virtual environments (`.venv`,
  `venv`), installed JavaScript packages (`node_modules`) and the agent's own state folder (`.localagent`). It is a
  `frozenset` so no code can change it by accident. Hiding them saves tokens and keeps the model out of files it must
  not edit.

## `walk_files`
```python
19 | def walk_files(root: Path) -> Iterator[Path]:
20 |     """Every file under ``root`` in sorted order, never entering a SKIP_DIRS folder.
21 | 
22 |     Pruning while walking matters on real projects: ``node_modules`` alone can hold
23 |     100 000 files, which a plain ``rglob`` would visit before filtering them out.
24 |     """
25 |     for folder, dirnames, filenames in os.walk(root):
26 |         dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)   # prune in place
27 |         for name in sorted(filenames):
28 |             yield Path(folder) / name
29 | 
30 | 
```
- `os.walk` visits one folder at a time and gives the sub-folder names in a list; assigning a filtered list back to
  `dirnames[:]` tells it **not to enter** the skipped ones. That is the speed difference on real projects: a plain
  `rglob("*")` would list all ~100 000 files in a typical `node_modules` before filtering them out.
- Folders and files are sorted, so results are stable (same order every time — good for tests and for the prompt).

## `SandboxError`
```python
31 | class SandboxError(Exception):
32 |     """Raised when the model asks for something outside the allowed area."""
33 | 
34 | 
```
- Raised when a path escapes the workspace. Tools turn it into an `ERROR:` string for the model.

## `resolve_inside`
```python
35 | def resolve_inside(root: Path, relative: str) -> Path:
36 |     """Return the absolute path for ``relative`` or raise if it escapes ``root``."""
37 |     root = root.resolve()
38 |     candidate = (root / relative).resolve()  # resolves "..", symlinks, absolute inputs
39 |     if candidate != root and root not in candidate.parents:
40 |         raise SandboxError(f"path '{relative}' is outside the workspace")
41 |     return candidate
42 | 
43 | 
```
- Makes the workspace root absolute, joins the requested path to it and calls `resolve()`: this collapses `..`,
  follows symlinks, and — if the request was absolute like `C:\Windows` — discards the root entirely, which is
  exactly what the next check catches.
- The result is allowed only if it *is* the root or the root is one of its parent folders; otherwise it raises.

## `truncate`
```python
44 | def truncate(text: str, limit: int) -> str:
45 |     """Shorten long text, keeping the start and the end (errors are usually last)."""
46 |     if len(text) <= limit:
47 |         return text
48 |     head = limit * 2 // 3
49 |     tail = limit - head
50 |     omitted = len(text) - limit
51 |     return f"{text[:head]}\n... [{omitted} characters omitted] ...\n{text[-tail:]}"
52 | 
53 | 
```
- Short text is returned unchanged. Otherwise the head gets two thirds of the budget, the tail the rest, with a marker
  saying how many characters were dropped. Errors and summaries usually appear at the end of output, so keeping the
  tail matters.

## `shell_argv`
```python
54 | def shell_argv(command: str) -> list[str]:
55 |     """How this computer runs one shell command: PowerShell on Windows, ``sh`` elsewhere."""
56 |     if os.name == "nt":
57 |         return ["powershell", "-NoProfile", "-NonInteractive", "-Command", command]
58 |     return ["sh", "-c", command]
59 | 
60 | 
```
- The one place that decides how a shell command runs on this computer: PowerShell (`-NoProfile` = skip your profile
  script, `-NonInteractive` = never wait for typing, `-Command` = run this text) on Windows, `sh -c` elsewhere. Used by
  the host runner (`run_shell` with `--allow-shell`) and by hooks.

## `run_command`
```python
61 | def run_command(argv: list[str], cwd: Path, timeout: int, limit: int) -> tuple[int, str]:
62 |     """Run a program (never through a shell) and return (exit code, output)."""
63 |     try:
64 |         done = subprocess.run(
65 |             argv,
66 |             cwd=cwd,
67 |             capture_output=True,
68 |             text=True,
69 |             encoding="utf-8",
70 |             errors="replace",
71 |             timeout=timeout,
72 |         )
73 |     except subprocess.TimeoutExpired:
74 |         return 124, f"Command timed out after {timeout}s and was killed."
75 |     except FileNotFoundError as exc:
76 |         return 127, f"Command not found: {exc}"
77 |     return done.returncode, truncate(done.stdout + done.stderr, limit)
```
- Takes a list `argv` (program + arguments), the folder to run in, a timeout and an output cap.
- `subprocess.run` with a **list** (never `shell=True`, so no shell-injection), output captured as text, UTF-8 with
  undecodable bytes replaced, and a timeout.
- On timeout the child is killed and we return exit code 124 (the `timeout` convention); program not found → 127 (the
  shell convention). Otherwise the exit code and the combined, truncated output.

> Note: this limits *where* files are touched and *how long* programs run, but a program run on your machine can still
> do anything your user account can. That is why `tools.py` asks for approval before running anything, and why
> `--sandbox docker` exists (see `runners.md`).
