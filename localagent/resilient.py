"""Retries, rate limits and fallback across several models.

    chat() ──► model 1 (rate-limited) ──fails/overloaded/quota──► model 2 ──► ... ──► local model

For each model in order:
  * its RateLimiter (if any) waits for a free slot or refuses when the daily
    quota is gone (then we move to the next model at once);
  * temporary failures (HTTP 429/500/502/503/504, network errors) are retried
    with growing pauses (2 s, 4 s, 8 s ...) up to ``max_retries`` times;
  * a "per day" 429 marks the model exhausted for today;
  * any other error (e.g. HTTP 400: our request is wrong) is raised at once,
    because retrying or switching model would not fix it.
Hosted models (the ones with a limiter) share one internet connection, so when it is
gone - the server's name cannot be looked up, or a network error repeats - they are all
skipped for ``OFFLINE_COOLDOWN`` seconds and the local model answers at once.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Callable

from .llm import ChatBackend, LLMError, Reply, TokenSink
from .ratelimit import QuotaExhausted, RateLimiter

RETRYABLE = {429, 500, 502, 503, 504}
QUOTA_VALUE = re.compile(r'quotaValue"?\s*:\s*"?(\d+)')
RETRY_DELAY = re.compile(r'retryDelay"?\s*:\s*"?(\d+(?:\.\d+)?)s')
OFFLINE_COOLDOWN = 60.0         # seconds before unreachable hosted models are tried again
UNREACHABLE = "unreachable (offline?)"
OFFLINE_HELP = ("Gemini cannot be reached - you seem to be offline. Work with the local model: "
                "type /model offline (or start with --offline).")


@dataclass
class Entry:
    """One model in the chain: a display name, its client, and an optional limiter."""

    name: str
    backend: ChatBackend
    limiter: RateLimiter | None = None


def estimate_tokens(messages: list[dict], tools: list[dict]) -> int:
    """Rough prompt size: about 4 characters per token (good enough for budgeting)."""
    return len(json.dumps(messages)) // 4 + len(json.dumps(tools)) // 4


def server_delay(error: LLMError) -> float | None:
    """Seconds the server asked us to wait (Google puts ``"retryDelay": "17s"`` in 429s)."""
    match = RETRY_DELAY.search(str(error))
    return float(match.group(1)) if match else None


def _is_minute_quota(error: LLMError) -> bool:
    """Google's per-minute 429 names a per-minute quota id."""
    text = str(error).lower().replace("_", "").replace(" ", "")
    return error.status == 429 and "perminute" in text


def _is_daily_quota(error: LLMError) -> bool:
    """Google's 429 text mentions a per-day quota when the daily limit is the cause."""
    text = str(error).lower().replace("_", "").replace(" ", "")
    return error.status == 429 and ("perday" in text or "daily" in text)


