# `localagent/cli.py` — the terminal chat (what you type into)

**Role in the flow:** starts everything. It reads flags, applies the settings files' `env`, chooses the model setup for
the **mode** and fills in whatever else you did not choose from what this computer has (`auto_configure`), builds
`Config → backend → ToolBox → Meter → Agent`, then loops: read a request or command, note the folder for `/undo`, run the
agent (showing every tool call, edit diff and checklist), verify the changed files, print usage and who answered. It is
the counterpart of Claude Code's terminal interface.

```
localagent                       # in your project folder: automatic setup (mode auto)
localagent --offline             # only the local model (started for you when needed)
localagent --online              # only Gemini
localagent -p "what does this project do?"     # one answer, printed; for scripts
localagent -c                    # continue the latest conversation of this project
localagent --doctor              # check the setup that would be used
localagent --backend openai --api-base http://127.0.0.1:8080/v1 --no-auto   # fully manual
```

**The three modes.** `auto` (default): Gemini answers while a key is set and the internet works; when Gemini cannot be
reached (or its quota is used up) the local model answers instead, by itself and at once — and Gemini takes over again
when it is back. Without a key, `auto` uses the local model. `online`: only Gemini (nothing local is started or
used). `offline`: only the local model, started for you if needed. Switch at any time in the chat with `/model online`,
`/model offline`, `/model auto` (or `/online`, `/offline`, `/auto`); the conversation and your files stay.

## Module header
@@ localagent/cli.py header @@
- The docstring explains how to start it and what the modes do.
- Imports: `argparse` (flags), `os` (environment variables), `re` (`@file` mentions), `sys` (the output streams),
  `time` (when a request started), `replace`, `Path`, `Callable`, `__version__`, and the modules it wires together —
  agent, backends, checkpoints, config (with `MODES`, `overlay`, `gemini_history`), doctor, `LLMError`, the local-model
  functions, meter, `sandbox_image_ready`, the sandbox helpers it uses for `!` and `@`, sessions, settings/permissions,
  and the toolbox with its `Approval` questions and text helpers.
- `HELP` lists the commands (shown at start and by `/help`), the `!`, `#` and `@` shortcuts, how to type multi-line
  requests, and that Ctrl+C stops a task without leaving.
- `INIT_PROMPT` is what `/init` asks the model: look at the project and write `AGENT.md` (purpose, how to run, how to
  test, conventions, key files; at most 60 lines) — Claude Code's `/init` and `CLAUDE.md`. It also says to write only
  `AGENT.md` and to list noticed problems under "Known issues" instead of fixing them; `INIT_TOOLS` enforces it —
  during `/init` the model can read anything but its only writing tool is `write_file`.
- `PLAN_FOLLOW_UP` is sent after you accept a plan from `/plan`; it starts with "Implement", so the agent's "you changed
  nothing" safeguards apply to it.
- `BUILT_IN` lists the built-in commands (custom commands cannot replace them). `MODE_TEXT` describes each mode for
  `/model`. `NOTHING_HELP` is shown when no model at all is available: `localagent setup` first, then both ways to set
  one up by hand. `SUBCOMMANDS` are the product commands handled before the flags; `COMMANDS_EPILOG` lists them at the
  end of `--help`.
- `MENTION` finds `@path` words — only at the start or after a space, so `me@example.com` is not one. `COLORS` and
  `FAILURE_WORDS` decide which progress lines are coloured.

## Asking before anything runs
@@ localagent/cli.py Approver @@
@@ localagent/cli.py Approver.__init__ @@
@@ localagent/cli.py Approver.__call__ @@
- Called by the toolbox whenever no permission rule decides (see `settings.md`). It prints the question, e.g.
  `allow: run python main.py? [y]es / [n]o / [a]lways:`.
- `y`/`yes` allows once. Enter or anything else refuses (the safe default). When there is no input at all (input piped
  in and used up) that counts as "no" — before 0.6 it crashed the program.
- `a`/`always` allows **and** saves a narrow rule (`suggest_rule`: exactly this command, this file, this website or this
  tool) in the project's `.localagent/settings.json`, so you are not asked again — like Claude Code's "Yes, and don't
  ask again". The question is an `Approval` (a string that also knows the tool and subject), which is how the rule can
  be built. `read` is injectable so tests can answer.

@@ localagent/cli.py refuse @@
- The approver of `-p` runs: there is no chat to ask in, so an action that no rule allows is refused (and said so on the
  error stream). Allow it with a rule, or pass `--auto-approve`. Claude Code's `-p` behaves the same way.

