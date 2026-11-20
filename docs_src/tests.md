# `tests/` — proving the agent works without a model

**Role:** these tests run in seconds and need no Ollama. A scripted fake model plays the LLM, and a tiny local HTTP
server imitates Ollama / OpenAI responses, so the loop, the verification gates, the safety rules and the real network
code are all exercised deterministically.

Run: `python -m unittest discover -s tests -t .` (use the venv's Python so the ruff/mypy layers are tested too).

## `tests/__init__.py`
@@ tests/__init__.py 1-1 @@
- Makes `tests` a package so `discover -t .` can import it and tests can import each other's helpers.

---
# `tests/test_agent.py` — core behaviour

## Helpers
@@ tests/test_agent.py header @@
- Imports the code under test. `GOOD` is valid code that prints 5. `BAD_SYNTAX` has a missing colon. `BAD_SCOPE` is a
  `return` outside a function: it *parses* but fails to *compile*, proving the compile stage is stronger than parsing.

@@ tests/test_agent.py make_box @@
- Builds a `ToolBox` on a temporary folder that auto-approves running code; extra keyword arguments tweak the config.

@@ tests/test_agent.py tool_call @@
- Builds the assistant message Ollama would send when the model asks for one tool.

@@ tests/test_agent.py FakeBackend @@
@@ tests/test_agent.py FakeBackend.__init__ @@
@@ tests/test_agent.py FakeBackend.chat @@
- Replays a fixed list of assistant messages, one per `chat` call, with fixed token counts. It satisfies `ChatBackend`,
  which is why the agent accepts it.

## Sandbox
@@ tests/test_agent.py SandboxTests @@
@@ tests/test_agent.py SandboxTests.test_escape_is_blocked @@
@@ tests/test_agent.py SandboxTests.test_inside_is_allowed @@
@@ tests/test_agent.py SandboxTests.test_truncate_keeps_both_ends @@
- A `..` escape must raise `SandboxError`; a nested path must stay inside the root; `truncate` must keep both the start
  and the end and mention what was omitted.

## Verification layers
@@ tests/test_agent.py VerifyTests @@
@@ tests/test_agent.py VerifyTests.check @@
@@ tests/test_agent.py VerifyTests.test_good_code_passes @@
@@ tests/test_agent.py VerifyTests.test_syntax_error_reports_line @@
@@ tests/test_agent.py VerifyTests.test_scope_error_caught_by_compile @@
@@ tests/test_agent.py VerifyTests.test_undefined_name_caught_by_ruff @@
- `check` writes source to a temp file and verifies it. Good code passes; a syntax error fails and names `m.py:1`; the
  scope error is caught by the compile stage; and — only when ruff is installed — `print(missing_name)` is rejected
  with rule `F821`, proving the second layer really runs.

## The write/run gates
@@ tests/test_agent.py ToolBoxTests @@
@@ tests/test_agent.py ToolBoxTests.test_bad_write_is_rejected_and_rolled_back @@
@@ tests/test_agent.py ToolBoxTests.test_bad_overwrite_restores_previous_content @@
@@ tests/test_agent.py ToolBoxTests.test_attempts_exhausted_says_stop @@
- A bad new file gives `REJECTED (1/3)` and does not exist afterwards (rollback). A bad *overwrite* restores the earlier
  good content. After three failures the message contains `STOP`.

@@ tests/test_agent.py ToolBoxTests.test_good_write_then_run @@
@@ tests/test_agent.py ToolBoxTests.test_run_refuses_file_broken_behind_our_back @@
@@ tests/test_agent.py ToolBoxTests.test_denied_when_user_says_no @@
- A good write passes verification and running it prints `5`. A broken file created behind the agent's back is refused
  at run time (`NOT RUN`) — the second check. When the user declines, running returns `DENIED`.

@@ tests/test_agent.py ToolBoxTests.test_edit_requires_unique_match @@
@@ tests/test_agent.py ToolBoxTests.test_path_escape_returns_error_string @@
@@ tests/test_agent.py ToolBoxTests.test_unknown_tool_and_bad_args @@
- `edit_file` refuses an ambiguous match (2 hits) and accepts a unique one. Path escapes, unknown tools and missing
  arguments all come back as error *strings*, never exceptions.

## The loop
@@ tests/test_agent.py AgentLoopTests @@
@@ tests/test_agent.py AgentLoopTests.test_reject_fix_run_finish @@
- **The key scenario:** the scripted model writes bad code, sees `REJECTED`, writes good code, runs it, and gives a
  final answer. Asserts 4 model calls and the content of each tool result in order.

@@ tests/test_agent.py AgentLoopTests.test_max_turns_stops_endless_loop @@
@@ tests/test_agent.py AgentLoopTests.test_string_arguments_are_accepted @@
- A model that never stops is halted by `max_turns` with a "Stopped after 3" message. Arguments sent as a JSON *string*
  are accepted.

## Phase 3/4 tools
@@ tests/test_agent.py Phase3ToolTests @@
@@ tests/test_agent.py Phase3ToolTests.test_outline_lists_classes_and_functions @@
@@ tests/test_agent.py Phase3ToolTests.test_multi_edit_applies_all_or_nothing @@
@@ tests/test_agent.py Phase3ToolTests.test_edit_file_all_renames_every_occurrence @@
@@ tests/test_agent.py Phase3ToolTests.test_auto_format_rewrites_style @@
@@ tests/test_agent.py Phase3ToolTests.test_writes_counter_and_read_only_flags @@
- `outline` lists a class, its method and a top-level function, and reports a missing path as an error.
- `multi_edit` applies two edits in order; a later failing edit (`edit 2`) leaves the file untouched (all-or-nothing);
  an empty list is refused. Auto-format is switched off here so the exact text can be compared.
- `edit_file` with a name that occurs twice is refused with an `all=true` hint; with `all=true` both occurrences are
  renamed. (An earlier draft of this test renamed the letter `f`, which also broke `from`; the write gate rejected it -
  a handy live demonstration that the gate works.)
- Auto-format (only when ruff is installed): `x   =   1` is rewritten to `x = 1` after the write passes verification.
- The `writes` counter counts a plain text write but not a rejected Python write; `search` is flagged read-only and
  `write_file` is not.

@@ tests/test_agent.py Phase3AgentTests @@
@@ tests/test_agent.py Phase3AgentTests.make_agent @@
@@ tests/test_agent.py Phase3AgentTests.test_nudge_when_change_requested_but_nothing_written @@
@@ tests/test_agent.py Phase3AgentTests.test_no_nudge_for_questions @@
@@ tests/test_agent.py Phase3AgentTests.test_parallel_read_only_calls_keep_order @@
@@ tests/test_agent.py Phase3AgentTests.test_repeated_identical_edit_is_refused_then_stopped @@
- `make_agent` builds an agent on a temp folder around a scripted fake model.
- **Nudge:** for "Fix the bug" the model first answers in text only; the agent injects the `NUDGE` message, the model
  then writes a file and the final answer is "done". For the question "Why is the sky blue?" there is no nudge and the
  first answer is returned.
- **Parallel reads:** two `read_file` calls in one turn produce results `1: AAA` and `1: BBB` in the original order, and
  two `->` progress events are reported.
- **Repeat guard:** a model that sends the identical `edit_file` four times gets one `OK`, then three
  `ERROR: you already made this exact call` results, and the request ends with the "repeating" stop message.

## The `finish` tool and escalation
@@ tests/test_agent.py FinishToolTests @@
@@ tests/test_agent.py FinishToolTests.make_agent @@
@@ tests/test_agent.py FinishToolTests.test_finish_in_same_turn_saves_a_call @@
@@ tests/test_agent.py FinishToolTests.test_finish_refused_when_a_call_failed @@
@@ tests/test_agent.py FinishToolTests.test_finish_refused_when_nothing_was_changed @@
@@ tests/test_agent.py FinishToolTests.test_finish_waits_until_what_was_read_has_been_seen @@
@@ tests/test_agent.py FinishToolTests.test_failure_detection @@
@@ tests/test_agent.py FinishToolTests.test_repeated_failures_escalate_once @@
- Write + run + finish in one response: the request ends after **one** model call with the summary, and the history
  ends with the `OK: finished` tool result (still a valid conversation).
- A finish next to a rejected write is refused once (`NOT FINISHED`); the corrected turn finishes.
- "Create n.txt" answered with an immediate finish is refused (nothing written); a question may finish at once.
- A question answered with `read_file` + `finish` in one response is refused once (the reason names `read_file`);
  the answer given after seeing the file is the one returned.
- `failed` recognises every failure form and accepts successes, `exit code 0` and empty text.
- A fake backend with `escalate`/`reset_route`: two failing runs trigger exactly one "stronger model" event, and the
  route is reset once at the start of the request.

@@ tests/test_agent.py footer @@
- Lets you run the file directly.

---
# `tests/test_phase2.py` — streaming, backends, context, undo, sessions, search, evals

## A fake model server
@@ tests/test_phase2.py header @@
- Imports the standard-library HTTP server pieces plus the modules under test. `FIZZ_CODE` is a correct fizzbuzz used by
  the eval-harness test.

@@ tests/test_phase2.py FakeServer @@
@@ tests/test_phase2.py FakeServer.__init__ @@
- Starts a real HTTP server on a free port (`("127.0.0.1", 0)`) in a background thread. Its handler records each
  request's JSON body and headers and replies with a canned status and body — so `post()` and the clients are tested
  against genuine sockets. `log_message` is silenced to keep test output clean.

@@ tests/test_phase2.py FakeServer.close @@
@@ tests/test_phase2.py ndjson @@
- `close` stops the server. `ndjson` encodes dictionaries as one-JSON-object-per-line bytes, Ollama's streaming format.

## Ollama client
@@ tests/test_phase2.py OllamaClientTests @@
@@ tests/test_phase2.py OllamaClientTests.test_streams_text_and_counts_tokens @@
@@ tests/test_phase2.py OllamaClientTests.test_collects_tool_calls @@
@@ tests/test_phase2.py OllamaClientTests.test_http_error_becomes_llm_error @@
@@ tests/test_phase2.py OllamaClientTests.test_unreachable_server_becomes_llm_error @@
@@ tests/test_phase2.py OllamaClientTests.test_error_line_in_stream @@
- Text pieces reach the callback in order and are joined; token counts come from the final line; the request asked for
  streaming. Tool calls are collected. A 404, an unreachable port (9) and an `{"error": ...}` line each become `LLMError`.

## Doctor
@@ tests/test_phase2.py DoctorTests @@
@@ tests/test_phase2.py DoctorTests.tags @@
@@ tests/test_phase2.py DoctorTests.test_healthy_when_model_installed @@
@@ tests/test_phase2.py DoctorTests.test_untagged_name_means_latest @@
@@ tests/test_phase2.py DoctorTests.test_missing_model_gives_pull_command @@
@@ tests/test_phase2.py DoctorTests.test_unreachable_server_is_reported @@
- `tags` starts a fake server whose `/api/tags` lists the given models (the fake server also answers `GET` for this).
  The doctor is healthy when the model is installed, treats `tinyllama` as `tinyllama:latest`, prints the exact
  `ollama pull` command when the model is missing, and reports an unreachable server as a failure.

## OpenAI-compatible client and registry
@@ tests/test_phase2.py OpenAIClientTests @@
@@ tests/test_phase2.py OpenAIClientTests.test_round_trip_with_tool_call_and_auth @@
@@ tests/test_phase2.py OpenAIClientTests.test_message_conversion @@
@@ tests/test_phase2.py BackendRegistryTests @@
@@ tests/test_phase2.py BackendRegistryTests.test_known_and_unknown @@
- Tool calls and usage numbers parse correctly and the bearer token is sent. Conversion turns dict arguments into JSON
  text and keeps the tool-call id link. The registry builds the right class per name and rejects unknown names.

## Context, undo, sessions
@@ tests/test_phase2.py ContextTests @@
@@ tests/test_phase2.py ContextTests.test_prompt_contains_tree_and_notes @@
@@ tests/test_phase2.py ContextTests.test_tree_is_capped @@
- The system prompt includes files and `AGENT.md` text but not `__pycache__`; the tree is capped with `...`.

@@ tests/test_phase2.py CheckpointTests @@
@@ tests/test_phase2.py CheckpointTests.test_undo_restores_deletes_and_recreates @@
- After modifying one file, deleting another and creating a third, `undo` restores the first two, removes the third,
  reports 2 restored, and a second `undo` says nothing to undo.

@@ tests/test_phase2.py SessionTests @@
@@ tests/test_phase2.py SessionTests.test_log_and_resume @@
@@ tests/test_phase2.py SessionTests.test_latest_of_empty_folder @@
- A run is logged as user + assistant messages; a fresh agent restores them and still starts with a system message. An
  empty folder has no latest session.

## Search, workspace check, ids
@@ tests/test_phase2.py SearchAndWorkspaceTests @@
@@ tests/test_phase2.py SearchAndWorkspaceTests.test_search_finds_lines_and_reports_bad_regex @@
@@ tests/test_phase2.py SearchAndWorkspaceTests.test_check_workspace_lists_broken_files @@
@@ tests/test_phase2.py SearchAndWorkspaceTests.test_agent_assigns_tool_call_ids @@
- `search` finds `file:line: text`, reports "no matches", and reports a bad regex politely. `check_workspace` returns
  exactly the broken file. The agent gives every tool call an id starting with `call_`.

## Eval harness
@@ tests/test_phase2.py EvalHarnessTests @@
@@ tests/test_phase2.py EvalHarnessTests.test_passing_run_is_scored_pass @@
@@ tests/test_phase2.py EvalHarnessTests.test_do_nothing_run_is_scored_fail @@
- A scripted "model" that writes correct fizzbuzz is scored PASS (2 calls); one that does nothing is scored FAIL —
  proving the scoreboard can tell good runs from bad ones. The do-nothing model is asked twice (2 calls) because the
  first useless answer to a "Create ..." request triggers the nudge; the second one still changes nothing.

@@ tests/test_phase2.py footer @@
- Lets you run the file directly.

---
# `tests/test_server.py` — the HTTP service

**Role:** proves the API, the token check and the per-session isolation using a real local socket (port 0 = any free
port) and a scripted fake model, so no model server is needed.

## Helpers
@@ tests/test_server.py header @@
- Imports the standard-library HTTP client, the server pieces, and the fake model helpers from `test_agent.py`.

@@ tests/test_server.py call @@
- Sends one request (optionally with a JSON body and bearer token) and returns `(status, parsed JSON)`. HTTP error codes
  are returned instead of raised so tests can assert on 401/404/400.

## Tests
@@ tests/test_server.py ServerTests @@
@@ tests/test_server.py ServerTests.start @@
- `start` builds a manager whose backend factory always returns the same scripted fake, launches the server on a daemon
  thread, registers cleanup, and returns its base URL. A temp folder holds the session workspaces.

@@ tests/test_server.py ServerTests.test_health_needs_no_token @@
@@ tests/test_server.py ServerTests.test_requests_without_token_are_rejected @@
- `/health` works without a token; creating a session or listing files without a token, or with a wrong one, gives 401.

@@ tests/test_server.py ServerTests.test_full_conversation_creates_files @@
- Create a session (201), send "Create hello.txt" (the fake model writes the file, then answers "created"): the reply
  has the answer, 2 model calls and a `write_file` event, and the files endpoint lists `hello.txt`.

@@ tests/test_server.py ServerTests.test_bad_requests @@
- Blank text and a missing `text` field → 400; unknown session ids → 404 for both messages and files; unknown paths → 404
  for GET and POST.

@@ tests/test_server.py ServerTests.test_sessions_have_separate_workspaces @@
- With no token configured, two sessions are created and only the first receives a file; the second session's file list is
  empty - isolation works.

@@ tests/test_server.py SessionLimitTests @@
@@ tests/test_server.py SessionLimitTests.manager @@
@@ tests/test_server.py SessionLimitTests.test_idle_sessions_expire_and_activity_keeps_them @@
@@ tests/test_server.py SessionLimitTests.test_close_all_closes_every_session @@
@@ tests/test_server.py SessionLimitTests.test_session_limit_returns_429_and_frees_after_expiry @@
- `manager` builds a `SessionManager` with a fake model and a clock the test controls (a one-item list).
- **Expiry:** two sessions with a 100 s TTL; at t=80 one is touched (renewing it); at t=150 the touched one is still alive
  (70 s idle) and the untouched one is gone (150 s idle).
- **Shutdown:** `close_all` closes both sessions' toolboxes (recorded by replacing `close` with a function that notes
  the session id; the `sid=session_id` default fixes each id at definition time) and forgets the sessions.
