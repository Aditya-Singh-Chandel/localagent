## What and why

<!-- What does this change, and what problem does it solve? For a bug fix: how was it reproduced? -->

## Checklist

- [ ] Tests added or updated, and `python -m unittest discover -s tests -t .` passes
- [ ] `ruff check` and `mypy` are clean
- [ ] `docs_src/` explains every new or changed line, and `python tools/build_docs.py` passes
- [ ] `docs_src/03-build-log.md` says why (with measurements where they exist)
- [ ] No new runtime dependency in `localagent/` (standard library only)
