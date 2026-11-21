"""Command-line interface: the chat prompt around the Agent (like Claude Code's terminal UI).

Start it inside a project folder:  localagent        (or: python -m localagent)
One request, answer printed:       localagent -p "what does this project do?"
The model follows the mode: "auto" (the default) answers with the online model (Gemini, or the
service chosen in `localagent setup`) and with the local model when that cannot be reached;
"online" uses only the online model; "offline" only the local one. Choose with --online /
--offline, or switch any time in the chat with /model.
Everything else is chosen from what this computer has (the Docker sandbox when it is ready);
--backend and --no-auto still let you set everything by hand.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import time
from dataclasses import replace
from pathlib import Path
from typing import Callable

from . import __version__
from .agent import Agent
from .backends import BACKENDS, make_backend
from .checkpoint import Checkpoints
from .config import MODES, Config, gemini_history, online_problem, overlay, with_provider
from .doctor import run_doctor
from .llm import ChatBackend, LLMError
from .localserver import (NO_LOCAL_MODEL, SETUP_HINT, LocalModel, can_start, describe_local,
                          find_model_file, ollama_up, server_up, stop_local_servers)
from .meter import Meter
from .models import run_models
from .providers import PROVIDERS, find_provider
from .runners import sandbox_image_ready
from .sandbox import SandboxError, looks_binary, resolve_inside, run_command, shell_argv
from .session import SessionLog, latest_session, list_sessions, load_session, session_title
from .settings import (PROJECT_SETTINGS, USER_SETTINGS, Permissions, apply_env, expand_command,
                       load_commands, load_settings, suggest_rule)
from .tools import Approval, ToolBox, read_text, write_text
from .wizard import run_setup

HELP = """Commands:
  /plan <request>    look first, show a plan, then carry it out if you agree
  /model [mode]      show the model, or switch: online (Gemini), offline (local model), auto
  /init              write AGENT.md (project notes the agent reads every session)
  /compact           summarise the conversation to save tokens
  /undo              revert the file changes of the last request that changed files
  /clear             forget the conversation (files stay); /reset does the same
  /resume [number]   list this project's earlier conversations, or continue one
  /stats             calls, tokens, time and today's quota use
  /permissions       the permission rules in force
  /doctor            check the setup
  /help   /quit      (your own commands: .localagent/commands/<name>.md -> /<name>)
Shortcuts: !command runs a command yourself (no model call; its output goes with your next
request), # note saves a note in AGENT.md, @file in a request attaches that file.
Several lines: end a line with \\, or put \"\"\" alone on a line to start and end a block.
Ctrl+C stops the current task; the conversation is kept."""
INIT_PROMPT = (
    "Create AGENT.md in the workspace root: notes for an AI coding agent working on this project. "
    "First look at the project (outline, and read the README and main config files if present). "
    "Write at most 60 lines with these sections: Purpose; How to run; How to test; Conventions "
    "(style, structure); Key files (one line each). If AGENT.md exists, update it instead. "
    "Only write AGENT.md: do not run, fix or change anything else, even if you notice problems "
    "(mention them in AGENT.md under 'Known issues').")
INIT_TOOLS = frozenset({"write_file"})           # /init may read anything but only write files
PLAN_FOLLOW_UP = "Implement the plan above step by step, verify the result, then finish."
BUILT_IN = {"/quit", "/exit", "/help", "/reset", "/clear", "/undo", "/stats", "/compact", "/init",
            "/plan", "/model", "/online", "/offline", "/auto", "/resume", "/permissions", "/doctor"}
MODE_TEXT = {"auto": "the online model, and the local model when it cannot be reached",
             "online": "the online model only", "offline": "the local model only"}
NOTHING_HELP = ("No model is available yet. The guided setup takes a minute:  localagent setup\n"
                "It offers a free Gemini key; your own key for OpenAI, Anthropic, OpenRouter, Groq, "
                "Mistral or DeepSeek;\na model server you already run (Ollama, LM Studio, llama.cpp); "
                "or a free model it downloads and runs for you.\n"
                "Then run  localagent --doctor  to check.")
SUBCOMMANDS = ("setup", "models")                # product commands, handled before the flags
COMMANDS_EPILOG = ("commands:\n  localagent setup            guided first run: online service and key, "
                   "offline model, Docker\n  localagent models           local models: list, download NAME, "
                   "use NAME-or-FILE")
MENTION = re.compile(r"(?<!\S)@([^\s@]+)")       # "@src/app.py", not an e-mail address
COLORS = {"+": "32", "-": "31", "@": "36"}       # diff lines: green, red, cyan
FAILURE_WORDS = ("REJECTED", "ERROR", "DENIED", "NOT RUN", "NOT FINISHED")


class Approver:
    """Asks before anything runs. Answering "a" (always) saves a permission rule, like
    Claude Code's "Yes, and don't ask again"."""

    def __init__(self, read: Callable[[str], str] = input) -> None:
        self._read = read
        self.permissions: Permissions | None = None       # set once the toolbox exists

    def __call__(self, action: str) -> bool:
        try:
            answer = self._read(f"  allow: {action}? [y]es / [n]o / [a]lways: ").strip().lower()
        except EOFError:                                  # no keyboard (input ran out): no
            return False
        if answer in {"a", "always"} and isinstance(action, Approval) and self.permissions:
            rule = suggest_rule(action.tool, action.subject)
            saved = self.permissions.always_allow(rule)
            print(f"  allowed from now on: {rule}" + (f" (saved in {saved})" if saved else ""))
        return answer in {"y", "yes", "a", "always"}


