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
 16 | from dataclasses import dataclass
 17 | from pathlib import Path
 18 | 
 19 | from .sandbox import walk_files
 20 | 
 21 | MAX_FILE_BYTES = 1_000_000
 22 | MAX_SNAPSHOT_FILES = 3000          # above this, snapshots are switched off (use git instead)
 23 | MAX_SNAPSHOT_BYTES = 60_000_000    # ~60 MB of file contents
 24 | 
 25 | 
```
- `hashlib` names file contents by their SHA-256 hash; `dataclass` builds `Change`; `walk_files` skips caches and
  dependency folders.
- `MAX_FILE_BYTES` ignores files over 1 MB so a stray data file cannot exhaust memory.
- `MAX_SNAPSHOT_FILES` (3000) and `MAX_SNAPSHOT_BYTES` (~60 MB) protect against huge folders. Started in your home
  folder or a giant repository, snapshots would otherwise read gigabytes. Above the limits `/undo` switches itself off
  once, with a message suggesting git — the professional tool for undo at that size.

## `TooLarge` and `Change`
```python
 26 | class TooLarge(Exception):
 27 |     """The folder is too big to snapshot in memory."""
 28 | 
 29 | 
```
- Raised by `_scan` the moment either limit is exceeded, so the scan stops early instead of reading everything first.

```python
 30 | @dataclass
 31 | class Change:
 32 |     """What one request changed: each path's content id before and after (None = no file)."""
 33 | 
 34 |     label: str                              # the start of the request, shown by /undo
 35 |     before: dict[str, str | None]
 36 |     after: dict[str, str | None]
 37 | 
 38 | 
```
- One undo step: a label (the start of the request, shown by `/undo`) and, for each changed path, the content id
  before and after the request — `None` meaning "no file" (created or deleted by the request).

## `Checkpoints`
```python
 39 | class Checkpoints:
 40 |     """The undo steps of a session, newest last."""
 41 | 
```
```python
 42 |     def __init__(self, root: Path, keep: int = 20, max_files: int = MAX_SNAPSHOT_FILES,
 43 |                  max_bytes: int = MAX_SNAPSHOT_BYTES) -> None:
 44 |         self._root = root.resolve()
 45 |         self._keep = keep
 46 |         self._max_files, self._max_bytes = max_files, max_bytes
 47 |         self._blobs: dict[str, bytes] = {}                    # content id -> content
 48 |         self._seen: dict[str, tuple[int, int, str]] = {}       # path -> (size, mtime, content id)
 49 |         self._open: tuple[str, dict[str, str]] | None = None  # the running request's "before"
 50 |         self._stack: list[Change] = []
 51 |         self.disabled = ""                        # reason, once the folder proved too large
 52 | 
```
- Keeps the absolute workspace root, how many steps to remember (`keep`), the size limits (adjustable, e.g. by tests),
  `_blobs` (each distinct file content once, by hash), `_seen` (per file: size, modification time and content id from
  the last scan), `_open` (the running request's label and "before" state), the stack of steps, and `disabled`, the
  reason once the folder proved too large.

```python
 53 |     def _scan(self) -> dict[str, str]:
 54 |         """{relative path: content id} of every small workspace file; reads only changed files."""
 55 |         snapshot: dict[str, str] = {}
 56 |         seen: dict[str, tuple[int, int, str]] = {}
 57 |         total = 0
 58 |         for path in walk_files(self._root):
 59 |             try:
 60 |                 stat = path.stat()
 61 |             except OSError:
 62 |                 continue                                       # deleted while we looked
 63 |             if stat.st_size > MAX_FILE_BYTES:
 64 |                 continue
 65 |             total += stat.st_size
 66 |             if len(snapshot) >= self._max_files or total > self._max_bytes:
 67 |                 raise TooLarge(f"more than {self._max_files} files or "
 68 |                                f"{self._max_bytes // 1_000_000} MB")
 69 |             key = path.relative_to(self._root).as_posix()
 70 |             known = self._seen.get(key)
 71 |             if known is not None and known[:2] == (stat.st_size, stat.st_mtime_ns):
 72 |                 digest = known[2]                              # unchanged: no need to read it
 73 |             else:
 74 |                 try:
 75 |                     data = path.read_bytes()
 76 |                 except OSError:
 77 |                     continue
 78 |                 digest = hashlib.sha256(data).hexdigest()
 79 |                 self._blobs.setdefault(digest, data)
 80 |             snapshot[key] = digest
 81 |             seen[key] = (stat.st_size, stat.st_mtime_ns, digest)
 82 |         self._seen = seen
 83 |         return snapshot
 84 | 
