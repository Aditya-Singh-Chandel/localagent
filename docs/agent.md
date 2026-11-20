# `localagent/agent.py` — the loop

**Role in the flow:** the heart. It owns the conversation (`messages`) and repeats:

```
user request ─► model call ─► tool calls? ──no──► final answer (return)
                    ▲              │yes
                    └── tool results appended ◄── ToolBox.call (verified)
```

It stops when the model answers without tool calls, or after `max_turns` calls.

**Token / call optimisations here:** (1) history is *append-only*, so the model server can reuse its cached prompt
prefix instead of re-reading everything; (2) bulky extras such as `thinking` are not stored; (3) tool results are
truncated; (4) old tool outputs are blanked only when the prompt nears the context limit; (5) the prompt asks for
several independent tool calls per turn, and every call in a turn is executed before the next model call.

## Module header
```python
  1 | """The agent loop: ask the model, run the tools it requests, repeat.
  2 | 
  3 |     user text -> [model -> tool calls -> tool results -> model ...] -> final answer
  4 | 
  5 | The loop ends when the model replies without tool calls, or after
  6 | ``max_turns`` model calls (a hard stop against endless loops).
  7 | """
  8 | from __future__ import annotations
  9 | 
 10 | import json
 11 | import re
 12 | from concurrent.futures import ThreadPoolExecutor
 13 | from dataclasses import replace
 14 | from typing import Callable
 15 | 
 16 | from .config import Config
 17 | from .context import build_system_prompt
 18 | from .llm import ChatBackend, TokenSink
 19 | from .meter import Meter
 20 | from .sandbox import truncate
 21 | from .session import SessionLog
 22 | from .tools import ToolBox
 23 | 
 24 | REMOVED = "[old tool output removed to save context]"
 25 | ACTION_WORDS = frozenset({"fix", "add", "create", "write", "implement", "change", "update",
 26 |                           "refactor", "remove", "rename", "make", "build", "modify", "delete",
 27 |                           "edit", "insert", "replace"})
 28 | WRITE_TOOLS = frozenset({"write_file", "edit_file", "multi_edit"})
 29 | MAX_REPEATS = 3
 30 | REPEAT_WARNING = ("ERROR: you already made this exact call. Repeating it cannot help. Use read_file to "
 31 |                   "look at the file as it is now, then try something different or explain the problem.")
 32 | NUDGE = ("You have not changed any file yet. If the request needs a change, make it now with "
 33 |          "edit_file, multi_edit or write_file. If it was only a question, repeat your answer.")
 34 | LOOKING = frozenset({"fetch_url", "delegate"})   # like reads: their result is information
 35 | FINISH_SPEC = {"type": "function", "function": {
 36 |     "name": "finish",
 37 |     "description": "End the task. Call it in the SAME response as your last write/run, but never "
 38 |                    "together with a read, search, list, outline, fetch or delegate call (you have "
 39 |                    "not seen their results yet). 'summary' "
 40 |                    "is what the user reads: for a question, the COMPLETE answer itself (never a "
 41 |                    "description like 'I explained X'); for a task, what you did and verified. It is "
 42 |                    "refused if another call in that response fails.",
 43 |     "parameters": {"type": "object", "properties": {"summary": {"type": "string"}},
 44 |                    "required": ["summary"]}}}
 45 | COMPACT_PROMPT = ("Summarise this conversation so you can continue the work later without it: the "
 46 |                   "user's goals, decisions made, files created or changed (with paths), what was "
 47 |                   "verified, and what is still open. Plain text, at most 300 words. Do not call tools.")
 48 | PLAN_PREFIX = ("PLAN MODE - do not change anything yet. Investigate with read-only tools "
 49 |                "(read_file, search, outline, list_files), then call finish with a numbered plan: "
 50 |                "which files to change, what to change in each, and how to verify it.\nRequest: ")
 51 | PLAN_ONLY_ERROR = ("ERROR: plan mode: only read-only tools (read_file, search, outline, list_files) "
 52 |                    "may be used now; describe the change in your plan instead.")
 53 | NOT_ALLOWED = "ERROR: that tool is not available for this request; use only the tools you were given."
 54 | DELEGATE_SPEC = {"type": "function", "function": {
 55 |     "name": "delegate",
 56 |     "description": "Give a self-contained investigation to a helper agent with a fresh, empty context "
 57 |                    "(e.g. 'find where invoices are calculated and explain the flow'). It can only "
 58 |                    "read and search, and returns a short report. Use it only for broad searches across "
 59 |                    "many files, to keep your own context small; it costs extra model calls.",
 60 |     "parameters": {"type": "object", "properties": {"task": {"type": "string"}},
 61 |                    "required": ["task"]}}}
 62 | HELPER_PREFIX = ("You are a helper agent. Investigate with read-only tools only, then call finish with "
 63 |                  "a concise report (at most 200 words): the relevant files, functions and line "
 64 |                  "numbers, and how things work. Task: ")
 65 | INTERRUPTED = "INTERRUPTED: the user stopped the task before this ran."
 66 | FAILURE_PREFIXES = ("ERROR", "REJECTED", "NOT RUN", "DENIED")
 67 | EXIT_CODE = re.compile(r"exit code (-?\d+)")
 68 | 
 69 | 
```
- Imports `json` (parse/print tool arguments), `replace` (copy the config for a helper), `Callable`, and the components wired together: config, system-prompt
  builder, backend interface and token-sink type, meter, `truncate`, session log, toolbox.
