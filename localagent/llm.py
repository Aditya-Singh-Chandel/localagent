"""Shared model-server plumbing plus the streaming Ollama client.

Ollama exposes ``POST /api/chat``. We send the conversation plus the tool
definitions and read back a stream of small JSON lines; text pieces are shown
to the user as they arrive and joined into one assistant message at the end.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable, Protocol

from .config import Config

TokenSink = Callable[[str], None]


class LLMError(RuntimeError):
    """Raised when the model server cannot be reached or rejects a request.

    ``status`` is the HTTP status code when the server answered with an error, and
    ``None`` when it could not be reached at all (network problem or timeout).
    """

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class Reply:
    """One model answer plus the numbers we need for cost/speed tracking."""

    message: dict          # {"role": "assistant", "content": ..., "tool_calls": [...]}
    prompt_tokens: int     # tokens the model had to read this call
    completion_tokens: int  # tokens the model generated this call
    seconds: float         # wall-clock time of the call


class ChatBackend(Protocol):
    """Anything with this ``chat`` method can drive the agent (servers, test fakes)."""

    def chat(self, messages: list[dict], tools: list[dict],
             on_token: TokenSink | None = None) -> Reply: ...


def post(url: str, payload: dict, headers: dict[str, str] | None = None,
         timeout: int = 900):
    """POST JSON and return the open response; turn every failure into LLMError."""
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", **(headers or {})},
        method="POST",
    )
    try:
        return urllib.request.urlopen(request, timeout=timeout)
    except urllib.error.HTTPError as exc:  # must come before URLError (subclass)
        detail = exc.read().decode("utf-8", errors="replace")
        exc.close()                        # release the connection
        raise LLMError(f"Server returned HTTP {exc.code}: {detail}", status=exc.code) from exc
    except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
        raise LLMError(
            f"Cannot reach the model server at {url} ({exc}). Is it running? "
            "For Ollama: open the Ollama app or run `ollama serve`."
        ) from exc


class OllamaClient:
    """Streams chat requests to Ollama and converts failures into LLMError."""

    def __init__(self, config: Config) -> None:
        self._config = config

    def chat(self, messages: list[dict], tools: list[dict],
             on_token: TokenSink | None = None) -> Reply:
        """Send the conversation and return the model's next message."""
        payload = {
            "model": self._config.model,
            "messages": messages,
            "tools": tools,
            "stream": True,           # receive text as it is produced
            "keep_alive": "30m",      # keep the model in RAM between calls (saves load time)
            "options": {
                "num_ctx": self._config.num_ctx,
                "temperature": self._config.temperature,
            },
        }
        started = time.monotonic()
        text_parts: list[str] = []
        tool_calls: list[dict] = []
        final: dict = {}
        with post(self._config.host + "/api/chat", payload) as response:
            try:
                for raw in response:          # one JSON object per line
                    if not raw.strip():
                        continue
                    chunk = json.loads(raw)
                    if "error" in chunk:
                        raise LLMError(f"Ollama error: {chunk['error']}")
                    piece = chunk.get("message", {})
                    text = piece.get("content") or ""
                    if text:
                        text_parts.append(text)
                        if on_token:
                            on_token(text)
                    tool_calls.extend(piece.get("tool_calls") or [])
                    if chunk.get("done"):
                        final = chunk         # last line carries the token counts
            except (TimeoutError, ConnectionError) as exc:
                raise LLMError(f"Connection to Ollama was lost: {exc}") from exc
        message: dict = {"role": "assistant", "content": "".join(text_parts)}
        if tool_calls:
            message["tool_calls"] = tool_calls
        return Reply(
            message=message,
            prompt_tokens=int(final.get("prompt_eval_count", 0)),
            completion_tokens=int(final.get("eval_count", 0)),
            seconds=time.monotonic() - started,
        )
