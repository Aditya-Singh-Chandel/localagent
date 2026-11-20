# `localagent/ratelimit.py` — staying inside Gemini's free-tier limits

**Role in the flow:** every Gemini model in the chain (see `resilient.md`) has its own `RateLimiter`. Before each request
the limiter **waits** if sending now would break a per-minute limit, and **refuses** if the model's daily quota is used up.
After each request it records the tokens actually used.

**Why do this on our side?** A request that breaks a limit is answered with HTTP 429 ("too many requests"), which wastes
time and, on some quotas, still counts. Waiting a few seconds locally is cheaper and keeps the agent predictable.

**The three limits of a free API key** (per model; numbers are settings in `config.py`):

| Limit | Default | What happens when reached |
|---|---|---|
| requests per minute (`rpm`) | 12 | wait until the oldest request in the last 60 s is older than 60 s |
| tokens per minute (`tpm`) | 250 000 | wait until enough old tokens leave the 60-second window |
| requests per day (`rpd`) | 1 500 | refuse (`QuotaExhausted`); the chain moves to the next model |

The daily count is written to `.localagent/usage.json`, so restarting the agent does not reset it. Google resets
daily quotas at **midnight US Pacific time**, so "today" is computed in that time zone. Example file:

```json
{"day": "2026-11-04", "models": {"gemini-3.5-flash": {"requests": 22, "tokens": 34288}}}
```

**Real limits differ per model — measured on this key (4 Nov 2026):** `gemini-3.5-flash` and `gemini-2.5-flash` were
cut off by Google after about **20 requests per day** each, not 1500. Google's 429 reply states the real number
(`"quotaValue": "20"`); the agent reads it, marks the model as used up until midnight Pacific, remembers the number in
`usage.json` (so `/stats` shows `x/20`), announces the switch, and moves to the next model in the chain. The lighter
models allow more: `gemini-3.5-flash-lite` was cut off after **500** requests (5 Nov 2026), and the chain then
continued on `gemini-3.1-flash-lite` by itself. You can set per-model numbers with
`LOCALAGENT_GEMINI_LIMITS`, e.g. `setx LOCALAGENT_GEMINI_LIMITS "gemini-3.5-flash=5/20,gemini-2.5-flash=5/20"`
(format `model=requests-per-minute/requests-per-day`).

The defaults are your stated limits; Google changes free-tier numbers from time to time. Check yours in Google AI Studio
(the "Rate limits" page) and change `rpm`/`tpm`/`rpd` in `config.py` if they differ. Even if a number is set too high,
the server's 429 replies are still handled by `resilient.py` (it waits as long as the server asks).

## Module header
```python
  1 | """Client-side rate limiting for hosted models with free-tier quotas (e.g. Gemini).
  2 | 
  3 | A free API key has three limits per model:
  4 |   * requests per minute (RPM)   -> we WAIT until a request is allowed
  5 |   * tokens per minute (TPM)     -> we WAIT until the tokens fit
  6 |   * requests per day (RPD)      -> we STOP using that model for the day
  7 | Waiting on our side is far cheaper than sending a request that the server
  8 | rejects with HTTP 429. The daily count is saved to a small JSON file so it
  9 | survives restarts. Google resets daily quotas at midnight Pacific time.
 10 | """
 11 | from __future__ import annotations
 12 | 
 13 | import json
 14 | import os
 15 | import threading
 16 | import time
 17 | from collections import deque
 18 | from datetime import datetime, timedelta, timezone
 19 | from pathlib import Path
 20 | from typing import Callable
 21 | 
 22 | from .llm import LLMError
 23 | 
 24 | WINDOW = 60.0                       # seconds in the per-minute windows
 25 | WAIT_NOTICE = 3.0                   # waits at least this long are announced
 26 | 
 27 | 
```
- `json` reads/writes the usage file; `os` replaces files in one step; `threading` gives a lock so two threads cannot
  update counters at once; `time` supplies the real clock; `deque` is a list that is fast to trim from the front (the
  per-minute windows); `datetime`/`timedelta`/`timezone` compute the Pacific date. `LLMError` is the base class for
  our error.
