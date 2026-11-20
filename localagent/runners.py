"""Where the agent's code actually runs: on this machine, or inside a Docker container.

Both runners offer the same method, ``run(kind, args)``, so the tools do not care
which one is active:
  * kind "python"  -> run a Python file          (args: [file, extra args...])
  * kind "tests"   -> run the project's tests     (args: [], or [your test command])
  * kind "shell"   -> run a shell command line    (args: [command])  -- Docker, or --allow-shell

The Docker runner starts ONE long-lived container per agent the first time it is
needed (``docker run -d ... sleep infinity``) and then uses ``docker exec`` for
each command, which is much faster than a new container per command. The
container has no network (unless allowed), a memory/CPU/process cap, runs as a
normal user, and only sees the workspace folder (mounted at /work).
"""
from __future__ import annotations

import atexit
import json
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Protocol

from .config import Config
from .sandbox import run_command, shell_argv

CONTAINER_WORKDIR = "/work"
VENV_FOLDERS = (".venv", "venv", "env")
NO_TEST_SCRIPT = "no test specified"            # npm's placeholder "test" script
# Inside the container: pytest when it is installed (it also runs unittest tests), else unittest.
PYTHON_TESTS_SH = ('if python -c "import pytest" 2>/dev/null; then python -m pytest -q --tb=short; '
                   'else python -m unittest discover {start}; fi')


class Runner(Protocol):
    """Anything that can run the three kinds of command."""

    supports_shell: bool
    shell_label: str

    def run(self, kind: str, args: list[str]) -> tuple[int, str]: ...

    def stop(self) -> None: ...


def project_python(root: Path) -> str:
    """The Python of the project: a virtual environment in the project folder (``.venv``,
    ``venv``, ``env``), else an activated one, else the agent's own. The project's packages
    are installed there, so its code and tests can import them."""
    inside = "Scripts/python.exe" if os.name == "nt" else "bin/python"
    active = os.environ.get("VIRTUAL_ENV")
    candidates = [root / f / inside for f in VENV_FOLDERS] + ([Path(active) / inside] if active else [])
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return sys.executable


def _unittest_start(root: Path) -> str:
    """Where unittest should look: a ``tests`` folder that is not a package can only be found
    by starting there (Python 3.11+ no longer enters folders without __init__.py)."""
    tests = root / "tests"
    plain = tests.is_dir() and not (tests / "__init__.py").exists() and not any(root.glob("test*.py"))
    return "-s tests" if plain else ""


PYTHON_TEST_GLOBS = ("test*.py", "*_test.py", "tests/test*.py", "tests/*_test.py",
                     "tests/*/test*.py", "test/test*.py")   # a fixed depth: never node_modules


def test_kind(root: Path) -> str:
    """Which test tool fits the project: "python", "npm", "go" or "cargo"."""
    python_tests = any(next(root.glob(pattern), None) for pattern in PYTHON_TEST_GLOBS)
    if not python_tests:
        package = root / "package.json"
        if package.is_file():
            try:
                script = json.loads(package.read_text(encoding="utf-8")).get("scripts", {}).get("test", "")
            except (ValueError, OSError, AttributeError):
                script = ""
            if script and NO_TEST_SCRIPT not in script:
                return "npm"
        if (root / "go.mod").is_file():
            return "go"
        if (root / "Cargo.toml").is_file():
            return "cargo"
    return "python"


class LocalRunner:
    """Runs commands directly on this computer with the project's Python."""

    def __init__(self, config: Config, root: Path) -> None:
        self._cfg = config
        self._root = root
        self._pytest: bool | None = None          # is pytest installed? (asked once)
        # A shell on the real computer is powerful, so it is opt-in (--allow-shell) and
        # every command still needs the user's approval in the toolbox.
        self.supports_shell = config.allow_host_shell
        self.shell_label = ("PowerShell on this Windows computer" if os.name == "nt"
                            else "sh on this computer")

    def _has_pytest(self, python: str) -> bool:
        """True when the project's Python can run pytest."""
        if self._pytest is None:
            code, _ = run_command([python, "-c", "import pytest"], self._root, 30, 200)
            self._pytest = code == 0
        return self._pytest

    def _tests_argv(self) -> list[str]:
        """The project's test command (see ``test_kind``)."""
        python = project_python(self._root)
        kind = test_kind(self._root)
        if kind == "npm":
            return [shutil.which("npm") or "npm", "test"]
        if kind == "go":
            return ["go", "test", "./..."]
        if kind == "cargo":
            return ["cargo", "test"]
        if self._has_pytest(python):
            return [python, "-m", "pytest", "-q", "--tb=short"]
        return [python, "-m", "unittest", "discover", *_unittest_start(self._root).split()]

    def run(self, kind: str, args: list[str]) -> tuple[int, str]:
        """Run on the host inside the workspace folder, with the timeout and output cap."""
        timeout = self._cfg.command_timeout
        if kind == "python":
            argv = [project_python(self._root), *args]
        elif kind == "tests":
            argv = shell_argv(args[0]) if args else self._tests_argv()   # your command, or found
            timeout = max(self._cfg.test_timeout, timeout)
        elif not self.supports_shell:
            return 126, "The shell tool needs --allow-shell or the Docker sandbox (--sandbox docker)."
        else:
            argv = shell_argv(args[0])
        return run_command(argv, self._root, timeout, self._cfg.max_output_chars)

    def stop(self) -> None:
        """Nothing to clean up for local runs."""


