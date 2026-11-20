"""The agent loop: ask the model, run the tools it requests, repeat.

    user text -> [model -> tool calls -> tool results -> model ...] -> final answer

The loop ends when the model replies without tool calls, or after
``max_turns`` model calls (a hard stop against endless loops).
"""
from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from typing import Callable

from .config import Config
from .context import build_system_prompt
from .llm import REMOVED, ChatBackend, TokenSink, fit_history, message_size
from .meter import Meter
from .sandbox import truncate
from .session import SessionLog
from .tools import ToolBox

__all__ = ["REMOVED", "Agent", "failed"]                 # REMOVED re-exported (tests use it)
ACTION_WORDS = frozenset({"fix", "add", "create", "write", "implement", "change", "update",
                          "refactor", "remove", "rename", "make", "build", "modify", "delete",
                          "edit", "insert", "replace"})
FILLER_WORDS = frozenset({"please", "pls", "kindly", "can", "could", "would", "will", "you", "u",
                          "now", "also", "then", "and", "just", "ok", "okay", "so", "i", "we",
                          "want", "need", "to", "lets", "let's", "go", "ahead", "quickly"})
EMPTY_REPLY = ("Your last reply was empty. Answer the request, or call the tools you need.")
BAD_ARGUMENTS = ("ERROR: the arguments of this call were not valid JSON (the reply may have been "
                 "cut off). Send the call again; for a big file, write it in smaller parts with "
                 "edit_file.")
WRITE_TOOLS = frozenset({"write_file", "edit_file", "multi_edit"})
MAX_REPEATS = 3
REPEAT_WARNING = ("ERROR: you already made this exact call. Repeating it cannot help. Use read_file to "
                  "look at the file as it is now, then try something different or explain the problem.")
NUDGE = ("You have not changed any file yet. If the request needs a change, make it now with "
         "edit_file, multi_edit or write_file. If it was only a question, repeat your answer.")
LOOKING = frozenset({"fetch_url", "delegate"})   # like reads: their result is information
FINISH_SPEC = {"type": "function", "function": {
    "name": "finish",
    "description": "End the task. Call it in the SAME response as your last write/run, but never "
                   "together with a read, search, list, outline, fetch or delegate call (you have "
                   "not seen their results yet). 'summary' "
                   "is what the user reads: for a question, the COMPLETE answer itself (never a "
                   "description like 'I explained X'); for a task, what you did and verified. It is "
                   "refused if another call in that response fails.",
    "parameters": {"type": "object", "properties": {"summary": {"type": "string"}},
                   "required": ["summary"]}}}
COMPACT_PROMPT = ("Summarise this conversation so you can continue the work later without it: the "
                  "user's goals, decisions made, files created or changed (with paths), what was "
                  "verified, and what is still open. Plain text, at most 300 words. Do not call tools.")
PLAN_PREFIX = ("PLAN MODE - do not change anything yet. Investigate with read-only tools "
               "(read_file, search, outline, list_files), then call finish with a numbered plan: "
               "which files to change, what to change in each, and how to verify it.\nRequest: ")
PLAN_ONLY_ERROR = ("ERROR: plan mode: only read-only tools (read_file, search, outline, list_files) "
                   "may be used now; describe the change in your plan instead.")
NOT_ALLOWED = "ERROR: that tool is not available for this request; use only the tools you were given."
DELEGATE_SPEC = {"type": "function", "function": {
    "name": "delegate",
    "description": "Give a self-contained investigation to a helper agent with a fresh, empty context "
                   "(e.g. 'find where invoices are calculated and explain the flow'). It can only "
                   "read and search, and returns a short report. Use it only for broad searches across "
                   "many files, to keep your own context small; it costs extra model calls.",
    "parameters": {"type": "object", "properties": {"task": {"type": "string"}},
                   "required": ["task"]}}}
HELPER_PREFIX = ("You are a helper agent. Investigate with read-only tools only, then call finish with "
                 "a concise report (at most 200 words): the relevant files, functions and line "
                 "numbers, and how things work. Task: ")
INTERRUPTED = "INTERRUPTED: the user stopped the task before this ran."
EMPTY_TWICE = ("The model sent an empty answer twice. Try rephrasing the request, or another "
               "model (/model).")
FAILURE_PREFIXES = ("ERROR", "REJECTED", "NOT RUN", "DENIED")
EXIT_CODE = re.compile(r"exit code (-?\d+)")
CUT_SUMMARY = re.compile(r'"summary"\s*:\s*"(.*)', re.DOTALL)


def failed(result: str) -> bool:
    """True when a tool result reports a problem (error, rejected write, non-zero exit code)."""
    first = result.splitlines()[0] if result else ""
    if first.startswith(FAILURE_PREFIXES):
        return True
    match = EXIT_CODE.match(first)
    return match is not None and match.group(1) != "0"