- `WINDOW = 60.0`: both per-minute limits look at the last 60 seconds ("sliding window"). `WAIT_NOTICE`: waits of 3
  seconds or more are announced.

## `QuotaExhausted`
```python
 28 | class QuotaExhausted(LLMError):
 29 |     """The model's daily request quota is used up (try another model or tomorrow)."""
 30 | 
 31 | 
```
- A special `LLMError` meaning "this model is done for today". Because it is an `LLMError`, any code that already
  handles model errors handles it too; `resilient.py` catches it specifically to switch models.

## Pacific time
```python
 32 | def _pacific_offset(utc: datetime) -> timedelta:
 33 |     """US Pacific time's distance from UTC at ``utc``: -7 h during daylight saving time (from
 34 |     the 2nd Sunday of March, 2 a.m., to the 1st Sunday of November, 2 a.m.), else -8 h."""
 35 |     march = datetime(utc.year, 3, 8, tzinfo=timezone.utc)          # 2nd Sunday: on/after the 8th
 36 |     begins = march + timedelta(days=(6 - march.weekday()) % 7, hours=10)     # 2 a.m. = 10:00 UTC
 37 |     november = datetime(utc.year, 11, 1, tzinfo=timezone.utc)
 38 |     ends = november + timedelta(days=(6 - november.weekday()) % 7, hours=9)  # 2 a.m. = 09:00 UTC
 39 |     return timedelta(hours=-7) if begins <= utc < ends else timedelta(hours=-8)
 40 | 
 41 | 
```
- California's distance from UTC at a moment: 7 hours behind during daylight saving time, else 8. The US rule since
  2007: summer time starts on the **second Sunday of March** at 2 a.m. (10:00 UTC) and ends on the **first Sunday of
  November** at 2 a.m. (09:00 UTC). `weekday()` counts Monday as 0 and Sunday as 6, so `(6 - weekday) % 7` days after
  a date is the first Sunday on or after it; the second Sunday of March is the first one on or after the 8th.

```python
 42 | def pacific_day(now: float) -> str:
 43 |     """Calendar date in US Pacific time, the clock Google uses for daily resets."""
 44 |     try:
 45 |         from zoneinfo import ZoneInfo
 46 |         return datetime.fromtimestamp(now, ZoneInfo("America/Los_Angeles")).date().isoformat()
 47 |     except Exception:                         # no time-zone data (normal on Windows): US rules
 48 |         utc = datetime.fromtimestamp(now, timezone.utc)
 49 |         return (utc + _pacific_offset(utc)).date().isoformat()
 50 | 
 51 | 
```
- Converts a timestamp (seconds since 1970) into a date string such as `2026-11-04` in California time.
- `ZoneInfo("America/Los_Angeles")` knows the time zone's whole history. Windows has no time-zone data for Python (it
  needs the `tzdata` package, which this stdlib-only project does not install), and the old fallback was a fixed UTC−8:
  from March to November the agent's "new day" came an hour late, so for an hour each day a model Google had already
  reset still looked used up. The fallback now applies the real rule (`_pacific_offset`).

