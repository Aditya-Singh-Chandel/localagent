"""Local models and the guided first run: which model suits this computer, which are installed,
and fetching llama.cpp and a model (resumable, checked against the publisher's checksum).

  localagent setup                      the guided first run: Gemini key, offline model, Docker
  localagent models                     installed models, the catalog, the recommendation
  localagent models download qwen3-4b   fetch a catalog model (about 2.5 GB)
  localagent models use qwen3-4b        make it the model offline mode uses
"""
from __future__ import annotations

import ctypes
import getpass
import os
import platform
import stat
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .config import Config
from .fetch import DownloadError, Progress, download, extract_archive, get_json
from .localserver import CHECKOUT, HOME, find_llama_server, find_model_file
from .runners import docker_available, sandbox_image_ready
from .settings import USER_SETTINGS, save_user_env

LLAMA_RELEASES = "https://api.github.com/repos/ggml-org/llama.cpp/releases?per_page=15"
KEY_PAGE = "https://aistudio.google.com/apikey"
GiB = 1024 ** 3
Say = Callable[[str], None]


@dataclass(frozen=True)
class CatalogModel:
    """A local model the agent knows how to fetch and check."""

    name: str            # what you type: `localagent models download qwen3-4b`
    repo: str            # the Hugging Face repository it comes from
    file: str            # the .gguf file in that repository
    size: int            # bytes, as published
    sha256: str          # checksum, as published (Hugging Face's large-file record)
    memory_gb: int       # computers with at least this much memory get it recommended
    note: str            # one line for the list

    @property
    def url(self) -> str:
        """Where Hugging Face serves the file."""
        return f"https://huggingface.co/{self.repo}/resolve/main/{self.file}"


CATALOG = (   # biggest first; sizes and checksums from Hugging Face's API (20 Nov 2026)
    CatalogModel("qwen3-8b", "Qwen/Qwen3-8B-GGUF", "Qwen3-8B-Q4_K_M.gguf", 5_027_783_488,
                 "d98cdcbd03e17ce47681435b5150e34c1417f50b5c0019dd560e4882c5745785", 14,
                 "the best that fits 16 GB of memory (12/15 on the evals)"),
    CatalogModel("qwen3-4b", "Qwen/Qwen3-4B-GGUF", "Qwen3-4B-Q4_K_M.gguf", 2_497_280_256,
                 "7485fe6f11af29433bc51cab58009521f205840f5b4ae3a32fa7f92e8534fdf5", 7,
                 "for 8 GB computers: twice as fast, weaker on bigger tasks"),
    CatalogModel("qwen3-1.7b", "Qwen/Qwen3-1.7B-GGUF", "Qwen3-1.7B-Q8_0.gguf", 1_834_426_016,
                 "061b54daade076b5d3362dac252678d17da8c68f07560be70818cace6590cb1a", 0,
                 "for small computers: questions and very small tasks only"),
)


def total_memory_gb() -> float | None:
    """This computer's memory in GB, or None when it cannot be told."""
    if sys.platform == "win32":
        class Status(ctypes.Structure):          # Windows' MEMORYSTATUSEX
            _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong),
                        ("total", ctypes.c_ulonglong), ("available", ctypes.c_ulonglong),
                        ("page_total", ctypes.c_ulonglong), ("page_free", ctypes.c_ulonglong),
                        ("virtual_total", ctypes.c_ulonglong), ("virtual_free", ctypes.c_ulonglong),
                        ("extended_free", ctypes.c_ulonglong)]

        status = Status()
        status.length = ctypes.sizeof(Status)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return status.total / GiB
        return None
    else:
        try:                                     # Linux and macOS: page size x number of pages
            return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / GiB
        except (ValueError, OSError, AttributeError):
            return None


def recommend(memory_gb: float | None) -> CatalogModel:
    """The biggest catalog model this much memory runs well (the middle one when unknown)."""
    if memory_gb is None:
        return CATALOG[1]
    return next(model for model in CATALOG if memory_gb >= model.memory_gb)