- **Limit:** with `max_sessions=1` over real HTTP, the second `POST /sessions` returns 429; after the clock passes the TTL
  the first session has expired, so creating a session succeeds again (201).

@@ tests/test_server.py footer @@
- Lets you run the file directly.

---
# `tests/test_phase4.py` — Gemini limits, retries, fallback, and the Docker sandbox

**Role:** proves the hosted-model safety net and the sandbox without spending real quota: a fake clock replaces real
waiting, scripted fake models replace Gemini, and the local fake HTTP server checks the exact wire format. Only the
last test uses real Docker, and it is skipped automatically when Docker is not running.

## Helpers
@@ tests/test_phase4.py header @@
- Imports the modules under test and `FakeServer` from `test_phase2.py`.

@@ tests/test_phase4.py FakeClock @@
@@ tests/test_phase4.py FakeClock.__init__ @@
@@ tests/test_phase4.py FakeClock.__call__ @@
@@ tests/test_phase4.py FakeClock.sleep @@
- A clock that only moves when told. Calling it returns the current fake time; `sleep(s)` records `s` and moves time
  forward by `s`, so a 60-second rate-limit wait finishes instantly but is still measured.

## Rate limiter
@@ tests/test_phase4.py RateLimiterTests @@
@@ tests/test_phase4.py RateLimiterTests.test_requests_per_minute_wait @@
@@ tests/test_phase4.py RateLimiterTests.test_tokens_per_minute_wait_and_too_big_request @@
@@ tests/test_phase4.py RateLimiterTests.test_daily_quota_persists_and_resets_next_day @@
@@ tests/test_phase4.py RateLimiterTests.test_mark_exhausted_and_pacific_day @@
- With 2 requests/minute, the third request waits exactly 60 s.
- With 1000 tokens/minute, two 600-token requests cannot share a minute (the second waits); a 5000-token request is
  refused outright.
