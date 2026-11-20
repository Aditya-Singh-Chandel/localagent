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
@@ localagent/agent.py header @@
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
@@ localagent/agent.py failed @@
- Looks at the first line of a tool result: `ERROR...`, `REJECTED...`, `NOT RUN...`, `DENIED...` or `exit code N` with
  N not 0 count as failures. Used to refuse a premature `finish` and to count "trouble" for model escalation.

## `_wants_change`
@@ localagent/agent.py _wants_change @@
- Lower-cases the request, takes its first word, strips punctuation and checks it against `ACTION_WORDS`. "Fix the
  bug" -> True; "Why is this slow?" -> False. A deliberately simple rule: it costs nothing and only decides whether the
  one-time reminder may be sent, so a wrong guess just costs one extra model call or skips a reminder.

## `_parse_args`
@@ localagent/agent.py _parse_args @@
- Models are inconsistent: tool arguments may be a dict (returned as is) or a JSON string (parsed). A parse failure, a
  non-dict result, or any other type becomes `{}`; the tool then reports "bad arguments" and the model can retry.

## `Agent`
@@ localagent/agent.py Agent @@
@@ localagent/agent.py Agent.__init__ @@
- Stores the parts: config, backend, toolbox, meter, optional session log, and a counter for tool-call ids.
  `_plan_only` is set while `/plan` investigates; `_allowed` lists the extra tools a restricted request may use
  (`None` = no restriction). `depth` is 0 for the main agent and 1 for a helper; `_on_event` remembers the current
  request's progress display so a helper can report through it. `_seen` counts
  the write calls made in the current request and `_repeats` how many identical ones were refused (repeat guard).
- `messages` starts with just the system message (rules, `AGENT.md`, file tree).

@@ localagent/agent.py Agent._system_message @@
@@ localagent/agent.py Agent.refresh_system @@
- `_system_message` builds a fresh system prompt (re-reading `AGENT.md` and the file list). `refresh_system` swaps it
  in place — used after `/init` writes `AGENT.md`, so the new notes apply at once.

@@ localagent/agent.py Agent._add @@
- The single place messages enter the history. It appends in memory and, if a log exists, writes the same message to
  disk. Never editing earlier messages (apart from `_trim_history`) is what keeps the server's prompt cache valid.

@@ localagent/agent.py Agent.restore @@
- For `--resume`: re-adds every saved message except system ones (the system prompt is rebuilt fresh so it reflects
  the current file tree and `AGENT.md`), then calls `repair`, because a session that crashed mid-task can end with tool
  calls that have no results.

@@ localagent/agent.py Agent.repair @@
- Model APIs refuse a conversation in which an assistant message asked for tools whose results never arrived — exactly
  what Ctrl+C in the middle of a task leaves behind. `repair` collects the ids that already have results, finds every
  call without one, and appends an `INTERRUPTED` result for each (appending keeps the history valid because results
  only need to come after their request). Returns how many it fixed.

@@ localagent/agent.py Agent.reset @@
- Starts over with a fresh system prompt. Files on disk are untouched.

## Compaction — summaries instead of forgetting
@@ localagent/agent.py Agent._size @@
@@ localagent/agent.py Agent._budget @@
- `_size`: total characters in the history (about 4 per token). `_budget`: 70 % of the context window in characters.

@@ localagent/agent.py Agent.compact @@
- Like Claude Code's compaction: one model call turns the whole conversation into a summary (goals, decisions,
  files, what was verified, what is open), and the history becomes *system prompt + summary + "Understood"*. Later
  requests then send a few hundred tokens instead of the whole past, which saves tokens on every following call.
- Needs more than 3 messages. The tool list is still sent (some servers reject tool calls in the history otherwise),
  but the prompt says not to call tools; only the text is used. An empty summary leaves the history unchanged.
- The new messages are also written to the session log, so `--resume` continues from the summary.
- Used by `/compact` and automatically by `run` (below).

## `Agent.run` — one user request
@@ localagent/agent.py Agent.run @@
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
@@ localagent/agent.py Agent._may_use @@
- Without a restriction every tool may be used; with one, read-only tools and the explicitly allowed ones.

## `Agent._delegate` — subagents
@@ localagent/agent.py Agent._delegate @@
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
@@ localagent/agent.py Agent._finish_summary @@
- Returns the `summary` argument of a `finish` call in this turn (default "Done."), or `None` when there is none.

## `Agent._run_one` and `Agent._execute` — running a turn's tool calls
@@ localagent/agent.py Agent._run_one @@
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

@@ localagent/agent.py Agent._execute @@
- `announce` prints `-> name args`; `report` prints the first line of a result — or, for `todo`, the whole
  checklist, so you can follow the plan. Both only feed `on_event`.
- **Parallel path:** when a turn has more than one call and *every* call is read-only (`list_files`, `read_file`,
  `search`, `outline`), all calls are announced, then run together in a thread pool (at most 4 workers).
  `pool.map` returns results in the original order, so the history is identical to sequential execution - only
  faster (useful when several large files are read at once). Writes and runs are never parallel: they must keep their
  order and their approval prompts.
- **Sequential path:** announce, run, report, one call at a time, so you see a call before its approval question.
  After every write tool, `_show_diff` displays what changed.

@@ localagent/agent.py Agent._show_diff @@
- Takes the toolbox's diff of the last write and shows at most `diff_lines` (40) lines, indented under the tool result,
  e.g. `@@ -1,2 +1,2 @@`, `-old line`, `+new line`, then `... N more diff lines` if it was longer. The diff goes **only to
  your screen**: the model already knows what it wrote, so sending it back would waste tokens.

## `Agent._trim_history`
@@ localagent/agent.py Agent._trim_history @@
- `budget` and `size` come from `_budget` and `_size`.
- Walks everything except the newest 4 messages; once under budget it stops. Only `tool` messages not already blanked
  are replaced by `REMOVED`, after subtracting the saved size. User and assistant messages are kept so the
  conversation stays coherent. Trade-off: this rewrites history (invalidating the server's cache once), but only when
  the context is nearly full — when something must go anyway.