- `REMOVED` is the placeholder that replaces old tool output when space runs short.
- `WRITE_TOOLS` names the tools that change files; `MAX_REPEATS` (3) and `REPEAT_WARNING` belong to the repeat guard
  (see `Agent._run_one`).
- `ACTION_WORDS` lists verbs that start a request to *change* something (fix, add, create...). `NUDGE` is the reminder
  sent when the model answered such a request without changing any file (see `Agent.run`). `ThreadPoolExecutor`
  (imported at the top) runs read-only tool calls in parallel.

- `PLAN_PREFIX` turns a request into a plan-mode request (investigate read-only, then finish with a numbered plan);
  `PLAN_ONLY_ERROR` is the answer to any non-read-only call during planning; `NOT_ALLOWED` the answer to a tool that a
  restricted request (such as `/init`) was not given; `INTERRUPTED` is the result given to
  tool calls that never ran because you pressed Ctrl+C.
- `DELEGATE_SPEC` defines the **`delegate` tool** (subagents, see `_delegate`); `HELPER_PREFIX` is put in front of a
  helper's task: investigate read-only, then finish with a report of at most 200 words.
- The `finish` description says that for a **question** the summary must be the complete answer itself, never a
  description of it — a live run once ended with "Explained how rate limits work in 6 sentences" instead of the
  explanation.
- `COMPACT_PROMPT` asks the model for a short summary of the conversation (used by `compact`).
- `LOOKING` names the tools besides the read-only ones whose result is *information* (`fetch_url`, `delegate`): a
  `finish` in the same response as any of them is refused (see the finish logic below).
- `FINISH_SPEC` is the definition of the **`finish` tool**, which the agent itself handles (it is not in `tools.py`).
  The model calls it *in the same response* as its last write/run, with a summary, which saves the separate
  "final answer" model call every request used to need — but never together with a read, search, list, outline,
  fetch or delegate call, whose results it cannot have seen yet (the description says so, to save the refused call). `FAILURE_PREFIXES` and `EXIT_CODE` recognise failed tool
  results; `re` is imported for that pattern.

## `failed`
```python
 70 | def failed(result: str) -> bool:
 71 |     """True when a tool result reports a problem (error, rejected write, non-zero exit code)."""
 72 |     first = result.splitlines()[0] if result else ""
 73 |     if first.startswith(FAILURE_PREFIXES):
 74 |         return True
 75 |     match = EXIT_CODE.match(first)
 76 |     return match is not None and match.group(1) != "0"
 77 | 
 78 | 
```
- Looks at the first line of a tool result: `ERROR...`, `REJECTED...`, `NOT RUN...`, `DENIED...` or `exit code N` with
  N not 0 count as failures. Used to refuse a premature `finish` and to count "trouble" for model escalation.

## `_wants_change`
```python
 79 | def _wants_change(text: str) -> bool:
 80 |     """True when the request starts like an instruction to change code (fix, add, create...)."""
 81 |     words = text.lower().split()
 82 |     return bool(words) and words[0].strip(".,:;!?") in ACTION_WORDS
 83 | 
 84 | 
```
- Lower-cases the request, takes its first word, strips punctuation and checks it against `ACTION_WORDS`. "Fix the
  bug" -> True; "Why is this slow?" -> False. A deliberately simple rule: it costs nothing and only decides whether the
  one-time reminder may be sent, so a wrong guess just costs one extra model call or skips a reminder.

