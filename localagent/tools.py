"""The tools the model may call, plus the verify-before-write/run gates.

Core rule: broken code never stays on disk and never gets executed (Python,
JSON, TOML, JavaScript and many more are checked; see verify.py).
  * write_file / edit_file write, verify, and ROLL BACK if verification fails.
  * run_python / run_tests re-verify first (a second, independent check).
Every tool returns a plain string; failures are returned, never raised, so the
model can read the error and try again.
"""
from __future__ import annotations

import ast
import difflib
import fnmatch
import json
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .config import Config
from .mcp import MCPError, MCPServer, clean_schema
from .runners import make_runner
from .sandbox import (SKIP_DIRS, SandboxError, looks_binary, project_files, quote_for_shell,
                      resolve_inside, run_command, shell_argv, skip_dir, truncate, walk_files)
from .settings import PROJECT_SETTINGS, Permissions
from .verify import CHECKED_SUFFIXES, _installed, verify_file, verify_python
from .web import fetch_text

__all__ = ["SKIP_DIRS", "READ_ONLY", "Approval", "Tool", "ToolBox"]   # SKIP_DIRS re-exported
MAX_SEARCH_HITS = 40
MAX_SEARCH_FILES = 20_000
MAX_SEARCH_BYTES = 2_000_000          # bigger files (logs, data, bundles) are not searched
MAX_READ_BYTES = 8_000_000            # bigger files are only read in parts (start/end)
TOGETHER_HINT = ("Hint: if this name is defined by another edit you have not made yet (for example "
                 "a rename), send ALL related edits together in ONE multi_edit call, or use "
                 "edit_file with all=true.")
EMPTY_OLD = ("ERROR: 'old' must not be empty: copy the exact text to replace from the file "
             "(to write a whole file, use write_file)")
READ_ONLY = frozenset({"list_files", "read_file", "search", "outline"})
TextStyle = tuple[str, bool]          # (line break "\n" or "\r\n", starts with a byte-order mark)
CRLF_SUFFIXES = frozenset({".bat", ".cmd"})   # Windows batch files need CRLF line breaks


DONE_MARKS = ("(done)", "[done]", "(completed)", "- done", "✓", "✔")


def _todo_item(item: object) -> dict:
    """One checklist entry from ``{"text", "done"}`` or plain text; "... (done)" also counts as done."""
    text = str(item.get("text", "")) if isinstance(item, dict) else str(item)
    done = bool(item.get("done", False)) if isinstance(item, dict) else False
    for mark in DONE_MARKS:                                # models often write the state into the text
        if text.lower().rstrip().endswith(mark):
            text, done = text.rstrip()[: -len(mark)].rstrip(), True
    if text.lstrip().lower().startswith("[x]"):
        text, done = text.lstrip()[3:].lstrip(), True
    return {"text": text, "done": done}


def text_style(raw: str) -> TextStyle:
    """How a text file is written: its usual line break and whether it has a byte-order mark."""
    crlf = raw.count("\r\n")
    return ("\r\n" if crlf > raw.count("\n") - crlf else "\n"), raw.startswith("﻿")


def read_text(path: Path) -> tuple[str, TextStyle]:
    """A UTF-8 file's text with plain "\\n" line breaks (no mark), plus its style to write back."""
    with path.open(encoding="utf-8", newline="") as handle:     # newline="": see "\r\n" as is
        raw = handle.read()
    style = text_style(raw)
    text = raw.replace("\r\n", "\n")
    return (text[1:] if style[1] else text), style


def write_text(path: Path, text: str, style: TextStyle) -> None:
    """Write ``text`` in ``style``, so an edit never changes a file's line breaks or mark."""
    newline, mark = style
    path.write_text(("﻿" if mark else "") + text.replace("\r\n", "\n"),
                    encoding="utf-8", newline=newline)