def refuse(action: str) -> bool:
    """The approver of -p runs: nobody is there to ask, so anything not allowed is refused."""
    print(f"[refused (no chat to ask in): {action} - allow it in .localagent/settings.json "
          "or use --auto-approve]", file=sys.stderr)
    return False


def confirm(question: str, read: Callable[[str], str] = input) -> bool:
    """A yes/no question; no input (EOF) or Ctrl+C counts as no."""
    try:
        return read(question).strip().lower() in {"y", "yes"}
    except (EOFError, KeyboardInterrupt):
        print()
        return False


def add_model_flags(parser: argparse.ArgumentParser) -> None:
    """The flags every entry point shares (chat, server, evals): which model, where code runs."""
    parser.add_argument("--mode", choices=MODES,
                        help="auto: Gemini, the local model when offline (default); "
                             "online: Gemini only; offline: the local model only")
    parser.add_argument("--online", dest="mode", action="store_const", const="online",
                        help="same as --mode online")
    parser.add_argument("--offline", dest="mode", action="store_const", const="offline",
                        help="same as --mode offline")
    parser.add_argument("--backend", choices=sorted(BACKENDS),
                        help="model service type, chosen by hand (the mode is then ignored)")
    parser.add_argument("--model", help="model name for ollama/openai, e.g. qwen3-8b")
    parser.add_argument("--api-base", help="URL of an OpenAI-style server, e.g. http://host:8080/v1")
    parser.add_argument("--sandbox", choices=["local", "docker"],
                        help="where code runs: this machine or a Docker container")


def model_flags(args: argparse.Namespace) -> dict:
    """The values of those shared flags, as Config field names (None = not given)."""
    return {"mode": args.mode, "backend": args.backend, "model": args.model,
            "api_base": args.api_base, "sandbox": args.sandbox}


