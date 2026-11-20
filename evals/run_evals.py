"""Run the evaluation tasks against a real model and print a scoreboard.

Usage:  python -m evals.run_evals --model qwen3:8b [--only fizzbuzz] [--type-check]
Each task gets a fresh temporary workspace, so tasks cannot affect each other.
"""
from __future__ import annotations

import argparse
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path

from localagent.agent import Agent
from localagent.backends import make_backend
from localagent.cli import add_model_flags, model_flags, model_label
from localagent.config import Config, gemini_history, overlay
from localagent.llm import ChatBackend, LLMError
from localagent.meter import Meter
from localagent.tools import ToolBox

from .tasks import TASKS, Task


@dataclass(frozen=True)
class Result:
    """Outcome of one task."""

    name: str
    passed: bool
    calls: int
    tokens: int
    seconds: float
    note: str = ""


def run_task(task: Task, config: Config, backend: ChatBackend, trace: bool = False) -> Result:
    """Run one task in a throw-away workspace and judge the result with its check."""
    with tempfile.TemporaryDirectory() as folder:
        workspace = Path(folder)
        for name, content in task.seed.items():
            (workspace / name).parent.mkdir(parents=True, exist_ok=True)   # seeds may be in folders
            (workspace / name).write_text(content, encoding="utf-8")
        run_config = replace(config, workspace=workspace, auto_approve=True)
        meter = Meter()
        toolbox = ToolBox(run_config, lambda _a: True)
        agent = Agent(run_config, backend, toolbox, meter)
        note = ""
        answer = ""
        try:
            answer = agent.run(task.prompt, on_event=print if trace else (lambda _m: None))
        except LLMError as exc:
            note = f"model error: {exc}"
        finally:
            toolbox.close()                         # stop the sandbox before the folder is deleted
        passed = False if note else task.check(workspace) and (
            task.answer_check is None or task.answer_check(answer))
        return Result(task.name, passed, meter.calls,
                      meter.prompt_tokens + meter.completion_tokens, meter.seconds, note)


def main(argv: list[str] | None = None) -> int:
    """Run all (or selected) tasks and print one line per task plus a summary."""
    parser = argparse.ArgumentParser(prog="evals.run_evals")
    add_model_flags(parser)
    parser.add_argument("--only", action="append", help="task name (repeatable)")
    parser.add_argument("--type-check", action="store_true")
    parser.add_argument("--trace", action="store_true", help="print every tool call")
    args = parser.parse_args(argv)
    config = overlay(Config.from_env(), **model_flags(args), type_check=args.type_check)
    config = gemini_history(config)                     # measure what the chat really uses
    backend = make_backend(config)
    tasks = [t for t in TASKS if not args.only or t.name in args.only]
    results = []
    for task in tasks:
        result = run_task(task, config, backend, args.trace)
        results.append(result)
        status = "PASS" if result.passed else "FAIL"
        print(f"{status}  {result.name:<12} calls={result.calls:<3} tokens={result.tokens:<6} "
              f"time={result.seconds:.0f}s {result.note}")
    passed = sum(r.passed for r in results)
    print(f"\n{passed}/{len(results)} tasks passed with {model_label(config)}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
