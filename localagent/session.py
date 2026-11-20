"""Session log: every conversation message is appended to a JSONL file.

One JSON object per line makes the log crash-safe (a partial last line only
loses one message), greppable, and easy to resume from or analyse later.
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


def latest_session(directory: Path) -> Path | None:
    """Newest session file in ``directory`` (names sort by timestamp), or None."""
    files = sorted(directory.glob("session-*.jsonl"))
    return files[-1] if files else None


def load_session(path: Path) -> list[dict]:
    """Read all messages back from a session file."""
    lines = path.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]
