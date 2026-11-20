"""Settings files and permission rules, in the style of Claude Code's settings.json.

Two optional JSON files are merged (user rules first, then project rules):
  ~/.localagent/settings.json             your rules for every project
  <project>/.localagent/settings.json     rules for one project ("always" answers are saved here)

Example:
  {"permissions": {"allow": ["run_tests", "run_shell(git status*)",
                             "fetch_url(https://docs.python.org/*)"],
                   "deny":  ["run_shell(*rm *)", "run_shell(*--force*)"]},
   "mcpServers": {"files": {"command": "npx",
                            "args": ["-y", "@modelcontextprotocol/server-filesystem", "."]}},
   "hooks": {"after_edit": "ruff check {file}", "after_request": "python -m pytest -q"},
   "test_command": "npm test",
   "env": {"LOCALAGENT_MODE": "offline"}}

A rule is a tool name (wildcards allowed, e.g. ``mcp__files__*``), optionally followed by
a pattern in brackets that must match the action's subject: the command line for
run_shell, the file for run_python, the URL for fetch_url, the arguments for MCP tools.
Deny rules win over allow rules; an action that matches neither is asked. A shell command
line made of several commands (``a && b``, ``a; b``, ``a | b``) is allowed only when every
part is; one with command substitution, grouping, redirection or several lines is always
asked, unless an "always" answer approved exactly that line.
``env`` sets environment variables (any ``LOCALAGENT_*`` setting) unless they are already set.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from pathlib import Path
from urllib.parse import urlsplit

USER_SETTINGS = Path.home() / ".localagent" / "settings.json"
PROJECT_SETTINGS = Path(".localagent") / "settings.json"     # relative to the workspace
USER_COMMANDS = Path.home() / ".localagent" / "commands"     # custom slash commands (*.md)
PROJECT_COMMANDS = Path(".localagent") / "commands"
SHELL_SPLIT = re.compile(r"&&|\|\||[;&|]")                     # between two commands
SHELL_UNSAFE = ("`", "$(", "(", ")", "<", ">", "\n", "\r")    # hide or redirect commands
ESCAPED = re.compile(r"\[[*?\[]\]")                            # [*] [?] [[] = literal characters


def read_json(path: Path) -> dict:
    """A settings file as a dict; a missing file is empty, a broken one is an error."""
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        raise ValueError(f"{path}: not valid JSON ({exc})") from None
    if not isinstance(data, dict):
        raise ValueError(f"{path}: must contain a JSON object {{...}}")
    return data


def load_settings(workspace: Path, user_file: Path = USER_SETTINGS) -> dict:
    """Merge the user and project settings files into one dict (project values win)."""
    merged: dict = {"permissions": {"allow": [], "deny": []}, "mcpServers": {}, "hooks": {},
                    "env": {}, "test_command": ""}
    for path in (user_file, workspace / PROJECT_SETTINGS):
        data = read_json(path)
        rules = data.get("permissions", {})
        for kind in ("allow", "deny"):
            merged["permissions"][kind] += [str(rule) for rule in rules.get(kind, [])]
        merged["mcpServers"].update(data.get("mcpServers", {}))
        merged["hooks"].update(data.get("hooks", {}))           # project hooks replace user ones
        merged["env"].update({str(k): str(v) for k, v in data.get("env", {}).items()})
        merged["test_command"] = str(data.get("test_command") or merged["test_command"])
    return merged


def apply_env(settings: dict) -> list[str]:
    """Set the settings' ``env`` variables that are not set already; returns their names.

    A real environment variable wins, so ``setx`` (or a one-off ``$env:X=...``) still overrides
    a settings file.
    """
    applied = []
    for name, value in settings.get("env", {}).items():
        if name not in os.environ:
            os.environ[name] = value
            applied.append(name)
    return applied


def load_commands(workspace: Path, user_dir: Path = USER_COMMANDS) -> dict[str, str]:
    """Custom slash commands: each ``<name>.md`` file becomes ``/<name>`` (project files win)."""
    commands: dict[str, str] = {}
    for folder in (user_dir, workspace / PROJECT_COMMANDS):
        if folder.is_dir():
            for path in sorted(folder.glob("*.md")):
                name = path.stem.lower()
                if re.fullmatch(r"[a-z0-9_-]+", name):            # simple, typeable names only
                    commands[name] = path.read_text(encoding="utf-8", errors="replace")
    return commands


def expand_command(template: str, arguments: str) -> str:
    """Put the words typed after the command where the file says $ARGUMENTS (else at the end)."""
    if "$ARGUMENTS" in template:
        return template.replace("$ARGUMENTS", arguments).strip()
    return (template.rstrip() + ("\n\n" + arguments if arguments else "")).strip()


def parse_rule(rule: str) -> tuple[str, str | None]:
    """``"run_shell(git *)"`` -> ``("run_shell", "git *")``; ``"run_tests"`` -> ``("run_tests", None)``."""
    rule = rule.strip()
    if rule.endswith(")") and "(" in rule:
        name, _, pattern = rule.partition("(")
        return name.strip(), pattern[:-1]
    return rule, None


def matches(rule: str, tool: str, subject: str) -> bool:
    """True when ``rule`` covers this tool and subject (shell-style wildcards)."""
    name, pattern = parse_rule(rule)
    if not fnmatchcase(tool, name):
        return False
    return pattern is None or fnmatchcase(subject, pattern)


def escape_pattern(text: str) -> str:
    """``text`` as a rule pattern that matches only itself (``*``, ``?`` and ``[`` made literal)."""
    return "".join(f"[{char}]" if char in "*?[" else char for char in text)


def is_exact(pattern: str | None) -> bool:
    """True for a pattern without wildcards, such as the ones "always" answers save."""
    return pattern is not None and not any(char in ESCAPED.sub("", pattern) for char in "*?[")


def command_parts(command: str) -> list[str] | None:
    """The simple commands in a command line (``git add . && git commit`` gives two), or None
    when it holds something a rule cannot vouch for: command substitution (``$(...)``,
    backticks), grouping (PowerShell runs ``(...)`` as a command), redirection or several lines.

    Splitting is deliberately simple: an operator inside quotes also splits, which can only
    make the agent ask more often, never less.
    """
    if any(mark in command for mark in SHELL_UNSAFE):
        return None
    return [part.strip() for part in SHELL_SPLIT.split(command) if part.strip()]


def suggest_rule(tool: str, subject: str) -> str:
    """The rule an "always allow" answer saves: narrow enough to stay safe."""
    if tool == "run_shell" or tool == "run_python":
        return f"{tool}({escape_pattern(subject)})"     # exactly this command / file
    if tool == "fetch_url":
        parts = urlsplit(subject)
        return f"fetch_url({parts.scheme}://{parts.netloc}/*)"   # this website
    return tool                                         # run_tests, one MCP tool


@dataclass
class Permissions:
    """Allow/deny rules plus the project file where new "always" rules are saved."""

    allow: list[str] = field(default_factory=list)
    deny: list[str] = field(default_factory=list)
    project_file: Path | None = None

    @classmethod
    def from_settings(cls, settings: dict, project_file: Path | None = None) -> "Permissions":
        rules = settings.get("permissions", {})
        return cls(list(rules.get("allow", [])), list(rules.get("deny", [])), project_file)

    def decide(self, tool: str, subject: str) -> str:
        """``"deny"``, ``"allow"`` or ``"ask"`` for this action."""
        if any(matches(rule, tool, subject) for rule in self.deny):
            return "deny"
        if tool != "run_shell":
            return "allow" if any(matches(rule, tool, subject) for rule in self.allow) else "ask"
        for rule in self.allow:                 # every command allowed, or exactly this line
            name, pattern = parse_rule(rule)
            if fnmatchcase(tool, name) and (pattern in (None, "*") or (
                    is_exact(pattern) and fnmatchcase(subject, pattern or ""))):
                return "allow"
        parts = command_parts(subject)
        if not parts:
            return "ask"
        if any(matches(rule, tool, part) for part in parts for rule in self.deny):
            return "deny"
        if all(any(matches(rule, tool, part) for rule in self.allow) for part in parts):
            return "allow"
        return "ask"

    def always_allow(self, rule: str) -> Path | None:
        """Allow ``rule`` from now on and save it in the project settings (if any)."""
        if rule not in self.allow:
            self.allow.append(rule)
        if self.project_file is None:
            return None
        data = read_json(self.project_file)
        saved = data.setdefault("permissions", {}).setdefault("allow", [])
        if rule not in saved:
            saved.append(rule)
        self.project_file.parent.mkdir(parents=True, exist_ok=True)
        self.project_file.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        return self.project_file
