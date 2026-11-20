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
 18 | from .llm import REMOVED, ChatBackend, TokenSink, fit_history, message_size
 19 | from .meter import Meter
 20 | from .sandbox import truncate
 21 | from .session import SessionLog
 22 | from .tools import ToolBox
 23 | 
 24 | __all__ = ["REMOVED", "Agent", "failed"]                 # REMOVED re-exported (tests use it)
 25 | ACTION_WORDS = frozenset({"fix", "add", "create", "write", "implement", "change", "update",
 26 |                           "refactor", "remove", "rename", "make", "build", "modify", "delete",
 27 |                           "edit", "insert", "replace"})
 28 | FILLER_WORDS = frozenset({"please", "pls", "kindly", "can", "could", "would", "will", "you", "u",
 29 |                           "now", "also", "then", "and", "just", "ok", "okay", "so", "i", "we",
 30 |                           "want", "need", "to", "lets", "let's", "go", "ahead", "quickly"})
 31 | EMPTY_REPLY = ("Your last reply was empty. Answer the request, or call the tools you need.")
 32 | BAD_ARGUMENTS = ("ERROR: the arguments of this call were not valid JSON (the reply may have been "
 33 |                  "cut off). Send the call again; for a big file, write it in smaller parts with "
 34 |                  "edit_file.")
 35 | WRITE_TOOLS = frozenset({"write_file", "edit_file", "multi_edit"})
 36 | MAX_REPEATS = 3
 37 | REPEAT_WARNING = ("ERROR: you already made this exact call. Repeating it cannot help. Use read_file to "
 38 |                   "look at the file as it is now, then try something different or explain the problem.")
 39 | NUDGE = ("You have not changed any file yet. If the request needs a change, make it now with "
 40 |          "edit_file, multi_edit or write_file. If it was only a question, repeat your answer.")
 41 | LOOKING = frozenset({"fetch_url", "delegate"})   # like reads: their result is information
 42 | FINISH_SPEC = {"type": "function", "function": {
 43 |     "name": "finish",
 44 |     "description": "End the task. Call it in the SAME response as your last write/run, but never "
 45 |                    "together with a read, search, list, outline, fetch or delegate call (you have "
 46 |                    "not seen their results yet). 'summary' "
 47 |                    "is what the user reads: for a question, the COMPLETE answer itself (never a "
 48 |                    "description like 'I explained X'); for a task, what you did and verified. It is "
 49 |                    "refused if another call in that response fails.",
 50 |     "parameters": {"type": "object", "properties": {"summary": {"type": "string"}},
 51 |                    "required": ["summary"]}}}
 52 | COMPACT_PROMPT = ("Summarise this conversation so you can continue the work later without it: the "
 53 |                   "user's goals, decisions made, files created or changed (with paths), what was "
 54 |                   "verified, and what is still open. Plain text, at most 300 words. Do not call tools.")
 55 | PLAN_PREFIX = ("PLAN MODE - do not change anything yet. Investigate with read-only tools "
 56 |                "(read_file, search, outline, list_files), then call finish with a numbered plan: "
 57 |                "which files to change, what to change in each, and how to verify it.\nRequest: ")
 58 | PLAN_ONLY_ERROR = ("ERROR: plan mode: only read-only tools (read_file, search, outline, list_files) "
 59 |                    "may be used now; describe the change in your plan instead.")
 60 | NOT_ALLOWED = "ERROR: that tool is not available for this request; use only the tools you were given."
 61 | DELEGATE_SPEC = {"type": "function", "function": {
 62 |     "name": "delegate",
 63 |     "description": "Give a self-contained investigation to a helper agent with a fresh, empty context "
 64 |                    "(e.g. 'find where invoices are calculated and explain the flow'). It can only "
 65 |                    "read and search, and returns a short report. Use it only for broad searches across "
 66 |                    "many files, to keep your own context small; it costs extra model calls.",
 67 |     "parameters": {"type": "object", "properties": {"task": {"type": "string"}},
 68 |                    "required": ["task"]}}}
 69 | HELPER_PREFIX = ("You are a helper agent. Investigate with read-only tools only, then call finish with "
 70 |                  "a concise report (at most 200 words): the relevant files, functions and line "
 71 |                  "numbers, and how things work. Task: ")
 72 | INTERRUPTED = "INTERRUPTED: the user stopped the task before this ran."
 73 | EMPTY_TWICE = ("The model sent an empty answer twice. Try rephrasing the request, or another "
 74 |                "model (/model).")
 75 | FAILURE_PREFIXES = ("ERROR", "REJECTED", "NOT RUN", "DENIED")
 76 | EXIT_CODE = re.compile(r"exit code (-?\d+)")
 77 | CUT_SUMMARY = re.compile(r'"summary"\s*:\s*"(.*)', re.DOTALL)
 78 | 
 79 | 