def build_parser() -> argparse.ArgumentParser:
    """Define the command-line flags."""
    parser = argparse.ArgumentParser(prog="localagent", description="A verifying coding agent",
                                     epilog=COMMANDS_EPILOG,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--version", action="version", version=f"localagent {__version__}")
    parser.add_argument("--provider", metavar="NAME[:MODEL]",
                        help="the online service: " + ", ".join(p.name for p in PROVIDERS)
                             + " - with a model if you like, e.g. openai:gpt-5-mini")
    parser.add_argument("-p", "--print", dest="prompt", metavar="REQUEST",
                        help="answer one request, print the answer and exit ('-' reads it from input)")
    add_model_flags(parser)
    parser.add_argument("--workspace", type=Path, help="project folder (default: current folder)")
    parser.add_argument("--num-ctx", type=int, help="history budget in tokens")
    parser.add_argument("--doctor", action="store_true", help="check the setup and exit")
    parser.add_argument("--resume", "-c", "--continue", dest="resume", action="store_true",
                        help="continue the latest conversation")
    parser.add_argument("--type-check", action="store_true", help="also verify with mypy")
    parser.add_argument("--auto-approve", action="store_true", help="never ask before running")
    parser.add_argument("--network", action="store_true",
                        help="allow internet inside the Docker sandbox (e.g. pip install)")
    parser.add_argument("--fallback", choices=["local", "ollama", "openai"],
                        help="with --backend gemini: local model to use when Gemini fails")
    parser.add_argument("--reasoning", help="reasoning effort for OpenAI-style servers, e.g. low")
    parser.add_argument("--allow-shell", action="store_true",
                        help="let the model run shell commands on this computer (asks each time)")
    parser.add_argument("--no-auto", action="store_true",
                        help="do not pick backend/sandbox/fallback automatically")
    return parser


def build_config(args: argparse.Namespace) -> Config:
    """Environment defaults overlaid with the flags the user actually gave."""
    config = overlay(Config.from_env(), **model_flags(args), workspace=args.workspace,
                     num_ctx=args.num_ctx, fallback_backend=args.fallback,
                     reasoning_effort=args.reasoning)
    config = replace(config, type_check=args.type_check, auto_approve=args.auto_approve,
                     sandbox_network=args.network, allow_host_shell=args.allow_shell)
    if args.provider:                              # --provider NAME[:MODEL]
        name, _, model = args.provider.partition(":")
        kept = config.provider_model if name.strip().lower() == config.provider else ""
        config = with_provider(config, name, model or kept)
    return config


def local_kind(config: Config) -> str:
    """The local model the modes would use: "openai" (a server answers at api_base), "ollama"
    (Ollama answers), "local" (llama.cpp can be started here) or "" (none). A kind chosen in
    `localagent setup` (``local_server``) is trusted; it is checked when it is used."""
    if config.local_server:
        return config.local_server
    if server_up(config.api_base):
        return "openai"
    if ollama_up(config.host):
        return "ollama"
    return "local" if can_start(config) else ""


def choose_mode(config: Config, mode: str, ctx_chosen: bool = False) -> tuple[Config, list[str]]:
    """Backend and offline backup for ``mode``; the notes explain the choice. Raises ValueError,
    saying what to do, when the mode cannot work on this computer."""
    if mode not in MODES:
        raise ValueError(f"unknown mode '{mode}': choose {', '.join(MODES)}")
    provider = find_provider(config.provider) or PROVIDERS[0]
    problem = online_problem(config)
    if mode == "online" and problem:
        raise ValueError(f"online mode needs a working online model: {problem}")
    if mode == "online" or (mode == "auto" and not problem):
        backup = "" if mode == "online" else (config.fallback_backend or local_kind(config))
        backend = "gemini" if provider.name == "gemini" else "hosted"
        config = replace(config, mode=mode, backend=backend, fallback_backend=backup)
        keyed = config.gemini_api_key if backend == "gemini" else config.provider_key
        notes = [f"mode {mode}: {online_name(config)} answers"
                 + (f" ({provider.key_env} is set)" if keyed else "")]
        if mode == "auto":
            notes.append("offline backup: " + (describe_local(config) if backup else
                                               f"none set up ({SETUP_HINT})"))
        return config, notes
    if not local_kind(config):
        raise ValueError(NO_LOCAL_MODEL if mode == "offline" else NOTHING_HELP)
    context = config.num_ctx if ctx_chosen else config.local_ctx
    config = replace(config, mode=mode, backend="local", fallback_backend="", num_ctx=context,
                     local_ctx=context)
    reason = "" if mode == "offline" else " (no online model is set up, so offline)"
    return config, [f"mode {mode}: the local model answers{reason} - {describe_local(config)}"]


def auto_configure(config: Config, args: argparse.Namespace) -> tuple[Config, list[str]]:
    """Choose what the user left open from what is available; explain each choice."""
    notes: list[str] = []
    ctx_chosen = args.num_ctx is not None or "LOCALAGENT_NUM_CTX" in os.environ
    if args.backend is None and "LOCALAGENT_BACKEND" not in os.environ:
        config, notes = choose_mode(config, config.mode, ctx_chosen)
    if (args.sandbox is None and "LOCALAGENT_SANDBOX" not in os.environ
            and sandbox_image_ready(config.sandbox_image)):
        config = replace(config, sandbox="docker")
        notes.append("sandbox: docker (Docker and the sandbox image are ready)")
    if (config.backend in ("gemini", "hosted") and not config.fallback_backend
            and args.fallback is None and config.mode != "online" and server_up(config.api_base)):
        config = replace(config, fallback_backend="openai")
        notes.append(f"fallback: the local model server at {config.api_base}")
    budgeted = gemini_history(config, chosen=ctx_chosen)
    if budgeted.num_ctx != config.num_ctx:
        config = budgeted
        notes.append(f"history budget: {config.num_ctx} tokens (online models handle long "
                     "conversations)")
    return config, notes


def online_name(config: Config) -> str:
    """The online model in a few words: "Gemini", "OpenAI gpt-5-mini", "m at https://..."."""
    provider = find_provider(config.provider) or PROVIDERS[0]
    if provider.name == "gemini":
        return "Gemini"
    if provider.name == "custom":
        return f"{config.provider_model} at {config.provider_base}"
    return f"{provider.title} {config.provider_model}"


def model_label(config: Config) -> str:
    """Short description of which model(s) will answer, for banners and scoreboards."""
    local = find_model_file(config)
    local_name = f"local:{local.stem}" if local else "local model"
    if config.local_server == "ollama":
        local_name = f"ollama:{config.model}"
    elif config.local_server == "openai" and config.api_base != Config.api_base:
        local_name = f"openai:{config.model}"          # your own server, not the downloaded model
    backup = "" if not config.fallback_backend else (
        f" > {local_name}" if config.fallback_backend in ("local", config.local_server)
        else f" > {config.fallback_backend}:{config.model}")
    if config.backend == "gemini":
        return "gemini:" + " > ".join(config.gemini_models + config.gemini_strong_models) + backup
    if config.backend == "hosted":
        return f"{config.provider}:{config.provider_model}{backup}"
    if config.backend == "local":
        return local_name
    return f"{config.backend}:{config.model}"


def read_request(read: Callable[[str], str] = input) -> str:
    """One request from the keyboard; supports ``\\`` line continuation and \"\"\" blocks."""
    first = read("you> ").lstrip("﻿")        # piped input may start with a byte-order mark
    if first.strip() == '"""':
        lines: list[str] = []
        while (line := read("... ")).strip() != '"""':
            lines.append(line)
        return "\n".join(lines).strip()
    parts = [first]
    while parts[-1].endswith("\\"):
        parts[-1] = parts[-1][:-1]
        parts.append(read("... "))
    return "\n".join(parts).strip()


def use_color() -> bool:
    """True when the terminal shows colours (and NO_COLOR is not set); switches them on in
    the Windows console, which needs to be told."""
    if os.environ.get("NO_COLOR") or not sys.stdout.isatty():
        return False
    if sys.platform == "win32":
        import ctypes
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)                # the screen
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        kernel32.SetConsoleMode(handle, mode.value | 0x0004)   # ENABLE_VIRTUAL_TERMINAL_PROCESSING
    return True


