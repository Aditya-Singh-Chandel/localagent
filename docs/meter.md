# `localagent/meter.py` — usage meter

**Role in the flow:** after every model call, `agent.py` hands the `Reply` to the meter. The CLI prints the
running totals. Local models are free, but tokens still cost *time*, so this tells you where time goes.

## Lines 1–12 — docstring and imports
```python
 1 | """Usage meter: counts model calls, tokens and time.
 2 | 
 3 | Local models cost no money, but tokens still cost *time* (a CPU reads roughly
 4 | 50-200 prompt tokens/s and writes 2-8 tokens/s), so we track them.
 5 | """
 6 | from __future__ import annotations
 7 | 
 8 | from dataclasses import dataclass
 9 | 
10 | from .llm import Reply
11 | 
12 | 
```
- Lines 1–5: docstring with rough CPU speeds. Line 6: lazy hints. Line 8: `dataclass`.
- Line 10: imports `Reply` (the type of what we record). Lines 7, 9, 11, 12: blank separators.

## Lines 13–21 — the counters
```python
13 | @dataclass
14 | class Meter:
15 |     """Running totals for one session."""
16 | 
17 |     calls: int = 0
18 |     prompt_tokens: int = 0
19 |     completion_tokens: int = 0
20 |     seconds: float = 0.0
21 | 
```
- Line 13: `@dataclass` (mutable — the totals change). Lines 14–15: class and docstring.
- Lines 17–20: four counters, all starting at zero.

## Lines 22–28 — `record`
```python
22 |     def record(self, reply: Reply) -> None:
23 |         """Add one model reply to the totals."""
24 |         self.calls += 1
25 |         self.prompt_tokens += reply.prompt_tokens
26 |         self.completion_tokens += reply.completion_tokens
27 |         self.seconds += reply.seconds
28 | 
```
- Line 22–23: method taking one `Reply`. Line 24 +1 call. Lines 25–26 add the token counts.
  Line 27 adds the seconds.

## Lines 29–34 — `summary`
```python
29 |     def summary(self) -> str:
30 |         """One human-readable line for the CLI."""
31 |         return (
32 |             f"{self.calls} model calls | {self.prompt_tokens} prompt tok | "
33 |             f"{self.completion_tokens} output tok | {self.seconds:.1f}s"
34 |         )
```
- Lines 31–34: returns one formatted line. `{self.seconds:.1f}` prints one decimal place.
  Adjacent string literals on lines 32–33 are joined by Python into one string.