- Daily quota of 2: after two requests a *new* limiter reading the same file is exhausted (the count survived a
  "restart") and refuses with `QuotaExhausted`; the status line shows requests and tokens; another model has its own
  counter; one day later the quota is free again.
- `mark_exhausted` works, and timestamp 0 (midnight UTC, 1 Jan 1970) is still 31 Dec 1969 in California.

## Fallback chain
@@ tests/test_phase4.py ScriptedBackend @@
@@ tests/test_phase4.py ScriptedBackend.__init__ @@
@@ tests/test_phase4.py ScriptedBackend.chat @@
@@ tests/test_phase4.py reply @@
- `ScriptedBackend` returns or raises prepared items in order and counts calls; `reply` makes a simple answer worth
  10 + 5 tokens.

@@ tests/test_phase4.py FallbackTests @@
@@ tests/test_phase4.py FallbackTests.run_chain @@
@@ tests/test_phase4.py FallbackTests.test_retries_busy_model_then_succeeds @@
@@ tests/test_phase4.py FallbackTests.test_server_retry_delay_is_respected @@
@@ tests/test_phase4.py FallbackTests.test_falls_back_after_retries_and_on_daily_quota @@
@@ tests/test_phase4.py FallbackTests.test_exhausted_model_is_skipped_without_a_call @@
@@ tests/test_phase4.py FallbackTests.test_bad_request_is_not_hidden_and_all_failing_explains @@
@@ tests/test_phase4.py FallbackTests.test_server_daily_limit_is_learned_and_announced @@
@@ tests/test_phase4.py FallbackTests.test_per_model_limit_overrides @@
@@ tests/test_phase4.py FallbackTests.test_limiter_counts_tokens_of_successful_calls @@
- `run_chain` builds a chain whose sleeps are recorded instead of performed.
- A 503 then success: 2 calls, one 2-second pause, `last_model` set.
- A 429 saying `retryDelay: 17s` waits 18 s (17 + 1 safety second).
- Chain of three: the first model stays busy (1 try + 2 retries = 3 calls), the second reports a *per-day* 429 (its
  limiter becomes exhausted), the third (the "local" one) answers.