def _wants_change(text: str) -> bool:
    """True when the request starts like an instruction to change code (fix, add, create...),
    also after polite words: "please fix", "can you add", "go ahead and create"."""
    for word in text.lower().split():
        word = word.strip(".,:;!?")
        if word not in FILLER_WORDS:
            return word in ACTION_WORDS
    return False


def _parse_args(raw: object) -> dict:
    """Tool arguments usually arrive as a dict, but some models send a JSON string."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            value = json.loads(raw)
            return value if isinstance(value, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _broken_args(raw: object) -> bool:
    """True when the arguments are text that is not valid JSON (usually a cut-off reply)."""
    if not isinstance(raw, str) or not raw.strip():
        return False
    try:
        json.loads(raw)
    except json.JSONDecodeError:
        return True
    return False


class Agent:
    """Holds the conversation and drives the model/tool loop."""

    def __init__(self, config: Config, backend: ChatBackend, toolbox: ToolBox,
                 meter: Meter, log: SessionLog | None = None, depth: int = 0) -> None:
        self._cfg = config
        self._depth = depth                            # 0 = main agent, 1 = a helper (subagent)
        self._on_event: Callable[[str], None] = lambda _m: None
        self._backend = backend
        self._tools = toolbox
        self._meter = meter
        self._log = log
        self._call_ids = 0
        self._seen: dict[tuple[str, str], int] = {}    # write calls made in this request
        self._repeats = 0                              # identical write calls refused so far
        self._plan_only = False                        # True while /plan investigates
        self._allowed: frozenset[str] | None = None   # extra tools when restricted (None = all)
        self.messages: list[dict] = [self._system_message()]

    def _system_message(self) -> dict:
        """A fresh system prompt (re-reads AGENT.md and the file list)."""
        return {"role": "system", "content": build_system_prompt(self._cfg)}

    def refresh_system(self) -> None:
        """Rebuild the system prompt in place, e.g. after AGENT.md was written by /init."""
        self.messages[0] = self._system_message()

    @property
    def backend(self) -> ChatBackend:
        """The model client answering now (its ``last_model`` says which model answered)."""
        return self._backend

    def set_backend(self, backend: ChatBackend, config: Config) -> None:
        """Answer with another model from now on (/model online or offline), conversation kept.

        The new config brings the new model's history budget: going offline usually shrinks
        it from 32K to 8K tokens, and the next request compacts or trims to fit.
        """
        self._backend = backend
        self._cfg = replace(self._cfg, num_ctx=config.num_ctx, backend=config.backend,
                            mode=config.mode, fallback_backend=config.fallback_backend)

    def _add(self, message: dict) -> None:
        """Append to the history (append-only keeps the server's prompt cache valid) and log it."""
        self.messages.append(message)
        if self._log:
            self._log.append(message)

    def restore(self, messages: list[dict]) -> None:
        """Continue an earlier session: re-add its messages (the system prompt is rebuilt fresh)."""
        for message in messages:
            if message.get("role") != "system":
                self._add(message)
        self.repair()                                  # a crash may have left calls unanswered

    def repair(self) -> int:
        """Give every unanswered tool call a result, so the history is valid again.

        Needed after Ctrl+C stops a task half-way: model APIs reject a conversation in
        which an assistant message asked for tools whose results never arrived.
        """
        answered = {m.get("tool_call_id") for m in self.messages if m.get("role") == "tool"}
        missing = [call for m in self.messages for call in m.get("tool_calls") or []
                   if call.get("id") not in answered]
        for call in missing:
            self._add({"role": "tool", "tool_name": call.get("function", {}).get("name", ""),
                       "tool_call_id": call.get("id"), "content": INTERRUPTED})
        return len(missing)

    def reset(self) -> None:
        """Forget the conversation (keeps files on disk) and start from a fresh system prompt."""
        self.messages = [self._system_message()]
        if self._log:
            self._log.mark("reset")                    # --resume starts after this point

    def _size(self) -> int:
        """Characters in the history, tool-call arguments included (about 4 per token)."""
        return sum(message_size(m) for m in self.messages)

    def _budget(self) -> int:
        """History size, in characters, that fits comfortably in the context window."""
        return int(self._cfg.num_ctx * 0.7) * 4

    def compact(self) -> str:
        """Replace the history with a model-written summary (one model call)."""
        if len(self.messages) <= 3:
            return "nothing to compact"
        before = self._size()
        prompt = {"role": "user", "content": COMPACT_PROMPT}
        request = fit_history(self.messages, self._budget()) + [prompt]   # must fit to be sent
        reply = self._backend.chat(request, self._tools.specs() + [FINISH_SPEC], None)
        self._meter.record(reply)
        summary = (reply.message.get("content") or "").strip()
        if not summary:
            return "compaction skipped: the model returned no summary"
        self.messages = [self._system_message(),
                         {"role": "user", "content": "Summary of our earlier conversation:\n" + summary},
                         {"role": "assistant", "content": "Understood. I will continue from this summary."}]
        if self._log:
            self._log.mark("compact")                  # --resume continues from the summary
            for message in self.messages[1:]:
                self._log.append(message)
        return f"compacted the conversation: {before} -> {self._size()} characters"

    def run(self, user_text: str, on_event: Callable[[str], None] = lambda _m: None,
            on_token: TokenSink | None = None, plan_only: bool = False,
            allowed: frozenset[str] | None = None) -> str:
        """Handle one user request and return the model's final answer.

        With ``plan_only`` (the /plan command) only read-only tools (and the checklist)
        are offered and the answer is a plan; nothing is changed. ``allowed`` restricts a
        normal request to read-only tools plus these (e.g. /init may only write files).
        """
        if plan_only:
            allowed = frozenset({"todo", "delegate"})
        if len(self.messages) > 6 and self._size() > self._cfg.compact_at * self._budget():
            on_event("[conversation is long: summarising it to save tokens]")
            on_event(f"[{self.compact()}]")
        self._add({"role": "user", "content": (PLAN_PREFIX + user_text) if plan_only else user_text})
        writes_at_start = self._tools.writes
        nudges = troubles = 0
        empty_retried = False
        self._seen, self._repeats, self._plan_only, self._allowed = {}, 0, plan_only, allowed
        self._on_event = on_event
        # must this request change files? Not for plans, nor when no writing tool is offered
        wants = _wants_change(user_text) and not plan_only and self._may_use("write_file")
        if self._depth == 0:                           # helpers leave the routing alone
            getattr(self._backend, "reset_route", lambda: None)()   # every request starts cheap
        specs = [s for s in self._tools.specs() if self._may_use(s["function"]["name"])]
        if self._depth == 0 and self._cfg.helpers and self._may_use("delegate"):
            specs.append(DELEGATE_SPEC)                # helpers cannot start helpers
        specs.append(FINISH_SPEC)
        for _ in range(self._cfg.max_turns):
            self._trim_history()
            reply = self._backend.chat(self.messages, specs, on_token)
            self._meter.record(reply)
            calls = reply.message.get("tool_calls") or []
            if not calls and not (reply.message.get("content") or "").strip():
                if empty_retried:                     # an empty answer is a failure, not a reply
                    return EMPTY_TWICE
                empty_retried = True                  # placeholder: some APIs reject empty turns
                self._add({"role": "assistant", "content": "(empty reply)"})
                self._add({"role": "user", "content": EMPTY_REPLY})
                continue
            kept = {"role": "assistant", "content": reply.message.get("content") or ""}
            if calls:
                for call in calls:                    # every call needs a unique id
                    self._call_ids += 1
                    call.setdefault("id", f"call_{self._call_ids}")
                kept["tool_calls"] = calls
            self._add(kept)                           # drops bulky extras such as "thinking"
            if not calls:
                if (nudges < self._cfg.max_nudges and self._tools.writes == writes_at_start
                        and wants):
                    nudges += 1                       # asked for a change, changed nothing
                    self._add({"role": "user", "content": NUDGE})
                    continue
                return kept["content"]
            results = self._execute(calls, on_event)
            names = [call["function"].get("name", "") for call in calls]
            problems = sum(failed(r) for n, r in zip(names, results, strict=True) if n != "finish")
            summary = self._finish_summary(calls)
            if summary is not None:
                unseen = sorted({n for n in names if self._tools.is_read_only(n) or n in LOOKING})
                unchanged = (self._tools.writes == writes_at_start and wants
                             and nudges < self._cfg.max_nudges)
                reason = ""
                if problems:
                    reason = "another call in this response failed; fix it first"
                elif unseen:                          # it answered before seeing what it asked for
                    reason = (f"you have not seen what {', '.join(unseen)} returned yet; "
                              "read the results, then finish")
                elif unchanged:
                    nudges += 1
                    reason = "no file was changed yet; make the requested change first"
                if reason:                            # refuse: failed / unseen / nothing done
                    results = [f"NOT FINISHED: {reason}" if n == "finish" else r
                               for n, r in zip(names, results, strict=True)]
            for call, name, result in zip(calls, names, results, strict=True):
                self._add({"role": "tool", "tool_name": name,
                           "tool_call_id": call["id"], "content": result})
            if summary is not None and not any(r.startswith("NOT FINISHED") for r in results):
                return summary                        # saved the extra "final answer" call
            if self._repeats >= MAX_REPEATS:
                return "Stopped: the model kept repeating the same edit without progress."
            troubles += problems
            escalate = getattr(self._backend, "escalate", None) if self._depth == 0 else None
            if troubles >= self._cfg.escalate_after and escalate and escalate():
                on_event("[trouble detected: switching to a stronger model]")   # first time only
        return f"Stopped after {self._cfg.max_turns} model calls without finishing."

    def _may_use(self, name: str) -> bool:
        """Whether this request may use the tool (always, unless it is restricted)."""
        return (self._allowed is None or self._tools.is_read_only(name)
                or name in self._allowed)

    def _delegate(self, task: str) -> str:
        """Run a read-only helper agent with a fresh context; return only its report."""
        if self._depth > 0 or not self._cfg.helpers:
            return "ERROR: helpers are not available here"
        if not task.strip():
            return "ERROR: describe the task for the helper"
        helper = Agent(replace(self._cfg, max_turns=self._cfg.helper_turns), self._backend,
                       self._tools, self._meter, None, depth=self._depth + 1)
        self._on_event(f"   [helper started: {task[:80]}]")
        report = helper.run(HELPER_PREFIX + task, on_event=lambda m: self._on_event("   | " + m),
                            allowed=frozenset())
        self._on_event("   [helper finished]")
        return truncate("HELPER REPORT:\n" + report, self._cfg.max_output_chars)

    @staticmethod
    def _finish_summary(calls: list[dict]) -> str | None:
        """The summary text if this turn contains a finish call, else None. A summary cut off
        mid-way (broken JSON) is rescued as far as it got, rather than lost."""
        for call in calls:
            function = call.get("function", {})
            if function.get("name") == "finish":
                raw = function.get("arguments")
                cut = CUT_SUMMARY.search(raw) if _broken_args(raw) else None
                if cut:
                    text = cut.group(1).replace("\\n", "\n").replace('\\"', '"').rstrip('"} \n')
                    return text + " [answer cut off]"
                return str(_parse_args(raw).get("summary", "Done."))
        return None

    def _run_one(self, call: dict) -> str:
        """Run one requested tool and return its (truncated) result text."""
        function = call.get("function", {})
        name = function.get("name", "")
        if name == "finish":
            return "OK: finished"                     # handled by run(); nothing to execute
        if _broken_args(function.get("arguments")):
            return BAD_ARGUMENTS                      # say why, instead of "missing 'path'"
        args = _parse_args(function.get("arguments"))
        if not self._may_use(name):
            return PLAN_ONLY_ERROR if self._plan_only else NOT_ALLOWED
        if name == "delegate":
            return self._delegate(str(args.get("task", "")))
        if name in WRITE_TOOLS:
            key = (name, json.dumps(args, sort_keys=True))
            self._seen[key] = self._seen.get(key, 0) + 1
            if self._seen[key] > 1:                   # same write again: refuse, count it
                self._repeats += 1
                return REPEAT_WARNING
        return truncate(self._tools.call(name, args), self._cfg.max_output_chars)

    def _execute(self, calls: list[dict], on_event: Callable[[str], None]) -> list[str]:
        """Run a turn's tool calls; read-only batches run in parallel, others one by one."""
        def announce(call: dict) -> None:
            function = call.get("function", {})
            shown = json.dumps(_parse_args(function.get("arguments")))[:100]
            on_event(f"-> {function.get('name', '')} {shown}")

        def report(name: str, result: str) -> None:
            if name == "todo":                        # show the whole checklist
                for line in result.splitlines():
                    on_event(f"   {line}"[:140])
                return
            on_event(f"   {result.splitlines()[0] if result else ''}"[:140])

        names = [call.get("function", {}).get("name", "") for call in calls]
        if len(calls) > 1 and all(self._tools.is_read_only(n) for n in names):
            for call in calls:
                announce(call)
            with ThreadPoolExecutor(max_workers=min(len(calls), 4)) as pool:
                results = list(pool.map(self._run_one, calls))   # map keeps the original order
            for name, result in zip(names, results, strict=True):
                report(name, result)
            return results
        results = []
        for call, name in zip(calls, names, strict=True):
            announce(call)
            results.append(self._run_one(call))
            report(name, results[-1])
            if name in WRITE_TOOLS:
                self._show_diff(on_event)
        return results

    def _show_diff(self, on_event: Callable[[str], None]) -> None:
        """Show the user what the last write changed (never sent to the model: saves tokens)."""
        diff = self._tools.pop_diff()
        limit = self._cfg.diff_lines
        for line in diff[:limit]:
            on_event(f"     {line}"[:160])
        if len(diff) > limit:
            on_event(f"     ... {len(diff) - limit} more diff lines")

    def _trim_history(self) -> None:
        """When the history no longer fits the context window, shorten it (llm.fit_history:
        old tool outputs first, then old call arguments, then the oldest exchanges)."""
        if self._size() > self._budget():
            self.messages = fit_history(self.messages, self._budget())