def make_printer(color: bool) -> Callable[[str], None]:
    """How progress lines are shown: diffs in green/red, tool calls in cyan, failures in red."""
    def show(line: str) -> None:
        if color:
            code = ""
            if line.startswith("     ") and line[5:6] in COLORS:
                code = COLORS[line[5]]
            elif line.startswith("-> "):
                code = "36"
            elif line.lstrip().startswith(FAILURE_WORDS):
                code = "31"
            line = f"\033[{code}m{line}\033[0m" if code else line
        print(line)
    return show


def handle_request(agent: Agent, toolbox: ToolBox, meter: Meter, text: str,
                   plan_only: bool = False, allowed: frozenset[str] | None = None,
                   show: Callable[[str], None] = print) -> str:
    """Run one request, show progress and the answer, then verify the whole workspace."""
    streamed: list[str] = []

    def stream(token: str) -> None:
        streamed.append(token)
        print(token, end="", flush=True)

    started = time.time()
    try:
        answer = agent.run(text, on_event=show, on_token=stream, plan_only=plan_only,
                           allowed=allowed)
    except LLMError as exc:
        agent.repair()
        print(f"\nerror: {exc}")
        return ""
    except KeyboardInterrupt:                      # Ctrl+C: stop this task, keep the chat
        agent.repair()
        print("\n[stopped - the conversation is kept; type your next request]")
        return ""
    shown = " ".join("".join(streamed).split())   # spacing does not matter when comparing
    if streamed:
        print()                                    # end the streamed line
    final = " ".join(answer.split())
    if final and not shown.endswith(final):
        print(answer)                              # e.g. a finish summary that was not streamed
    if not plan_only:
        problems = toolbox.check_workspace(since=started)      # only what changed just now
        print("[check of changed files: all OK]" if not problems
              else "[workspace check FAILED]\n" + "\n".join(problems))
        outcome = toolbox.run_hook("after_request")      # e.g. your test suite
        if outcome is not None:
            print(f"[after_request hook: exit code {outcome[0]}]\n{outcome[1].strip()}")
    who = getattr(agent.backend, "last_model", "")
    print(f"[{meter.summary()}" + (f" | {who}" if who else "") + "]")
    return answer


