# `localagent/session.py` — conversation log and resume

**Role in the flow:** `Agent._add` writes every message to a `.jsonl` file under `.localagent/sessions/`. `--resume`
loads the newest file back into a new `Agent`. The log doubles as an audit trail and as raw material for analysing
where tokens and time go.

## Module header
```python
 1 | """Session log: every conversation message is appended to a JSONL file.
 2 | 
 3 | One JSON object per line makes the log crash-safe (a partial last line only
 4 | loses one message), greppable, and easy to resume from or analyse later.
 5 | """
 6 | from __future__ import annotations
 7 | 
 8 | import json
 9 | import time
10 | from pathlib import Path
11 | 
12 | 
```
- One JSON object per line is crash-safe: if the program dies mid-write only the last line can be damaged.

## `SessionLog`
```python
13 | class SessionLog:
14 |     """Append-only writer for one session's messages."""
15 | 
```
```python
16 |     def __init__(self, directory: Path) -> None:
17 |         directory.mkdir(parents=True, exist_ok=True)
18 |         self.path = directory / f"session-{time.strftime('%Y%m%d-%H%M%S')}.jsonl"
19 | 
```
- Creates the folder if needed and picks a filename from the current time (`session-YYYYmmdd-HHMMSS.jsonl`); such
  names sort in chronological order.

```python
20 |     def append(self, message: dict) -> None:
21 |         """Write one message as a single JSON line."""
22 |         with self.path.open("a", encoding="utf-8") as handle:
23 |             handle.write(json.dumps(message, ensure_ascii=False) + "\n")
24 | 
25 | 
```
- Opens the file in append mode each time (so nothing stays buffered if the program crashes) and writes one line.
  `ensure_ascii=False` keeps non-English text readable.

## `latest_session`
```python
26 | def latest_session(directory: Path) -> Path | None:
27 |     """Newest session file in ``directory`` (names sort by timestamp), or None."""
28 |     files = sorted(directory.glob("session-*.jsonl"))
29 |     return files[-1] if files else None
30 | 
31 | 
```
- Sorted filenames end with the newest; returns `None` when there are no sessions yet.

## `load_session`
```python
32 | def load_session(path: Path) -> list[dict]:
33 |     """Read all messages back from a session file."""
34 |     lines = path.read_text(encoding="utf-8").splitlines()
35 |     return [json.loads(line) for line in lines if line.strip()]
```
- Reads the file, ignores blank lines, and parses each line back into a message dictionary.