class DockerRunner:
    """Runs commands inside a locked-down container that only sees the workspace."""

    supports_shell = True
    shell_label = "sh inside an isolated Linux container"

    def __init__(self, config: Config, root: Path) -> None:
        self._cfg = config
        self._root = root
        self.name = f"localagent-{uuid.uuid4().hex[:10]}"
        self._started = False

    def _docker(self, *args: str, timeout: int = 120) -> tuple[int, str]:
        return run_command(["docker", *args], self._root, timeout, 4000)

    def start(self) -> None:
        """Create the container (once). Raises RuntimeError with advice when Docker fails."""
        if self._started:
            return
        code, out = self._docker("image", "inspect", self._cfg.sandbox_image)
        if code != 0:
            raise RuntimeError(
                f"Docker image {self._cfg.sandbox_image} not found ({out.strip()[:200]}). Build it with:\n"
                "  docker build -t localagent-sandbox:latest -f docker/sandbox.Dockerfile docker")
        argv = ["run", "-d", "--rm", "--name", self.name,
                "--memory", self._cfg.sandbox_memory, "--cpus", self._cfg.sandbox_cpus,
                "--pids-limit", "256", "--security-opt", "no-new-privileges",
                "-v", f"{self._root}:{CONTAINER_WORKDIR}", "-w", CONTAINER_WORKDIR]
        if not self._cfg.sandbox_network:
            argv += ["--network", "none"]
        code, out = self._docker(*argv, self._cfg.sandbox_image)
        if code != 0:
            raise RuntimeError(f"Could not start the sandbox container: {out.strip()[:300]}\n"
                               "Is Docker Desktop running?")
        self._started = True
        atexit.register(self.stop)

    def stop(self) -> None:
        """Remove the container (safe to call more than once, even after the folder is gone)."""
        if not self._started:
            return
        self._started = False
        try:                                       # no cwd: the workspace may already be deleted
            subprocess.run(["docker", "rm", "-f", self.name], capture_output=True, timeout=60)
        except (OSError, subprocess.TimeoutExpired):
            pass                                   # best effort; `docker ps` shows leftovers

    def _tests_command(self) -> list[str]:
        """The project's test command inside the container (see ``test_kind``)."""
        kind = test_kind(self._root)
        if kind == "npm":
            return ["npm", "test"]
        if kind == "go":
            return ["go", "test", "./..."]
        if kind == "cargo":
            return ["cargo", "test"]
        return ["sh", "-c", PYTHON_TESTS_SH.format(start=_unittest_start(self._root))]

    def run(self, kind: str, args: list[str]) -> tuple[int, str]:
        """Run inside the container; a timeout also kills the processes inside it."""
        try:
            self.start()
        except RuntimeError as exc:
            return 125, str(exc)
        seconds = self._cfg.command_timeout
        if kind == "python":
            inner = ["python", *args]
        elif kind == "tests":
            inner = ["sh", "-c", args[0]] if args else self._tests_command()
            seconds = max(self._cfg.test_timeout, seconds)
        else:
            inner = ["sh", "-c", args[0]]
        argv = ["docker", "exec", self.name, "timeout", "-s", "KILL", str(seconds), *inner]
        code, out = run_command(argv, self._root, seconds + 15, self._cfg.max_output_chars)
        if code == 137:                           # killed by `timeout -s KILL`
            return 124, f"Command timed out after {seconds}s and was killed.\n{out}"
        return code, out


def make_runner(config: Config, root: Path) -> Runner:
    """Pick the runner named by ``config.sandbox``."""
    if config.sandbox == "docker":
        return DockerRunner(config, root)
    if config.sandbox == "local":
        return LocalRunner(config, root)
    raise ValueError(f"unknown sandbox '{config.sandbox}'. Choose 'local' or 'docker'.")


def sandbox_image_ready(image: str) -> bool:
    """True when Docker answers AND the sandbox image exists (one quick command)."""
    try:
        done = subprocess.run(["docker", "image", "inspect", image], capture_output=True,
                              timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return done.returncode == 0


def docker_available() -> bool:
    """True when the docker command exists and its daemon answers."""
    try:
        done = subprocess.run(["docker", "info", "--format", "{{.ServerVersion}}"],
                              capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return done.returncode == 0
