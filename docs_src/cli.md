# `localagent/cli.py` — the terminal chat (what you type into)

**Role in the flow:** starts everything. It reads flags, fills in whatever you did not choose from what this computer
has (`auto_configure`), loads the settings files, builds `Config → backend → ToolBox → Meter → Agent`, then loops:
read a request or command, snapshot the folder, run the agent (showing every tool call, edit diff and checklist), verify
the whole folder, print usage. It is the counterpart of Claude Code's terminal interface.

```
localagent                       # in your project folder: automatic setup
localagent --doctor              # check the setup that would be used
localagent --backend openai --api-base http://127.0.0.1:8080/v1 --no-auto   # fully manual
```

## Module header
@@ localagent/cli.py header @@
- The docstring explains how to start it and what is chosen automatically.
- Imports: `argparse` (flags), `overlay` and `gemini_history` (shared rules in `config.py`), `os` (environment variables), `sys` (the output streams), `time` (when a request
  started), `urllib.request` (probe a local model server), `replace`,
  `Path`, `Callable`, and the modules it wires together — agent, backends, checkpoints, config, doctor, `LLMError`,
  meter, `sandbox_image_ready`, sessions, settings/permissions, and the toolbox with its `Approval` questions.
- `HELP` lists the commands (shown at start and by `/help`), how to type multi-line requests, and that Ctrl+C stops a
  task without leaving.
- `INIT_PROMPT` is what `/init` asks the model: look at the project and write `AGENT.md` (purpose, how to run, how to
  test, conventions, key files; at most 60 lines) — Claude Code's `/init` and `CLAUDE.md`.
- `INIT_PROMPT` also says to write only `AGENT.md` and to list noticed problems under "Known issues" instead of fixing
  them; `INIT_TOOLS` enforces it — during `/init` the model can read anything but its only writing tool is
  `write_file`.
- `BUILT_IN` lists the built-in commands (custom commands cannot replace them).
- `PLAN_FOLLOW_UP` is sent after you accept a plan from `/plan`; it starts with "Implement", so the agent's "you changed
  nothing" safeguards apply to it.

## `Approver` — asking before anything runs
@@ localagent/cli.py Approver @@
@@ localagent/cli.py Approver.__init__ @@
@@ localagent/cli.py Approver.__call__ @@
- Called by the toolbox whenever no permission rule decides (see `settings.md`). It prints the question, e.g.
  `allow: run python main.py? [y]es / [n]o / [a]lways:`.
- `y`/`yes` allows once. Enter or anything else refuses (the safe default).
- `a`/`always` allows **and** saves a narrow rule (`suggest_rule`: exactly this command, this file, this website or this
  tool) in the project's `.localagent/settings.json`, so you are not asked again — like Claude Code's "Yes, and don't
  ask again". The question is an `Approval` (a string that also knows the tool and subject), which is how the rule can
  be built. `read` is injectable so tests can answer.

## The flags shared by the chat, the server and the evals
@@ localagent/cli.py add_model_flags @@
- Adds the four flags all three programs need: `--backend`, `--model`, `--api-base` and `--sandbox`. Defined once
  here, so the three cannot drift apart (before, each program declared its own copy).

@@ localagent/cli.py model_flags @@
- Reads those four values back as `Config` field names (`api-base` becomes `api_base`), ready for `overlay`.

## `build_parser`
@@ localagent/cli.py build_parser @@
- Declares the flags: the shared ones (`add_model_flags`), then the chat's own. Value flags default to `None`, meaning
  "not given" (so automatic setup and environment variables can fill them): `--workspace` (default: the current
  folder), `--num-ctx`, `--fallback`, `--reasoning`.
- On/off flags: `--doctor`, `--resume`, `--type-check`, `--auto-approve`, `--network` (internet inside the sandbox),
  `--allow-shell` (shell on this computer, asks every time) and `--no-auto` (no automatic choices).

