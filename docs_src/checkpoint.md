# `localagent/checkpoint.py` — `/undo`

**Role in the flow:** before each user request the CLI calls `take()`; if you dislike the result, `/undo` calls
`undo()` and the workspace returns to how it was. Snapshots are plain in-memory copies, so no git repo is needed.

## Module header
@@ localagent/checkpoint.py header @@
- `MAX_FILE_BYTES` ignores files over 1 MB so a stray data file cannot exhaust memory. `walk_files` skips caches and
  dependency folders.
- `MAX_SNAPSHOT_FILES` (3000) and `MAX_SNAPSHOT_BYTES` (~60 MB) protect against huge folders. Started in your home
  folder or a giant repository, snapshots would otherwise copy gigabytes into memory before every request. Above the
  limits `/undo` switches itself off once, with a message suggesting git — the professional tool for undo at that size.

## `TooLarge`
@@ localagent/checkpoint.py TooLarge @@
- Raised by `_scan` the moment either limit is exceeded, so the scan stops early instead of reading everything first.

## `Checkpoints`
@@ localagent/checkpoint.py Checkpoints @@
@@ localagent/checkpoint.py Checkpoints.__init__ @@
- Keeps the absolute workspace root, how many snapshots to remember (`keep`), the size limits (adjustable, e.g. by
  tests), the stack of snapshots — each a `{relative path: file bytes}` dictionary — and `disabled`, the reason once
  the folder proved too large.

@@ localagent/checkpoint.py Checkpoints._scan @@
- Reads every small file found by `walk_files` (which never enters `node_modules`, `.venv` and similar) into a
  dictionary keyed by forward-slash relative path, counting files and bytes; past a limit it raises `TooLarge`. Reading raw bytes means any file type is restored exactly.

@@ localagent/checkpoint.py Checkpoints.take @@
- Pushes a fresh scan. `del self._stack[:-self._keep]` removes everything except the newest `keep` snapshots.
- If the scan is too large, snapshots are switched off for good (the stack is emptied) and the message is returned
  **once**; later calls return `None` silently. Normal calls return `None`.

@@ localagent/checkpoint.py Checkpoints.undo @@
- When switched off it explains why; with an empty stack it says so. Otherwise it pops the newest snapshot and scans the current state.
- Files present now but absent from the snapshot were **created** by the agent: they are deleted.
- Files in the snapshot whose current bytes differ (or that no longer exist) were **changed or deleted**: they are
  rewritten, recreating folders if needed. The return message reports both counts.
