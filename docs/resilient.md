# `localagent/resilient.py` — retries and fallback across models

**Role in the flow:** with `--backend gemini`, the agent does not talk to one model but to a **chain**:

```
gemini-3.5-flash ──fails──► gemini-2.5-flash ──fails──► gemini-3.1-flash-lite ──fails──► your local model (optional)
```

`FallbackBackend` looks like any other backend to the agent (it has the same `chat` method), so `agent.py` did not
change at all. Inside, each model gets a few retries for *temporary* problems, and the chain moves on when a model is
overloaded for too long or its daily quota is used up.

**Why:** on the free tier we observed both `503 "model is experiencing high demand"` and `429 "too many requests"`
during normal use. Without this file every such hiccup would stop the agent mid-task.

| Error | Meaning | What we do |
|---|---|---|
| 429 with "per day" in the text | daily quota gone | mark the model exhausted, go to next model at once |
| 429, 500, 502, 503, 504 | temporary (rate limit, overload, server hiccup) | wait, retry the same model (up to `max_retries`), then next model |
| no status (network error, timeout) | temporary | same as above |
| 404 | the model is retired or not offered to this key (seen: `gemini-2.5-flash-lite` "no longer available to new users") | skip it for the rest of the session, go to the next model |
| any other status, e.g. 400 | our request is wrong | raise immediately — retrying cannot fix it, and hiding it would hide a bug |