## Writing the usage file safely
```python
 52 | def _write_atomic(path: Path, text: str) -> None:
 53 |     """Replace ``path`` in one step, so another agent never reads half a file (best effort)."""
 54 |     temporary = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
 55 |     try:
 56 |         path.parent.mkdir(parents=True, exist_ok=True)
 57 |         temporary.write_text(text, encoding="utf-8")
 58 |         for attempt in range(8):
 59 |             try:
 60 |                 os.replace(temporary, path)
 61 |                 return
 62 |             except PermissionError:            # Windows: another agent or a virus scanner has
 63 |                 time.sleep(0.05 * (attempt + 1))   # one of the files open for a moment
 64 |     except OSError:
 65 |         pass                                   # counting is advisory: never stop a model call
 66 |     for _ in range(3):                         # do not leave the temporary file behind
 67 |         try:
 68 |             temporary.unlink(missing_ok=True)
 69 |             return
 70 |         except OSError:
 71 |             time.sleep(0.1)
 72 | 
 73 | 
```
- Writes the text to a temporary file next to the real one, then **replaces** the real file in one step
  (`os.replace`). Another agent — a second terminal, or the HTTP server's sessions — reading at that moment sees the
  old file or the new one, never half a file. (Before 0.6 a half-written file read as "no counts at all", which wiped
  every model's daily counts and "used up" flags.)
- On Windows the replace can fail for a moment while someone else has one of the files open — another agent, or a virus
  scanner looking at the new file — so it is tried eight times with a growing pause (about 2 seconds in all). Counting
  is advisory: if writing fails anyway the error is swallowed instead of stopping a model call, and the temporary file
  is removed (also retried, so no `.tmp` files pile up next to `usage.json`).

```python
 74 | _SHARED: dict[tuple, "RateLimiter"] = {}
 75 | _SHARED_LOCK = threading.Lock()
 76 | 
 77 | 
```
```python
 78 | def shared_limiter(model: str, rpm: int, tpm: int, rpd: int, usage_file: Path | None,
 79 |                    on_wait: Callable[[str], None] | None = None) -> "RateLimiter":
 80 |     """The one limiter for this model in this program.
 81 | 
 82 |     Every server session builds its own model chain; if each had its own limiter, ten
 83 |     sessions would each believe they may send the whole per-minute limit.
 84 |     """
 85 |     key = (model, rpm, tpm, rpd, str(usage_file))
 86 |     with _SHARED_LOCK:
 87 |         if key not in _SHARED:
 88 |             _SHARED[key] = RateLimiter(model, rpm, tpm, rpd, usage_file, on_wait=on_wait)
 89 |         return _SHARED[key]
 90 | 
 91 | 
```
- `shared_limiter` returns **the one limiter** for a model (and its limits and usage file) in this program, creating
  it the first time. Why: the HTTP server builds a model chain for every session; with a limiter each, ten sessions
  each believed they could send the whole per-minute limit, and together sent ten times too much. `_SHARED` holds them;
  `_SHARED_LOCK` makes creating one safe when two sessions start at the same moment.

## `RateLimiter`
```python
 92 | class RateLimiter:
 93 |     """Tracks one model's usage and blocks or refuses calls that would break its limits."""
 94 | 
```
```python
 95 |     def __init__(self, model: str, rpm: int, tpm: int, rpd: int, usage_file: Path | None = None,
 96 |                  clock: Callable[[], float] = time.time,
 97 |                  sleep: Callable[[float], None] = time.sleep,
 98 |                  on_wait: Callable[[str], None] | None = None) -> None:
 99 |         self.model = model
100 |         self._rpm, self._tpm, self._rpd = rpm, tpm, rpd
101 |         self._file = usage_file
102 |         self._memory: dict = {}                          # used when there is no file
103 |         self._clock, self._sleep = clock, sleep
104 |         self._on_wait = on_wait                          # told about long waits (None = silent)
105 |         self._requests: deque[float] = deque()          # send times in the last minute
106 |         self._tokens: deque[tuple[float, int]] = deque()  # (time, tokens) in the last minute
107 |         self._lock = threading.Lock()
108 | 
109 |     # ----- daily counters, shared by all models in one JSON file --------------
```
- Stores the model name, the three limits and the usage-file path (`None` = keep counts only in memory, used by tests).
- `clock` and `sleep` are **injectable**: real code uses `time.time`/`time.sleep`; tests pass a fake clock so a
  "60-second wait" takes no real time. `on_wait` is told about long waits (see `before_call`).
- `_requests` holds the send times of recent requests; `_tokens` holds `(time, tokens)` pairs of recent replies.

## Daily counters
```python
110 |     def _load(self) -> dict:
111 |         """Whole usage record: {"day": "YYYY-MM-DD", "models": {name: counters}}."""
112 |         if self._file is None:
113 |             return self._memory
114 |         try:
115 |             data = json.loads(self._file.read_text(encoding="utf-8"))
116 |             return data if isinstance(data, dict) else {}
117 |         except (OSError, ValueError):
118 |             return {}                                   # missing or unreadable: start fresh
119 | 
```
- Returns the whole usage record. With no file it returns the in-memory copy. A missing, unreadable or corrupted file
  yields `{}` (start fresh) instead of crashing the agent.

```python
120 |     def _counters(self) -> dict:
121 |         """This model's counters for today; a new day starts from zero."""
122 |         data = self._load()
123 |         if data.get("day") != pacific_day(self._clock()):
124 |             return {"requests": 0, "tokens": 0}
125 |         return dict(data.get("models", {}).get(self.model, {"requests": 0, "tokens": 0}))
126 | 
```
- This model's `{"requests", "tokens"}` for today. If the stored day is not today, counting starts again from zero —
  that is how the daily reset happens, with no timer needed.

```python
127 |     def _store(self, counters: dict) -> None:
128 |         """Save this model's counters, keeping other models' counters for the same day."""
129 |         data = self._load()
130 |         day = pacific_day(self._clock())
131 |         if data.get("day") != day:
132 |             data = {"day": day, "models": {}}
133 |         data.setdefault("models", {})[self.model] = counters
134 |         if self._file is None:
135 |             self._memory = data
136 |             return
137 |         _write_atomic(self._file, json.dumps(data, indent=1))
138 | 
139 |     # ----- public API ---------------------------------------------------------
```
- Writes this model's counters back (with `_write_atomic`). If the stored day is old, the record is replaced by a
  fresh one for today. Other models' counters for the same day are kept, because all limiters share one file.

## Public API
```python
140 |     def set_rpm(self, rpm: int) -> None:
141 |         """Adopt a lower requests-per-minute limit learned from the server, and save it for today."""
142 |         with self._lock:
143 |             if 0 < rpm < self._rpm:
144 |                 self._rpm = rpm
145 |                 counters = self._counters()
146 |                 counters["learned_rpm"] = rpm
147 |                 self._store(counters)
148 | 
```
- Lowers the per-minute request limit to the value the server reported (never raises it) and saves it as
  `learned_rpm` in today's record, so other runs of the agent start with the right limit instead of learning it again.

```python
149 |     def exhausted(self) -> bool:
150 |         """True when the daily quota is used up (by our count, or because the server said so)."""
151 |         with self._lock:
152 |             counters = self._counters()
153 |         return bool(counters.get("exhausted")) or int(counters["requests"]) >= self._rpd
154 | 
```
- Whether the model is used up for today: either our count reached `rpd`, or the server said so (`exhausted` flag).

```python
155 |     def mark_exhausted(self, server_limit: int | None = None) -> None:
156 |         """The server said the daily quota is gone: stop using this model today.
157 | 
158 |         ``server_limit`` is the real daily limit when the server told us (Google's
159 |         429 contains ``"quotaValue": "20"``); it is remembered so status lines show it.
160 |         """
161 |         with self._lock:
162 |             counters = self._counters()
163 |             counters["exhausted"] = True
164 |             if server_limit:
165 |                 counters["server_limit"] = server_limit
166 |             self._store(counters)
167 | 
```
- Used when the *server* says the daily quota is gone (its count can differ from ours: the real limit may be lower
  than configured, or you used the key elsewhere). Sets today's `exhausted` flag so the model is skipped until the next
  Pacific day, and stores `server_limit` when the server told us the number.

```python
168 |     def before_call(self, estimated_tokens: int) -> None:
169 |         """Wait until a request of about ``estimated_tokens`` is allowed; count it as sent."""
170 |         if estimated_tokens > self._tpm:
171 |             raise LLMError(f"request of ~{estimated_tokens} tokens exceeds {self.model}'s "
172 |                            f"limit of {self._tpm} tokens per minute; reduce num_ctx")
173 |         with self._lock:
174 |             counters = self._counters()
175 |             if counters.get("exhausted") or int(counters["requests"]) >= self._rpd:
176 |                 raise QuotaExhausted(f"{self.model}: daily quota used up")
177 |             learned = int(counters.get("learned_rpm", 0))
178 |             if 0 < learned < self._rpm:                     # learned earlier today, maybe by
179 |                 self._rpm = learned                         # another run of the agent
180 |             told = False
181 |             while True:
182 |                 now = self._clock()
183 |                 while self._requests and now - self._requests[0] >= WINDOW:
184 |                     self._requests.popleft()
185 |                 while self._tokens and now - self._tokens[0][0] >= WINDOW:
186 |                     self._tokens.popleft()
187 |                 waits = []
188 |                 if len(self._requests) >= self._rpm:
189 |                     waits.append(self._requests[0] + WINDOW - now)
190 |                 used = sum(count for _, count in self._tokens)
191 |                 if self._tokens and used + estimated_tokens > self._tpm:
192 |                     waits.append(self._tokens[0][0] + WINDOW - now)
193 |                 if not waits:
194 |                     break
195 |                 pause = max(min(waits), 0.05)
196 |                 if pause >= WAIT_NOTICE and self._on_wait and not told:
197 |                     self._on_wait(f"{self.model}: waiting {pause:.0f}s for the per-minute limit")
198 |                     told = True
199 |                 self._sleep(pause)                      # wait for the oldest entry to age out
200 |             self._requests.append(now)
201 |             counters["requests"] = int(counters["requests"]) + 1   # failed sends count too
202 |             self._store(counters)
203 | 
```
- A request bigger than the whole per-minute token budget can never be sent; it fails at once with advice to lower
  `num_ctx` (which makes the history trimmer keep the prompt smaller).
- Refuses with `QuotaExhausted` when today's requests reached `rpd` or the model was marked exhausted. Adopts a lower
  `learned_rpm` saved earlier today (possibly by another run).
- The loop: drop entries older than 60 s from both windows; if the request window is full, or the tokens already used
  plus this request's estimate would exceed `tpm`, compute how long until the oldest entry expires and sleep that
  long (at least 0.05 s), then check again. A wait of 3 s or more is announced once (`told`), e.g.
  `[gemini-3.5-flash-lite: waiting 41s for the per-minute limit]` — before 0.6 the agent simply went silent.
- When allowed: remember the send time and add 1 to today's count. The count goes up **before** the request is sent,
  so failed requests count too — conservative, so we never overshoot the daily limit.

```python
204 |     def after_call(self, tokens: int) -> None:
205 |         """Record the tokens a finished call really used."""
206 |         with self._lock:
207 |             self._tokens.append((self._clock(), tokens))
208 |             counters = self._counters()
209 |             counters["tokens"] = int(counters["tokens"]) + tokens
210 |             self._store(counters)
211 | 
```
- Adds the real token usage (prompt + answer + hidden thinking) to the per-minute window and to today's total.

```python
212 |     def status(self) -> str:
213 |         """One line for the user: requests and tokens used today."""
214 |         with self._lock:
215 |             counters = self._counters()
216 |         limit = counters.get("server_limit", self._rpd)
217 |         line = f"{self.model}: {counters['requests']}/{limit} requests, {counters['tokens']} tokens today"
218 |         return line + (" - quota used up until midnight Pacific" if counters.get("exhausted") else "")
```
- One human-readable line, e.g. `gemini-3.5-flash: 22/20 requests, 34288 tokens today - quota used up until midnight
  Pacific`. The limit shown is the server's real one when known, else the configured one. Shown by `/stats` and
  `--doctor`.