def attach_files(text: str, root: Path, limit: int) -> tuple[str, list[str]]:
    """``@path`` mentions of workspace files: their contents are added to the request, which
    saves the model a read_file call. Unknown paths stay as typed; ``limit`` caps the size."""
    attached: list[str] = []
    parts: list[str] = []
    total = 0
    for match in MENTION.finditer(text):
        name = match.group(1).rstrip(".,;:!?)\"'")
        try:
            path = resolve_inside(root, name)
        except SandboxError:
            continue
        if name in attached or not path.is_file() or looks_binary(path):
            continue
        content = path.read_text(encoding="utf-8-sig", errors="replace")
        if total + len(content) > limit:
            continue                               # too big for the context: the model reads it
        total += len(content)
        attached.append(name)
        parts.append(f"Contents of {name} (attached by the user):\n```\n{content}\n```")
    return (text + "\n\n" + "\n\n".join(parts) if parts else text), attached


def remember(note: str, root: Path) -> Path:
    """Add ``note`` to AGENT.md under "## Notes" (both are created when missing)."""
    path = root / "AGENT.md"
    text, style = read_text(path) if path.is_file() else ("# Project notes\n", ("\n", False))
    if "## Notes" not in text:
        text = text.rstrip("\n") + "\n\n## Notes\n"
    start = text.index("## Notes")
    end = text.find("\n## ", start + 1)                # the next section, if any
    end = len(text) if end == -1 else end
    text = text[:end].rstrip("\n") + f"\n- {note}\n" + text[end:]
    write_text(path, text, style)
    return path


def sessions_folder(config: Config) -> Path:
    """This project's conversation logs: <project>/.localagent/sessions (an absolute state_dir wins)."""
    return config.workspace / config.state_dir / "sessions"