def catalog_entry(name: str) -> CatalogModel | None:
    """The catalog model called ``name`` (any letter case), or None."""
    return next((model for model in CATALOG if model.name == name.strip().lower()), None)


def installed_models(config: Config) -> list[Path]:
    """Every .gguf model where offline mode looks, plus a configured one elsewhere."""
    found: list[Path] = []
    for folder in (HOME / "models", CHECKOUT / "models"):
        if folder.is_dir():
            found += sorted(p for p in folder.glob("*.gguf") if not p.name.lower().startswith("mmproj"))
    given = Path(config.model_file).expanduser() if config.model_file else None
    if given is not None and given.is_file() and given not in found:
        found.insert(0, given)
    return found


def install_model(model: CatalogModel, on_progress: Progress) -> Path:
    """Fetch a catalog model into ~/.localagent/models (resumable, checksum-verified)."""
    return download(model.url, HOME / "models" / model.file, model.sha256, model.size, on_progress)


def llama_variants() -> tuple[str, ...]:
    """The llama.cpp builds that fit this computer, best first. Windows: the Vulkan build (uses
    most graphics cards and carries the processor-only libraries too); Linux: the plain build
    (the Vulkan one needs extra system libraries); macOS: its one build (Metal included)."""
    arm = platform.machine().lower() in ("arm64", "aarch64")
    if sys.platform == "win32":
        return ("win-vulkan-arm64", "win-cpu-arm64") if arm else ("win-vulkan-x64", "win-cpu-x64")
    if sys.platform == "darwin":
        return ("macos-arm64",) if arm else ("macos-x64",)
    return ("ubuntu-arm64",) if arm else ("ubuntu-x64", "ubuntu-vulkan-x64")


def find_llama_release(variants: tuple[str, ...]) -> tuple[str, str, int, str]:
    """(file name, URL, size, sha256) of the newest llama.cpp build in ``variants``.

    The newest releases are searched because llama.cpp's "latest" release can be a placeholder
    without programs (seen in Nov 2026); only a file with a published checksum is accepted.
    """
    releases = get_json(LLAMA_RELEASES)
    for release in releases if isinstance(releases, list) else []:
        tag = str(release.get("tag_name", ""))
        assets = {str(a.get("name")): a for a in release.get("assets", [])}
        for variant in variants:
            for extension in ("zip", "tar.gz"):
                asset = assets.get(f"llama-{tag}-bin-{variant}.{extension}")
                digest = str((asset or {}).get("digest") or "")
                if asset is not None and digest.startswith("sha256:"):
                    return (str(asset["name"]), str(asset["browser_download_url"]),
                            int(asset["size"]), digest.removeprefix("sha256:"))
    raise DownloadError("no llama.cpp build for this computer was found among the newest releases; "
                        "download one by hand from https://github.com/ggml-org/llama.cpp/releases "
                        "and unzip it into ~/.localagent/llama/")


