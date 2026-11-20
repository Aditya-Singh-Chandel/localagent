# `localagent/session.py` — conversation log and resume

**Role in the flow:** `Agent._add` writes every message to a `.jsonl` file under `.localagent/sessions/`. `--resume`
loads the newest file back into a new `Agent`. The log doubles as an audit trail and as raw material for analysing
where tokens and time go.

## Module header
@@ localagent/session.py header @@
- One JSON object per line is crash-safe: if the program dies mid-write only the last line can be damaged.

## `SessionLog`
@@ localagent/session.py SessionLog @@
@@ localagent/session.py SessionLog.__init__ @@
- Creates the folder if needed and picks a filename from the current time (`session-YYYYmmdd-HHMMSS.jsonl`); such
  names sort in chronological order.

@@ localagent/session.py SessionLog.append @@
- Opens the file in append mode each time (so nothing stays buffered if the program crashes) and writes one line.
  `ensure_ascii=False` keeps non-English text readable.

## `latest_session`
@@ localagent/session.py latest_session @@
- Sorted filenames end with the newest; returns `None` when there are no sessions yet.

## `load_session`
@@ localagent/session.py load_session @@
- Reads the file, ignores blank lines, and parses each line back into a message dictionary.