Wait time: if the server says how long (`"retryDelay": "17s"` in Google's 429 replies) we wait that long plus 1 s;
otherwise 2 s, 4 s, 8 s ... (doubling, "exponential backoff"), at most 30 s.

## Module header
```python
  1 | """Retries, rate limits and fallback across several models.
  2 | 
  3 |     chat() ──► model 1 (rate-limited) ──fails/overloaded/quota──► model 2 ──► ... ──► local model
  4 | 
  5 | For each model in order:
  6 |   * its RateLimiter (if any) waits for a free slot or refuses when the daily
  7 |     quota is gone (then we move to the next model at once);
  8 |   * temporary failures (HTTP 429/500/502/503/504, network errors) are retried
  9 |     with growing pauses (2 s, 4 s, 8 s ...) up to ``max_retries`` times;
 10 |   * a "per day" 429 marks the model exhausted for today;
 11 |   * any other error (e.g. HTTP 400: our request is wrong) is raised at once,
 12 |     because retrying or switching model would not fix it.
 13 | """
 14 | from __future__ import annotations
 15 | 
 16 | import json
 17 | import re
 18 | import time
 19 | from dataclasses import dataclass
 20 | from typing import Callable
 21 | 
 22 | from .llm import ChatBackend, LLMError, Reply, TokenSink
 23 | from .ratelimit import QuotaExhausted, RateLimiter
 24 | 
 25 | RETRYABLE = {429, 500, 502, 503, 504}
 26 | QUOTA_VALUE = re.compile(r'quotaValue"?\s*:\s*"?(\d+)')
 27 | RETRY_DELAY = re.compile(r'retryDelay"?\s*:\s*"?(\d+(?:\.\d+)?)s')
 28 | 
 29 | 
```
- `json` measures message size; `re` finds `retryDelay` in error text; `time.sleep` waits; `dataclass` builds `Entry`.
- `RETRYABLE` lists the HTTP codes treated as temporary. `RETRY_DELAY` is a regular expression matching
  `retryDelay": "17s"` (quotes optional, decimals allowed) and capturing the number. `QUOTA_VALUE` captures the
  real limit from `"quotaValue": "20"` in Google's quota errors.

## `Entry`
```python
 30 | @dataclass
 31 | class Entry:
 32 |     """One model in the chain: a display name, its client, and an optional limiter."""
 33 | 
 34 |     name: str
 35 |     backend: ChatBackend
 36 |     limiter: RateLimiter | None = None
 37 | 
 38 | 
```
- One link of the chain: a name for messages, the client that talks to the model, and an optional `RateLimiter`
  (the local model has none — it has no quota).

## Helpers
```python
 39 | def estimate_tokens(messages: list[dict], tools: list[dict]) -> int:
 40 |     """Rough prompt size: about 4 characters per token (good enough for budgeting)."""
 41 |     return len(json.dumps(messages)) // 4 + len(json.dumps(tools)) // 4
 42 | 
 43 | 
```
- The limiter needs the request size *before* sending. Exact counting needs the model's tokenizer; ~4 characters per
  token is a standard estimate that is close enough for budgeting.

```python
 44 | def server_delay(error: LLMError) -> float | None:
 45 |     """Seconds the server asked us to wait (Google puts ``"retryDelay": "17s"`` in 429s)."""
 46 |     match = RETRY_DELAY.search(str(error))
 47 |     return float(match.group(1)) if match else None
 48 | 
 49 | 
```
- Returns the seconds the server asked us to wait, or `None` when the error text has no `retryDelay`.

```python
 50 | def _is_minute_quota(error: LLMError) -> bool:
 51 |     """Google's per-minute 429 names a per-minute quota id."""
 52 |     text = str(error).lower().replace("_", "").replace(" ", "")
 53 |     return error.status == 429 and "perminute" in text
 54 | 
 55 | 
```
- Recognises a 429 caused by a *per-minute* quota (its quota id contains `PerMinute`).

```python
 56 | def _is_daily_quota(error: LLMError) -> bool:
 57 |     """Google's 429 text mentions a per-day quota when the daily limit is the cause."""
 58 |     text = str(error).lower().replace("_", "").replace(" ", "")
 59 |     return error.status == 429 and ("perday" in text or "daily" in text)
 60 | 
 61 | 
```
- Google's daily-quota 429 mentions a per-day quota id (for example `GenerateRequestsPerDay...`). The text is
  lower-cased and spaces/underscores removed so small wording differences still match.

## `FallbackBackend`
**Routing (cheap first, strong on demand):** normally the order is *cheap models → strong models → local model*. When
the agent sees repeated failures it calls `escalate()`, and until the next request the order becomes *strong → cheap →
local*. Why: on the free key the strong models allow only ~20 requests a day while the lite models allow far more, and
the lite models passed all eval tasks — so spending strong-model requests on routine steps would exhaust them for
nothing.

```python
 62 | class FallbackBackend:
 63 |     """A ChatBackend that tries several models in order until one answers."""
 64 | 
```
```python
 65 |     def __init__(self, entries: list[Entry], max_retries: int = 3,
 66 |                  sleep: Callable[[float], None] = time.sleep,
 67 |                  on_notice: Callable[[str], None] = lambda _m: None,
 68 |                  strong: list[Entry] | None = None) -> None:
 69 |         if not entries:
 70 |             raise ValueError("FallbackBackend needs at least one model")
 71 |         self._entries = entries
 72 |         self._strong = strong or []                # reserve models, see escalate()
 73 |         self._escalated = False
 74 |         self._unavailable: set[str] = set()       # models the server says do not exist (404)
 75 |         self._retries = max_retries
 76 |         self._sleep = sleep
 77 |         self._notice = on_notice
 78 |         self.last_model = ""                        # which model answered most recently
 79 | 
```
- Needs at least one entry. `strong` is the optional **reserve** list (the strong models with tiny daily quotas);
  `_escalated` says whether they currently go first. `_unavailable` remembers models that answered 404. `sleep` is injectable for tests. `on_notice` receives short messages such as
  "gemini-3.5-flash busy (503); retrying in 2s" (the CLI prints them in brackets). `last_model` remembers who answered.

```python
 80 |     def status(self) -> list[str]:
 81 |         """Daily usage lines for every rate-limited model."""
 82 |         return [e.limiter.status() for e in self._strong + self._entries if e.limiter]
 83 | 
```
- Daily usage lines of all rate-limited models, strong ones included (used by `/stats`).

```python
 84 |     def escalate(self) -> bool:
 85 |         """Put the strong reserve models first; True only when this changes anything."""
 86 |         if self._escalated or not self._strong:
 87 |             return False
 88 |         self._escalated = True
 89 |         return True
 90 | 
```
```python
 91 |     def reset_route(self) -> None:
 92 |         """Back to the everyday order (called at the start of every request)."""
 93 |         self._escalated = False
 94 | 
```
```python
 95 |     def _order(self) -> list[Entry]:
 96 |         """Everyday: cheap models, then strong, then local. Escalated: strong first."""
 97 |         local = [e for e in self._entries if e.limiter is None]
 98 |         cheap = [e for e in self._entries if e.limiter is not None]
 99 |         return (self._strong + cheap if self._escalated else cheap + self._strong) + local
100 | 
```
- `escalate` puts the reserve first and returns `True` only if that changed something (so the agent announces it once);
  with no reserve it returns `False`. `reset_route` restores the everyday order; the agent calls it at the start of
  every request. `_order` builds the list: local entries (no limiter) always last.

```python
101 |     def chat(self, messages: list[dict], tools: list[dict],
102 |              on_token: TokenSink | None = None) -> Reply:
103 |         """Ask each model in turn; return the first answer."""
104 |         problems: list[str] = []
105 |         estimate = estimate_tokens(messages, tools)
106 |         for entry in self._order():
107 |             if entry.name in self._unavailable:
108 |                 problems.append(f"{entry.name}: not available to this key")
109 |                 continue
110 |             if entry.limiter and entry.limiter.exhausted():
111 |                 problems.append(f"{entry.name}: daily quota used up")
112 |                 continue
113 |             reply = self._try(entry, messages, tools, on_token, estimate, problems)
114 |             if reply is not None:
115 |                 self.last_model = entry.name
116 |                 return reply
117 |         raise LLMError("every model failed:\n  " + "\n  ".join(problems))
118 | 
```
- Estimates the prompt size once, then goes down the chain in `_order()`, skipping models known to be unavailable. A model whose quota is already exhausted is skipped
  without sending anything. The first successful reply is returned and `last_model` updated.
- If every model failed, one `LLMError` lists what went wrong with each, so you can see why.

```python
119 |     def _try(self, entry: Entry, messages: list[dict], tools: list[dict],
120 |              on_token: TokenSink | None, estimate: int, problems: list[str]) -> Reply | None:
121 |         """Call one model with retries; None means 'give up on this model'."""
122 |         for attempt in range(self._retries + 1):
123 |             try:
124 |                 if entry.limiter:
125 |                     entry.limiter.before_call(estimate)
126 |                 reply = entry.backend.chat(messages, tools, on_token)
127 |             except QuotaExhausted as exc:
128 |                 problems.append(str(exc))
129 |                 return None
130 |             except LLMError as exc:
131 |                 if _is_daily_quota(exc):
132 |                     found = QUOTA_VALUE.search(str(exc))
133 |                     limit = int(found.group(1)) if found else None
134 |                     if entry.limiter:
135 |                         entry.limiter.mark_exhausted(limit)
136 |                     told = f" (Google's limit: {limit}/day)" if limit else ""
137 |                     self._notice(f"{entry.name}: daily quota used up{told}; trying the next model")
138 |                     problems.append(f"{entry.name}: daily quota used up{told}")
139 |                     return None
140 |                 if exc.status == 404:                   # model retired or not offered to this key
141 |                     self._unavailable.add(entry.name)
142 |                     self._notice(f"{entry.name}: not available (HTTP 404); skipping it from now on")
143 |                     problems.append(f"{entry.name}: not available (HTTP 404)")
144 |                     return None
145 |                 if _is_minute_quota(exc) and entry.limiter:
146 |                     found = QUOTA_VALUE.search(str(exc))
147 |                     if found:
148 |                         entry.limiter.set_rpm(int(found.group(1)))   # learn the real limit
149 |                 if exc.status is not None and exc.status not in RETRYABLE:
150 |                     raise                           # our request is wrong: do not hide it
151 |                 if attempt == self._retries:
152 |                     problems.append(f"{entry.name}: {str(exc)[:200]}")
153 |                     return None
154 |                 asked = server_delay(exc)
155 |                 pause = min(asked + 1.0, 90.0) if asked is not None else min(2.0 ** (attempt + 1), 30.0)
156 |                 self._notice(f"{entry.name} busy ({exc.status or 'network'}); "
157 |                              f"retrying in {pause:.0f}s")
158 |                 self._sleep(pause)
159 |                 continue
160 |             if entry.limiter:
161 |                 entry.limiter.after_call(reply.prompt_tokens + reply.completion_tokens)
162 |             return reply
163 |         return None
```
- Up to `max_retries + 1` attempts on one model. Each attempt first asks the limiter for permission (it may wait).
- `QuotaExhausted` from our own limiter → give up on this model (return `None`).
- An `LLMError` that is a daily-quota 429 → read the real limit if present, mark the model exhausted (remembering
  the limit), announce `gemini-...: daily quota used up (Google's limit: 20/day); trying the next model`, and give up
  on this model.
- HTTP 404 → remember the model as unavailable, announce it once, move on.
- A per-minute 429 that states the real limit (`"quotaValue": "5"`) lowers that model's limiter (`set_rpm`), so
  the next requests wait on our side instead of being rejected again.
- A non-temporary status → re-raise (see the table above).
- A temporary error on the last attempt → note it and give up on this model; otherwise announce, wait, retry.
- Success → report the real token use to the limiter and return the reply.
