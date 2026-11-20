# `localagent/session.py` — conversation log and resume

**Role in the flow:** `Agent._add` writes every message to a `.jsonl` file under `.localagent/sessions/`. `--resume`
(or `-c`) loads the newest file back into a new `Agent`; `/resume` lists the project's conversations and continues
any of them. The log doubles as an audit trail and as raw material for analysing where tokens and time go.

## Module header
```python
 1 | """Session log: every conversation message is appended to a JSONL file.
 2 | 
 3 | One JSON object per line makes the log crash-safe (a partial last line only
 4 | loses one message), greppable, and easy to resume from or analyse later.
 5 | Besides messages, the log holds event lines such as ``{"event": "reset"}``:
 6 | ``--resume`` continues after the last one, so a conversation you cleared with
 7 | /reset, or that /compact replaced by a summary, never comes back.
 8 | """
 9 | from __future__ import annotations
10 | 
11 | import json
12 | import time
13 | from pathlib import Path
14 | 
15 | 
```
- One JSON object per line is crash-safe: if the program dies mid-write only the last line can be damaged.
- Besides messages the log holds **event lines** such as `{"event": "reset"}`. They exist because of a bug found in
  version 0.6's review: `/reset` only cleared the conversation in memory, so `--resume` brought the "forgotten"
  conversation straight back; and after `/compact`, resuming restored the whole long history *plus* the summary, so the
  next request overflowed the context. Resuming now starts after the last event.

## `SessionLog`
```python
16 | class SessionLog:
17 |     """Append-only writer for one session's messages."""
18 | 
```
```python
19 |     def __init__(self, directory: Path) -> None:
20 |         directory.mkdir(parents=True, exist_ok=True)
21 |         self.path = directory / f"session-{time.strftime('%Y%m%d-%H%M%S')}.jsonl"
22 | 
```
- Creates the folder if needed and picks a filename from the current time (`session-YYYYmmdd-HHMMSS.jsonl`); such
  names sort in chronological order. The file itself appears with the first message.

```python
23 |     def append(self, message: dict) -> None:
24 |         """Write one message as a single JSON line."""
25 |         with self.path.open("a", encoding="utf-8") as handle:
26 |             handle.write(json.dumps(message, ensure_ascii=False) + "\n")
27 | 
```
- Opens the file in append mode each time (so nothing stays buffered if the program crashes) and writes one line.
  `ensure_ascii=False` keeps non-English text readable.

```python
28 |     def mark(self, event: str) -> None:
29 |         """Record that the conversation started over ("reset" or "compact")."""
30 |         self.append({"event": event, "time": time.strftime("%Y-%m-%d %H:%M:%S")})
31 | 
32 | 
```
- Writes an event line (`"reset"` from `Agent.reset`, `"compact"` from `Agent.compact`) with the time it happened.

## Finding sessions
```python
33 | def list_sessions(directory: Path) -> list[Path]:
34 |     """Every session file in ``directory``, oldest first (names sort by timestamp)."""
35 |     return sorted(directory.glob("session-*.jsonl")) if directory.is_dir() else []
36 | 
37 | 
```
```python
38 | def latest_session(directory: Path) -> Path | None:
39 |     """Newest session file in ``directory``, or None."""
40 |     files = list_sessions(directory)
41 |     return files[-1] if files else None
42 | 
43 | 
```
- `list_sessions`: every session file, oldest first (the timestamped names sort that way); an empty list when the
  folder does not exist yet. `latest_session`: the last of them, or `None`.

## Reading a session back
```python
44 | def _items(path: Path) -> list[dict]:
45 |     """Every readable line of a log; a line cut off mid-write (a crash) is skipped."""
46 |     items = []
47 |     for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
48 |         try:
49 |             item = json.loads(line)
50 |         except ValueError:
51 |             continue
52 |         if isinstance(item, dict):
53 |             items.append(item)
54 |     return items
55 | 
56 | 
```
- Every line that parses as a JSON object. A line that does not — the program was stopped while writing it — is
  skipped. (Before 0.6 such a line made `--resume` crash with a traceback, although the header promised crash-safety.)

```python
57 | def load_session(path: Path) -> list[dict]:
58 |     """The messages to continue with: those after the last reset/compact event."""
59 |     messages: list[dict] = []
60 |     for item in _items(path):
61 |         if "event" in item:
62 |             messages = []                           # everything before it was cleared
63 |         else:
64 |             messages.append(item)
65 |     return messages
66 | 
67 | 
```
- Collects messages, starting over at every event line, so only what came after the last reset or compaction is
  returned. After a compaction that is the summary and everything since.

```python
68 | def session_title(path: Path) -> str:
69 |     """A one-line description of a session for the /resume list: when, and its first request."""
70 |     first = next((m.get("content", "") for m in _items(path)
71 |                   if m.get("role") == "user"), "") or "(empty)"
72 |     stamp = path.stem.removeprefix("session-")
73 |     when = f"{stamp[:4]}-{stamp[4:6]}-{stamp[6:8]} {stamp[9:11]}:{stamp[11:13]}"
74 |     return f"{when}  {' '.join(str(first).split())[:70]}"
```
- One line per conversation for `/resume`: the date and time from the file name plus the first request (the very
  first one, even if the conversation was later compacted — that is what you remember it by), with its whitespace
  squeezed and cut to 70 characters.