```
- Imports `json` (parse/print tool arguments), `replace` (copy the config for a helper or a model switch), `Callable`,
  and the components wired together: config, system-prompt builder, backend interface and token-sink type, the history
  helpers `fit_history` and `message_size` (`llm.py`), meter, `truncate`, session log, toolbox.
- `REMOVED` (the placeholder that replaces old tool output when space runs short) now lives in `llm.py` with the rest
  of the history fitting; `__all__` re-exports it so older imports keep working.
- `FILLER_WORDS` are the polite words skipped before looking for an action verb (see `_wants_change`).
- `EMPTY_REPLY` is the reminder sent after an empty answer; `EMPTY_TWICE` the message when the model answers with
  nothing twice. `BAD_ARGUMENTS` is the result of a tool call whose arguments are broken JSON. `CUT_SUMMARY` finds the
  start of a `finish` summary that was cut off.
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
 80 | def failed(result: str) -> bool:
 81 |     """True when a tool result reports a problem (error, rejected write, non-zero exit code)."""
 82 |     first = result.splitlines()[0] if result else ""
 83 |     if first.startswith(FAILURE_PREFIXES):
 84 |         return True
 85 |     match = EXIT_CODE.match(first)
 86 |     return match is not None and match.group(1) != "0"
 87 | 
 88 | 
```
- Looks at the first line of a tool result: `ERROR...`, `REJECTED...`, `NOT RUN...`, `DENIED...` or `exit code N` with
  N not 0 count as failures. Used to refuse a premature `finish` and to count "trouble" for model escalation.