class Chat:
    """The interactive loop and its commands. Holds what a command may change: the model
    setup (/model), the conversation (/clear, /resume), and output waiting to be sent (!)."""

    def __init__(self, config: Config, args: argparse.Namespace, backend: ChatBackend,
                 toolbox: ToolBox, meter: Meter) -> None:
        self.config, self.backend, self.toolbox, self.meter = config, backend, toolbox, meter
        self.ctx_chosen = args.num_ctx is not None or "LOCALAGENT_NUM_CTX" in os.environ
        self.root = config.workspace.resolve()
        self.log = SessionLog(sessions_folder(config))
        self.agent = Agent(config, backend, toolbox, meter, self.log)
        self.checkpoints = Checkpoints(config.workspace)
        self.commands = load_commands(config.workspace)  # custom /commands (.localagent/commands)
        self.show = make_printer(use_color())
        self.pending: list[str] = []                    # output of !commands, sent next time
        self.help = HELP + "".join(f"\n  /{name:<16} {(text.strip().splitlines() or [''])[0][:60]}"
                                   for name, text in sorted(self.commands.items()))
        if args.resume:
            previous = latest_session(sessions_folder(config))
            if previous is not None and previous != self.log.path:
                self.agent.restore(load_session(previous))
                print(f"resumed {previous.name}")

    def run(self, text: str, plan_only: bool = False, allowed: frozenset[str] | None = None) -> str:
        """One request: earlier !command output and @file contents go with it; the files it
        changes become one /undo step."""
        label = (text.splitlines() or [""])[0][:60]          # how /undo names this step
        if self.pending:
            text = "\n\n".join(self.pending) + "\n\nMy request: " + text
            self.pending = []
        limit = int(self.config.num_ctx * 0.7) * 4 // 3      # attachments: a third of the budget
        text, attached = attach_files(text, self.root, limit)
        if attached:
            print(f"[attached: {', '.join(attached)}]")
        message = self.checkpoints.take(label)
        if message:
            print(f"[{message}]")
        try:
            return handle_request(self.agent, self.toolbox, self.meter, text, plan_only, allowed,
                                  self.show)
        finally:
            self.checkpoints.commit()

    def model_status(self) -> list[str]:
        """What /model shows: the mode, who answers, and what online and offline would use."""
        problem = online_problem(self.config)
        last = getattr(self.backend, "last_model", "")
        return [f"mode: {self.config.mode} ({MODE_TEXT.get(self.config.mode, '')})",
                f"models now: {model_label(self.config)}" + (f"; last answer from {last}" if last else ""),
                f"online:  {online_name(self.config)}, " + (problem or "key set"),
                f"offline: {describe_local(self.config)}",
                "switch with /model online, /model offline or /model auto "
                "(other services and models: localagent setup)"]

    def switch(self, mode: str) -> None:
        """/model <mode>: build the new model setup, start the local model if needed, swap it in.
        The conversation and the files stay; only who answers changes."""
        try:
            config, notes = choose_mode(replace(self.config, fallback_backend=""), mode,
                                        self.ctx_chosen)
            config = gemini_history(config, chosen=self.ctx_chosen)
            backend = make_backend(config)
            if isinstance(backend, LocalModel):
                backend.ensure_ready()                  # start llama.cpp now, not mid-task
        except (ValueError, LLMError) as exc:
            print(f"cannot switch to {mode}: {exc}")
            return
        except KeyboardInterrupt:
            print("\n[switch stopped; nothing changed]")
            return
        if mode == "online" and stop_local_servers():
            notes.append("stopped the local model server (its memory is free again)")
        self.config, self.backend = config, backend
        self.agent.set_backend(backend, config)
        for note in notes:
            print(f"  {note}")
        print(f"[now {mode}: {model_label(config)}]")

    def resume(self, choice: str) -> None:
        """/resume: list this project's conversations (newest first), or continue number N."""
        sessions = list_sessions(sessions_folder(self.config))[::-1][:10]
        if not choice:
            for number, path in enumerate(sessions, start=1):
                mark = "  (this conversation)" if path == self.log.path else ""
                print(f"  {number}. {session_title(path)}{mark}")
            print("continue one with /resume <number>" if sessions else "no earlier conversations")
            return
        if not choice.isdigit() or not 1 <= int(choice) <= len(sessions):
            print(f"choose a number from 1 to {len(sessions)} (see /resume)")
            return
        picked = sessions[int(choice) - 1]
        if picked == self.log.path:
            print("that is this conversation")
            return
        self.agent.reset()
        self.agent.restore(load_session(picked))
        print(f"continued {picked.name} ({len(self.agent.messages) - 1} messages)")

    def shell(self, command: str) -> None:
        """``!command``: run it yourself in the project folder (you typed it, so no question);
        its output is shown, and sent along with your next request."""
        code, output = run_command(shell_argv(command), self.root, 600, self.config.max_output_chars)
        print(output.rstrip())
        print(f"[exit code {code}; the output goes with your next request]")
        self.pending.append(f"I ran `{command}` myself (exit code {code}). Output:\n{output.strip()}")

    def command(self, text: str) -> bool:
        """Act on one line; False means quit."""
        command, _, rest = text.partition(" ")
        rest = rest.strip()
        name = command.lower()
        if name in ("/quit", "/exit"):
            return False
        if name == "/help":
            print(self.help)
        elif name in ("/reset", "/clear"):
            self.agent.reset()
            print("conversation cleared (files are unchanged)")
        elif name == "/undo":
            print(self.checkpoints.undo())
        elif name == "/stats":
            print(self.meter.summary())
            for line in getattr(self.backend, "status", lambda: [])():
                print(line)                        # daily quota use per Gemini model
        elif name == "/compact":
            try:
                print(self.agent.compact())
            except (LLMError, KeyboardInterrupt) as exc:
                print(f"compaction stopped: {exc}")
        elif name == "/init":
            self.run(INIT_PROMPT, allowed=INIT_TOOLS)
            notes_file = self.root / "AGENT.md"
            print(f"[AGENT.md {'written' if notes_file.is_file() else 'was NOT written - try /init again'}]")
            self.agent.refresh_system()            # the new notes take effect immediately
        elif name == "/plan":
            if not rest:
                print("usage: /plan <what you want done>")
            elif self.run(rest, plan_only=True) and confirm("Carry out this plan? [y/N] "):
                self.run(PLAN_FOLLOW_UP)
        elif name in ("/model", "/online", "/offline", "/auto"):
            wanted = rest.lower() if name == "/model" else name[1:]
            if wanted:
                self.switch(wanted)
            else:
                print("\n".join(self.model_status()))
        elif name == "/resume":
            self.resume(rest)
        elif name == "/permissions":
            rules = self.toolbox.permissions
            print("allow: " + (", ".join(rules.allow) or "(none)"))
            print("deny:  " + (", ".join(rules.deny) or "(none)"))
            print(f"rules live in {USER_SETTINGS} and {self.root / PROJECT_SETTINGS}")
        elif name == "/doctor":
            print("\n".join(run_doctor(self.config)[1]))
        elif name.startswith("/") and name[1:] in self.commands:
            self.run(expand_command(self.commands[name[1:]], rest))
        elif name.startswith("/"):
            print(f"unknown command {command} - type /help (nothing was sent to the model)")
        elif text.startswith("!") and text[1:].strip():
            self.shell(text[1:].strip())
        elif text.startswith("#") and text[1:].strip() and "\n" not in text:
            print(f"[noted in {remember(text[1:].strip(), self.root)}]")
            self.agent.refresh_system()
        else:
            self.run(text)
        return True

    def loop(self, notes: list[str]) -> int:
        """Show the banner, then read and act on lines until /quit or end of input."""
        print(f"localagent {__version__} | {self.config.mode} | {model_label(self.config)} | "
              f"sandbox={self.config.sandbox} | folder={self.root}")
        for note in notes + self.toolbox.notes:
            print(f"  {note}")
        print(self.help)
        while True:
            try:
                text = read_request()
            except (EOFError, KeyboardInterrupt):
                print()
                return 0
            if not text:
                continue
            try:
                if not self.command(text):
                    return 0
            except KeyboardInterrupt:              # e.g. Ctrl+C during a !command
                print("\n[stopped]")


