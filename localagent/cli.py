"""Command-line interface: the chat prompt around the Agent (like Claude Code's terminal UI).

Start it inside a project folder:  localagent        (or: python -m localagent)
Whatever you do not choose with flags is chosen for you from what this computer has:
Gemini when a key is set, the Docker sandbox when it is ready, and a running local
model server as the last fallback (``--no-auto`` turns this off).
"""
from __future__ import annotations

import argparse
import os
import sys
import time
import urllib.request
from dataclasses import replace
from pathlib import Path
from typing import Callable

from .agent import Agent
from .backends import BACKENDS, make_backend
from .checkpoint import Checkpoints
from .config import Config, gemini_history, overlay
from .doctor import run_doctor
from .llm import ChatBackend, LLMError
from .meter import Meter
from .runners import sandbox_image_ready
from .session import SessionLog, latest_session, load_session
from .settings import Permissions, expand_command, load_commands, load_settings, suggest_rule
from .tools import Approval, ToolBox

HELP = """Commands:
  /plan <request>  look first, show a plan, then carry it out if you agree
  /init            write AGENT.md (project notes the agent reads every session)
  /compact         summarise the conversation to save tokens
  /undo            revert the file changes of the last request
  /reset           forget the conversation (files stay)
  /stats           calls, tokens, time and today's quota use
  /help   /quit    (your own commands: .localagent/commands/<name>.md -> /<name>)
Multi-line input: end a line with \\ to continue it, or put \"\"\" alone on a line to start and end a block.
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
BUILT_IN = {"/quit", "/help", "/reset", "/undo", "/stats", "/compact", "/init", "/plan"}


class Approver:
    """Asks before anything runs. Answering "a" (always) saves a permission rule, like
    Claude Code's "Yes, and don't ask again"."""

    def __init__(self, read: Callable[[str], str] = input) -> None:
        self._read = read
        self.permissions: Permissions | None = None       # set once the toolbox exists

    def __call__(self, action: str) -> bool:
        answer = self._read(f"  allow: {action}? [y]es / [n]o / [a]lways: ").strip().lower()
        if answer in {"a", "always"} and isinstance(action, Approval) and self.permissions:
            rule = suggest_rule(action.tool, action.subject)
            saved = self.permissions.always_allow(rule)
            print(f"  allowed from now on: {rule}" + (f" (saved in {saved})" if saved else ""))
        return answer in {"y", "yes", "a", "always"}


def add_model_flags(parser: argparse.ArgumentParser) -> None:
    """The flags every entry point shares (chat, server, evals): which model, where code runs."""
    parser.add_argument("--backend", choices=sorted(BACKENDS), help="model service type")
    parser.add_argument("--model", help="model name for ollama/openai, e.g. qwen3-8b")
    parser.add_argument("--api-base", help="URL of an OpenAI-style server, e.g. http://host:8080/v1")
    parser.add_argument("--sandbox", choices=["local", "docker"],
                        help="where code runs: this machine or a Docker container")


def model_flags(args: argparse.Namespace) -> dict:
    """The values of those shared flags, as Config field names (None = not given)."""
    return {"backend": args.backend, "model": args.model, "api_base": args.api_base,
            "sandbox": args.sandbox}


def build_parser() -> argparse.ArgumentParser:
    """Define the command-line flags."""
    parser = argparse.ArgumentParser(prog="localagent", description="A verifying coding agent")
    add_model_flags(parser)
    parser.add_argument("--workspace", type=Path, help="project folder (default: current folder)")
    parser.add_argument("--num-ctx", type=int, help="context window size in tokens")
    parser.add_argument("--doctor", action="store_true", help="check the setup and exit")
    parser.add_argument("--resume", action="store_true", help="continue the latest session")
    parser.add_argument("--type-check", action="store_true", help="also verify with mypy")
    parser.add_argument("--auto-approve", action="store_true", help="never ask before running")
    parser.add_argument("--network", action="store_true",
                        help="allow internet inside the Docker sandbox (e.g. pip install)")
    parser.add_argument("--fallback", choices=["ollama", "openai"],
                        help="with --backend gemini: local server to use when Gemini fails")
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
    return replace(config, type_check=args.type_check, auto_approve=args.auto_approve,
                   sandbox_network=args.network, allow_host_shell=args.allow_shell)


