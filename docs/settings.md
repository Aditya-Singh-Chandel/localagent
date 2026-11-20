# `localagent/settings.py` — settings files and permission rules

**Role in the flow:** decides, *before* anyone is asked, whether an action may happen. Claude Code has the same idea
(`settings.json` with `allow`/`deny` lists). Without rules you would be asked about every run, test and command; with
them, safe routine actions just happen, dangerous ones never happen, and only the rest is asked.

```
                 action (tool + subject)
                          │
          matches a DENY rule? ──yes──► refused, never asked
                          │no
          matches an ALLOW rule? ──yes──► runs, not asked
                          │no
                 ask you: [y]es / [n]o / [a]lways  ("a" saves an allow rule)
```

**Where rules live** (both optional, merged): `%USERPROFILE%\.localagent\settings.json` (all projects) and
`<project>\.localagent\settings.json` (one project; "always" answers are saved here). Example:

```json
{
  "permissions": {
    "allow": ["run_tests", "run_python(*)", "run_shell(git status*)", "run_shell(git diff*)",
              "fetch_url(https://docs.python.org/*)"],
    "deny":  ["run_shell(*rm *)", "run_shell(*del *)", "run_shell(*--force*)"]
  },
  "mcpServers": {
    "files": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "."]}
  },
  "hooks": {"after_edit": "ruff check {file}", "after_request": "python -m pytest -q"}
}
```
`hooks` are your own commands run automatically (see `tools.md`): `after_edit` after every file write (a failure is
shown to the model), `after_request` after every request (shown to you).

| Tool name in rules | Subject matched by the pattern in brackets |
|---|---|
| `run_python` | the file, e.g. `run_python(scripts/*)` |
| `run_tests` | nothing (use the bare name) |
| `run_shell` | the whole command line, e.g. `run_shell(npm test)` |
| `fetch_url` | the address, e.g. `fetch_url(https://docs.python.org/*)` |
| `mcp__<server>__<tool>` | the arguments as JSON; wildcards in the name work: `mcp__files__*` |

Patterns use shell-style wildcards: `*` any text, `?` one character. Matching is case-sensitive.

## Module header
```python
  1 | """Settings files and permission rules, in the style of Claude Code's settings.json.
  2 | 
  3 | Two optional JSON files are merged (user rules first, then project rules):
  4 |   ~/.localagent/settings.json             your rules for every project
  5 |   <project>/.localagent/settings.json     rules for one project ("always" answers are saved here)
  6 | 
  7 | Example:
  8 |   {"permissions": {"allow": ["run_tests", "run_shell(git status*)",
  9 |                              "fetch_url(https://docs.python.org/*)"],
 10 |                    "deny":  ["run_shell(*rm *)", "run_shell(*--force*)"]},
 11 |    "mcpServers": {"files": {"command": "npx",
 12 |                             "args": ["-y", "@modelcontextprotocol/server-filesystem", "."]}},
 13 |    "hooks": {"after_edit": "ruff check {file}", "after_request": "python -m pytest -q"}}
 14 | 
 15 | A rule is a tool name (wildcards allowed, e.g. ``mcp__files__*``), optionally followed by
 16 | a pattern in brackets that must match the action's subject: the command line for
 17 | run_shell, the file for run_python, the URL for fetch_url, the arguments for MCP tools.
 18 | Deny rules win over allow rules; an action that matches neither is asked.
 19 | """
 20 | from __future__ import annotations
 21 | 
 22 | import json
 23 | import re
 24 | from dataclasses import dataclass, field
 25 | from fnmatch import fnmatchcase
 26 | from pathlib import Path
 27 | from urllib.parse import urlsplit
 28 | 
 29 | USER_SETTINGS = Path.home() / ".localagent" / "settings.json"
 30 | PROJECT_SETTINGS = Path(".localagent") / "settings.json"     # relative to the workspace
 31 | USER_COMMANDS = Path.home() / ".localagent" / "commands"     # custom slash commands (*.md)
 32 | PROJECT_COMMANDS = Path(".localagent") / "commands"
 33 | 
 34 | 
```
- The docstring is the user guide in brief. `json` reads and writes the files; `dataclass`/`field` build
  `Permissions`; `fnmatchcase` matches wildcards (case-sensitive on every system, so a rule means the same on Windows
  and Linux); `urlsplit` takes a URL apart for website rules.