def install_llama(on_progress: Progress, variants: tuple[str, ...] | None = None) -> Path:
    """Fetch and unpack the newest llama.cpp build into ~/.localagent/llama/<build>; returns the
    folder. The archive is checked against GitHub's published checksum, then deleted."""
    name, url, size, sha256 = find_llama_release(variants or llama_variants())
    folder = HOME / "llama" / name.removesuffix(".zip").removesuffix(".tar.gz")
    archive = download(url, HOME / "llama" / "downloads" / name, sha256, size, on_progress)
    extract_archive(archive, folder)
    archive.unlink()
    if sys.platform != "win32":                  # archives may lose the "may run" mark
        for program in folder.rglob("llama-*"):
            if program.is_file():
                program.chmod(program.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return folder


def check_gemini_key(config: Config, key: str) -> str:
    """"ok", "rejected" or "unreachable" for a Gemini key (listing models costs no quota)."""
    try:
        get_json(config.gemini_base + "/models", {"Authorization": f"Bearer {key}"}, timeout=15)
    except DownloadError as exc:
        return "rejected" if "HTTP 4" in str(exc) else "unreachable"
    return "ok"


def progress_printer(say: Say = print) -> Progress:
    """Shows a download's progress: in place once a second in a terminal, else every 30 s."""
    started, shown = time.monotonic(), [0.0]
    live = sys.stdout.isatty()

    def show(done: int, total: int | None) -> None:
        now = time.monotonic()
        finished = total is not None and done >= total
        if now - shown[0] < (1 if live else 30) and not finished:
            return
        shown[0] = now
        speed = done / max(now - started, 0.001)
        text = f"  {done / GiB:.2f} GB"
        if total:
            left = (total - done) / max(speed, 1) / 60
            text += f" of {total / GiB:.2f} GB ({done * 100 // total}%), about {left:.0f} min left"
        text += f" at {speed / 1e6:.1f} MB/s"
        if live and say is print:
            print(f"\r{text:<78}", end="\n" if finished else "", flush=True)
        else:
            say(text)
    return show


def _sandbox_hint() -> str:
    """The command that builds the sandbox image, with this copy's Dockerfile when it has one."""
    dockerfile = CHECKOUT / "docker" / "sandbox.Dockerfile"
    if dockerfile.is_file():
        return (f'docker build -t localagent-sandbox:latest -f "{dockerfile}" '
                f'"{dockerfile.parent}"')
    return "see docs/docker.md for the sandbox image"


def run_setup(config: Config, ask: Callable[[str], str] = input,
              secret: Callable[[str], str] = getpass.getpass, say: Say = print,
              settings_file: Path = USER_SETTINGS, progress: Progress | None = None) -> int:
    """`localagent setup`: the guided first run. Returns 0 when at least one model is ready."""
    def yes(question: str) -> bool:
        try:
            answer = ask(question).strip().lower()
        except (EOFError, KeyboardInterrupt):
            return False
        return answer in ("", "y", "yes")                # Enter accepts the suggestion

    say("localagent setup: three short steps; Enter accepts the suggestion in [brackets].")
    say("\n1/3  Online model: Gemini (fast, a free daily quota)")
    online = _setup_gemini(config, secret, say, settings_file)
    say("\n2/3  Offline model: runs on this computer (private, no quota, slower)")
    offline = _setup_local(config, yes, say, progress or progress_printer(say))
    say("\n3/3  Sandbox: Docker runs the agent's code in a sealed box (optional)")
    if not docker_available():
        say("  note  Docker is not running. Install Docker Desktop (docker.com) and start it - or work "
            "without it: code then runs on this computer, after your approval.")
    elif sandbox_image_ready(config.sandbox_image):
        say("  ok    Docker and the sandbox image are ready")
    else:
        say(f"  note  Docker runs; build the sandbox image once with:\n        {_sandbox_hint()}")
    say("")
    if online and offline:
        say("Ready, online and offline. In a project folder, type: localagent   (/model switches)")
    elif online:
        say("Ready (online only). For offline work, run localagent setup again later.")
    elif offline:
        say("Ready (offline only). In a project folder, type: localagent --offline")
    else:
        say("No model is ready yet. Run localagent setup again (a Gemini key is the quickest start).")
    say("Check everything at any time with: localagent --doctor")
    return 0 if online or offline else 1


def _setup_gemini(config: Config, secret: Callable[[str], str], say: Say,
                  settings_file: Path) -> bool:
    """Step 1: check the Gemini key, or ask for one and save it. True when online can work."""
    if config.gemini_api_key:
        state = check_gemini_key(config, config.gemini_api_key)
        say({"ok": "  ok    your GEMINI_API_KEY works",
             "rejected": f"  FAIL  your GEMINI_API_KEY was rejected; create a new one at {KEY_PAGE}",
             "unreachable": "  note  a key is set, but Google cannot be reached right now (offline?)"}[state])
        return state != "rejected"
    say(f"  Get a free key at {KEY_PAGE} (sign in with Google, then 'Create API key').")
    try:
        key = secret("  Paste the key (it stays hidden; Enter skips): ").strip()
    except (EOFError, KeyboardInterrupt):
        key = ""
    if not key:
        say("  skipped: without a key the agent works offline only")
        return False
    state = check_gemini_key(config, key)
    if state == "rejected":
        say("  FAIL  Google rejected that key; nothing was saved. Run setup again with a new key.")
        return False
    path = save_user_env("GEMINI_API_KEY", key, settings_file)
    os.environ["GEMINI_API_KEY"] = key
    say(f"  ok    key saved in {path} (only your user account can read that folder)"
        + ("" if state == "ok" else "; not checked, Google cannot be reached right now"))
    return True


def _setup_local(config: Config, yes: Callable[[str], bool], say: Say, progress: Progress) -> bool:
    """Step 2: find or fetch llama.cpp and a model sized for this computer. True when ready."""
    memory = total_memory_gb()
    pick = recommend(memory)
    size = f"{memory:.1f} GB of memory" if memory else "an unknown amount of memory"
    say(f"  This computer has {size}: the suggested model is {pick.name} "
        f"({pick.size / GiB:.1f} GB download) - {pick.note}")
    program = find_llama_server(config)
    if program is not None:
        say(f"  ok    llama.cpp found: {program}")
    elif yes("  llama.cpp, the program that runs the model (25-40 MB), is missing. Download it? [Y/n] "):
        try:
            say(f"  ok    llama.cpp installed in {install_llama(progress)}")
            program = find_llama_server(config)
        except DownloadError as exc:
            say(f"  FAIL  {exc}")
    model = find_model_file(config)
    if model is not None:
        say(f"  ok    model found: {model}")
    elif yes(f"  No model yet. Download {pick.name} ({pick.size / GiB:.1f} GB; it can be stopped and "
             "continued later)? [Y/n] "):
        try:
            model = install_model(pick, progress)
            say(f"  ok    model saved: {model}")
        except DownloadError as exc:
            say(f"  FAIL  {exc}")
    return program is not None and model is not None


def run_models(words: list[str], config: Config, say: Say = print,
               settings_file: Path = USER_SETTINGS, progress: Progress | None = None) -> int:
    """`localagent models` (list), `models download NAME`, `models use NAME-or-FILE`."""
    if not words:
        used = find_model_file(config)
        installed = installed_models(config)
        say("Installed models:" if installed else "No model is installed yet.")
        for found in installed:
            mark = "*" if used is not None and found.resolve() == used.resolve() else " "
            say(f"  {mark} {found}  ({found.stat().st_size / GiB:.1f} GB)")
        pick = recommend(total_memory_gb())
        say("\nAvailable (localagent models download NAME):")
        for entry in CATALOG:
            say(f"  {entry.name:<11} {entry.size / GiB:4.1f} GB  {entry.note}"
                + ("   <- suggested for this computer" if entry == pick else ""))
        if used is not None:
            say("\n* = used by offline mode. Another one: localagent models use NAME-or-FILE")
        return 0
    action, target = words[0].lower(), " ".join(words[1:])
    model = catalog_entry(target)
    if action == "download":
        if model is None:
            say(f"unknown model '{target}'; choose one of: {', '.join(m.name for m in CATALOG)}")
            return 2
        try:
            say(f"saved {install_model(model, progress or progress_printer(say))}")
        except DownloadError as exc:
            say(f"download failed: {exc}")
            return 1
        return 0
    if action == "use":
        places = [HOME / "models" / model.file, CHECKOUT / "models" / model.file] if model else \
            [Path(target).expanduser()]
        path = next((p for p in places if p.is_file()), None)
        if path is None:
            say(f"{target} is not there" + (f"; download it first: localagent models download "
                                            f"{model.name}" if model else ""))
            return 2
        saved = save_user_env("LOCALAGENT_MODEL_FILE", str(path.resolve()), settings_file)
        say(f"offline mode now uses {path} (saved in {saved})")
        if os.environ.get("LOCALAGENT_MODEL_FILE"):
            say("note: LOCALAGENT_MODEL_FILE is also set in your environment, and that one wins; "
                "remove it to use this choice")
        return 0
    say("usage: localagent models [download NAME | use NAME-or-FILE]")
    return 2