## `_wants_change`
```python
 89 | def _wants_change(text: str) -> bool:
 90 |     """True when the request starts like an instruction to change code (fix, add, create...),
 91 |     also after polite words: "please fix", "can you add", "go ahead and create"."""
 92 |     for word in text.lower().split():
 93 |         word = word.strip(".,:;!?")
 94 |         if word not in FILLER_WORDS:
 95 |             return word in ACTION_WORDS
 96 |     return False
 97 | 
 98 | 
```
- Lower-cases the request and skips polite words (`FILLER_WORDS`: "please", "can you", "go ahead and", "I want you
  to" ...); the first other word, stripped of punctuation, must be an `ACTION_WORDS` verb. "Fix the bug" and "Please fix
  the bug" -> True; "Why is this slow?" and "Can you explain this?" -> False. Before 0.6 only the very first word
  counted, so the most natural way to ask — "please fix ..." — never got the reminder. A deliberately simple rule: it
  costs nothing and only decides whether the one-time reminder may be sent, so a wrong guess just costs one extra model
  call or skips a reminder.

## `_parse_args` and `_broken_args`
```python
 99 | def _parse_args(raw: object) -> dict:
100 |     """Tool arguments usually arrive as a dict, but some models send a JSON string."""
101 |     if isinstance(raw, dict):
102 |         return raw
103 |     if isinstance(raw, str):
104 |         try:
105 |             value = json.loads(raw)
106 |             return value if isinstance(value, dict) else {}
107 |         except json.JSONDecodeError:
108 |             return {}
109 |     return {}
110 | 
111 | 
```
- Models are inconsistent: tool arguments may be a dict (returned as is) or a JSON string (parsed). A parse failure, a
  non-dict result, or any other type becomes `{}`.

```python
112 | def _broken_args(raw: object) -> bool:
113 |     """True when the arguments are text that is not valid JSON (usually a cut-off reply)."""
114 |     if not isinstance(raw, str) or not raw.strip():
115 |         return False
116 |     try:
117 |         json.loads(raw)
118 |     except json.JSONDecodeError:
119 |         return True
120 |     return False
121 | 
122 | 
```
- True when the arguments are text that is not valid JSON. That nearly always means the model's answer was **cut off**
  (it hit its output limit in the middle of a long `write_file`). Before 0.6 such a call reached the tool as `{}` and
  the model was told "bad arguments: KeyError('path')" — so it hunted for a mistake in its arguments instead of
  writing less at once.

## `Agent`
```python
123 | class Agent:
124 |     """Holds the conversation and drives the model/tool loop."""
125 | 
```
```python
126 |     def __init__(self, config: Config, backend: ChatBackend, toolbox: ToolBox,
127 |                  meter: Meter, log: SessionLog | None = None, depth: int = 0) -> None:
128 |         self._cfg = config
129 |         self._depth = depth                            # 0 = main agent, 1 = a helper (subagent)
130 |         self._on_event: Callable[[str], None] = lambda _m: None
131 |         self._backend = backend
132 |         self._tools = toolbox
133 |         self._meter = meter
134 |         self._log = log
135 |         self._call_ids = 0
136 |         self._seen: dict[tuple[str, str], int] = {}    # write calls made in this request
137 |         self._repeats = 0                              # identical write calls refused so far
138 |         self._plan_only = False                        # True while /plan investigates
139 |         self._allowed: frozenset[str] | None = None   # extra tools when restricted (None = all)
140 |         self.messages: list[dict] = [self._system_message()]
141 | 
```
- Stores the parts: config, backend, toolbox, meter, optional session log, and a counter for tool-call ids.
  `_plan_only` is set while `/plan` investigates; `_allowed` lists the extra tools a restricted request may use
  (`None` = no restriction). `depth` is 0 for the main agent and 1 for a helper; `_on_event` remembers the current
  request's progress display so a helper can report through it. `_seen` counts
  the write calls made in the current request and `_repeats` how many identical ones were refused (repeat guard).
- `messages` starts with just the system message (rules, `AGENT.md`, file tree).

```python
142 |     def _system_message(self) -> dict:
143 |         """A fresh system prompt (re-reads AGENT.md and the file list)."""
144 |         return {"role": "system", "content": build_system_prompt(self._cfg)}
145 | 
```
```python
146 |     def refresh_system(self) -> None:
147 |         """Rebuild the system prompt in place, e.g. after AGENT.md was written by /init."""
148 |         self.messages[0] = self._system_message()
149 | 
```
- `_system_message` builds a fresh system prompt (re-reading `AGENT.md` and the file list). `refresh_system` swaps it
  in place — used after `/init` writes `AGENT.md` and after a `# note`, so the new notes apply at once.

## Switching models mid-conversation
```python
150 |     @property
151 |     def backend(self) -> ChatBackend:
152 |         """The model client answering now (its ``last_model`` says which model answered)."""
153 |         return self._backend
154 | 
```
- The client that answers now. The CLI reads its `last_model` to show who answered each request.

```python
155 |     def set_backend(self, backend: ChatBackend, config: Config) -> None:
156 |         """Answer with another model from now on (/model online or offline), conversation kept.
157 | 
158 |         The new config brings the new model's history budget: going offline usually shrinks
159 |         it from 32K to 8K tokens, and the next request compacts or trims to fit.
160 |         """
161 |         self._backend = backend
162 |         self._cfg = replace(self._cfg, num_ctx=config.num_ctx, backend=config.backend,
163 |                             mode=config.mode, fallback_backend=config.fallback_backend)
164 | 
```
- Used by `/model online|offline|auto`: from now on another model answers, and the **conversation stays**. The history
  is stored in one neutral shape that every backend converts on the way out (Gemini's thought signatures are kept for
  Gemini and never sent elsewhere — see `openai_compat.md`), so nothing needs converting here.
- The new config brings the new history budget (and mode and backend names). Going offline usually shrinks the budget
  from 32K to 8K tokens; nothing is cut at once — the next request compacts or trims to fit.

```python
165 |     def _add(self, message: dict) -> None:
166 |         """Append to the history (append-only keeps the server's prompt cache valid) and log it."""
167 |         self.messages.append(message)
168 |         if self._log:
169 |             self._log.append(message)
170 | 
```
- The single place messages enter the history. It appends in memory and, if a log exists, writes the same message to
  disk. Never editing earlier messages (apart from `_trim_history`) is what keeps the server's prompt cache valid.

```python
171 |     def restore(self, messages: list[dict]) -> None:
172 |         """Continue an earlier session: re-add its messages (the system prompt is rebuilt fresh)."""
173 |         for message in messages:
174 |             if message.get("role") != "system":
175 |                 self._add(message)
176 |         self.repair()                                  # a crash may have left calls unanswered
177 | 
```
- For `--resume`: re-adds every saved message except system ones (the system prompt is rebuilt fresh so it reflects
  the current file tree and `AGENT.md`), then calls `repair`, because a session that crashed mid-task can end with tool
  calls that have no results.

```python
178 |     def repair(self) -> int:
179 |         """Give every unanswered tool call a result, so the history is valid again.
180 | 
181 |         Needed after Ctrl+C stops a task half-way: model APIs reject a conversation in
182 |         which an assistant message asked for tools whose results never arrived.
183 |         """
184 |         answered = {m.get("tool_call_id") for m in self.messages if m.get("role") == "tool"}
185 |         missing = [call for m in self.messages for call in m.get("tool_calls") or []
186 |                    if call.get("id") not in answered]
187 |         for call in missing:
188 |             self._add({"role": "tool", "tool_name": call.get("function", {}).get("name", ""),
189 |                        "tool_call_id": call.get("id"), "content": INTERRUPTED})
190 |         return len(missing)
191 | 
```
- Model APIs refuse a conversation in which an assistant message asked for tools whose results never arrived — exactly
  what Ctrl+C in the middle of a task leaves behind. `repair` collects the ids that already have results, finds every
  call without one, and appends an `INTERRUPTED` result for each (appending keeps the history valid because results
  only need to come after their request). Returns how many it fixed.

```python
192 |     def reset(self) -> None:
193 |         """Forget the conversation (keeps files on disk) and start from a fresh system prompt."""
194 |         self.messages = [self._system_message()]
195 |         if self._log:
196 |             self._log.mark("reset")                    # --resume starts after this point
197 | 
```
- Starts over with a fresh system prompt (`/clear`, `/reset`). Files on disk are untouched. The session log gets a
  `reset` event, so `--resume` does not bring the cleared conversation back (it did before 0.6).

## Compaction — summaries instead of forgetting
```python
198 |     def _size(self) -> int:
199 |         """Characters in the history, tool-call arguments included (about 4 per token)."""
200 |         return sum(message_size(m) for m in self.messages)
201 | 
```
```python
202 |     def _budget(self) -> int:
203 |         """History size, in characters, that fits comfortably in the context window."""
204 |         return int(self._cfg.num_ctx * 0.7) * 4
205 | 
```
- `_size`: total characters in the history — text **and tool-call arguments** (`message_size`; about 4 characters per
  token). Before 0.6 the arguments were not counted, so the file contents of every `write_file` were invisible to the
  budget. `_budget`: 70 % of the context window in characters.

```python
206 |     def compact(self) -> str:
207 |         """Replace the history with a model-written summary (one model call)."""
208 |         if len(self.messages) <= 3:
209 |             return "nothing to compact"
210 |         before = self._size()
211 |         prompt = {"role": "user", "content": COMPACT_PROMPT}
212 |         request = fit_history(self.messages, self._budget()) + [prompt]   # must fit to be sent
213 |         reply = self._backend.chat(request, self._tools.specs() + [FINISH_SPEC], None)
214 |         self._meter.record(reply)
215 |         summary = (reply.message.get("content") or "").strip()
216 |         if not summary:
217 |             return "compaction skipped: the model returned no summary"
218 |         self.messages = [self._system_message(),
219 |                          {"role": "user", "content": "Summary of our earlier conversation:\n" + summary},
220 |                          {"role": "assistant", "content": "Understood. I will continue from this summary."}]
221 |         if self._log:
222 |             self._log.mark("compact")                  # --resume continues from the summary
223 |             for message in self.messages[1:]:
224 |                 self._log.append(message)
225 |         return f"compacted the conversation: {before} -> {self._size()} characters"
226 | 
```
- Like Claude Code's compaction: one model call turns the whole conversation into a summary (goals, decisions,
  files, what was verified, what is open), and the history becomes *system prompt + summary + "Understood"*. Later
  requests then send a few hundred tokens instead of the whole past, which saves tokens on every following call.