## `_parse_args`
```python
 85 | def _parse_args(raw: object) -> dict:
 86 |     """Tool arguments usually arrive as a dict, but some models send a JSON string."""
 87 |     if isinstance(raw, dict):
 88 |         return raw
 89 |     if isinstance(raw, str):
 90 |         try:
 91 |             value = json.loads(raw)
 92 |             return value if isinstance(value, dict) else {}
 93 |         except json.JSONDecodeError:
 94 |             return {}
 95 |     return {}
 96 | 
 97 | 
```
- Models are inconsistent: tool arguments may be a dict (returned as is) or a JSON string (parsed). A parse failure, a
  non-dict result, or any other type becomes `{}`; the tool then reports "bad arguments" and the model can retry.

## `Agent`
```python
 98 | class Agent:
 99 |     """Holds the conversation and drives the model/tool loop."""
100 | 
```
```python
101 |     def __init__(self, config: Config, backend: ChatBackend, toolbox: ToolBox,
102 |                  meter: Meter, log: SessionLog | None = None, depth: int = 0) -> None:
103 |         self._cfg = config
104 |         self._depth = depth                            # 0 = main agent, 1 = a helper (subagent)
105 |         self._on_event: Callable[[str], None] = lambda _m: None
106 |         self._backend = backend
107 |         self._tools = toolbox
108 |         self._meter = meter
109 |         self._log = log
110 |         self._call_ids = 0
111 |         self._seen: dict[tuple[str, str], int] = {}    # write calls made in this request
112 |         self._repeats = 0                              # identical write calls refused so far
113 |         self._plan_only = False                        # True while /plan investigates
114 |         self._allowed: frozenset[str] | None = None   # extra tools when restricted (None = all)
115 |         self.messages: list[dict] = [self._system_message()]
116 | 
```
- Stores the parts: config, backend, toolbox, meter, optional session log, and a counter for tool-call ids.
  `_plan_only` is set while `/plan` investigates; `_allowed` lists the extra tools a restricted request may use
  (`None` = no restriction). `depth` is 0 for the main agent and 1 for a helper; `_on_event` remembers the current
  request's progress display so a helper can report through it. `_seen` counts
  the write calls made in the current request and `_repeats` how many identical ones were refused (repeat guard).
- `messages` starts with just the system message (rules, `AGENT.md`, file tree).

```python
117 |     def _system_message(self) -> dict:
118 |         """A fresh system prompt (re-reads AGENT.md and the file list)."""
119 |         return {"role": "system", "content": build_system_prompt(self._cfg)}
120 | 
```
```python
121 |     def refresh_system(self) -> None:
122 |         """Rebuild the system prompt in place, e.g. after AGENT.md was written by /init."""
123 |         self.messages[0] = self._system_message()
124 | 
```
- `_system_message` builds a fresh system prompt (re-reading `AGENT.md` and the file list). `refresh_system` swaps it
  in place — used after `/init` writes `AGENT.md`, so the new notes apply at once.

```python
125 |     def _add(self, message: dict) -> None:
126 |         """Append to the history (append-only keeps the server's prompt cache valid) and log it."""
127 |         self.messages.append(message)
128 |         if self._log:
129 |             self._log.append(message)
130 | 
```
- The single place messages enter the history. It appends in memory and, if a log exists, writes the same message to
  disk. Never editing earlier messages (apart from `_trim_history`) is what keeps the server's prompt cache valid.

```python
131 |     def restore(self, messages: list[dict]) -> None:
132 |         """Continue an earlier session: re-add its messages (the system prompt is rebuilt fresh)."""
133 |         for message in messages:
134 |             if message.get("role") != "system":
135 |                 self._add(message)
136 |         self.repair()                                  # a crash may have left calls unanswered
137 | 
```
- For `--resume`: re-adds every saved message except system ones (the system prompt is rebuilt fresh so it reflects
  the current file tree and `AGENT.md`), then calls `repair`, because a session that crashed mid-task can end with tool
  calls that have no results.

