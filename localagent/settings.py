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
   "hooks": {"after_edit": "ruff check {file}", "after_request": "python -m pytest -q"}}

A rule is a tool name (wildcards allowed, e.g. ``mcp__files__*``), optionally followed by
a pattern in brackets that must match the action's subject: the command line for
run_shell, the file for run_python, the URL for fetch_url, the arguments for MCP tools.
Deny rules win over allow rules; an action that matches neither is asked.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from pathlib import Path
from urllib.parse import urlsplit

USER_SETTINGS = Path.home() / ".localagent" / "settings.json"
PROJECT_SETTINGS = Path(".localagent") / "settings.json"     # relative to the workspace
USER_COMMANDS = Path.home() / ".localagent" / "commands"     # custom slash commands (*.md)
PROJECT_COMMANDS = Path(".localagent") / "commands"


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
    """Merge the user and project settings files into one dict."""
    merged: dict = {"permissions": {"allow": [], "deny": []}, "mcpServers": {}, "hooks": {}}
    for path in (user_file, workspace / PROJECT_SETTINGS):
        data = read_json(path)
        rules = data.get("permissions", {})
        for kind in ("allow", "deny"):
            merged["permissions"][kind] += [str(rule) for rule in rules.get(kind, [])]
        merged["mcpServers"].update(data.get("mcpServers", {}))
        merged["hooks"].update(data.get("hooks", {}))           # project hooks replace user ones
    return merged


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


def suggest_rule(tool: str, subject: str) -> str:
    """The rule an "always allow" answer saves: narrow enough to stay safe."""
    if tool == "run_shell" or tool == "run_python":
        return f"{tool}({subject})"                     # exactly this command / file
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
        if any(matches(rule, tool, subject) for rule in self.allow):
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