```
- Returns `{relative path: content id}` for every small file found by `walk_files`, counting files and bytes; past a
  limit it raises `TooLarge`.
- A file whose size and modification time (in nanoseconds) are unchanged since the last scan keeps its content id
  **without being read** — so after the first scan, a snapshot reads only the files that changed. New contents are
  hashed and stored once in `_blobs` (`setdefault`: an identical content already stored is not stored again). Reading
  raw bytes means any file type is restored exactly. A file that disappears during the scan is skipped.

```python
 85 |     def _switch_off(self, reason: TooLarge) -> str:
 86 |         """Stop taking snapshots for good and free their memory."""
 87 |         self.disabled = f"the folder is too large to snapshot ({reason}); use git to undo"
 88 |         self._stack.clear()
 89 |         self._blobs.clear()
 90 |         self._seen.clear()
 91 |         self._open = None
 92 |         return f"/undo is off: {self.disabled}"
 93 | 
```
- Turns snapshots off for good, frees their memory and returns the one-time message.

```python
 94 |     def take(self, label: str = "") -> str | None:
 95 |         """Note the state before a request. Returns a message only at the moment /undo is
 96 |         switched off because the folder is too large (later calls stay silent)."""
 97 |         if self.disabled:
 98 |             return None
 99 |         self.commit()                                          # close a request left open
100 |         try:
101 |             self._open = (label, self._scan())
102 |         except TooLarge as exc:
103 |             return self._switch_off(exc)
104 |         return None
105 | 
```
- Closes a request left open (`commit`), then notes the current state with the request's label. When the folder is too
  large, `/undo` is switched off and the message is returned **once**; later calls return `None` silently.

```python
106 |     def commit(self) -> None:
107 |         """After a request: keep what it changed as one undo step (nothing changed: no step)."""
108 |         if self._open is None or self.disabled:
109 |             return
110 |         label, before = self._open
111 |         self._open = None
112 |         try:
113 |             after = self._scan()
114 |         except TooLarge as exc:
115 |             self._switch_off(exc)
116 |             return
117 |         changed = {p for p in before.keys() | after.keys() if before.get(p) != after.get(p)}
118 |         if changed:
119 |             self._stack.append(Change(label, {p: before.get(p) for p in changed},
120 |                                       {p: after.get(p) for p in changed}))
121 |             del self._stack[:-self._keep]                       # keep only the newest ``keep``
122 |         self._forget_unused()
123 | 
```
- Compares the noted "before" with the state now. The paths whose content differs — changed, created or deleted —
  become one `Change` with only those paths, kept on the stack (the newest `keep`). A request that changed nothing
  adds nothing. Then unused contents are forgotten.

```python
124 |     def _forget_unused(self) -> None:
125 |         """Drop stored contents that neither the current files nor any undo step need."""
126 |         used = {entry[2] for entry in self._seen.values()}
127 |         for change in self._stack:
128 |             used.update(d for d in (*change.before.values(), *change.after.values()) if d)
129 |         if self._open is not None:
130 |             used.update(self._open[1].values())
131 |         self._blobs = {digest: data for digest, data in self._blobs.items() if digest in used}
132 | 
```
- Keeps a stored content only while something needs it: a current file (it will be the "before" of the next request),
  an undo step, or the open request. Everything else is dropped, so memory holds about one copy of the project plus
  the contents the undo steps need.

```python
133 |     def undo(self) -> str:
134 |         """Put back the newest change and describe what happened."""
135 |         if self.disabled:
136 |             return f"undo is off: {self.disabled}"
137 |         self.commit()                                          # include a request left open
138 |         if not self._stack:
139 |             return "nothing to undo"
140 |         change = self._stack.pop()
141 |         current = self._scan()
142 |         restored = removed = 0
143 |         kept: list[str] = []
144 |         for relative, old in sorted(change.before.items()):
145 |             if current.get(relative) != change.after[relative]:
146 |                 kept.append(relative)                          # changed since: yours now
147 |                 continue
148 |             target = self._root / relative
149 |             if old is None:
150 |                 target.unlink(missing_ok=True)                 # the request created it
151 |                 removed += 1
152 |             else:
153 |                 target.parent.mkdir(parents=True, exist_ok=True)
154 |                 target.write_bytes(self._blobs[old])
155 |                 restored += 1
156 |         self._scan()                                           # the files just written
157 |         self._forget_unused()
158 |         message = f"undone: restored {restored} file(s), removed {removed} new file(s)"
159 |         if change.label:
160 |             message += f' (from "{change.label}")'
161 |         if kept:
162 |             message += (f"; left {len(kept)} file(s) alone because they changed since: "
163 |                         + ", ".join(kept[:5]))
164 |         return message
```
- When switched off it explains why. It first closes an open request (so changes since the last request began count).
  With no steps it says `nothing to undo`.
- Otherwise it takes the newest step and the current state. For each path in the step: if its content now is **not**
  what the request left there, someone changed it since — it is left alone and listed. Otherwise a file the request
  created is deleted, and a changed or deleted file gets its old content back (recreating folders if needed).
- The message reports what was restored and removed, which request the step came from, and any files left alone, e.g.
  `undone: restored 1 file(s), removed 1 new file(s) (from "add a --json flag"); left 1 file(s) alone because they
  changed since: app.py`.
