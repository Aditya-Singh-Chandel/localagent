"""The offline model: find, start and stop a llama.cpp server, and talk to whichever local
model server is running (llama.cpp, LM Studio or Ollama).

Offline work needs two files, the llama.cpp server program and a model, looked for in order:
  program: $LOCALAGENT_LLAMA_SERVER, ~/.localagent/llama/ (any sub-folder, GPU builds first),
           this checkout's bin/llamacpp-vulkan and bin/llamacpp folders, then the PATH
  model:   $LOCALAGENT_MODEL_FILE, the first *.gguf in ~/.localagent/models/, then in this
           checkout's models/ folder
A server is started only when the local model is really needed; loading a model takes
10-60 s. It listens on 127.0.0.1 only (nothing outside this computer can use it), writes its
output to ~/.localagent/llama-server.log, and is stopped when the agent ends - on Windows even
when the terminal window is simply closed.
"""
from __future__ import annotations

import atexit
import ctypes
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import replace
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

from .config import Config
from .llm import ChatBackend, LLMError, OllamaClient, Reply, TokenSink, fit_history
from .openai_compat import OpenAICompatClient

HOME = Path.home() / ".localagent"
CHECKOUT = Path(__file__).resolve().parent.parent       # the source folder, when run from it
PROGRAM = "llama-server.exe" if os.name == "nt" else "llama-server"
LOG_FILE = HOME / "llama-server.log"
START_TIMEOUT = 300.0                                   # seconds a model may take to load
GPU_WORDS = ("cuda", "vulkan", "metal", "rocm", "hip", "sycl")
THIS_COMPUTER = frozenset({"127.0.0.1", "localhost", "::1"})
NO_LOCAL_MODEL = (
    "No local model is available. Offline work needs llama.cpp's llama-server program and a "
    ".gguf model file: put them in ~/.localagent/llama/ and ~/.localagent/models/ (or set "
    "LOCALAGENT_LLAMA_SERVER and LOCALAGENT_MODEL_FILE), or start Ollama or LM Studio. "
    "Step by step: docs/01-setup-and-models.md")


