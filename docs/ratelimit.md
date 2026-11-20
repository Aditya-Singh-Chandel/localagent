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
 14 | import threading
 15 | import time
 16 | from collections import deque
 17 | from datetime import datetime, timedelta, timezone, tzinfo
 18 | from pathlib import Path
 19 | from typing import Callable
 20 | 
 21 | from .llm import LLMError
 22 | 
 23 | WINDOW = 60.0                       # seconds in the per-minute windows
 24 | 
 25 | 
```
- `json` reads/writes the usage file; `threading` gives a lock so two threads cannot update counters at once; `time`
  supplies the real clock; `deque` is a list that is fast to trim from the front (the per-minute windows);
  `datetime`/`timezone`/`tzinfo` compute the Pacific date. `LLMError` is the base class for our error.
- `WINDOW = 60.0`: both per-minute limits look at the last 60 seconds ("sliding window").

## `QuotaExhausted`
```python
 26 | class QuotaExhausted(LLMError):
 27 |     """The model's daily request quota is used up (try another model or tomorrow)."""
 28 | 
 29 | 
```
- A special `LLMError` meaning "this model is done for today". Because it is an `LLMError`, any code that already
  handles model errors handles it too; `resilient.py` catches it specifically to switch models.

## `pacific_day`
```python
 30 | def pacific_day(now: float) -> str:
 31 |     """Calendar date in US Pacific time, the clock Google uses for daily resets."""
 32 |     zone: tzinfo
 33 |     try:
 34 |         from zoneinfo import ZoneInfo
 35 |         zone = ZoneInfo("America/Los_Angeles")
 36 |     except Exception:                                 # no time-zone data on this system
 37 |         zone = timezone(timedelta(hours=-8))          # standard time; off by 1 h in summer
 38 |     return datetime.fromtimestamp(now, zone).date().isoformat()
 39 | 
 40 | 
```
- Converts a timestamp (seconds since 1970) into a date string such as `2026-11-04` in California time.
- `ZoneInfo("America/Los_Angeles")` knows daylight-saving time. Some Windows Pythons lack time-zone data; then we fall
  back to a fixed UTC−8, which can be one hour off in summer (harmless: at worst a day starts an hour early or late).

## `RateLimiter`
```python
 41 | class RateLimiter:
 42 |     """Tracks one model's usage and blocks or refuses calls that would break its limits."""
 43 | 
```
```python
 44 |     def __init__(self, model: str, rpm: int, tpm: int, rpd: int, usage_file: Path | None = None,
 45 |                  clock: Callable[[], float] = time.time,
 46 |                  sleep: Callable[[float], None] = time.sleep) -> None:
 47 |         self.model = model
 48 |         self._rpm, self._tpm, self._rpd = rpm, tpm, rpd
 49 |         self._file = usage_file
 50 |         self._memory: dict = {}                          # used when there is no file
 51 |         self._clock, self._sleep = clock, sleep
 52 |         self._requests: deque[float] = deque()          # send times in the last minute
 53 |         self._tokens: deque[tuple[float, int]] = deque()  # (time, tokens) in the last minute
 54 |         self._lock = threading.Lock()
 55 | 
 56 |     # ----- daily counters, shared by all models in one JSON file --------------
```
- Stores the model name, the three limits and the usage-file path (`None` = keep counts only in memory, used by tests).
- `clock` and `sleep` are **injectable**: real code uses `time.time`/`time.sleep`; tests pass a fake clock so a
  "60-second wait" takes no real time.
- `_requests` holds the send times of recent requests; `_tokens` holds `(time, tokens)` pairs of recent replies.

## Daily counters
```python
 57 |     def _load(self) -> dict:
 58 |         """Whole usage record: {"day": "YYYY-MM-DD", "models": {name: counters}}."""
 59 |         if self._file is None:
 60 |             return self._memory
 61 |         try:
 62 |             data = json.loads(self._file.read_text(encoding="utf-8"))
 63 |             return data if isinstance(data, dict) else {}
 64 |         except (OSError, ValueError):
 65 |             return {}                                   # missing or unreadable: start fresh
 66 | 
```
- Returns the whole usage record. With no file it returns the in-memory copy. A missing, unreadable or corrupted file
  yields `{}` (start fresh) instead of crashing the agent.

```python
 67 |     def _counters(self) -> dict:
 68 |         """This model's counters for today; a new day starts from zero."""
 69 |         data = self._load()
 70 |         if data.get("day") != pacific_day(self._clock()):
 71 |             return {"requests": 0, "tokens": 0}
 72 |         return dict(data.get("models", {}).get(self.model, {"requests": 0, "tokens": 0}))
 73 | 
```
- This model's `{"requests", "tokens"}` for today. If the stored day is not today, counting starts again from zero —
  that is how the daily reset happens, with no timer needed.

```python
 74 |     def _store(self, counters: dict) -> None:
 75 |         """Save this model's counters, keeping other models' counters for the same day."""
 76 |         data = self._load()
 77 |         day = pacific_day(self._clock())
 78 |         if data.get("day") != day:
 79 |             data = {"day": day, "models": {}}
 80 |         data.setdefault("models", {})[self.model] = counters
 81 |         if self._file is None:
 82 |             self._memory = data
 83 |             return
 84 |         self._file.parent.mkdir(parents=True, exist_ok=True)
 85 |         self._file.write_text(json.dumps(data, indent=1), encoding="utf-8")
 86 | 
 87 |     # ----- public API ---------------------------------------------------------
