"""Shared model-server plumbing plus the streaming Ollama client.

Ollama exposes ``POST /api/chat``. We send the conversation plus the tool
definitions and read back a stream of small JSON lines; text pieces are shown
to the user as they arrive and joined into one assistant message at the end.
"""
from __future__ import annotations

import json
import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable, Protocol
from urllib.parse import urlsplit

from .config import Config

TokenSink = Callable[[str], None]
REMOVED = "[old tool output removed to save context]"
SHORTENED = "[removed to save context]"
LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "0.0.0.0", "host.docker.internal"})


class LLMError(RuntimeError):
    """Raised when the model server cannot be reached or rejects a request.

    ``status`` is the HTTP status code when the server answered with an error, and
    ``None`` when it could not be reached at all (network problem or timeout). ``dns``
    is True when even the server's name could not be looked up - almost always: no internet.
    ``final`` is True when retrying cannot help (for example: no local model is installed).
    """

    def __init__(self, message: str, status: int | None = None, dns: bool = False,
                 final: bool = False) -> None:
        super().__init__(message)
        self.status = status
        self.dns = dns
        self.final = final


def is_local_url(url: str) -> bool:
    """True when ``url`` points at this computer (a local model server)."""
    return (urlsplit(url).hostname or "") in LOCAL_HOSTS


def reachable(url: str, timeout: float = 3.0) -> bool:
    """True when a network connection to ``url``'s server opens within ``timeout`` seconds.

    Much cheaper than a model request, so it is used to check whether the internet is back.
    """
    parts = urlsplit(url)
    port = parts.port or (443 if parts.scheme == "https" else 80)
    try:
        with socket.create_connection((parts.hostname or "", port), timeout=timeout):
            return True
    except (OSError, ValueError):
        return False


def message_size(message: dict) -> int:
    """Characters a message adds to the prompt: its text plus its tool calls' arguments."""
    size = len(str(message.get("content") or ""))
    for call in message.get("tool_calls") or []:
        arguments = call.get("function", {}).get("arguments", "")
        size += len(arguments if isinstance(arguments, str) else json.dumps(arguments))
    return size


def _shorten_call(call: dict) -> dict:
    """A copy of an old tool call whose long arguments (file contents, edits) are replaced.

    Short ones such as ``path`` stay, so the history still says which file was written.
    The thought signature is dropped: it belonged to the original arguments.
    """
    function = call.get("function", {})
    arguments = function.get("arguments", {})
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except ValueError:
            arguments = {}
    if not isinstance(arguments, dict):
        arguments = {}
    short = {key: value if len(json.dumps(value)) <= 200 else SHORTENED
             for key, value in arguments.items()}
    kept = {key: value for key, value in call.items() if key != "extra_content"}
    return {**kept, "function": {**function, "arguments": short}}


def fit_history(messages: list[dict], budget: int, keep_last: int = 4) -> list[dict]:
    """A copy of ``messages`` that fits ``budget`` characters (about 4 per token).

    Cheapest losses first: the oldest tool outputs are blanked, then the bulky arguments of
    old tool calls are shortened, and only then are the oldest whole exchanges dropped (from
    one user message to the next, so every remaining tool result still follows its call).
    The system prompt and the newest ``keep_last`` messages are never changed.
    """
    result = list(messages)
    size = sum(message_size(m) for m in result)
    protected = max(len(result) - keep_last, 1)
    for index in range(1, protected):                       # 1. old tool outputs
        if size <= budget:
            return result
        message = result[index]
        if message.get("role") == "tool" and message.get("content") != REMOVED:
            size -= len(str(message.get("content") or "")) - len(REMOVED)
            result[index] = {**message, "content": REMOVED}
    for index in range(1, protected):                       # 2. old tool-call arguments
        if size <= budget:
            return result
        message = result[index]
        if message.get("tool_calls"):
            shorter = {**message, "tool_calls": [_shorten_call(c) for c in message["tool_calls"]]}
            size -= message_size(message) - message_size(shorter)
            result[index] = shorter
    while size > budget:                                    # 3. the oldest exchanges
        starts = [i for i in range(2, len(result)) if result[i].get("role") == "user"]
        if not starts:
            break                                           # one huge request: nothing to drop
        size -= sum(message_size(m) for m in result[1:starts[0]])
        result = [result[0]] + result[starts[0]:]
    return result


def _reach_hint(url: str) -> str:
    """What to try when ``url`` cannot be reached: start the local server, or check the internet."""
    if is_local_url(url):
        return ("Is the local model server running? In the chat, /model offline starts it "
                "(llama.cpp); for Ollama or LM Studio, open the app.")
    return "Check your internet connection, or work offline with the local model: /model offline"


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
        dns = isinstance(getattr(exc, "reason", None), socket.gaierror)   # name lookup failed
        raise LLMError(f"Cannot reach the model server at {url} ({exc}). {_reach_hint(url)}",
                       dns=dns) from exc


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