class FallbackBackend:
    """A ChatBackend that tries several models in order until one answers."""

    def __init__(self, entries: list[Entry], max_retries: int = 3,
                 sleep: Callable[[float], None] = time.sleep,
                 on_notice: Callable[[str], None] = lambda _m: None,
                 strong: list[Entry] | None = None,
                 probe: Callable[[], bool] | None = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        if not entries:
            raise ValueError("FallbackBackend needs at least one model")
        self._entries = entries
        self._strong = strong or []                # reserve models, see escalate()
        self._escalated = False
        self._unavailable: set[str] = set()       # models the server says do not exist (404)
        self._retries = max_retries
        self._sleep = sleep
        self._notice = on_notice
        self._probe = probe                        # quick "is the internet back?" check
        self._clock = clock
        self._retry_at = 0.0                       # when unreachable hosted models are tried again
        self.offline = False                       # True while the hosted models are unreachable
        self.last_model = ""                        # which model answered most recently

    def status(self) -> list[str]:
        """Daily usage lines for every rate-limited model (plus the offline state)."""
        lines = [e.limiter.status() for e in self._strong + self._entries if e.limiter]
        if self.offline:
            lines.append("offline: Gemini is unreachable, so the local model answers")
        return lines

    @staticmethod
    def _hosted(entry: Entry) -> bool:
        """Hosted models are the rate-limited ones; the local model has no limiter."""
        return entry.limiter is not None

    def _hosted_down(self) -> bool:
        """True while hosted models must be skipped because the internet seems to be gone."""
        if not self.offline:
            return False
        if self._clock() < self._retry_at:
            return True
        if self._probe is not None and not self._probe():    # still no connection: wait more
            self._retry_at = self._clock() + OFFLINE_COOLDOWN
            return True
        return False                                         # worth a real try again

    def _go_offline(self, has_local: bool) -> None:
        """Skip hosted models for a while; say so the first time."""
        if not self.offline:
            self._notice("Gemini is unreachable (no internet?): "
                         + ("the local model answers for now" if has_local else "no local model is set up")
                         + f"; trying Gemini again in {OFFLINE_COOLDOWN:.0f}s")
        self.offline = True
        self._retry_at = self._clock() + OFFLINE_COOLDOWN

    def escalate(self) -> bool:
        """Put the strong reserve models first; True only when this changes anything."""
        if self._escalated or not self._strong:
            return False
        self._escalated = True
        return True

    def reset_route(self) -> None:
        """Back to the everyday order (called at the start of every request)."""
        self._escalated = False

    def _order(self) -> list[Entry]:
        """Everyday: cheap models, then strong, then local. Escalated: strong first."""
        local = [e for e in self._entries if e.limiter is None]
        cheap = [e for e in self._entries if e.limiter is not None]
        return (self._strong + cheap if self._escalated else cheap + self._strong) + local

    def chat(self, messages: list[dict], tools: list[dict],
             on_token: TokenSink | None = None) -> Reply:
        """Ask each model in turn; return the first answer."""
        problems: list[str] = []
        estimate = estimate_tokens(messages, tools)
        order = self._order()
        has_local = any(not self._hosted(e) for e in order)
        hosted_down = self._hosted_down()
        for entry in order:
            if self._hosted(entry) and hosted_down:
                problems.append(f"{entry.name}: {UNREACHABLE}")
                continue
            if entry.name in self._unavailable:
                problems.append(f"{entry.name}: not available to this key")
                continue
            if entry.limiter and entry.limiter.exhausted():
                problems.append(f"{entry.name}: daily quota used up")
                continue
            reply = self._try(entry, messages, tools, on_token, estimate, problems)
            if reply is not None:
                if self._hosted(entry) and self.offline:
                    self.offline = False
                    self._notice(f"Gemini is reachable again: back online ({entry.name})")
                self.last_model = entry.name
                return reply
            if self._hosted(entry) and problems and problems[-1].endswith(UNREACHABLE):
                self._go_offline(has_local)            # the other hosted models share the line
                hosted_down = True
        if hosted_down and not has_local:
            raise LLMError(OFFLINE_HELP + "\n  " + "\n  ".join(problems))
        raise LLMError("every model failed:\n  " + "\n  ".join(problems))

    def _try(self, entry: Entry, messages: list[dict], tools: list[dict],
             on_token: TokenSink | None, estimate: int, problems: list[str]) -> Reply | None:
        """Call one model with retries; None means 'give up on this model'."""
        for attempt in range(self._retries + 1):
            try:
                if entry.limiter:
                    entry.limiter.before_call(estimate)
                reply = entry.backend.chat(messages, tools, on_token)
            except QuotaExhausted as exc:
                problems.append(str(exc))
                return None
            except LLMError as exc:
                if exc.final:                                     # e.g. no local model at all
                    problems.append(f"{entry.name}: {exc}")
                    return None
                if exc.status is None and self._hosted(entry) and (
                        exc.dns or attempt >= 1 or (self._probe is not None and not self._probe())):
                    problems.append(f"{entry.name}: {UNREACHABLE}")   # no internet: retrying
                    return None                                       # would only wait in vain
                if _is_daily_quota(exc):
                    found = QUOTA_VALUE.search(str(exc))
                    limit = int(found.group(1)) if found else None
                    if entry.limiter:
                        entry.limiter.mark_exhausted(limit)
                    told = f" (Google's limit: {limit}/day)" if limit else ""
                    self._notice(f"{entry.name}: daily quota used up{told}; trying the next model")
                    problems.append(f"{entry.name}: daily quota used up{told}")
                    return None
                if exc.status == 404:                   # model retired or not offered to this key
                    self._unavailable.add(entry.name)
                    self._notice(f"{entry.name}: not available (HTTP 404); skipping it from now on")
                    problems.append(f"{entry.name}: not available (HTTP 404)")
                    return None
                if _is_minute_quota(exc) and entry.limiter:
                    found = QUOTA_VALUE.search(str(exc))
                    if found:
                        entry.limiter.set_rpm(int(found.group(1)))   # learn the real limit
                if exc.status is not None and exc.status not in RETRYABLE:
                    raise                           # our request is wrong: do not hide it
                if attempt == self._retries:
                    problems.append(f"{entry.name}: {str(exc)[:200]}")
                    return None
                asked = server_delay(exc)
                pause = min(asked + 1.0, 90.0) if asked is not None else min(2.0 ** (attempt + 1), 30.0)
                self._notice(f"{entry.name} busy ({exc.status or 'network'}); "
                             f"retrying in {pause:.0f}s")
                self._sleep(pause)
                continue
            if entry.limiter:
                entry.limiter.after_call(reply.prompt_tokens + reply.completion_tokens)
            return reply
        return None
