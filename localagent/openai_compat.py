"""Client for any OpenAI-compatible server (LM Studio, llama.cpp, vLLM, ...).

This is the scaling path: run the same agent against a bigger model on a GPU
box by pointing ``--backend openai --api-base http://host:port/v1`` at it.
Internal messages use Ollama's shape, so they are converted on the way out and
back in.
"""
from __future__ import annotations

import json
import time
from typing import Iterable

from .config import Config
from .llm import LLMError, Reply, TokenSink, post

# Google's documented placeholder for tool calls that have no signature of their own
# (e.g. made by another model before a fallback switch). Gemini 3 rejects them otherwise.
PLACEHOLDER_SIGNATURE = {"google": {"thought_signature": "skip_thought_signature_validator"}}


def to_openai(messages: list[dict], gemini: bool = False) -> list[dict]:
    """Convert internal (Ollama-shaped) messages to OpenAI chat messages.

    For ``gemini``, every tool call carries a thought signature - its own, or Google's
    placeholder when another model made the call - so switching models mid-task works.
    Other servers never see Google's signatures (a conversation may move from Gemini to the
    local model at any time, and a strict server could reject the unknown field).
    """
    converted: list[dict] = []
    for message in messages:
        if message["role"] == "assistant" and message.get("tool_calls"):
            calls = []
            for call in message["tool_calls"]:
                function = call["function"]
                arguments = function.get("arguments", {})
                entry = {
                    "id": call.get("id", "call_0"),
                    "type": "function",
                    "function": {
                        "name": function["name"],
                        "arguments": arguments if isinstance(arguments, str)
                        else json.dumps(arguments),
                    },
                }
                if gemini:                           # its own signature, else the placeholder
                    entry["extra_content"] = call.get("extra_content") or PLACEHOLDER_SIGNATURE
                calls.append(entry)
            converted.append({"role": "assistant", "content": message.get("content") or None,
                              "tool_calls": calls})
        elif message["role"] == "tool":
            converted.append({"role": "tool", "tool_call_id": message.get("tool_call_id", ""),
                              "content": message["content"]})
        else:
            converted.append({"role": message["role"], "content": message["content"]})
    return converted


def _kept_call(call: dict) -> dict:
    """A tool call in our internal shape; a thought signature is kept for the next turn."""
    kept = {"id": call.get("id"), "function": {
        "name": call["function"]["name"], "arguments": call["function"]["arguments"]}}
    if "extra_content" in call:                        # must be sent back unchanged next turn
        kept["extra_content"] = call["extra_content"]
    return kept


def read_stream(lines: Iterable[bytes], on_token: TokenSink | None) -> tuple[dict, dict]:
    """Assemble a streamed answer: ``data: {...}`` lines (Server-Sent Events) until ``[DONE]``.

    Text pieces go to ``on_token`` as they arrive. Tool calls arrive in pieces too, keyed by
    their ``index``; names and argument text are concatenated. Returns (message, usage).
    """
    text: list[str] = []
    calls: dict[int, dict] = {}
    usage: dict = {}
    for raw in lines:
        line = raw.decode("utf-8", errors="replace").strip()
        if not line.startswith("data:"):
            continue                                    # blank lines and comments
        data = line[5:].strip()
        if data == "[DONE]":
            break
        try:
            chunk = json.loads(data)
        except ValueError:
            continue
        if not isinstance(chunk, dict):
            continue
        if chunk.get("error"):
            raise LLMError(f"Server error in stream: {chunk['error']}")
        usage = chunk.get("usage") or usage             # usually only in the last chunk
        for choice in chunk.get("choices") or []:
            delta = choice.get("delta") or {}
            piece = delta.get("content")
            if piece:
                text.append(piece)
                if on_token:
                    on_token(piece)
            for part in delta.get("tool_calls") or []:
                slot = calls.setdefault(part.get("index", len(calls)),
                                        {"id": None, "function": {"name": "", "arguments": ""}})
                slot["id"] = part.get("id") or slot["id"]
                function = part.get("function") or {}
                slot["function"]["name"] += function.get("name") or ""
                slot["function"]["arguments"] += function.get("arguments") or ""
                if part.get("extra_content"):
                    slot["extra_content"] = part["extra_content"]
    message: dict = {"role": "assistant", "content": "".join(text)}
    if calls:
        message["tool_calls"] = [_kept_call(calls[i]) for i in sorted(calls)]
    return message, usage


class OpenAICompatClient:
    """Chat client for ``/chat/completions`` servers (streaming by default)."""

    def __init__(self, config: Config) -> None:
        self._config = config

    def chat(self, messages: list[dict], tools: list[dict],
             on_token: TokenSink | None = None) -> Reply:
        """Send the conversation and return the model's next message."""
        payload: dict = {
            "model": self._config.model,
            "messages": to_openai(messages, gemini=self._config.api_base == self._config.gemini_base),
            "temperature": self._config.temperature,
            "stream": self._config.stream,
        }
        if self._config.stream:                      # ask for token counts in the last chunk
            payload["stream_options"] = {"include_usage": True}
        if tools:                                    # some servers reject an empty list
            payload["tools"] = tools
        if self._config.reasoning_effort:            # e.g. "low": less hidden thinking
            payload["reasoning_effort"] = self._config.reasoning_effort
        headers = {"Authorization": f"Bearer {self._config.api_key}"} if self._config.api_key else {}
        started = time.monotonic()
        with post(self._config.api_base + "/chat/completions", payload, headers,
                  timeout=self._config.request_timeout) as response:
            if self._config.stream:
                try:
                    message, usage = read_stream(response, on_token)
                except (TimeoutError, ConnectionError) as exc:
                    raise LLMError(f"The connection broke during the answer: {exc}") from exc
            else:
                try:
                    body = json.load(response)
                    raw = body["choices"][0]["message"]
                except (ValueError, KeyError, IndexError, TypeError) as exc:
                    raise LLMError(f"The server's answer had an unexpected format ({exc!r})") from exc
                message = {"role": "assistant", "content": raw.get("content") or ""}
                if raw.get("tool_calls"):
                    message["tool_calls"] = [_kept_call(call) for call in raw["tool_calls"]]
                if on_token and message["content"]:
                    on_token(message["content"])     # whole text at once
                usage = body.get("usage") or {}
        prompt = int(usage.get("prompt_tokens", 0))
        # total can exceed prompt + completion when the model "thinks" in hidden tokens;
        # those still count against quotas, so they are added to the completion side.
        completion = max(int(usage.get("completion_tokens", 0)),
                         int(usage.get("total_tokens", 0)) - prompt)
        return Reply(
            message=message,
            prompt_tokens=prompt,
            completion_tokens=completion,
            seconds=time.monotonic() - started,
        )
