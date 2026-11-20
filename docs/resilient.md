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
 13 | Hosted models (the ones with a limiter) share one internet connection, so when it is
 14 | gone - the server's name cannot be looked up, or a network error repeats - they are all
 15 | skipped for ``OFFLINE_COOLDOWN`` seconds and the local model answers at once.
 16 | """
 17 | from __future__ import annotations
 18 | 
 19 | import json
 20 | import re
 21 | import time
 22 | from dataclasses import dataclass
 23 | from typing import Callable
 24 | 
 25 | from .llm import ChatBackend, LLMError, Reply, TokenSink
 26 | from .ratelimit import QuotaExhausted, RateLimiter
 27 | 
 28 | RETRYABLE = {429, 500, 502, 503, 504}
 29 | QUOTA_VALUE = re.compile(r'quotaValue"?\s*:\s*"?(\d+)')
 30 | RETRY_DELAY = re.compile(r'retryDelay"?\s*:\s*"?(\d+(?:\.\d+)?)s')
 31 | OFFLINE_COOLDOWN = 60.0         # seconds before unreachable hosted models are tried again
 32 | UNREACHABLE = "unreachable (offline?)"
 33 | OFFLINE_HELP = ("Gemini cannot be reached - you seem to be offline. Work with the local model: "
 34 |                 "type /model offline (or start with --offline).")
 35 | 
 36 | 
```
- `json` measures message size; `re` finds `retryDelay` in error text; `time.sleep` waits; `dataclass` builds `Entry`.
- `RETRYABLE` lists the HTTP codes treated as temporary. `RETRY_DELAY` is a regular expression matching
  `retryDelay": "17s"` (quotes optional, decimals allowed) and capturing the number. `QUOTA_VALUE` captures the
  real limit from `"quotaValue": "20"` in Google's quota errors.
- `OFFLINE_COOLDOWN` (60 s): how long hosted models are skipped once the internet seems gone. `UNREACHABLE` is the
  wording that marks such a failure. `OFFLINE_HELP` is the error shown when Gemini is unreachable and no local model is
  set up — it says how to switch to one.

**Offline detection (version 0.6).** Before, a missing internet connection was treated like any temporary error: every
Gemini model was retried three times with growing pauses (2 + 4 + 8 s) before the local model was asked — 28 seconds
of waiting on *every* model call, every time, because nothing remembered that the network was down. Now:
```
hosted call fails, cannot even look up the name (DNS)  ─┐
hosted call fails twice with a network error           ─┼─► "offline": every hosted model is skipped
the 3-second probe cannot connect after a failure      ─┘   for 60 s; the local model answers at once
after 60 s: probe (no request, no quota) ── still down ──► skip for another 60 s
                                         └─ connects ────► try Gemini again ─ answers ─► "back online"
```

## `Entry`
```python
 37 | @dataclass
 38 | class Entry:
 39 |     """One model in the chain: a display name, its client, and an optional limiter."""
 40 | 
 41 |     name: str
 42 |     backend: ChatBackend
 43 |     limiter: RateLimiter | None = None
 44 | 
 45 | 
```
- One link of the chain: a name for messages, the client that talks to the model, and an optional `RateLimiter`
  (the local model has none — it has no quota).

## Helpers
```python
 46 | def estimate_tokens(messages: list[dict], tools: list[dict]) -> int:
 47 |     """Rough prompt size: about 4 characters per token (good enough for budgeting)."""
 48 |     return len(json.dumps(messages)) // 4 + len(json.dumps(tools)) // 4
 49 | 
 50 | 
```
- The limiter needs the request size *before* sending. Exact counting needs the model's tokenizer; ~4 characters per
  token is a standard estimate that is close enough for budgeting.

```python
 51 | def server_delay(error: LLMError) -> float | None:
 52 |     """Seconds the server asked us to wait (Google puts ``"retryDelay": "17s"`` in 429s)."""
 53 |     match = RETRY_DELAY.search(str(error))
 54 |     return float(match.group(1)) if match else None
 55 | 
 56 | 
```
- Returns the seconds the server asked us to wait, or `None` when the error text has no `retryDelay`.

```python
 57 | def _is_minute_quota(error: LLMError) -> bool:
 58 |     """Google's per-minute 429 names a per-minute quota id."""
 59 |     text = str(error).lower().replace("_", "").replace(" ", "")
 60 |     return error.status == 429 and "perminute" in text
 61 | 
 62 | 