- Needs more than 3 messages. The history sent with the request is first fitted to the budget (`fit_history`): the
  request that asks for the summary must itself fit the model's window, or the model refuses it — which is exactly
  when compaction is needed most, for example right after switching to the 8K local model.
- The tool list is still sent (some servers reject tool calls in the history otherwise), but the prompt says not to
  call tools; only the text is used. An empty summary leaves the history unchanged.
- The session log gets a `compact` event followed by the new messages, so `--resume` continues from the summary
  instead of restoring the whole long past *and* the summary (which overflowed the next request before 0.6).
- Used by `/compact` and automatically by `run` (below).

## `Agent.run` — one user request
```python
227 |     def run(self, user_text: str, on_event: Callable[[str], None] = lambda _m: None,
228 |             on_token: TokenSink | None = None, plan_only: bool = False,
229 |             allowed: frozenset[str] | None = None) -> str:
230 |         """Handle one user request and return the model's final answer.
231 | 
232 |         With ``plan_only`` (the /plan command) only read-only tools (and the checklist)
233 |         are offered and the answer is a plan; nothing is changed. ``allowed`` restricts a
234 |         normal request to read-only tools plus these (e.g. /init may only write files).
235 |         """
236 |         if plan_only:
237 |             allowed = frozenset({"todo", "delegate"})
238 |         if len(self.messages) > 6 and self._size() > self._cfg.compact_at * self._budget():
239 |             on_event("[conversation is long: summarising it to save tokens]")
240 |             on_event(f"[{self.compact()}]")
241 |         self._add({"role": "user", "content": (PLAN_PREFIX + user_text) if plan_only else user_text})
242 |         writes_at_start = self._tools.writes
243 |         nudges = troubles = 0
244 |         empty_retried = False
245 |         self._seen, self._repeats, self._plan_only, self._allowed = {}, 0, plan_only, allowed
246 |         self._on_event = on_event
247 |         # must this request change files? Not for plans, nor when no writing tool is offered
248 |         wants = _wants_change(user_text) and not plan_only and self._may_use("write_file")
249 |         if self._depth == 0:                           # helpers leave the routing alone
250 |             getattr(self._backend, "reset_route", lambda: None)()   # every request starts cheap
251 |         specs = [s for s in self._tools.specs() if self._may_use(s["function"]["name"])]
252 |         if self._depth == 0 and self._cfg.helpers and self._may_use("delegate"):
253 |             specs.append(DELEGATE_SPEC)                # helpers cannot start helpers
254 |         specs.append(FINISH_SPEC)
255 |         for _ in range(self._cfg.max_turns):
256 |             self._trim_history()
257 |             reply = self._backend.chat(self.messages, specs, on_token)
258 |             self._meter.record(reply)
259 |             calls = reply.message.get("tool_calls") or []
260 |             if not calls and not (reply.message.get("content") or "").strip():
261 |                 if empty_retried:                     # an empty answer is a failure, not a reply
262 |                     return EMPTY_TWICE
263 |                 empty_retried = True                  # placeholder: some APIs reject empty turns
264 |                 self._add({"role": "assistant", "content": "(empty reply)"})
265 |                 self._add({"role": "user", "content": EMPTY_REPLY})
266 |                 continue
267 |             kept = {"role": "assistant", "content": reply.message.get("content") or ""}
268 |             if calls:
269 |                 for call in calls:                    # every call needs a unique id
270 |                     self._call_ids += 1
271 |                     call.setdefault("id", f"call_{self._call_ids}")
272 |                 kept["tool_calls"] = calls
273 |             self._add(kept)                           # drops bulky extras such as "thinking"
274 |             if not calls:
275 |                 if (nudges < self._cfg.max_nudges and self._tools.writes == writes_at_start
276 |                         and wants):
277 |                     nudges += 1                       # asked for a change, changed nothing
278 |                     self._add({"role": "user", "content": NUDGE})
279 |                     continue
280 |                 return kept["content"]
281 |             results = self._execute(calls, on_event)
282 |             names = [call["function"].get("name", "") for call in calls]
283 |             problems = sum(failed(r) for n, r in zip(names, results, strict=True) if n != "finish")
284 |             summary = self._finish_summary(calls)
285 |             if summary is not None:
286 |                 unseen = sorted({n for n in names if self._tools.is_read_only(n) or n in LOOKING})
287 |                 unchanged = (self._tools.writes == writes_at_start and wants
288 |                              and nudges < self._cfg.max_nudges)
289 |                 reason = ""
290 |                 if problems:
291 |                     reason = "another call in this response failed; fix it first"
292 |                 elif unseen:                          # it answered before seeing what it asked for
293 |                     reason = (f"you have not seen what {', '.join(unseen)} returned yet; "
294 |                               "read the results, then finish")
295 |                 elif unchanged:
296 |                     nudges += 1
297 |                     reason = "no file was changed yet; make the requested change first"
298 |                 if reason:                            # refuse: failed / unseen / nothing done
299 |                     results = [f"NOT FINISHED: {reason}" if n == "finish" else r
300 |                                for n, r in zip(names, results, strict=True)]
301 |             for call, name, result in zip(calls, names, results, strict=True):
302 |                 self._add({"role": "tool", "tool_name": name,
303 |                            "tool_call_id": call["id"], "content": result})
304 |             if summary is not None and not any(r.startswith("NOT FINISHED") for r in results):
305 |                 return summary                        # saved the extra "final answer" call
306 |             if self._repeats >= MAX_REPEATS:
307 |                 return "Stopped: the model kept repeating the same edit without progress."
308 |             troubles += problems
309 |             escalate = getattr(self._backend, "escalate", None) if self._depth == 0 else None
310 |             if troubles >= self._cfg.escalate_after and escalate and escalate():
311 |                 on_event("[trouble detected: switching to a stronger model]")   # first time only
312 |         return f"Stopped after {self._cfg.max_turns} model calls without finishing."
313 | 
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
- `calls` is the list of requested tools (`or []` handles `None`).
- **An empty answer** (no text, no tool calls) is not an answer. Gemini sometimes returns one when its tool call went
  wrong on its side. Before 0.6 it was shown to you as a blank reply. Now the agent stores a placeholder
  `(empty reply)` — Gemini rejects conversations containing an empty turn — adds `EMPTY_REPLY`, and asks once more
  (`empty_retried`); a second empty answer ends the request with `EMPTY_TWICE`, which suggests rephrasing or another
  model.
- `kept` is the message we store: role and text only.
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
314 |     def _may_use(self, name: str) -> bool:
315 |         """Whether this request may use the tool (always, unless it is restricted)."""
316 |         return (self._allowed is None or self._tools.is_read_only(name)
317 |                 or name in self._allowed)
318 | 
```
- Without a restriction every tool may be used; with one, read-only tools and the explicitly allowed ones.