- An exhausted model is skipped with zero calls; `status()` lists the rate-limited model.
- An HTTP 400 is raised immediately (status preserved); when every model fails the error says "every model failed";
  an empty chain is refused.
- A per-day 429 carrying `"quotaValue": "20"` (the exact shape Google sent) marks the model exhausted, its status
  shows `1/20` and "used up", and a notice names Google's limit. Per-model overrides parse with or without spaces,
  unknown models get the defaults, and a malformed entry explains the format.
- A successful call records 15 tokens and 1 request; the size estimate is about 4 characters per token.

## Routing
@@ tests/test_phase4.py RoutingTests @@
@@ tests/test_phase4.py RoutingTests.test_escalation_puts_strong_models_first_until_reset @@
@@ tests/test_phase4.py RoutingTests.test_strong_models_are_tried_after_cheap_ones_fail @@
@@ tests/test_phase4.py RoutingTests.test_missing_model_is_skipped_for_good @@
@@ tests/test_phase4.py RoutingTests.test_per_minute_limit_is_learned @@
- Cheap answers first; after `escalate()` (which reports `True` once) the strong model answers; after `reset_route()`
  the cheap one again. Status covers both; a chain without reserve cannot escalate.
- When the cheap model is used up, the strong reserve is still tried before giving up.
- A model answering 404 is asked once, announced, and never asked again; the next model answers both requests.
- A per-minute 429 with `quotaValue` 5 lowers the limiter from 12 to 5; a higher value never raises it again.