```python
138 |     def repair(self) -> int:
139 |         """Give every unanswered tool call a result, so the history is valid again.
140 | 
141 |         Needed after Ctrl+C stops a task half-way: model APIs reject a conversation in
142 |         which an assistant message asked for tools whose results never arrived.
143 |         """
144 |         answered = {m.get("tool_call_id") for m in self.messages if m.get("role") == "tool"}
145 |         missing = [call for m in self.messages for call in m.get("tool_calls") or []
146 |                    if call.get("id") not in answered]
147 |         for call in missing:
148 |             self._add({"role": "tool", "tool_name": call.get("function", {}).get("name", ""),
149 |                        "tool_call_id": call.get("id"), "content": INTERRUPTED})
150 |         return len(missing)
151 | 
```
- Model APIs refuse a conversation in which an assistant message asked for tools whose results never arrived — exactly
  what Ctrl+C in the middle of a task leaves behind. `repair` collects the ids that already have results, finds every
  call without one, and appends an `INTERRUPTED` result for each (appending keeps the history valid because results
  only need to come after their request). Returns how many it fixed.

```python
152 |     def reset(self) -> None:
153 |         """Forget the conversation (keeps files on disk) and start from a fresh system prompt."""
154 |         self.messages = [self._system_message()]
155 | 
```
- Starts over with a fresh system prompt. Files on disk are untouched.

## Compaction — summaries instead of forgetting
```python
156 |     def _size(self) -> int:
157 |         """Characters in the history (about 4 per token)."""
158 |         return sum(len(str(m.get("content", ""))) for m in self.messages)
159 | 
```
```python
160 |     def _budget(self) -> int:
161 |         """History size, in characters, that fits comfortably in the context window."""
162 |         return int(self._cfg.num_ctx * 0.7) * 4
163 | 
```
- `_size`: total characters in the history (about 4 per token). `_budget`: 70 % of the context window in characters.

```python
164 |     def compact(self) -> str:
165 |         """Replace the history with a model-written summary (one model call)."""
166 |         if len(self.messages) <= 3:
167 |             return "nothing to compact"
168 |         before = self._size()
169 |         request = self.messages + [{"role": "user", "content": COMPACT_PROMPT}]
170 |         reply = self._backend.chat(request, self._tools.specs() + [FINISH_SPEC], None)
171 |         self._meter.record(reply)
172 |         summary = (reply.message.get("content") or "").strip()
173 |         if not summary:
174 |             return "compaction skipped: the model returned no summary"
175 |         self.messages = [self._system_message(),
176 |                          {"role": "user", "content": "Summary of our earlier conversation:\n" + summary},
177 |                          {"role": "assistant", "content": "Understood. I will continue from this summary."}]
178 |         if self._log:
179 |             for message in self.messages[1:]:
180 |                 self._log.append(message)
181 |         return f"compacted the conversation: {before} -> {self._size()} characters"
182 | 
```
- Like Claude Code's compaction: one model call turns the whole conversation into a summary (goals, decisions,
  files, what was verified, what is open), and the history becomes *system prompt + summary + "Understood"*. Later
  requests then send a few hundred tokens instead of the whole past, which saves tokens on every following call.
- Needs more than 3 messages. The tool list is still sent (some servers reject tool calls in the history otherwise),
  but the prompt says not to call tools; only the text is used. An empty summary leaves the history unchanged.
- The new messages are also written to the session log, so `--resume` continues from the summary.
- Used by `/compact` and automatically by `run` (below).

