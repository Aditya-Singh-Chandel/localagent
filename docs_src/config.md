# `localagent/config.py` — settings

**Role in the flow:** created first at startup. Every other module receives one `Config` object instead of
reading globals, so changing behaviour (model, limits, safety switches) means changing one value here, and tests
can build their own `Config`.

**Depends on:** only the standard library. **Used by:** almost every module.

## Module header
@@ localagent/config.py header @@
- The triple-quoted text is the module docstring (shown by `help()`); it has no effect on behaviour.
- `Any` types the free-form settings accepted by `overlay`.
- `from __future__ import annotations` makes type hints lazy, so forward references and newer syntax such as
  `list[str]` work on older Pythons.
- `import os` gives `os.environ` (environment variables). `dataclass` generates the constructor for a class from its
  fields; `replace` copies a frozen dataclass with some fields changed. `Path` is the standard file-path object.
- From `providers.py` (the table of online services): `GEMINI_BASE` (Google's address, the default of two fields),
  `CUSTOM_KEY` (the key variable of the "custom" entry), `PROVIDERS` and `find_provider` (the table and a lookup by
  name) and `key_help` (the "no key" message). `providers.py` imports nothing from here, so there is no import loop.

## `_normalize_host`
@@ localagent/config.py _normalize_host @@
- The leading underscore marks it private. It takes the raw `OLLAMA_HOST` text and returns a URL.
- Ollama's own docs let people write `127.0.0.1:11434` without a scheme. If the text already starts with `http://`
  or `https://` we only remove a trailing `/` (so `host + "/api/chat"` never gives `//`); otherwise we prepend `http://`.

## Paid-tier presets and the modes
@@ localagent/config.py after__normalize_host @@
- `PAID_RPM`, `PAID_RPD`, `PAID_TPM`: per-model limits used when `LOCALAGENT_GEMINI_TIER=paid` (billing enabled on your
  Google project). They sit well under Google's Tier 1 limits; the agent still adopts lower numbers if Google reports
  them.
- `MODES`: the three ways to choose who answers — `auto`, `online`, `offline` (see `mode` below and `cli.md`).
- `LOCAL_SERVERS`: the allowed values of `local_server` — empty (any local model that answers), `openai` or `ollama`.

## `_split`
@@ localagent/config.py _split @@
- Turns a comma-separated environment value such as `"a, b,c"` into a tuple `("a", "b", "c")`, ignoring blanks.

## `_int`
@@ localagent/config.py _int @@
- Reads a whole-number environment variable. An empty or missing one gives the default; a typo such as
  `LOCALAGENT_NUM_CTX=8k` raises a `ValueError` that names the variable, shows an example and repeats the bad value.
  The CLI prints it as one `setup problem:` line. Before 0.6 the same typo crashed the program with a Python
  traceback before anything was shown.

## `Config` — the settings
@@ localagent/config.py Config @@
- `@dataclass(frozen=True)` builds `__init__` from the fields and makes instances **immutable**, so no component can
  silently change a setting mid-run.
- `backend`: which model-server client to use (`ollama`, `openai`, `gemini`, `hosted` or `local`; see `backends.md`).
  You normally do not set it: the **mode** chooses it (see below). Setting it by hand (`--backend`) skips the mode.
- `model`: the model tag/name the server understands. `host`: where Ollama listens.
- `api_base` / `api_key`: address and bearer token for an OpenAI-compatible server. The default
  `http://127.0.0.1:8080/v1` is where `scripts\serve.ps1` starts llama.cpp (LM Studio uses `http://localhost:1234/v1`).
- `workspace`: the **only** folder the agent may read or write (enforced by `sandbox.py`). Default `.` = the folder you
  start the agent in, like Claude Code: `cd` into a project, run `localagent`.
- `state_dir`: where session logs go, **relative to the workspace**: `<project>\.localagent\sessions`, next to the
  project's own `settings.json` and `commands`, so `--resume` always continues *this* project's conversation. (An
  absolute path is used as it is.) The `.localagent` folder is never listed, searched, checked or snapshotted (it is in
  `SKIP_DIRS`). Until the final verification it was relative to the folder the agent was *started from*: started
  from the agent's own folder with `--workspace`, every project's logs were mixed in one place and `--resume` could
  continue a different project's conversation.
- `num_ctx`: the **history budget** in tokens (and the context window requested from Ollama when Ollama is the main
  model). 8K by default; the modes set it to Gemini's 32K or to the local model's `local_ctx`. `temperature`:
  randomness; now **0.0** (greedy), the most repeatable choice for an agent, changed after the real model gave
  different outcomes for the same task.
- `max_turns`: hard cap on model calls per user request. `max_fix_attempts`: rejected writes allowed per file before
  the tool says STOP. `command_timeout`: seconds before a run is killed. `test_timeout`: the same for `run_tests`
  (2 minutes: a first `cargo test` or `go test` compiles for a while). `max_output_chars`: any tool result longer
  than this is shortened (saves tokens).
- `memory_chars` / `tree_entries`: caps on how much of `AGENT.md` and the file overview enter the system prompt. The
  overview was raised from 40 to 50 lines in 0.6 (about 60 more tokens per call) because the new summarised overview
  then lists a mid-sized project's main code folder instead of making the model ask for it.
- `request_timeout`: seconds to wait for a model's answer — 15 minutes, because a local model on a CPU may need minutes
  before its first word.
- `type_check`: also run mypy while verifying. `auto_approve`: never ask before running code.
- `auto_format`: run `ruff format` on every verified Python write. `max_nudges`: how many "you changed nothing" reminders
  the agent may send per request (see `agent.md`). `reasoning_effort`: sent to OpenAI-style servers when set.
- `allow_host_shell`: offer the shell tool on this computer (asks before every command). `usage_file`: where daily quota
  counts live — one file in your home folder for **all** projects, because quotas belong to the API key, not to a
  project (env `LOCALAGENT_USAGE_FILE`). `compact_at`: history share that triggers automatic compaction. `diff_lines`: how many lines of each edit's diff
  are shown to you.
- **Gemini settings:** `gemini_api_key` (read from the `GEMINI_API_KEY` environment variable — never write a key into
  code), `gemini_base` (Google's OpenAI-compatible address), `gemini_models` (everyday chain: `gemini-3.5-flash-lite` > `gemini-3.1-flash-lite` — fast models with large daily
  quotas; `gemini-2.5-flash-lite` was removed after Google answered 404 "no longer available to new users"), `gemini_strong_models` (reserve: `gemini-3.5-flash`, `gemini-2.5-flash`, ~20
  requests/day each, used first only after an escalation; env `LOCALAGENT_GEMINI_STRONG`), `escalate_after` (failed tool
  results in one request before escalating, default 2). Gemma was left out: it returned internal errors on
  conversations with earlier tool calls. `rpm`/`tpm`/`rpd`
  (default free-tier limits per model, see `ratelimit.md`), `gemini_limits` (per-model overrides
  `model=rpm/rpd`), `max_retries` (per model), `fallback_backend` (`openai`/`ollama`
  server tried last; empty = none).
- `gemini_tier`: `free` (lite models first, strong models in reserve) or `paid` (strong models first, much higher
  limits). `stream`: show answers while they are written. `helpers` / `helper_turns`: the `delegate` tool and how many
  model calls one helper may use. `gemini_timeout`: an online request (Gemini or another service) is given up after 2
  minutes — a hung connection should not wait 15. `fallback_backend` may also be `local`: the local model of
  `localserver.py`, started when needed.
- **The online service** (see `providers.md`): `provider` — who answers online: `gemini` (the default and the only one
  with a free quota) or `openai`, `anthropic`, `openrouter`, `groq`, `mistral`, `deepseek`, `custom`; env
  `LOCALAGENT_PROVIDER`, flag `--provider`. `provider_model`: that service's model (env `LOCALAGENT_PROVIDER_MODEL`;
  Gemini ignores it and uses its own chain). `provider_base`: its address — from the table, or
  `LOCALAGENT_PROVIDER_BASE` (needed for `custom`, possible for any, e.g. a company proxy). `provider_key`: your key,
  read from the **service's own variable** (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, ...), so a key you already have set
  for other tools just works. `localagent setup` fills all four for you.