```
- Recognises a 429 caused by a *per-minute* quota (its quota id contains `PerMinute`).

```python
 63 | def _is_daily_quota(error: LLMError) -> bool:
 64 |     """Google's 429 text mentions a per-day quota when the daily limit is the cause."""
 65 |     text = str(error).lower().replace("_", "").replace(" ", "")
 66 |     return error.status == 429 and ("perday" in text or "daily" in text)
 67 | 
 68 | 
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
 69 | class FallbackBackend:
 70 |     """A ChatBackend that tries several models in order until one answers."""
 71 | 
```
```python
 72 |     def __init__(self, entries: list[Entry], max_retries: int = 3,
 73 |                  sleep: Callable[[float], None] = time.sleep,
 74 |                  on_notice: Callable[[str], None] = lambda _m: None,
 75 |                  strong: list[Entry] | None = None,
 76 |                  probe: Callable[[], bool] | None = None,
 77 |                  clock: Callable[[], float] = time.monotonic) -> None:
 78 |         if not entries:
 79 |             raise ValueError("FallbackBackend needs at least one model")
 80 |         self._entries = entries
 81 |         self._strong = strong or []                # reserve models, see escalate()
 82 |         self._escalated = False
 83 |         self._unavailable: set[str] = set()       # models the server says do not exist (404)
 84 |         self._retries = max_retries
 85 |         self._sleep = sleep
 86 |         self._notice = on_notice
 87 |         self._probe = probe                        # quick "is the internet back?" check
 88 |         self._clock = clock
 89 |         self._retry_at = 0.0                       # when unreachable hosted models are tried again
 90 |         self.offline = False                       # True while the hosted models are unreachable
 91 |         self.last_model = ""                        # which model answered most recently
 92 | 
```
- Needs at least one entry. `strong` is the optional **reserve** list (the strong models with tiny daily quotas);
  `_escalated` says whether they currently go first. `_unavailable` remembers models that answered 404. `sleep` is injectable for tests. `on_notice` receives short messages such as
  "gemini-3.5-flash busy (503); retrying in 2s" (the CLI prints them in brackets). `last_model` remembers who answered.
- `probe` is the quick connection check (`llm.reachable` for Gemini's address); `clock` measures the cool-down (a
  fake clock in tests); `_retry_at` is when hosted models may be tried again; `offline` says whether they are
  currently skipped (`/stats` and `/model` show it).

```python
 93 |     def status(self) -> list[str]:
 94 |         """Daily usage lines for every rate-limited model (plus the offline state)."""
 95 |         lines = [e.limiter.status() for e in self._strong + self._entries if e.limiter]
 96 |         if self.offline:
 97 |             lines.append("offline: Gemini is unreachable, so the local model answers")
 98 |         return lines
 99 | 
