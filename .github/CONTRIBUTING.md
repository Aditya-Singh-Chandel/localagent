# Contributing

Thank you for helping. A few rules keep the project easy to trust and easy to learn from.

## Set up
```
python -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]"       # Linux/macOS: .venv/bin/python
git config core.hooksPath .githooks                     # the commit guard (see docs/check_commit.md)
```

## Before every commit
All four must pass (CI runs them on Linux and Windows):
```
.venv\Scripts\python -m ruff check localagent tests tools evals
.venv\Scripts\python -m mypy localagent evals tools
.venv\Scripts\python -m unittest discover -s tests -t .
.venv\Scripts\python tools/build_docs.py
```

## The rules
1. **Standard library only** in `localagent/` — no runtime dependencies.
2. **Every code line is documented exactly once.** Edit `docs_src/` (never `docs/`), mark code blocks with
   `@@ <file> <block> @@`, explain them so a beginner can follow, and run `tools/build_docs.py` (it fails otherwise).
3. **A bug fix starts with a reproduction** and ends with a test that would have caught it.
4. **Every change says why** in `docs_src/03-build-log.md`, with measurements where they exist.
5. **Fewer model calls matter.** A change that adds calls or tokens needs a reason (and ideally an eval run).
6. Commits use your private GitHub address (`<id>+<username>@users.noreply.github.com`); the guard refuses others.

## Releasing
Change the version in `localagent/__init__.py`, add a `CHANGELOG.md` entry, run `tools/build_release.py`, and publish
the files from `dist/` with `SHA256SUMS.txt`.