def run_once(config: Config, backend: ChatBackend, toolbox: ToolBox, meter: Meter,
             prompt: str) -> int:
    """``-p``: one request without the chat. The answer goes to standard output and progress
    to standard error, so scripts can use the answer; anything that needs a question is refused."""
    text = sys.stdin.read() if prompt == "-" else prompt
    root = config.workspace.resolve()
    text, _ = attach_files(text.strip(), root, int(config.num_ctx * 0.7) * 4 // 3)
    agent = Agent(config, backend, toolbox, meter, SessionLog(sessions_folder(config)))
    started = time.time()
    try:
        answer = agent.run(text, on_event=lambda line: print(line, file=sys.stderr))
    except LLMError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(answer)
    problems = toolbox.check_workspace(since=started)
    if problems:
        print("[workspace check FAILED]\n" + "\n".join(problems), file=sys.stderr)
    print(f"[{meter.summary()}]", file=sys.stderr)
    return 1 if problems else 0


def run_subcommand(name: str, words: list[str]) -> int:
    """`localagent setup` and `localagent models ...` (see models.py); no chat is started."""
    try:
        apply_env(load_settings(Path(os.environ.get("LOCALAGENT_WORKSPACE", "."))))
        config = Config.from_env()
    except ValueError as exc:
        print(f"setup problem: {exc}")
        return 2
    try:
        return run_setup(config) if name == "setup" else run_models(words, config)
    except KeyboardInterrupt:                      # a download stopped: it continues next time
        print("\n[stopped - run the same command again to continue]")
        return 130


def guided_first_run(config: Config, args: argparse.Namespace,
                     read: Callable[[str], str] = input) -> bool:
    """The very first start - no model set up, a person at the keyboard - offers the guided
    setup at once instead of an error. True when it ran and made a model ready."""
    interactive = sys.stdin.isatty() and sys.stdout.isatty()
    if (not interactive or args.prompt is not None or args.doctor or args.no_auto
            or args.backend is not None or not online_problem(config) or local_kind(config)):
        return False
    print("No model is set up yet. The guided setup takes a minute: an online service with your "
          "key (Gemini's is free),\na model server you already run, or a free model downloaded "
          "for you.")
    try:
        answer = read("Start it now? [Y/n] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    return answer in ("", "y", "yes") and run_subcommand("setup", []) == 0


def main(argv: list[str] | None = None) -> int:
    """Entry point: choose the setup, build the components, then chat (or answer once)."""
    for stream in (sys.stdout, sys.stderr):        # a character the console cannot show must
        if hasattr(stream, "reconfigure"):         # not crash the program (piped output on Windows)
            stream.reconfigure(errors="replace")
    words = sys.argv[1:] if argv is None else argv
    if words and words[0] in SUBCOMMANDS:
        return run_subcommand(words[0], words[1:])
    args = build_parser().parse_args(words)
    workspace = args.workspace or Path(os.environ.get("LOCALAGENT_WORKSPACE", "."))
    try:
        settings = load_settings(workspace)        # its "env" applies before the config is read
        apply_env(settings)
        config = build_config(args)
        if guided_first_run(config, args):         # the setup saved new settings: read them again
            settings = load_settings(workspace)
            apply_env(settings)
            config = build_config(args)
    except ValueError as exc:                      # e.g. broken settings.json, a typo in a variable
        print(f"setup problem: {exc}")
        return 2
    notes: list[str] = []
    try:
        if not args.no_auto:
            config, notes = auto_configure(config, args)
    except ValueError as exc:                      # e.g. no model at all yet
        if not args.doctor:
            print(f"setup problem: {exc}")
            return 2
        notes = [f"FAIL  {exc}"]
    if args.doctor:
        healthy, report = run_doctor(config)
        print("\n".join(notes + report))
        return 0 if healthy else 1
    try:
        backend = make_backend(config)
    except ValueError as exc:                      # e.g. missing key
        print(f"setup problem: {exc}")
        return 2
    approver = Approver()
    approve: Callable[[str], bool] = approver
    if config.auto_approve:
        approve = lambda _a: True                  # noqa: E731 (a one-line answer)
    elif args.prompt is not None:
        approve = refuse
    meter = Meter()
    toolbox = ToolBox(config, approve, settings)
    approver.permissions = toolbox.permissions
    try:
        if args.prompt is not None:
            return run_once(config, backend, toolbox, meter, args.prompt)
        return Chat(config, args, backend, toolbox, meter).loop(notes)
    finally:
        toolbox.close()                            # stops the sandbox and MCP servers
        stop_local_servers()                       # and a local model this run started