## `Agent.run` — one user request
```python
183 |     def run(self, user_text: str, on_event: Callable[[str], None] = lambda _m: None,
184 |             on_token: TokenSink | None = None, plan_only: bool = False,
185 |             allowed: frozenset[str] | None = None) -> str:
186 |         """Handle one user request and return the model's final answer.
187 | 
188 |         With ``plan_only`` (the /plan command) only read-only tools (and the checklist)
189 |         are offered and the answer is a plan; nothing is changed. ``allowed`` restricts a
190 |         normal request to read-only tools plus these (e.g. /init may only write files).
191 |         """
192 |         if plan_only:
193 |             allowed = frozenset({"todo", "delegate"})
194 |         if len(self.messages) > 6 and self._size() > self._cfg.compact_at * self._budget():
195 |             on_event("[conversation is long: summarising it to save tokens]")
196 |             on_event(f"[{self.compact()}]")
197 |         self._add({"role": "user", "content": (PLAN_PREFIX + user_text) if plan_only else user_text})
198 |         writes_at_start = self._tools.writes
199 |         nudges = troubles = 0
200 |         self._seen, self._repeats, self._plan_only, self._allowed = {}, 0, plan_only, allowed
201 |         self._on_event = on_event
202 |         # must this request change files? Not for plans, nor when no writing tool is offered
203 |         wants = _wants_change(user_text) and not plan_only and self._may_use("write_file")
204 |         if self._depth == 0:                           # helpers leave the routing alone
205 |             getattr(self._backend, "reset_route", lambda: None)()   # every request starts cheap
206 |         specs = [s for s in self._tools.specs() if self._may_use(s["function"]["name"])]
207 |         if self._depth == 0 and self._cfg.helpers and self._may_use("delegate"):
208 |             specs.append(DELEGATE_SPEC)                # helpers cannot start helpers
209 |         specs.append(FINISH_SPEC)
210 |         for _ in range(self._cfg.max_turns):
211 |             self._trim_history()
212 |             reply = self._backend.chat(self.messages, specs, on_token)
213 |             self._meter.record(reply)
214 |             calls = reply.message.get("tool_calls") or []
215 |             kept = {"role": "assistant", "content": reply.message.get("content") or ""}
216 |             if calls:
217 |                 for call in calls:                    # every call needs a unique id
218 |                     self._call_ids += 1
219 |                     call.setdefault("id", f"call_{self._call_ids}")
220 |                 kept["tool_calls"] = calls
221 |             self._add(kept)                           # drops bulky extras such as "thinking"
222 |             if not calls:
223 |                 if (nudges < self._cfg.max_nudges and self._tools.writes == writes_at_start
224 |                         and wants):
225 |                     nudges += 1                       # asked for a change, changed nothing
226 |                     self._add({"role": "user", "content": NUDGE})
227 |                     continue
228 |                 return kept["content"]
229 |             results = self._execute(calls, on_event)
230 |             names = [call["function"].get("name", "") for call in calls]
231 |             problems = sum(failed(r) for n, r in zip(names, results, strict=True) if n != "finish")
232 |             summary = self._finish_summary(calls)
233 |             if summary is not None:
234 |                 unseen = sorted({n for n in names if self._tools.is_read_only(n) or n in LOOKING})
235 |                 unchanged = (self._tools.writes == writes_at_start and wants
236 |                              and nudges < self._cfg.max_nudges)
237 |                 reason = ""
238 |                 if problems:
239 |                     reason = "another call in this response failed; fix it first"
240 |                 elif unseen:                          # it answered before seeing what it asked for
241 |                     reason = (f"you have not seen what {', '.join(unseen)} returned yet; "
242 |                               "read the results, then finish")
243 |                 elif unchanged:
244 |                     nudges += 1
245 |                     reason = "no file was changed yet; make the requested change first"
246 |                 if reason:                            # refuse: failed / unseen / nothing done
247 |                     results = [f"NOT FINISHED: {reason}" if n == "finish" else r
248 |                                for n, r in zip(names, results, strict=True)]
249 |             for call, name, result in zip(calls, names, results, strict=True):
250 |                 self._add({"role": "tool", "tool_name": name,
251 |                            "tool_call_id": call["id"], "content": result})
252 |             if summary is not None and not any(r.startswith("NOT FINISHED") for r in results):
253 |                 return summary                        # saved the extra "final answer" call
254 |             if self._repeats >= MAX_REPEATS:
255 |                 return "Stopped: the model kept repeating the same edit without progress."
256 |             troubles += problems
257 |             escalate = getattr(self._backend, "escalate", None) if self._depth == 0 else None
258 |             if troubles >= self._cfg.escalate_after and escalate and escalate():
259 |                 on_event("[trouble detected: switching to a stronger model]")   # first time only
260 |         return f"Stopped after {self._cfg.max_turns} model calls without finishing."
261 | 
```
- `on_event` receives progress lines (default: ignore); `on_token` receives live text from the model.
- `plan_only` (the `/plan` command): the request is stored with `PLAN_PREFIX`, only read-only tools, the `todo`
  checklist and `finish` are offered, and the "you changed nothing" checks are off (`wants` is False), because a plan
  must not change files.