- **Online / offline settings:** `mode` — `auto` (default: the online model when it is set up, the local model when
  that cannot be reached or nothing online is set up), `online` (the online model only) or `offline` (the local model
  only); env `LOCALAGENT_MODE`, flags `--mode`, `--online`, `--offline`, or `/model` in the chat. `local_server`:
  which local model counts — empty: any server that answers (then Ollama, then llama.cpp started here); `openai`: only
  the server at `api_base` (started from the downloaded model when nothing answers and the address is on this
  computer); `ollama`: only Ollama. `localagent setup` writes it when you pick one (env `LOCALAGENT_LOCAL_SERVER`), so
  your choice is never replaced by a different model that happens to run. `local_ctx`: the local model's
  context window (8K; the server is started with it). `llama_server` and `model_file`: where llama.cpp's server program
  and the `.gguf` model are (empty = look in the usual places, see `localserver.md`). `gpu_layers`: how many of the
  model's layers go to the graphics card; `-1` lets llama.cpp fit as many as the card's memory holds (it measures this
  itself), any other number is used as given (this laptop's 2 GB card: about 9). `local_threads`: CPU threads for the
  local model (0 = llama.cpp decides).
- **Sandbox settings:** `sandbox` (`local` or `docker`), `sandbox_image`, `sandbox_network` (internet inside the
  container; off by default), `sandbox_memory`, `sandbox_cpus` (see `runners.md`).

## `Config.from_env`
@@ localagent/config.py Config.from_env @@
- `@classmethod` means it is called on the class (`Config.from_env()`) and receives the class as `cls`.
- `base = cls()` is a Config with all defaults. `replace(base, ...)` returns a copy with the listed fields overridden.
- Each `os.environ.get("NAME", default)` reads an environment variable, falling back to the default. Values are text,
  so `workspace` is wrapped in `Path(...)` and whole numbers are read with `_int`. `api_base` has any trailing `/`
  removed.
- `tier`/`paid` are read first because they change the default limits. `LOCALAGENT_RPM`, `LOCALAGENT_RPD` and
  `LOCALAGENT_TPM` override the limits directly; otherwise the paid presets or the free defaults apply.
- `mode` is read in lower case and checked against `MODES`; anything else is explained as a `ValueError`. The same for
  `local_server` and `LOCAL_SERVERS`.
- Also read: `GEMINI_API_KEY` (spaces stripped), `LOCALAGENT_GEMINI_MODELS` (comma list; empty keeps the default
  chain), `LOCALAGENT_GEMINI_LIMITS` (comma list of `model=rpm/rpd`), `LOCALAGENT_REASONING`, `LOCALAGENT_FALLBACK`,
  `LOCALAGENT_SANDBOX`, and for the local model `LOCALAGENT_LOCAL_CTX`, `LOCALAGENT_LLAMA_SERVER`,
  `LOCALAGENT_MODEL_FILE`, `LOCALAGENT_GPU_LAYERS` and `LOCALAGENT_THREADS`. The Docker image sets several of these;
  a settings file can set any of them with `"env"` (see `settings.md`).
- The finished config goes through `with_provider` with `LOCALAGENT_PROVIDER` (default `gemini`) and
  `LOCALAGENT_PROVIDER_MODEL`, which fills in the online service's address and key.

## `with_provider`
@@ localagent/config.py with_provider @@
- Makes `name` the online service: looks it up in the table (any letter case; an unknown name is a `ValueError` that
  lists the valid ones), takes its address — or `LOCALAGENT_PROVIDER_BASE` when set, without a trailing `/` — and
  reads your key from the variable the table names for it. Used by `from_env` and by the `--provider` flag (`cli.md`),
  so a flag and a variable work exactly the same way.

## `online_problem`
@@ localagent/config.py online_problem @@
- One place that decides whether the online model can work, in words a person can act on; empty text means "ready".
  Gemini needs only its key. Another service needs an address (only `custom` can lack one), a key (except `custom`:
  a server on your own network may need none) and a chosen model. Every message ends with the fix — `localagent
  setup`, or the variable to set. The modes (`cli.choose_mode`), `make_hosted`, `/model` and `--doctor` all ask it, so
  they can never disagree about whether online works.

## Shared rules used by every entry point (chat, server, evals)
@@ localagent/config.py after_online_problem @@
- `GEMINI_CONTEXT` (32 768 tokens) is the history budget for online models, which are built for long prompts; the 8K
  default (`num_ctx`) suits small local models.

@@ localagent/config.py overlay @@
- Returns a copy of the config with only the given settings replaced; a value of `None` means "this flag was not given"
  and is skipped. This is how flags beat environment variables, which beat defaults. `**given: Any` accepts any
  setting name; `replace` itself rejects names that are not settings.

@@ localagent/config.py gemini_history @@
- Raises `num_ctx` to `GEMINI_CONTEXT` when the backend is online (`gemini` or `hosted`), unless you chose a size
  yourself (`chosen` = the
  `--num-ctx` flag was given, or `LOCALAGENT_NUM_CTX` is set). The chat, the HTTP server and the evals all call it,
  so all three behave the same. Before, the rule lived in the chat only: the server and the evals ran Gemini with
  the 8K budget, so long sessions summarised early (extra calls) and the evals measured a different setup than the
  one you use.