def _globs(pattern: str) -> list[str]:
    """``*.{js,ts}`` -> ``["*.js", "*.ts"]`` (fnmatch itself knows no braces)."""
    found = re.search(r"\{([^{}]*)\}", pattern)
    if not found:
        return [pattern]
    return [expanded for option in found.group(1).split(",")
            for expanded in _globs(pattern[:found.start()] + option + pattern[found.end():])]


class Approval(str):
    """The question shown to the user ("run python a.py") that also carries, for permission
    rules and "always allow" answers, which tool asks and about what (command, file, URL)."""

    tool: str
    subject: str

    def __new__(cls, text: str, tool: str, subject: str) -> "Approval":
        question = super().__new__(cls, text)
        question.tool = tool
        question.subject = subject
        return question


@dataclass(frozen=True)
class Tool:
    """A callable plus the JSON schema the model sees."""

    name: str
    description: str
    parameters: dict
    func: Callable[[dict], str]

    def spec(self) -> dict:
        """Ollama/OpenAI-style function definition."""
        return {"type": "function", "function": {
            "name": self.name, "description": self.description,
            "parameters": self.parameters}}


def _schema(properties: dict, required: list[str]) -> dict:
    """Build a JSON-schema object for tool parameters."""
    return {"type": "object", "properties": properties, "required": required}


