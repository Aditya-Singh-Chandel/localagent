# `localagent/__init__.py` and `localagent/__main__.py` — package markers

## `__init__.py`
```python
1 | """localagent: a small coding agent that runs on a local Ollama model."""
```
- Its presence makes the folder a Python *package* so `from localagent.agent import Agent` works. The one line is
  a docstring and nothing else runs on import.

## `__main__.py`
```python
1 | """Allows ``python -m localagent`` to start the CLI."""
2 | from .cli import main
3 | 
4 | raise SystemExit(main())
```
- Line 1: docstring. Line 2: imports `main` from `cli.py` (relative import, same package).
- Line 3: blank. Line 4: `raise SystemExit(main())` runs the CLI and exits the process with its return code.
  This file is what executes when you type `python -m localagent`.
