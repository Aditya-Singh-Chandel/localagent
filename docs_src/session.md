# `localagent/session.py` — conversation log and resume

**Role in the flow:** `Agent._add` writes every message to a `.jsonl` file under `.localagent/sessions/`. `--resume`
(or `-c`) loads the newest file back into a new `Agent`; `/resume` lists the project's conversations and continues
any of them. The log doubles as an audit trail and as raw material for analysing where tokens and time go.

## Module header
@@ localagent/session.py header @@
- One JSON object per line is crash-safe: if the program dies mid-write only the last line can be damaged.
- Besides messages the log holds **event lines** such as `{"event": "reset"}`. They exist because of a bug found in
  version 0.6's review: `/reset` only cleared the conversation in memory, so `--resume` brought the "forgotten"
  conversation straight back; and after `/compact`, resuming restored the whole long history *plus* the summary, so the
  next request overflowed the context. Resuming now starts after the last event.

## `SessionLog`
@@ localagent/session.py SessionLog @@
@@ localagent/session.py SessionLog.__init__ @@
- Creates the folder if needed and picks a filename from the current time (`session-YYYYmmdd-HHMMSS.jsonl`); such
  names sort in chronological order. The file itself appears with the first message.

@@ localagent/session.py SessionLog.append @@
- Opens the file in append mode each time (so nothing stays buffered if the program crashes) and writes one line.
  `ensure_ascii=False` keeps non-English text readable.

@@ localagent/session.py SessionLog.mark @@
- Writes an event line (`"reset"` from `Agent.reset`, `"compact"` from `Agent.compact`) with the time it happened.

## Finding sessions
@@ localagent/session.py list_sessions @@
@@ localagent/session.py latest_session @@
- `list_sessions`: every session file, oldest first (the timestamped names sort that way); an empty list when the
  folder does not exist yet. `latest_session`: the last of them, or `None`.

## Reading a session back
@@ localagent/session.py _items @@
- Every line that parses as a JSON object. A line that does not — the program was stopped while writing it — is
  skipped. (Before 0.6 such a line made `--resume` crash with a traceback, although the header promised crash-safety.)

@@ localagent/session.py load_session @@
- Collects messages, starting over at every event line, so only what came after the last reset or compaction is
  returned. After a compaction that is the summary and everything since.

@@ localagent/session.py session_title @@
- One line per conversation for `/resume`: the date and time from the file name plus the first request (the very
  first one, even if the conversation was later compacted — that is what you remember it by), with its whitespace
  squeezed and cut to 70 characters.
