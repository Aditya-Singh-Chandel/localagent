# `localagent/cli.py` — the terminal chat (what you type into)

**Role in the flow:** starts everything. It reads flags, applies the settings files' `env`, chooses the model setup for
the **mode** and fills in whatever else you did not choose from what this computer has (`auto_configure`), builds
`Config → backend → ToolBox → Meter → Agent`, then loops: read a request or command, note the folder for `/undo`, run the
agent (showing every tool call, edit diff and checklist), verify the changed files, print usage and who answered. It is
the counterpart of Claude Code's terminal interface.

```
localagent                       # in your project folder: automatic setup (mode auto)
localagent --offline             # only the local model (started for you when needed)
localagent --online              # only the online model (Gemini, or the service chosen in setup)
localagent --provider anthropic:claude-sonnet-5-5   # this run: another online service and model
localagent -p "what does this project do?"     # one answer, printed; for scripts
localagent -c                    # continue the latest conversation of this project
localagent --doctor              # check the setup that would be used
localagent --backend openai --api-base http://127.0.0.1:8080/v1 --no-auto   # fully manual
```

**The three modes.** `auto` (default): the online model answers while it is set up and the internet works — Gemini,
or the service you chose in `localagent setup` (OpenAI, Anthropic, OpenRouter, Groq, Mistral, DeepSeek, any
OpenAI-compatible address). When it cannot be reached (or a quota is used up) the local model answers instead, by
itself and at once — and the online model takes over again when it is back. With nothing online set up, `auto` uses
the local model. `online`: only the online model (nothing local is started or used). `offline`: only the local model,
started for you if needed. Switch at any time in the chat with `/model online`, `/model offline`, `/model auto` (or
`/online`, `/offline`, `/auto`); the conversation and your files stay.

**The first start.** With nothing set up at all and a person at the keyboard, `localagent` offers the guided setup
at once instead of printing an error (`guided_first_run`).

## Module header
```python
  1 | """Command-line interface: the chat prompt around the Agent (like Claude Code's terminal UI).
  2 | 
  3 | Start it inside a project folder:  localagent        (or: python -m localagent)
  4 | One request, answer printed:       localagent -p "what does this project do?"
  5 | The model follows the mode: "auto" (the default) answers with the online model (Gemini, or the
  6 | service chosen in `localagent setup`) and with the local model when that cannot be reached;
  7 | "online" uses only the online model; "offline" only the local one. Choose with --online /
  8 | --offline, or switch any time in the chat with /model.
  9 | Everything else is chosen from what this computer has (the Docker sandbox when it is ready);
 10 | --backend and --no-auto still let you set everything by hand.
 11 | """
 12 | from __future__ import annotations
 13 | 
 14 | import argparse
 15 | import os
 16 | import re
 17 | import sys
 18 | import time
 19 | from dataclasses import replace
 20 | from pathlib import Path
 21 | from typing import Callable
 22 | 
 23 | from . import __version__
 24 | from .agent import Agent
 25 | from .backends import BACKENDS, make_backend
 26 | from .checkpoint import Checkpoints
 27 | from .config import MODES, Config, gemini_history, online_problem, overlay, with_provider
 28 | from .doctor import run_doctor
 29 | from .llm import ChatBackend, LLMError
 30 | from .localserver import (NO_LOCAL_MODEL, SETUP_HINT, LocalModel, can_start, describe_local,
 31 |                           find_model_file, ollama_up, server_up, stop_local_servers)
 32 | from .meter import Meter
 33 | from .models import run_models
 34 | from .providers import PROVIDERS, find_provider
 35 | from .runners import sandbox_image_ready
 36 | from .sandbox import SandboxError, looks_binary, resolve_inside, run_command, shell_argv
 37 | from .session import SessionLog, latest_session, list_sessions, load_session, session_title
 38 | from .settings import (PROJECT_SETTINGS, USER_SETTINGS, Permissions, apply_env, expand_command,
 39 |                        load_commands, load_settings, suggest_rule)
 40 | from .tools import Approval, ToolBox, read_text, write_text
 41 | from .wizard import run_setup
 42 | 
 43 | HELP = """Commands:
 44 |   /plan <request>    look first, show a plan, then carry it out if you agree
 45 |   /model [mode]      show the model, or switch: online (Gemini), offline (local model), auto
 46 |   /init              write AGENT.md (project notes the agent reads every session)
 47 |   /compact           summarise the conversation to save tokens
 48 |   /undo              revert the file changes of the last request that changed files
 49 |   /clear             forget the conversation (files stay); /reset does the same
 50 |   /resume [number]   list this project's earlier conversations, or continue one
 51 |   /stats             calls, tokens, time and today's quota use
 52 |   /permissions       the permission rules in force
 53 |   /doctor            check the setup
 54 |   /help   /quit      (your own commands: .localagent/commands/<name>.md -> /<name>)
 55 | Shortcuts: !command runs a command yourself (no model call; its output goes with your next
 56 | request), # note saves a note in AGENT.md, @file in a request attaches that file.
 57 | Several lines: end a line with \\, or put \"\"\" alone on a line to start and end a block.
 58 | Ctrl+C stops the current task; the conversation is kept."""
 59 | INIT_PROMPT = (
 60 |     "Create AGENT.md in the workspace root: notes for an AI coding agent working on this project. "
 61 |     "First look at the project (outline, and read the README and main config files if present). "
 62 |     "Write at most 60 lines with these sections: Purpose; How to run; How to test; Conventions "
 63 |     "(style, structure); Key files (one line each). If AGENT.md exists, update it instead. "
 64 |     "Only write AGENT.md: do not run, fix or change anything else, even if you notice problems "
 65 |     "(mention them in AGENT.md under 'Known issues').")
 66 | INIT_TOOLS = frozenset({"write_file"})           # /init may read anything but only write files
 67 | PLAN_FOLLOW_UP = "Implement the plan above step by step, verify the result, then finish."
 68 | BUILT_IN = {"/quit", "/exit", "/help", "/reset", "/clear", "/undo", "/stats", "/compact", "/init",
 69 |             "/plan", "/model", "/online", "/offline", "/auto", "/resume", "/permissions", "/doctor"}
 70 | MODE_TEXT = {"auto": "the online model, and the local model when it cannot be reached",
 71 |              "online": "the online model only", "offline": "the local model only"}
 72 | NOTHING_HELP = ("No model is available yet. The guided setup takes a minute:  localagent setup\n"
 73 |                 "It offers a free Gemini key; your own key for OpenAI, Anthropic, OpenRouter, Groq, "
 74 |                 "Mistral or DeepSeek;\na model server you already run (Ollama, LM Studio, llama.cpp); "
 75 |                 "or a free model it downloads and runs for you.\n"
 76 |                 "Then run  localagent --doctor  to check.")
 77 | SUBCOMMANDS = ("setup", "models")                # product commands, handled before the flags
 78 | COMMANDS_EPILOG = ("commands:\n  localagent setup            guided first run: online service and key, "
 79 |                    "offline model, Docker\n  localagent models           local models: list, download NAME, "
 80 |                    "use NAME-or-FILE")
 81 | MENTION = re.compile(r"(?<!\S)@([^\s@]+)")       # "@src/app.py", not an e-mail address
 82 | COLORS = {"+": "32", "-": "31", "@": "36"}       # diff lines: green, red, cyan
 83 | FAILURE_WORDS = ("REJECTED", "ERROR", "DENIED", "NOT RUN", "NOT FINISHED")
 84 | 
 85 | 
```
- The docstring explains how to start it and what the modes do.
- Imports: `argparse` (flags), `os` (environment variables), `re` (`@file` mentions), `sys` (the output streams),
  `time` (when a request started), `replace`, `Path`, `Callable`, `__version__`, and the modules it wires together —
  agent, backends, checkpoints, config (with `MODES`, `overlay`, `gemini_history`, `online_problem` and
  `with_provider`), doctor, `LLMError`, the local-model functions, meter, `run_models`, the provider table,
  `sandbox_image_ready`, the sandbox helpers it uses for `!` and `@`, sessions, settings/permissions, the toolbox with
  its `Approval` questions and text helpers, and `run_setup` (the guided setup, `wizard.py`).
