"""Usage meter: counts model calls, tokens and time.

Local models cost no money, but tokens still cost *time* (a CPU reads roughly
50-200 prompt tokens/s and writes 2-8 tokens/s), so we track them.
"""
from __future__ import annotations

from dataclasses import dataclass

from .llm import Reply


@dataclass
class Meter:
    """Running totals for one session."""

    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    seconds: float = 0.0

    def record(self, reply: Reply) -> None:
        """Add one model reply to the totals."""
        self.calls += 1
        self.prompt_tokens += reply.prompt_tokens
        self.completion_tokens += reply.completion_tokens
        self.seconds += reply.seconds

    def summary(self) -> str:
        """One human-readable line for the CLI."""
        return (
            f"{self.calls} model calls | {self.prompt_tokens} prompt tok | "
            f"{self.completion_tokens} output tok | {self.seconds:.1f}s"
        )
