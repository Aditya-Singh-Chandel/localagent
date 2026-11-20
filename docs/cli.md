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
```python
  1 | """Command-line interface: the chat prompt around the Agent (like Claude Code's terminal UI).
  2 | 
  3 | Start it inside a project folder:  localagent        (or: python -m localagent)
  4 | Whatever you do not choose with flags is chosen for you from what this computer has:
  5 | Gemini when a key is set, the Docker sandbox when it is ready, and a running local
  6 | model server as the last fallback (``--no-auto`` turns this off).
  7 | """
  8 | from __future__ import annotations
  9 | 
 10 | import argparse
 11 | import os
 12 | import sys
 13 | import time
 14 | import urllib.request
 15 | from dataclasses import replace
 16 | from pathlib import Path
 17 | from typing import Callable
 18 | 
 19 | from .agent import Agent
 20 | from .backends import BACKENDS, make_backend
 21 | from .checkpoint import Checkpoints
 22 | from .config import Config, gemini_history, overlay
 23 | from .doctor import run_doctor
 24 | from .llm import ChatBackend, LLMError
 25 | from .meter import Meter
 26 | from .runners import sandbox_image_ready
 27 | from .session import SessionLog, latest_session, load_session
 28 | from .settings import Permissions, expand_command, load_commands, load_settings, suggest_rule
 29 | from .tools import Approval, ToolBox
 30 | 
 31 | HELP = """Commands:
 32 |   /plan <request>  look first, show a plan, then carry it out if you agree
 33 |   /init            write AGENT.md (project notes the agent reads every session)
 34 |   /compact         summarise the conversation to save tokens
 35 |   /undo            revert the file changes of the last request
 36 |   /reset           forget the conversation (files stay)
 37 |   /stats           calls, tokens, time and today's quota use
 38 |   /help   /quit    (your own commands: .localagent/commands/<name>.md -> /<name>)
 39 | Multi-line input: end a line with \\ to continue it, or put \"\"\" alone on a line to start and end a block.
 40 | Ctrl+C stops the current task; the conversation is kept."""
 41 | INIT_PROMPT = (
 42 |     "Create AGENT.md in the workspace root: notes for an AI coding agent working on this project. "
 43 |     "First look at the project (outline, and read the README and main config files if present). "
 44 |     "Write at most 60 lines with these sections: Purpose; How to run; How to test; Conventions "
 45 |     "(style, structure); Key files (one line each). If AGENT.md exists, update it instead. "
 46 |     "Only write AGENT.md: do not run, fix or change anything else, even if you notice problems "
 47 |     "(mention them in AGENT.md under 'Known issues').")
 48 | INIT_TOOLS = frozenset({"write_file"})           # /init may read anything but only write files
 49 | PLAN_FOLLOW_UP = "Implement the plan above step by step, verify the result, then finish."
 50 | BUILT_IN = {"/quit", "/help", "/reset", "/undo", "/stats", "/compact", "/init", "/plan"}
 51 | 
 52 | 
```
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
```python
 53 | class Approver:
 54 |     """Asks before anything runs. Answering "a" (always) saves a permission rule, like
 55 |     Claude Code's "Yes, and don't ask again"."""
 56 | 
```
```python
 57 |     def __init__(self, read: Callable[[str], str] = input) -> None:
 58 |         self._read = read
 59 |         self.permissions: Permissions | None = None       # set once the toolbox exists
 60 | 
```
```python
 61 |     def __call__(self, action: str) -> bool:
 62 |         answer = self._read(f"  allow: {action}? [y]es / [n]o / [a]lways: ").strip().lower()
 63 |         if answer in {"a", "always"} and isinstance(action, Approval) and self.permissions:
 64 |             rule = suggest_rule(action.tool, action.subject)
 65 |             saved = self.permissions.always_allow(rule)
 66 |             print(f"  allowed from now on: {rule}" + (f" (saved in {saved})" if saved else ""))
 67 |         return answer in {"y", "yes", "a", "always"}
 68 | 
 69 | 
```
- Called by the toolbox whenever no permission rule decides (see `settings.md`). It prints the question, e.g.
  `allow: run python main.py? [y]es / [n]o / [a]lways:`.
