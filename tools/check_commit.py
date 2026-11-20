"""Pre-commit guard: stop a commit that would publish something it must not.

It runs by itself before every ``git commit`` once enabled (once per clone):
    git config core.hooksPath .githooks
and looks at every file the commit would record (the "staged" files). It refuses when one
  * is bigger than 20 MB - GitHub rejects files over 100 MB only after uploading everything;
    models and program files belong outside git (see .gitignore),
  * is a model, key or .env file, even when it was forced past .gitignore,
  * contains text that looks like an API key, an access token or a private key.
It also refuses when the commit's author email is a real address: every commit records it, and
on GitHub anyone can read it. GitHub's private "noreply" address still links the commit to your
account. Set it once per clone:  git config user.email "<id>+<username>@users.noreply.github.com"
To commit anyway, deliberately:  git commit --no-verify
"""
from __future__ import annotations

import re
import subprocess
import sys
from functools import partial
from typing import Callable

MAX_BYTES = 20_000_000
BLOCKED_FILES = re.compile(
    r"(^|/)(\.env(\.[^/]*)?|id_(rsa|ed25519|ecdsa)[^/]*|[^/]*\.(gguf|pem|key|p12|pfx))$",
    re.IGNORECASE)
ALLOWED_FILES = re.compile(r"(^|/)\.env\.example$", re.IGNORECASE)
PRIVATE_EMAIL = re.compile(r"<[^<>@\s]+@users\.noreply\.github\.com>")
SECRETS = {
    "Google API key": re.compile(rb"AIza[0-9A-Za-z_-]{35}"),
    "GitHub token": re.compile(rb"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{60,})"),
    "Anthropic API key": re.compile(rb"\bsk-ant-[A-Za-z0-9_-]{20,}"),
    "OpenAI API key": re.compile(rb"\bsk-(?!ant-)(?:proj-)?[A-Za-z0-9_-]{40,}"),
    "AWS access key": re.compile(rb"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    "Slack token": re.compile(rb"\bxox[abprs]-[0-9A-Za-z-]{10,}"),
    "private key": re.compile(rb"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
}


def git(*args: str) -> bytes:
    """Run one git command and return what it printed (raises when git fails)."""
    return subprocess.run(["git", *args], capture_output=True, check=True).stdout


def staged_files() -> list[str]:
    """Files the commit would add or change (deleted files cannot leak anything)."""
    names = git("diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z")
    return [name for name in names.decode("utf-8", "replace").split("\0") if name]


def problems(name: str, size: int, read: Callable[[], bytes]) -> list[str]:
    """Everything wrong with one staged file; ``read`` fetches its content only when needed."""
    found = []
    if BLOCKED_FILES.search(name) and not ALLOWED_FILES.search(name):
        found.append(f"{name}: model, key and .env files must never be committed")
    if size > MAX_BYTES:
        found.append(f"{name}: {size / 1_000_000:.0f} MB is too big for git "
                     f"(limit {MAX_BYTES // 1_000_000} MB)")
        return found                                 # never read a huge file just to scan it
    data = read()
    found += [f"{name}: contains what looks like a {label}"
              for label, pattern in SECRETS.items() if pattern.search(data)]
    return found


def author_problem(ident: str) -> str | None:
    """Why the author identity (``Name <email> time zone``) would leak an email, or None."""
    if PRIVATE_EMAIL.search(ident):
        return None
    return ("the author email would be public on GitHub; use your private noreply address:\n"
            '  git config user.email "<id>+<username>@users.noreply.github.com"\n'
            "  (find it at https://github.com/settings/emails)")


def main() -> int:
    """Check the author and every staged file; print what is wrong; exit code 1 blocks the commit."""
    found: list[str] = []
    leak = author_problem(git("var", "GIT_AUTHOR_IDENT").decode("utf-8", "replace"))
    if leak:
        found.append(leak)
    for name in staged_files():
        try:
            size = int(git("cat-file", "-s", f":{name}"))
        except (subprocess.CalledProcessError, ValueError):
            continue                                 # e.g. a submodule: no content of its own
        found += problems(name, size, partial(git, "show", f":{name}"))
    for line in found:
        print(f"COMMIT BLOCKED: {line}", file=sys.stderr)
    if found:
        print("Unstage a file with:  git restore --staged <file>\n"
              "Only if you are sure it is safe:  git commit --no-verify", file=sys.stderr)
    return 1 if found else 0


if __name__ == "__main__":
    raise SystemExit(main())
