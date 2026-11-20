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
```python
  1 | """Command-line interface: the chat prompt around the Agent (like Claude Code's terminal UI).
  2 | 
  3 | Start it inside a project folder:  localagent        (or: python -m localagent)
  4 | One request, answer printed:       localagent -p "what does this project do?"
  5 | The model follows the mode: "auto" (the default) answers with Gemini when a key is set and
  6 | with the local model when Gemini cannot be reached; "online" uses only Gemini; "offline" only
  7 | the local model. Choose with --online / --offline, or switch any time in the chat with /model.
  8 | Everything else is chosen from what this computer has (the Docker sandbox when it is ready);
  9 | --backend and --no-auto still let you set everything by hand.
 10 | """
 11 | from __future__ import annotations
 12 | 
 13 | import argparse
 14 | import os
 15 | import re
 16 | import sys
 17 | import time
 18 | from dataclasses import replace
 19 | from pathlib import Path
 20 | from typing import Callable
 21 | 
 22 | from . import __version__
 23 | from .agent import Agent
 24 | from .backends import BACKENDS, GEMINI_KEY_HELP, make_backend
 25 | from .checkpoint import Checkpoints
 26 | from .config import MODES, Config, gemini_history, overlay
 27 | from .doctor import run_doctor
 28 | from .llm import ChatBackend, LLMError
 29 | from .localserver import (NO_LOCAL_MODEL, SETUP_HINT, LocalModel, can_start, describe_local,
 30 |                           find_model_file, ollama_up, server_up, stop_local_servers)
 31 | from .meter import Meter
 32 | from .models import run_models, run_setup
 33 | from .runners import sandbox_image_ready
 34 | from .sandbox import SandboxError, looks_binary, resolve_inside, run_command, shell_argv
 35 | from .session import SessionLog, latest_session, list_sessions, load_session, session_title
 36 | from .settings import (PROJECT_SETTINGS, USER_SETTINGS, Permissions, apply_env, expand_command,
 37 |                        load_commands, load_settings, suggest_rule)
 38 | from .tools import Approval, ToolBox, read_text, write_text
 39 | 
 40 | HELP = """Commands:
 41 |   /plan <request>    look first, show a plan, then carry it out if you agree
 42 |   /model [mode]      show the model, or switch: online (Gemini), offline (local model), auto
 43 |   /init              write AGENT.md (project notes the agent reads every session)
 44 |   /compact           summarise the conversation to save tokens
 45 |   /undo              revert the file changes of the last request that changed files
 46 |   /clear             forget the conversation (files stay); /reset does the same
 47 |   /resume [number]   list this project's earlier conversations, or continue one
 48 |   /stats             calls, tokens, time and today's quota use
 49 |   /permissions       the permission rules in force
 50 |   /doctor            check the setup
 51 |   /help   /quit      (your own commands: .localagent/commands/<name>.md -> /<name>)
 52 | Shortcuts: !command runs a command yourself (no model call; its output goes with your next
 53 | request), # note saves a note in AGENT.md, @file in a request attaches that file.
 54 | Several lines: end a line with \\, or put \"\"\" alone on a line to start and end a block.
 55 | Ctrl+C stops the current task; the conversation is kept."""
 56 | INIT_PROMPT = (
 57 |     "Create AGENT.md in the workspace root: notes for an AI coding agent working on this project. "
 58 |     "First look at the project (outline, and read the README and main config files if present). "
 59 |     "Write at most 60 lines with these sections: Purpose; How to run; How to test; Conventions "
 60 |     "(style, structure); Key files (one line each). If AGENT.md exists, update it instead. "
 61 |     "Only write AGENT.md: do not run, fix or change anything else, even if you notice problems "
 62 |     "(mention them in AGENT.md under 'Known issues').")
 63 | INIT_TOOLS = frozenset({"write_file"})           # /init may read anything but only write files
 64 | PLAN_FOLLOW_UP = "Implement the plan above step by step, verify the result, then finish."
 65 | BUILT_IN = {"/quit", "/exit", "/help", "/reset", "/clear", "/undo", "/stats", "/compact", "/init",
 66 |             "/plan", "/model", "/online", "/offline", "/auto", "/resume", "/permissions", "/doctor"}
 67 | MODE_TEXT = {"auto": "Gemini, and the local model when Gemini cannot be reached",
 68 |              "online": "Gemini only", "offline": "the local model only"}
 69 | NOTHING_HELP = ("No model is available yet. The guided setup takes a minute:  localagent setup\n"
 70 |                 "Or by hand (one or both):\n"
 71 |                 f"  online:  {GEMINI_KEY_HELP}\n  offline: {NO_LOCAL_MODEL}\n"
 72 |                 "Then run  localagent --doctor  to check.")
 73 | SUBCOMMANDS = ("setup", "models")                # product commands, handled before the flags
 74 | COMMANDS_EPILOG = ("commands:\n  localagent setup            guided first run: Gemini key, offline "
 75 |                    "model, Docker\n  localagent models           local models: list, download NAME, "
 76 |                    "use NAME-or-FILE")
 77 | MENTION = re.compile(r"(?<!\S)@([^\s@]+)")       # "@src/app.py", not an e-mail address
 78 | COLORS = {"+": "32", "-": "31", "@": "36"}       # diff lines: green, red, cyan
 79 | FAILURE_WORDS = ("REJECTED", "ERROR", "DENIED", "NOT RUN", "NOT FINISHED")
 80 | 
 81 | 
```
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
```python
 82 | class Approver:
 83 |     """Asks before anything runs. Answering "a" (always) saves a permission rule, like
 84 |     Claude Code's "Yes, and don't ask again"."""
 85 | 
```
```python
 86 |     def __init__(self, read: Callable[[str], str] = input) -> None:
 87 |         self._read = read
 88 |         self.permissions: Permissions | None = None       # set once the toolbox exists
 89 | 
```
```python
 90 |     def __call__(self, action: str) -> bool:
 91 |         try:
 92 |             answer = self._read(f"  allow: {action}? [y]es / [n]o / [a]lways: ").strip().lower()
 93 |         except EOFError:                                  # no keyboard (input ran out): no
 94 |             return False
 95 |         if answer in {"a", "always"} and isinstance(action, Approval) and self.permissions:
 96 |             rule = suggest_rule(action.tool, action.subject)
 97 |             saved = self.permissions.always_allow(rule)
 98 |             print(f"  allowed from now on: {rule}" + (f" (saved in {saved})" if saved else ""))
 99 |         return answer in {"y", "yes", "a", "always"}
100 | 
101 | 
```
- Called by the toolbox whenever no permission rule decides (see `settings.md`). It prints the question, e.g.
  `allow: run python main.py? [y]es / [n]o / [a]lways:`.