## `Agent._delegate` — subagents
```python
319 |     def _delegate(self, task: str) -> str:
320 |         """Run a read-only helper agent with a fresh context; return only its report."""
321 |         if self._depth > 0 or not self._cfg.helpers:
322 |             return "ERROR: helpers are not available here"
323 |         if not task.strip():
324 |             return "ERROR: describe the task for the helper"
325 |         helper = Agent(replace(self._cfg, max_turns=self._cfg.helper_turns), self._backend,
326 |                        self._tools, self._meter, None, depth=self._depth + 1)
327 |         self._on_event(f"   [helper started: {task[:80]}]")
328 |         report = helper.run(HELPER_PREFIX + task, on_event=lambda m: self._on_event("   | " + m),
329 |                             allowed=frozenset())
330 |         self._on_event("   [helper finished]")
331 |         return truncate("HELPER REPORT:\n" + report, self._cfg.max_output_chars)
332 | 
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
333 |     @staticmethod
334 |     def _finish_summary(calls: list[dict]) -> str | None:
335 |         """The summary text if this turn contains a finish call, else None. A summary cut off
336 |         mid-way (broken JSON) is rescued as far as it got, rather than lost."""
337 |         for call in calls:
338 |             function = call.get("function", {})
339 |             if function.get("name") == "finish":
340 |                 raw = function.get("arguments")
341 |                 cut = CUT_SUMMARY.search(raw) if _broken_args(raw) else None
342 |                 if cut:
343 |                     text = cut.group(1).replace("\\n", "\n").replace('\\"', '"').rstrip('"} \n')
344 |                     return text + " [answer cut off]"
345 |                 return str(_parse_args(raw).get("summary", "Done."))
346 |         return None
347 | 
```
- Returns the `summary` argument of a `finish` call in this turn (default "Done."), or `None` when there is none.
- When the arguments were cut off, the summary is rescued as far as it got (`CUT_SUMMARY` finds its start; escaped
  line breaks and quotes are restored) and marked `[answer cut off]` — better than losing a long answer completely.

