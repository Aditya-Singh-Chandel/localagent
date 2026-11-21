"""`localagent setup`: the guided first run, in three short steps.

1/3  Online model: Google Gemini (its key is free), another service with your own key (OpenAI,
     Anthropic, OpenRouter, Groq, Mistral, DeepSeek, any OpenAI-compatible address) - or none.
2/3  Offline model: a model server you already run (Ollama, LM Studio, llama.cpp, vLLM, Jan), a
     free model this program downloads and runs for you, a server at another address - or none.
3/3  Sandbox: whether Docker is ready to run the agent's code in a sealed box (optional).

Every key is checked before it is saved (listing a service's models costs no quota). Choices go
into your user settings file (``~/.localagent/settings.json``, its "env" part), so they apply in
every folder; a real environment variable still wins. Enter accepts the suggestion shown in
[brackets]; when input ends (Ctrl+Z, a script), nothing more is changed.
"""
from __future__ import annotations

import getpass
import os
import sys
from functools import partial
from pathlib import Path
from typing import Callable

from .config import Config, online_problem
from .fetch import DownloadError, Progress
from .localserver import can_start, find_llama_server, find_model_file, running_local
from .models import (GiB, CatalogModel, Say, install_llama, install_model, progress_printer,
                     recommend, sandbox_hint, total_memory_gb)
from .providers import (CUSTOM_KEY, PROVIDERS, LocalServer, Provider, chat_models, check_key,
                        find_provider, key_headers, pick_local_model, pick_model,
                        scan_local_servers, server_models)
from .runners import docker_available, sandbox_image_ready
from .settings import USER_SETTINGS, read_json, save_user_env

Scan = Callable[[tuple[LocalServer, ...]], list[tuple[LocalServer, list[str]]]]
SHOWN_MODELS = 8                                   # models listed as examples


def read_secret(question: str) -> str:
    """A key typed by a person is not shown (getpass). Keys piped in by a script are read like
    any answer: Windows' getpass reads only the keyboard and would wait forever."""
    return getpass.getpass(question) if sys.stdin.isatty() else input(question)


def run_setup(config: Config, ask: Callable[[str], str] = input,
              secret: Callable[[str], str] = read_secret, say: Say = print,
              settings_file: Path = USER_SETTINGS, progress: Progress | None = None,
              scan: Scan = scan_local_servers) -> int:
    """`localagent setup`. Returns 0 when at least one model (online or offline) is ready."""
    wizard = Wizard(config, ask, secret, say, settings_file, progress or progress_printer(say), scan)
    say("localagent setup: three short steps; Enter accepts the suggestion in [brackets].")
    say("\n1/3  Online model: fast and strong; needs the internet and an account (Gemini's is free)")
    online = wizard.online()
    say("\n2/3  Offline model: runs on this computer - private, no quota, slower")
    offline = wizard.offline()
    say("\n3/3  Sandbox: Docker runs the agent's code in a sealed box (optional)")
    wizard.sandbox()
    say("")
    if online and offline:
        say("Ready, online and offline. In a project folder, type: localagent   (/model switches)")
    elif online:
        say("Ready (online only). For offline work, run localagent setup again later.")
    elif offline:
        say("Ready (offline only). In a project folder, type: localagent")
    else:
        say("No model is ready yet. Run localagent setup again (a Gemini key is the quickest start).")
    say("Check everything at any time with: localagent --doctor")
    return 0 if online or offline else 1


