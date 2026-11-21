# `localagent/checkpoint.py` — `/undo`

**Role in the flow:** before each request the CLI calls `take()`, after it `commit()`; what the request changed becomes
one **undo step**. `/undo` puts the newest step back. No git repository is needed.

**What changed in version 0.6, and why.** The old design copied the whole folder before every request and `/undo`
restored the newest copy. Three problems showed up in review:
1. A request that changed nothing (a question) was still an undo step, so `/undo` right after it seemed to do nothing
   ("restored 0 files") — the change you wanted to undo was one step further back.
2. `/undo` restored *every* file to its old state — including a file **you** had edited in your editor after the
   request finished. Your own work was silently thrown away.
3. Memory: 20 full copies of a project of up to 60 MB could take 1.2 GB, and every file was re-read before every request.

Now each step records only the files the request changed, quiet requests are not steps, a file changed again since is
left alone (and named), and every distinct file content is stored once.

## Module header
```python
  1 | """Undo support: remember what each request changed, and put it back on /undo.
  2 | 
  3 | Before a request, ``take`` notes the content of every file; afterwards, ``commit`` compares
  4 | and keeps only what changed (before and after) as one undo step. ``undo`` puts the newest
  5 | step back - except files that changed again since (you edited them yourself): those are left
  6 | alone and named, so /undo never destroys your own work. A request that changed nothing is not
  7 | an undo step. It works without git.
  8 | 
  9 | Memory stays small: each distinct file content is stored once (found by its hash) and shared
 10 | by all steps, and a file whose size and time stamp are unchanged is not read again. Files over
 11 | 1 MB are ignored; a folder with more than 3000 files or 60 MB switches /undo off.
 12 | """
 13 | from __future__ import annotations
 14 | 
 15 | import hashlib
 16 | import time
 17 | from dataclasses import dataclass
 18 | from pathlib import Path
 19 | 
 20 | from .sandbox import walk_files
 21 | 
 22 | RACY_NS = 2_000_000_000            # files changed less than 2 s before a scan are read again
 23 | MAX_FILE_BYTES = 1_000_000
 24 | MAX_SNAPSHOT_FILES = 3000          # above this, snapshots are switched off (use git instead)
 25 | MAX_SNAPSHOT_BYTES = 60_000_000    # ~60 MB of file contents
 26 | 
 27 | 
```
- `hashlib` names file contents by their SHA-256 hash; `time` tells how recent a change is; `dataclass` builds
  `Change`; `walk_files` skips caches and dependency folders.
- `RACY_NS` (2 seconds, in nanoseconds): files changed this shortly before a scan are read again next time (see
  `_scan`).
- `MAX_FILE_BYTES` ignores files over 1 MB so a stray data file cannot exhaust memory.
- `MAX_SNAPSHOT_FILES` (3000) and `MAX_SNAPSHOT_BYTES` (~60 MB) protect against huge folders. Started in your home
  folder or a giant repository, snapshots would otherwise read gigabytes. Above the limits `/undo` switches itself off
  once, with a message suggesting git — the professional tool for undo at that size.

## `TooLarge` and `Change`
```python
 28 | class TooLarge(Exception):
 29 |     """The folder is too big to snapshot in memory."""
 30 | 
 31 | 
```
- Raised by `_scan` the moment either limit is exceeded, so the scan stops early instead of reading everything first.

```python
 32 | @dataclass
 33 | class Change:
 34 |     """What one request changed: each path's content id before and after (None = no file)."""
 35 | 
 36 |     label: str                              # the start of the request, shown by /undo
 37 |     before: dict[str, str | None]
 38 |     after: dict[str, str | None]
 39 | 
 40 | 
```
- One undo step: a label (the start of the request, shown by `/undo`) and, for each changed path, the content id
  before and after the request — `None` meaning "no file" (created or deleted by the request).

