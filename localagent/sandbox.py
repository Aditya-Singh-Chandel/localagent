"""Safety helpers: keep file access inside the workspace and bound commands.

The model is untrusted. Every path it names goes through ``resolve_inside`` and
every program it launches goes through ``run_command``.
"""
from __future__ import annotations

import os
import re
import shlex
import signal
import subprocess
import sys
from pathlib import Path
from typing import Iterator

# Folders never shown to the model, searched, snapshotted or verified: version control, caches,
# virtual environments, installed packages, build output, editor state, and the agent's own
# state folder. A folder can still be opened by naming it (list_files, read_file).
SKIP_DIRS = frozenset({
    ".git", ".hg", ".svn", "__pycache__", ".venv", "venv", ".mypy_cache", ".ruff_cache",
    ".pytest_cache", ".tox", ".nox", ".eggs", ".hypothesis", ".ipynb_checkpoints",
    "node_modules", "bower_components", ".localagent", "target", "dist", ".next", ".nuxt",
    ".svelte-kit", ".angular", ".turbo", ".parcel-cache", ".cache", ".gradle", ".dart_tool",
    ".terraform", ".idea", ".vs", "coverage", "htmlcov", "__pypackages__"})
SAFE_WORD = re.compile(r"[A-Za-z0-9_./\\:+=-]+")      # needs no quotes in sh or PowerShell


def skip_dir(name: str) -> bool:
    """True for a folder that is never entered (``SKIP_DIRS`` or Python's ``*.egg-info``)."""
    return name in SKIP_DIRS or name.endswith(".egg-info")


def walk_files(root: Path) -> Iterator[Path]:
    """Every file under ``root`` in sorted order, never entering a skipped folder.

    Pruning while walking matters on real projects: ``node_modules`` alone can hold
    100 000 files, which a plain ``rglob`` would visit before filtering them out.
    """
    for folder, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not skip_dir(d))   # prune in place
        for name in sorted(filenames):
            yield Path(folder) / name


def _in_git_repo(root: Path) -> bool:
    """True when ``root`` or a folder above it holds a ``.git`` entry (folder or file)."""
    return any((folder / ".git").exists() for folder in (root, *root.parents))


def project_files(root: Path) -> Iterator[Path]:
    """Like ``walk_files``, but in a git repository the files .gitignore excludes are left out.

    ``git ls-files`` knows the ignore rules exactly (nested .gitignore files, global
    excludes), so build output and downloads the project ignores never reach the model.
    Without git, or outside a repository, every file is walked as usual.
    """
    root = root.resolve()
    listed = None
    if _in_git_repo(root):
        try:
            done = subprocess.run(["git", "-C", str(root), "ls-files", "--cached", "--others",
                                   "--exclude-standard", "-z"], capture_output=True, timeout=30)
            listed = done.stdout if done.returncode == 0 else None
        except (OSError, subprocess.TimeoutExpired):
            listed = None
    names = sorted({name for name in (listed or b"").decode("utf-8", "replace").split("\0") if name})
    if not names:                            # no git, or the whole folder is ignored (e.g. the
        yield from walk_files(root)          # server's session folders): walk it as usual
        return
    for name in names:
        if any(skip_dir(part) for part in name.split("/")[:-1]):
            continue
        path = root / name
        if path.is_file():                   # --cached also lists files deleted since the commit
            yield path


def looks_binary(path: Path) -> bool:
    """True for a file that is not text (a NUL byte early on): images, models, programs."""
    try:
        with path.open("rb") as handle:
            return b"\0" in handle.read(8192)
    except OSError:
        return False


class SandboxError(Exception):
    """Raised when the model asks for something outside the allowed area."""


def resolve_inside(root: Path, relative: str) -> Path:
    """Return the absolute path for ``relative`` or raise if it escapes ``root``."""
    root = root.resolve()
    candidate = (root / relative).resolve()  # resolves "..", symlinks, absolute inputs
    if candidate != root and root not in candidate.parents:
        raise SandboxError(f"path '{relative}' is outside the workspace")
    return candidate


def truncate(text: str, limit: int) -> str:
    """Shorten long text, keeping the start and the end (errors are usually last)."""
    if len(text) <= limit:
        return text
    head = limit * 2 // 3
    tail = limit - head
    omitted = len(text) - limit
    return f"{text[:head]}\n... [{omitted} characters omitted] ...\n{text[-tail:]}"


def shell_argv(command: str) -> list[str]:
    """How this computer runs one shell command: PowerShell on Windows, ``sh`` elsewhere."""
    if os.name == "nt":
        return ["powershell", "-NoProfile", "-NonInteractive", "-Command", command]
    return ["sh", "-c", command]


def quote_for_shell(word: str) -> str:
    """``word`` (e.g. a file name) written so the shell of ``shell_argv`` takes it literally.

    A file name chosen by the model must never be able to run a command of its own, such as
    ``a$(curl evil).py``; a name starting with ``-`` must never be read as an option.
    """
    if word.startswith("-"):
        word = "./" + word
    if SAFE_WORD.fullmatch(word):
        return word
    if os.name == "nt":                                  # PowerShell: '' is a quote inside '...'
        return "'" + word.replace("'", "''") + "'"
    return shlex.quote(word)


def kill_tree(process: subprocess.Popen) -> None:
    """Stop a program and every program it started (best effort, never raises)."""
    if sys.platform == "win32":
        try:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(process.pid)],
                           capture_output=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired):
            pass
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)      # its own process group (see run_command)
        except OSError:
            pass
    try:
        process.kill()
    except OSError:
        pass


def _leftover_output(process: subprocess.Popen) -> tuple[str, str]:
    """What a killed program printed before it died (empty when its pipes stay blocked)."""
    try:
        out, err = process.communicate(timeout=5)
    except (subprocess.TimeoutExpired, ValueError, OSError):
        return "", ""
    return out or "", err or ""


def run_command(argv: list[str], cwd: Path, timeout: int, limit: int,
                input_text: str | None = None) -> tuple[int, str]:
    """Run a program (never through a shell) and return (exit code, output).

    The program reads ``input_text`` (or nothing: a program waiting for typing would otherwise
    take the user's keyboard), and it runs in a process group of its own, so a timeout or
    Ctrl+C stops everything it started - a server it launched, a test runner's workers -
    instead of waiting for them forever.
    """
    options: dict = {}
    if sys.platform == "win32":
        options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        options["start_new_session"] = True
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}   # Python children can print any character
    try:
        process = subprocess.Popen(
            argv, cwd=cwd, env=env, text=True, encoding="utf-8", errors="replace",
            stdin=subprocess.DEVNULL if input_text is None else subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, **options)
    except FileNotFoundError as exc:
        return 127, f"Command not found: {exc}"
    try:
        out, err = process.communicate(input_text, timeout=timeout)
    except subprocess.TimeoutExpired:
        kill_tree(process)
        out, err = _leftover_output(process)
        report = f"Command timed out after {timeout}s and was killed.\n{out}{err}".strip()
        return 124, truncate(report, limit)
    except KeyboardInterrupt:
        kill_tree(process)
        raise
    return process.returncode, truncate((out or "") + (err or ""), limit)
