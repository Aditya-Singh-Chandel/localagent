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
@@ localagent/ratelimit.py header @@
- `json` reads/writes the usage file; `threading` gives a lock so two threads cannot update counters at once; `time`
  supplies the real clock; `deque` is a list that is fast to trim from the front (the per-minute windows);
  `datetime`/`timezone`/`tzinfo` compute the Pacific date. `LLMError` is the base class for our error.
- `WINDOW = 60.0`: both per-minute limits look at the last 60 seconds ("sliding window").

## `QuotaExhausted`
@@ localagent/ratelimit.py QuotaExhausted @@
- A special `LLMError` meaning "this model is done for today". Because it is an `LLMError`, any code that already
  handles model errors handles it too; `resilient.py` catches it specifically to switch models.

## `pacific_day`
@@ localagent/ratelimit.py pacific_day @@
- Converts a timestamp (seconds since 1970) into a date string such as `2026-11-04` in California time.
- `ZoneInfo("America/Los_Angeles")` knows daylight-saving time. Some Windows Pythons lack time-zone data; then we fall
  back to a fixed UTC−8, which can be one hour off in summer (harmless: at worst a day starts an hour early or late).

## `RateLimiter`
@@ localagent/ratelimit.py RateLimiter @@
@@ localagent/ratelimit.py RateLimiter.__init__ @@
- Stores the model name, the three limits and the usage-file path (`None` = keep counts only in memory, used by tests).
- `clock` and `sleep` are **injectable**: real code uses `time.time`/`time.sleep`; tests pass a fake clock so a
  "60-second wait" takes no real time.
- `_requests` holds the send times of recent requests; `_tokens` holds `(time, tokens)` pairs of recent replies.

## Daily counters
@@ localagent/ratelimit.py RateLimiter._load @@
- Returns the whole usage record. With no file it returns the in-memory copy. A missing, unreadable or corrupted file
  yields `{}` (start fresh) instead of crashing the agent.

@@ localagent/ratelimit.py RateLimiter._counters @@
- This model's `{"requests", "tokens"}` for today. If the stored day is not today, counting starts again from zero —
  that is how the daily reset happens, with no timer needed.

@@ localagent/ratelimit.py RateLimiter._store @@
- Writes this model's counters back. If the stored day is old, the record is replaced by a fresh one for today. Other
  models' counters for the same day are kept, because all limiters share one file.

## Public API
@@ localagent/ratelimit.py RateLimiter.set_rpm @@
- Lowers the per-minute request limit to the value the server reported (never raises it) and saves it as
  `learned_rpm` in today's record, so other runs of the agent start with the right limit instead of learning it again.

@@ localagent/ratelimit.py RateLimiter.exhausted @@
- Whether the model is used up for today: either our count reached `rpd`, or the server said so (`exhausted` flag).

@@ localagent/ratelimit.py RateLimiter.mark_exhausted @@
- Used when the *server* says the daily quota is gone (its count can differ from ours: the real limit may be lower
  than configured, or you used the key elsewhere). Sets today's `exhausted` flag so the model is skipped until the next
  Pacific day, and stores `server_limit` when the server told us the number.

@@ localagent/ratelimit.py RateLimiter.before_call @@
- A request bigger than the whole per-minute token budget can never be sent; it fails at once with advice to lower
  `num_ctx` (which makes the history trimmer keep the prompt smaller).
- Refuses with `QuotaExhausted` when today's requests reached `rpd` or the model was marked exhausted. Adopts a lower
  `learned_rpm` saved earlier today (possibly by another run).
- The loop: drop entries older than 60 s from both windows; if the request window is full, or the tokens already used
  plus this request's estimate would exceed `tpm`, compute how long until the oldest entry expires and sleep that
  long (at least 0.05 s), then check again.
- When allowed: remember the send time and add 1 to today's count. The count goes up **before** the request is sent,
  so failed requests count too — conservative, so we never overshoot the daily limit.

@@ localagent/ratelimit.py RateLimiter.after_call @@
- Adds the real token usage (prompt + answer + hidden thinking) to the per-minute window and to today's total.

@@ localagent/ratelimit.py RateLimiter.status @@
- One human-readable line, e.g. `gemini-3.5-flash: 22/20 requests, 34288 tokens today - quota used up until midnight
  Pacific`. The limit shown is the server's real one when known, else the configured one. Shown by `/stats` and
  `--doctor`.