## Gemini wire format
@@ tests/test_phase4.py GeminiWireTests @@
@@ tests/test_phase4.py GeminiWireTests.test_thought_signature_round_trip_reasoning_and_hidden_tokens @@
@@ tests/test_phase4.py GeminiWireTests.test_missing_signatures_get_placeholder_only_for_gemini @@
@@ tests/test_phase4.py GeminiWireTests.test_gemini_backend_needs_key_and_labels @@
@@ tests/test_phase4.py GeminiWireTests.test_doctor_reports_missing_key @@
@@ tests/test_phase4.py GeminiWireTests.test_doctor_treats_a_missing_backup_as_a_note @@
- Against the fake server: `reasoning_effort` is sent; the tool call's `extra_content` (thought signature) is kept on
  the stored message and sent back by `to_openai`; with 60 prompt + 20 completion but 150 total tokens, 90 are counted
  as completion (70 hidden thinking + 20).
- A tool call without a signature is sent unchanged to ordinary servers, but gets Google's placeholder signature when
  `fill_signatures` is on.
- The Gemini backend refuses to start without a key, builds a `FallbackBackend` with one, labels the chain correctly,
  and rejects an unknown fallback name. The doctor reports a missing key as a failure.
- With Gemini answering (its model list is faked with `mock.patch`) but the backup server unreachable, the doctor
  stays healthy, prints no `FAIL`, and prints a `note` about the backup.

## Runners
@@ tests/test_phase4.py RunnerTests @@
@@ tests/test_phase4.py RunnerTests.test_local_runner_refuses_shell_and_toolbox_hides_tool @@
@@ tests/test_phase4.py RunnerTests.test_docker_toolbox_offers_shell @@
@@ tests/test_phase4.py RunnerTests.test_docker_sandbox_isolation_end_to_end @@
- Locally the shell is refused (code 126) and the `run_shell` tool is not even offered to the model.
- With `sandbox="docker"` the tool is offered and an empty command is rejected — without starting a container.
- **Real Docker (skipped if Docker is off):** a written file runs in the container and prints `from container`;
  `whoami && pwd` shows the non-root user `agent` in `/work`; opening a web page fails (no network); `sleep 30` with a
  2-second limit returns the timeout code 124. The container is removed in `finally`.

@@ tests/test_phase4.py footer @@
- Lets you run the file directly.

---
# `tests/test_phase7.py` — the Claude-Code-like features

**Role:** proves the features that make the agent usable in any real project folder: fast folder walking, checks for
more languages, the `todo` checklist, the opt-in shell on this computer, compaction, `/init` and the defaults.
No model and no network are needed (scripted fakes again); the JavaScript test runs only when Node.js is installed.

## Helpers
@@ tests/test_phase7.py header @@
- Imports the pieces under test and the fake model helpers from `test_agent.py`.

@@ tests/test_phase7.py box_for @@
- A toolbox on a temporary folder that approves everything; extra settings can be passed.

## Folder walking
@@ tests/test_phase7.py WalkTests @@
@@ tests/test_phase7.py WalkTests.test_skipped_folders_are_never_entered @@
@@ tests/test_phase7.py WalkTests.test_search_uses_glob_on_file_names @@
- A project with `node_modules`, `.localagent` and `venv` folders: only `README.md` and `src/app.py` are found (sorted),
  the prompt's file list shows exactly those two, and a change inside `node_modules` is invisible to `/undo` snapshots.
- `search` with `glob: *.js` finds a match in a JavaScript file; the default (`*.py`) does not look at it.

## Languages
@@ tests/test_phase7.py LanguageTests @@
@@ tests/test_phase7.py LanguageTests.check @@
@@ tests/test_phase7.py LanguageTests.test_json_and_toml @@
@@ tests/test_phase7.py LanguageTests.test_javascript_with_node @@
@@ tests/test_phase7.py LanguageTests.test_bad_json_write_is_rolled_back_and_workspace_check_sees_it @@
- `check` writes a file and runs `verify_file` on it.
- Valid JSON/TOML pass; a trailing comma in JSON and a missing TOML value fail; a Markdown file is never checked.
- With Node.js: valid JavaScript passes, `const x = ;` fails.
- Writing broken JSON is rejected and rolled back (the file does not exist); a broken JSON file made outside the agent is
  reported by the end-of-request workspace check.

