"""Safety helpers: keep file access inside the workspace and bound commands.

The model is untrusted. Every path it names goes through ``resolve_inside`` and
every program it launches goes through ``run_command``.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Iterator

# Folders never shown to the model, searched, snapshotted or verified: version control, caches,
# virtual environments, installed JavaScript packages, and the agent's own state folder.
SKIP_DIRS = frozenset({".git", "__pycache__", ".venv", "venv", ".mypy_cache", ".ruff_cache",
                       ".pytest_cache", ".tox", "node_modules", ".localagent"})


def walk_files(root: Path) -> Iterator[Path]:
    """Every file under ``root`` in sorted order, never entering a SKIP_DIRS folder.

    Pruning while walking matters on real projects: ``node_modules`` alone can hold
    100 000 files, which a plain ``rglob`` would visit before filtering them out.
    """
    for folder, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)   # prune in place
        for name in sorted(filenames):
            yield Path(folder) / name


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


def run_command(argv: list[str], cwd: Path, timeout: int, limit: int) -> tuple[int, str]:
    """Run a program (never through a shell) and return (exit code, output)."""
    try:
        done = subprocess.run(
            argv,
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return 124, f"Command timed out after {timeout}s and was killed."
    except FileNotFoundError as exc:
        return 127, f"Command not found: {exc}"
    return done.returncode, truncate(done.stdout + done.stderr, limit)
