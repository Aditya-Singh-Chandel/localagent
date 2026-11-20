"""A minimal MCP (Model Context Protocol) client for local "stdio" servers.

MCP is the standard Claude Code uses to plug in extra tools (databases, browsers,
issue trackers, ...). A server is a program we start; we talk to it with JSON-RPC
2.0 messages, one JSON object per line, over its stdin/stdout:

    -> {"jsonrpc":"2.0","id":1,"method":"initialize","params":{...}}
    <- {"jsonrpc":"2.0","id":1,"result":{...}}
    -> {"jsonrpc":"2.0","method":"notifications/initialized"}
    -> {"jsonrpc":"2.0","id":2,"method":"tools/list"}            (each tool: name, description, inputSchema)
    -> {"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":..., "arguments":{...}}}

A background thread reads the server's answers and hands each one to the request
waiting for its id, so a slow or silent server can never freeze the agent (every
request has a timeout).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
from pathlib import Path

from . import __version__

PROTOCOL_VERSION = "2025-06-18"
SCHEMA_KEYS = {"type", "description", "properties", "required", "items", "enum", "format",
               "minimum", "maximum", "minItems", "maxItems", "nullable", "anyOf"}


class MCPError(Exception):
    """The server failed to start, answered with an error, or did not answer in time."""


def clean_schema(schema: object) -> dict:
    """Keep only JSON-schema keywords that every model API accepts (Gemini is strict)."""
    if not isinstance(schema, dict):
        return {"type": "object", "properties": {}}
    cleaned: dict = {}
    for key, value in schema.items():
        if key not in SCHEMA_KEYS:
            continue                                        # e.g. "$schema", "additionalProperties"
        if key == "type" and isinstance(value, list):       # ["string", "null"] -> string + nullable
            real = [t for t in value if t != "null"]
            cleaned["type"] = real[0] if real else "string"
            if "null" in value:
                cleaned["nullable"] = True
        elif key == "properties" and isinstance(value, dict):
            cleaned["properties"] = {name: clean_schema(sub) for name, sub in value.items()}
        elif key == "items":
            cleaned["items"] = clean_schema(value)
        elif key == "anyOf" and isinstance(value, list):
            cleaned["anyOf"] = [clean_schema(sub) for sub in value]
        else:
            cleaned[key] = value
    if "type" not in cleaned and "anyOf" not in cleaned:
        cleaned["type"] = "object" if "properties" in cleaned else "string"
    return cleaned


class MCPServer:
    """One running MCP server and the tools it offers."""

    def __init__(self, name: str, command: str, args: list[str] | None = None,
                 env: dict[str, str] | None = None, cwd: Path | None = None,
                 timeout: float = 30.0) -> None:
        self.name = name
        self._argv = [shutil.which(command) or command, *(args or [])]   # finds npx.cmd etc.
        self._env = {**os.environ, **(env or {})}
        self._cwd = cwd
        self._timeout = timeout
        self._proc: subprocess.Popen | None = None
        self._next_id = 0
        self._answers: dict[int, dict] = {}
        self._ready = threading.Condition()
        self._write_lock = threading.Lock()
        self._closed = False
        self._reader: threading.Thread | None = None
        self.tools: list[dict] = []

    # ----- lifecycle -----------------------------------------------------------
    def start(self) -> "MCPServer":
        """Launch the program, do the MCP handshake and fetch the tool list."""
        try:
            self._proc = subprocess.Popen(self._argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                          stderr=subprocess.DEVNULL, cwd=self._cwd, env=self._env)
        except OSError as exc:
            raise MCPError(f"cannot start MCP server '{self.name}': {exc}") from None
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()
        self.request("initialize", {"protocolVersion": PROTOCOL_VERSION, "capabilities": {},
                                    "clientInfo": {"name": "localagent", "version": __version__}})
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        self.tools = self._list_tools()
        return self

    def close(self) -> None:
        """Stop the server as the MCP spec advises: end its input, wait, terminate, kill."""
        self._closed = True
        proc = self._proc
        if proc is None:
            return
        with self._write_lock:                              # no message is half-written
            try:
                if proc.stdin is not None:
                    proc.stdin.close()                      # end of input: servers exit on it
            except OSError:
                pass
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        if self._reader is not None:
            self._reader.join(timeout=2)                    # it stops at the end of the output
            if not self._reader.is_alive() and proc.stdout is not None:
                proc.stdout.close()                         # no file handle is left open

    # ----- JSON-RPC plumbing ---------------------------------------------------
    def _send(self, message: dict) -> None:
        if self._proc is None or self._proc.stdin is None:
            raise MCPError(f"MCP server '{self.name}' is not running")
        data = (json.dumps(message) + "\n").encode("utf-8")
        with self._write_lock:
            try:
                self._proc.stdin.write(data)
                self._proc.stdin.flush()
            except (OSError, ValueError) as exc:            # ValueError: we closed the pipe
                raise MCPError(f"MCP server '{self.name}' stopped: {exc}") from None

    def _read_loop(self) -> None:
        """Background thread: route each answer to the request waiting for it."""
        assert self._proc is not None and self._proc.stdout is not None
        for raw in self._proc.stdout:
            try:
                message = json.loads(raw.decode("utf-8", errors="replace"))
            except ValueError:
                continue                                    # not JSON (e.g. a stray log line)
            if not isinstance(message, dict):
                continue
            if "id" in message and ("result" in message or "error" in message):
                with self._ready:
                    self._answers[message["id"]] = message
                    self._ready.notify_all()
            elif "id" in message and "method" in message:   # the server asks US something
                try:
                    self._send({"jsonrpc": "2.0", "id": message["id"],
                                "error": {"code": -32601, "message": "not supported by localagent"}})
                except MCPError:
                    pass                                    # we are shutting it down anyway
        with self._ready:                                   # program ended
            self._closed = True
            self._ready.notify_all()

    def request(self, method: str, params: dict | None = None,
                timeout: float | None = None) -> dict:
        """Send one request and wait for its answer's ``result``."""
        with self._ready:
            self._next_id += 1
            request_id = self._next_id
        self._send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}})
        with self._ready:
            done = self._ready.wait_for(lambda: request_id in self._answers or self._closed,
                                        timeout=timeout or self._timeout)
            answer = self._answers.pop(request_id, None)
        if answer is None:
            reason = "stopped" if done else f"did not answer within {timeout or self._timeout:.0f}s"
            raise MCPError(f"MCP server '{self.name}' {reason} ({method})")
        if "error" in answer:
            error = answer["error"]
            raise MCPError(f"MCP server '{self.name}': {error.get('message', error)}")
        return answer.get("result") or {}

    # ----- tools ---------------------------------------------------------------
    def _list_tools(self) -> list[dict]:
        tools: list[dict] = []
        cursor = None
        while True:                                         # the list may come in pages
            result = self.request("tools/list", {"cursor": cursor} if cursor else {})
            tools += [t for t in result.get("tools", []) if isinstance(t, dict) and t.get("name")]
            cursor = result.get("nextCursor")
            if not cursor:
                return tools

    def call_tool(self, name: str, arguments: dict, timeout: float | None = None) -> str:
        """Run one tool on the server and return its result as text."""
        result = self.request("tools/call", {"name": name, "arguments": arguments}, timeout)
        parts = []
        for item in result.get("content", []):
            if item.get("type") == "text":
                parts.append(str(item.get("text", "")))
            else:
                parts.append(f"[{item.get('type', 'data')} content not shown]")
        text = "\n".join(parts).strip() or "(no output)"
        return f"ERROR: {text}" if result.get("isError") else text