class Wizard:
    """The questions of `localagent setup`, and what the answers save."""

    def __init__(self, config: Config, ask: Callable[[str], str], secret: Callable[[str], str],
                 say: Say, settings_file: Path, progress: Progress, scan: Scan) -> None:
        self.config = config
        self._ask = ask
        self._secret = secret
        self.say = say
        self.settings_file = settings_file
        self.progress = progress
        self.scan = scan
        saved = read_json(settings_file).get("env", {})
        self._real = {name for name, value in os.environ.items() if saved.get(name) != value}

    # ---- asking ---------------------------------------------------------------------
    def answer(self, question: str) -> str | None:
        """The typed answer without surrounding spaces, or None when input has ended. A
        byte-order mark is dropped: Windows PowerShell puts one before piped answers."""
        try:
            return self._ask(question).replace("\ufeff", "").strip()
        except (EOFError, KeyboardInterrupt):
            return None

    def hidden(self, question: str) -> str:
        """A typed secret, not shown on the screen; "" when skipped or input has ended."""
        try:
            return self._secret(question).replace("\ufeff", "").strip()
        except (EOFError, KeyboardInterrupt):
            return ""

    def yes(self, question: str) -> bool:
        """A [Y/n] question: Enter or "y" is yes; input that has ended is no."""
        typed = self.answer(question)
        return typed is not None and typed.lower() in ("", "y", "yes")

    def choose(self, labels: list[str], default: int) -> int | None:
        """Numbered choices; the number picked (from 1), or None when input has ended."""
        for number, label in enumerate(labels, 1):
            self.say(f"  {number}) {label}")
        while True:
            typed = self.answer(f"  Choose 1-{len(labels)} [{default}]: ")
            if typed is None:
                return None
            if not typed:
                return default
            if typed.isdigit() and 1 <= int(typed) <= len(labels):
                return int(typed)
            self.say(f"  please type a number from 1 to {len(labels)}")

    def save(self, name: str, value: str | None) -> None:
        """Remember a setting in the user settings file (None forgets it) and use it at once."""
        save_user_env(name, value, self.settings_file)
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value
        if name in self._real:
            self.say(f"  note  {name} is also set in your environment, and that one wins: remove "
                     "it there for this choice to apply")

    # ---- step 1: online ---------------------------------------------------------------
    def online(self) -> bool:
        """Step 1: the online service, its key (checked, or asked for) and its model."""
        current = find_provider(self.config.provider) or PROVIDERS[0]
        ready = not online_problem(self.config)
        labels = []
        for provider in PROVIDERS:
            keyed = provider.key_env != CUSTOM_KEY and bool(os.environ.get(provider.key_env))
            mark = "  [in use]" if ready and provider == current else "  [key found]" if keyed else ""
            labels.append(f"{provider.title:<34} {provider.note}{mark}")
        labels.append("skip - keep what is set up now" if ready else "skip - no online model")
        keyed_first = next((number for number, provider in enumerate(PROVIDERS, 1)
                            if provider.key_env != CUSTOM_KEY and os.environ.get(provider.key_env)), 1)
        choice = self.choose(labels, PROVIDERS.index(current) + 1 if ready else keyed_first)
        if choice is None or choice > len(PROVIDERS):
            self.say("  skipped" + ("" if ready else ": the agent works offline only"))
            return ready
        return self.use_provider(PROVIDERS[choice - 1])

    def use_provider(self, provider: Provider) -> bool:
        """Check (or ask for) the key of ``provider``, choose its model, save the choice."""
        base = provider.base
        if provider.name == "custom":
            known = self.config.provider_base if self.config.provider == "custom" else ""
            typed = self.answer(f"  The service's address, usually ending in /v1 [{known}]: ")
            base = (typed or known).rstrip("/") if typed is not None else ""
            if not base:
                self.say("  skipped: no address given")
                return False
        key = os.environ.get(provider.key_env, "").strip()
        pasted = False
        if key:
            self.say(f"  ok    {provider.key_env} is set; checking it")
        else:
            if provider.key_page:
                self.say(f"  Get a key at {provider.key_page}")
            key = self.hidden("  Paste the key (it stays hidden; Enter skips): ")
            pasted = bool(key)
            if not key and provider.name != "custom":      # a custom service may need no key
                self.say("  skipped: without a key there is no online model")
                return False
        state, offered, detail = check_key(base, key_headers(provider, key))
        if state == "rejected":
            again = f" Make a new key at {provider.key_page}" if provider.key_page else ""
            self.say(f"  FAIL  {provider.title} refused the key ({detail}); nothing was saved.{again}")
            return False
        if state == "unreachable":
            self.say(f"  note  {provider.title} cannot be reached right now ({detail}); the choice "
                     "is saved unchecked")
        else:
            self.say(f"  ok    the key works ({len(offered)} models offered)")
        model = ""
        if provider.name != "gemini":                  # Gemini uses its own chain of models
            model = self.choose_model(provider, offered)
            if not model:
                self.say("  skipped: no model chosen, nothing was saved")
                return False
        if pasted:
            self.save(provider.key_env, key)
        self.save("LOCALAGENT_PROVIDER", provider.name)
        self.save("LOCALAGENT_PROVIDER_MODEL", model or None)
        self.save("LOCALAGENT_PROVIDER_BASE", base if provider.name == "custom" else None)
        chosen = provider.title + (f", model {model}" if model else "")
        self.say(f"  ok    online model: {chosen} (saved in {self.settings_file})")
        return True

    def choose_model(self, provider: Provider, offered: list[str]) -> str:
        """Suggest a model (a good one for this agent, when offered) and let the user type another."""
        current = self.config.provider_model if self.config.provider == provider.name else ""
        suggestion = current if current and (not offered or current in offered) else \
            pick_model(provider, offered)
        if not suggestion and provider.suggested:
            suggestion = provider.suggested[0]
        if offered:
            examples = [suggestion] + [name for name in chat_models(offered) if name != suggestion]
            self.say(f"  {len(offered)} models offered, for example: "
                     + ", ".join(examples[:SHOWN_MODELS]))
        typed = self.answer(f"  Model [{suggestion}]: ")
        if typed is None:
            return ""
        model = typed or suggestion
        if model and offered and model not in offered:
            self.say(f"  note  {model} is not in {provider.title}'s list; it is used anyway")
        return model

    # ---- step 2: offline --------------------------------------------------------------
    def offline(self) -> bool:
        """Step 2: a local server that runs now, the downloaded model (or a download), or a
        server at another address."""
        found = self.scan(self.own_servers())
        program, model_file = find_llama_server(self.config), find_model_file(self.config)
        choices: list[tuple[str, Callable[[], bool]]] = []
        for server, models in found:
            listed = ", ".join(models[:3]) + (f" and {len(models) - 3} more" if len(models) > 3 else "")
            choices.append((f"{server.title} at {server.base} ({listed or 'no model loaded yet'})",
                            partial(self.use_server, server, models)))
        if program is not None and model_file is not None:
            choices.append((f"the downloaded model {model_file.name} (started when needed)",
                            self.use_downloaded))
        else:
            memory = total_memory_gb()
            pick = recommend(memory)
            choices.append((f"download a free model for me: {pick.name}, {pick.size / GiB:.1f} GB "
                            f"({pick.note})", partial(self.download, pick, memory)))
        choices.append(("a model server at another address (you type it)", self.use_address))
        ready = bool(running_local(self.config)) or can_start(self.config)
        labels = [label for label, _ in choices]
        labels.append("skip - keep what is set up now" if ready else "skip - no offline model")
        choice = self.choose(labels, 1)
        if choice is None or choice > len(choices):
            self.say("  skipped" + ("" if ready else ": without internet nothing will answer"))
            return ready
        return choices[choice - 1][1]()

    def own_servers(self) -> tuple[LocalServer, ...]:
        """Addresses you set yourself (OLLAMA_HOST, LOCALAGENT_API_BASE), looked at first."""
        own = []
        if self.config.host != Config.host:
            own.append(LocalServer("Ollama", self.config.host, "ollama"))
        if self.config.api_base != Config.api_base:
            own.append(LocalServer("your model server", self.config.api_base, "openai"))
        return tuple(own)

    def use_server(self, server: LocalServer, models: list[str]) -> bool:
        """Use a model server that runs on this computer, or at an address you gave."""
        suggestion = pick_local_model(models)
        if models:
            more = " ..." if len(models) > SHOWN_MODELS else ""
            self.say(f"  Models there: {', '.join(models[:SHOWN_MODELS])}{more}")
        else:
            how = "ollama pull qwen3:8b" if server.kind == "ollama" else "load one in it first"
            self.say(f"  note  {server.title} has no model yet ({how})")
        typed = self.answer(f"  Model [{suggestion}]: ")
        model = (typed or suggestion) if typed is not None else ""
        if not model:
            self.say("  skipped: no model chosen")
            return False
        if server.kind == "ollama":
            self.save("LOCALAGENT_LOCAL_SERVER", "ollama")
            self.save("OLLAMA_HOST", None if server.base == Config.host else server.base)
            self.save("LOCALAGENT_API_BASE", None)
        else:
            self.save("LOCALAGENT_LOCAL_SERVER", "openai")
            self.save("LOCALAGENT_API_BASE", None if server.base == Config.api_base else server.base)
        self.save("LOCALAGENT_MODEL", model)
        self.say(f"  ok    offline model: {model} on {server.title} at {server.base}")
        return True

    def use_downloaded(self) -> bool:
        """Use the model downloaded by setup, started with llama.cpp when it is needed."""
        self.save("LOCALAGENT_LOCAL_SERVER", "openai")
        self.save("LOCALAGENT_API_BASE", None)
        self.save("LOCALAGENT_MODEL", None)
        model = find_model_file(self.config)
        name = model.name if model is not None else "the downloaded model"
        self.say(f"  ok    offline model: {name} with llama.cpp (started when needed)")
        return True

    def download(self, pick: CatalogModel, memory: float | None) -> bool:
        """Fetch llama.cpp and the model suggested for this computer (each asked first)."""
        size = f"{memory:.1f} GB of memory" if memory else "an unknown amount of memory"
        self.say(f"  This computer has {size}: the suggested model is {pick.name} "
                 f"({pick.size / GiB:.1f} GB download) - {pick.note}")
        program = find_llama_server(self.config)
        if program is not None:
            self.say(f"  ok    llama.cpp found: {program}")
        elif self.yes("  llama.cpp, the program that runs the model (25-40 MB), is missing. "
                      "Download it? [Y/n] "):
            try:
                self.say(f"  ok    llama.cpp installed in {install_llama(self.progress)}")
                program = find_llama_server(self.config)
            except DownloadError as exc:
                self.say(f"  FAIL  {exc}")
        model = find_model_file(self.config)
        if model is not None:
            self.say(f"  ok    model found: {model}")
        elif self.yes(f"  Download {pick.name} ({pick.size / GiB:.1f} GB; it can be stopped and "
                      "continued later)? [Y/n] "):
            try:
                model = install_model(pick, self.progress)
                self.say(f"  ok    model saved: {model}")
            except DownloadError as exc:
                self.say(f"  FAIL  {exc}")
        return program is not None and model is not None and self.use_downloaded()

    def use_address(self) -> bool:
        """A model server at an address you type (another computer, another port)."""
        typed = self.answer("  Its address, e.g. http://192.168.1.20:8080/v1 (for Ollama: "
                            "http://host:11434): ")
        if not typed:
            self.say("  skipped: no address given")
            return False
        base = typed.rstrip("/") if "://" in typed else "http://" + typed.rstrip("/")
        ollama = LocalServer("Ollama", base.removesuffix("/v1"), "ollama")
        models = server_models(ollama, timeout=5)
        if models is not None:                          # Ollama's own API sets the context size
            return self.use_server(ollama, models)
        server = LocalServer("the model server", base, "openai")
        models = server_models(server, timeout=5)
        if models is not None:
            return self.use_server(server, models)
        self.say(f"  FAIL  nothing answers at {base} (start the server first, or check the address)")
        return False

    # ---- step 3: sandbox --------------------------------------------------------------
    def sandbox(self) -> None:
        """Step 3: say whether Docker and the sandbox image are ready (nothing is changed)."""
        if not docker_available():
            self.say("  note  Docker is not running. Install Docker Desktop (docker.com) and start "
                     "it - or work without it: code then runs on this computer, after your approval.")
        elif sandbox_image_ready(self.config.sandbox_image):
            self.say("  ok    Docker and the sandbox image are ready")
        else:
            self.say(f"  note  Docker runs; build the sandbox image once with:\n        {sandbox_hint()}")