## `Checkpoints`
```python
 41 | class Checkpoints:
 42 |     """The undo steps of a session, newest last."""
 43 | 
```
```python
 44 |     def __init__(self, root: Path, keep: int = 20, max_files: int = MAX_SNAPSHOT_FILES,
 45 |                  max_bytes: int = MAX_SNAPSHOT_BYTES) -> None:
 46 |         self._root = root.resolve()
 47 |         self._keep = keep
 48 |         self._max_files, self._max_bytes = max_files, max_bytes
 49 |         self._blobs: dict[str, bytes] = {}                    # content id -> content
 50 |         self._seen: dict[str, tuple[int, int, str]] = {}       # path -> (size, mtime, content id)
 51 |         self._open: tuple[str, dict[str, str]] | None = None  # the running request's "before"
 52 |         self._stack: list[Change] = []
 53 |         self.disabled = ""                        # reason, once the folder proved too large
 54 | 
```
- Keeps the absolute workspace root, how many steps to remember (`keep`), the size limits (adjustable, e.g. by tests),
  `_blobs` (each distinct file content once, by hash), `_seen` (per file: size, modification time and content id from
  the last scan), `_open` (the running request's label and "before" state), the stack of steps, and `disabled`, the
  reason once the folder proved too large.

```python
 55 |     def _scan(self) -> dict[str, str]:
 56 |         """{relative path: content id} of every small workspace file; reads only changed files.
 57 | 
 58 |         A file changed in the last ``RACY_NS`` before the scan is remembered as "not to be
 59 |         trusted" (time stamp -1): a second edit within the same clock tick, with the same size,
 60 |         would otherwise look unchanged - Windows' time stamps are coarse (git calls this
 61 |         "racily clean" and re-reads such files too).
 62 |         """
 63 |         snapshot: dict[str, str] = {}
 64 |         seen: dict[str, tuple[int, int, str]] = {}
 65 |         total = 0
 66 |         scan_ns = time.time_ns()
 67 |         for path in walk_files(self._root):
 68 |             try:
 69 |                 stat = path.stat()
 70 |             except OSError:
 71 |                 continue                                       # deleted while we looked
 72 |             if stat.st_size > MAX_FILE_BYTES:
 73 |                 continue
 74 |             total += stat.st_size
 75 |             if len(snapshot) >= self._max_files or total > self._max_bytes:
 76 |                 raise TooLarge(f"more than {self._max_files} files or "
 77 |                                f"{self._max_bytes // 1_000_000} MB")
 78 |             key = path.relative_to(self._root).as_posix()
 79 |             known = self._seen.get(key)
 80 |             if known is not None and known[:2] == (stat.st_size, stat.st_mtime_ns):
 81 |                 digest = known[2]                              # unchanged: no need to read it
 82 |             else:
 83 |                 try:
 84 |                     data = path.read_bytes()
 85 |                 except OSError:
 86 |                     continue
 87 |                 digest = hashlib.sha256(data).hexdigest()
 88 |                 self._blobs.setdefault(digest, data)
 89 |             snapshot[key] = digest
 90 |             racy = scan_ns - stat.st_mtime_ns < RACY_NS
 91 |             seen[key] = (stat.st_size, -1 if racy else stat.st_mtime_ns, digest)
 92 |         self._seen = seen
 93 |         return snapshot
 94 | 
```
- Returns `{relative path: content id}` for every small file found by `walk_files`, counting files and bytes; past a
  limit it raises `TooLarge`.
- A file whose size and modification time (in nanoseconds) are unchanged since the last scan keeps its content id
  **without being read** — so after the first scan, a snapshot reads only the files that changed. New contents are
  hashed and stored once in `_blobs` (`setdefault`: an identical content already stored is not stored again). Reading
  raw bytes means any file type is restored exactly. A file that disappears during the scan is skipped.
- **"Racily clean" files.** A file changed less than `RACY_NS` before the scan is remembered with the time stamp `-1`,
  which never matches, so the next scan reads it again. Without this, a second edit inside the same clock tick that
  keeps the size the same looks unchanged — Windows updates time stamps coarsely. Found by the first CI run on a
  Windows machine (`/undo` left a changed file as it was); git has the same problem and the same cure. Only files
  changed in the last two seconds are re-read, so the speed-up stays.

```python
 95 |     def _switch_off(self, reason: TooLarge) -> str:
 96 |         """Stop taking snapshots for good and free their memory."""
 97 |         self.disabled = f"the folder is too large to snapshot ({reason}); use git to undo"
 98 |         self._stack.clear()
 99 |         self._blobs.clear()
100 |         self._seen.clear()
101 |         self._open = None
102 |         return f"/undo is off: {self.disabled}"
103 | 
```
- Turns snapshots off for good, frees their memory and returns the one-time message.

```python
104 |     def take(self, label: str = "") -> str | None:
105 |         """Note the state before a request. Returns a message only at the moment /undo is
106 |         switched off because the folder is too large (later calls stay silent)."""
107 |         if self.disabled:
108 |             return None
109 |         self.commit()                                          # close a request left open
110 |         try:
111 |             self._open = (label, self._scan())
112 |         except TooLarge as exc:
113 |             return self._switch_off(exc)
114 |         return None
115 | 
```
- Closes a request left open (`commit`), then notes the current state with the request's label. When the folder is too
  large, `/undo` is switched off and the message is returned **once**; later calls return `None` silently.

```python
116 |     def commit(self) -> None:
117 |         """After a request: keep what it changed as one undo step (nothing changed: no step)."""
118 |         if self._open is None or self.disabled:
119 |             return
120 |         label, before = self._open
121 |         self._open = None
122 |         try:
123 |             after = self._scan()
124 |         except TooLarge as exc:
125 |             self._switch_off(exc)
126 |             return
127 |         changed = {p for p in before.keys() | after.keys() if before.get(p) != after.get(p)}
128 |         if changed:
129 |             self._stack.append(Change(label, {p: before.get(p) for p in changed},
130 |                                       {p: after.get(p) for p in changed}))
131 |             del self._stack[:-self._keep]                       # keep only the newest ``keep``
132 |         self._forget_unused()
133 | 
```
- Compares the noted "before" with the state now. The paths whose content differs — changed, created or deleted —
  become one `Change` with only those paths, kept on the stack (the newest `keep`). A request that changed nothing
  adds nothing. Then unused contents are forgotten.

```python
134 |     def _forget_unused(self) -> None:
135 |         """Drop stored contents that neither the current files nor any undo step need."""
136 |         used = {entry[2] for entry in self._seen.values()}
137 |         for change in self._stack:
138 |             used.update(d for d in (*change.before.values(), *change.after.values()) if d)
139 |         if self._open is not None:
140 |             used.update(self._open[1].values())
141 |         self._blobs = {digest: data for digest, data in self._blobs.items() if digest in used}
142 | 
```
- Keeps a stored content only while something needs it: a current file (it will be the "before" of the next request),
  an undo step, or the open request. Everything else is dropped, so memory holds about one copy of the project plus
  the contents the undo steps need.

```python
143 |     def undo(self) -> str:
144 |         """Put back the newest change and describe what happened."""
145 |         if self.disabled:
146 |             return f"undo is off: {self.disabled}"
147 |         self.commit()                                          # include a request left open
148 |         if not self._stack:
149 |             return "nothing to undo"
150 |         change = self._stack.pop()
151 |         current = self._scan()
152 |         restored = removed = 0
153 |         kept: list[str] = []
154 |         for relative, old in sorted(change.before.items()):
155 |             if current.get(relative) != change.after[relative]:
156 |                 kept.append(relative)                          # changed since: yours now
157 |                 continue
158 |             target = self._root / relative
159 |             if old is None:
160 |                 target.unlink(missing_ok=True)                 # the request created it
161 |                 removed += 1
162 |             else:
163 |                 target.parent.mkdir(parents=True, exist_ok=True)
164 |                 target.write_bytes(self._blobs[old])
165 |                 restored += 1
166 |         self._scan()                                           # the files just written
167 |         self._forget_unused()
168 |         message = f"undone: restored {restored} file(s), removed {removed} new file(s)"
169 |         if change.label:
170 |             message += f' (from "{change.label}")'
171 |         if kept:
172 |             message += (f"; left {len(kept)} file(s) alone because they changed since: "
173 |                         + ", ".join(kept[:5]))
174 |         return message
```
- When switched off it explains why. It first closes an open request (so changes since the last request began count).
  With no steps it says `nothing to undo`.
- Otherwise it takes the newest step and the current state. For each path in the step: if its content now is **not**
  what the request left there, someone changed it since — it is left alone and listed. Otherwise a file the request
  created is deleted, and a changed or deleted file gets its old content back (recreating folders if needed).
- The message reports what was restored and removed, which request the step came from, and any files left alone, e.g.
  `undone: restored 1 file(s), removed 1 new file(s) (from "add a --json flag"); left 1 file(s) alone because they
  changed since: app.py`.