```
- Daily usage lines of all rate-limited models, strong ones included, plus an `offline:` line while Gemini is being
  skipped (used by `/stats`).

```python
100 |     @staticmethod
101 |     def _hosted(entry: Entry) -> bool:
102 |         """Hosted models are the rate-limited ones; the local model has no limiter."""
103 |         return entry.limiter is not None
104 | 
```
- Hosted models are recognised by their rate limiter; the local model has none.

```python
105 |     def _hosted_down(self) -> bool:
106 |         """True while hosted models must be skipped because the internet seems to be gone."""
107 |         if not self.offline:
108 |             return False
109 |         if self._clock() < self._retry_at:
110 |             return True
111 |         if self._probe is not None and not self._probe():    # still no connection: wait more
112 |             self._retry_at = self._clock() + OFFLINE_COOLDOWN
113 |             return True
114 |         return False                                         # worth a real try again
115 | 
```
- Decides whether to skip hosted models now: not when online; yes during the cool-down; after it, yes again (with a new
  cool-down) when the probe still cannot connect — that costs 3 seconds at most instead of a failing request;
  otherwise no, so the next call tries Gemini for real.

```python
116 |     def _go_offline(self, has_local: bool) -> None:
117 |         """Skip hosted models for a while; say so the first time."""
118 |         if not self.offline:
119 |             self._notice("Gemini is unreachable (no internet?): "
120 |                          + ("the local model answers for now" if has_local else "no local model is set up")
121 |                          + f"; trying Gemini again in {OFFLINE_COOLDOWN:.0f}s")
122 |         self.offline = True
123 |         self._retry_at = self._clock() + OFFLINE_COOLDOWN
124 | 
```
- Starts (or extends) the cool-down. The first time, it says so once, e.g. `[Gemini is unreachable (no internet?): the
  local model answers for now; trying Gemini again in 60s]`.

```python
125 |     def escalate(self) -> bool:
126 |         """Put the strong reserve models first; True only when this changes anything."""
127 |         if self._escalated or not self._strong:
128 |             return False
129 |         self._escalated = True
130 |         return True
131 | 
```
```python
132 |     def reset_route(self) -> None:
133 |         """Back to the everyday order (called at the start of every request)."""
134 |         self._escalated = False
135 | 
```
```python
136 |     def _order(self) -> list[Entry]:
137 |         """Everyday: cheap models, then strong, then local. Escalated: strong first."""
138 |         local = [e for e in self._entries if e.limiter is None]
139 |         cheap = [e for e in self._entries if e.limiter is not None]
140 |         return (self._strong + cheap if self._escalated else cheap + self._strong) + local
141 | 
```
- `escalate` puts the reserve first and returns `True` only if that changed something (so the agent announces it once);
  with no reserve it returns `False`. `reset_route` restores the everyday order; the agent calls it at the start of
  every request. `_order` builds the list: local entries (no limiter) always last.

```python
142 |     def chat(self, messages: list[dict], tools: list[dict],
143 |              on_token: TokenSink | None = None) -> Reply:
144 |         """Ask each model in turn; return the first answer."""
145 |         problems: list[str] = []
146 |         estimate = estimate_tokens(messages, tools)
147 |         order = self._order()
148 |         has_local = any(not self._hosted(e) for e in order)
149 |         hosted_down = self._hosted_down()
150 |         for entry in order:
151 |             if self._hosted(entry) and hosted_down:
152 |                 problems.append(f"{entry.name}: {UNREACHABLE}")
153 |                 continue
154 |             if entry.name in self._unavailable:
155 |                 problems.append(f"{entry.name}: not available to this key")
156 |                 continue
157 |             if entry.limiter and entry.limiter.exhausted():
158 |                 problems.append(f"{entry.name}: daily quota used up")
159 |                 continue
160 |             reply = self._try(entry, messages, tools, on_token, estimate, problems)
161 |             if reply is not None:
162 |                 if self._hosted(entry) and self.offline:
163 |                     self.offline = False
164 |                     self._notice(f"Gemini is reachable again: back online ({entry.name})")
165 |                 self.last_model = entry.name
166 |                 return reply
167 |             if self._hosted(entry) and problems and problems[-1].endswith(UNREACHABLE):
168 |                 self._go_offline(has_local)            # the other hosted models share the line
169 |                 hosted_down = True
170 |         if hosted_down and not has_local:
171 |             raise LLMError(OFFLINE_HELP + "\n  " + "\n  ".join(problems))
172 |         raise LLMError("every model failed:\n  " + "\n  ".join(problems))
173 | 
```
- Estimates the prompt size once, then goes down the chain in `_order()`, skipping models known to be unavailable,
  and — while `_hosted_down()` — every hosted model. A model whose quota is already exhausted is skipped without sending
  anything. The first successful reply is returned and `last_model` updated; a hosted reply while `offline` was set
  ends the offline state with a `back online` notice.
- When a hosted model gave up because it was unreachable, `_go_offline` is called and the remaining hosted models are
  skipped at once (they share the same internet connection), so the local model answers immediately.
- If every model failed, one `LLMError` lists what went wrong with each, so you can see why. When the reason is "no
  internet" and there is no local model, the error starts with `OFFLINE_HELP`, which says how to work offline.

```python
174 |     def _try(self, entry: Entry, messages: list[dict], tools: list[dict],
175 |              on_token: TokenSink | None, estimate: int, problems: list[str]) -> Reply | None:
176 |         """Call one model with retries; None means 'give up on this model'."""
177 |         for attempt in range(self._retries + 1):
178 |             try:
179 |                 if entry.limiter:
180 |                     entry.limiter.before_call(estimate)
181 |                 reply = entry.backend.chat(messages, tools, on_token)
182 |             except QuotaExhausted as exc:
183 |                 problems.append(str(exc))
184 |                 return None
185 |             except LLMError as exc:
186 |                 if exc.final:                                     # e.g. no local model at all
187 |                     problems.append(f"{entry.name}: {exc}")
188 |                     return None
189 |                 if exc.status is None and self._hosted(entry) and (
190 |                         exc.dns or attempt >= 1 or (self._probe is not None and not self._probe())):
191 |                     problems.append(f"{entry.name}: {UNREACHABLE}")   # no internet: retrying
192 |                     return None                                       # would only wait in vain
193 |                 if _is_daily_quota(exc):
194 |                     found = QUOTA_VALUE.search(str(exc))
195 |                     limit = int(found.group(1)) if found else None
196 |                     if entry.limiter:
197 |                         entry.limiter.mark_exhausted(limit)
198 |                     told = f" (Google's limit: {limit}/day)" if limit else ""
199 |                     self._notice(f"{entry.name}: daily quota used up{told}; trying the next model")
200 |                     problems.append(f"{entry.name}: daily quota used up{told}")
201 |                     return None
202 |                 if exc.status == 404:                   # model retired or not offered to this key
203 |                     self._unavailable.add(entry.name)
204 |                     self._notice(f"{entry.name}: not available (HTTP 404); skipping it from now on")
205 |                     problems.append(f"{entry.name}: not available (HTTP 404)")
206 |                     return None
207 |                 if _is_minute_quota(exc) and entry.limiter:
208 |                     found = QUOTA_VALUE.search(str(exc))
209 |                     if found:
210 |                         entry.limiter.set_rpm(int(found.group(1)))   # learn the real limit
211 |                 if exc.status is not None and exc.status not in RETRYABLE:
212 |                     raise                           # our request is wrong: do not hide it
213 |                 if attempt == self._retries:
214 |                     problems.append(f"{entry.name}: {str(exc)[:200]}")
215 |                     return None
216 |                 asked = server_delay(exc)
217 |                 pause = min(asked + 1.0, 90.0) if asked is not None else min(2.0 ** (attempt + 1), 30.0)
218 |                 self._notice(f"{entry.name} busy ({exc.status or 'network'}); "
219 |                              f"retrying in {pause:.0f}s")
220 |                 self._sleep(pause)
221 |                 continue
222 |             if entry.limiter:
223 |                 entry.limiter.after_call(reply.prompt_tokens + reply.completion_tokens)
224 |             return reply
225 |         return None
```
- Up to `max_retries + 1` attempts on one model. Each attempt first asks the limiter for permission (it may wait).
- `QuotaExhausted` from our own limiter → give up on this model (return `None`).
- An error marked `final` (for example "no local model is installed") → give up at once: waiting cannot fix it.
- A hosted model that cannot be reached (no HTTP status) → give up and record it as unreachable when the name lookup
  failed (`dns`), when it is the second attempt, or when the probe cannot connect either. Retrying a missing internet
  connection only adds waiting.
- An `LLMError` that is a daily-quota 429 → read the real limit if present, mark the model exhausted (remembering
  the limit), announce `gemini-...: daily quota used up (Google's limit: 20/day); trying the next model`, and give up
  on this model.
- HTTP 404 → remember the model as unavailable, announce it once, move on.
- A per-minute 429 that states the real limit (`"quotaValue": "5"`) lowers that model's limiter (`set_rpm`), so
  the next requests wait on our side instead of being rejected again.
- A non-temporary status → re-raise (see the table above).
- A temporary error on the last attempt → note it and give up on this model; otherwise announce, wait, retry.
- Success → report the real token use to the limiter and return the reply.