## Todo and shell
@@ tests/test_phase7.py TodoAndShellTests @@
@@ tests/test_phase7.py TodoAndShellTests.test_todo_checklist @@
@@ tests/test_phase7.py TodoAndShellTests.test_host_shell_is_opt_in_and_asks @@
@@ tests/test_phase7.py TodoAndShellTests.test_todo_progress_is_shown_in_full @@
- The checklist renders as `TODO 1/2 done` + `[x]`/`[ ]` lines; an empty list is an error; plain-text items work too
  (the real model sent plain strings the first time).
- Without `--allow-shell` there is no `run_shell` tool. With it, `echo hello-shell` runs (through PowerShell on
  Windows), and the approval question contained the command. A runner without the flag refuses with code 126.
- When the model sends a checklist, every line of it reaches the progress display.

## Compaction and notes
@@ tests/test_phase7.py CompactionTests @@
@@ tests/test_phase7.py CompactionTests.make @@
@@ tests/test_phase7.py CompactionTests.test_manual_compact_replaces_history_with_summary @@
@@ tests/test_phase7.py CompactionTests.test_empty_summary_keeps_history @@
@@ tests/test_phase7.py CompactionTests.test_automatic_compaction_before_a_request @@
@@ tests/test_phase7.py CompactionTests.test_reset_and_refresh_reload_agent_notes @@
- A fresh agent has nothing to compact. After two requests, `compact()` leaves exactly system + summary + "Understood",
  with the model's summary inside.
- An empty summary changes nothing.
- With a tiny context (1000 tokens) and `compact_at=0.1`, the fourth request is preceded by an automatic summary (a
  progress line says so) and still gets its answer.
- After `AGENT.md` appears, `refresh_system` puts its text in the system prompt; `reset` keeps it and clears the rest.

## Any-folder defaults
@@ tests/test_phase7.py AnyFolderTests @@
@@ tests/test_phase7.py AnyFolderTests.test_defaults_work_in_current_folder_with_global_quota_file @@
@@ tests/test_phase7.py AnyFolderTests.test_usage_file_can_be_moved_by_environment @@
- The workspace defaults to the current folder; the quota file lives in the home folder; `--allow-shell` reaches the
  config; `/init`'s request asks for `AGENT.md`.
- `LOCALAGENT_USAGE_FILE` moves the quota file (the test restores the environment afterwards).

@@ tests/test_phase7.py footer @@
- Lets you run the file directly.

---
# `tests/test_phase8.py` — permissions, web pages, MCP, plan mode, interrupts, diffs, CLI helpers

**Role:** proves the Claude-Code-style features of the latest phase without a model or the internet: a local web server
stands in for websites, and a tiny MCP server written inside this file stands in for real MCP servers.

## Helpers
@@ tests/test_phase8.py header @@
- Imports the pieces under test. `FAKE_MCP_SERVER` is the full source of a minimal MCP server: it answers the
  handshake (first sending the client a question of its own, `roots/list`, to prove the client copes), lists its tools
  in **two pages** (`echo` with a deliberately messy schema, then `fail`), echoes text, and reports an error result
  for `fail` (with an image part, to test that non-text content is named, not dumped).

@@ tests/test_phase8.py write_server @@
- Saves that source to a temporary file and returns the command that starts it with this Python.

## Permissions
@@ tests/test_phase8.py PermissionTests @@
@@ tests/test_phase8.py PermissionTests.test_rules_match_tools_and_subjects @@
@@ tests/test_phase8.py PermissionTests.test_suggested_rules_are_narrow @@
@@ tests/test_phase8.py PermissionTests.test_settings_merge_and_always_allow_is_saved @@
@@ tests/test_phase8.py PermissionTests.test_toolbox_applies_rules_before_asking @@
- Rule parsing and matching, wildcard tool names (`mcp__files__*`), and **deny beats allow**; unmatched → ask.
- "Always" suggestions: the exact command, the whole website, the bare `run_tests`.
- A user file (deny + an MCP server) and a project file (allow) merge; `always_allow` appends to the project file on
  disk; a broken project file is reported as invalid JSON.
- In the toolbox: a denied `run_python` is refused without asking, an allowed `run_tests` runs without asking, and with
  the allow rule removed the question arrives as an `Approval` carrying tool and subject; a "no" gives `DENIED`.

## Web pages
@@ tests/test_phase8.py WebTests @@
@@ tests/test_phase8.py WebTests.serve @@
@@ tests/test_phase8.py WebTests.test_html_becomes_visible_text @@
@@ tests/test_phase8.py WebTests.test_fetch_and_errors @@
@@ tests/test_phase8.py WebTests.test_fetch_tool_asks_and_obeys_rules @@
- `serve` starts a local web server that answers every request with a chosen status, body and content type.
- A page with a title, a style block, bold text, a script and a list becomes exactly
  `Title / Hello world / one` (the script text never appears).