```
- Writes this model's counters back. If the stored day is old, the record is replaced by a fresh one for today. Other
  models' counters for the same day are kept, because all limiters share one file.

## Public API
```python
 88 |     def set_rpm(self, rpm: int) -> None:
 89 |         """Adopt a lower requests-per-minute limit learned from the server, and save it for today."""
 90 |         with self._lock:
 91 |             if 0 < rpm < self._rpm:
 92 |                 self._rpm = rpm
 93 |                 counters = self._counters()
 94 |                 counters["learned_rpm"] = rpm
 95 |                 self._store(counters)
 96 | 
```
- Lowers the per-minute request limit to the value the server reported (never raises it) and saves it as
  `learned_rpm` in today's record, so other runs of the agent start with the right limit instead of learning it again.

```python
 97 |     def exhausted(self) -> bool:
 98 |         """True when the daily quota is used up (by our count, or because the server said so)."""
 99 |         with self._lock:
100 |             counters = self._counters()
101 |         return bool(counters.get("exhausted")) or int(counters["requests"]) >= self._rpd
102 | 
```
- Whether the model is used up for today: either our count reached `rpd`, or the server said so (`exhausted` flag).

```python
103 |     def mark_exhausted(self, server_limit: int | None = None) -> None:
104 |         """The server said the daily quota is gone: stop using this model today.
105 | 
106 |         ``server_limit`` is the real daily limit when the server told us (Google's
107 |         429 contains ``"quotaValue": "20"``); it is remembered so status lines show it.
108 |         """
109 |         with self._lock:
110 |             counters = self._counters()
111 |             counters["exhausted"] = True
112 |             if server_limit:
113 |                 counters["server_limit"] = server_limit
114 |             self._store(counters)
115 | 
```
- Used when the *server* says the daily quota is gone (its count can differ from ours: the real limit may be lower
  than configured, or you used the key elsewhere). Sets today's `exhausted` flag so the model is skipped until the next
  Pacific day, and stores `server_limit` when the server told us the number.

```python
116 |     def before_call(self, estimated_tokens: int) -> None:
117 |         """Wait until a request of about ``estimated_tokens`` is allowed; count it as sent."""
118 |         if estimated_tokens > self._tpm:
119 |             raise LLMError(f"request of ~{estimated_tokens} tokens exceeds {self.model}'s "
120 |                            f"limit of {self._tpm} tokens per minute; reduce num_ctx")
121 |         with self._lock:
122 |             counters = self._counters()
123 |             if counters.get("exhausted") or int(counters["requests"]) >= self._rpd:
124 |                 raise QuotaExhausted(f"{self.model}: daily quota used up")
125 |             learned = int(counters.get("learned_rpm", 0))
126 |             if 0 < learned < self._rpm:                     # learned earlier today, maybe by
127 |                 self._rpm = learned                         # another run of the agent
128 |             while True:
129 |                 now = self._clock()
130 |                 while self._requests and now - self._requests[0] >= WINDOW:
131 |                     self._requests.popleft()
132 |                 while self._tokens and now - self._tokens[0][0] >= WINDOW:
133 |                     self._tokens.popleft()
134 |                 waits = []
135 |                 if len(self._requests) >= self._rpm:
136 |                     waits.append(self._requests[0] + WINDOW - now)
137 |                 used = sum(count for _, count in self._tokens)
138 |                 if self._tokens and used + estimated_tokens > self._tpm:
139 |                     waits.append(self._tokens[0][0] + WINDOW - now)
140 |                 if not waits:
141 |                     break
142 |                 self._sleep(max(min(waits), 0.05))      # wait for the oldest entry to age out
143 |             self._requests.append(now)
144 |             counters["requests"] = int(counters["requests"]) + 1   # failed sends count too
145 |             self._store(counters)
146 | 
```
- A request bigger than the whole per-minute token budget can never be sent; it fails at once with advice to lower
  `num_ctx` (which makes the history trimmer keep the prompt smaller).
- Refuses with `QuotaExhausted` when today's requests reached `rpd` or the model was marked exhausted. Adopts a lower
  `learned_rpm` saved earlier today (possibly by another run).
- The loop: drop entries older than 60 s from both windows; if the request window is full, or the tokens already used
  plus this request's estimate would exceed `tpm`, compute how long until the oldest entry expires and sleep that
  long (at least 0.05 s), then check again.
- When allowed: remember the send time and add 1 to today's count. The count goes up **before** the request is sent,
  so failed requests count too — conservative, so we never overshoot the daily limit.

```python
147 |     def after_call(self, tokens: int) -> None:
148 |         """Record the tokens a finished call really used."""
149 |         with self._lock:
150 |             self._tokens.append((self._clock(), tokens))
151 |             counters = self._counters()
152 |             counters["tokens"] = int(counters["tokens"]) + tokens
153 |             self._store(counters)
154 | 
```
- Adds the real token usage (prompt + answer + hidden thinking) to the per-minute window and to today's total.

```python
155 |     def status(self) -> str:
156 |         """One line for the user: requests and tokens used today."""
157 |         with self._lock:
158 |             counters = self._counters()
159 |         limit = counters.get("server_limit", self._rpd)
160 |         line = f"{self.model}: {counters['requests']}/{limit} requests, {counters['tokens']} tokens today"
161 |         return line + (" - quota used up until midnight Pacific" if counters.get("exhausted") else "")
```
- One human-readable line, e.g. `gemini-3.5-flash: 22/20 requests, 34288 tokens today - quota used up until midnight
  Pacific`. The limit shown is the server's real one when known, else the configured one. Shown by `/stats` and
  `--doctor`.