def _get(url: str, timeout: float = 1.0) -> int:
    """HTTP status of a quick GET request (0 when nothing answers)."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return int(response.status)
    except urllib.error.HTTPError as exc:
        exc.close()
        return exc.code
    except (OSError, ValueError):
        return 0


def server_up(api_base: str) -> bool:
    """True when an OpenAI-style model server answers at ``api_base`` within a second."""
    return _get(api_base + "/models") == 200


def ollama_up(host: str) -> bool:
    """True when Ollama answers at ``host`` within a second."""
    return _get(host + "/api/tags") == 200


def running_local(config: Config) -> str:
    """Which local model server answers now: "openai" (llama.cpp, LM Studio...), "ollama" or ""."""
    if server_up(config.api_base):
        return "openai"
    if ollama_up(config.host):
        return "ollama"
    return ""


def _gpu_build(path: Path) -> bool:
    """True for a llama.cpp build that can use the graphics card."""
    return any(word in str(path).lower() for word in GPU_WORDS)


def find_llama_server(config: Config) -> Path | None:
    """The llama.cpp server program, or None."""
    if config.llama_server:
        given = Path(config.llama_server).expanduser()
        return given if given.is_file() else None
    home = HOME / "llama"
    installed = sorted(home.rglob(PROGRAM), key=lambda p: (not _gpu_build(p), str(p))) \
        if home.is_dir() else []
    checkout = [CHECKOUT / "bin" / "llamacpp-vulkan" / PROGRAM, CHECKOUT / "bin" / "llamacpp" / PROGRAM]
    for path in installed + checkout:
        if path.is_file():
            return path
    on_path = shutil.which("llama-server")
    return Path(on_path) if on_path else None


def find_model_file(config: Config) -> Path | None:
    """The local model, a .gguf file (a vision add-on "mmproj" file is never chosen), or None."""
    if config.model_file:
        given = Path(config.model_file).expanduser()
        return given if given.is_file() else None
    for folder in (HOME / "models", CHECKOUT / "models"):
        if folder.is_dir():
            models = sorted(p for p in folder.glob("*.gguf") if not p.name.lower().startswith("mmproj"))
            if models:
                return models[0]
    return None


def can_start(config: Config) -> bool:
    """True when a llama.cpp server could be started on this computer for ``config.api_base``."""
    return ((urlsplit(config.api_base).hostname or "") in THIS_COMPUTER
            and find_llama_server(config) is not None and find_model_file(config) is not None)


def describe_local(config: Config) -> str:
    """One line for /model and --doctor: which local model would answer, and its state."""
    kind = running_local(config)
    if kind == "openai":
        return f"the model server at {config.api_base} (running)"
    if kind == "ollama":
        return f"Ollama at {config.host}, model {config.model} (running)"
    model = find_model_file(config)
    if can_start(config) and model is not None:
        return f"{model.name} with llama.cpp (started when needed)"
    return "none - see docs/01-setup-and-models.md"


_JOBS: list[int] = []                                   # Windows job handles kept open (see below)


def _end_with_this_program(process: subprocess.Popen) -> None:
    """Windows: put ``process`` in a job that Windows closes when this program ends, however it
    ends. Closing the terminal window skips Python's atexit, so without the job a 5 GB model
    would stay in memory. (Elsewhere atexit is relied on.)"""
    if sys.platform != "win32":
        return
    from ctypes import wintypes

    class Basic(ctypes.Structure):
        _fields_ = [("user_time", ctypes.c_int64), ("job_time", ctypes.c_int64),
                    ("flags", wintypes.DWORD), ("min_set", ctypes.c_size_t),
                    ("max_set", ctypes.c_size_t), ("processes", wintypes.DWORD),
                    ("affinity", ctypes.c_size_t), ("priority", wintypes.DWORD),
                    ("scheduling", wintypes.DWORD)]

    class Extended(ctypes.Structure):
        _fields_ = [("basic", Basic), ("io", ctypes.c_ulonglong * 6),
                    ("process_memory", ctypes.c_size_t), ("job_memory", ctypes.c_size_t),
                    ("peak_process", ctypes.c_size_t), ("peak_job", ctypes.c_size_t)]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                                 wintypes.DWORD]
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    job = kernel32.CreateJobObjectW(None, None)
    limits = Extended()
    limits.basic.flags = 0x2000                         # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    handle = kernel32.OpenProcess(0x0101, False, process.pid)   # SET_QUOTA | TERMINATE
    if job and handle and kernel32.SetInformationJobObject(job, 9, ctypes.byref(limits),
                                                           ctypes.sizeof(limits)):
        if kernel32.AssignProcessToJobObject(job, handle):
            _JOBS.append(job)                           # open until this program ends
    if handle:
        kernel32.CloseHandle(handle)                    # the job keeps its own reference


def _log_tail(lines: int = 8) -> str:
    """The last lines of the server's log, to explain why it stopped."""
    try:
        text = LOG_FILE.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "(no log)"
    return "\n".join(text.strip().splitlines()[-lines:])


class LlamaServer:
    """A llama.cpp server process started by this program (and stopped when it ends)."""

    def __init__(self, program: Path, model: Path, api_base: str, context: int,
                 gpu_layers: int = -1, threads: int = 0) -> None:
        self.program, self.model, self.api_base = program, model, api_base.rstrip("/")
        self.context, self.gpu_layers, self.threads = context, gpu_layers, threads
        self.alias = model.stem                         # the model's name in the server's API
        self.process: subprocess.Popen | None = None

    def argv(self) -> list[str]:
        """Its command line: the model, this computer only, the context size, tool calls."""
        port = urlsplit(self.api_base).port or 8080
        argv = [str(self.program), "-m", str(self.model), "--host", "127.0.0.1",
                "--port", str(port), "-c", str(self.context), "--jinja",
                "--reasoning-budget", "0", "--alias", self.alias]
        if self.gpu_layers >= 0:                        # else llama.cpp fits the GPU itself
            argv += ["-ngl", str(self.gpu_layers)]
        if self.threads > 0:
            argv += ["-t", str(self.threads)]
        return argv

    def running(self) -> bool:
        """True while the process this program started is alive."""
        return self.process is not None and self.process.poll() is None

    def ready(self) -> bool:
        """True once the model is loaded (llama.cpp's /health says 200; 503 while loading)."""
        root = self.api_base[:-3] if self.api_base.endswith("/v1") else self.api_base
        return _get(root + "/health") == 200

    def start(self, on_notice: Callable[[str], None]) -> None:
        """Start the server unless it already runs, then wait until the model is loaded."""
        if not self.running():
            LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
            options: dict = {}
            if sys.platform == "win32":                 # no window; Ctrl+C stays with the agent
                options["creationflags"] = subprocess.CREATE_NO_WINDOW
            else:
                options["start_new_session"] = True
            with LOG_FILE.open("wb") as log:
                self.process = subprocess.Popen(self.argv(), stdin=subprocess.DEVNULL, stdout=log,
                                                stderr=subprocess.STDOUT, cwd=self.program.parent,
                                                **options)
            _end_with_this_program(self.process)
            atexit.register(self.stop)
            on_notice(f"starting the local model {self.model.name} "
                      f"(loading takes 10-60 s; log: {LOG_FILE})")
        started = time.monotonic()
        while not self.ready():
            if self.process is not None and self.process.poll() is not None:
                code, self.process = self.process.returncode, None
                raise LLMError(f"the local model server stopped while starting (exit code {code}):"
                               f"\n{_log_tail()}", final=True)
            if time.monotonic() - started > START_TIMEOUT:
                self.stop()
                raise LLMError(f"the local model did not load within {START_TIMEOUT:.0f}s; "
                               f"see {LOG_FILE}", final=True)
            time.sleep(0.5)
        on_notice(f"local model ready: {self.alias}")

    def stop(self) -> None:
        """Stop the server (frees its memory); safe to call more than once."""
        process, self.process = self.process, None
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


