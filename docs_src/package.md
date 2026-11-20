# `localagent/__init__.py` and `localagent/__main__.py` — package markers

## `__init__.py`
@@ localagent/__init__.py 1-3 @@
- Its presence makes the folder a Python *package* so `from localagent.agent import Agent` works. Line 1 is a
  docstring; nothing else runs on import.
- Line 3, `__version__`, is **the one place the version number lives**. `pyproject.toml` reads it when the package is
  built (`dynamic = ["version"]`), `localagent --version` prints it, the banner and `--doctor` show it, MCP servers are
  told it, and web pages see it in the `User-Agent`. To release a new version, change only this line.

## `__main__.py`
@@ localagent/__main__.py 1-4 @@
- Line 1: docstring. Line 2: imports `main` from `cli.py` (relative import, same package).
- Line 3: blank. Line 4: `raise SystemExit(main())` runs the CLI and exits the process with its return code.
  This file is what executes when you type `python -m localagent`.