- Fetching HTML and plain text works; a 404 and a `file://` address raise readable errors.
- Through the tool: approved → page text; nobody to approve → `DENIED`; a deny rule → refused; an `ftp://` address →
  `ERROR`.

## MCP
@@ tests/test_phase8.py MCPTests @@
@@ tests/test_phase8.py MCPTests.test_schema_cleaning @@
@@ tests/test_phase8.py MCPTests.test_server_tools_through_the_toolbox @@
@@ tests/test_phase8.py MCPTests.test_timeout_and_stopped_server @@
- Schema cleaning drops `$schema`/`additionalProperties`, turns `["integer","null"]` into integer + nullable, fills
  missing types, and replaces a missing schema with "no arguments".
- With three configured servers — the fake one, one whose program does not exist, one without a command — the toolbox
  offers `mcp__fake__echo` and `mcp__fake__fail` (from both pages), the echo works, the failing tool returns `ERROR`,
  the notes report 2 tools and both skipped servers, and a deny rule on `mcp__fake__*` blocks calls.
- A request the server never answers times out with a clear message; after `close` the program has ended, both of
  its pipes are closed (no leaked file handles), and calls fail with `MCPError`.

## Agent features
@@ tests/test_phase8.py RecordingBackend @@
@@ tests/test_phase8.py RecordingBackend.__init__ @@
@@ tests/test_phase8.py RecordingBackend.chat @@
- A fake model that also records which tool names it was offered on each call.

@@ tests/test_phase8.py AgentFeatureTests @@
@@ tests/test_phase8.py AgentFeatureTests.make @@
@@ tests/test_phase8.py AgentFeatureTests.test_plan_mode_is_read_only @@
@@ tests/test_phase8.py AgentFeatureTests.test_restricted_request_offers_only_allowed_tools @@
@@ tests/test_phase8.py AgentFeatureTests.test_repair_answers_unanswered_calls @@
@@ tests/test_phase8.py AgentFeatureTests.test_edits_are_shown_as_a_diff @@
- **Plan mode:** `write_file` is not offered (but `read_file` is); when the model calls it anyway it gets
  `PLAN_ONLY_ERROR`, no file appears, the request was stored with the plan prefix, and the plan is returned.
- **Restricted request** (how `/init` works): with `allowed={write_file}` the model is offered `write_file` but not
  `run_tests`; calling `run_tests` anyway gets `NOT_ALLOWED`; writing `AGENT.md` and finishing works. Plan mode now offers
  the `todo` checklist.
- **Repair:** of two calls only one has a result; `repair` adds `INTERRUPTED` for the other, a second repair finds
  nothing; restoring a "crashed" log repairs it automatically.
- **Diffs:** writing then editing a file shows `+one`, `-two`, `+TWO` and the hunk header on screen, without the
  `+++` header, and none of it reaches the model's tool results; with `diff_lines=2` a 4-line new file shows
  `... 3 more diff lines`.