def server_up(api_base: str) -> bool:
    """True when an OpenAI-style model server answers at ``api_base`` within a second."""
    try:
        with urllib.request.urlopen(api_base + "/models", timeout=1) as response:
            return response.status == 200
    except (OSError, ValueError):
        return False


def auto_configure(config: Config, args: argparse.Namespace) -> tuple[Config, list[str]]:
    """Choose what the user left open from what is available; explain each choice."""
    notes: list[str] = []
    if args.backend is None and "LOCALAGENT_BACKEND" not in os.environ and config.gemini_api_key:
        config = replace(config, backend="gemini")
        notes.append("backend: gemini (GEMINI_API_KEY is set)")
    if (args.sandbox is None and "LOCALAGENT_SANDBOX" not in os.environ
            and sandbox_image_ready(config.sandbox_image)):
        config = replace(config, sandbox="docker")
        notes.append("sandbox: docker (Docker and the sandbox image are ready)")
    if (config.backend == "gemini" and not config.fallback_backend and args.fallback is None
            and server_up(config.api_base)):
        config = replace(config, fallback_backend="openai")
        notes.append(f"fallback: the local model server at {config.api_base}")
    budgeted = gemini_history(config, chosen=args.num_ctx is not None)
    if budgeted.num_ctx != config.num_ctx:
        config = budgeted
        notes.append(f"history budget: {config.num_ctx} tokens (Gemini handles long conversations)")
    return config, notes


def model_label(config: Config) -> str:
    """Short description of which model(s) will answer, for banners and scoreboards."""
    if config.backend == "gemini":
        chain = " > ".join(config.gemini_models + config.gemini_strong_models)
        return f"gemini:{chain}" + (f" > {config.fallback_backend}:{config.model}"
                                    if config.fallback_backend else "")
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


def handle_request(agent: Agent, toolbox: ToolBox, meter: Meter, text: str,
                   plan_only: bool = False, allowed: frozenset[str] | None = None) -> str:
    """Run one request, show progress and the answer, then verify the whole workspace."""
    streamed: list[str] = []

    def show(token: str) -> None:
        streamed.append(token)
        print(token, end="", flush=True)

    started = time.time()
    try:
        answer = agent.run(text, on_event=print, on_token=show, plan_only=plan_only,
                           allowed=allowed)
    except LLMError as exc:
        agent.repair()
        print(f"\nerror: {exc}")
        return ""
    except KeyboardInterrupt:                      # Ctrl+C: stop this task, keep the chat
        agent.repair()
        print("\n[stopped - the conversation is kept; type your next request]")
        return ""
    shown = "".join(streamed).strip()
    if streamed:
        print()                                    # end the streamed line
    if answer.strip() and answer.strip() != shown:
        print(answer)                              # e.g. a finish summary that was not streamed
    if not plan_only:
        problems = toolbox.check_workspace(since=started)      # only what changed just now
        print("[check of changed files: all OK]" if not problems
              else "[workspace check FAILED]\n" + "\n".join(problems))
    if not plan_only:
        outcome = toolbox.run_hook("after_request")      # e.g. your test suite
        if outcome is not None:
            print(f"[after_request hook: exit code {outcome[0]}]\n{outcome[1].strip()}")
    print(f"[{meter.summary()}]")
    return answer


def snapshot(checkpoints: Checkpoints) -> None:
    """Snapshot the folder for /undo; the one-time "too large" message is shown if it comes."""
    message = checkpoints.take()
    if message:
        print(f"[{message}]")


def sessions_folder(config: Config) -> Path:
    """This project's conversation logs: <project>/.localagent/sessions (an absolute state_dir wins)."""
    return config.workspace / config.state_dir / "sessions"