- `allowed` restricts a normal request to read-only tools plus the named ones. `/init` uses it (`write_file` only):
  in the first end-to-end test the model, asked to write `AGENT.md`, noticed a failing test and fixed the bug instead
  of writing the notes. A rule in the prompt is a request; a missing tool is a guarantee.
- **Automatic compaction:** before a new request, if the history has more than 6 messages and is larger than
  `compact_at` (60 %) of the budget, it is summarised first, with two progress lines saying so. Doing it *between*
  requests means a task is never interrupted half-way.
- Adds the user's message, remembers how many writes existed at the start (for the nudge), clears the repeat-guard
  counters, and calls the backend's `reset_route()` if it has one (each request starts on the cheap models again).
- Only the main agent (`depth == 0`) resets the backend's route at the start of a request and may escalate: a helper
  shares the main agent's model chain, and found in review — without this rule a helper would have undone an
  escalation the main agent had made, and a struggling helper could have escalated for it.
- `wants` (computed once) says whether the request must change files: it looks like an instruction, it is not a
  plan, and a writing tool is offered at all (a read-only helper must never be told "you changed nothing").
- `specs` = the toolbox's tools this request may use (`_may_use`), plus `delegate` for the main agent (helpers cannot
  start helpers; `helpers=False` switches it off), plus `finish`. Plan mode may use `todo` and `delegate`. Then it loops at most `max_turns` times.
- `_trim_history()` makes room if needed; `self._backend.chat(...)` calls the model with the full history and the tool
  specs; `self._meter.record(reply)` accounts for tokens and time.
- `calls` is the list of requested tools (`or []` handles `None`). `kept` is the message we store: role and text only.
- If there are calls, each gets a unique id (`call.setdefault("id", ...)`) because OpenAI-style servers need ids to
  match results to requests; then they are attached to `kept`. `_add(kept)` stores it (dropping `thinking` etc.).
- No calls → normally this is the final answer. **Exception (the nudge):** if the request looked like an instruction to
  change code (`_wants_change`), no file was successfully written during this request (`toolbox.writes` unchanged since
  the request began) and fewer than `max_nudges` reminders were sent, the agent appends the `NUDGE` user message and
  loops again instead of returning. This was added because the real 8B model sometimes read a file, explained the bug in
  text and stopped without editing it. Questions are never nudged.
- Otherwise `_execute` runs the turn's calls (below) and returns one result per call in the same order.
  `problems` counts failed results (ignoring `finish` itself).
- **The finish logic:** if the turn contains `finish` (`_finish_summary`), it is *refused*, for the first reason that
  applies: another call in the same turn failed; the turn also **looked** at something (`unseen`: a read-only tool or
  one in `LOOKING`) — the summary was written before those results existed; or a change was requested but nothing has
  been written yet (counted like a nudge). A refused finish gets the result `NOT FINISHED: <reason>` so the model
  knows to continue.
- Why the `unseen` rule exists (found in the final verification): asked "list the files and say what `report.js`
  does", `gemini-3.5-flash-lite` sent `read_file` **and** `finish` in one response, so its answer was written before
  it had seen the file — and was wrong ("a report by category and month"; the script prints one total). Finishing in
  the same response as a *write or run* stays allowed: their success is checked by the agent (a failure refuses the
  finish), whereas what a read returns can only be judged by the model, after seeing it.
- Every result is appended as a `tool` message carrying the call id (also the `finish` one, so the history stays a
  valid conversation for later requests). `zip(..., strict=True)` guarantees calls and results line up.
- An accepted finish returns the summary immediately: **no extra model call**.
- **Escalation:** failures are added to `troubles`. Once they reach `escalate_after` (2) and the backend has an
  `escalate` method, it is called; the first time it actually switches, the event
  `[trouble detected: switching to a stronger model]` is shown. Cheap models do the routine work; the scarce strong
  models are only used when the cheap one is struggling.
- **Repeat guard exit:** after the results are stored, if `_repeats` reached `MAX_REPEATS` the request ends at once with
  a "Stopped: the model kept repeating the same edit" message. This was added after a real run where the 8B model sent
  the *same* `edit_file` call 18 times (20 model calls, 43 k tokens, still failing).
- If the loop ends without a final answer, return a clear "Stopped after N model calls" message.

