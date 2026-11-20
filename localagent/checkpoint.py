"""Undo support: snapshot the workspace before each request, restore on /undo.

Plain in-memory copies of files, so it works without git. Large files
(over 1 MB) are ignored to keep memory use small.
"""
from __future__ import annotations

from pathlib import Path

from .sandbox import walk_files

MAX_FILE_BYTES = 1_000_000
MAX_SNAPSHOT_FILES = 3000          # above this, snapshots are switched off (use git instead)
MAX_SNAPSHOT_BYTES = 60_000_000    # ~60 MB of file contents per snapshot


class TooLarge(Exception):
    """The folder is too big to snapshot in memory."""


class Checkpoints:
    """A stack of workspace snapshots."""

    def __init__(self, root: Path, keep: int = 20, max_files: int = MAX_SNAPSHOT_FILES,
                 max_bytes: int = MAX_SNAPSHOT_BYTES) -> None:
        self._root = root.resolve()
        self._keep = keep
        self._max_files, self._max_bytes = max_files, max_bytes
        self._stack: list[dict[str, bytes]] = []
        self.disabled = ""                        # reason, once the folder proved too large

    def _scan(self) -> dict[str, bytes]:
        """Read every small workspace file into a {relative path: bytes} dict."""
        files: dict[str, bytes] = {}
        total = 0
        for path in walk_files(self._root):
            size = path.stat().st_size
            if size <= MAX_FILE_BYTES:
                total += size
                if len(files) >= self._max_files or total > self._max_bytes:
                    raise TooLarge(f"more than {self._max_files} files or "
                                   f"{self._max_bytes // 1_000_000} MB")
                files[path.relative_to(self._root).as_posix()] = path.read_bytes()
        return files

    def take(self) -> str | None:
        """Remember the current state. Returns a message only at the moment snapshots are
        switched off because the folder is too large (later calls stay silent)."""
        if self.disabled:
            return None
        try:
            snapshot = self._scan()
        except TooLarge as exc:
            self.disabled = f"the folder is too large to snapshot ({exc}); use git to undo"
            self._stack.clear()
            return f"/undo is off: {self.disabled}"
        self._stack.append(snapshot)
        del self._stack[:-self._keep]                    # keep only the newest ``keep``
        return None

    def undo(self) -> str:
        """Restore the newest snapshot and describe what changed."""
        if self.disabled:
            return f"undo is off: {self.disabled}"
        if not self._stack:
            return "nothing to undo"
        snapshot = self._stack.pop()
        current = self._scan()
        removed = 0
        for relative in set(current) - set(snapshot):      # files the agent created
            (self._root / relative).unlink()
            removed += 1
        restored = 0
        for relative, data in snapshot.items():            # files changed or deleted
            if current.get(relative) != data:
                target = self._root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
                restored += 1
        return f"undone: restored {restored} file(s), removed {removed} new file(s)"