class ToolBox:
    """Owns the workspace, the tool table and the per-file failure counters."""

    def __init__(self, config: Config, approve: Callable[[str], bool] | None = None,
                 settings: dict | None = None) -> None:
        self._cfg = config
        config.workspace.mkdir(parents=True, exist_ok=True)
        self._root = config.workspace.resolve()
        self._approve = approve or (lambda _action: False)
        self._failures: dict[str, int] = {}
        self.writes = 0                           # successful file writes so far
        self.started = time.time()                # files changed after this are "this session's"
        self.runner = make_runner(config, self._root)   # where code runs: host or container
        self.todo: list[dict] = []                # the model's visible task checklist
        self.permissions = Permissions.from_settings(
            settings or {}, self._root / PROJECT_SETTINGS if settings is not None else None)
        self.notes: list[str] = []                # startup messages for the user (MCP servers)
        self._diff: list[str] = []                # diff of the latest successful write
        self._mcp: list[MCPServer] = []
        self._tools = {t.name: t for t in self._build()}
        self._start_mcp((settings or {}).get("mcpServers", {}))
        self.hooks: dict = dict((settings or {}).get("hooks", {}))   # your own commands (see run_hook)
        self.test_command = str((settings or {}).get("test_command") or "")   # "" = detect

    # ----- public API used by the agent -------------------------------------
    def specs(self) -> list[dict]:
        """Tool definitions to send to the model."""
        return [tool.spec() for tool in self._tools.values()]

    def close(self) -> None:
        """Release resources: the Docker sandbox container and any MCP server programs."""
        self.runner.stop()
        for server in self._mcp:
            server.close()

    def pop_diff(self) -> list[str]:
        """The diff of the latest write (for the user's screen only), then forget it."""
        diff, self._diff = self._diff, []
        return diff

    @staticmethod
    def is_read_only(name: str) -> bool:
        """True for tools that never change anything, so they may run in parallel."""
        return name in READ_ONLY

    def check_workspace(self, since: float | None = None) -> list[str]:
        """Verify checkable files (only those changed after ``since`` if given); list failures.

        Checking only changed files keeps this fast in big projects and never blames the
        agent for files that were already broken before it started.
        """
        problems: list[str] = []
        for path in walk_files(self._root):
            if since is not None and path.stat().st_mtime < since - 1.0:   # 1 s clock slack
                continue
            if path.suffix.lower() in CHECKED_SUFFIXES:
                report = verify_file(path, self._root, self._cfg.type_check)
                if not report.ok:
                    problems.append(f"{path.relative_to(self._root).as_posix()}:\n{report.render()}")
        return problems

    def call(self, name: str, args: dict) -> str:
        """Run one tool by name; always returns a string."""
        tool = self._tools.get(name)
        if tool is None:
            return f"ERROR: unknown tool '{name}'. Available: {', '.join(self._tools)}"
        try:
            return tool.func(args)
        except SandboxError as exc:
            return f"ERROR: {exc}"
        except UnicodeDecodeError:
            return ("ERROR: that file is not UTF-8 text (binary, or another encoding), so it "
                    "cannot be edited safely")
        except (KeyError, TypeError, ValueError) as exc:
            return f"ERROR: bad arguments for {name}: {exc!r}"
        except OSError as exc:
            return f"ERROR: {exc}"

    # ----- tool table --------------------------------------------------------
    def _build(self) -> list[Tool]:
        return [
            Tool("list_files", "List files and folders in a workspace folder.",
                 _schema({"path": {"type": "string", "description": "folder, default '.'"}}, []),
                 self._list_files),
            Tool("read_file", "Read a text file with line numbers. Use start/end to read part of it.",
                 _schema({"path": {"type": "string"}, "start": {"type": "integer"},
                          "end": {"type": "integer"}}, ["path"]),
                 self._read_file),
            Tool("search", "Find lines matching a regular expression in workspace files (like "
                 "grep; files that .gitignore excludes are skipped). Returns file:line: text. "
                 "Much cheaper than reading files.",
                 _schema({"pattern": {"type": "string"},
                          "path": {"type": "string", "description": "folder or file, default '.'"},
                          "glob": {"type": "string", "description": "file pattern such as '*.py', "
                                   "'src/*.ts' or '*.{js,ts}'; default: all text files"}},
                         ["pattern"]),
                 self._search),
            Tool("outline", "List the classes and functions (with line numbers) of one Python "
                 "file, or of every Python file in a folder. Cheaper than reading whole files.",
                 _schema({"path": {"type": "string", "description": "file or folder, default '.'"}},
                         []),
                 self._outline),
            Tool("write_file", "Create or overwrite a file. Code and config files are verified "
                 "first (syntax, undefined names) and rejected with the errors if they do not pass.",
                 _schema({"path": {"type": "string"}, "content": {"type": "string"}},
                         ["path", "content"]),
                 self._write_file),
            Tool("edit_file", "Replace one exact, unique piece of text in a file. To rename "
                 "something that appears several times in the file, set all=true.",
                 _schema({"path": {"type": "string"}, "old": {"type": "string"},
                          "new": {"type": "string"},
                          "all": {"type": "boolean", "description": "replace every occurrence"}},
                         ["path", "old", "new"]),
                 self._edit_file),
            Tool("multi_edit", "Apply several exact, unique text replacements to ONE file in a "
                 "single call (one verification). Each edit is {old, new}; they apply in order.",
                 _schema({"path": {"type": "string"},
                          "edits": {"type": "array", "items": {
                              "type": "object",
                              "properties": {"old": {"type": "string"}, "new": {"type": "string"}},
                              "required": ["old", "new"]}}}, ["path", "edits"]),
                 self._multi_edit),
            Tool("run_python", "Verify then run a Python file. Refuses if verification fails.",
                 _schema({"path": {"type": "string"},
                          "args": {"type": "array", "items": {"type": "string"}}}, ["path"]),
                 self._run_python),
            Tool("run_tests", "Verify the changed files, then run the project's tests (pytest, "
                 "unittest, npm test, go test or cargo test: whichever the project uses).",
                 _schema({}, []), self._run_tests),
            Tool("todo", "Show the user your plan as a checklist. Send the whole list each "
                 "time and set done=true on finished items. Use it for tasks with 3 or more steps.",
                 _schema({"items": {"type": "array", "items": {
                     "type": "object",
                     "properties": {"text": {"type": "string"}, "done": {"type": "boolean"}},
                     "required": ["text"]}}}, ["items"]),
                 self._todo),
            Tool("fetch_url", "Read a web page (for example library documentation) as plain "
                 "text. The user is asked first.",
                 _schema({"url": {"type": "string"}}, ["url"]), self._fetch_url),
        ] + ([Tool("run_shell", f"Run a shell command ({self.runner.shell_label}), e.g. pip "
                   "install, pytest, git, node. The workspace is the current folder.",
                   _schema({"command": {"type": "string"}}, ["command"]), self._run_shell)]
             if self.runner.supports_shell else [])

    # ----- read-only tools ---------------------------------------------------
    def _list_files(self, args: dict) -> str:
        folder = resolve_inside(self._root, args.get("path") or ".")
        if not folder.is_dir():
            return f"ERROR: '{args.get('path', '.')}' is not a folder"
        rows = [p.name + ("/" if p.is_dir() else "")
                for p in sorted(folder.iterdir()) if not (p.is_dir() and skip_dir(p.name))]
        return "\n".join(rows) or "(empty folder)"

    def _read_file(self, args: dict) -> str:
        path = resolve_inside(self._root, args["path"])
        if not path.is_file():
            return f"ERROR: file '{args['path']}' does not exist"
        size = path.stat().st_size
        if looks_binary(path):
            return f"ERROR: '{args['path']}' is a binary file ({size} bytes), not text"
        start = max(int(args.get("start") or 1), 1)
        if size > MAX_READ_BYTES:
            return self._read_part(path, args, start)
        lines = path.read_text(encoding="utf-8-sig", errors="replace").splitlines()
        if lines and start > len(lines):
            return f"(the file has only {len(lines)} lines)"
        end = min(int(args.get("end") or len(lines)), len(lines))
        body = "\n".join(f"{n}: {lines[n - 1]}" for n in range(start, end + 1))
        if body and (start > 1 or end < len(lines)):             # tell the model there is more
            body += f"\n(lines {start}-{end} of {len(lines)})"
        return truncate(body, self._cfg.max_output_chars) or "(empty file)"

    def _read_part(self, path: Path, args: dict, start: int) -> str:
        """Lines ``start`` to ``end`` of a file too big to load whole (read line by line)."""
        if not args.get("end"):
            return (f"ERROR: '{args['path']}' is {path.stat().st_size // 1_000_000} MB, too big to "
                    "read whole; use search to find the lines you need, then read_file with start "
                    "and end")
        end, rows = int(args["end"]), []
        with path.open(encoding="utf-8-sig", errors="replace") as handle:
            for number, line in enumerate(handle, start=1):
                if number > end:
                    break
                if number >= start:
                    rows.append(f"{number}: {line.rstrip(chr(13) + chr(10))}")
        return truncate("\n".join(rows), self._cfg.max_output_chars) or "(no such lines)"

    def _search(self, args: dict) -> str:
        try:
            pattern = re.compile(args["pattern"])
        except re.error as exc:
            return f"ERROR: invalid regular expression: {exc}"
        target = resolve_inside(self._root, args.get("path") or ".")
        files = [target] if target.is_file() else project_files(target)
        globs = _globs(args.get("glob") or "*")
        hits: list[str] = []
        for scanned, path in enumerate(files, start=1):
            if scanned > MAX_SEARCH_FILES:
                hits.append(f"... stopped after looking at {MAX_SEARCH_FILES} files; "
                            "narrow 'path' or 'glob'")
                break
            relative = path.relative_to(self._root).as_posix()
            if not any(fnmatch.fnmatch(path.name, g) or fnmatch.fnmatch(relative, g) for g in globs):
                continue
            try:
                if path.stat().st_size > MAX_SEARCH_BYTES or looks_binary(path):
                    continue                          # data, bundles, models: not code to grep
                lines = path.read_text(encoding="utf-8").splitlines()
            except (UnicodeDecodeError, OSError):
                continue                              # another encoding, or gone: skip
            for number, line in enumerate(lines, start=1):
                if pattern.search(line):
                    hits.append(f"{relative}:{number}: {line.strip()[:120]}")
                    if len(hits) >= MAX_SEARCH_HITS:
                        return "\n".join(hits) + f"\n... stopped at {MAX_SEARCH_HITS} matches"
        return "\n".join(hits) or "no matches"

    def _outline(self, args: dict) -> str:
        target = resolve_inside(self._root, args.get("path") or ".")
        if target.is_file():
            files = [target]
        elif target.is_dir():
            files = [p for p in project_files(target) if p.suffix == ".py"]
        else:
            return f"ERROR: '{args.get('path', '.')}' does not exist"
        rows: list[str] = []
        for path in files:
            rows.append(path.relative_to(self._root).as_posix() + ":")
            try:
                tree = ast.parse(path.read_text(encoding="utf-8-sig"))
            except (SyntaxError, UnicodeDecodeError, ValueError):
                rows.append("  (cannot parse)")
                continue
            for node in tree.body:
                if isinstance(node, ast.ClassDef):
                    rows.append(f"  class {node.name}  line {node.lineno}")
                    rows += [f"    def {m.name}  line {m.lineno}" for m in node.body
                             if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))]
                elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    rows.append(f"  def {node.name}  line {node.lineno}")
        return truncate("\n".join(rows), self._cfg.max_output_chars) or "(no Python files)"

    def _todo(self, args: dict) -> str:
        items = args["items"]
        if not isinstance(items, list) or not items:
            return "ERROR: 'items' must be a non-empty list of {text, done}"
        self.todo = [_todo_item(i) for i in items]
        finished = sum(item["done"] for item in self.todo)
        lines = [f"TODO {finished}/{len(self.todo)} done"]
        lines += [f"  [{'x' if item['done'] else ' '}] {item['text']}" for item in self.todo]
        return "\n".join(lines)

    # ----- write tools (verified, rolled back on failure) -------------------
    def _write_file(self, args: dict) -> str:
        return self._commit(args["path"], str(args["content"]))

    def _edit_file(self, args: dict) -> str:
        path = resolve_inside(self._root, args["path"])
        if not path.is_file():
            return f"ERROR: file '{args['path']}' does not exist"
        old, new = str(args["old"]).replace("\r\n", "\n"), str(args["new"]).replace("\r\n", "\n")
        if not old:
            return EMPTY_OLD
        text, style = read_text(path)
        if old.startswith("﻿") and style[1]:      # the edit names the byte-order mark itself:
            text, style = "﻿" + text, (style[0], False)   # it is text now, and may go
        count = text.count(old)
        if args.get("all") is True and count >= 1:
            return self._commit(args["path"], text.replace(old, new), style)
        if count != 1:
            return (f"ERROR: 'old' text must match exactly once, but matched {count} times"
                    + (" (use all=true to replace every occurrence)" if count > 1 else ""))
        return self._commit(args["path"], text.replace(old, new), style)

    def _multi_edit(self, args: dict) -> str:
        path = resolve_inside(self._root, args["path"])
        if not path.is_file():
            return f"ERROR: file '{args['path']}' does not exist"
        edits = args["edits"]
        if not isinstance(edits, list) or not edits:
            return "ERROR: 'edits' must be a non-empty list of {old, new}"
        text, style = read_text(path)
        for number, edit in enumerate(edits, start=1):
            old = str(edit["old"]).replace("\r\n", "\n")
            if not old:
                return f"{EMPTY_OLD}. Edit {number} is empty; nothing was changed."
            count = text.count(old)
            if count != 1:
                return (f"ERROR: edit {number}: 'old' text must match exactly once, "
                        f"but matched {count} times. Nothing was changed.")
            text = text.replace(old, str(edit["new"]).replace("\r\n", "\n"))
        return self._commit(args["path"], text, style)

    def _format(self, path: Path) -> None:
        """Best-effort ``ruff format`` of a file that already passed verification."""
        if self._cfg.auto_format and _installed("ruff"):
            run_command([sys.executable, "-m", "ruff", "format", str(path)], self._root, 30, 500)

    @staticmethod
    def _style_of(path: Path, previous: bytes | None) -> TextStyle:
        """The style to write in: the file's own, else LF (CRLF for batch files)."""
        if previous is not None:
            try:
                return text_style(previous.decode("utf-8"))
            except UnicodeDecodeError:
                pass
        return ("\r\n" if path.suffix.lower() in CRLF_SUFFIXES else "\n"), False

    def _commit(self, relative: str, text: str, style: TextStyle | None = None) -> str:
        """Write ``text``; verify checkable files and roll back on failure."""
        path = resolve_inside(self._root, relative)
        key = path.relative_to(self._root).as_posix()
        previous = path.read_bytes() if path.is_file() else None   # exact bytes, for rollback
        path.parent.mkdir(parents=True, exist_ok=True)
        write_text(path, text, style or self._style_of(path, previous))
        if path.suffix.lower() not in CHECKED_SUFFIXES:
            self.writes += 1
            self._remember_diff(key, previous, path)
            return f"OK: wrote {key} ({len(text.splitlines())} lines)" + self._after_edit(key)
        report = verify_file(path, self._root, self._cfg.type_check)
        if report.ok:
            self._failures.pop(key, None)
            self.writes += 1
            if path.suffix == ".py":
                self._format(path)
            self._remember_diff(key, previous, path)
            return (f"OK: wrote {key} ({len(text.splitlines())} lines); verification passed"
                    + self._after_edit(key))
        if previous is None:                      # roll back: undo the bad write
            path.unlink()
        else:
            path.write_bytes(previous)            # byte for byte, as it was
        count = self._failures.get(key, 0) + 1
        self._failures[key] = count
        if count >= self._cfg.max_fix_attempts:
            return (f"REJECTED ({count}/{self._cfg.max_fix_attempts}): {key} NOT written.\n"
                    f"{report.render()}\nSTOP: attempts exhausted. Explain the problem to "
                    "the user instead of retrying.")
        hint = f"\n{TOGETHER_HINT}" if "F821" in report.render() else ""   # F821 = undefined name
        return (f"REJECTED ({count}/{self._cfg.max_fix_attempts}): {key} NOT written. "
                f"Fix these errors and call write_file again:\n{report.render()}{hint}")

    def _remember_diff(self, key: str, previous: bytes | None, path: Path) -> None:
        """Keep a unified diff of the change so the user can see what was edited."""
        try:
            now = path.read_text(encoding="utf-8-sig").splitlines()
        except (OSError, UnicodeDecodeError):
            now = []
        before = previous.decode("utf-8-sig", "replace").splitlines() if previous is not None else []
        diff = list(difflib.unified_diff(before, now, f"a/{key}", f"b/{key}", n=1, lineterm=""))
        self._diff = diff[2:]                     # drop the ---/+++ header: the file is named already

    # ----- hooks: your own commands, run automatically ----------------------
    def run_hook(self, name: str, file: str = "") -> tuple[int, str] | None:
        """Run the hook ``name`` from the settings (``{file}`` = the changed file); None if unset.

        Hooks are commands YOU wrote in settings.json, so they run without a question, on this
        computer, in the project folder, with the normal time limit. The file name is chosen
        by the model, so it is quoted: a name can never smuggle in a command of its own.
        """
        command = self.hooks.get(name)
        if not command:
            return None
        argv = shell_argv(str(command).replace("{file}", quote_for_shell(file) if file else ""))
        return run_command(argv, self._root, self._cfg.command_timeout, self._cfg.max_output_chars)

    def _after_edit(self, key: str) -> str:
        """Run the after_edit hook; a failure is added to the result so the model fixes it."""
        outcome = self.run_hook("after_edit", key)
        if outcome is None or outcome[0] == 0:
            return ""
        return f"\nHOOK after_edit failed (exit code {outcome[0]}):\n{outcome[1].strip()}"

    # ----- permission gate for everything that runs or reaches out ---------
    def _permitted(self, tool: str, subject: str, question: str) -> str | None:
        """None when the action may go ahead; otherwise the refusal text for the model."""
        decision = self.permissions.decide(tool, subject)
        if decision == "deny":
            return f"DENIED by a deny rule in the settings: {tool}({subject})"
        if decision == "allow" or self._approve(Approval(question, tool, subject)):
            return None
        return "DENIED: the user did not approve this."

    # ----- execution tools (verified again, then user-approved) -------------
    def _run_python(self, args: dict) -> str:
        path = resolve_inside(self._root, args["path"])
        if not path.is_file():
            return f"ERROR: file '{args['path']}' does not exist"
        report = verify_python(path, self._root, self._cfg.type_check)
        if not report.ok:
            return f"NOT RUN: verification failed:\n{report.render()}"
        extra = [str(a) for a in args.get("args") or []]
        relative = path.relative_to(self._root).as_posix()
        denied = self._permitted("run_python", relative, f"run python {relative} {' '.join(extra)}")
        if denied:
            return denied
        code, output = self.runner.run("python", [relative, *extra])
        return f"exit code {code}\n{output}"

    def _run_tests(self, args: dict) -> str:
        problems = self.check_workspace(since=self.started)   # files changed this session
        if problems:
            return "NOT RUN: verification failed:\n" + "\n".join(problems)
        shown = f" ({self.test_command})" if self.test_command else ""
        denied = self._permitted("run_tests", "", f"run the tests{shown}")
        if denied:
            return denied
        code, output = self.runner.run("tests", [self.test_command] if self.test_command else [])
        return f"exit code {code}\n{output}"

    def _run_shell(self, args: dict) -> str:
        command = str(args["command"]).strip()
        if not command:
            return "ERROR: empty command"
        denied = self._permitted("run_shell", command, f"run ({self.runner.shell_label}): {command}")
        if denied:
            return denied
        code, output = self.runner.run("shell", [command])
        return f"exit code {code}\n{output}"

    # ----- reaching outside: web pages and MCP servers ----------------------
    def _fetch_url(self, args: dict) -> str:
        url = str(args["url"]).strip()
        denied = self._permitted("fetch_url", url, f"fetch the web page {url}")
        if denied:
            return denied
        try:
            text = fetch_text(url)
        except ValueError as exc:
            return f"ERROR: could not fetch {url}: {exc}"
        return truncate(text, self._cfg.max_output_chars) or "(the page has no text)"

    def _start_mcp(self, servers: dict) -> None:
        """Start each configured MCP server and add its tools as ``mcp__<server>__<tool>``."""
        for name, spec in servers.items():
            if not isinstance(spec, dict) or not spec.get("command"):
                self.notes.append(f"MCP server '{name}': needs a \"command\"; skipped")
                continue
            server = MCPServer(name, str(spec["command"]), [str(a) for a in spec.get("args", [])],
                               {str(k): str(v) for k, v in spec.get("env", {}).items()}, self._root)
            try:
                server.start()
            except MCPError as exc:
                server.close()
                self.notes.append(f"{exc}; skipped")
                continue
            self._mcp.append(server)
            for tool in server.tools:
                full = re.sub(r"[^A-Za-z0-9_-]", "_", f"mcp__{name}__{tool['name']}")[:64]
                description = str(tool.get("description", ""))[:500] or f"MCP tool from {name}"
                self._tools[full] = Tool(full, description, clean_schema(tool.get("inputSchema")),
                                         self._mcp_caller(server, str(tool["name"]), full))
            self.notes.append(f"MCP server '{name}': {len(server.tools)} tool(s)")

    def _mcp_caller(self, server: MCPServer, tool: str, full: str) -> Callable[[dict], str]:
        """The function behind one MCP tool: ask permission, then forward the call."""
        def call(args: dict) -> str:
            shown = json.dumps(args, sort_keys=True)
            denied = self._permitted(full, shown, f"use MCP tool {server.name}/{tool} {shown[:200]}")
            if denied:
                return denied
            try:
                return server.call_tool(tool, args, timeout=self._cfg.command_timeout * 4)
            except MCPError as exc:
                return f"ERROR: {exc}"
        return call
