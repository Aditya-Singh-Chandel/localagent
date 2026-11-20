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
@@ localagent/resilient.py header @@
- `json` measures message size; `re` finds `retryDelay` in error text; `time.sleep` waits; `dataclass` builds `Entry`.
- `RETRYABLE` lists the HTTP codes treated as temporary. `RETRY_DELAY` is a regular expression matching
  `retryDelay": "17s"` (quotes optional, decimals allowed) and capturing the number. `QUOTA_VALUE` captures the
  real limit from `"quotaValue": "20"` in Google's quota errors.

## `Entry`
@@ localagent/resilient.py Entry @@
- One link of the chain: a name for messages, the client that talks to the model, and an optional `RateLimiter`
  (the local model has none — it has no quota).

## Helpers
@@ localagent/resilient.py estimate_tokens @@
- The limiter needs the request size *before* sending. Exact counting needs the model's tokenizer; ~4 characters per
  token is a standard estimate that is close enough for budgeting.

@@ localagent/resilient.py server_delay @@
- Returns the seconds the server asked us to wait, or `None` when the error text has no `retryDelay`.

@@ localagent/resilient.py _is_minute_quota @@
- Recognises a 429 caused by a *per-minute* quota (its quota id contains `PerMinute`).

@@ localagent/resilient.py _is_daily_quota @@
- Google's daily-quota 429 mentions a per-day quota id (for example `GenerateRequestsPerDay...`). The text is
  lower-cased and spaces/underscores removed so small wording differences still match.

## `FallbackBackend`
**Routing (cheap first, strong on demand):** normally the order is *cheap models → strong models → local model*. When
the agent sees repeated failures it calls `escalate()`, and until the next request the order becomes *strong → cheap →
local*. Why: on the free key the strong models allow only ~20 requests a day while the lite models allow far more, and
the lite models passed all eval tasks — so spending strong-model requests on routine steps would exhaust them for
nothing.

@@ localagent/resilient.py FallbackBackend @@
@@ localagent/resilient.py FallbackBackend.__init__ @@
- Needs at least one entry. `strong` is the optional **reserve** list (the strong models with tiny daily quotas);
  `_escalated` says whether they currently go first. `_unavailable` remembers models that answered 404. `sleep` is injectable for tests. `on_notice` receives short messages such as
  "gemini-3.5-flash busy (503); retrying in 2s" (the CLI prints them in brackets). `last_model` remembers who answered.

@@ localagent/resilient.py FallbackBackend.status @@
- Daily usage lines of all rate-limited models, strong ones included (used by `/stats`).

@@ localagent/resilient.py FallbackBackend.escalate @@
@@ localagent/resilient.py FallbackBackend.reset_route @@
@@ localagent/resilient.py FallbackBackend._order @@
- `escalate` puts the reserve first and returns `True` only if that changed something (so the agent announces it once);
  with no reserve it returns `False`. `reset_route` restores the everyday order; the agent calls it at the start of
  every request. `_order` builds the list: local entries (no limiter) always last.

@@ localagent/resilient.py FallbackBackend.chat @@
- Estimates the prompt size once, then goes down the chain in `_order()`, skipping models known to be unavailable. A model whose quota is already exhausted is skipped
  without sending anything. The first successful reply is returned and `last_model` updated.
- If every model failed, one `LLMError` lists what went wrong with each, so you can see why.

@@ localagent/resilient.py FallbackBackend._try @@
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