- `USER_SETTINGS` and `PROJECT_SETTINGS` are the two file locations (the project one is relative to the workspace);
  `USER_COMMANDS` and `PROJECT_COMMANDS` the folders for custom slash commands. `re` checks command names.

## Reading the files
```python
 35 | def read_json(path: Path) -> dict:
 36 |     """A settings file as a dict; a missing file is empty, a broken one is an error."""
 37 |     if not path.is_file():
 38 |         return {}
 39 |     try:
 40 |         data = json.loads(path.read_text(encoding="utf-8"))
 41 |     except (ValueError, OSError) as exc:
 42 |         raise ValueError(f"{path}: not valid JSON ({exc})") from None
 43 |     if not isinstance(data, dict):
 44 |         raise ValueError(f"{path}: must contain a JSON object {{...}}")
 45 |     return data
 46 | 
 47 | 
```
- A missing file is simply "no settings". A file that is not valid JSON, or not a `{...}` object, is an **error** with
  the file name — silently ignoring a broken file could silently drop your deny rules.

```python
 48 | def load_settings(workspace: Path, user_file: Path = USER_SETTINGS) -> dict:
 49 |     """Merge the user and project settings files into one dict."""
 50 |     merged: dict = {"permissions": {"allow": [], "deny": []}, "mcpServers": {}, "hooks": {}}
 51 |     for path in (user_file, workspace / PROJECT_SETTINGS):
 52 |         data = read_json(path)
 53 |         rules = data.get("permissions", {})
 54 |         for kind in ("allow", "deny"):
 55 |             merged["permissions"][kind] += [str(rule) for rule in rules.get(kind, [])]
 56 |         merged["mcpServers"].update(data.get("mcpServers", {}))
 57 |         merged["hooks"].update(data.get("hooks", {}))           # project hooks replace user ones
 58 |     return merged
 59 | 
 60 | 
```
- Starts from empty lists, then adds the user file's rules and the project file's rules (both kinds simply
  accumulate, so a project cannot remove a user's deny rule). MCP server definitions and hooks are merged by name (the
  project wins on a clash).

## Custom slash commands
Like Claude Code's custom commands: put a Markdown file in `<project>\.localagent\commands\` (or
`%USERPROFILE%\.localagent\commands\` for every project). `review.md` becomes `/review`; its text is the request,
and `$ARGUMENTS` is replaced by whatever you type after the command. Example `review.md`:
```
Review $ARGUMENTS for bugs, unclear names and missing error handling. Do not change anything;
list the problems with file:line and a suggested fix.
```
Then `/review src/app.py`. The first line of the file is shown in `/help`.

```python
 61 | def load_commands(workspace: Path, user_dir: Path = USER_COMMANDS) -> dict[str, str]:
 62 |     """Custom slash commands: each ``<name>.md`` file becomes ``/<name>`` (project files win)."""
 63 |     commands: dict[str, str] = {}
 64 |     for folder in (user_dir, workspace / PROJECT_COMMANDS):
 65 |         if folder.is_dir():
 66 |             for path in sorted(folder.glob("*.md")):
 67 |                 name = path.stem.lower()
 68 |                 if re.fullmatch(r"[a-z0-9_-]+", name):            # simple, typeable names only
 69 |                     commands[name] = path.read_text(encoding="utf-8", errors="replace")
 70 |     return commands
 71 | 
 72 | 
```
- Reads every `*.md` file from the user folder, then the project folder (so a project command replaces a user command
  of the same name). Only simple names (letters, digits, `-`, `_`) become commands.

```python
 73 | def expand_command(template: str, arguments: str) -> str:
 74 |     """Put the words typed after the command where the file says $ARGUMENTS (else at the end)."""
 75 |     if "$ARGUMENTS" in template:
 76 |         return template.replace("$ARGUMENTS", arguments).strip()
 77 |     return (template.rstrip() + ("\n\n" + arguments if arguments else "")).strip()
 78 | 
 79 | 
