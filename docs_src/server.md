# `localagent/server.py` — service mode (many sessions over HTTP)

**Role in the flow:** everything else in the project is a single user at a terminal. This file puts the same `Agent`
behind a small JSON-over-HTTP API so several people or programs can each hold their own conversation at once. It is the
first step of "Phase 5: run it as a service" in the roadmap. It uses only the standard library, like the rest of the code.

```
client ── POST /sessions ───────────────► new Session (own workspace folder + own Agent + own Meter)
client ── POST /sessions/<id>/messages ─► Session.agent.run(text) ──► answer, events, calls, tokens
client ── GET  /sessions/<id>/files ────► what the agent has produced in that session's folder
anyone ── GET  /health ─────────────────► {"status": "ok"}  (the only call that needs no token)
```

Start it: `python -m localagent.server --token SECRET --backend openai --api-base http://127.0.0.1:8080/v1 --model qwen3-8b`
(add `--auto-approve` only on a locked-down machine; without it, sessions are never allowed to *run* code - they can still
write and verify files).

## Module header
@@ localagent/server.py header @@
- The docstring is the API reference. `hmac.compare_digest` compares tokens in constant time; `threading` provides the
  locks; `uuid` makes session ids; `ThreadingHTTPServer` handles each request in its own thread so one slow model call
  does not block other sessions. `os` reads `LOCALAGENT_TOKEN`. `MAX_BODY` caps a request body at 1 MB. `MAX_SESSIONS` (20) and `SESSION_TTL`
  (3600 s) are the default limits; `time` supplies the monotonic clock used for expiry. The shared flags
  (`add_model_flags`, `model_flags`) come from `cli.py`, the shared rules (`overlay`, `gemini_history`) from `config.py`.

## `TooManySessions`
@@ localagent/server.py TooManySessions @@
- A tiny exception raised when the server already holds the maximum number of live sessions. The handler turns it
  into HTTP **429** ("too many requests"), so a flood of `POST /sessions` cannot exhaust memory or disk.

## `Session`
@@ localagent/server.py Session @@
@@ localagent/server.py Session.__init__ @@
- A session is one `Agent` with its own `Meter` and `ToolBox` (kept as `toolbox` so it can be closed). The toolbox's approval callback returns
  `config.auto_approve`, so running code is allowed only if the server was started with `--auto-approve`.
- `last_used` is stamped by the manager every time the session is touched (used for expiry).
- `lock` makes requests to the *same* session run one at a time (an agent's history is not thread-safe); different
  sessions run in parallel.

## `SessionManager`
@@ localagent/server.py SessionManager @@
@@ localagent/server.py SessionManager.__init__ @@
- Holds the base config, the folder under which each session gets a sub-folder, and a `backend_factory` (default
  `make_backend`; tests pass a fake). `_guard` protects the dictionary of sessions. `max_sessions` and `ttl` are the
  limits; `clock` is injectable so tests can move time without waiting.

@@ localagent/server.py SessionManager.create @@
- Under the guard it first drops expired sessions, then refuses (`TooManySessions`) if the limit is reached.
- Makes a 12-character random id, copies the config with `workspace = <root>/<id>` (this is the isolation: the sandbox
  in `sandbox.py` confines each agent to its own folder), builds the backend and session, stamps `last_used`, stores it and returns the id.

@@ localagent/server.py SessionManager._expire @@
- Deletes every session whose idle time (`now - last_used`) exceeds the TTL, closing its toolbox (which stops its
  sandbox container, if any). Only the in-memory object goes; the session's
  files stay on disk (delete the folder yourself if you want the space back).

@@ localagent/server.py SessionManager.get @@
- Drops expired sessions, then returns the session (renewing its `last_used`, so active conversations never expire) or
  `None` for an unknown or expired id.

@@ localagent/server.py SessionManager.close_all @@
- Empties the session table under the guard, then closes each session's toolbox outside it (closing can take a few
  seconds per session; holding the guard that long would block other requests). Closing stops each sandbox
  container and MCP server. Used when the server stops.

## The request handler
@@ localagent/server.py make_handler @@
- `make_handler` builds a handler *class* bound to one manager and token (the standard-library server instantiates the
  class per request). Inside it:
  * `log_message` is silenced so the console is not flooded.
  * `_send` writes a JSON response with the right headers.
  * `_authorized` is true when no token is configured or the `Authorization` header equals `Bearer <token>`.
  * `_read_json` reads the body (refusing > 1 MB or invalid JSON) and returns a dict or `None`.
  * `do_GET`: `/health` is answered first, before authentication. Everything else needs the token (401 otherwise).
    `/sessions/<id>/files` lists the session's files (via `walk_files`, skipping cache folders); unknown session → 404.
  * `do_POST`: unauthorised → 401; `/sessions` creates a session (201, or **429** when the server is full); `/sessions/<id>/messages` calls `_message`;
    anything else → 404.
  * `_message`: unknown session → 404; a missing/blank `text` → 400; otherwise, holding the session lock, it runs the
    agent and collects its progress lines. A model-server problem (`LLMError`) becomes 502. Success returns the answer,
    the events (`-> tool args` lines), the session's model-call count and total tokens.

## `build_server` and `main`
@@ localagent/server.py build_server @@
- Creates the HTTP server without starting it. Port `0` asks the OS for a free port (used by the tests).

@@ localagent/server.py main @@
- Command-line entry (the shared `--backend/--model/--api-base/--sandbox`, also `--max-sessions`, `--session-ttl`; `--token` defaults to the
  `LOCALAGENT_TOKEN` environment variable so the token need not appear in a command line): host (default `127.0.0.1`, i.e. this machine only), port, token, session root folder, backend,
  model, API base and `--auto-approve`. It overlays those on the environment config, prints the address and whether a
  token is required, and serves until Ctrl+C.
- For Gemini it raises the history budget to 32K tokens with `gemini_history` — the same rule as the chat. Without it, every session used the 8K budget meant for local models and
  long conversations were summarised (an extra model call each time) far earlier than needed.
- When it stops (Ctrl+C), it releases the port (`server_close`) and closes every live session (`close_all`), so no
  sandbox container is left running. Found in the final verification: before, each session started with
  `--sandbox docker` left its container behind. (A process killed outright cannot clean up; then remove leftovers with
  `docker rm -f localagent-...`.)
- Each reply carries `answer`, `events` (the progress lines), `calls` and `tokens` (the session's totals so far).

@@ localagent/server.py footer @@
- Standard script entry point (`python -m localagent.server`).

## Security notes (read before exposing it)
* Always set `--token` and keep the default `127.0.0.1` unless it sits behind HTTPS and a firewall; this server speaks
  plain HTTP.
* Sessions get separate folders. Code that *runs* (`--auto-approve`) executes with the server's user rights unless you
  add `--sandbox docker`, which gives every session its own locked-down container. Use that for anyone you do not
  fully trust.
* Sessions are capped (`--max-sessions`, default 20) and expire after `--session-ttl` seconds idle (default 3600). There
  is still no per-client request-rate limit, no HTTPS and no persistence across restarts (roadmap).
