# `localagent/meter.py` — usage meter

**Role in the flow:** after every model call, `agent.py` hands the `Reply` to the meter. The CLI prints the
running totals. Local models are free, but tokens still cost *time*, so this tells you where time goes.

## Lines 1–12 — docstring and imports
@@ localagent/meter.py 1-12 @@
- Lines 1–5: docstring with rough CPU speeds. Line 6: lazy hints. Line 8: `dataclass`.
- Line 10: imports `Reply` (the type of what we record). Lines 7, 9, 11, 12: blank separators.

## Lines 13–21 — the counters
@@ localagent/meter.py 13-21 @@
- Line 13: `@dataclass` (mutable — the totals change). Lines 14–15: class and docstring.
- Lines 17–20: four counters, all starting at zero.

## Lines 22–28 — `record`
@@ localagent/meter.py 22-28 @@
- Line 22–23: method taking one `Reply`. Line 24 +1 call. Lines 25–26 add the token counts.
  Line 27 adds the seconds.

## Lines 29–34 — `summary`
@@ localagent/meter.py 29-34 @@
- Lines 31–34: returns one formatted line. `{self.seconds:.1f}` prints one decimal place.
  Adjacent string literals on lines 32–33 are joined by Python into one string.
