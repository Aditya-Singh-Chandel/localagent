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
@@ localagent/settings.py header @@
- The docstring is the user guide in brief. `json` reads and writes the files; `dataclass`/`field` build
  `Permissions`; `fnmatchcase` matches wildcards (case-sensitive on every system, so a rule means the same on Windows
  and Linux); `urlsplit` takes a URL apart for website rules.
- `USER_SETTINGS` and `PROJECT_SETTINGS` are the two file locations (the project one is relative to the workspace);
  `USER_COMMANDS` and `PROJECT_COMMANDS` the folders for custom slash commands. `re` checks command names.

## Reading the files
@@ localagent/settings.py read_json @@
- A missing file is simply "no settings". A file that is not valid JSON, or not a `{...}` object, is an **error** with
  the file name — silently ignoring a broken file could silently drop your deny rules.

@@ localagent/settings.py load_settings @@
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

@@ localagent/settings.py load_commands @@
- Reads every `*.md` file from the user folder, then the project folder (so a project command replaces a user command
  of the same name). Only simple names (letters, digits, `-`, `_`) become commands.

@@ localagent/settings.py expand_command @@
- Replaces `$ARGUMENTS` with the typed words; a file without `$ARGUMENTS` gets the words appended as a new paragraph.

## Rules
@@ localagent/settings.py parse_rule @@
- Splits `run_shell(git *)` into the tool name and the pattern; a bare name has no pattern.

@@ localagent/settings.py matches @@
- The tool name is matched with wildcards (so `mcp__files__*` covers all of that server's tools); without a pattern
  the rule covers every subject.

@@ localagent/settings.py suggest_rule @@
- What an "always" answer saves — deliberately narrow: exactly this command or this file for shell/python, the whole
  website for `fetch_url` (scheme + host + `/*`), and the bare name for `run_tests` and MCP tools.

## `Permissions`
@@ localagent/settings.py Permissions @@
@@ localagent/settings.py Permissions.from_settings @@
@@ localagent/settings.py Permissions.decide @@
@@ localagent/settings.py Permissions.always_allow @@
- Holds the allow and deny lists and the project file for saving.
- `from_settings` builds it from merged settings (copies, so changes do not touch the input).
- `decide` checks deny first (deny always wins), then allow, else `"ask"`.
- `always_allow` adds the rule for this session and — if there is a project file — re-reads that file, adds the rule
  once, creates the folder if needed and writes it back nicely indented. It returns where it saved (for the message).
