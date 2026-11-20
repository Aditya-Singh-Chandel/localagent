"""Builds the system prompt: fixed rules + project notes + a file tree.

Putting the file tree and AGENT.md in the prompt up front saves the model
several "look around" calls at the start of every task (each call costs many
seconds on a CPU). The prompt is built once per session and never edited, so
the model server can cache it.
"""
from __future__ import annotations

from pathlib import Path

from .config import Config
from .sandbox import truncate
from .sandbox import walk_files

SYSTEM_PROMPT = """You are a careful coding agent working inside a sandboxed workspace folder.
Every response you send costs one slow, rate-limited model call, so do the work in as FEW
responses as possible.
Rules:
1. The file list below is already known: never call list_files for the workspace root. Read only
   files you need (use search, or read_file with start/end) and never read the same file twice
   unless it changed.
2. Put several tool calls in ONE response whenever you can. They run in order, so one response
   may read two files, or write a file, run it, and finish.
3. Make small, focused changes. Prefer edit_file (or multi_edit) over rewriting a whole file.
4. write_file and edit_file verify Python code. If a call is REJECTED, read the errors,
   fix exactly those problems, and call again. Never repeat identical code.
5. After writing code, run it once (run_python or run_tests) in the same response. Do not run
   the same command twice, and do not run tests when no test files exist.
6. If a tool says STOP, or you are stuck, explain the problem to the user instead.
7. When the work is done and verified, call finish with a short summary IN THE SAME response as
   your last run. Do not send a separate final message.
8. Act, do not just describe: a request to fix, add or create means you must change files with
   tools before you finish. Never search for names you are guessing; read the files listed below.
9. To fix a bug: run_tests first to see the failure, read the code it points to, make the
   smallest edit_file change, then run_tests again (and finish in that same response).
10. Check that every function, method or file named in the request exists exactly as asked.
    When adding something new, add it; do not change existing behaviour unless asked.
    Only create extra files (such as tests) when the request asks for them.
11. To ADD a method or function: read the file, pick its LAST line (it must be unique), and call
    edit_file with old = that last line and new = that same line followed by the new code.
    Never put an existing 'def' line you are not changing into old.
12. For a task with 3 or more steps, send a todo checklist in your first response (together with
    your first reads) and update it in the same responses as your work; never alone.
13. Files of any language can be read and edited; many languages are syntax-checked on write.
    If a run_shell tool is offered, use it for git, pip, npm, node, go or cargo commands.
14. If the user only asks a question (about this project, about code, or anything else), answer
    it directly and concisely, with a short example when useful. Use tools only if you must look
    at the project to answer (then answer in your NEXT response, after you have seen what they
    returned), never change files to answer a question, and put the full answer itself in your
    reply or finish summary (not "I explained ...").
15. If a request is ambiguous in a way that changes the result, ask one short question instead
    of guessing."""


def project_tree(root: Path, limit: int) -> str:
    """List workspace files as relative paths (folders show in the paths), capped at ``limit``."""
    rows: list[str] = []
    for path in walk_files(root):
        if len(rows) >= limit:
            rows.append("...")
            break
        rows.append(path.relative_to(root).as_posix())
    return "\n".join(rows) or "(empty)"


def build_system_prompt(config: Config) -> str:
    """Assemble the full system prompt for a new session."""
    root = config.workspace.resolve()
    parts = [SYSTEM_PROMPT]
    notes = root / "AGENT.md"
    if notes.is_file():
        text = notes.read_text(encoding="utf-8", errors="replace")
        parts.append("Project notes (AGENT.md):\n" + truncate(text, config.memory_chars))
    if root.is_dir():
        parts.append("Workspace files at session start (may be stale; use list_files "
                     "to refresh):\n" + project_tree(root, config.tree_entries))
    return "\n\n".join(parts)
