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
 29 | from .localserver import (NO_LOCAL_MODEL, LocalModel, can_start, describe_local, find_model_file,
 30 |                           ollama_up, server_up, stop_local_servers)
 31 | from .meter import Meter
 32 | from .runners import sandbox_image_ready
 33 | from .sandbox import SandboxError, looks_binary, resolve_inside, run_command, shell_argv
 34 | from .session import SessionLog, latest_session, list_sessions, load_session, session_title
 35 | from .settings import (PROJECT_SETTINGS, USER_SETTINGS, Permissions, apply_env, expand_command,
 36 |                        load_commands, load_settings, suggest_rule)
 37 | from .tools import Approval, ToolBox, read_text, write_text
 38 | 
 39 | HELP = """Commands:
 40 |   /plan <request>    look first, show a plan, then carry it out if you agree
 41 |   /model [mode]      show the model, or switch: online (Gemini), offline (local model), auto
 42 |   /init              write AGENT.md (project notes the agent reads every session)
 43 |   /compact           summarise the conversation to save tokens
 44 |   /undo              revert the file changes of the last request that changed files
 45 |   /clear             forget the conversation (files stay); /reset does the same
 46 |   /resume [number]   list this project's earlier conversations, or continue one
 47 |   /stats             calls, tokens, time and today's quota use
 48 |   /permissions       the permission rules in force
 49 |   /doctor            check the setup
 50 |   /help   /quit      (your own commands: .localagent/commands/<name>.md -> /<name>)
 51 | Shortcuts: !command runs a command yourself (no model call; its output goes with your next
 52 | request), # note saves a note in AGENT.md, @file in a request attaches that file.
 53 | Several lines: end a line with \\, or put \"\"\" alone on a line to start and end a block.
 54 | Ctrl+C stops the current task; the conversation is kept."""
 55 | INIT_PROMPT = (
 56 |     "Create AGENT.md in the workspace root: notes for an AI coding agent working on this project. "
 57 |     "First look at the project (outline, and read the README and main config files if present). "
 58 |     "Write at most 60 lines with these sections: Purpose; How to run; How to test; Conventions "
 59 |     "(style, structure); Key files (one line each). If AGENT.md exists, update it instead. "
 60 |     "Only write AGENT.md: do not run, fix or change anything else, even if you notice problems "
 61 |     "(mention them in AGENT.md under 'Known issues').")
 62 | INIT_TOOLS = frozenset({"write_file"})           # /init may read anything but only write files
 63 | PLAN_FOLLOW_UP = "Implement the plan above step by step, verify the result, then finish."
 64 | BUILT_IN = {"/quit", "/exit", "/help", "/reset", "/clear", "/undo", "/stats", "/compact", "/init",
 65 |             "/plan", "/model", "/online", "/offline", "/auto", "/resume", "/permissions", "/doctor"}
 66 | MODE_TEXT = {"auto": "Gemini, and the local model when Gemini cannot be reached",
 67 |              "online": "Gemini only", "offline": "the local model only"}
 68 | NOTHING_HELP = ("No model is available yet. Set one up (or both):\n"
 69 |                 f"  online:  {GEMINI_KEY_HELP}\n  offline: {NO_LOCAL_MODEL}\n"
 70 |                 "Then run  localagent --doctor  to check.")
 71 | MENTION = re.compile(r"(?<!\S)@([^\s@]+)")       # "@src/app.py", not an e-mail address
 72 | COLORS = {"+": "32", "-": "31", "@": "36"}       # diff lines: green, red, cyan
 73 | FAILURE_WORDS = ("REJECTED", "ERROR", "DENIED", "NOT RUN", "NOT FINISHED")
 74 | 
 75 | 
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
  `/model`. `NOTHING_HELP` is shown when no model at all is available: both ways to set one up.
- `MENTION` finds `@path` words — only at the start or after a space, so `me@example.com` is not one. `COLORS` and
  `FAILURE_WORDS` decide which progress lines are coloured.

## Asking before anything runs
```python
 76 | class Approver:
 77 |     """Asks before anything runs. Answering "a" (always) saves a permission rule, like
 78 |     Claude Code's "Yes, and don't ask again"."""
 79 | 
```
```python
 80 |     def __init__(self, read: Callable[[str], str] = input) -> None:
 81 |         self._read = read
 82 |         self.permissions: Permissions | None = None       # set once the toolbox exists
 83 | 
```
```python
 84 |     def __call__(self, action: str) -> bool:
 85 |         try:
 86 |             answer = self._read(f"  allow: {action}? [y]es / [n]o / [a]lways: ").strip().lower()
 87 |         except EOFError:                                  # no keyboard (input ran out): no
 88 |             return False
 89 |         if answer in {"a", "always"} and isinstance(action, Approval) and self.permissions:
 90 |             rule = suggest_rule(action.tool, action.subject)
 91 |             saved = self.permissions.always_allow(rule)
 92 |             print(f"  allowed from now on: {rule}" + (f" (saved in {saved})" if saved else ""))
 93 |         return answer in {"y", "yes", "a", "always"}
 94 | 
 95 | 
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
 96 | def refuse(action: str) -> bool:
 97 |     """The approver of -p runs: nobody is there to ask, so anything not allowed is refused."""
 98 |     print(f"[refused (no chat to ask in): {action} - allow it in .localagent/settings.json "
 99 |           "or use --auto-approve]", file=sys.stderr)
100 |     return False
101 | 
102 | 
```
- The approver of `-p` runs: there is no chat to ask in, so an action that no rule allows is refused (and said so on the
  error stream). Allow it with a rule, or pass `--auto-approve`. Claude Code's `-p` behaves the same way.