- `y`/`yes` allows once. Enter or anything else refuses (the safe default).
- `a`/`always` allows **and** saves a narrow rule (`suggest_rule`: exactly this command, this file, this website or this
  tool) in the project's `.localagent/settings.json`, so you are not asked again — like Claude Code's "Yes, and don't
  ask again". The question is an `Approval` (a string that also knows the tool and subject), which is how the rule can
  be built. `read` is injectable so tests can answer.

## The flags shared by the chat, the server and the evals
```python
 70 | def add_model_flags(parser: argparse.ArgumentParser) -> None:
 71 |     """The flags every entry point shares (chat, server, evals): which model, where code runs."""
 72 |     parser.add_argument("--backend", choices=sorted(BACKENDS), help="model service type")
 73 |     parser.add_argument("--model", help="model name for ollama/openai, e.g. qwen3-8b")
 74 |     parser.add_argument("--api-base", help="URL of an OpenAI-style server, e.g. http://host:8080/v1")
 75 |     parser.add_argument("--sandbox", choices=["local", "docker"],
 76 |                         help="where code runs: this machine or a Docker container")
 77 | 
 78 | 
```
- Adds the four flags all three programs need: `--backend`, `--model`, `--api-base` and `--sandbox`. Defined once
  here, so the three cannot drift apart (before, each program declared its own copy).

```python
 79 | def model_flags(args: argparse.Namespace) -> dict:
 80 |     """The values of those shared flags, as Config field names (None = not given)."""
 81 |     return {"backend": args.backend, "model": args.model, "api_base": args.api_base,
 82 |             "sandbox": args.sandbox}
 83 | 
 84 | 
```
- Reads those four values back as `Config` field names (`api-base` becomes `api_base`), ready for `overlay`.

## `build_parser`
```python
 85 | def build_parser() -> argparse.ArgumentParser:
 86 |     """Define the command-line flags."""
 87 |     parser = argparse.ArgumentParser(prog="localagent", description="A verifying coding agent")
 88 |     add_model_flags(parser)
 89 |     parser.add_argument("--workspace", type=Path, help="project folder (default: current folder)")
 90 |     parser.add_argument("--num-ctx", type=int, help="context window size in tokens")
 91 |     parser.add_argument("--doctor", action="store_true", help="check the setup and exit")
 92 |     parser.add_argument("--resume", action="store_true", help="continue the latest session")
 93 |     parser.add_argument("--type-check", action="store_true", help="also verify with mypy")
 94 |     parser.add_argument("--auto-approve", action="store_true", help="never ask before running")
 95 |     parser.add_argument("--network", action="store_true",
 96 |                         help="allow internet inside the Docker sandbox (e.g. pip install)")
 97 |     parser.add_argument("--fallback", choices=["ollama", "openai"],
 98 |                         help="with --backend gemini: local server to use when Gemini fails")
 99 |     parser.add_argument("--reasoning", help="reasoning effort for OpenAI-style servers, e.g. low")
100 |     parser.add_argument("--allow-shell", action="store_true",
101 |                         help="let the model run shell commands on this computer (asks each time)")
102 |     parser.add_argument("--no-auto", action="store_true",
103 |                         help="do not pick backend/sandbox/fallback automatically")
104 |     return parser
105 | 
106 | 
```
- Declares the flags: the shared ones (`add_model_flags`), then the chat's own. Value flags default to `None`, meaning
  "not given" (so automatic setup and environment variables can fill them): `--workspace` (default: the current
  folder), `--num-ctx`, `--fallback`, `--reasoning`.
- On/off flags: `--doctor`, `--resume`, `--type-check`, `--auto-approve`, `--network` (internet inside the sandbox),
  `--allow-shell` (shell on this computer, asks every time) and `--no-auto` (no automatic choices).