- `y`/`yes` allows once. Enter or anything else refuses (the safe default). When there is no input at all (input piped
  in and used up) that counts as "no" — before 0.6 it crashed the program.
- `a`/`always` allows **and** saves a narrow rule (`suggest_rule`: exactly this command, this file, this website or this
  tool) in the project's `.localagent/settings.json`, so you are not asked again — like Claude Code's "Yes, and don't
  ask again". The question is an `Approval` (a string that also knows the tool and subject), which is how the rule can
  be built. `read` is injectable so tests can answer.

```python
102 | def refuse(action: str) -> bool:
103 |     """The approver of -p runs: nobody is there to ask, so anything not allowed is refused."""
104 |     print(f"[refused (no chat to ask in): {action} - allow it in .localagent/settings.json "
105 |           "or use --auto-approve]", file=sys.stderr)
106 |     return False
107 | 
108 | 
```
- The approver of `-p` runs: there is no chat to ask in, so an action that no rule allows is refused (and said so on the
  error stream). Allow it with a rule, or pass `--auto-approve`. Claude Code's `-p` behaves the same way.

```python
109 | def confirm(question: str, read: Callable[[str], str] = input) -> bool:
110 |     """A yes/no question; no input (EOF) or Ctrl+C counts as no."""
111 |     try:
112 |         return read(question).strip().lower() in {"y", "yes"}
113 |     except (EOFError, KeyboardInterrupt):
114 |         print()
115 |         return False
116 | 
117 | 
```
- A yes/no question (`/plan`'s "Carry out this plan?"); no input or Ctrl+C means no.

## The flags shared by the chat, the server and the evals
```python
118 | def add_model_flags(parser: argparse.ArgumentParser) -> None:
119 |     """The flags every entry point shares (chat, server, evals): which model, where code runs."""
120 |     parser.add_argument("--mode", choices=MODES,
121 |                         help="auto: Gemini, the local model when offline (default); "
122 |                              "online: Gemini only; offline: the local model only")
123 |     parser.add_argument("--online", dest="mode", action="store_const", const="online",
124 |                         help="same as --mode online")
125 |     parser.add_argument("--offline", dest="mode", action="store_const", const="offline",
126 |                         help="same as --mode offline")
127 |     parser.add_argument("--backend", choices=sorted(BACKENDS),
128 |                         help="model service type, chosen by hand (the mode is then ignored)")
129 |     parser.add_argument("--model", help="model name for ollama/openai, e.g. qwen3-8b")
130 |     parser.add_argument("--api-base", help="URL of an OpenAI-style server, e.g. http://host:8080/v1")
131 |     parser.add_argument("--sandbox", choices=["local", "docker"],
132 |                         help="where code runs: this machine or a Docker container")
133 | 
134 | 
```
- Adds the flags all three programs need: `--mode` (with the shortcuts `--online` and `--offline`, which simply set it),
  `--backend`, `--model`, `--api-base` and `--sandbox`. Defined once here, so the three cannot drift apart.

```python
135 | def model_flags(args: argparse.Namespace) -> dict:
136 |     """The values of those shared flags, as Config field names (None = not given)."""
137 |     return {"mode": args.mode, "backend": args.backend, "model": args.model,
138 |             "api_base": args.api_base, "sandbox": args.sandbox}
139 | 
140 | 
```
- Reads those values back as `Config` field names (`api-base` becomes `api_base`), ready for `overlay`.

## `build_parser`
```python
141 | def build_parser() -> argparse.ArgumentParser:
142 |     """Define the command-line flags."""
143 |     parser = argparse.ArgumentParser(prog="localagent", description="A verifying coding agent",
144 |                                      epilog=COMMANDS_EPILOG,
145 |                                      formatter_class=argparse.RawDescriptionHelpFormatter)
146 |     parser.add_argument("--version", action="version", version=f"localagent {__version__}")
147 |     parser.add_argument("-p", "--print", dest="prompt", metavar="REQUEST",
148 |                         help="answer one request, print the answer and exit ('-' reads it from input)")
149 |     add_model_flags(parser)
150 |     parser.add_argument("--workspace", type=Path, help="project folder (default: current folder)")
151 |     parser.add_argument("--num-ctx", type=int, help="history budget in tokens")
152 |     parser.add_argument("--doctor", action="store_true", help="check the setup and exit")
153 |     parser.add_argument("--resume", "-c", "--continue", dest="resume", action="store_true",
154 |                         help="continue the latest conversation")
155 |     parser.add_argument("--type-check", action="store_true", help="also verify with mypy")
156 |     parser.add_argument("--auto-approve", action="store_true", help="never ask before running")
157 |     parser.add_argument("--network", action="store_true",
158 |                         help="allow internet inside the Docker sandbox (e.g. pip install)")
159 |     parser.add_argument("--fallback", choices=["local", "ollama", "openai"],
160 |                         help="with --backend gemini: local model to use when Gemini fails")
161 |     parser.add_argument("--reasoning", help="reasoning effort for OpenAI-style servers, e.g. low")
162 |     parser.add_argument("--allow-shell", action="store_true",
163 |                         help="let the model run shell commands on this computer (asks each time)")
164 |     parser.add_argument("--no-auto", action="store_true",
165 |                         help="do not pick backend/sandbox/fallback automatically")
166 |     return parser
167 | 
168 | 
```
- `--version` prints `localagent 0.6.0` and exits. `-p`/`--print REQUEST` answers one request (`-` reads it from your
  input, e.g. `type question.txt | localagent -p -`).
- The shared flags (`add_model_flags`), then the chat's own. Value flags default to `None`, meaning "not given" (so
  automatic setup and environment variables can fill them): `--workspace` (default: the current folder), `--num-ctx`,
  `--fallback` (`local`, `ollama` or `openai`), `--reasoning`.