```python
103 | def confirm(question: str, read: Callable[[str], str] = input) -> bool:
104 |     """A yes/no question; no input (EOF) or Ctrl+C counts as no."""
105 |     try:
106 |         return read(question).strip().lower() in {"y", "yes"}
107 |     except (EOFError, KeyboardInterrupt):
108 |         print()
109 |         return False
110 | 
111 | 
```
- A yes/no question (`/plan`'s "Carry out this plan?"); no input or Ctrl+C means no.

## The flags shared by the chat, the server and the evals
```python
112 | def add_model_flags(parser: argparse.ArgumentParser) -> None:
113 |     """The flags every entry point shares (chat, server, evals): which model, where code runs."""
114 |     parser.add_argument("--mode", choices=MODES,
115 |                         help="auto: Gemini, the local model when offline (default); "
116 |                              "online: Gemini only; offline: the local model only")
117 |     parser.add_argument("--online", dest="mode", action="store_const", const="online",
118 |                         help="same as --mode online")
119 |     parser.add_argument("--offline", dest="mode", action="store_const", const="offline",
120 |                         help="same as --mode offline")
121 |     parser.add_argument("--backend", choices=sorted(BACKENDS),
122 |                         help="model service type, chosen by hand (the mode is then ignored)")
123 |     parser.add_argument("--model", help="model name for ollama/openai, e.g. qwen3-8b")
124 |     parser.add_argument("--api-base", help="URL of an OpenAI-style server, e.g. http://host:8080/v1")
125 |     parser.add_argument("--sandbox", choices=["local", "docker"],
126 |                         help="where code runs: this machine or a Docker container")
127 | 
128 | 
```
- Adds the flags all three programs need: `--mode` (with the shortcuts `--online` and `--offline`, which simply set it),
  `--backend`, `--model`, `--api-base` and `--sandbox`. Defined once here, so the three cannot drift apart.

```python
129 | def model_flags(args: argparse.Namespace) -> dict:
130 |     """The values of those shared flags, as Config field names (None = not given)."""
131 |     return {"mode": args.mode, "backend": args.backend, "model": args.model,
132 |             "api_base": args.api_base, "sandbox": args.sandbox}
133 | 
134 | 
```
- Reads those values back as `Config` field names (`api-base` becomes `api_base`), ready for `overlay`.

## `build_parser`
```python
135 | def build_parser() -> argparse.ArgumentParser:
136 |     """Define the command-line flags."""
137 |     parser = argparse.ArgumentParser(prog="localagent", description="A verifying coding agent")
138 |     parser.add_argument("--version", action="version", version=f"localagent {__version__}")
139 |     parser.add_argument("-p", "--print", dest="prompt", metavar="REQUEST",
140 |                         help="answer one request, print the answer and exit ('-' reads it from input)")
141 |     add_model_flags(parser)
142 |     parser.add_argument("--workspace", type=Path, help="project folder (default: current folder)")
143 |     parser.add_argument("--num-ctx", type=int, help="history budget in tokens")
144 |     parser.add_argument("--doctor", action="store_true", help="check the setup and exit")
145 |     parser.add_argument("--resume", "-c", "--continue", dest="resume", action="store_true",
146 |                         help="continue the latest conversation")
147 |     parser.add_argument("--type-check", action="store_true", help="also verify with mypy")
148 |     parser.add_argument("--auto-approve", action="store_true", help="never ask before running")
149 |     parser.add_argument("--network", action="store_true",
150 |                         help="allow internet inside the Docker sandbox (e.g. pip install)")
151 |     parser.add_argument("--fallback", choices=["local", "ollama", "openai"],
152 |                         help="with --backend gemini: local model to use when Gemini fails")
153 |     parser.add_argument("--reasoning", help="reasoning effort for OpenAI-style servers, e.g. low")
154 |     parser.add_argument("--allow-shell", action="store_true",
155 |                         help="let the model run shell commands on this computer (asks each time)")
156 |     parser.add_argument("--no-auto", action="store_true",
157 |                         help="do not pick backend/sandbox/fallback automatically")
158 |     return parser
159 | 
160 | 
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
161 | def build_config(args: argparse.Namespace) -> Config:
162 |     """Environment defaults overlaid with the flags the user actually gave."""
163 |     config = overlay(Config.from_env(), **model_flags(args), workspace=args.workspace,
164 |                      num_ctx=args.num_ctx, fallback_backend=args.fallback,
165 |                      reasoning_effort=args.reasoning)
166 |     return replace(config, type_check=args.type_check, auto_approve=args.auto_approve,
167 |                    sandbox_network=args.network, allow_host_shell=args.allow_shell)
168 | 
169 | 
```
- Starts from `Config.from_env()` (defaults + environment variables), `overlay`s only the value flags actually given
  (the shared ones from `model_flags` plus the chat's own), then sets the on/off flags. Order of strength: flags >
  environment variables > settings-file `env` > defaults.

## Choosing the model setup
```python
170 | def local_kind(config: Config) -> str:
171 |     """The local model the modes would use: "openai" (a server answers at api_base), "ollama"
172 |     (Ollama answers), "local" (llama.cpp can be started here) or "" (none)."""
173 |     if server_up(config.api_base):
174 |         return "openai"
175 |     if ollama_up(config.host):
176 |         return "ollama"
177 |     return "local" if can_start(config) else ""
178 | 
179 | 
```
- What the local model would be right now: `openai` when a server answers at `api_base` (llama.cpp, LM Studio),
  `ollama` when Ollama answers, `local` when llama.cpp can be started here, `""` when there is nothing. The quick checks
  take a second at most.

```python
180 | def choose_mode(config: Config, mode: str, ctx_chosen: bool = False) -> tuple[Config, list[str]]:
181 |     """Backend and offline backup for ``mode``; the notes explain the choice. Raises ValueError,
182 |     saying what to do, when the mode cannot work on this computer."""
183 |     if mode not in MODES:
184 |         raise ValueError(f"unknown mode '{mode}': choose {', '.join(MODES)}")
185 |     keyed = bool(config.gemini_api_key)
186 |     if mode == "online" and not keyed:
187 |         raise ValueError("online mode needs a Gemini key. " + GEMINI_KEY_HELP)
188 |     if mode == "online" or (mode == "auto" and keyed):
189 |         backup = "" if mode == "online" else (config.fallback_backend or local_kind(config))
190 |         config = replace(config, mode=mode, backend="gemini", fallback_backend=backup)
191 |         notes = [f"mode {mode}: Gemini answers (GEMINI_API_KEY is set)"]
192 |         if mode == "auto":
193 |             notes.append("offline backup: " + (describe_local(config) if backup else
194 |                                                "none set up (see docs/01-setup-and-models.md)"))
195 |         return config, notes
196 |     if not local_kind(config):
197 |         raise ValueError(NO_LOCAL_MODEL if mode == "offline" else NOTHING_HELP)
198 |     context = config.num_ctx if ctx_chosen else config.local_ctx
199 |     config = replace(config, mode=mode, backend="local", fallback_backend="", num_ctx=context,
200 |                      local_ctx=context)
201 |     reason = "" if mode == "offline" else " (no GEMINI_API_KEY, so offline)"
202 |     return config, [f"mode {mode}: the local model answers{reason} - {describe_local(config)}"]
203 | 
204 | 
```
- Turns a mode into a model setup and explains it:
  - `online` needs a key (else a `ValueError` saying how to get one) → backend `gemini`, no local backup;
  - `auto` with a key → backend `gemini` with the local model as its last link (`fallback_backend`) when there is one,
    and a note naming it — or "none set up";
  - `offline`, or `auto` without a key → backend `local`, history budget = the local context (8K) unless you chose a
    size; with nothing local a `ValueError` says what to install (`NO_LOCAL_MODEL`, or `NOTHING_HELP` in `auto`).
- Used at start-up (`auto_configure`), by `/model`, and by the HTTP server and the evals, so all four agree.

```python
205 | def auto_configure(config: Config, args: argparse.Namespace) -> tuple[Config, list[str]]:
206 |     """Choose what the user left open from what is available; explain each choice."""
207 |     notes: list[str] = []
208 |     ctx_chosen = args.num_ctx is not None or "LOCALAGENT_NUM_CTX" in os.environ
209 |     if args.backend is None and "LOCALAGENT_BACKEND" not in os.environ:
210 |         config, notes = choose_mode(config, config.mode, ctx_chosen)
211 |     if (args.sandbox is None and "LOCALAGENT_SANDBOX" not in os.environ
212 |             and sandbox_image_ready(config.sandbox_image)):
213 |         config = replace(config, sandbox="docker")
214 |         notes.append("sandbox: docker (Docker and the sandbox image are ready)")
215 |     if (config.backend == "gemini" and not config.fallback_backend and args.fallback is None
216 |             and config.mode != "online" and server_up(config.api_base)):
217 |         config = replace(config, fallback_backend="openai")
218 |         notes.append(f"fallback: the local model server at {config.api_base}")
219 |     budgeted = gemini_history(config, chosen=ctx_chosen)
220 |     if budgeted.num_ctx != config.num_ctx:
221 |         config = budgeted
222 |         notes.append(f"history budget: {config.num_ctx} tokens (Gemini handles long conversations)")
223 |     return config, notes
224 | 
225 | 
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
226 | def model_label(config: Config) -> str:
227 |     """Short description of which model(s) will answer, for banners and scoreboards."""
228 |     local = find_model_file(config)
229 |     local_name = f"local:{local.stem}" if local else "local model"
230 |     if config.backend == "gemini":
231 |         chain = " > ".join(config.gemini_models + config.gemini_strong_models)
232 |         backup = "" if not config.fallback_backend else (
233 |             f" > {local_name}" if config.fallback_backend == "local"
234 |             else f" > {config.fallback_backend}:{config.model}")
235 |         return f"gemini:{chain}{backup}"
236 |     if config.backend == "local":
237 |         return local_name
238 |     return f"{config.backend}:{config.model}"
239 | 
240 | 
```
- A readable description of who answers, e.g.
  `gemini:gemini-3.5-flash-lite > gemini-3.1-flash-lite > gemini-3.5-flash > gemini-2.5-flash > local:Qwen3-8B-Q4_K_M`
  (the local model named after its file), or `local:Qwen3-8B-Q4_K_M` offline. Used by the banner, `/model` and the eval
  scoreboard.

## `read_request` — typing a request
```python
241 | def read_request(read: Callable[[str], str] = input) -> str:
242 |     """One request from the keyboard; supports ``\\`` line continuation and \"\"\" blocks."""
243 |     first = read("you> ").lstrip("﻿")        # piped input may start with a byte-order mark
244 |     if first.strip() == '"""':
245 |         lines: list[str] = []
246 |         while (line := read("... ")).strip() != '"""':
247 |             lines.append(line)
248 |         return "\n".join(lines).strip()
249 |     parts = [first]
250 |     while parts[-1].endswith("\\"):
251 |         parts[-1] = parts[-1][:-1]
252 |         parts.append(read("... "))
253 |     return "\n".join(parts).strip()
254 | 
255 | 
```
- Reads one line after `you> ` and removes an invisible byte-order mark from its start: text piped into a program by
  PowerShell can begin with one, which made a piped `/init` arrive as `﻿/init` and be treated as an ordinary
  request (found in the end-to-end test).
- A line ending in `\` continues on the next line (`... ` prompt). A line that is only
  `"""` starts a block that ends at the next `"""` line — handy for pasting code or long instructions.
  (`:=` reads a line and tests it in one step.) `read` is injectable for tests.

## Colours
```python
256 | def use_color() -> bool:
257 |     """True when the terminal shows colours (and NO_COLOR is not set); switches them on in
258 |     the Windows console, which needs to be told."""
259 |     if os.environ.get("NO_COLOR") or not sys.stdout.isatty():
260 |         return False
261 |     if sys.platform == "win32":
262 |         import ctypes
263 |         kernel32 = ctypes.windll.kernel32
264 |         handle = kernel32.GetStdHandle(-11)                # the screen
265 |         mode = ctypes.c_uint32()
266 |         if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
267 |             return False
268 |         kernel32.SetConsoleMode(handle, mode.value | 0x0004)   # ENABLE_VIRTUAL_TERMINAL_PROCESSING
269 |     return True
270 | 
271 | 
```
- Colours only when the output is a terminal (not a file or a pipe) and `NO_COLOR` is not set (the common convention
  for "no colours, please"). The Windows console shows colour codes only after being told to: `SetConsoleMode` with
  `ENABLE_VIRTUAL_TERMINAL_PROCESSING` (`0x0004`) switches that on.

```python
272 | def make_printer(color: bool) -> Callable[[str], None]:
273 |     """How progress lines are shown: diffs in green/red, tool calls in cyan, failures in red."""
274 |     def show(line: str) -> None:
275 |         if color:
276 |             code = ""
277 |             if line.startswith("     ") and line[5:6] in COLORS:
278 |                 code = COLORS[line[5]]
279 |             elif line.startswith("-> "):
280 |                 code = "36"
281 |             elif line.lstrip().startswith(FAILURE_WORDS):
282 |                 code = "31"
283 |             line = f"\033[{code}m{line}\033[0m" if code else line
284 |         print(line)
285 |     return show
286 | 
287 | 
```
- Returns the function that shows progress lines: in a diff, added lines green and removed lines red (`@@` headers
  cyan); tool calls (`-> ...`) cyan; results starting with `REJECTED`, `ERROR`, `DENIED`, `NOT RUN` or `NOT FINISHED`
  red. A colour is the code `\033[<n>m` before the text and `\033[0m` after it.

## `handle_request` — one request
```python
288 | def handle_request(agent: Agent, toolbox: ToolBox, meter: Meter, text: str,
289 |                    plan_only: bool = False, allowed: frozenset[str] | None = None,
290 |                    show: Callable[[str], None] = print) -> str:
291 |     """Run one request, show progress and the answer, then verify the whole workspace."""
292 |     streamed: list[str] = []
293 | 
294 |     def stream(token: str) -> None:
295 |         streamed.append(token)
296 |         print(token, end="", flush=True)
297 | 
298 |     started = time.time()
299 |     try:
300 |         answer = agent.run(text, on_event=show, on_token=stream, plan_only=plan_only,
301 |                            allowed=allowed)
302 |     except LLMError as exc:
303 |         agent.repair()
304 |         print(f"\nerror: {exc}")
305 |         return ""
306 |     except KeyboardInterrupt:                      # Ctrl+C: stop this task, keep the chat
307 |         agent.repair()
308 |         print("\n[stopped - the conversation is kept; type your next request]")
309 |         return ""
310 |     shown = "".join(streamed).strip()
311 |     if streamed:
312 |         print()                                    # end the streamed line
313 |     if answer.strip() and answer.strip() != shown:
314 |         print(answer)                              # e.g. a finish summary that was not streamed
315 |     if not plan_only:
316 |         problems = toolbox.check_workspace(since=started)      # only what changed just now
317 |         print("[check of changed files: all OK]" if not problems
318 |               else "[workspace check FAILED]\n" + "\n".join(problems))
319 |         outcome = toolbox.run_hook("after_request")      # e.g. your test suite
320 |         if outcome is not None:
321 |             print(f"[after_request hook: exit code {outcome[0]}]\n{outcome[1].strip()}")
322 |     who = getattr(agent.backend, "last_model", "")
323 |     print(f"[{meter.summary()}" + (f" | {who}" if who else "") + "]")
324 |     return answer
325 | 
326 | 
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
327 | def attach_files(text: str, root: Path, limit: int) -> tuple[str, list[str]]:
328 |     """``@path`` mentions of workspace files: their contents are added to the request, which
329 |     saves the model a read_file call. Unknown paths stay as typed; ``limit`` caps the size."""
330 |     attached: list[str] = []
331 |     parts: list[str] = []
332 |     total = 0
333 |     for match in MENTION.finditer(text):
334 |         name = match.group(1).rstrip(".,;:!?)\"'")
335 |         try:
336 |             path = resolve_inside(root, name)
337 |         except SandboxError:
338 |             continue
339 |         if name in attached or not path.is_file() or looks_binary(path):
340 |             continue
341 |         content = path.read_text(encoding="utf-8-sig", errors="replace")
342 |         if total + len(content) > limit:
343 |             continue                               # too big for the context: the model reads it
344 |         total += len(content)
345 |         attached.append(name)
346 |         parts.append(f"Contents of {name} (attached by the user):\n```\n{content}\n```")
347 |     return (text + "\n\n" + "\n\n".join(parts) if parts else text), attached
348 | 
349 | 
```
- Like Claude Code's `@` mentions: for each `@path` naming a text file inside the workspace, the file's contents are added
  to the request, which saves the model a `read_file` call. Measured live: "what does @calc.py compute?" took 1 call and
  2 004 tokens instead of 2 calls and 4 017 (after rule 1 told the model not to read attached files again).
- Trailing punctuation is ignored (`@app.py,`). Paths outside the workspace, missing files, binary files and repeats are
  left as typed. `limit` caps the total: the chat allows a third of the history budget, so attachments can never crowd
  out the conversation (a file that does not fit is left for the model to read in parts).

```python
350 | def remember(note: str, root: Path) -> Path:
351 |     """Add ``note`` to AGENT.md under "## Notes" (both are created when missing)."""
352 |     path = root / "AGENT.md"
353 |     text, style = read_text(path) if path.is_file() else ("# Project notes\n", ("\n", False))
354 |     if "## Notes" not in text:
355 |         text = text.rstrip("\n") + "\n\n## Notes\n"
356 |     start = text.index("## Notes")
357 |     end = text.find("\n## ", start + 1)                # the next section, if any
358 |     end = len(text) if end == -1 else end
359 |     text = text[:end].rstrip("\n") + f"\n- {note}\n" + text[end:]
360 |     write_text(path, text, style)
361 |     return path
362 | 
363 | 
```
- Like Claude Code's `#` memory: `# use pytest, not unittest` adds `- use pytest, not unittest` to `AGENT.md` under a
  `## Notes` heading — at the end of that section, before the next `## ` heading. A missing file or section is
  created. The file's line breaks are kept (`read_text`/`write_text`). No model call is used.

```python
364 | def sessions_folder(config: Config) -> Path:
365 |     """This project's conversation logs: <project>/.localagent/sessions (an absolute state_dir wins)."""
366 |     return config.workspace / config.state_dir / "sessions"
367 | 
368 | 
```
- Where this project's conversation logs live: `<workspace>\.localagent\sessions`. Joining a path with an absolute
  `state_dir` gives that absolute path (that is how `pathlib` joins), so a custom absolute location still works.

## `Chat` — the loop and its commands
```python
369 | class Chat:
370 |     """The interactive loop and its commands. Holds what a command may change: the model
371 |     setup (/model), the conversation (/clear, /resume), and output waiting to be sent (!)."""
372 | 
```
```python
373 |     def __init__(self, config: Config, args: argparse.Namespace, backend: ChatBackend,
374 |                  toolbox: ToolBox, meter: Meter) -> None:
375 |         self.config, self.backend, self.toolbox, self.meter = config, backend, toolbox, meter
376 |         self.ctx_chosen = args.num_ctx is not None or "LOCALAGENT_NUM_CTX" in os.environ
377 |         self.root = config.workspace.resolve()
378 |         self.log = SessionLog(sessions_folder(config))
379 |         self.agent = Agent(config, backend, toolbox, meter, self.log)
380 |         self.checkpoints = Checkpoints(config.workspace)
381 |         self.commands = load_commands(config.workspace)  # custom /commands (.localagent/commands)
382 |         self.show = make_printer(use_color())
383 |         self.pending: list[str] = []                    # output of !commands, sent next time
384 |         self.help = HELP + "".join(f"\n  /{name:<16} {(text.strip().splitlines() or [''])[0][:60]}"
385 |                                    for name, text in sorted(self.commands.items()))
386 |         if args.resume:
387 |             previous = latest_session(sessions_folder(config))
388 |             if previous is not None and previous != self.log.path:
389 |                 self.agent.restore(load_session(previous))
390 |                 print(f"resumed {previous.name}")
391 | 
```
- Holds everything a command may change: the config and backend (`/model`), the agent and its session log (`/clear`,
  `/resume`), the undo steps, custom commands (from `.localagent/commands`, listed under the help text with the first
  line of each file), the coloured printer, and `pending` — output of `!commands` waiting to go with the next request.
- `ctx_chosen` remembers whether you chose the history budget yourself (then `/model` keeps it).
- `--resume`/`-c` reloads the newest conversation of **this project** (repairing it if it ended mid-task, and starting
  after its last `/clear` or compaction — see `session.md`).

```python
392 |     def run(self, text: str, plan_only: bool = False, allowed: frozenset[str] | None = None) -> str:
393 |         """One request: earlier !command output and @file contents go with it; the files it
394 |         changes become one /undo step."""
395 |         label = (text.splitlines() or [""])[0][:60]          # how /undo names this step
396 |         if self.pending:
397 |             text = "\n\n".join(self.pending) + "\n\nMy request: " + text
398 |             self.pending = []
399 |         limit = int(self.config.num_ctx * 0.7) * 4 // 3      # attachments: a third of the budget
400 |         text, attached = attach_files(text, self.root, limit)
401 |         if attached:
402 |             print(f"[attached: {', '.join(attached)}]")
403 |         message = self.checkpoints.take(label)
404 |         if message:
405 |             print(f"[{message}]")
406 |         try:
407 |             return handle_request(self.agent, self.toolbox, self.meter, text, plan_only, allowed,
408 |                                   self.show)
409 |         finally:
410 |             self.checkpoints.commit()
411 | 
```
- One request: earlier `!` output goes first ("I ran ... Output: ... My request: ..."), `@file` contents are attached,
  and the files it changes become one `/undo` step labelled with the start of your request (`take` before, `commit`
  after — also when the request fails or is stopped).

```python
412 |     def model_status(self) -> list[str]:
413 |         """What /model shows: the mode, who answers, and what online and offline would use."""
414 |         key = "key set" if self.config.gemini_api_key else "no key - " + GEMINI_KEY_HELP
415 |         last = getattr(self.backend, "last_model", "")
416 |         return [f"mode: {self.config.mode} ({MODE_TEXT.get(self.config.mode, '')})",
417 |                 f"models now: {model_label(self.config)}" + (f"; last answer from {last}" if last else ""),
418 |                 f"online:  Gemini, {key}",
419 |                 f"offline: {describe_local(self.config)}",
420 |                 "switch with /model online, /model offline or /model auto"]
421 | 
```
- What `/model` shows: the mode and what it means, the model chain now and which model answered last, whether a Gemini
  key is set, what the local model is (running, or started when needed, or not set up), and how to switch.

```python
422 |     def switch(self, mode: str) -> None:
423 |         """/model <mode>: build the new model setup, start the local model if needed, swap it in.
424 |         The conversation and the files stay; only who answers changes."""
425 |         try:
426 |             config, notes = choose_mode(replace(self.config, fallback_backend=""), mode,
427 |                                         self.ctx_chosen)
428 |             config = gemini_history(config, chosen=self.ctx_chosen)
429 |             backend = make_backend(config)
430 |             if isinstance(backend, LocalModel):
431 |                 backend.ensure_ready()                  # start llama.cpp now, not mid-task
432 |         except (ValueError, LLMError) as exc:
433 |             print(f"cannot switch to {mode}: {exc}")
434 |             return
435 |         except KeyboardInterrupt:
436 |             print("\n[switch stopped; nothing changed]")
437 |             return
438 |         if mode == "online" and stop_local_servers():
439 |             notes.append("stopped the local model server (its memory is free again)")
440 |         self.config, self.backend = config, backend
441 |         self.agent.set_backend(backend, config)
442 |         for note in notes:
443 |             print(f"  {note}")
444 |         print(f"[now {mode}: {model_label(config)}]")
445 | 
```
- `/model <mode>`: builds the new setup (`choose_mode`, then the history budget) and the new backend. Going offline
  starts the local model **right away** (`ensure_ready`), so it loads now and not in the middle of your next task; this
  can take up to a minute and can be stopped with Ctrl+C (nothing changes then).
- Any problem (no key, no local model, the server would not start) is shown and **nothing changes**.
- Going online stops a local model server this program started (an 8B model holds about 5 GB of memory).
- Then the agent gets the new backend — the conversation and files stay — and the new setup is printed.

```python
446 |     def resume(self, choice: str) -> None:
447 |         """/resume: list this project's conversations (newest first), or continue number N."""
448 |         sessions = list_sessions(sessions_folder(self.config))[::-1][:10]
449 |         if not choice:
450 |             for number, path in enumerate(sessions, start=1):
451 |                 mark = "  (this conversation)" if path == self.log.path else ""
452 |                 print(f"  {number}. {session_title(path)}{mark}")
453 |             print("continue one with /resume <number>" if sessions else "no earlier conversations")
454 |             return
455 |         if not choice.isdigit() or not 1 <= int(choice) <= len(sessions):
456 |             print(f"choose a number from 1 to {len(sessions)} (see /resume)")
457 |             return
458 |         picked = sessions[int(choice) - 1]
459 |         if picked == self.log.path:
460 |             print("that is this conversation")
461 |             return
462 |         self.agent.reset()
463 |         self.agent.restore(load_session(picked))
464 |         print(f"continued {picked.name} ({len(self.agent.messages) - 1} messages)")
465 | 
```
- `/resume` lists this project's last 10 conversations, newest first, each with its date and first request; the
  current one is marked. `/resume 3` clears the current conversation (logged as a reset) and continues number 3 — its
  messages are added to the current log, so it carries on there.

```python
466 |     def shell(self, command: str) -> None:
467 |         """``!command``: run it yourself in the project folder (you typed it, so no question);
468 |         its output is shown, and sent along with your next request."""
469 |         code, output = run_command(shell_argv(command), self.root, 600, self.config.max_output_chars)
470 |         print(output.rstrip())
471 |         print(f"[exit code {code}; the output goes with your next request]")
472 |         self.pending.append(f"I ran `{command}` myself (exit code {code}). Output:\n{output.strip()}")
473 | 
```
- `!command`, like Claude Code's bash mode: runs the command yourself in the project folder (PowerShell on Windows), with
  a 10-minute limit, **without asking** (you typed it) and **without a model call**. The output is shown and kept in
  `pending`, so the model sees it with your next request ("!pytest", then "fix the failures"). Ctrl+C stops it.

```python
474 |     def command(self, text: str) -> bool:
475 |         """Act on one line; False means quit."""
476 |         command, _, rest = text.partition(" ")
477 |         rest = rest.strip()
478 |         name = command.lower()
479 |         if name in ("/quit", "/exit"):
480 |             return False
481 |         if name == "/help":
482 |             print(self.help)
483 |         elif name in ("/reset", "/clear"):
484 |             self.agent.reset()
485 |             print("conversation cleared (files are unchanged)")
486 |         elif name == "/undo":
487 |             print(self.checkpoints.undo())
488 |         elif name == "/stats":
489 |             print(self.meter.summary())
490 |             for line in getattr(self.backend, "status", lambda: [])():
491 |                 print(line)                        # daily quota use per Gemini model
492 |         elif name == "/compact":
493 |             try:
494 |                 print(self.agent.compact())
495 |             except (LLMError, KeyboardInterrupt) as exc:
496 |                 print(f"compaction stopped: {exc}")
497 |         elif name == "/init":
498 |             self.run(INIT_PROMPT, allowed=INIT_TOOLS)
499 |             notes_file = self.root / "AGENT.md"
500 |             print(f"[AGENT.md {'written' if notes_file.is_file() else 'was NOT written - try /init again'}]")
501 |             self.agent.refresh_system()            # the new notes take effect immediately
502 |         elif name == "/plan":
503 |             if not rest:
504 |                 print("usage: /plan <what you want done>")
505 |             elif self.run(rest, plan_only=True) and confirm("Carry out this plan? [y/N] "):
506 |                 self.run(PLAN_FOLLOW_UP)
507 |         elif name in ("/model", "/online", "/offline", "/auto"):
508 |             wanted = rest.lower() if name == "/model" else name[1:]
509 |             if wanted:
510 |                 self.switch(wanted)
511 |             else:
512 |                 print("\n".join(self.model_status()))
513 |         elif name == "/resume":
514 |             self.resume(rest)
515 |         elif name == "/permissions":
516 |             rules = self.toolbox.permissions
517 |             print("allow: " + (", ".join(rules.allow) or "(none)"))
518 |             print("deny:  " + (", ".join(rules.deny) or "(none)"))
519 |             print(f"rules live in {USER_SETTINGS} and {self.root / PROJECT_SETTINGS}")
520 |         elif name == "/doctor":
521 |             print("\n".join(run_doctor(self.config)[1]))
522 |         elif name.startswith("/") and name[1:] in self.commands:
523 |             self.run(expand_command(self.commands[name[1:]], rest))
524 |         elif name.startswith("/"):
525 |             print(f"unknown command {command} - type /help (nothing was sent to the model)")
526 |         elif text.startswith("!") and text[1:].strip():
527 |             self.shell(text[1:].strip())
528 |         elif text.startswith("#") and text[1:].strip() and "\n" not in text:
529 |             print(f"[noted in {remember(text[1:].strip(), self.root)}]")
530 |             self.agent.refresh_system()
531 |         else:
532 |             self.run(text)
533 |         return True
534 | 
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
535 |     def loop(self, notes: list[str]) -> int:
536 |         """Show the banner, then read and act on lines until /quit or end of input."""
537 |         print(f"localagent {__version__} | {self.config.mode} | {model_label(self.config)} | "
538 |               f"sandbox={self.config.sandbox} | folder={self.root}")
539 |         for note in notes + self.toolbox.notes:
540 |             print(f"  {note}")
541 |         print(self.help)
542 |         while True:
543 |             try:
544 |                 text = read_request()
545 |             except (EOFError, KeyboardInterrupt):
546 |                 print()
547 |                 return 0
548 |             if not text:
549 |                 continue
550 |             try:
551 |                 if not self.command(text):
552 |                     return 0
553 |             except KeyboardInterrupt:              # e.g. Ctrl+C during a !command
554 |                 print("\n[stopped]")
555 | 
556 | 
```
- The banner shows the version, the mode, the model chain, the sandbox and the folder, then the automatic-setup notes
  and the toolbox's notes (for example which MCP servers started), then `HELP`.
- Reads a request at a time; Ctrl+C or Ctrl+D at the prompt (`EOFError`) leaves the program. A Ctrl+C during a command
  (for example a long `!command`) stops that command and returns to the prompt.

## `run_once` — the `-p` mode
```python
557 | def run_once(config: Config, backend: ChatBackend, toolbox: ToolBox, meter: Meter,
558 |              prompt: str) -> int:
559 |     """``-p``: one request without the chat. The answer goes to standard output and progress
560 |     to standard error, so scripts can use the answer; anything that needs a question is refused."""
561 |     text = sys.stdin.read() if prompt == "-" else prompt
562 |     root = config.workspace.resolve()
563 |     text, _ = attach_files(text.strip(), root, int(config.num_ctx * 0.7) * 4 // 3)
564 |     agent = Agent(config, backend, toolbox, meter, SessionLog(sessions_folder(config)))
565 |     started = time.time()
566 |     try:
567 |         answer = agent.run(text, on_event=lambda line: print(line, file=sys.stderr))
568 |     except LLMError as exc:
569 |         print(f"error: {exc}", file=sys.stderr)
570 |         return 1
571 |     print(answer)
572 |     problems = toolbox.check_workspace(since=started)
573 |     if problems:
574 |         print("[workspace check FAILED]\n" + "\n".join(problems), file=sys.stderr)
575 |     print(f"[{meter.summary()}]", file=sys.stderr)
576 |     return 1 if problems else 0
577 | 
578 | 
```
- One request without the chat, for scripts and other programs: `localagent -p "list the TODO comments" > todos.txt`.
  The request may come from your input (`-p -`) and may attach `@files`.
- The **answer goes to standard output** and everything else (progress, notices, the usage line) to standard error,
  so a script receives just the answer. Actions that would need a question are refused (see `refuse`). The
  conversation is logged like any other, so `localagent -c` can continue it in the chat.
- Exit code 0 when answered and the changed files pass the check, 1 when the model failed or a changed file is broken.

## `main` — start-up
```python
579 | def main(argv: list[str] | None = None) -> int:
580 |     """Entry point: choose the setup, build the components, then chat (or answer once)."""
581 |     for stream in (sys.stdout, sys.stderr):        # a character the console cannot show must
582 |         if hasattr(stream, "reconfigure"):         # not crash the program (piped output on Windows)
583 |             stream.reconfigure(errors="replace")
584 |     args = build_parser().parse_args(argv)
585 |     workspace = args.workspace or Path(os.environ.get("LOCALAGENT_WORKSPACE", "."))
586 |     try:
587 |         settings = load_settings(workspace)        # its "env" applies before the config is read
588 |         apply_env(settings)
589 |         config = build_config(args)
590 |     except ValueError as exc:                      # e.g. broken settings.json, a typo in a variable
591 |         print(f"setup problem: {exc}")
592 |         return 2
593 |     notes: list[str] = []
594 |     try:
595 |         if not args.no_auto:
596 |             config, notes = auto_configure(config, args)
597 |     except ValueError as exc:                      # e.g. no model at all yet
598 |         if not args.doctor:
599 |             print(f"setup problem: {exc}")
600 |             return 2
601 |         notes = [f"FAIL  {exc}"]
602 |     if args.doctor:
603 |         healthy, report = run_doctor(config)
604 |         print("\n".join(notes + report))
605 |         return 0 if healthy else 1
606 |     try:
607 |         backend = make_backend(config)
608 |     except ValueError as exc:                      # e.g. missing key
609 |         print(f"setup problem: {exc}")
610 |         return 2
611 |     approver = Approver()
612 |     approve: Callable[[str], bool] = approver
613 |     if config.auto_approve:
614 |         approve = lambda _a: True                  # noqa: E731 (a one-line answer)
615 |     elif args.prompt is not None:
616 |         approve = refuse
617 |     meter = Meter()
618 |     toolbox = ToolBox(config, approve, settings)
619 |     approver.permissions = toolbox.permissions
620 |     try:
621 |         if args.prompt is not None:
622 |             return run_once(config, backend, toolbox, meter, args.prompt)
623 |         return Chat(config, args, backend, toolbox, meter).loop(notes)
624 |     finally:
625 |         toolbox.close()                            # stops the sandbox and MCP servers
626 |         stop_local_servers()                       # and a local model this run started
```
- First makes the output robust: with output piped or redirected on Windows, Python writes in the old Windows code
  page, where one emoji or arrow in a model's answer or a diff would crash the program with `UnicodeEncodeError`;
  `errors="replace"` shows such a character as `?` instead (found in review).
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