## `Agent._run_one` and `Agent._execute` — running a turn's tool calls
```python
348 |     def _run_one(self, call: dict) -> str:
349 |         """Run one requested tool and return its (truncated) result text."""
350 |         function = call.get("function", {})
351 |         name = function.get("name", "")
352 |         if name == "finish":
353 |             return "OK: finished"                     # handled by run(); nothing to execute
354 |         if _broken_args(function.get("arguments")):
355 |             return BAD_ARGUMENTS                      # say why, instead of "missing 'path'"
356 |         args = _parse_args(function.get("arguments"))
357 |         if not self._may_use(name):
358 |             return PLAN_ONLY_ERROR if self._plan_only else NOT_ALLOWED
359 |         if name == "delegate":
360 |             return self._delegate(str(args.get("task", "")))
361 |         if name in WRITE_TOOLS:
362 |             key = (name, json.dumps(args, sort_keys=True))
363 |             self._seen[key] = self._seen.get(key, 0) + 1
364 |             if self._seen[key] > 1:                   # same write again: refuse, count it
365 |                 self._repeats += 1
366 |                 return REPEAT_WARNING
367 |         return truncate(self._tools.call(name, args), self._cfg.max_output_chars)
368 | 
```
- Reads the call's function name. `finish` is not executed; it just returns `OK: finished` (the decision is made in
  `run`). Broken (cut-off) arguments return `BAD_ARGUMENTS`, which says what happened and suggests writing a big file
  in smaller parts. Otherwise the arguments are normalised by `_parse_args`; `delegate` starts a helper. A tool this
  request may not use is refused with
  `PLAN_ONLY_ERROR` (plan mode) or `NOT_ALLOWED` — a second lock behind the reduced tool list, in case a model calls a
  tool it was not offered.