## `build_config`
@@ localagent/cli.py build_config @@
- Starts from `Config.from_env()` (defaults + environment variables), `overlay`s only the value flags actually given
  (the shared ones from `model_flags` plus the chat's own), then sets the on/off flags. Order of strength: flags > environment variables > defaults.

## Automatic setup
@@ localagent/cli.py server_up @@
- Asks an OpenAI-style server for its model list with a 1-second timeout; any normal answer means a local model server
  is running (e.g. `serve.ps1`).

@@ localagent/cli.py auto_configure @@
- Only fills choices you left open (no flag and no environment variable), and returns a note for each choice so the
  banner can explain it:
  - **backend:** `gemini` when `GEMINI_API_KEY` is set (fast, strong, free tier);
  - **sandbox:** `docker` when Docker runs and the sandbox image exists (one quick `docker image inspect`);
  - **fallback:** the local model server, when the backend is Gemini and a server answers at `api_base`;
  - **history budget:** 32 768 tokens with Gemini (unless you set `--num-ctx`). The 8 192 default fits the small local
    model; with it, Gemini sessions forgot old tool output and summarised far earlier than necessary. At 32K a call
    carries at most ~23K tokens of history — still well inside the free tier's 250 000 tokens per minute.
- With nothing available it changes nothing, so the program still starts (and `--doctor` says what is missing).

## `model_label`
@@ localagent/cli.py model_label @@
- A readable description of who answers, e.g.
  `gemini:gemini-3.5-flash-lite > gemini-3.1-flash-lite > gemini-3.5-flash > gemini-2.5-flash > openai:qwen3-8b`.
  Used by the banner and the eval scoreboard.

## `read_request` — typing a request
@@ localagent/cli.py read_request @@
- Reads one line after `you> ` and removes an invisible byte-order mark from its start: text piped into a program by
  PowerShell can begin with one, which made a piped `/init` arrive as `\ufeff/init` and be treated as an ordinary
  request (found in the end-to-end test).
- A line ending in `\` continues on the next line (`... ` prompt). A line that is only
  `"""` starts a block that ends at the next `"""` line — handy for pasting code or long instructions.
  (`:=` reads a line and tests it in one step.) `read` is injectable for tests.

## `handle_request` — one request
@@ localagent/cli.py handle_request @@
- `show` prints streamed text as it arrives.
- Runs the agent with `print` as the progress display (tool calls, results, diffs, checklists). With `plan_only` the
  agent only investigates and returns a plan.
- A model-service problem (`LLMError`) is printed and the chat continues. **Ctrl+C** (`KeyboardInterrupt`) stops the
  task: `agent.repair()` gives any half-finished tool calls an "interrupted" result so the conversation stays valid,
  and you can type the next request.
- After the run: if text was streamed, the line is ended; the answer is printed too unless it is exactly what was
  already streamed — so a `finish` summary always appears, even when the model streamed other text first.
- After a normal run (not a plan) the `after_request` hook runs if you set one (for example your test suite) and its
  exit code and output are shown.
- After a normal run (not a plan) the files **changed during this request** are verified once more (`since=started`):
  fast even in big projects, and never blaming you for files broken before; finally the usage line. Returns the answer
  (used by `/plan`).

## `snapshot`
@@ localagent/cli.py snapshot @@
- Takes a `/undo` snapshot before a request and prints the one-time "undo is off" message if the folder turned out to
  be too large.

## `chat` — the loop
@@ localagent/cli.py sessions_folder @@
- Where this project's conversation logs live: `<workspace>\.localagent\sessions`. Joining a path with an absolute
  `state_dir` gives that absolute path (that is how `pathlib` joins), so a custom absolute location still works.

@@ localagent/cli.py chat @@
- Builds the session log (in `sessions_folder`) and the agent; `--resume` reloads the newest session of **this
  project** (and repairs it if it ended mid-task).
- `Checkpoints` provide `/undo`. Custom commands are loaded from `.localagent/commands` (see `settings.md`) and listed
  under the help text with the first line of each file. The banner shows the model chain, the sandbox and the folder, then the automatic-setup
  notes and the toolbox's notes (for example which MCP servers started), then `HELP`.
- Each input is split into a command word and the rest. Ctrl+C or Ctrl+D at the prompt (`EOFError`) leaves the program.
  Commands: `/quit`, `/help`, `/reset`, `/undo`, `/stats` (also each Gemini model's daily use), `/compact`, `/init`
  (restricted to `INIT_TOOLS`; afterwards it reports whether `AGENT.md` now exists, then reloads the system prompt so
  the notes apply at once), and `/plan <request>`: a read-only
  investigation that prints a plan and asks `Carry out this plan? [y/N]`; on yes the folder is snapshotted and
  `PLAN_FOLLOW_UP` runs it. `/<name>` of a custom command expands its file (`expand_command`) and runs it as a request.
  Any other `/word` is reported as unknown **without contacting the model** (a typo should not cost an API call).
  Anything else is a normal request (snapshot first, so `/undo` can revert it).

## `main` — start-up
@@ localagent/cli.py main @@
- First makes the output robust: with output piped or redirected on Windows, Python writes in the old Windows code
  page, where one emoji or arrow in a model's answer or a diff would crash the program with `UnicodeEncodeError`;
  `errors="replace"` shows such a character as `?` instead (found in review).
- Parses flags, builds the config and (unless `--no-auto`) applies automatic setup.
- `--doctor` prints the automatic choices plus the health report and exits with 0 (healthy) or 1.
- Loads the settings files and builds the backend; a broken `settings.json` or a missing Gemini key is printed as a
  `setup problem` with exit code 2 instead of a crash.
- Builds the approver, meter and toolbox (with `--auto-approve` nothing is asked; deny rules still apply), connects the
  approver to the toolbox's permissions, and runs the chat. `finally` always closes the toolbox, which stops the Docker
  sandbox and any MCP server programs, even after an error.
