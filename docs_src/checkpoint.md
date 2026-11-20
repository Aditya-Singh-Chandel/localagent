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
@@ localagent/checkpoint.py header @@
- `hashlib` names file contents by their SHA-256 hash; `dataclass` builds `Change`; `walk_files` skips caches and
  dependency folders.
- `MAX_FILE_BYTES` ignores files over 1 MB so a stray data file cannot exhaust memory.
- `MAX_SNAPSHOT_FILES` (3000) and `MAX_SNAPSHOT_BYTES` (~60 MB) protect against huge folders. Started in your home
  folder or a giant repository, snapshots would otherwise read gigabytes. Above the limits `/undo` switches itself off
  once, with a message suggesting git — the professional tool for undo at that size.

## `TooLarge` and `Change`
@@ localagent/checkpoint.py TooLarge @@
- Raised by `_scan` the moment either limit is exceeded, so the scan stops early instead of reading everything first.

@@ localagent/checkpoint.py Change @@
- One undo step: a label (the start of the request, shown by `/undo`) and, for each changed path, the content id
  before and after the request — `None` meaning "no file" (created or deleted by the request).

## `Checkpoints`
@@ localagent/checkpoint.py Checkpoints @@
@@ localagent/checkpoint.py Checkpoints.__init__ @@
- Keeps the absolute workspace root, how many steps to remember (`keep`), the size limits (adjustable, e.g. by tests),
  `_blobs` (each distinct file content once, by hash), `_seen` (per file: size, modification time and content id from
  the last scan), `_open` (the running request's label and "before" state), the stack of steps, and `disabled`, the
  reason once the folder proved too large.

@@ localagent/checkpoint.py Checkpoints._scan @@
- Returns `{relative path: content id}` for every small file found by `walk_files`, counting files and bytes; past a
  limit it raises `TooLarge`.
- A file whose size and modification time (in nanoseconds) are unchanged since the last scan keeps its content id
  **without being read** — so after the first scan, a snapshot reads only the files that changed. New contents are
  hashed and stored once in `_blobs` (`setdefault`: an identical content already stored is not stored again). Reading
  raw bytes means any file type is restored exactly. A file that disappears during the scan is skipped.

@@ localagent/checkpoint.py Checkpoints._switch_off @@
- Turns snapshots off for good, frees their memory and returns the one-time message.

@@ localagent/checkpoint.py Checkpoints.take @@
- Closes a request left open (`commit`), then notes the current state with the request's label. When the folder is too
  large, `/undo` is switched off and the message is returned **once**; later calls return `None` silently.

@@ localagent/checkpoint.py Checkpoints.commit @@
- Compares the noted "before" with the state now. The paths whose content differs — changed, created or deleted —
  become one `Change` with only those paths, kept on the stack (the newest `keep`). A request that changed nothing
  adds nothing. Then unused contents are forgotten.

@@ localagent/checkpoint.py Checkpoints._forget_unused @@
- Keeps a stored content only while something needs it: a current file (it will be the "before" of the next request),
  an undo step, or the open request. Everything else is dropped, so memory holds about one copy of the project plus
  the contents the undo steps need.

@@ localagent/checkpoint.py Checkpoints.undo @@
- When switched off it explains why. It first closes an open request (so changes since the last request began count).
  With no steps it says `nothing to undo`.
- Otherwise it takes the newest step and the current state. For each path in the step: if its content now is **not**
  what the request left there, someone changed it since — it is left alone and listed. Otherwise a file the request
  created is deleted, and a changed or deleted file gets its old content back (recreating folders if needed).
- The message reports what was restored and removed, which request the step came from, and any files left alone, e.g.
  `undone: restored 1 file(s), removed 1 new file(s) (from "add a --json flag"); left 1 file(s) alone because they
  changed since: app.py`.