## `Agent._may_use`
```python
262 |     def _may_use(self, name: str) -> bool:
263 |         """Whether this request may use the tool (always, unless it is restricted)."""
264 |         return (self._allowed is None or self._tools.is_read_only(name)
265 |                 or name in self._allowed)
266 | 
```
- Without a restriction every tool may be used; with one, read-only tools and the explicitly allowed ones.

## `Agent._delegate` — subagents
```python
267 |     def _delegate(self, task: str) -> str:
268 |         """Run a read-only helper agent with a fresh context; return only its report."""
269 |         if self._depth > 0 or not self._cfg.helpers:
270 |             return "ERROR: helpers are not available here"
271 |         if not task.strip():
272 |             return "ERROR: describe the task for the helper"
273 |         helper = Agent(replace(self._cfg, max_turns=self._cfg.helper_turns), self._backend,
274 |                        self._tools, self._meter, None, depth=self._depth + 1)
275 |         self._on_event(f"   [helper started: {task[:80]}]")
276 |         report = helper.run(HELPER_PREFIX + task, on_event=lambda m: self._on_event("   | " + m),
277 |                             allowed=frozenset())
278 |         self._on_event("   [helper finished]")
279 |         return truncate("HELPER REPORT:\n" + report, self._cfg.max_output_chars)
280 | 
```
- Like Claude Code's Task/Explore agents: the main agent hands a self-contained investigation ("find where invoices are
  calculated and explain the flow") to a **helper** — a second `Agent` with a fresh, empty conversation, the same model
  chain, toolbox and meter, at most `helper_turns` (8) model calls, and only read-only tools (`allowed=frozenset()`).
- The helper's steps are shown indented with `|`; only its final report (cut to `max_output_chars`) enters the main
  conversation. Benefit: the main context stays small on big codebases, because the dozens of file excerpts the helper
  read never enter it. Cost: extra model calls — so the tool description tells the model to use it only for broad
  searches. Measured live: one delegated investigation of this project took 8 calls in total and produced a correct,
  sourced answer.
- Refused inside a helper (no chains of helpers), when switched off, or without a task.

## `Agent._finish_summary`
```python
281 |     @staticmethod
282 |     def _finish_summary(calls: list[dict]) -> str | None:
283 |         """The summary text if this turn contains a finish call, else None."""
284 |         for call in calls:
285 |             function = call.get("function", {})
286 |             if function.get("name") == "finish":
287 |                 return str(_parse_args(function.get("arguments")).get("summary", "Done."))
288 |         return None
289 | 
```
- Returns the `summary` argument of a `finish` call in this turn (default "Done."), or `None` when there is none.

## `Agent._run_one` and `Agent._execute` — running a turn's tool calls
```python
290 |     def _run_one(self, call: dict) -> str:
291 |         """Run one requested tool and return its (truncated) result text."""
292 |         function = call.get("function", {})
293 |         name = function.get("name", "")
294 |         args = _parse_args(function.get("arguments"))
295 |         if name == "finish":
296 |             return "OK: finished"                     # handled by run(); nothing to execute
297 |         if not self._may_use(name):
298 |             return PLAN_ONLY_ERROR if self._plan_only else NOT_ALLOWED
299 |         if name == "delegate":
300 |             return self._delegate(str(args.get("task", "")))
301 |         if name in WRITE_TOOLS:
302 |             key = (name, json.dumps(args, sort_keys=True))
303 |             self._seen[key] = self._seen.get(key, 0) + 1
304 |             if self._seen[key] > 1:                   # same write again: refuse, count it
305 |                 self._repeats += 1
306 |                 return REPEAT_WARNING
307 |         return truncate(self._tools.call(name, args), self._cfg.max_output_chars)
308 | 
```
- Reads the call's function name and arguments (normalised by `_parse_args`). `finish` is not executed; it just
  returns `OK: finished` (the decision is made in `run`); `delegate` starts a helper. A tool this request may not use
  is refused with
  `PLAN_ONLY_ERROR` (plan mode) or `NOT_ALLOWED` — a second lock behind the reduced tool list, in case a model calls a
  tool it was not offered.