- `HELP` lists the commands (shown at start and by `/help`), the `!`, `#` and `@` shortcuts, how to type multi-line
  requests, and that Ctrl+C stops a task without leaving.
- `INIT_PROMPT` is what `/init` asks the model: look at the project and write `AGENT.md` (purpose, how to run, how to
  test, conventions, key files; at most 60 lines) — Claude Code's `/init` and `CLAUDE.md`. It also says to write only
  `AGENT.md` and to list noticed problems under "Known issues" instead of fixing them; `INIT_TOOLS` enforces it —
  during `/init` the model can read anything but its only writing tool is `write_file`.
- `PLAN_FOLLOW_UP` is sent after you accept a plan from `/plan`; it starts with "Implement", so the agent's "you changed
  nothing" safeguards apply to it.
- `BUILT_IN` lists the built-in commands (custom commands cannot replace them). `MODE_TEXT` describes each mode for
  `/model`. `NOTHING_HELP` is shown when no model at all is available: `localagent setup`, and everything it offers.
  `SUBCOMMANDS` are the product commands handled before the flags; `COMMANDS_EPILOG` lists them at the end of
  `--help`.
- `MENTION` finds `@path` words — only at the start or after a space, so `me@example.com` is not one. `COLORS` and
  `FAILURE_WORDS` decide which progress lines are coloured.

## Asking before anything runs
```python
 86 | class Approver:
 87 |     """Asks before anything runs. Answering "a" (always) saves a permission rule, like
 88 |     Claude Code's "Yes, and don't ask again"."""
 89 | 
```
```python
 90 |     def __init__(self, read: Callable[[str], str] = input) -> None:
 91 |         self._read = read
 92 |         self.permissions: Permissions | None = None       # set once the toolbox exists
 93 | 
```
```python
 94 |     def __call__(self, action: str) -> bool:
 95 |         try:
 96 |             answer = self._read(f"  allow: {action}? [y]es / [n]o / [a]lways: ").strip().lower()
 97 |         except EOFError:                                  # no keyboard (input ran out): no
 98 |             return False
 99 |         if answer in {"a", "always"} and isinstance(action, Approval) and self.permissions:
100 |             rule = suggest_rule(action.tool, action.subject)
101 |             saved = self.permissions.always_allow(rule)
102 |             print(f"  allowed from now on: {rule}" + (f" (saved in {saved})" if saved else ""))
103 |         return answer in {"y", "yes", "a", "always"}
104 | 
105 | 
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
106 | def refuse(action: str) -> bool:
107 |     """The approver of -p runs: nobody is there to ask, so anything not allowed is refused."""
108 |     print(f"[refused (no chat to ask in): {action} - allow it in .localagent/settings.json "
109 |           "or use --auto-approve]", file=sys.stderr)
110 |     return False
111 | 
112 | 
```
- The approver of `-p` runs: there is no chat to ask in, so an action that no rule allows is refused (and said so on the
  error stream). Allow it with a rule, or pass `--auto-approve`. Claude Code's `-p` behaves the same way.

```python
113 | def confirm(question: str, read: Callable[[str], str] = input) -> bool:
114 |     """A yes/no question; no input (EOF) or Ctrl+C counts as no."""
115 |     try:
116 |         return read(question).strip().lower() in {"y", "yes"}
117 |     except (EOFError, KeyboardInterrupt):
118 |         print()
119 |         return False
120 | 
121 | 
```
- A yes/no question (`/plan`'s "Carry out this plan?"); no input or Ctrl+C means no.

## The flags shared by the chat, the server and the evals
```python
122 | def add_model_flags(parser: argparse.ArgumentParser) -> None:
123 |     """The flags every entry point shares (chat, server, evals): which model, where code runs."""
124 |     parser.add_argument("--mode", choices=MODES,
125 |                         help="auto: Gemini, the local model when offline (default); "
126 |                              "online: Gemini only; offline: the local model only")
127 |     parser.add_argument("--online", dest="mode", action="store_const", const="online",
128 |                         help="same as --mode online")
129 |     parser.add_argument("--offline", dest="mode", action="store_const", const="offline",
130 |                         help="same as --mode offline")
131 |     parser.add_argument("--backend", choices=sorted(BACKENDS),
132 |                         help="model service type, chosen by hand (the mode is then ignored)")
133 |     parser.add_argument("--model", help="model name for ollama/openai, e.g. qwen3-8b")
134 |     parser.add_argument("--api-base", help="URL of an OpenAI-style server, e.g. http://host:8080/v1")
135 |     parser.add_argument("--sandbox", choices=["local", "docker"],
136 |                         help="where code runs: this machine or a Docker container")
137 | 
138 | 
```
- Adds the flags all three programs need: `--mode` (with the shortcuts `--online` and `--offline`, which simply set it),
  `--backend`, `--model`, `--api-base` and `--sandbox`. Defined once here, so the three cannot drift apart.

```python
139 | def model_flags(args: argparse.Namespace) -> dict:
140 |     """The values of those shared flags, as Config field names (None = not given)."""
141 |     return {"mode": args.mode, "backend": args.backend, "model": args.model,
142 |             "api_base": args.api_base, "sandbox": args.sandbox}
143 | 
144 | 
```
- Reads those values back as `Config` field names (`api-base` becomes `api_base`), ready for `overlay`.

## `build_parser`
```python
145 | def build_parser() -> argparse.ArgumentParser:
146 |     """Define the command-line flags."""
147 |     parser = argparse.ArgumentParser(prog="localagent", description="A verifying coding agent",
148 |                                      epilog=COMMANDS_EPILOG,
149 |                                      formatter_class=argparse.RawDescriptionHelpFormatter)
150 |     parser.add_argument("--version", action="version", version=f"localagent {__version__}")
151 |     parser.add_argument("--provider", metavar="NAME[:MODEL]",
152 |                         help="the online service: " + ", ".join(p.name for p in PROVIDERS)
153 |                              + " - with a model if you like, e.g. openai:gpt-5-mini")
154 |     parser.add_argument("-p", "--print", dest="prompt", metavar="REQUEST",
155 |                         help="answer one request, print the answer and exit ('-' reads it from input)")
156 |     add_model_flags(parser)
157 |     parser.add_argument("--workspace", type=Path, help="project folder (default: current folder)")
158 |     parser.add_argument("--num-ctx", type=int, help="history budget in tokens")
159 |     parser.add_argument("--doctor", action="store_true", help="check the setup and exit")
160 |     parser.add_argument("--resume", "-c", "--continue", dest="resume", action="store_true",
161 |                         help="continue the latest conversation")
162 |     parser.add_argument("--type-check", action="store_true", help="also verify with mypy")
163 |     parser.add_argument("--auto-approve", action="store_true", help="never ask before running")
164 |     parser.add_argument("--network", action="store_true",
165 |                         help="allow internet inside the Docker sandbox (e.g. pip install)")
166 |     parser.add_argument("--fallback", choices=["local", "ollama", "openai"],
167 |                         help="with --backend gemini: local model to use when Gemini fails")
168 |     parser.add_argument("--reasoning", help="reasoning effort for OpenAI-style servers, e.g. low")
169 |     parser.add_argument("--allow-shell", action="store_true",
170 |                         help="let the model run shell commands on this computer (asks each time)")
171 |     parser.add_argument("--no-auto", action="store_true",
172 |                         help="do not pick backend/sandbox/fallback automatically")
173 |     return parser
174 | 
175 | 
```
- `--version` prints `localagent 0.6.0` and exits. `--provider NAME[:MODEL]` picks the online service for this run
  (its help lists the names from the table). `-p`/`--print REQUEST` answers one request (`-` reads it from your input,
  e.g. `type question.txt | localagent -p -`).
- The shared flags (`add_model_flags`), then the chat's own. Value flags default to `None`, meaning "not given" (so
  automatic setup and environment variables can fill them): `--workspace` (default: the current folder), `--num-ctx`,
  `--fallback` (`local`, `ollama` or `openai`), `--reasoning`.