def chat(config: Config, args: argparse.Namespace, backend: ChatBackend, toolbox: ToolBox,
         meter: Meter, notes: list[str]) -> int:
    """The interactive loop: read a request or command, act on it, repeat."""
    log = SessionLog(sessions_folder(config))
    agent = Agent(config, backend, toolbox, meter, log)
    if args.resume:
        previous = latest_session(sessions_folder(config))
        if previous is not None:
            agent.restore(load_session(previous))
            print(f"resumed {previous.name}")
    checkpoints = Checkpoints(config.workspace)
    commands = load_commands(config.workspace)     # custom /commands from .localagent/commands
    help_text = HELP + "".join(
        f"\n  /{name:<15} {(text.strip().splitlines() or [''])[0][:60]}"
        for name, text in sorted(commands.items()))
    print(f"localagent | {model_label(config)} | sandbox={config.sandbox} | "
          f"folder={config.workspace.resolve()}")
    for note in notes + toolbox.notes:
        print(f"  {note}")
    print(help_text)
    while True:
        try:
            text = read_request()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        command, _, rest = text.partition(" ")
        if not text:
            continue
        if command == "/quit":
            return 0
        if command == "/help":
            print(help_text)
        elif command == "/reset":
            agent.reset()
            print("conversation cleared")
        elif command == "/undo":
            print(checkpoints.undo())
        elif command == "/stats":
            print(meter.summary())
            for line in getattr(backend, "status", lambda: [])():
                print(line)                        # daily quota use per Gemini model
        elif command == "/compact":
            try:
                print(agent.compact())
            except (LLMError, KeyboardInterrupt) as exc:
                print(f"compaction stopped: {exc}")
        elif command == "/init":
            snapshot(checkpoints)
            handle_request(agent, toolbox, meter, INIT_PROMPT, allowed=INIT_TOOLS)
            notes_file = config.workspace / "AGENT.md"
            print(f"[AGENT.md {'written' if notes_file.is_file() else 'was NOT written - try /init again'}]")
            agent.refresh_system()                 # the new notes take effect immediately
        elif command == "/plan":
            if not rest.strip():
                print("usage: /plan <what you want done>")
                continue
            plan = handle_request(agent, toolbox, meter, rest.strip(), plan_only=True)
            if plan and input("Carry out this plan? [y/N] ").strip().lower() in {"y", "yes"}:
                snapshot(checkpoints)
                handle_request(agent, toolbox, meter, PLAN_FOLLOW_UP)
        elif command.startswith("/") and command[1:].lower() in commands:
            request = expand_command(commands[command[1:].lower()], rest.strip())
            snapshot(checkpoints)
            handle_request(agent, toolbox, meter, request)
        elif command.startswith("/") and command not in BUILT_IN:
            print(f"unknown command {command} - type /help (nothing was sent to the model)")
        else:
            snapshot(checkpoints)
            handle_request(agent, toolbox, meter, text)


def main(argv: list[str] | None = None) -> int:
    """Entry point: choose the setup, build the components, then chat."""
    for stream in (sys.stdout, sys.stderr):        # a character the console cannot show must
        if hasattr(stream, "reconfigure"):         # not crash the program (piped output on Windows)
            stream.reconfigure(errors="replace")
    args = build_parser().parse_args(argv)
    config = build_config(args)
    notes: list[str] = []
    if not args.no_auto:
        config, notes = auto_configure(config, args)
    if args.doctor:
        healthy, report = run_doctor(config)
        print("\n".join(notes + report))
        return 0 if healthy else 1
    try:
        settings = load_settings(config.workspace)
        backend = make_backend(config)
    except ValueError as exc:                      # e.g. missing key, broken settings.json
        print(f"setup problem: {exc}")
        return 2
    approver = Approver()
    meter = Meter()
    toolbox = ToolBox(config, (lambda _a: True) if config.auto_approve else approver, settings)
    approver.permissions = toolbox.permissions
    try:
        return chat(config, args, backend, toolbox, meter, notes)
    finally:
        toolbox.close()                            # stops the sandbox and MCP servers
