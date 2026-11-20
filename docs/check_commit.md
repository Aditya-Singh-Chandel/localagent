# `tools/check_commit.py` — the pre-commit guard

**Role:** a safety net for publishing the project with git. Before every `git commit` it looks at what the commit
would record and **refuses** the commit when something must not be published. It is not part of the agent; it
protects the repository.

**Why it exists.** When this project was first put on GitHub (November 2026), the first commit contained the 5 GB model
file (GitHub refuses files over 100 MB — but only after uploading everything), 105 llama.cpp program files,
conversation logs, Python caches, and a personal e-mail address as the author. All of it was cleaned up (see
`03-build-log.md`, Phase 10); this guard makes sure none of it can happen again by accident.

**Turn it on** (once per copy of the repository — the setting is not copied by `git clone`):
```
git config core.hooksPath .githooks
```
Git then runs `.githooks/pre-commit` before every commit, which finds a Python (the project's `.venv` first) and runs
this file. To commit anyway, deliberately: `git commit --no-verify`.

What it checks:

| Check | Refuses | Why |
|---|---|---|
| author | an author e-mail that is not a GitHub `noreply` address | every commit records it, and anyone can read it on GitHub |
| size | any file over 20 MB | git keeps every version forever; GitHub rejects 100 MB |
| kind | `.gguf`, `.pem`, `.key`, `.p12`, `.pfx`, `id_rsa*`, `.env` (`.env.example` is fine) | models and secrets, even when forced past `.gitignore` |
| content | text that looks like a Google, GitHub, Anthropic, OpenAI, AWS or Slack key, or a private key | a pushed key is public within minutes |

## Module header
```python
 1 | """Pre-commit guard: stop a commit that would publish something it must not.
 2 | 
 3 | It runs by itself before every ``git commit`` once enabled (once per clone):
 4 |     git config core.hooksPath .githooks
 5 | and looks at every file the commit would record (the "staged" files). It refuses when one
 6 |   * is bigger than 20 MB - GitHub rejects files over 100 MB only after uploading everything;
 7 |     models and program files belong outside git (see .gitignore),
 8 |   * is a model, key or .env file, even when it was forced past .gitignore,
 9 |   * contains text that looks like an API key, an access token or a private key.
10 | It also refuses when the commit's author email is a real address: every commit records it, and
11 | on GitHub anyone can read it. GitHub's private "noreply" address still links the commit to your
12 | account. Set it once per clone:  git config user.email "<id>+<username>@users.noreply.github.com"
13 | To commit anyway, deliberately:  git commit --no-verify
14 | """
15 | from __future__ import annotations
16 | 
17 | import re
18 | import subprocess
19 | import sys
20 | from functools import partial
21 | from typing import Callable
22 | 
23 | MAX_BYTES = 20_000_000
24 | BLOCKED_FILES = re.compile(
25 |     r"(^|/)(\.env(\.[^/]*)?|id_(rsa|ed25519|ecdsa)[^/]*|[^/]*\.(gguf|pem|key|p12|pfx))$",
26 |     re.IGNORECASE)
27 | ALLOWED_FILES = re.compile(r"(^|/)\.env\.example$", re.IGNORECASE)
28 | PRIVATE_EMAIL = re.compile(r"<[^<>@\s]+@users\.noreply\.github\.com>")
29 | SECRETS = {
30 |     "Google API key": re.compile(rb"AIza[0-9A-Za-z_-]{35}"),
31 |     "GitHub token": re.compile(rb"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{60,})"),
32 |     "Anthropic API key": re.compile(rb"\bsk-ant-[A-Za-z0-9_-]{20,}"),
33 |     "OpenAI API key": re.compile(rb"\bsk-(?!ant-)(?:proj-)?[A-Za-z0-9_-]{40,}"),
34 |     "AWS access key": re.compile(rb"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
35 |     "Slack token": re.compile(rb"\bxox[abprs]-[0-9A-Za-z-]{10,}"),
36 |     "private key": re.compile(rb"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
37 | }
38 | 
39 | 
```
- The docstring is the user guide. `re` holds the patterns; `subprocess` runs git; `sys` prints to the error stream;
  `partial` and `Callable` let the content be read only when needed.
- `MAX_BYTES` is the 20 MB limit. `BLOCKED_FILES` matches the forbidden kinds of file (in any folder, any letter
  case); `ALLOWED_FILES` the one exception, `.env.example` — a template that by convention holds no real values.
- `PRIVATE_EMAIL` matches GitHub's private address `<number>+<username>@users.noreply.github.com`, which still links a
  commit to your account (avatar, contribution graph) without showing your real address.
- `SECRETS` maps a description to a pattern of **bytes**, so binary files can be searched too. Each follows the
  provider's documented key format: Google keys start `AIza` plus 35 characters; GitHub tokens `ghp_`, `gho_`, `ghu_`,
  `ghs_`, `ghr_` or `github_pat_`; Anthropic `sk-ant-`; OpenAI `sk-` (but not `sk-ant-`, via `(?!ant-)`); AWS access
  keys `AKIA`/`ASIA` plus 16 capitals or digits; Slack `xox?-`; and the first line of any private key. The patterns
  cannot match their own source text (after `AIza` this file has a `[`, which is not a key character), so the guard
  never blocks itself; the tests build their fake keys at run time for the same reason.

## Talking to git
```python
40 | def git(*args: str) -> bytes:
41 |     """Run one git command and return what it printed (raises when git fails)."""
42 |     return subprocess.run(["git", *args], capture_output=True, check=True).stdout
43 | 
44 | 
```
- Runs one git command and returns its output as bytes; a failing command raises (`check=True`).

```python
45 | def staged_files() -> list[str]:
46 |     """Files the commit would add or change (deleted files cannot leak anything)."""
47 |     names = git("diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z")
48 |     return [name for name in names.decode("utf-8", "replace").split("\0") if name]
49 | 
50 | 
```
- `git diff --cached` lists what the commit would record. `--diff-filter=ACMR` keeps added, copied, modified and renamed
  files (a deleted file cannot leak anything); `-z` separates names with a zero byte, so any file name works.

## The checks
```python
51 | def problems(name: str, size: int, read: Callable[[], bytes]) -> list[str]:
52 |     """Everything wrong with one staged file; ``read`` fetches its content only when needed."""
53 |     found = []
54 |     if BLOCKED_FILES.search(name) and not ALLOWED_FILES.search(name):
55 |         found.append(f"{name}: model, key and .env files must never be committed")
56 |     if size > MAX_BYTES:
57 |         found.append(f"{name}: {size / 1_000_000:.0f} MB is too big for git "
58 |                      f"(limit {MAX_BYTES // 1_000_000} MB)")
59 |         return found                                 # never read a huge file just to scan it
60 |     data = read()
61 |     found += [f"{name}: contains what looks like a {label}"
62 |               for label, pattern in SECRETS.items() if pattern.search(data)]
63 |     return found
64 | 
65 | 
```
- Everything wrong with one staged file: a forbidden kind; a size over the limit — and then it stops, because reading a
  5 GB file into memory just to scan it would be absurd; otherwise the content is read (`read`, only now) and every
  secret pattern is tried.

```python
66 | def author_problem(ident: str) -> str | None:
67 |     """Why the author identity (``Name <email> time zone``) would leak an email, or None."""
68 |     if PRIVATE_EMAIL.search(ident):
69 |         return None
70 |     return ("the author email would be public on GitHub; use your private noreply address:\n"
71 |             '  git config user.email "<id>+<username>@users.noreply.github.com"\n'
72 |             "  (find it at https://github.com/settings/emails)")
73 | 
74 | 
```
- `ident` is git's author identity, `Name <email> time zone`. A private noreply address passes; anything else returns
  the explanation and the exact command to fix it, with the page where GitHub shows your noreply address.

```python
75 | def main() -> int:
76 |     """Check the author and every staged file; print what is wrong; exit code 1 blocks the commit."""
77 |     found: list[str] = []
78 |     leak = author_problem(git("var", "GIT_AUTHOR_IDENT").decode("utf-8", "replace"))
79 |     if leak:
80 |         found.append(leak)
81 |     for name in staged_files():
82 |         try:
83 |             size = int(git("cat-file", "-s", f":{name}"))
84 |         except (subprocess.CalledProcessError, ValueError):
85 |             continue                                 # e.g. a submodule: no content of its own
86 |         found += problems(name, size, partial(git, "show", f":{name}"))
87 |     for line in found:
88 |         print(f"COMMIT BLOCKED: {line}", file=sys.stderr)
89 |     if found:
90 |         print("Unstage a file with:  git restore --staged <file>\n"
91 |               "Only if you are sure it is safe:  git commit --no-verify", file=sys.stderr)
92 |     return 1 if found else 0
93 | 
94 | 
```
- Asks git for the author identity (`git var GIT_AUTHOR_IDENT` — what the commit is about to use) and checks it. Then for
  every staged file: its size from git (`cat-file -s`, without reading it) and `problems`, with the content read through
  `partial(git, "show", ":name")` (the staged version, which may differ from the file on disk). A submodule has no
  content of its own and is skipped.
- Every problem is printed as `COMMIT BLOCKED: ...`, followed by how to unstage a file and how to commit anyway.
  Exit code 1 makes git abort the commit; 0 lets it continue.
- Tested both ways on this repository: nothing staged with the private address → passes; a scratch repository with a
  Gmail author, a fake Google key, a `.env` file and a 25 MB file → all four reported, commit refused.

```python
95 | if __name__ == "__main__":
96 |     raise SystemExit(main())
```
- Runs `main` when git (or you) runs the file directly.