- **Repeat guard:** for write tools only (`WRITE_TOOLS`) it counts identical `(name, arguments)` pairs within the request.
  The first is executed; any identical repeat is *not* executed and returns `REPEAT_WARNING` (telling the model to
  re-read the file and try something different), and `_repeats` goes up. Read/run tools are exempt because repeating
  `run_tests` after an edit is legitimate.
- Otherwise runs the tool through `ToolBox.call` (which applies every verification gate) and truncates the result.

```python
369 |     def _execute(self, calls: list[dict], on_event: Callable[[str], None]) -> list[str]:
370 |         """Run a turn's tool calls; read-only batches run in parallel, others one by one."""
371 |         def announce(call: dict) -> None:
372 |             function = call.get("function", {})
373 |             shown = json.dumps(_parse_args(function.get("arguments")))[:100]
374 |             on_event(f"-> {function.get('name', '')} {shown}")
375 | 
376 |         def report(name: str, result: str) -> None:
377 |             if name == "todo":                        # show the whole checklist
378 |                 for line in result.splitlines():
379 |                     on_event(f"   {line}"[:140])
380 |                 return
381 |             on_event(f"   {result.splitlines()[0] if result else ''}"[:140])
382 | 
383 |         names = [call.get("function", {}).get("name", "") for call in calls]
384 |         if len(calls) > 1 and all(self._tools.is_read_only(n) for n in names):
385 |             for call in calls:
386 |                 announce(call)
387 |             with ThreadPoolExecutor(max_workers=min(len(calls), 4)) as pool:
388 |                 results = list(pool.map(self._run_one, calls))   # map keeps the original order
389 |             for name, result in zip(names, results, strict=True):
390 |                 report(name, result)
391 |             return results
392 |         results = []
393 |         for call, name in zip(calls, names, strict=True):
394 |             announce(call)
395 |             results.append(self._run_one(call))
396 |             report(name, results[-1])
397 |             if name in WRITE_TOOLS:
398 |                 self._show_diff(on_event)
399 |         return results
400 | 
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
401 |     def _show_diff(self, on_event: Callable[[str], None]) -> None:
402 |         """Show the user what the last write changed (never sent to the model: saves tokens)."""
403 |         diff = self._tools.pop_diff()
404 |         limit = self._cfg.diff_lines
405 |         for line in diff[:limit]:
406 |             on_event(f"     {line}"[:160])
407 |         if len(diff) > limit:
408 |             on_event(f"     ... {len(diff) - limit} more diff lines")
409 | 
```
- Takes the toolbox's diff of the last write and shows at most `diff_lines` (40) lines, indented under the tool result,
  e.g. `@@ -1,2 +1,2 @@`, `-old line`, `+new line`, then `... N more diff lines` if it was longer. The diff goes **only to
  your screen**: the model already knows what it wrote, so sending it back would waste tokens.

## `Agent._trim_history`
```python
410 |     def _trim_history(self) -> None:
411 |         """When the history no longer fits the context window, shorten it (llm.fit_history:
412 |         old tool outputs first, then old call arguments, then the oldest exchanges)."""
413 |         if self._size() > self._budget():
414 |             self.messages = fit_history(self.messages, self._budget())
```
- Runs before every model call. When the history is larger than the budget it is replaced by `fit_history`'s shortened
  copy (see `llm.md`): old tool outputs blanked first, then old call arguments shortened, and only as a last resort the
  oldest exchanges dropped. The newest 4 messages and the system prompt are never touched. Trade-off: this rewrites
  history (invalidating the server's cache once), but only when the context is nearly full — when something must go
  anyway. Compaction (between requests, at 60 %) normally comes first, so dropping is rare.
