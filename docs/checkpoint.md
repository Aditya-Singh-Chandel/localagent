# `localagent/checkpoint.py` — `/undo`

**Role in the flow:** before each user request the CLI calls `take()`; if you dislike the result, `/undo` calls
`undo()` and the workspace returns to how it was. Snapshots are plain in-memory copies, so no git repo is needed.

## Module header
```python
 1 | """Undo support: snapshot the workspace before each request, restore on /undo.
 2 | 
 3 | Plain in-memory copies of files, so it works without git. Large files
 4 | (over 1 MB) are ignored to keep memory use small.
 5 | """
 6 | from __future__ import annotations
 7 | 
 8 | from pathlib import Path
 9 | 
10 | from .sandbox import walk_files
11 | 
12 | MAX_FILE_BYTES = 1_000_000
13 | MAX_SNAPSHOT_FILES = 3000          # above this, snapshots are switched off (use git instead)
14 | MAX_SNAPSHOT_BYTES = 60_000_000    # ~60 MB of file contents per snapshot
15 | 
16 | 
```
- `MAX_FILE_BYTES` ignores files over 1 MB so a stray data file cannot exhaust memory. `walk_files` skips caches and
  dependency folders.
- `MAX_SNAPSHOT_FILES` (3000) and `MAX_SNAPSHOT_BYTES` (~60 MB) protect against huge folders. Started in your home
  folder or a giant repository, snapshots would otherwise copy gigabytes into memory before every request. Above the
  limits `/undo` switches itself off once, with a message suggesting git — the professional tool for undo at that size.

## `TooLarge`
```python
17 | class TooLarge(Exception):
18 |     """The folder is too big to snapshot in memory."""
19 | 
20 | 
```
- Raised by `_scan` the moment either limit is exceeded, so the scan stops early instead of reading everything first.

## `Checkpoints`
```python
21 | class Checkpoints:
22 |     """A stack of workspace snapshots."""
23 | 
```
```python
24 |     def __init__(self, root: Path, keep: int = 20, max_files: int = MAX_SNAPSHOT_FILES,
25 |                  max_bytes: int = MAX_SNAPSHOT_BYTES) -> None:
26 |         self._root = root.resolve()
27 |         self._keep = keep
28 |         self._max_files, self._max_bytes = max_files, max_bytes
29 |         self._stack: list[dict[str, bytes]] = []
30 |         self.disabled = ""                        # reason, once the folder proved too large
31 | 
```
- Keeps the absolute workspace root, how many snapshots to remember (`keep`), the size limits (adjustable, e.g. by
  tests), the stack of snapshots — each a `{relative path: file bytes}` dictionary — and `disabled`, the reason once
  the folder proved too large.

```python
32 |     def _scan(self) -> dict[str, bytes]:
33 |         """Read every small workspace file into a {relative path: bytes} dict."""
34 |         files: dict[str, bytes] = {}
35 |         total = 0
36 |         for path in walk_files(self._root):
37 |             size = path.stat().st_size
38 |             if size <= MAX_FILE_BYTES:
39 |                 total += size
40 |                 if len(files) >= self._max_files or total > self._max_bytes:
41 |                     raise TooLarge(f"more than {self._max_files} files or "
42 |                                    f"{self._max_bytes // 1_000_000} MB")
43 |                 files[path.relative_to(self._root).as_posix()] = path.read_bytes()
44 |         return files
45 | 
```
- Reads every small file found by `walk_files` (which never enters `node_modules`, `.venv` and similar) into a
  dictionary keyed by forward-slash relative path, counting files and bytes; past a limit it raises `TooLarge`. Reading raw bytes means any file type is restored exactly.

```python
46 |     def take(self) -> str | None:
47 |         """Remember the current state. Returns a message only at the moment snapshots are
48 |         switched off because the folder is too large (later calls stay silent)."""
49 |         if self.disabled:
50 |             return None
51 |         try:
52 |             snapshot = self._scan()
53 |         except TooLarge as exc:
54 |             self.disabled = f"the folder is too large to snapshot ({exc}); use git to undo"
55 |             self._stack.clear()
56 |             return f"/undo is off: {self.disabled}"
57 |         self._stack.append(snapshot)
58 |         del self._stack[:-self._keep]                    # keep only the newest ``keep``
59 |         return None
60 | 
```
- Pushes a fresh scan. `del self._stack[:-self._keep]` removes everything except the newest `keep` snapshots.
- If the scan is too large, snapshots are switched off for good (the stack is emptied) and the message is returned
  **once**; later calls return `None` silently. Normal calls return `None`.

```python
61 |     def undo(self) -> str:
62 |         """Restore the newest snapshot and describe what changed."""
63 |         if self.disabled:
64 |             return f"undo is off: {self.disabled}"
65 |         if not self._stack:
66 |             return "nothing to undo"
67 |         snapshot = self._stack.pop()
68 |         current = self._scan()
69 |         removed = 0
70 |         for relative in set(current) - set(snapshot):      # files the agent created
71 |             (self._root / relative).unlink()
72 |             removed += 1
73 |         restored = 0
74 |         for relative, data in snapshot.items():            # files changed or deleted
75 |             if current.get(relative) != data:
76 |                 target = self._root / relative
77 |                 target.parent.mkdir(parents=True, exist_ok=True)
78 |                 target.write_bytes(data)
79 |                 restored += 1
80 |         return f"undone: restored {restored} file(s), removed {removed} new file(s)"
```
- When switched off it explains why; with an empty stack it says so. Otherwise it pops the newest snapshot and scans the current state.
- Files present now but absent from the snapshot were **created** by the agent: they are deleted.
- Files in the snapshot whose current bytes differ (or that no longer exist) were **changed or deleted**: they are
  rewritten, recreating folders if needed. The return message reports both counts.