## CLI helpers
@@ tests/test_phase8.py CliHelperTests @@
@@ tests/test_phase8.py CliHelperTests.test_multiline_input @@
@@ tests/test_phase8.py CliHelperTests.test_session_logs_belong_to_the_project @@
@@ tests/test_phase8.py CliHelperTests.test_shared_setup_helpers @@
@@ tests/test_phase8.py CliHelperTests.test_todo_understands_done_written_in_the_text @@
@@ tests/test_phase8.py CliHelperTests.test_approver_always_saves_a_rule @@
@@ tests/test_phase8.py CliHelperTests.test_auto_configure_uses_what_is_available @@
@@ tests/test_phase8.py CliHelperTests.test_ctrl_c_keeps_the_conversation_usable @@
- A `\`-continued line and a `"""` block are read as multi-line requests; a leading byte-order mark is removed.
- Conversation logs go to `<project>/.localagent/sessions` whatever folder the agent was started from; an absolute
  `state_dir` is used as given.
- The shared helpers: `overlay` skips `None`; `gemini_history` gives Gemini 32K but keeps 8K when the size was chosen
  (flag or `LOCALAGENT_NUM_CTX`, set inside `mock.patch.dict` so the real environment is restored) or the model is
  local; `shell_argv` picks PowerShell on Windows and `sh` elsewhere.
- Checklist items marked `(done)` in the text or starting with `[x]`, or with `done: true`, count as done (`3/4`).
- Answering `a` saves `run_shell(git status)` in memory and in the project file; Enter refuses; `YES` allows.
- With a key, a ready sandbox image and a running local server (simulated with `mock.patch`), automatic setup picks
  gemini + docker + the local fallback + the 32K history budget with four notes; explicit flags are never overridden.
- A model call interrupted by Ctrl+C returns an empty answer and prints the "stopped" message instead of crashing.

## Limits and hints
@@ tests/test_phase8.py LimitsAndHintsTests @@
@@ tests/test_phase8.py LimitsAndHintsTests.test_learned_rpm_is_saved_for_other_runs @@
@@ tests/test_phase8.py LimitsAndHintsTests.test_undefined_name_rejection_suggests_editing_together @@
- A per-minute limit learned by one limiter is used by a new limiter reading the same usage file.
- With ruff installed, a write rejected for an undefined name carries the "edit together" hint.

@@ tests/test_phase8.py footer @@
- Lets you run the file directly.

---
# `tests/test_phase9.py` — many languages, streaming, helpers, custom commands, tiers, big folders

**Role:** proves the breadth features. Language tests run each checker only when its program is installed (this PC has
Node, Go, Rust, gcc/g++ and PowerShell, so they all ran); everything else uses fakes and temporary folders.

## Helpers
@@ tests/test_phase9.py header @@
- `SAMPLES` holds, per extension, the program needed, a valid file and a broken one (TypeScript with an `enum`, Go,
  Rust, C, C++, PowerShell).

@@ tests/test_phase9.py check @@
- Writes a file into a folder and returns whether `verify_file` accepts it.

## Languages
@@ tests/test_phase9.py LanguageTests @@
@@ tests/test_phase9.py LanguageTests.test_each_installed_language_checker @@
@@ tests/test_phase9.py LanguageTests.test_missing_outside_header_is_not_an_error @@
@@ tests/test_phase9.py LanguageTests.test_unknown_and_tool_less_files_pass @@
- For every installed language: the valid file passes, the broken one fails (`subTest` reports each language
  separately; missing programs are skipped).
- A C file that includes a header which does not exist here passes (it cannot be checked without that library).
- An unknown file type passes; with the program "uninstalled" (a mock), even a nonsense `.go` file passes — checks are
  skipped, never failed, when their tool is missing. The checked-extension list includes the new languages.

## Streaming
@@ tests/test_phase9.py StreamTests @@
@@ tests/test_phase9.py StreamTests.lines @@
@@ tests/test_phase9.py StreamTests.test_text_and_tool_calls_are_assembled @@
@@ tests/test_phase9.py StreamTests.test_error_inside_a_stream @@
@@ tests/test_phase9.py StreamTests.test_summary_is_printed_when_streamed_text_differs @@
- `lines` turns event texts into `data: ...` lines ending with `[DONE]`, as a server sends them.
- Two text pieces become "Hello" and reach the display in order; a comment line is ignored; a tool call split over two
  chunks (name `read_` + `file`, argument text in two halves) is glued back together with its id and thought
  signature; the token counts from the last chunk are returned.
- An error object inside the stream raises `LLMError`.
- When the model streams "Working on it." but finishes with the summary "All done: 3 files.", both appear on screen.

## Helper agents
@@ tests/test_phase9.py HelperTests @@
@@ tests/test_phase9.py HelperTests.test_delegate_runs_a_read_only_helper_and_returns_its_report @@
@@ tests/test_phase9.py HelperTests.test_helper_leaves_the_main_routing_alone @@
@@ tests/test_phase9.py HelperTests.test_helpers_can_be_switched_off @@
- The main agent calls `delegate`; the helper is offered neither `delegate` nor `write_file`, its write attempt is
  refused, it searches and finishes; the main agent receives `HELPER REPORT: ...` and finishes. All 5 model calls are
  counted by the shared meter and the helper's steps are shown with `|`.
- A helper whose reads keep failing neither resets nor escalates the shared model chain: only the main agent's one
  reset at the start of its request is seen.
- With `helpers=False` the tool is not offered.

## Commands and tiers
@@ tests/test_phase9.py CommandAndTierTests @@
@@ tests/test_phase9.py CommandAndTierTests.test_custom_commands_load_and_expand @@
@@ tests/test_phase9.py CommandAndTierTests.test_paid_tier_puts_strong_models_first_with_higher_limits @@
- A user and a project `review.md`: the project one wins; a file with an invalid name is ignored; `$ARGUMENTS` is
  replaced, or the words are appended when the file has no placeholder.
- `LOCALAGENT_GEMINI_TIER=paid` gives 1000 requests/minute and 10 000/day, puts the strong models first and leaves no
  reserve to escalate to; `LOCALAGENT_RPM` overrides the limit directly.

## Big folders
@@ tests/test_phase9.py HookTests @@
@@ tests/test_phase9.py HookTests.test_after_edit_failure_reaches_the_model_and_after_request_runs @@
@@ tests/test_phase9.py HookTests.test_hooks_merge_from_settings_files @@
- A failing `after_edit` hook (it prints the file name and exits 3) leaves the write in place but appends
  `HOOK after_edit failed (exit code 3)` and its output to the result; `after_request` runs and returns its output;
  without hooks nothing runs. (The test command quotes its text so PowerShell and `sh` print it identically.)
- Hooks merge from the user and project settings files, the project winning per hook.

@@ tests/test_phase9.py BigFolderTests @@
@@ tests/test_phase9.py BigFolderTests.test_snapshots_switch_off_once_for_huge_folders @@
@@ tests/test_phase9.py BigFolderTests.test_workspace_check_can_look_only_at_recent_changes @@
@@ tests/test_phase9.py BigFolderTests.test_search_stops_on_enormous_folders @@
- With a 3-file limit, a 5-file folder switches `/undo` off with one message (the next snapshot is silent and `undo`
  explains why); a normal folder snapshots fine.
- A file broken an hour ago is found by a full check but not by a check of recent changes; a file broken now blocks
  `run_tests`; once it is gone, the old broken file does not block the tests.
- With the file limit lowered to 2 (a mock), a search stops with the "narrow the search" note.

@@ tests/test_phase9.py footer @@
- Lets you run the file directly.