- On/off flags: `--doctor`, `--resume` (also `-c` and `--continue`, Claude Code's names), `--type-check`,
  `--auto-approve`, `--network` (internet inside the sandbox), `--allow-shell` (shell on this computer, asks every time)
  and `--no-auto` (no automatic choices).

## `build_config`
```python
169 | def build_config(args: argparse.Namespace) -> Config:
170 |     """Environment defaults overlaid with the flags the user actually gave."""
171 |     config = overlay(Config.from_env(), **model_flags(args), workspace=args.workspace,
172 |                      num_ctx=args.num_ctx, fallback_backend=args.fallback,
173 |                      reasoning_effort=args.reasoning)
174 |     return replace(config, type_check=args.type_check, auto_approve=args.auto_approve,
175 |                    sandbox_network=args.network, allow_host_shell=args.allow_shell)
176 | 
177 | 
```
- Starts from `Config.from_env()` (defaults + environment variables), `overlay`s only the value flags actually given
  (the shared ones from `model_flags` plus the chat's own), then sets the on/off flags. Order of strength: flags >
  environment variables > settings-file `env` > defaults.

## Choosing the model setup
```python
178 | def local_kind(config: Config) -> str:
179 |     """The local model the modes would use: "openai" (a server answers at api_base), "ollama"
180 |     (Ollama answers), "local" (llama.cpp can be started here) or "" (none)."""
181 |     if server_up(config.api_base):
182 |         return "openai"
183 |     if ollama_up(config.host):
184 |         return "ollama"
185 |     return "local" if can_start(config) else ""
186 | 
187 | 
```
- What the local model would be right now: `openai` when a server answers at `api_base` (llama.cpp, LM Studio),
  `ollama` when Ollama answers, `local` when llama.cpp can be started here, `""` when there is nothing. The quick checks
  take a second at most.

```python
188 | def choose_mode(config: Config, mode: str, ctx_chosen: bool = False) -> tuple[Config, list[str]]:
189 |     """Backend and offline backup for ``mode``; the notes explain the choice. Raises ValueError,
190 |     saying what to do, when the mode cannot work on this computer."""
191 |     if mode not in MODES:
192 |         raise ValueError(f"unknown mode '{mode}': choose {', '.join(MODES)}")
193 |     keyed = bool(config.gemini_api_key)
194 |     if mode == "online" and not keyed:
195 |         raise ValueError("online mode needs a Gemini key. " + GEMINI_KEY_HELP)
196 |     if mode == "online" or (mode == "auto" and keyed):
197 |         backup = "" if mode == "online" else (config.fallback_backend or local_kind(config))
198 |         config = replace(config, mode=mode, backend="gemini", fallback_backend=backup)
199 |         notes = [f"mode {mode}: Gemini answers (GEMINI_API_KEY is set)"]
200 |         if mode == "auto":
201 |             notes.append("offline backup: " + (describe_local(config) if backup else
202 |                                                f"none set up ({SETUP_HINT})"))
203 |         return config, notes
204 |     if not local_kind(config):
205 |         raise ValueError(NO_LOCAL_MODEL if mode == "offline" else NOTHING_HELP)
206 |     context = config.num_ctx if ctx_chosen else config.local_ctx
207 |     config = replace(config, mode=mode, backend="local", fallback_backend="", num_ctx=context,
208 |                      local_ctx=context)
209 |     reason = "" if mode == "offline" else " (no GEMINI_API_KEY, so offline)"
210 |     return config, [f"mode {mode}: the local model answers{reason} - {describe_local(config)}"]
211 | 
212 | 
```
- Turns a mode into a model setup and explains it:
  - `online` needs a key (else a `ValueError` saying how to get one) → backend `gemini`, no local backup;
  - `auto` with a key → backend `gemini` with the local model as its last link (`fallback_backend`) when there is one,
    and a note naming it — or "none set up";
  - `offline`, or `auto` without a key → backend `local`, history budget = the local context (8K) unless you chose a
    size; with nothing local a `ValueError` says what to install (`NO_LOCAL_MODEL`, or `NOTHING_HELP` in `auto`).
- Used at start-up (`auto_configure`), by `/model`, and by the HTTP server and the evals, so all four agree.

```python
213 | def auto_configure(config: Config, args: argparse.Namespace) -> tuple[Config, list[str]]:
214 |     """Choose what the user left open from what is available; explain each choice."""
215 |     notes: list[str] = []
216 |     ctx_chosen = args.num_ctx is not None or "LOCALAGENT_NUM_CTX" in os.environ
217 |     if args.backend is None and "LOCALAGENT_BACKEND" not in os.environ:
218 |         config, notes = choose_mode(config, config.mode, ctx_chosen)
219 |     if (args.sandbox is None and "LOCALAGENT_SANDBOX" not in os.environ
220 |             and sandbox_image_ready(config.sandbox_image)):
221 |         config = replace(config, sandbox="docker")
222 |         notes.append("sandbox: docker (Docker and the sandbox image are ready)")
223 |     if (config.backend == "gemini" and not config.fallback_backend and args.fallback is None
224 |             and config.mode != "online" and server_up(config.api_base)):
225 |         config = replace(config, fallback_backend="openai")
226 |         notes.append(f"fallback: the local model server at {config.api_base}")
227 |     budgeted = gemini_history(config, chosen=ctx_chosen)
228 |     if budgeted.num_ctx != config.num_ctx:
229 |         config = budgeted
230 |         notes.append(f"history budget: {config.num_ctx} tokens (Gemini handles long conversations)")
231 |     return config, notes
232 | 
233 | 
```
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
```python
234 | def model_label(config: Config) -> str:
235 |     """Short description of which model(s) will answer, for banners and scoreboards."""
236 |     local = find_model_file(config)
237 |     local_name = f"local:{local.stem}" if local else "local model"
238 |     if config.backend == "gemini":
239 |         chain = " > ".join(config.gemini_models + config.gemini_strong_models)
240 |         backup = "" if not config.fallback_backend else (
241 |             f" > {local_name}" if config.fallback_backend == "local"
242 |             else f" > {config.fallback_backend}:{config.model}")
243 |         return f"gemini:{chain}{backup}"
244 |     if config.backend == "local":
245 |         return local_name
246 |     return f"{config.backend}:{config.model}"
247 | 
248 | 
```
- A readable description of who answers, e.g.
  `gemini:gemini-3.5-flash-lite > gemini-3.1-flash-lite > gemini-3.5-flash > gemini-2.5-flash > local:Qwen3-8B-Q4_K_M`
  (the local model named after its file), or `local:Qwen3-8B-Q4_K_M` offline. Used by the banner, `/model` and the eval
  scoreboard.

## `read_request` — typing a request
```python
249 | def read_request(read: Callable[[str], str] = input) -> str:
250 |     """One request from the keyboard; supports ``\\`` line continuation and \"\"\" blocks."""
251 |     first = read("you> ").lstrip("﻿")        # piped input may start with a byte-order mark
252 |     if first.strip() == '"""':
253 |         lines: list[str] = []
254 |         while (line := read("... ")).strip() != '"""':
255 |             lines.append(line)
256 |         return "\n".join(lines).strip()
257 |     parts = [first]
258 |     while parts[-1].endswith("\\"):
259 |         parts[-1] = parts[-1][:-1]
260 |         parts.append(read("... "))
261 |     return "\n".join(parts).strip()
262 | 
263 | 
```
- Reads one line after `you> ` and removes an invisible byte-order mark from its start: text piped into a program by
  PowerShell can begin with one, which made a piped `/init` arrive as `﻿/init` and be treated as an ordinary
  request (found in the end-to-end test).
- A line ending in `\` continues on the next line (`... ` prompt). A line that is only
  `"""` starts a block that ends at the next `"""` line — handy for pasting code or long instructions.
  (`:=` reads a line and tests it in one step.) `read` is injectable for tests.

## Colours
```python
264 | def use_color() -> bool:
265 |     """True when the terminal shows colours (and NO_COLOR is not set); switches them on in
266 |     the Windows console, which needs to be told."""
267 |     if os.environ.get("NO_COLOR") or not sys.stdout.isatty():
268 |         return False
269 |     if sys.platform == "win32":
270 |         import ctypes
271 |         kernel32 = ctypes.windll.kernel32
272 |         handle = kernel32.GetStdHandle(-11)                # the screen
273 |         mode = ctypes.c_uint32()
274 |         if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
275 |             return False
276 |         kernel32.SetConsoleMode(handle, mode.value | 0x0004)   # ENABLE_VIRTUAL_TERMINAL_PROCESSING
277 |     return True
278 | 
279 | 
```
- Colours only when the output is a terminal (not a file or a pipe) and `NO_COLOR` is not set (the common convention
  for "no colours, please"). The Windows console shows colour codes only after being told to: `SetConsoleMode` with
  `ENABLE_VIRTUAL_TERMINAL_PROCESSING` (`0x0004`) switches that on.

```python
280 | def make_printer(color: bool) -> Callable[[str], None]:
281 |     """How progress lines are shown: diffs in green/red, tool calls in cyan, failures in red."""
282 |     def show(line: str) -> None:
283 |         if color:
284 |             code = ""
285 |             if line.startswith("     ") and line[5:6] in COLORS:
286 |                 code = COLORS[line[5]]
287 |             elif line.startswith("-> "):
288 |                 code = "36"
289 |             elif line.lstrip().startswith(FAILURE_WORDS):
290 |                 code = "31"
291 |             line = f"\033[{code}m{line}\033[0m" if code else line
292 |         print(line)
293 |     return show
294 | 
295 | 
```
- Returns the function that shows progress lines: in a diff, added lines green and removed lines red (`@@` headers
  cyan); tool calls (`-> ...`) cyan; results starting with `REJECTED`, `ERROR`, `DENIED`, `NOT RUN` or `NOT FINISHED`
  red. A colour is the code `\033[<n>m` before the text and `\033[0m` after it.

## `handle_request` — one request
```python
296 | def handle_request(agent: Agent, toolbox: ToolBox, meter: Meter, text: str,
297 |                    plan_only: bool = False, allowed: frozenset[str] | None = None,
298 |                    show: Callable[[str], None] = print) -> str:
299 |     """Run one request, show progress and the answer, then verify the whole workspace."""
300 |     streamed: list[str] = []
301 | 
302 |     def stream(token: str) -> None:
303 |         streamed.append(token)
304 |         print(token, end="", flush=True)
305 | 
306 |     started = time.time()
307 |     try:
308 |         answer = agent.run(text, on_event=show, on_token=stream, plan_only=plan_only,
309 |                            allowed=allowed)
310 |     except LLMError as exc:
311 |         agent.repair()
312 |         print(f"\nerror: {exc}")
313 |         return ""
314 |     except KeyboardInterrupt:                      # Ctrl+C: stop this task, keep the chat
315 |         agent.repair()
316 |         print("\n[stopped - the conversation is kept; type your next request]")
317 |         return ""
318 |     shown = "".join(streamed).strip()
319 |     if streamed:
320 |         print()                                    # end the streamed line
321 |     if answer.strip() and answer.strip() != shown:
322 |         print(answer)                              # e.g. a finish summary that was not streamed
323 |     if not plan_only:
324 |         problems = toolbox.check_workspace(since=started)      # only what changed just now
325 |         print("[check of changed files: all OK]" if not problems
326 |               else "[workspace check FAILED]\n" + "\n".join(problems))
327 |         outcome = toolbox.run_hook("after_request")      # e.g. your test suite
328 |         if outcome is not None:
329 |             print(f"[after_request hook: exit code {outcome[0]}]\n{outcome[1].strip()}")
330 |     who = getattr(agent.backend, "last_model", "")
331 |     print(f"[{meter.summary()}" + (f" | {who}" if who else "") + "]")
332 |     return answer
333 | 
334 | 
```
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
```python
335 | def attach_files(text: str, root: Path, limit: int) -> tuple[str, list[str]]:
336 |     """``@path`` mentions of workspace files: their contents are added to the request, which
337 |     saves the model a read_file call. Unknown paths stay as typed; ``limit`` caps the size."""
338 |     attached: list[str] = []
339 |     parts: list[str] = []
340 |     total = 0
341 |     for match in MENTION.finditer(text):
342 |         name = match.group(1).rstrip(".,;:!?)\"'")
343 |         try:
344 |             path = resolve_inside(root, name)
345 |         except SandboxError:
346 |             continue
347 |         if name in attached or not path.is_file() or looks_binary(path):
348 |             continue
349 |         content = path.read_text(encoding="utf-8-sig", errors="replace")
350 |         if total + len(content) > limit:
351 |             continue                               # too big for the context: the model reads it
352 |         total += len(content)
353 |         attached.append(name)
354 |         parts.append(f"Contents of {name} (attached by the user):\n```\n{content}\n```")
355 |     return (text + "\n\n" + "\n\n".join(parts) if parts else text), attached
356 | 
357 | 
```
- Like Claude Code's `@` mentions: for each `@path` naming a text file inside the workspace, the file's contents are added
  to the request, which saves the model a `read_file` call. Measured live: "what does @calc.py compute?" took 1 call and
  2 004 tokens instead of 2 calls and 4 017 (after rule 1 told the model not to read attached files again).
- Trailing punctuation is ignored (`@app.py,`). Paths outside the workspace, missing files, binary files and repeats are
  left as typed. `limit` caps the total: the chat allows a third of the history budget, so attachments can never crowd
  out the conversation (a file that does not fit is left for the model to read in parts).

```python
358 | def remember(note: str, root: Path) -> Path:
359 |     """Add ``note`` to AGENT.md under "## Notes" (both are created when missing)."""
360 |     path = root / "AGENT.md"
361 |     text, style = read_text(path) if path.is_file() else ("# Project notes\n", ("\n", False))
362 |     if "## Notes" not in text:
363 |         text = text.rstrip("\n") + "\n\n## Notes\n"
364 |     start = text.index("## Notes")
365 |     end = text.find("\n## ", start + 1)                # the next section, if any
366 |     end = len(text) if end == -1 else end
367 |     text = text[:end].rstrip("\n") + f"\n- {note}\n" + text[end:]
368 |     write_text(path, text, style)
369 |     return path
370 | 
371 | 
```
- Like Claude Code's `#` memory: `# use pytest, not unittest` adds `- use pytest, not unittest` to `AGENT.md` under a
  `## Notes` heading — at the end of that section, before the next `## ` heading. A missing file or section is
  created. The file's line breaks are kept (`read_text`/`write_text`). No model call is used.

```python
372 | def sessions_folder(config: Config) -> Path:
373 |     """This project's conversation logs: <project>/.localagent/sessions (an absolute state_dir wins)."""
374 |     return config.workspace / config.state_dir / "sessions"
375 | 
376 | 
```
- Where this project's conversation logs live: `<workspace>\.localagent\sessions`. Joining a path with an absolute
  `state_dir` gives that absolute path (that is how `pathlib` joins), so a custom absolute location still works.

## `Chat` — the loop and its commands
```python
377 | class Chat:
378 |     """The interactive loop and its commands. Holds what a command may change: the model
379 |     setup (/model), the conversation (/clear, /resume), and output waiting to be sent (!)."""
380 | 
```
```python
381 |     def __init__(self, config: Config, args: argparse.Namespace, backend: ChatBackend,
382 |                  toolbox: ToolBox, meter: Meter) -> None:
383 |         self.config, self.backend, self.toolbox, self.meter = config, backend, toolbox, meter
384 |         self.ctx_chosen = args.num_ctx is not None or "LOCALAGENT_NUM_CTX" in os.environ
385 |         self.root = config.workspace.resolve()
386 |         self.log = SessionLog(sessions_folder(config))
387 |         self.agent = Agent(config, backend, toolbox, meter, self.log)
388 |         self.checkpoints = Checkpoints(config.workspace)
389 |         self.commands = load_commands(config.workspace)  # custom /commands (.localagent/commands)
390 |         self.show = make_printer(use_color())
391 |         self.pending: list[str] = []                    # output of !commands, sent next time
392 |         self.help = HELP + "".join(f"\n  /{name:<16} {(text.strip().splitlines() or [''])[0][:60]}"
393 |                                    for name, text in sorted(self.commands.items()))
394 |         if args.resume:
395 |             previous = latest_session(sessions_folder(config))
396 |             if previous is not None and previous != self.log.path:
397 |                 self.agent.restore(load_session(previous))
398 |                 print(f"resumed {previous.name}")
399 | 
```
- Holds everything a command may change: the config and backend (`/model`), the agent and its session log (`/clear`,
  `/resume`), the undo steps, custom commands (from `.localagent/commands`, listed under the help text with the first
  line of each file), the coloured printer, and `pending` — output of `!commands` waiting to go with the next request.
- `ctx_chosen` remembers whether you chose the history budget yourself (then `/model` keeps it).
- `--resume`/`-c` reloads the newest conversation of **this project** (repairing it if it ended mid-task, and starting
  after its last `/clear` or compaction — see `session.md`).

```python
400 |     def run(self, text: str, plan_only: bool = False, allowed: frozenset[str] | None = None) -> str:
401 |         """One request: earlier !command output and @file contents go with it; the files it
402 |         changes become one /undo step."""
403 |         label = (text.splitlines() or [""])[0][:60]          # how /undo names this step
404 |         if self.pending:
405 |             text = "\n\n".join(self.pending) + "\n\nMy request: " + text
406 |             self.pending = []
407 |         limit = int(self.config.num_ctx * 0.7) * 4 // 3      # attachments: a third of the budget
408 |         text, attached = attach_files(text, self.root, limit)
409 |         if attached:
410 |             print(f"[attached: {', '.join(attached)}]")
411 |         message = self.checkpoints.take(label)
412 |         if message:
413 |             print(f"[{message}]")
414 |         try:
415 |             return handle_request(self.agent, self.toolbox, self.meter, text, plan_only, allowed,
416 |                                   self.show)
417 |         finally:
418 |             self.checkpoints.commit()
419 | 
```
- One request: earlier `!` output goes first ("I ran ... Output: ... My request: ..."), `@file` contents are attached,
  and the files it changes become one `/undo` step labelled with the start of your request (`take` before, `commit`
  after — also when the request fails or is stopped).

```python
420 |     def model_status(self) -> list[str]:
421 |         """What /model shows: the mode, who answers, and what online and offline would use."""
422 |         key = "key set" if self.config.gemini_api_key else "no key - " + GEMINI_KEY_HELP
423 |         last = getattr(self.backend, "last_model", "")
424 |         return [f"mode: {self.config.mode} ({MODE_TEXT.get(self.config.mode, '')})",
425 |                 f"models now: {model_label(self.config)}" + (f"; last answer from {last}" if last else ""),
426 |                 f"online:  Gemini, {key}",
427 |                 f"offline: {describe_local(self.config)}",
428 |                 "switch with /model online, /model offline or /model auto"]
429 | 
```
- What `/model` shows: the mode and what it means, the model chain now and which model answered last, whether a Gemini
  key is set, what the local model is (running, or started when needed, or not set up), and how to switch.

```python
430 |     def switch(self, mode: str) -> None:
431 |         """/model <mode>: build the new model setup, start the local model if needed, swap it in.
432 |         The conversation and the files stay; only who answers changes."""
433 |         try:
434 |             config, notes = choose_mode(replace(self.config, fallback_backend=""), mode,
435 |                                         self.ctx_chosen)
436 |             config = gemini_history(config, chosen=self.ctx_chosen)
437 |             backend = make_backend(config)
438 |             if isinstance(backend, LocalModel):
439 |                 backend.ensure_ready()                  # start llama.cpp now, not mid-task
440 |         except (ValueError, LLMError) as exc:
441 |             print(f"cannot switch to {mode}: {exc}")
442 |             return
443 |         except KeyboardInterrupt:
444 |             print("\n[switch stopped; nothing changed]")
445 |             return
446 |         if mode == "online" and stop_local_servers():
447 |             notes.append("stopped the local model server (its memory is free again)")
448 |         self.config, self.backend = config, backend
449 |         self.agent.set_backend(backend, config)
450 |         for note in notes:
451 |             print(f"  {note}")
452 |         print(f"[now {mode}: {model_label(config)}]")
453 | 
```
- `/model <mode>`: builds the new setup (`choose_mode`, then the history budget) and the new backend. Going offline
  starts the local model **right away** (`ensure_ready`), so it loads now and not in the middle of your next task; this
  can take up to a minute and can be stopped with Ctrl+C (nothing changes then).
- Any problem (no key, no local model, the server would not start) is shown and **nothing changes**.
- Going online stops a local model server this program started (an 8B model holds about 5 GB of memory).
- Then the agent gets the new backend — the conversation and files stay — and the new setup is printed.

```python
454 |     def resume(self, choice: str) -> None:
455 |         """/resume: list this project's conversations (newest first), or continue number N."""
456 |         sessions = list_sessions(sessions_folder(self.config))[::-1][:10]
457 |         if not choice:
458 |             for number, path in enumerate(sessions, start=1):
459 |                 mark = "  (this conversation)" if path == self.log.path else ""
460 |                 print(f"  {number}. {session_title(path)}{mark}")
461 |             print("continue one with /resume <number>" if sessions else "no earlier conversations")
462 |             return
463 |         if not choice.isdigit() or not 1 <= int(choice) <= len(sessions):
464 |             print(f"choose a number from 1 to {len(sessions)} (see /resume)")
465 |             return
466 |         picked = sessions[int(choice) - 1]
467 |         if picked == self.log.path:
468 |             print("that is this conversation")
469 |             return
470 |         self.agent.reset()
471 |         self.agent.restore(load_session(picked))
472 |         print(f"continued {picked.name} ({len(self.agent.messages) - 1} messages)")
473 | 
```
- `/resume` lists this project's last 10 conversations, newest first, each with its date and first request; the
  current one is marked. `/resume 3` clears the current conversation (logged as a reset) and continues number 3 — its
  messages are added to the current log, so it carries on there.

```python
474 |     def shell(self, command: str) -> None:
475 |         """``!command``: run it yourself in the project folder (you typed it, so no question);
476 |         its output is shown, and sent along with your next request."""
477 |         code, output = run_command(shell_argv(command), self.root, 600, self.config.max_output_chars)
478 |         print(output.rstrip())
479 |         print(f"[exit code {code}; the output goes with your next request]")
480 |         self.pending.append(f"I ran `{command}` myself (exit code {code}). Output:\n{output.strip()}")
481 | 
```
- `!command`, like Claude Code's bash mode: runs the command yourself in the project folder (PowerShell on Windows), with
  a 10-minute limit, **without asking** (you typed it) and **without a model call**. The output is shown and kept in
  `pending`, so the model sees it with your next request ("!pytest", then "fix the failures"). Ctrl+C stops it.

```python
482 |     def command(self, text: str) -> bool:
483 |         """Act on one line; False means quit."""
484 |         command, _, rest = text.partition(" ")
485 |         rest = rest.strip()
486 |         name = command.lower()
487 |         if name in ("/quit", "/exit"):
488 |             return False
489 |         if name == "/help":
490 |             print(self.help)
491 |         elif name in ("/reset", "/clear"):
492 |             self.agent.reset()
493 |             print("conversation cleared (files are unchanged)")
494 |         elif name == "/undo":
495 |             print(self.checkpoints.undo())
496 |         elif name == "/stats":
497 |             print(self.meter.summary())
498 |             for line in getattr(self.backend, "status", lambda: [])():
499 |                 print(line)                        # daily quota use per Gemini model
500 |         elif name == "/compact":
501 |             try:
502 |                 print(self.agent.compact())
503 |             except (LLMError, KeyboardInterrupt) as exc:
504 |                 print(f"compaction stopped: {exc}")
505 |         elif name == "/init":
506 |             self.run(INIT_PROMPT, allowed=INIT_TOOLS)
507 |             notes_file = self.root / "AGENT.md"
508 |             print(f"[AGENT.md {'written' if notes_file.is_file() else 'was NOT written - try /init again'}]")
509 |             self.agent.refresh_system()            # the new notes take effect immediately
510 |         elif name == "/plan":
511 |             if not rest:
512 |                 print("usage: /plan <what you want done>")
513 |             elif self.run(rest, plan_only=True) and confirm("Carry out this plan? [y/N] "):
514 |                 self.run(PLAN_FOLLOW_UP)
515 |         elif name in ("/model", "/online", "/offline", "/auto"):
516 |             wanted = rest.lower() if name == "/model" else name[1:]
517 |             if wanted:
518 |                 self.switch(wanted)
519 |             else:
520 |                 print("\n".join(self.model_status()))
521 |         elif name == "/resume":
522 |             self.resume(rest)
523 |         elif name == "/permissions":
524 |             rules = self.toolbox.permissions
525 |             print("allow: " + (", ".join(rules.allow) or "(none)"))
526 |             print("deny:  " + (", ".join(rules.deny) or "(none)"))
527 |             print(f"rules live in {USER_SETTINGS} and {self.root / PROJECT_SETTINGS}")
528 |         elif name == "/doctor":
529 |             print("\n".join(run_doctor(self.config)[1]))
530 |         elif name.startswith("/") and name[1:] in self.commands:
531 |             self.run(expand_command(self.commands[name[1:]], rest))
532 |         elif name.startswith("/"):
533 |             print(f"unknown command {command} - type /help (nothing was sent to the model)")
534 |         elif text.startswith("!") and text[1:].strip():
535 |             self.shell(text[1:].strip())
536 |         elif text.startswith("#") and text[1:].strip() and "\n" not in text:
537 |             print(f"[noted in {remember(text[1:].strip(), self.root)}]")
538 |             self.agent.refresh_system()
539 |         else:
540 |             self.run(text)
541 |         return True
542 | 
```
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

```python
543 |     def loop(self, notes: list[str]) -> int:
544 |         """Show the banner, then read and act on lines until /quit or end of input."""
545 |         print(f"localagent {__version__} | {self.config.mode} | {model_label(self.config)} | "
546 |               f"sandbox={self.config.sandbox} | folder={self.root}")
547 |         for note in notes + self.toolbox.notes:
548 |             print(f"  {note}")
549 |         print(self.help)
550 |         while True:
551 |             try:
552 |                 text = read_request()
553 |             except (EOFError, KeyboardInterrupt):
554 |                 print()
555 |                 return 0
556 |             if not text:
557 |                 continue
558 |             try:
559 |                 if not self.command(text):
560 |                     return 0
561 |             except KeyboardInterrupt:              # e.g. Ctrl+C during a !command
562 |                 print("\n[stopped]")
563 | 
564 | 
```
- The banner shows the version, the mode, the model chain, the sandbox and the folder, then the automatic-setup notes
  and the toolbox's notes (for example which MCP servers started), then `HELP`.
- Reads a request at a time; Ctrl+C or Ctrl+D at the prompt (`EOFError`) leaves the program. A Ctrl+C during a command
  (for example a long `!command`) stops that command and returns to the prompt.

## `run_once` — the `-p` mode
```python
565 | def run_once(config: Config, backend: ChatBackend, toolbox: ToolBox, meter: Meter,
566 |              prompt: str) -> int:
567 |     """``-p``: one request without the chat. The answer goes to standard output and progress
568 |     to standard error, so scripts can use the answer; anything that needs a question is refused."""
569 |     text = sys.stdin.read() if prompt == "-" else prompt
570 |     root = config.workspace.resolve()
571 |     text, _ = attach_files(text.strip(), root, int(config.num_ctx * 0.7) * 4 // 3)
572 |     agent = Agent(config, backend, toolbox, meter, SessionLog(sessions_folder(config)))
573 |     started = time.time()
574 |     try:
575 |         answer = agent.run(text, on_event=lambda line: print(line, file=sys.stderr))
576 |     except LLMError as exc:
577 |         print(f"error: {exc}", file=sys.stderr)
578 |         return 1
579 |     print(answer)
580 |     problems = toolbox.check_workspace(since=started)
581 |     if problems:
582 |         print("[workspace check FAILED]\n" + "\n".join(problems), file=sys.stderr)
583 |     print(f"[{meter.summary()}]", file=sys.stderr)
584 |     return 1 if problems else 0
585 | 
586 | 
```
- One request without the chat, for scripts and other programs: `localagent -p "list the TODO comments" > todos.txt`.
  The request may come from your input (`-p -`) and may attach `@files`.
- The **answer goes to standard output** and everything else (progress, notices, the usage line) to standard error,
  so a script receives just the answer. Actions that would need a question are refused (see `refuse`). The
  conversation is logged like any other, so `localagent -c` can continue it in the chat.
- Exit code 0 when answered and the changed files pass the check, 1 when the model failed or a changed file is broken.

## The product commands
```python
587 | def run_subcommand(name: str, words: list[str]) -> int:
588 |     """`localagent setup` and `localagent models ...` (see models.py); no chat is started."""
589 |     try:
590 |         apply_env(load_settings(Path(os.environ.get("LOCALAGENT_WORKSPACE", "."))))
591 |         config = Config.from_env()
592 |     except ValueError as exc:
593 |         print(f"setup problem: {exc}")
594 |         return 2
595 |     try:
596 |         return run_setup(config) if name == "setup" else run_models(words, config)
597 |     except KeyboardInterrupt:                      # a download stopped: it continues next time
598 |         print("\n[stopped - run the same command again to continue]")
599 |         return 130
600 | 
601 | 
```
- `localagent setup` and `localagent models ...` (see `models.md`). They are recognised **before** the flag parser,
  so they need no flags of their own and the chat's flags stay as they were. The settings files' `env` is applied
  and the configuration read; a problem there is one `setup problem` line. Ctrl+C during a download says how to
  continue it (exit code 130, the usual code for "stopped by Ctrl+C").

## `main` — start-up
```python
602 | def main(argv: list[str] | None = None) -> int:
603 |     """Entry point: choose the setup, build the components, then chat (or answer once)."""
604 |     for stream in (sys.stdout, sys.stderr):        # a character the console cannot show must
605 |         if hasattr(stream, "reconfigure"):         # not crash the program (piped output on Windows)
606 |             stream.reconfigure(errors="replace")
607 |     words = sys.argv[1:] if argv is None else argv
608 |     if words and words[0] in SUBCOMMANDS:
609 |         return run_subcommand(words[0], words[1:])
610 |     args = build_parser().parse_args(words)
611 |     workspace = args.workspace or Path(os.environ.get("LOCALAGENT_WORKSPACE", "."))
612 |     try:
613 |         settings = load_settings(workspace)        # its "env" applies before the config is read
614 |         apply_env(settings)
615 |         config = build_config(args)
616 |     except ValueError as exc:                      # e.g. broken settings.json, a typo in a variable
617 |         print(f"setup problem: {exc}")
618 |         return 2
619 |     notes: list[str] = []
620 |     try:
621 |         if not args.no_auto:
622 |             config, notes = auto_configure(config, args)
623 |     except ValueError as exc:                      # e.g. no model at all yet
624 |         if not args.doctor:
625 |             print(f"setup problem: {exc}")
626 |             return 2
627 |         notes = [f"FAIL  {exc}"]
628 |     if args.doctor:
629 |         healthy, report = run_doctor(config)
630 |         print("\n".join(notes + report))
631 |         return 0 if healthy else 1
632 |     try:
633 |         backend = make_backend(config)
634 |     except ValueError as exc:                      # e.g. missing key
635 |         print(f"setup problem: {exc}")
636 |         return 2
637 |     approver = Approver()
638 |     approve: Callable[[str], bool] = approver
639 |     if config.auto_approve:
640 |         approve = lambda _a: True                  # noqa: E731 (a one-line answer)
641 |     elif args.prompt is not None:
642 |         approve = refuse
643 |     meter = Meter()
644 |     toolbox = ToolBox(config, approve, settings)
645 |     approver.permissions = toolbox.permissions
646 |     try:
647 |         if args.prompt is not None:
648 |             return run_once(config, backend, toolbox, meter, args.prompt)
649 |         return Chat(config, args, backend, toolbox, meter).loop(notes)
650 |     finally:
651 |         toolbox.close()                            # stops the sandbox and MCP servers
652 |         stop_local_servers()                       # and a local model this run started
```
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
