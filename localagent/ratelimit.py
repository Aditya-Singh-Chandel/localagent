"""Client-side rate limiting for hosted models with free-tier quotas (e.g. Gemini).

A free API key has three limits per model:
  * requests per minute (RPM)   -> we WAIT until a request is allowed
  * tokens per minute (TPM)     -> we WAIT until the tokens fit
  * requests per day (RPD)      -> we STOP using that model for the day
Waiting on our side is far cheaper than sending a request that the server
rejects with HTTP 429. The daily count is saved to a small JSON file so it
survives restarts. Google resets daily quotas at midnight Pacific time.
"""
from __future__ import annotations

import json
import os
import threading
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from .llm import LLMError

WINDOW = 60.0                       # seconds in the per-minute windows
WAIT_NOTICE = 3.0                   # waits at least this long are announced


class QuotaExhausted(LLMError):
    """The model's daily request quota is used up (try another model or tomorrow)."""


def _pacific_offset(utc: datetime) -> timedelta:
    """US Pacific time's distance from UTC at ``utc``: -7 h during daylight saving time (from
    the 2nd Sunday of March, 2 a.m., to the 1st Sunday of November, 2 a.m.), else -8 h."""
    march = datetime(utc.year, 3, 8, tzinfo=timezone.utc)          # 2nd Sunday: on/after the 8th
    begins = march + timedelta(days=(6 - march.weekday()) % 7, hours=10)     # 2 a.m. = 10:00 UTC
    november = datetime(utc.year, 11, 1, tzinfo=timezone.utc)
    ends = november + timedelta(days=(6 - november.weekday()) % 7, hours=9)  # 2 a.m. = 09:00 UTC
    return timedelta(hours=-7) if begins <= utc < ends else timedelta(hours=-8)


def pacific_day(now: float) -> str:
    """Calendar date in US Pacific time, the clock Google uses for daily resets."""
    try:
        from zoneinfo import ZoneInfo
        return datetime.fromtimestamp(now, ZoneInfo("America/Los_Angeles")).date().isoformat()
    except Exception:                         # no time-zone data (normal on Windows): US rules
        utc = datetime.fromtimestamp(now, timezone.utc)
        return (utc + _pacific_offset(utc)).date().isoformat()


def _write_atomic(path: Path, text: str) -> None:
    """Replace ``path`` in one step, so another agent never reads half a file (best effort)."""
    temporary = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(text, encoding="utf-8")
        for attempt in range(8):
            try:
                os.replace(temporary, path)
                return
            except PermissionError:            # Windows: another agent or a virus scanner has
                time.sleep(0.05 * (attempt + 1))   # one of the files open for a moment
    except OSError:
        pass                                   # counting is advisory: never stop a model call
    for _ in range(3):                         # do not leave the temporary file behind
        try:
            temporary.unlink(missing_ok=True)
            return
        except OSError:
            time.sleep(0.1)


_SHARED: dict[tuple, "RateLimiter"] = {}
_SHARED_LOCK = threading.Lock()


def shared_limiter(model: str, rpm: int, tpm: int, rpd: int, usage_file: Path | None,
                   on_wait: Callable[[str], None] | None = None) -> "RateLimiter":
    """The one limiter for this model in this program.

    Every server session builds its own model chain; if each had its own limiter, ten
    sessions would each believe they may send the whole per-minute limit.
    """
    key = (model, rpm, tpm, rpd, str(usage_file))
    with _SHARED_LOCK:
        if key not in _SHARED:
            _SHARED[key] = RateLimiter(model, rpm, tpm, rpd, usage_file, on_wait=on_wait)
        return _SHARED[key]