## `build_config`
```python
107 | def build_config(args: argparse.Namespace) -> Config:
108 |     """Environment defaults overlaid with the flags the user actually gave."""
109 |     config = overlay(Config.from_env(), **model_flags(args), workspace=args.workspace,
110 |                      num_ctx=args.num_ctx, fallback_backend=args.fallback,
111 |                      reasoning_effort=args.reasoning)
112 |     return replace(config, type_check=args.type_check, auto_approve=args.auto_approve,
113 |                    sandbox_network=args.network, allow_host_shell=args.allow_shell)
114 | 
115 | 
```
- Starts from `Config.from_env()` (defaults + environment variables), `overlay`s only the value flags actually given
  (the shared ones from `model_flags` plus the chat's own), then sets the on/off flags. Order of strength: flags > environment variables > defaults.

## Automatic setup
```python
116 | def server_up(api_base: str) -> bool:
117 |     """True when an OpenAI-style model server answers at ``api_base`` within a second."""
118 |     try:
119 |         with urllib.request.urlopen(api_base + "/models", timeout=1) as response:
120 |             return response.status == 200
121 |     except (OSError, ValueError):
122 |         return False
123 | 
124 | 
```
- Asks an OpenAI-style server for its model list with a 1-second timeout; any normal answer means a local model server
  is running (e.g. `serve.ps1`).

```python
125 | def auto_configure(config: Config, args: argparse.Namespace) -> tuple[Config, list[str]]:
126 |     """Choose what the user left open from what is available; explain each choice."""
127 |     notes: list[str] = []
128 |     if args.backend is None and "LOCALAGENT_BACKEND" not in os.environ and config.gemini_api_key:
129 |         config = replace(config, backend="gemini")
130 |         notes.append("backend: gemini (GEMINI_API_KEY is set)")
131 |     if (args.sandbox is None and "LOCALAGENT_SANDBOX" not in os.environ
132 |             and sandbox_image_ready(config.sandbox_image)):
133 |         config = replace(config, sandbox="docker")
134 |         notes.append("sandbox: docker (Docker and the sandbox image are ready)")
135 |     if (config.backend == "gemini" and not config.fallback_backend and args.fallback is None
136 |             and server_up(config.api_base)):
137 |         config = replace(config, fallback_backend="openai")
138 |         notes.append(f"fallback: the local model server at {config.api_base}")
139 |     budgeted = gemini_history(config, chosen=args.num_ctx is not None)
140 |     if budgeted.num_ctx != config.num_ctx:
141 |         config = budgeted
142 |         notes.append(f"history budget: {config.num_ctx} tokens (Gemini handles long conversations)")
143 |     return config, notes
144 | 
145 | 
```
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
```python
146 | def model_label(config: Config) -> str:
147 |     """Short description of which model(s) will answer, for banners and scoreboards."""
148 |     if config.backend == "gemini":
149 |         chain = " > ".join(config.gemini_models + config.gemini_strong_models)
150 |         return f"gemini:{chain}" + (f" > {config.fallback_backend}:{config.model}"
151 |                                     if config.fallback_backend else "")
152 |     return f"{config.backend}:{config.model}"
153 | 
154 | 
```
- A readable description of who answers, e.g.
  `gemini:gemini-3.5-flash-lite > gemini-3.1-flash-lite > gemini-3.5-flash > gemini-2.5-flash > openai:qwen3-8b`.
  Used by the banner and the eval scoreboard.

## `read_request` — typing a request
```python
155 | def read_request(read: Callable[[str], str] = input) -> str:
156 |     """One request from the keyboard; supports ``\\`` line continuation and \"\"\" blocks."""
157 |     first = read("you> ").lstrip("﻿")        # piped input may start with a byte-order mark
158 |     if first.strip() == '"""':
159 |         lines: list[str] = []
160 |         while (line := read("... ")).strip() != '"""':
161 |             lines.append(line)
162 |         return "\n".join(lines).strip()
163 |     parts = [first]
164 |     while parts[-1].endswith("\\"):
165 |         parts[-1] = parts[-1][:-1]
166 |         parts.append(read("... "))
167 |     return "\n".join(parts).strip()
168 | 
169 | 
```
- Reads one line after `you> ` and removes an invisible byte-order mark from its start: text piped into a program by
  PowerShell can begin with one, which made a piped `/init` arrive as `\ufeff/init` and be treated as an ordinary
  request (found in the end-to-end test).
- A line ending in `\` continues on the next line (`... ` prompt). A line that is only
  `"""` starts a block that ends at the next `"""` line — handy for pasting code or long instructions.
  (`:=` reads a line and tests it in one step.) `read` is injectable for tests.

## `handle_request` — one request
```python
170 | def handle_request(agent: Agent, toolbox: ToolBox, meter: Meter, text: str,
171 |                    plan_only: bool = False, allowed: frozenset[str] | None = None) -> str:
172 |     """Run one request, show progress and the answer, then verify the whole workspace."""
173 |     streamed: list[str] = []
174 | 
175 |     def show(token: str) -> None:
176 |         streamed.append(token)
177 |         print(token, end="", flush=True)
178 | 
179 |     started = time.time()
180 |     try:
181 |         answer = agent.run(text, on_event=print, on_token=show, plan_only=plan_only,
182 |                            allowed=allowed)
183 |     except LLMError as exc:
184 |         agent.repair()
185 |         print(f"\nerror: {exc}")
186 |         return ""
187 |     except KeyboardInterrupt:                      # Ctrl+C: stop this task, keep the chat
188 |         agent.repair()
189 |         print("\n[stopped - the conversation is kept; type your next request]")
190 |         return ""
191 |     shown = "".join(streamed).strip()
192 |     if streamed:
193 |         print()                                    # end the streamed line
194 |     if answer.strip() and answer.strip() != shown:
195 |         print(answer)                              # e.g. a finish summary that was not streamed
196 |     if not plan_only:
197 |         problems = toolbox.check_workspace(since=started)      # only what changed just now
198 |         print("[check of changed files: all OK]" if not problems
199 |               else "[workspace check FAILED]\n" + "\n".join(problems))
200 |     if not plan_only:
201 |         outcome = toolbox.run_hook("after_request")      # e.g. your test suite
202 |         if outcome is not None:
203 |             print(f"[after_request hook: exit code {outcome[0]}]\n{outcome[1].strip()}")
204 |     print(f"[{meter.summary()}]")
205 |     return answer
206 | 
207 | 
```
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
```python
208 | def snapshot(checkpoints: Checkpoints) -> None:
209 |     """Snapshot the folder for /undo; the one-time "too large" message is shown if it comes."""
210 |     message = checkpoints.take()
211 |     if message:
212 |         print(f"[{message}]")
213 | 
214 | 
```
- Takes a `/undo` snapshot before a request and prints the one-time "undo is off" message if the folder turned out to
  be too large.

## `chat` — the loop
```python
215 | def sessions_folder(config: Config) -> Path:
216 |     """This project's conversation logs: <project>/.localagent/sessions (an absolute state_dir wins)."""
217 |     return config.workspace / config.state_dir / "sessions"
218 | 
219 | 
```
- Where this project's conversation logs live: `<workspace>\.localagent\sessions`. Joining a path with an absolute
  `state_dir` gives that absolute path (that is how `pathlib` joins), so a custom absolute location still works.

```python
220 | def chat(config: Config, args: argparse.Namespace, backend: ChatBackend, toolbox: ToolBox,
221 |          meter: Meter, notes: list[str]) -> int:
222 |     """The interactive loop: read a request or command, act on it, repeat."""
223 |     log = SessionLog(sessions_folder(config))
224 |     agent = Agent(config, backend, toolbox, meter, log)
225 |     if args.resume:
226 |         previous = latest_session(sessions_folder(config))
227 |         if previous is not None:
228 |             agent.restore(load_session(previous))
229 |             print(f"resumed {previous.name}")
230 |     checkpoints = Checkpoints(config.workspace)
231 |     commands = load_commands(config.workspace)     # custom /commands from .localagent/commands
232 |     help_text = HELP + "".join(
233 |         f"\n  /{name:<15} {(text.strip().splitlines() or [''])[0][:60]}"
234 |         for name, text in sorted(commands.items()))
235 |     print(f"localagent | {model_label(config)} | sandbox={config.sandbox} | "
236 |           f"folder={config.workspace.resolve()}")
237 |     for note in notes + toolbox.notes:
238 |         print(f"  {note}")
239 |     print(help_text)
240 |     while True:
241 |         try:
242 |             text = read_request()
243 |         except (EOFError, KeyboardInterrupt):
244 |             print()
245 |             return 0
246 |         command, _, rest = text.partition(" ")
247 |         if not text:
248 |             continue
249 |         if command == "/quit":
250 |             return 0
251 |         if command == "/help":
252 |             print(help_text)
253 |         elif command == "/reset":
254 |             agent.reset()
255 |             print("conversation cleared")
256 |         elif command == "/undo":
257 |             print(checkpoints.undo())
258 |         elif command == "/stats":
259 |             print(meter.summary())
260 |             for line in getattr(backend, "status", lambda: [])():
261 |                 print(line)                        # daily quota use per Gemini model
262 |         elif command == "/compact":
263 |             try:
264 |                 print(agent.compact())
265 |             except (LLMError, KeyboardInterrupt) as exc:
266 |                 print(f"compaction stopped: {exc}")
267 |         elif command == "/init":
268 |             snapshot(checkpoints)
269 |             handle_request(agent, toolbox, meter, INIT_PROMPT, allowed=INIT_TOOLS)
270 |             notes_file = config.workspace / "AGENT.md"
271 |             print(f"[AGENT.md {'written' if notes_file.is_file() else 'was NOT written - try /init again'}]")
272 |             agent.refresh_system()                 # the new notes take effect immediately
273 |         elif command == "/plan":
274 |             if not rest.strip():
275 |                 print("usage: /plan <what you want done>")
276 |                 continue
277 |             plan = handle_request(agent, toolbox, meter, rest.strip(), plan_only=True)
278 |             if plan and input("Carry out this plan? [y/N] ").strip().lower() in {"y", "yes"}:
279 |                 snapshot(checkpoints)
280 |                 handle_request(agent, toolbox, meter, PLAN_FOLLOW_UP)
281 |         elif command.startswith("/") and command[1:].lower() in commands:
282 |             request = expand_command(commands[command[1:].lower()], rest.strip())
283 |             snapshot(checkpoints)
284 |             handle_request(agent, toolbox, meter, request)
285 |         elif command.startswith("/") and command not in BUILT_IN:
286 |             print(f"unknown command {command} - type /help (nothing was sent to the model)")
287 |         else:
288 |             snapshot(checkpoints)
289 |             handle_request(agent, toolbox, meter, text)
290 | 
291 | 
```
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
```python
292 | def main(argv: list[str] | None = None) -> int:
293 |     """Entry point: choose the setup, build the components, then chat."""
294 |     for stream in (sys.stdout, sys.stderr):        # a character the console cannot show must
295 |         if hasattr(stream, "reconfigure"):         # not crash the program (piped output on Windows)
296 |             stream.reconfigure(errors="replace")
297 |     args = build_parser().parse_args(argv)
298 |     config = build_config(args)
299 |     notes: list[str] = []
300 |     if not args.no_auto:
301 |         config, notes = auto_configure(config, args)
302 |     if args.doctor:
303 |         healthy, report = run_doctor(config)
304 |         print("\n".join(notes + report))
305 |         return 0 if healthy else 1
306 |     try:
307 |         settings = load_settings(config.workspace)
308 |         backend = make_backend(config)
309 |     except ValueError as exc:                      # e.g. missing key, broken settings.json
310 |         print(f"setup problem: {exc}")
311 |         return 2
312 |     approver = Approver()
313 |     meter = Meter()
314 |     toolbox = ToolBox(config, (lambda _a: True) if config.auto_approve else approver, settings)
315 |     approver.permissions = toolbox.permissions
316 |     try:
317 |         return chat(config, args, backend, toolbox, meter, notes)
318 |     finally:
319 |         toolbox.close()                            # stops the sandbox and MCP servers
```
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