_SERVERS: dict[tuple[str, str, str], LlamaServer] = {}


def server_for(config: Config) -> LlamaServer:
    """The llama.cpp server for this program, model and address (one per program run)."""
    program, model = find_llama_server(config), find_model_file(config)
    if program is None or model is None:
        raise LLMError(NO_LOCAL_MODEL, final=True)
    key = (str(program), str(model), config.api_base)
    if key not in _SERVERS:
        _SERVERS[key] = LlamaServer(program, model, config.api_base, config.local_ctx,
                                    config.gpu_layers, config.local_threads)
    return _SERVERS[key]


def stop_local_servers() -> int:
    """Stop every llama.cpp server this program started; returns how many were running."""
    running = [server for server in _SERVERS.values() if server.running()]
    for server in running:
        server.stop()
    return len(running)


class LocalModel:
    """The local model as a ChatBackend.

    It uses the local server that is running (``kind`` "openai" or "ollama", or "" for
    whichever answers), starts llama.cpp's when none is, and fits the conversation into the
    local context window - a history built for Gemini's 32K budget would not fit 8K.
    """

    def __init__(self, config: Config, kind: str = "",
                 on_notice: Callable[[str], None] = lambda _m: None) -> None:
        self._config = config
        self._kind = kind
        self._notice = on_notice
        self._client: ChatBackend | None = None
        self._server: LlamaServer | None = None
        self.last_model = ""                            # what answered (shown after a request)

    def ensure_ready(self) -> None:
        """Connect to a running local server or start one; raises LLMError when impossible."""
        if self._client is not None and (self._server is None or self._server.running()):
            return
        self._client, kind = None, self._kind
        if kind in ("", "openai") and server_up(self._config.api_base):
            kind = "openai"
        elif kind in ("", "ollama") and ollama_up(self._config.host):
            kind = "ollama"
        elif kind in ("", "openai") and can_start(self._config):
            self._server = server_for(self._config)
            self._server.start(self._notice)
            kind = "openai"
        elif kind:
            where = self._config.host if kind == "ollama" else self._config.api_base
            raise LLMError(f"the local {kind} model server is not running at {where}", final=True)
        else:
            raise LLMError(NO_LOCAL_MODEL, final=True)
        local = replace(self._config, num_ctx=self._config.local_ctx)
        if self._server is not None:
            local = replace(local, model=self._server.alias)
        self._client = OllamaClient(local) if kind == "ollama" else OpenAICompatClient(local)
        self.last_model = (f"ollama:{local.model}" if kind == "ollama"
                           else f"local:{self._server.alias if self._server else local.model}")

    def chat(self, messages: list[dict], tools: list[dict],
             on_token: TokenSink | None = None) -> Reply:
        """Answer with the local model, the conversation shortened to fit its context."""
        self.ensure_ready()
        assert self._client is not None
        budget = int(self._config.local_ctx * 0.7) * 4 - len(json.dumps(tools))
        return self._client.chat(fit_history(messages, max(budget, 4000)), tools, on_token)
