"""Session log: every conversation message is appended to a JSONL file.

One JSON object per line makes the log crash-safe (a partial last line only
loses one message), greppable, and easy to resume from or analyse later.
Besides messages, the log holds event lines such as ``{"event": "reset"}``:
``--resume`` continues after the last one, so a conversation you cleared with
/reset, or that /compact replaced by a summary, never comes back.
"""
from __future__ import annotations

import json
import time
from pathlib import Path


class SessionLog:
    """Append-only writer for one session's messages."""

    def __init__(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / f"session-{time.strftime('%Y%m%d-%H%M%S')}.jsonl"

    def append(self, message: dict) -> None:
        """Write one message as a single JSON line."""
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(message, ensure_ascii=False) + "\n")

    def mark(self, event: str) -> None:
        """Record that the conversation started over ("reset" or "compact")."""
        self.append({"event": event, "time": time.strftime("%Y-%m-%d %H:%M:%S")})


def list_sessions(directory: Path) -> list[Path]:
    """Every session file in ``directory``, oldest first (names sort by timestamp)."""
    return sorted(directory.glob("session-*.jsonl")) if directory.is_dir() else []


def latest_session(directory: Path) -> Path | None:
    """Newest session file in ``directory``, or None."""
    files = list_sessions(directory)
    return files[-1] if files else None


def _items(path: Path) -> list[dict]:
    """Every readable line of a log; a line cut off mid-write (a crash) is skipped."""
    items = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if isinstance(item, dict):
            items.append(item)
    return items


def load_session(path: Path) -> list[dict]:
    """The messages to continue with: those after the last reset/compact event."""
    messages: list[dict] = []
    for item in _items(path):
        if "event" in item:
            messages = []                           # everything before it was cleared
        else:
            messages.append(item)
    return messages


def session_title(path: Path) -> str:
    """A one-line description of a session for the /resume list: when, and its first request."""
    first = next((m.get("content", "") for m in _items(path)
                  if m.get("role") == "user"), "") or "(empty)"
    stamp = path.stem.removeprefix("session-")
    when = f"{stamp[:4]}-{stamp[4:6]}-{stamp[6:8]} {stamp[9:11]}:{stamp[11:13]}"
    return f"{when}  {' '.join(str(first).split())[:70]}"