class RateLimiter:
    """Tracks one model's usage and blocks or refuses calls that would break its limits."""

    def __init__(self, model: str, rpm: int, tpm: int, rpd: int, usage_file: Path | None = None,
                 clock: Callable[[], float] = time.time,
                 sleep: Callable[[float], None] = time.sleep,
                 on_wait: Callable[[str], None] | None = None) -> None:
        self.model = model
        self._rpm, self._tpm, self._rpd = rpm, tpm, rpd
        self._file = usage_file
        self._memory: dict = {}                          # used when there is no file
        self._clock, self._sleep = clock, sleep
        self._on_wait = on_wait                          # told about long waits (None = silent)
        self._requests: deque[float] = deque()          # send times in the last minute
        self._tokens: deque[tuple[float, int]] = deque()  # (time, tokens) in the last minute
        self._lock = threading.Lock()

    # ----- daily counters, shared by all models in one JSON file --------------
    def _load(self) -> dict:
        """Whole usage record: {"day": "YYYY-MM-DD", "models": {name: counters}}."""
        if self._file is None:
            return self._memory
        try:
            data = json.loads(self._file.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}                                   # missing or unreadable: start fresh

    def _counters(self) -> dict:
        """This model's counters for today; a new day starts from zero."""
        data = self._load()
        if data.get("day") != pacific_day(self._clock()):
            return {"requests": 0, "tokens": 0}
        return dict(data.get("models", {}).get(self.model, {"requests": 0, "tokens": 0}))

    def _store(self, counters: dict) -> None:
        """Save this model's counters, keeping other models' counters for the same day."""
        data = self._load()
        day = pacific_day(self._clock())
        if data.get("day") != day:
            data = {"day": day, "models": {}}
        data.setdefault("models", {})[self.model] = counters
        if self._file is None:
            self._memory = data
            return
        _write_atomic(self._file, json.dumps(data, indent=1))

    # ----- public API ---------------------------------------------------------
    def set_rpm(self, rpm: int) -> None:
        """Adopt a lower requests-per-minute limit learned from the server, and save it for today."""
        with self._lock:
            if 0 < rpm < self._rpm:
                self._rpm = rpm
                counters = self._counters()
                counters["learned_rpm"] = rpm
                self._store(counters)

    def exhausted(self) -> bool:
        """True when the daily quota is used up (by our count, or because the server said so)."""
        with self._lock:
            counters = self._counters()
        return bool(counters.get("exhausted")) or int(counters["requests"]) >= self._rpd

    def mark_exhausted(self, server_limit: int | None = None) -> None:
        """The server said the daily quota is gone: stop using this model today.

        ``server_limit`` is the real daily limit when the server told us (Google's
        429 contains ``"quotaValue": "20"``); it is remembered so status lines show it.
        """
        with self._lock:
            counters = self._counters()
            counters["exhausted"] = True
            if server_limit:
                counters["server_limit"] = server_limit
            self._store(counters)

    def before_call(self, estimated_tokens: int) -> None:
        """Wait until a request of about ``estimated_tokens`` is allowed; count it as sent."""
        if estimated_tokens > self._tpm:
            raise LLMError(f"request of ~{estimated_tokens} tokens exceeds {self.model}'s "
                           f"limit of {self._tpm} tokens per minute; reduce num_ctx")
        with self._lock:
            counters = self._counters()
            if counters.get("exhausted") or int(counters["requests"]) >= self._rpd:
                raise QuotaExhausted(f"{self.model}: daily quota used up")
            learned = int(counters.get("learned_rpm", 0))
            if 0 < learned < self._rpm:                     # learned earlier today, maybe by
                self._rpm = learned                         # another run of the agent
            told = False
            while True:
                now = self._clock()
                while self._requests and now - self._requests[0] >= WINDOW:
                    self._requests.popleft()
                while self._tokens and now - self._tokens[0][0] >= WINDOW:
                    self._tokens.popleft()
                waits = []
                if len(self._requests) >= self._rpm:
                    waits.append(self._requests[0] + WINDOW - now)
                used = sum(count for _, count in self._tokens)
                if self._tokens and used + estimated_tokens > self._tpm:
                    waits.append(self._tokens[0][0] + WINDOW - now)
                if not waits:
                    break
                pause = max(min(waits), 0.05)
                if pause >= WAIT_NOTICE and self._on_wait and not told:
                    self._on_wait(f"{self.model}: waiting {pause:.0f}s for the per-minute limit")
                    told = True
                self._sleep(pause)                      # wait for the oldest entry to age out
            self._requests.append(now)
            counters["requests"] = int(counters["requests"]) + 1   # failed sends count too
            self._store(counters)

    def after_call(self, tokens: int) -> None:
        """Record the tokens a finished call really used."""
        with self._lock:
            self._tokens.append((self._clock(), tokens))
            counters = self._counters()
            counters["tokens"] = int(counters["tokens"]) + tokens
            self._store(counters)

    def status(self) -> str:
        """One line for the user: requests and tokens used today."""
        with self._lock:
            counters = self._counters()
        limit = counters.get("server_limit", self._rpd)
        line = f"{self.model}: {counters['requests']}/{limit} requests, {counters['tokens']} tokens today"
        return line + (" - quota used up until midnight Pacific" if counters.get("exhausted") else "")