- On/off flags: `--doctor`, `--resume` (also `-c` and `--continue`, Claude Code's names), `--type-check`,
  `--auto-approve`, `--network` (internet inside the sandbox), `--allow-shell` (shell on this computer, asks every time)
  and `--no-auto` (no automatic choices).

## `build_config`
```python
176 | def build_config(args: argparse.Namespace) -> Config:
177 |     """Environment defaults overlaid with the flags the user actually gave."""
178 |     config = overlay(Config.from_env(), **model_flags(args), workspace=args.workspace,
179 |                      num_ctx=args.num_ctx, fallback_backend=args.fallback,
180 |                      reasoning_effort=args.reasoning)
181 |     config = replace(config, type_check=args.type_check, auto_approve=args.auto_approve,
182 |                      sandbox_network=args.network, allow_host_shell=args.allow_shell)
183 |     if args.provider:                              # --provider NAME[:MODEL]
184 |         name, _, model = args.provider.partition(":")
185 |         kept = config.provider_model if name.strip().lower() == config.provider else ""
186 |         config = with_provider(config, name, model or kept)
187 |     return config
188 | 
189 | 
```
- Starts from `Config.from_env()` (defaults + environment variables), `overlay`s only the value flags actually given
  (the shared ones from `model_flags` plus the chat's own), then sets the on/off flags. Order of strength: flags >
  environment variables > settings-file `env` > defaults.
- `--provider` goes through `with_provider` (`config.md`), exactly like `LOCALAGENT_PROVIDER`: the part before the
  first `:` names the service, the rest is the model (`openrouter:qwen/qwen3-coder:free` keeps its own colon). Naming
  the service that is already chosen without a model keeps the saved model; another service starts without one.

## Choosing the model setup
```python
190 | def local_kind(config: Config) -> str:
191 |     """The local model the modes would use: "openai" (a server answers at api_base), "ollama"
192 |     (Ollama answers), "local" (llama.cpp can be started here) or "" (none). A kind chosen in
193 |     `localagent setup` (``local_server``) is trusted; it is checked when it is used."""
194 |     if config.local_server:
195 |         return config.local_server
196 |     if server_up(config.api_base):
197 |         return "openai"
198 |     if ollama_up(config.host):
199 |         return "ollama"
200 |     return "local" if can_start(config) else ""
201 | 
202 | 
```
- What the local model would be right now: `openai` when a server answers at `api_base` (llama.cpp, LM Studio),
  `ollama` when Ollama answers, `local` when llama.cpp can be started here, `""` when there is nothing. The quick checks
  take a second at most.
- A kind you chose in `localagent setup` (`local_server`) is returned as it is, without a check: it is checked when it
  is used, and then the error names that server ("not running at ...") instead of a vague "no local model".

```python
203 | def choose_mode(config: Config, mode: str, ctx_chosen: bool = False) -> tuple[Config, list[str]]:
204 |     """Backend and offline backup for ``mode``; the notes explain the choice. Raises ValueError,
205 |     saying what to do, when the mode cannot work on this computer."""
206 |     if mode not in MODES:
207 |         raise ValueError(f"unknown mode '{mode}': choose {', '.join(MODES)}")
208 |     provider = find_provider(config.provider) or PROVIDERS[0]
209 |     problem = online_problem(config)
210 |     if mode == "online" and problem:
211 |         raise ValueError(f"online mode needs a working online model: {problem}")
212 |     if mode == "online" or (mode == "auto" and not problem):
213 |         backup = "" if mode == "online" else (config.fallback_backend or local_kind(config))
214 |         backend = "gemini" if provider.name == "gemini" else "hosted"
215 |         config = replace(config, mode=mode, backend=backend, fallback_backend=backup)
216 |         keyed = config.gemini_api_key if backend == "gemini" else config.provider_key
217 |         notes = [f"mode {mode}: {online_name(config)} answers"
218 |                  + (f" ({provider.key_env} is set)" if keyed else "")]
219 |         if mode == "auto":
220 |             notes.append("offline backup: " + (describe_local(config) if backup else
221 |                                                f"none set up ({SETUP_HINT})"))
222 |         return config, notes
223 |     if not local_kind(config):
224 |         raise ValueError(NO_LOCAL_MODEL if mode == "offline" else NOTHING_HELP)
225 |     context = config.num_ctx if ctx_chosen else config.local_ctx
226 |     config = replace(config, mode=mode, backend="local", fallback_backend="", num_ctx=context,
227 |                      local_ctx=context)
228 |     reason = "" if mode == "offline" else " (no online model is set up, so offline)"
229 |     return config, [f"mode {mode}: the local model answers{reason} - {describe_local(config)}"]
230 | 
231 | 
```
- Turns a mode into a model setup and explains it. "The online model works" means `online_problem` (`config.md`) has
  nothing to say; its backend is `gemini` for Gemini and `hosted` for any other service:
  - `online` needs a working online model (else a `ValueError` with the fix) → no local backup;
  - `auto` with a working online model → it, with the local model as its last link (`fallback_backend`) when there is
    one, and a note naming it — or "none set up". The first note says who answers, e.g. `OpenAI gpt-5-mini answers
    (OPENAI_API_KEY is set)`;
  - `offline`, or `auto` with nothing online → backend `local`, history budget = the local context (8K) unless you
    chose a size; with nothing local a `ValueError` says what to install (`NO_LOCAL_MODEL`, or `NOTHING_HELP` in
    `auto`).
- Used at start-up (`auto_configure`), by `/model`, and by the HTTP server and the evals, so all four agree.

```python
232 | def auto_configure(config: Config, args: argparse.Namespace) -> tuple[Config, list[str]]:
233 |     """Choose what the user left open from what is available; explain each choice."""
234 |     notes: list[str] = []
235 |     ctx_chosen = args.num_ctx is not None or "LOCALAGENT_NUM_CTX" in os.environ
236 |     if args.backend is None and "LOCALAGENT_BACKEND" not in os.environ:
237 |         config, notes = choose_mode(config, config.mode, ctx_chosen)
238 |     if (args.sandbox is None and "LOCALAGENT_SANDBOX" not in os.environ
239 |             and sandbox_image_ready(config.sandbox_image)):
240 |         config = replace(config, sandbox="docker")
241 |         notes.append("sandbox: docker (Docker and the sandbox image are ready)")
242 |     if (config.backend in ("gemini", "hosted") and not config.fallback_backend
243 |             and args.fallback is None and config.mode != "online" and server_up(config.api_base)):
244 |         config = replace(config, fallback_backend="openai")
245 |         notes.append(f"fallback: the local model server at {config.api_base}")
246 |     budgeted = gemini_history(config, chosen=ctx_chosen)
247 |     if budgeted.num_ctx != config.num_ctx:
248 |         config = budgeted
249 |         notes.append(f"history budget: {config.num_ctx} tokens (online models handle long "
250 |                      "conversations)")
251 |     return config, notes
252 | 
253 | 
```
- Only fills choices you left open (no flag and no environment variable), and returns a note for each choice so the
  banner can explain it:
  - **models:** `choose_mode` for the configured mode — unless you chose `--backend` (or `LOCALAGENT_BACKEND`) by hand;
  - **sandbox:** `docker` when Docker runs and the sandbox image exists (one quick `docker image inspect`);
  - **fallback:** with a hand-chosen online backend (`--backend gemini` or `hosted`, not online mode), the local server
    when one answers;
  - **history budget:** 32 768 tokens with an online model (unless you set `--num-ctx`). The 8 192 default fits the
    small local model; with it, Gemini sessions forgot old tool output and summarised far earlier than necessary. At
    32K a call carries at most ~23K tokens of history — still well inside Gemini's free 250 000 tokens per minute.
- When the mode cannot work (no key and nothing local), `choose_mode`'s `ValueError` reaches `main`, which prints it as
  a setup problem — or, with `--doctor`, runs the health check anyway.

## `online_name` and `model_label`
```python
254 | def online_name(config: Config) -> str:
255 |     """The online model in a few words: "Gemini", "OpenAI gpt-5-mini", "m at https://..."."""
256 |     provider = find_provider(config.provider) or PROVIDERS[0]
257 |     if provider.name == "gemini":
258 |         return "Gemini"
259 |     if provider.name == "custom":
260 |         return f"{config.provider_model} at {config.provider_base}"
261 |     return f"{provider.title} {config.provider_model}"
262 | 
263 | 
```
- The online model in a few words for notes and `/model`: `Gemini`, `OpenAI gpt-5-mini`, or for `custom` the model and
  its address.

```python
264 | def model_label(config: Config) -> str:
265 |     """Short description of which model(s) will answer, for banners and scoreboards."""
266 |     local = find_model_file(config)
267 |     local_name = f"local:{local.stem}" if local else "local model"
268 |     if config.local_server == "ollama":
269 |         local_name = f"ollama:{config.model}"
270 |     elif config.local_server == "openai" and config.api_base != Config.api_base:
271 |         local_name = f"openai:{config.model}"          # your own server, not the downloaded model
272 |     backup = "" if not config.fallback_backend else (
273 |         f" > {local_name}" if config.fallback_backend in ("local", config.local_server)
274 |         else f" > {config.fallback_backend}:{config.model}")
275 |     if config.backend == "gemini":
276 |         return "gemini:" + " > ".join(config.gemini_models + config.gemini_strong_models) + backup
277 |     if config.backend == "hosted":
278 |         return f"{config.provider}:{config.provider_model}{backup}"
279 |     if config.backend == "local":
280 |         return local_name
281 |     return f"{config.backend}:{config.model}"
282 | 
283 | 
```
- A readable description of who answers, e.g.
  `gemini:gemini-3.5-flash-lite > gemini-3.1-flash-lite > gemini-3.5-flash > gemini-2.5-flash > local:Qwen3-8B-Q4_K_M`
  (the local model named after its file), `openai:gpt-5-mini > local:Qwen3-8B-Q4_K_M`, or `local:Qwen3-8B-Q4_K_M`
  offline. A local server you chose is named by its model instead of the downloaded file: `ollama:qwen3:8b`, or
  `openai:<model>` for a server at an address of your own. Used by the banner, `/model` and the eval scoreboard.

## `read_request` — typing a request
```python
284 | def read_request(read: Callable[[str], str] = input) -> str:
285 |     """One request from the keyboard; supports ``\\`` line continuation and \"\"\" blocks."""
286 |     first = read("you> ").lstrip("﻿")        # piped input may start with a byte-order mark
287 |     if first.strip() == '"""':
288 |         lines: list[str] = []
289 |         while (line := read("... ")).strip() != '"""':
290 |             lines.append(line)
291 |         return "\n".join(lines).strip()
292 |     parts = [first]
293 |     while parts[-1].endswith("\\"):
294 |         parts[-1] = parts[-1][:-1]
295 |         parts.append(read("... "))
296 |     return "\n".join(parts).strip()
297 | 
298 | 
```
- Reads one line after `you> ` and removes an invisible byte-order mark from its start: text piped into a program by
  PowerShell can begin with one, which made a piped `/init` arrive as `﻿/init` and be treated as an ordinary
  request (found in the end-to-end test).
- A line ending in `\` continues on the next line (`... ` prompt). A line that is only
  `"""` starts a block that ends at the next `"""` line — handy for pasting code or long instructions.
  (`:=` reads a line and tests it in one step.) `read` is injectable for tests.

## Colours
```python
299 | def use_color() -> bool:
300 |     """True when the terminal shows colours (and NO_COLOR is not set); switches them on in
301 |     the Windows console, which needs to be told."""
302 |     if os.environ.get("NO_COLOR") or not sys.stdout.isatty():
303 |         return False
304 |     if sys.platform == "win32":
305 |         import ctypes
306 |         kernel32 = ctypes.windll.kernel32
307 |         handle = kernel32.GetStdHandle(-11)                # the screen
308 |         mode = ctypes.c_uint32()
309 |         if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
310 |             return False
311 |         kernel32.SetConsoleMode(handle, mode.value | 0x0004)   # ENABLE_VIRTUAL_TERMINAL_PROCESSING
312 |     return True
313 | 
314 | 
```
- Colours only when the output is a terminal (not a file or a pipe) and `NO_COLOR` is not set (the common convention
  for "no colours, please"). The Windows console shows colour codes only after being told to: `SetConsoleMode` with
  `ENABLE_VIRTUAL_TERMINAL_PROCESSING` (`0x0004`) switches that on.

```python
315 | def make_printer(color: bool) -> Callable[[str], None]:
316 |     """How progress lines are shown: diffs in green/red, tool calls in cyan, failures in red."""
317 |     def show(line: str) -> None:
318 |         if color:
319 |             code = ""
320 |             if line.startswith("     ") and line[5:6] in COLORS:
321 |                 code = COLORS[line[5]]
322 |             elif line.startswith("-> "):
323 |                 code = "36"
324 |             elif line.lstrip().startswith(FAILURE_WORDS):
325 |                 code = "31"
326 |             line = f"\033[{code}m{line}\033[0m" if code else line
327 |         print(line)
328 |     return show
329 | 
330 | 
```
- Returns the function that shows progress lines: in a diff, added lines green and removed lines red (`@@` headers
  cyan); tool calls (`-> ...`) cyan; results starting with `REJECTED`, `ERROR`, `DENIED`, `NOT RUN` or `NOT FINISHED`
  red. A colour is the code `\033[<n>m` before the text and `\033[0m` after it.

## `handle_request` — one request
```python
331 | def handle_request(agent: Agent, toolbox: ToolBox, meter: Meter, text: str,
332 |                    plan_only: bool = False, allowed: frozenset[str] | None = None,
333 |                    show: Callable[[str], None] = print) -> str:
334 |     """Run one request, show progress and the answer, then verify the whole workspace."""
335 |     streamed: list[str] = []
336 | 
337 |     def stream(token: str) -> None:
338 |         streamed.append(token)
339 |         print(token, end="", flush=True)
340 | 
341 |     started = time.time()
342 |     try:
343 |         answer = agent.run(text, on_event=show, on_token=stream, plan_only=plan_only,
344 |                            allowed=allowed)
345 |     except LLMError as exc:
346 |         agent.repair()
347 |         print(f"\nerror: {exc}")
348 |         return ""
349 |     except KeyboardInterrupt:                      # Ctrl+C: stop this task, keep the chat
350 |         agent.repair()
351 |         print("\n[stopped - the conversation is kept; type your next request]")
352 |         return ""
353 |     shown = "".join(streamed).strip()
354 |     if streamed:
355 |         print()                                    # end the streamed line
356 |     if answer.strip() and answer.strip() != shown:
357 |         print(answer)                              # e.g. a finish summary that was not streamed
358 |     if not plan_only:
359 |         problems = toolbox.check_workspace(since=started)      # only what changed just now
360 |         print("[check of changed files: all OK]" if not problems
361 |               else "[workspace check FAILED]\n" + "\n".join(problems))
362 |         outcome = toolbox.run_hook("after_request")      # e.g. your test suite
363 |         if outcome is not None:
364 |             print(f"[after_request hook: exit code {outcome[0]}]\n{outcome[1].strip()}")
365 |     who = getattr(agent.backend, "last_model", "")
366 |     print(f"[{meter.summary()}" + (f" | {who}" if who else "") + "]")
367 |     return answer
368 | 
369 | 
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
370 | def attach_files(text: str, root: Path, limit: int) -> tuple[str, list[str]]:
371 |     """``@path`` mentions of workspace files: their contents are added to the request, which
372 |     saves the model a read_file call. Unknown paths stay as typed; ``limit`` caps the size."""
373 |     attached: list[str] = []
374 |     parts: list[str] = []
375 |     total = 0
376 |     for match in MENTION.finditer(text):
377 |         name = match.group(1).rstrip(".,;:!?)\"'")
378 |         try:
379 |             path = resolve_inside(root, name)
380 |         except SandboxError:
381 |             continue
382 |         if name in attached or not path.is_file() or looks_binary(path):
383 |             continue
384 |         content = path.read_text(encoding="utf-8-sig", errors="replace")
385 |         if total + len(content) > limit:
386 |             continue                               # too big for the context: the model reads it
387 |         total += len(content)
388 |         attached.append(name)
389 |         parts.append(f"Contents of {name} (attached by the user):\n```\n{content}\n```")
390 |     return (text + "\n\n" + "\n\n".join(parts) if parts else text), attached
391 | 
392 | 
```
- Like Claude Code's `@` mentions: for each `@path` naming a text file inside the workspace, the file's contents are added
  to the request, which saves the model a `read_file` call. Measured live: "what does @calc.py compute?" took 1 call and
  2 004 tokens instead of 2 calls and 4 017 (after rule 1 told the model not to read attached files again).
- Trailing punctuation is ignored (`@app.py,`). Paths outside the workspace, missing files, binary files and repeats are
  left as typed. `limit` caps the total: the chat allows a third of the history budget, so attachments can never crowd
  out the conversation (a file that does not fit is left for the model to read in parts).

```python
393 | def remember(note: str, root: Path) -> Path:
394 |     """Add ``note`` to AGENT.md under "## Notes" (both are created when missing)."""
395 |     path = root / "AGENT.md"
396 |     text, style = read_text(path) if path.is_file() else ("# Project notes\n", ("\n", False))
397 |     if "## Notes" not in text:
398 |         text = text.rstrip("\n") + "\n\n## Notes\n"
399 |     start = text.index("## Notes")
400 |     end = text.find("\n## ", start + 1)                # the next section, if any
401 |     end = len(text) if end == -1 else end
402 |     text = text[:end].rstrip("\n") + f"\n- {note}\n" + text[end:]
403 |     write_text(path, text, style)
404 |     return path
405 | 
406 | 
```
- Like Claude Code's `#` memory: `# use pytest, not unittest` adds `- use pytest, not unittest` to `AGENT.md` under a
  `## Notes` heading — at the end of that section, before the next `## ` heading. A missing file or section is
  created. The file's line breaks are kept (`read_text`/`write_text`). No model call is used.

```python
407 | def sessions_folder(config: Config) -> Path:
408 |     """This project's conversation logs: <project>/.localagent/sessions (an absolute state_dir wins)."""
409 |     return config.workspace / config.state_dir / "sessions"
410 | 
411 | 
```
- Where this project's conversation logs live: `<workspace>\.localagent\sessions`. Joining a path with an absolute
  `state_dir` gives that absolute path (that is how `pathlib` joins), so a custom absolute location still works.

## `Chat` — the loop and its commands
```python
412 | class Chat:
413 |     """The interactive loop and its commands. Holds what a command may change: the model
414 |     setup (/model), the conversation (/clear, /resume), and output waiting to be sent (!)."""
415 | 
```
```python
416 |     def __init__(self, config: Config, args: argparse.Namespace, backend: ChatBackend,
417 |                  toolbox: ToolBox, meter: Meter) -> None:
418 |         self.config, self.backend, self.toolbox, self.meter = config, backend, toolbox, meter
419 |         self.ctx_chosen = args.num_ctx is not None or "LOCALAGENT_NUM_CTX" in os.environ
420 |         self.root = config.workspace.resolve()
421 |         self.log = SessionLog(sessions_folder(config))
422 |         self.agent = Agent(config, backend, toolbox, meter, self.log)
423 |         self.checkpoints = Checkpoints(config.workspace)
424 |         self.commands = load_commands(config.workspace)  # custom /commands (.localagent/commands)
425 |         self.show = make_printer(use_color())
426 |         self.pending: list[str] = []                    # output of !commands, sent next time
427 |         self.help = HELP + "".join(f"\n  /{name:<16} {(text.strip().splitlines() or [''])[0][:60]}"
428 |                                    for name, text in sorted(self.commands.items()))
429 |         if args.resume:
430 |             previous = latest_session(sessions_folder(config))
431 |             if previous is not None and previous != self.log.path:
432 |                 self.agent.restore(load_session(previous))
433 |                 print(f"resumed {previous.name}")
434 | 
```
- Holds everything a command may change: the config and backend (`/model`), the agent and its session log (`/clear`,
  `/resume`), the undo steps, custom commands (from `.localagent/commands`, listed under the help text with the first
  line of each file), the coloured printer, and `pending` — output of `!commands` waiting to go with the next request.
- `ctx_chosen` remembers whether you chose the history budget yourself (then `/model` keeps it).
- `--resume`/`-c` reloads the newest conversation of **this project** (repairing it if it ended mid-task, and starting
  after its last `/clear` or compaction — see `session.md`).

```python
435 |     def run(self, text: str, plan_only: bool = False, allowed: frozenset[str] | None = None) -> str:
436 |         """One request: earlier !command output and @file contents go with it; the files it
437 |         changes become one /undo step."""
438 |         label = (text.splitlines() or [""])[0][:60]          # how /undo names this step
439 |         if self.pending:
440 |             text = "\n\n".join(self.pending) + "\n\nMy request: " + text
441 |             self.pending = []
442 |         limit = int(self.config.num_ctx * 0.7) * 4 // 3      # attachments: a third of the budget
443 |         text, attached = attach_files(text, self.root, limit)
444 |         if attached:
445 |             print(f"[attached: {', '.join(attached)}]")
446 |         message = self.checkpoints.take(label)
447 |         if message:
448 |             print(f"[{message}]")
449 |         try:
450 |             return handle_request(self.agent, self.toolbox, self.meter, text, plan_only, allowed,
451 |                                   self.show)
452 |         finally:
453 |             self.checkpoints.commit()
454 | 
```
- One request: earlier `!` output goes first ("I ran ... Output: ... My request: ..."), `@file` contents are attached,
  and the files it changes become one `/undo` step labelled with the start of your request (`take` before, `commit`
  after — also when the request fails or is stopped).

```python
455 |     def model_status(self) -> list[str]:
456 |         """What /model shows: the mode, who answers, and what online and offline would use."""
457 |         problem = online_problem(self.config)
458 |         last = getattr(self.backend, "last_model", "")
459 |         return [f"mode: {self.config.mode} ({MODE_TEXT.get(self.config.mode, '')})",
460 |                 f"models now: {model_label(self.config)}" + (f"; last answer from {last}" if last else ""),
461 |                 f"online:  {online_name(self.config)}, " + (problem or "key set"),
462 |                 f"offline: {describe_local(self.config)}",
463 |                 "switch with /model online, /model offline or /model auto "
464 |                 "(other services and models: localagent setup)"]
465 | 
```
- What `/model` shows: the mode and what it means, the model chain now and which model answered last, the online
  model with "key set" or what it still lacks, what the local model is (running, or started when needed, or not set
  up), and how to switch — other services and models are chosen with `localagent setup`.

```python
466 |     def switch(self, mode: str) -> None:
467 |         """/model <mode>: build the new model setup, start the local model if needed, swap it in.
468 |         The conversation and the files stay; only who answers changes."""
469 |         try:
470 |             config, notes = choose_mode(replace(self.config, fallback_backend=""), mode,
471 |                                         self.ctx_chosen)
472 |             config = gemini_history(config, chosen=self.ctx_chosen)
473 |             backend = make_backend(config)
474 |             if isinstance(backend, LocalModel):
475 |                 backend.ensure_ready()                  # start llama.cpp now, not mid-task
476 |         except (ValueError, LLMError) as exc:
477 |             print(f"cannot switch to {mode}: {exc}")
478 |             return
479 |         except KeyboardInterrupt:
480 |             print("\n[switch stopped; nothing changed]")
481 |             return
482 |         if mode == "online" and stop_local_servers():
483 |             notes.append("stopped the local model server (its memory is free again)")
484 |         self.config, self.backend = config, backend
485 |         self.agent.set_backend(backend, config)
486 |         for note in notes:
487 |             print(f"  {note}")
488 |         print(f"[now {mode}: {model_label(config)}]")
489 | 
```
- `/model <mode>`: builds the new setup (`choose_mode`, then the history budget) and the new backend. Going offline
  starts the local model **right away** (`ensure_ready`), so it loads now and not in the middle of your next task; this
  can take up to a minute and can be stopped with Ctrl+C (nothing changes then).
- Any problem (no key, no local model, the server would not start) is shown and **nothing changes**.
- Going online stops a local model server this program started (an 8B model holds about 5 GB of memory).
- Then the agent gets the new backend — the conversation and files stay — and the new setup is printed.

```python
490 |     def resume(self, choice: str) -> None:
491 |         """/resume: list this project's conversations (newest first), or continue number N."""
492 |         sessions = list_sessions(sessions_folder(self.config))[::-1][:10]
493 |         if not choice:
494 |             for number, path in enumerate(sessions, start=1):
495 |                 mark = "  (this conversation)" if path == self.log.path else ""
496 |                 print(f"  {number}. {session_title(path)}{mark}")
497 |             print("continue one with /resume <number>" if sessions else "no earlier conversations")
498 |             return
499 |         if not choice.isdigit() or not 1 <= int(choice) <= len(sessions):
500 |             print(f"choose a number from 1 to {len(sessions)} (see /resume)")
501 |             return
502 |         picked = sessions[int(choice) - 1]
503 |         if picked == self.log.path:
504 |             print("that is this conversation")
505 |             return
506 |         self.agent.reset()
507 |         self.agent.restore(load_session(picked))
508 |         print(f"continued {picked.name} ({len(self.agent.messages) - 1} messages)")
509 | 
```
- `/resume` lists this project's last 10 conversations, newest first, each with its date and first request; the
  current one is marked. `/resume 3` clears the current conversation (logged as a reset) and continues number 3 — its
  messages are added to the current log, so it carries on there.

```python
510 |     def shell(self, command: str) -> None:
511 |         """``!command``: run it yourself in the project folder (you typed it, so no question);
512 |         its output is shown, and sent along with your next request."""
513 |         code, output = run_command(shell_argv(command), self.root, 600, self.config.max_output_chars)
514 |         print(output.rstrip())
515 |         print(f"[exit code {code}; the output goes with your next request]")
516 |         self.pending.append(f"I ran `{command}` myself (exit code {code}). Output:\n{output.strip()}")
517 | 
```
- `!command`, like Claude Code's bash mode: runs the command yourself in the project folder (PowerShell on Windows), with
  a 10-minute limit, **without asking** (you typed it) and **without a model call**. The output is shown and kept in
  `pending`, so the model sees it with your next request ("!pytest", then "fix the failures"). Ctrl+C stops it.

```python
518 |     def command(self, text: str) -> bool:
519 |         """Act on one line; False means quit."""
520 |         command, _, rest = text.partition(" ")
521 |         rest = rest.strip()
522 |         name = command.lower()
523 |         if name in ("/quit", "/exit"):
524 |             return False
525 |         if name == "/help":
526 |             print(self.help)
527 |         elif name in ("/reset", "/clear"):
528 |             self.agent.reset()
529 |             print("conversation cleared (files are unchanged)")
530 |         elif name == "/undo":
531 |             print(self.checkpoints.undo())
532 |         elif name == "/stats":
533 |             print(self.meter.summary())
534 |             for line in getattr(self.backend, "status", lambda: [])():
535 |                 print(line)                        # daily quota use per Gemini model
536 |         elif name == "/compact":
537 |             try:
538 |                 print(self.agent.compact())
539 |             except (LLMError, KeyboardInterrupt) as exc:
540 |                 print(f"compaction stopped: {exc}")
541 |         elif name == "/init":
542 |             self.run(INIT_PROMPT, allowed=INIT_TOOLS)
543 |             notes_file = self.root / "AGENT.md"
544 |             print(f"[AGENT.md {'written' if notes_file.is_file() else 'was NOT written - try /init again'}]")
545 |             self.agent.refresh_system()            # the new notes take effect immediately
546 |         elif name == "/plan":
547 |             if not rest:
548 |                 print("usage: /plan <what you want done>")
549 |             elif self.run(rest, plan_only=True) and confirm("Carry out this plan? [y/N] "):
550 |                 self.run(PLAN_FOLLOW_UP)
551 |         elif name in ("/model", "/online", "/offline", "/auto"):
552 |             wanted = rest.lower() if name == "/model" else name[1:]
553 |             if wanted:
554 |                 self.switch(wanted)
555 |             else:
556 |                 print("\n".join(self.model_status()))
557 |         elif name == "/resume":
558 |             self.resume(rest)
559 |         elif name == "/permissions":
560 |             rules = self.toolbox.permissions
561 |             print("allow: " + (", ".join(rules.allow) or "(none)"))
562 |             print("deny:  " + (", ".join(rules.deny) or "(none)"))
563 |             print(f"rules live in {USER_SETTINGS} and {self.root / PROJECT_SETTINGS}")
564 |         elif name == "/doctor":
565 |             print("\n".join(run_doctor(self.config)[1]))
566 |         elif name.startswith("/") and name[1:] in self.commands:
567 |             self.run(expand_command(self.commands[name[1:]], rest))
568 |         elif name.startswith("/"):
569 |             print(f"unknown command {command} - type /help (nothing was sent to the model)")
570 |         elif text.startswith("!") and text[1:].strip():
571 |             self.shell(text[1:].strip())
572 |         elif text.startswith("#") and text[1:].strip() and "\n" not in text:
573 |             print(f"[noted in {remember(text[1:].strip(), self.root)}]")
574 |             self.agent.refresh_system()
575 |         else:
576 |             self.run(text)
577 |         return True
578 | 
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
579 |     def loop(self, notes: list[str]) -> int:
580 |         """Show the banner, then read and act on lines until /quit or end of input."""
581 |         print(f"localagent {__version__} | {self.config.mode} | {model_label(self.config)} | "
582 |               f"sandbox={self.config.sandbox} | folder={self.root}")
583 |         for note in notes + self.toolbox.notes:
584 |             print(f"  {note}")
585 |         print(self.help)
586 |         while True:
587 |             try:
588 |                 text = read_request()
589 |             except (EOFError, KeyboardInterrupt):
590 |                 print()
591 |                 return 0
592 |             if not text:
593 |                 continue
594 |             try:
595 |                 if not self.command(text):
596 |                     return 0
597 |             except KeyboardInterrupt:              # e.g. Ctrl+C during a !command
598 |                 print("\n[stopped]")
599 | 
600 | 
```
- The banner shows the version, the mode, the model chain, the sandbox and the folder, then the automatic-setup notes
  and the toolbox's notes (for example which MCP servers started), then `HELP`.
- Reads a request at a time; Ctrl+C or Ctrl+D at the prompt (`EOFError`) leaves the program. A Ctrl+C during a command
  (for example a long `!command`) stops that command and returns to the prompt.

## `run_once` — the `-p` mode
```python
601 | def run_once(config: Config, backend: ChatBackend, toolbox: ToolBox, meter: Meter,
602 |              prompt: str) -> int:
603 |     """``-p``: one request without the chat. The answer goes to standard output and progress
604 |     to standard error, so scripts can use the answer; anything that needs a question is refused."""
605 |     text = sys.stdin.read() if prompt == "-" else prompt
606 |     root = config.workspace.resolve()
607 |     text, _ = attach_files(text.strip(), root, int(config.num_ctx * 0.7) * 4 // 3)
608 |     agent = Agent(config, backend, toolbox, meter, SessionLog(sessions_folder(config)))
609 |     started = time.time()
610 |     try:
611 |         answer = agent.run(text, on_event=lambda line: print(line, file=sys.stderr))
612 |     except LLMError as exc:
613 |         print(f"error: {exc}", file=sys.stderr)
614 |         return 1
615 |     print(answer)
616 |     problems = toolbox.check_workspace(since=started)
617 |     if problems:
618 |         print("[workspace check FAILED]\n" + "\n".join(problems), file=sys.stderr)
619 |     print(f"[{meter.summary()}]", file=sys.stderr)
620 |     return 1 if problems else 0
621 | 
622 | 
```
- One request without the chat, for scripts and other programs: `localagent -p "list the TODO comments" > todos.txt`.
  The request may come from your input (`-p -`) and may attach `@files`.
- The **answer goes to standard output** and everything else (progress, notices, the usage line) to standard error,
  so a script receives just the answer. Actions that would need a question are refused (see `refuse`). The
  conversation is logged like any other, so `localagent -c` can continue it in the chat.
- Exit code 0 when answered and the changed files pass the check, 1 when the model failed or a changed file is broken.

## The product commands
```python
623 | def run_subcommand(name: str, words: list[str]) -> int:
624 |     """`localagent setup` and `localagent models ...` (see models.py); no chat is started."""
625 |     try:
626 |         apply_env(load_settings(Path(os.environ.get("LOCALAGENT_WORKSPACE", "."))))
627 |         config = Config.from_env()
628 |     except ValueError as exc:
629 |         print(f"setup problem: {exc}")
630 |         return 2
631 |     try:
632 |         return run_setup(config) if name == "setup" else run_models(words, config)
633 |     except KeyboardInterrupt:                      # a download stopped: it continues next time
634 |         print("\n[stopped - run the same command again to continue]")
635 |         return 130
636 | 
637 | 
```
- `localagent setup` (see `wizard.md`) and `localagent models ...` (see `models.md`). They are recognised **before**
  the flag parser,
  so they need no flags of their own and the chat's flags stay as they were. The settings files' `env` is applied
  and the configuration read; a problem there is one `setup problem` line. Ctrl+C during a download says how to
  continue it (exit code 130, the usual code for "stopped by Ctrl+C").

## `guided_first_run` — the very first start
```python
638 | def guided_first_run(config: Config, args: argparse.Namespace,
639 |                      read: Callable[[str], str] = input) -> bool:
640 |     """The very first start - no model set up, a person at the keyboard - offers the guided
641 |     setup at once instead of an error. True when it ran and made a model ready."""
642 |     interactive = sys.stdin.isatty() and sys.stdout.isatty()
643 |     if (not interactive or args.prompt is not None or args.doctor or args.no_auto
644 |             or args.backend is not None or not online_problem(config) or local_kind(config)):
645 |         return False
646 |     print("No model is set up yet. The guided setup takes a minute: an online service with your "
647 |           "key (Gemini's is free),\na model server you already run, or a free model downloaded "
648 |           "for you.")
649 |     try:
650 |         answer = read("Start it now? [Y/n] ").strip().lower()
651 |     except (EOFError, KeyboardInterrupt):
652 |         print()
653 |         return False
654 |     return answer in ("", "y", "yes") and run_subcommand("setup", []) == 0
655 | 
656 | 
```
- Offers the guided setup instead of an error when **all** of these hold: a person is typing (input and output are a
  terminal), it is a normal chat start (no `-p`, `--doctor`, `--no-auto` or `--backend`), no online model works and
  no local model is available. One question, `Start it now? [Y/n]`; Enter or `y` runs `localagent setup`. True when the
  setup made a model ready; then `main` reads the settings again and carries on into the chat. Scripts and CI never
  see the question.

## `main` — start-up
```python
657 | def main(argv: list[str] | None = None) -> int:
658 |     """Entry point: choose the setup, build the components, then chat (or answer once)."""
659 |     for stream in (sys.stdout, sys.stderr):        # a character the console cannot show must
660 |         if hasattr(stream, "reconfigure"):         # not crash the program (piped output on Windows)
661 |             stream.reconfigure(errors="replace")
662 |     words = sys.argv[1:] if argv is None else argv
663 |     if words and words[0] in SUBCOMMANDS:
664 |         return run_subcommand(words[0], words[1:])
665 |     args = build_parser().parse_args(words)
666 |     workspace = args.workspace or Path(os.environ.get("LOCALAGENT_WORKSPACE", "."))
667 |     try:
668 |         settings = load_settings(workspace)        # its "env" applies before the config is read
669 |         apply_env(settings)
670 |         config = build_config(args)
671 |         if guided_first_run(config, args):         # the setup saved new settings: read them again
672 |             settings = load_settings(workspace)
673 |             apply_env(settings)
674 |             config = build_config(args)
675 |     except ValueError as exc:                      # e.g. broken settings.json, a typo in a variable
676 |         print(f"setup problem: {exc}")
677 |         return 2
678 |     notes: list[str] = []
679 |     try:
680 |         if not args.no_auto:
681 |             config, notes = auto_configure(config, args)
682 |     except ValueError as exc:                      # e.g. no model at all yet
683 |         if not args.doctor:
684 |             print(f"setup problem: {exc}")
685 |             return 2
686 |         notes = [f"FAIL  {exc}"]
687 |     if args.doctor:
688 |         healthy, report = run_doctor(config)
689 |         print("\n".join(notes + report))
690 |         return 0 if healthy else 1
691 |     try:
692 |         backend = make_backend(config)
693 |     except ValueError as exc:                      # e.g. missing key
694 |         print(f"setup problem: {exc}")
695 |         return 2
696 |     approver = Approver()
697 |     approve: Callable[[str], bool] = approver
698 |     if config.auto_approve:
699 |         approve = lambda _a: True                  # noqa: E731 (a one-line answer)
700 |     elif args.prompt is not None:
701 |         approve = refuse
702 |     meter = Meter()
703 |     toolbox = ToolBox(config, approve, settings)
704 |     approver.permissions = toolbox.permissions
705 |     try:
706 |         if args.prompt is not None:
707 |             return run_once(config, backend, toolbox, meter, args.prompt)
708 |         return Chat(config, args, backend, toolbox, meter).loop(notes)
709 |     finally:
710 |         toolbox.close()                            # stops the sandbox and MCP servers
711 |         stop_local_servers()                       # and a local model this run started
```
- First makes the output robust: with output piped or redirected on Windows, Python writes in the old Windows code
  page, where one emoji or arrow in a model's answer or a diff would crash the program with `UnicodeEncodeError`;
  `errors="replace"` shows such a character as `?` instead (found in review).
- A first word `setup` or `models` goes to `run_subcommand`.
- Loads the settings files of the workspace and applies their `env` **before** building the config (so a settings file
  can set the mode, the model file or anything else). A broken `settings.json`, or a typo in a variable such as
  `LOCALAGENT_NUM_CTX=8k`, is printed as one `setup problem` line with exit code 2 instead of a traceback.
- On the very first start `guided_first_run` may run the setup; the settings are then loaded and applied again, so the
  chat starts with what was just chosen.
- Unless `--no-auto`, applies automatic setup. When no model can work, that is a setup problem too — except with
  `--doctor`, which then runs anyway and shows the reason first, because that is when you need it most.
- `--doctor` prints the automatic choices plus the health report and exits with 0 (healthy) or 1.
- Builds the backend (a missing key or model → setup problem). The approver: with `--auto-approve` nothing is asked (deny
  rules still apply); with `-p` nothing can be asked, so `refuse`; otherwise the interactive `Approver`, connected to
  the toolbox's permissions for "always" answers.
- Runs the chat (or the single `-p` request). `finally` always closes the toolbox — stopping the Docker sandbox and any
  MCP server programs — and stops a local model server this run started, even after an error.
