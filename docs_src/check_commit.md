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
@@ tools/check_commit.py header @@
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
@@ tools/check_commit.py git @@
- Runs one git command and returns its output as bytes; a failing command raises (`check=True`).

@@ tools/check_commit.py staged_files @@
- `git diff --cached` lists what the commit would record. `--diff-filter=ACMR` keeps added, copied, modified and renamed
  files (a deleted file cannot leak anything); `-z` separates names with a zero byte, so any file name works.

## The checks
@@ tools/check_commit.py problems @@
- Everything wrong with one staged file: a forbidden kind; a size over the limit — and then it stops, because reading a
  5 GB file into memory just to scan it would be absurd; otherwise the content is read (`read`, only now) and every
  secret pattern is tried.

@@ tools/check_commit.py author_problem @@
- `ident` is git's author identity, `Name <email> time zone`. A private noreply address passes; anything else returns
  the explanation and the exact command to fix it, with the page where GitHub shows your noreply address.

@@ tools/check_commit.py main @@
- Asks git for the author identity (`git var GIT_AUTHOR_IDENT` — what the commit is about to use) and checks it. Then for
  every staged file: its size from git (`cat-file -s`, without reading it) and `problems`, with the content read through
  `partial(git, "show", ":name")` (the staged version, which may differ from the file on disk). A submodule has no
  content of its own and is skipped.
- Every problem is printed as `COMMIT BLOCKED: ...`, followed by how to unstage a file and how to commit anyway.
  Exit code 1 makes git abort the commit; 0 lets it continue.
- Tested both ways on this repository: nothing staged with the private address → passes; a scratch repository with a
  Gmail author, a fake Google key, a `.env` file and a 25 MB file → all four reported, commit refused.

@@ tools/check_commit.py footer @@
- Runs `main` when git (or you) runs the file directly.