- **Repeat guard:** for write tools only (`WRITE_TOOLS`) it counts identical `(name, arguments)` pairs within the request.
  The first is executed; any identical repeat is *not* executed and returns `REPEAT_WARNING` (telling the model to
  re-read the file and try something different), and `_repeats` goes up. Read/run tools are exempt because repeating
  `run_tests` after an edit is legitimate.
- Otherwise runs the tool through `ToolBox.call` (which applies every verification gate) and truncates the result.

```python
309 |     def _execute(self, calls: list[dict], on_event: Callable[[str], None]) -> list[str]:
310 |         """Run a turn's tool calls; read-only batches run in parallel, others one by one."""
311 |         def announce(call: dict) -> None:
312 |             function = call.get("function", {})
313 |             shown = json.dumps(_parse_args(function.get("arguments")))[:100]
314 |             on_event(f"-> {function.get('name', '')} {shown}")
315 | 
316 |         def report(name: str, result: str) -> None:
317 |             if name == "todo":                        # show the whole checklist
318 |                 for line in result.splitlines():
319 |                     on_event(f"   {line}"[:140])
320 |                 return
321 |             on_event(f"   {result.splitlines()[0] if result else ''}"[:140])
322 | 
323 |         names = [call.get("function", {}).get("name", "") for call in calls]
324 |         if len(calls) > 1 and all(self._tools.is_read_only(n) for n in names):
325 |             for call in calls:
326 |                 announce(call)
327 |             with ThreadPoolExecutor(max_workers=min(len(calls), 4)) as pool:
328 |                 results = list(pool.map(self._run_one, calls))   # map keeps the original order
329 |             for name, result in zip(names, results, strict=True):
330 |                 report(name, result)
331 |             return results
332 |         results = []
333 |         for call, name in zip(calls, names, strict=True):
334 |             announce(call)
335 |             results.append(self._run_one(call))
336 |             report(name, results[-1])
337 |             if name in WRITE_TOOLS:
338 |                 self._show_diff(on_event)
339 |         return results
340 | 
```
- `announce` prints `-> name args`; `report` prints the first line of a result — or, for `todo`, the whole
  checklist, so you can follow the plan. Both only feed `on_event`.
- **Parallel path:** when a turn has more than one call and *every* call is read-only (`list_files`, `read_file`,
  `search`, `outline`), all calls are announced, then run together in a thread pool (at most 4 workers).
  `pool.map` returns results in the original order, so the history is identical to sequential execution - only
  faster (useful when several large files are read at once). Writes and runs are never parallel: they must keep their
  order and their approval prompts.
- **Sequential path:** announce, run, report, one call at a time, so you see a call before its approval question.
  After every write tool, `_show_diff` displays what changed.

```python
341 |     def _show_diff(self, on_event: Callable[[str], None]) -> None:
342 |         """Show the user what the last write changed (never sent to the model: saves tokens)."""
343 |         diff = self._tools.pop_diff()
344 |         limit = self._cfg.diff_lines
345 |         for line in diff[:limit]:
346 |             on_event(f"     {line}"[:160])
347 |         if len(diff) > limit:
348 |             on_event(f"     ... {len(diff) - limit} more diff lines")
349 | 
```
- Takes the toolbox's diff of the last write and shows at most `diff_lines` (40) lines, indented under the tool result,
  e.g. `@@ -1,2 +1,2 @@`, `-old line`, `+new line`, then `... N more diff lines` if it was longer. The diff goes **only to
  your screen**: the model already knows what it wrote, so sending it back would waste tokens.

## `Agent._trim_history`
```python
350 |     def _trim_history(self) -> None:
351 |         """Blank the oldest tool outputs when the prompt nears the context limit."""
352 |         budget = self._budget()
353 |         size = self._size()
354 |         for message in self.messages[:-4]:               # never touch the newest turns
355 |             if size <= budget:
356 |                 return
357 |             if message["role"] == "tool" and message["content"] != REMOVED:
358 |                 size -= len(message["content"]) - len(REMOVED)
359 |                 message["content"] = REMOVED
```
- `budget` and `size` come from `_budget` and `_size`.
- Walks everything except the newest 4 messages; once under budget it stops. Only `tool` messages not already blanked
  are replaced by `REMOVED`, after subtracting the saved size. User and assistant messages are kept so the
  conversation stays coherent. Trade-off: this rewrites history (invalidating the server's cache once), but only when
  the context is nearly full — when something must go anyway.