```
- Replaces `$ARGUMENTS` with the typed words; a file without `$ARGUMENTS` gets the words appended as a new paragraph.

## Rules
```python
 80 | def parse_rule(rule: str) -> tuple[str, str | None]:
 81 |     """``"run_shell(git *)"`` -> ``("run_shell", "git *")``; ``"run_tests"`` -> ``("run_tests", None)``."""
 82 |     rule = rule.strip()
 83 |     if rule.endswith(")") and "(" in rule:
 84 |         name, _, pattern = rule.partition("(")
 85 |         return name.strip(), pattern[:-1]
 86 |     return rule, None
 87 | 
 88 | 
```
- Splits `run_shell(git *)` into the tool name and the pattern; a bare name has no pattern.

```python
 89 | def matches(rule: str, tool: str, subject: str) -> bool:
 90 |     """True when ``rule`` covers this tool and subject (shell-style wildcards)."""
 91 |     name, pattern = parse_rule(rule)
 92 |     if not fnmatchcase(tool, name):
 93 |         return False
 94 |     return pattern is None or fnmatchcase(subject, pattern)
 95 | 
 96 | 
```
- The tool name is matched with wildcards (so `mcp__files__*` covers all of that server's tools); without a pattern
  the rule covers every subject.

```python
 97 | def suggest_rule(tool: str, subject: str) -> str:
 98 |     """The rule an "always allow" answer saves: narrow enough to stay safe."""
 99 |     if tool == "run_shell" or tool == "run_python":
100 |         return f"{tool}({subject})"                     # exactly this command / file
101 |     if tool == "fetch_url":
102 |         parts = urlsplit(subject)
103 |         return f"fetch_url({parts.scheme}://{parts.netloc}/*)"   # this website
104 |     return tool                                         # run_tests, one MCP tool
105 | 
106 | 
```
- What an "always" answer saves — deliberately narrow: exactly this command or this file for shell/python, the whole
  website for `fetch_url` (scheme + host + `/*`), and the bare name for `run_tests` and MCP tools.

## `Permissions`
```python
107 | @dataclass
108 | class Permissions:
109 |     """Allow/deny rules plus the project file where new "always" rules are saved."""
110 | 
111 |     allow: list[str] = field(default_factory=list)
112 |     deny: list[str] = field(default_factory=list)
113 |     project_file: Path | None = None
114 | 
```
```python
115 |     @classmethod
116 |     def from_settings(cls, settings: dict, project_file: Path | None = None) -> "Permissions":
117 |         rules = settings.get("permissions", {})
118 |         return cls(list(rules.get("allow", [])), list(rules.get("deny", [])), project_file)
119 | 
```
```python
120 |     def decide(self, tool: str, subject: str) -> str:
121 |         """``"deny"``, ``"allow"`` or ``"ask"`` for this action."""
122 |         if any(matches(rule, tool, subject) for rule in self.deny):
123 |             return "deny"
124 |         if any(matches(rule, tool, subject) for rule in self.allow):
125 |             return "allow"
126 |         return "ask"
127 | 
```
```python
128 |     def always_allow(self, rule: str) -> Path | None:
129 |         """Allow ``rule`` from now on and save it in the project settings (if any)."""
130 |         if rule not in self.allow:
131 |             self.allow.append(rule)
132 |         if self.project_file is None:
133 |             return None
134 |         data = read_json(self.project_file)
135 |         saved = data.setdefault("permissions", {}).setdefault("allow", [])
136 |         if rule not in saved:
137 |             saved.append(rule)
138 |         self.project_file.parent.mkdir(parents=True, exist_ok=True)
139 |         self.project_file.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
140 |         return self.project_file
```
- Holds the allow and deny lists and the project file for saving.
- `from_settings` builds it from merged settings (copies, so changes do not touch the input).
- `decide` checks deny first (deny always wins), then allow, else `"ask"`.
- `always_allow` adds the rule for this session and — if there is a project file — re-reads that file, adds the rule
  once, creates the folder if needed and writes it back nicely indented. It returns where it saved (for the message).