@@ localagent/cli.py confirm @@
- A yes/no question (`/plan`'s "Carry out this plan?"); no input or Ctrl+C means no.

## The flags shared by the chat, the server and the evals
@@ localagent/cli.py add_model_flags @@
- Adds the flags all three programs need: `--mode` (with the shortcuts `--online` and `--offline`, which simply set it),
  `--backend`, `--model`, `--api-base` and `--sandbox`. Defined once here, so the three cannot drift apart.

@@ localagent/cli.py model_flags @@
- Reads those values back as `Config` field names (`api-base` becomes `api_base`), ready for `overlay`.

## `build_parser`
@@ localagent/cli.py build_parser @@
- `--version` prints `localagent 0.6.0` and exits. `-p`/`--print REQUEST` answers one request (`-` reads it from your
  input, e.g. `type question.txt | localagent -p -`).
- The shared flags (`add_model_flags`), then the chat's own. Value flags default to `None`, meaning "not given" (so
  automatic setup and environment variables can fill them): `--workspace` (default: the current folder), `--num-ctx`,
  `--fallback` (`local`, `ollama` or `openai`), `--reasoning`.
- On/off flags: `--doctor`, `--resume` (also `-c` and `--continue`, Claude Code's names), `--type-check`,
  `--auto-approve`, `--network` (internet inside the sandbox), `--allow-shell` (shell on this computer, asks every time)
  and `--no-auto` (no automatic choices).

## `build_config`
@@ localagent/cli.py build_config @@
- Starts from `Config.from_env()` (defaults + environment variables), `overlay`s only the value flags actually given
  (the shared ones from `model_flags` plus the chat's own), then sets the on/off flags. Order of strength: flags >
  environment variables > settings-file `env` > defaults.

## Choosing the model setup
@@ localagent/cli.py local_kind @@
- What the local model would be right now: `openai` when a server answers at `api_base` (llama.cpp, LM Studio),
  `ollama` when Ollama answers, `local` when llama.cpp can be started here, `""` when there is nothing. The quick checks
  take a second at most.

@@ localagent/cli.py choose_mode @@
- Turns a mode into a model setup and explains it:
  - `online` needs a key (else a `ValueError` saying how to get one) → backend `gemini`, no local backup;
  - `auto` with a key → backend `gemini` with the local model as its last link (`fallback_backend`) when there is one,
    and a note naming it — or "none set up";
  - `offline`, or `auto` without a key → backend `local`, history budget = the local context (8K) unless you chose a
    size; with nothing local a `ValueError` says what to install (`NO_LOCAL_MODEL`, or `NOTHING_HELP` in `auto`).
- Used at start-up (`auto_configure`), by `/model`, and by the HTTP server and the evals, so all four agree.

@@ localagent/cli.py auto_configure @@
- Only fills choices you left open (no flag and no environment variable), and returns a note for each choice so the
  banner can explain it:
  - **models:** `choose_mode` for the configured mode — unless you chose `--backend` (or `LOCALAGENT_BACKEND`) by hand;
  - **sandbox:** `docker` when Docker runs and the sandbox image exists (one quick `docker image inspect`);
  - **fallback:** with a hand-chosen `--backend gemini` (and not online mode), the local server when one answers;
  - **history budget:** 32 768 tokens with Gemini (unless you set `--num-ctx`). The 8 192 default fits the small local
    model; with it, Gemini sessions forgot old tool output and summarised far earlier than necessary. At 32K a call
    carries at most ~23K tokens of history — still well inside the free tier's 250 000 tokens per minute.
- When the mode cannot work (no key and nothing local), `choose_mode`'s `ValueError` reaches `main`, which prints it as
  a setup problem — or, with `--doctor`, runs the health check anyway.

## `model_label`
@@ localagent/cli.py model_label @@
- A readable description of who answers, e.g.
  `gemini:gemini-3.5-flash-lite > gemini-3.1-flash-lite > gemini-3.5-flash > gemini-2.5-flash > local:Qwen3-8B-Q4_K_M`
  (the local model named after its file), or `local:Qwen3-8B-Q4_K_M` offline. Used by the banner, `/model` and the eval
  scoreboard.

## `read_request` — typing a request
@@ localagent/cli.py read_request @@
- Reads one line after `you> ` and removes an invisible byte-order mark from its start: text piped into a program by
  PowerShell can begin with one, which made a piped `/init` arrive as `﻿/init` and be treated as an ordinary
  request (found in the end-to-end test).
- A line ending in `\` continues on the next line (`... ` prompt). A line that is only
  `"""` starts a block that ends at the next `"""` line — handy for pasting code or long instructions.
  (`:=` reads a line and tests it in one step.) `read` is injectable for tests.

## Colours
@@ localagent/cli.py use_color @@
- Colours only when the output is a terminal (not a file or a pipe) and `NO_COLOR` is not set (the common convention
  for "no colours, please"). The Windows console shows colour codes only after being told to: `SetConsoleMode` with
  `ENABLE_VIRTUAL_TERMINAL_PROCESSING` (`0x0004`) switches that on.

@@ localagent/cli.py make_printer @@
- Returns the function that shows progress lines: in a diff, added lines green and removed lines red (`@@` headers
  cyan); tool calls (`-> ...`) cyan; results starting with `REJECTED`, `ERROR`, `DENIED`, `NOT RUN` or `NOT FINISHED`
  red. A colour is the code `\033[<n>m` before the text and `\033[0m` after it.

## `handle_request` — one request
@@ localagent/cli.py handle_request @@
- `stream` prints streamed text as it arrives; `show` prints progress lines (coloured by the chat).
- Runs the agent. With `plan_only` the agent only investigates and returns a plan.
- A model-service problem (`LLMError`) is printed and the chat continues. **Ctrl+C** (`KeyboardInterrupt`) stops the
  task: `agent.repair()` gives any half-finished tool calls an "interrupted" result so the conversation stays valid,
  and you can type the next request.
- After the run: if text was streamed, the line is ended; the answer is printed too unless it is exactly what was
  already streamed — so a `finish` summary always appears, even when the model streamed other text first.
- After a normal run (not a plan) the files **changed during this request** are verified once more (`since=started`):
  fast even in big projects, and never blaming you for files broken before. Then the `after_request` hook runs if you
  set one (for example your test suite) and its exit code and output are shown.
- Finally the usage line, ending with **who answered** — e.g. `[3 model calls | ... | 4.2s | gemini-3.5-flash-lite]`, or
  `local:Qwen3-8B-Q4_K_M` when you are offline — so you always know whether the internet was used. Returns the answer
  (used by `/plan`).

## `@file`, `# note` and the logs folder
@@ localagent/cli.py attach_files @@
- Like Claude Code's `@` mentions: for each `@path` naming a text file inside the workspace, the file's contents are added
  to the request, which saves the model a `read_file` call. Measured live: "what does @calc.py compute?" took 1 call and
  2 004 tokens instead of 2 calls and 4 017 (after rule 1 told the model not to read attached files again).
- Trailing punctuation is ignored (`@app.py,`). Paths outside the workspace, missing files, binary files and repeats are
  left as typed. `limit` caps the total: the chat allows a third of the history budget, so attachments can never crowd
  out the conversation (a file that does not fit is left for the model to read in parts).

@@ localagent/cli.py remember @@
- Like Claude Code's `#` memory: `# use pytest, not unittest` adds `- use pytest, not unittest` to `AGENT.md` under a
  `## Notes` heading — at the end of that section, before the next `## ` heading. A missing file or section is
  created. The file's line breaks are kept (`read_text`/`write_text`). No model call is used.

@@ localagent/cli.py sessions_folder @@
- Where this project's conversation logs live: `<workspace>\.localagent\sessions`. Joining a path with an absolute
  `state_dir` gives that absolute path (that is how `pathlib` joins), so a custom absolute location still works.

## `Chat` — the loop and its commands
@@ localagent/cli.py Chat @@
@@ localagent/cli.py Chat.__init__ @@
- Holds everything a command may change: the config and backend (`/model`), the agent and its session log (`/clear`,
  `/resume`), the undo steps, custom commands (from `.localagent/commands`, listed under the help text with the first
  line of each file), the coloured printer, and `pending` — output of `!commands` waiting to go with the next request.
- `ctx_chosen` remembers whether you chose the history budget yourself (then `/model` keeps it).
- `--resume`/`-c` reloads the newest conversation of **this project** (repairing it if it ended mid-task, and starting
  after its last `/clear` or compaction — see `session.md`).

@@ localagent/cli.py Chat.run @@
- One request: earlier `!` output goes first ("I ran ... Output: ... My request: ..."), `@file` contents are attached,
  and the files it changes become one `/undo` step labelled with the start of your request (`take` before, `commit`
  after — also when the request fails or is stopped).

@@ localagent/cli.py Chat.model_status @@
- What `/model` shows: the mode and what it means, the model chain now and which model answered last, whether a Gemini
  key is set, what the local model is (running, or started when needed, or not set up), and how to switch.

@@ localagent/cli.py Chat.switch @@
- `/model <mode>`: builds the new setup (`choose_mode`, then the history budget) and the new backend. Going offline
  starts the local model **right away** (`ensure_ready`), so it loads now and not in the middle of your next task; this
  can take up to a minute and can be stopped with Ctrl+C (nothing changes then).
- Any problem (no key, no local model, the server would not start) is shown and **nothing changes**.
- Going online stops a local model server this program started (an 8B model holds about 5 GB of memory).
- Then the agent gets the new backend — the conversation and files stay — and the new setup is printed.

@@ localagent/cli.py Chat.resume @@
- `/resume` lists this project's last 10 conversations, newest first, each with its date and first request; the
  current one is marked. `/resume 3` clears the current conversation (logged as a reset) and continues number 3 — its
  messages are added to the current log, so it carries on there.

@@ localagent/cli.py Chat.shell @@
- `!command`, like Claude Code's bash mode: runs the command yourself in the project folder (PowerShell on Windows), with
  a 10-minute limit, **without asking** (you typed it) and **without a model call**. The output is shown and kept in
  `pending`, so the model sees it with your next request ("!pytest", then "fix the failures"). Ctrl+C stops it.

@@ localagent/cli.py Chat.command @@
- Acts on one typed line and returns `False` to quit (`/quit`, `/exit`). The command word is matched in lower case.
- `/help`; `/clear` or `/reset` (forget the conversation, logged so `--resume` respects it); `/undo`; `/stats` (also each
  Gemini model's daily use and whether you are offline); `/compact`; `/init` (restricted to `INIT_TOOLS`; afterwards it
  reports whether `AGENT.md` now exists and reloads the system prompt so the notes apply at once); `/plan <request>`
  (read-only investigation, then `Carry out this plan? [y/N]`, then `PLAN_FOLLOW_UP`); `/model [mode]` and the
  shortcuts `/online`, `/offline`, `/auto`; `/resume [n]`; `/permissions` (the allow and deny rules in force and where
  they live); `/doctor` (the health check, inside the chat).
- `/<name>` of a custom command expands its file (`expand_command`) and runs it as a request. Any other `/word` is
  reported as unknown **without contacting the model** (a typo should not cost an API call).
- `!command` runs a command (`shell`); a one-line `# note` is remembered in `AGENT.md` (`remember`), after which the
  system prompt is rebuilt so the note applies at once. Anything else is a request (`run`).

@@ localagent/cli.py Chat.loop @@
- The banner shows the version, the mode, the model chain, the sandbox and the folder, then the automatic-setup notes
  and the toolbox's notes (for example which MCP servers started), then `HELP`.
- Reads a request at a time; Ctrl+C or Ctrl+D at the prompt (`EOFError`) leaves the program. A Ctrl+C during a command
  (for example a long `!command`) stops that command and returns to the prompt.

## `run_once` — the `-p` mode
@@ localagent/cli.py run_once @@
- One request without the chat, for scripts and other programs: `localagent -p "list the TODO comments" > todos.txt`.
  The request may come from your input (`-p -`) and may attach `@files`.
- The **answer goes to standard output** and everything else (progress, notices, the usage line) to standard error,
  so a script receives just the answer. Actions that would need a question are refused (see `refuse`). The
  conversation is logged like any other, so `localagent -c` can continue it in the chat.
- Exit code 0 when answered and the changed files pass the check, 1 when the model failed or a changed file is broken.

## The product commands
@@ localagent/cli.py run_subcommand @@
- `localagent setup` and `localagent models ...` (see `models.md`). They are recognised **before** the flag parser,
  so they need no flags of their own and the chat's flags stay as they were. The settings files' `env` is applied
  and the configuration read; a problem there is one `setup problem` line. Ctrl+C during a download says how to
  continue it (exit code 130, the usual code for "stopped by Ctrl+C").

## `main` — start-up
@@ localagent/cli.py main @@
- First makes the output robust: with output piped or redirected on Windows, Python writes in the old Windows code
  page, where one emoji or arrow in a model's answer or a diff would crash the program with `UnicodeEncodeError`;
  `errors="replace"` shows such a character as `?` instead (found in review).
- A first word `setup` or `models` goes to `run_subcommand`.
- Loads the settings files of the workspace and applies their `env` **before** building the config (so a settings file
  can set the mode, the model file or anything else). A broken `settings.json`, or a typo in a variable such as
  `LOCALAGENT_NUM_CTX=8k`, is printed as one `setup problem` line with exit code 2 instead of a traceback.
- Unless `--no-auto`, applies automatic setup. When no model can work, that is a setup problem too — except with
  `--doctor`, which then runs anyway and shows the reason first, because that is when you need it most.
- `--doctor` prints the automatic choices plus the health report and exits with 0 (healthy) or 1.
- Builds the backend (a missing Gemini key → setup problem). The approver: with `--auto-approve` nothing is asked (deny
  rules still apply); with `-p` nothing can be asked, so `refuse`; otherwise the interactive `Approver`, connected to
  the toolbox's permissions for "always" answers.
- Runs the chat (or the single `-p` request). `finally` always closes the toolbox — stopping the Docker sandbox and any
  MCP server programs — and stops a local model server this run started, even after an error.
