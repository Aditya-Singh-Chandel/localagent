"""Undo support: remember what each request changed, and put it back on /undo.

Before a request, ``take`` notes the content of every file; afterwards, ``commit`` compares
and keeps only what changed (before and after) as one undo step. ``undo`` puts the newest
step back - except files that changed again since (you edited them yourself): those are left
alone and named, so /undo never destroys your own work. A request that changed nothing is not
an undo step. It works without git.

Memory stays small: each distinct file content is stored once (found by its hash) and shared
by all steps, and a file whose size and time stamp are unchanged is not read again. Files over
1 MB are ignored; a folder with more than 3000 files or 60 MB switches /undo off.
"""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from pathlib import Path

from .sandbox import walk_files

RACY_NS = 2_000_000_000            # files changed less than 2 s before a scan are read again
MAX_FILE_BYTES = 1_000_000
MAX_SNAPSHOT_FILES = 3000          # above this, snapshots are switched off (use git instead)
MAX_SNAPSHOT_BYTES = 60_000_000    # ~60 MB of file contents


class TooLarge(Exception):
    """The folder is too big to snapshot in memory."""


@dataclass
class Change:
    """What one request changed: each path's content id before and after (None = no file)."""

    label: str                              # the start of the request, shown by /undo
    before: dict[str, str | None]
    after: dict[str, str | None]


class Checkpoints:
    """The undo steps of a session, newest last."""

    def __init__(self, root: Path, keep: int = 20, max_files: int = MAX_SNAPSHOT_FILES,
                 max_bytes: int = MAX_SNAPSHOT_BYTES) -> None:
        self._root = root.resolve()
        self._keep = keep
        self._max_files, self._max_bytes = max_files, max_bytes
        self._blobs: dict[str, bytes] = {}                    # content id -> content
        self._seen: dict[str, tuple[int, int, str]] = {}       # path -> (size, mtime, content id)
        self._open: tuple[str, dict[str, str]] | None = None  # the running request's "before"
        self._stack: list[Change] = []
        self.disabled = ""                        # reason, once the folder proved too large

    def _scan(self) -> dict[str, str]:
        """{relative path: content id} of every small workspace file; reads only changed files.

        A file changed in the last ``RACY_NS`` before the scan is remembered as "not to be
        trusted" (time stamp -1): a second edit within the same clock tick, with the same size,
        would otherwise look unchanged - Windows' time stamps are coarse (git calls this
        "racily clean" and re-reads such files too).
        """
        snapshot: dict[str, str] = {}
        seen: dict[str, tuple[int, int, str]] = {}
        total = 0
        scan_ns = time.time_ns()
        for path in walk_files(self._root):
            try:
                stat = path.stat()
            except OSError:
                continue                                       # deleted while we looked
            if stat.st_size > MAX_FILE_BYTES:
                continue
            total += stat.st_size
            if len(snapshot) >= self._max_files or total > self._max_bytes:
                raise TooLarge(f"more than {self._max_files} files or "
                               f"{self._max_bytes // 1_000_000} MB")
            key = path.relative_to(self._root).as_posix()
            known = self._seen.get(key)
            if known is not None and known[:2] == (stat.st_size, stat.st_mtime_ns):
                digest = known[2]                              # unchanged: no need to read it
            else:
                try:
                    data = path.read_bytes()
                except OSError:
                    continue
                digest = hashlib.sha256(data).hexdigest()
                self._blobs.setdefault(digest, data)
            snapshot[key] = digest
            racy = scan_ns - stat.st_mtime_ns < RACY_NS
            seen[key] = (stat.st_size, -1 if racy else stat.st_mtime_ns, digest)
        self._seen = seen
        return snapshot

    def _switch_off(self, reason: TooLarge) -> str:
        """Stop taking snapshots for good and free their memory."""
        self.disabled = f"the folder is too large to snapshot ({reason}); use git to undo"
        self._stack.clear()
        self._blobs.clear()
        self._seen.clear()
        self._open = None
        return f"/undo is off: {self.disabled}"

    def take(self, label: str = "") -> str | None:
        """Note the state before a request. Returns a message only at the moment /undo is
        switched off because the folder is too large (later calls stay silent)."""
        if self.disabled:
            return None
        self.commit()                                          # close a request left open
        try:
            self._open = (label, self._scan())
        except TooLarge as exc:
            return self._switch_off(exc)
        return None

    def commit(self) -> None:
        """After a request: keep what it changed as one undo step (nothing changed: no step)."""
        if self._open is None or self.disabled:
            return
        label, before = self._open
        self._open = None
        try:
            after = self._scan()
        except TooLarge as exc:
            self._switch_off(exc)
            return
        changed = {p for p in before.keys() | after.keys() if before.get(p) != after.get(p)}
        if changed:
            self._stack.append(Change(label, {p: before.get(p) for p in changed},
                                      {p: after.get(p) for p in changed}))
            del self._stack[:-self._keep]                       # keep only the newest ``keep``
        self._forget_unused()

    def _forget_unused(self) -> None:
        """Drop stored contents that neither the current files nor any undo step need."""
        used = {entry[2] for entry in self._seen.values()}
        for change in self._stack:
            used.update(d for d in (*change.before.values(), *change.after.values()) if d)
        if self._open is not None:
            used.update(self._open[1].values())
        self._blobs = {digest: data for digest, data in self._blobs.items() if digest in used}

    def undo(self) -> str:
        """Put back the newest change and describe what happened."""
        if self.disabled:
            return f"undo is off: {self.disabled}"
        self.commit()                                          # include a request left open
        if not self._stack:
            return "nothing to undo"
        change = self._stack.pop()
        current = self._scan()
        restored = removed = 0
        kept: list[str] = []
        for relative, old in sorted(change.before.items()):
            if current.get(relative) != change.after[relative]:
                kept.append(relative)                          # changed since: yours now
                continue
            target = self._root / relative
            if old is None:
                target.unlink(missing_ok=True)                 # the request created it
                removed += 1
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(self._blobs[old])
                restored += 1
        self._scan()                                           # the files just written
        self._forget_unused()
        message = f"undone: restored {restored} file(s), removed {removed} new file(s)"
        if change.label:
            message += f' (from "{change.label}")'
        if kept:
            message += (f"; left {len(kept)} file(s) alone because they changed since: "
                        + ", ".join(kept[:5]))
        return message
