# `localagent/config.py` — settings

**Role in the flow:** created first at startup. Every other module receives one `Config` object instead of
reading globals, so changing behaviour (model, limits, safety switches) means changing one value here, and tests
can build their own `Config`.

**Depends on:** only the standard library. **Used by:** almost every module.

## Module header
```python
  1 | """Central configuration for the local coding agent.
  2 | 
  3 | Every tunable number or name lives here, so the rest of the code contains no
  4 | "magic values". Values can be overridden with environment variables
  5 | (``Config.from_env``) or with command-line flags (see ``cli.py``).
  6 | """
  7 | from __future__ import annotations
  8 | 
  9 | import os
 10 | from dataclasses import dataclass, replace
 11 | from pathlib import Path
 12 | from typing import Any
 13 | 
 14 | 
```
- The triple-quoted text is the module docstring (shown by `help()`); it has no effect on behaviour.
- `Any` types the free-form settings accepted by `overlay`.
- `from __future__ import annotations` makes type hints lazy, so forward references and newer syntax such as
  `list[str]` work on older Pythons.
- `import os` gives `os.environ` (environment variables). `dataclass` generates the constructor for a class from its
  fields; `replace` copies a frozen dataclass with some fields changed. `Path` is the standard file-path object.

## `_normalize_host`
```python
 15 | def _normalize_host(raw: str) -> str:
 16 |     """Turn ``127.0.0.1:11434`` (Ollama's own style) into a full URL."""
 17 |     if raw.startswith(("http://", "https://")):
 18 |         return raw.rstrip("/")
 19 |     return "http://" + raw.rstrip("/")
 20 | 
 21 | 
```
- The leading underscore marks it private. It takes the raw `OLLAMA_HOST` text and returns a URL.
- Ollama's own docs let people write `127.0.0.1:11434` without a scheme. If the text already starts with `http://`
  or `https://` we only remove a trailing `/` (so `host + "/api/chat"` never gives `//`); otherwise we prepend `http://`.

## Paid-tier presets
```python
 22 | PAID_RPM, PAID_RPD, PAID_TPM = 1000, 10_000, 1_000_000   # paid-tier presets (well under Tier 1)
 23 | 
 24 | 
```
- `PAID_RPM`, `PAID_RPD`, `PAID_TPM`: per-model limits used when `LOCALAGENT_GEMINI_TIER=paid` (billing enabled on your
  Google project). They sit well under Google's Tier 1 limits; the agent still adopts lower numbers if Google reports
  them.

## `_split`
```python
 25 | def _split(raw: str) -> tuple[str, ...]:
 26 |     """Turn ``"a, b,c"`` into ``("a", "b", "c")``; empty text gives ``()``."""
 27 |     return tuple(part.strip() for part in raw.split(",") if part.strip())
 28 | 
 29 | 
```
- Turns a comma-separated environment value such as `"a, b,c"` into a tuple `("a", "b", "c")`, ignoring blanks.

## `Config` — the settings
```python
 30 | @dataclass(frozen=True)
 31 | class Config:
 32 |     """Immutable settings object that is handed to every component."""
 33 | 
 34 |     backend: str = "ollama"                  # "ollama" or "openai" (OpenAI-compatible server)
 35 |     model: str = "qwen3:8b"                  # model tag/name understood by the backend
 36 |     host: str = "http://localhost:11434"     # Ollama server address
 37 |     api_base: str = "http://127.0.0.1:8080/v1"  # OpenAI-compatible server (llama.cpp serve.ps1;
 38 |     #                                             LM Studio uses http://localhost:1234/v1)
 39 |     api_key: str = ""                        # bearer token for the OpenAI-compatible server
 40 |     workspace: Path = Path(".")              # the ONLY folder the agent may touch (default: here)
 41 |     state_dir: Path = Path(".localagent")    # session logs: <workspace>/.localagent/sessions
 42 |     num_ctx: int = 8192                      # context window requested from Ollama
 43 |     temperature: float = 0.0                 # 0 = greedy: the most repeatable code
 44 |     max_turns: int = 20                      # model calls allowed per user request
 45 |     max_fix_attempts: int = 3                # rejected writes allowed per file
 46 |     command_timeout: int = 30                # seconds before a run is killed
 47 |     max_output_chars: int = 4000             # cap on any single tool result
 48 |     memory_chars: int = 2000                 # cap on AGENT.md text put in the prompt
 49 |     tree_entries: int = 40                   # cap on file-tree lines put in the prompt
 50 |     type_check: bool = False                 # also run mypy during verification
 51 |     auto_approve: bool = False               # skip the "may I run this?" prompt
 52 |     allow_host_shell: bool = False           # offer run_shell on this computer (asks every time)
 53 |     usage_file: Path = Path.home() / ".localagent" / "usage.json"   # quota counts, all projects
 54 |     compact_at: float = 0.6                  # summarise history above this share of the budget
 55 |     diff_lines: int = 40                     # lines of each edit's diff shown to the user
 56 |     auto_format: bool = True                 # run `ruff format` on every verified Python write
 57 |     max_nudges: int = 1                      # "you changed nothing" reminders per request
 58 |     reasoning_effort: str = ""               # sent to OpenAI-style servers when set ("low", ...)
 59 |     stream: bool = True                      # show OpenAI-style answers while they are written
 60 |     helpers: bool = True                     # offer the delegate tool (read-only helper agents)
 61 |     helper_turns: int = 8                    # model calls a helper may use
 62 |     # ---- Gemini (hosted) -----------------------------------------------------------
 63 |     gemini_api_key: str = ""                 # from the GEMINI_API_KEY environment variable
 64 |     gemini_tier: str = "free"                # "free" or "paid" (billing on: higher limits)
 65 |     gemini_base: str = "https://generativelanguage.googleapis.com/v1beta/openai"
 66 |     gemini_models: tuple[str, ...] = ("gemini-3.5-flash-lite", "gemini-3.1-flash-lite")
 67 |     # ^ everyday chain, tried in order: fast "lite" models with large daily quotas
 68 |     gemini_strong_models: tuple[str, ...] = ("gemini-3.5-flash", "gemini-2.5-flash")
 69 |     # ^ reserve: small daily quota (~20 on a free key), used first only after an escalation
 70 |     escalate_after: int = 2                  # failed tool results in one request before escalating
 71 |     rpm: int = 12                            # requests per minute allowed per model
 72 |     tpm: int = 250_000                       # tokens per minute allowed per model
 73 |     rpd: int = 1500                          # requests per day allowed per model
 74 |     gemini_limits: tuple[str, ...] = ()      # per-model overrides "model=rpm/rpd", e.g. "m=5/20"
 75 |     max_retries: int = 3                     # retries of one model on 429/5xx/network errors
 76 |     fallback_backend: str = ""               # e.g. "openai": local server used last
 77 |     # ---- where code runs --------------------------------------------------------------
 78 |     sandbox: str = "local"                   # "local" (this machine) or "docker" (container)
 79 |     sandbox_image: str = "localagent-sandbox:latest"
 80 |     sandbox_network: bool = False            # allow internet inside the container (pip install)
 81 |     sandbox_memory: str = "1g"               # container memory cap
 82 |     sandbox_cpus: str = "1.0"                # container CPU cap
 83 | 
```
- `@dataclass(frozen=True)` builds `__init__` from the fields and makes instances **immutable**, so no component can
  silently change a setting mid-run.
- `backend`: which model-server client to use (`ollama` or `openai`; see `backends.md`).
- `model`: the model tag/name the server understands. `host`: where Ollama listens.
- `api_base` / `api_key`: address and bearer token for an OpenAI-compatible server. The default
  `http://127.0.0.1:8080/v1` is where `serve.ps1` starts llama.cpp (LM Studio uses `http://localhost:1234/v1`).
- `workspace`: the **only** folder the agent may read or write (enforced by `sandbox.py`). Default `.` = the folder you
  start the agent in, like Claude Code: `cd` into a project, run `localagent`.
- `state_dir`: where session logs go, **relative to the workspace**: `<project>\.localagent\sessions`, next to the
  project's own `settings.json` and `commands`, so `--resume` always continues *this* project's conversation. (An
  absolute path is used as it is.) The `.localagent` folder is never listed, searched, checked or snapshotted (it is in
  `SKIP_DIRS`). Until the final verification it was relative to the folder the agent was *started from*: started
  from the agent's own folder with `--workspace`, every project's logs were mixed in one place and `--resume` could
  continue a different project's conversation.
- `num_ctx`: context window requested from Ollama (bigger = more RAM, slower). `temperature`: randomness; now **0.0** (greedy), the most
  repeatable choice for an agent, changed after the real model gave different outcomes for the same task.
- `max_turns`: hard cap on model calls per user request. `max_fix_attempts`: rejected writes allowed per file before
  the tool says STOP. `command_timeout`: seconds before a run is killed. `max_output_chars`: any tool result longer
  than this is shortened (saves tokens).
- `memory_chars` / `tree_entries`: caps on how much of `AGENT.md` and the file tree enter the system prompt.
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
  model calls one helper may use.
- **Sandbox settings:** `sandbox` (`local` or `docker`), `sandbox_image`, `sandbox_network` (internet inside the
  container; off by default), `sandbox_memory`, `sandbox_cpus` (see `runners.md`).

## `Config.from_env`
```python
 84 |     @classmethod
 85 |     def from_env(cls) -> "Config":
 86 |         """Build a Config from defaults, then apply environment overrides."""
 87 |         base = cls()
 88 |         tier = os.environ.get("LOCALAGENT_GEMINI_TIER", base.gemini_tier).strip().lower()
 89 |         paid = tier == "paid"
 90 |         return replace(
 91 |             base,
 92 |             backend=os.environ.get("LOCALAGENT_BACKEND", base.backend),
 93 |             model=os.environ.get("LOCALAGENT_MODEL", base.model),
 94 |             host=_normalize_host(os.environ.get("OLLAMA_HOST", base.host)),
 95 |             api_base=os.environ.get("LOCALAGENT_API_BASE", base.api_base).rstrip("/"),
 96 |             api_key=os.environ.get("LOCALAGENT_API_KEY", base.api_key),
 97 |             workspace=Path(os.environ.get("LOCALAGENT_WORKSPACE", str(base.workspace))),
 98 |             num_ctx=int(os.environ.get("LOCALAGENT_NUM_CTX", base.num_ctx)),
 99 |             gemini_api_key=os.environ.get("GEMINI_API_KEY", base.gemini_api_key).strip(),
100 |             gemini_models=_split(os.environ.get("LOCALAGENT_GEMINI_MODELS", ""))
101 |             or base.gemini_models,
102 |             gemini_strong_models=_split(os.environ.get("LOCALAGENT_GEMINI_STRONG", ""))
103 |             or base.gemini_strong_models,
104 |             gemini_limits=_split(os.environ.get("LOCALAGENT_GEMINI_LIMITS", "")),
105 |             gemini_tier=tier,
106 |             rpm=int(os.environ.get("LOCALAGENT_RPM", PAID_RPM if paid else base.rpm)),
107 |             rpd=int(os.environ.get("LOCALAGENT_RPD", PAID_RPD if paid else base.rpd)),
108 |             tpm=int(os.environ.get("LOCALAGENT_TPM", PAID_TPM if paid else base.tpm)),
109 |             reasoning_effort=os.environ.get("LOCALAGENT_REASONING", base.reasoning_effort),
110 |             fallback_backend=os.environ.get("LOCALAGENT_FALLBACK", base.fallback_backend),
111 |             sandbox=os.environ.get("LOCALAGENT_SANDBOX", base.sandbox),
112 |             usage_file=Path(os.environ.get("LOCALAGENT_USAGE_FILE", str(base.usage_file))),
113 |         )
114 | 
115 | 
```
- `@classmethod` means it is called on the class (`Config.from_env()`) and receives the class as `cls`.
- `base = cls()` is a Config with all defaults. `replace(base, ...)` returns a copy with the listed fields overridden.
- Each `os.environ.get("NAME", default)` reads an environment variable, falling back to the default. Values are text,
  so `workspace` is wrapped in `Path(...)` and `num_ctx` in `int(...)`. `api_base` has any trailing `/` removed.
- `tier`/`paid` are read first because they change the default limits. `LOCALAGENT_RPM`, `LOCALAGENT_RPD` and
  `LOCALAGENT_TPM` override the limits directly; otherwise the paid presets or the free defaults apply.
- Also read: `GEMINI_API_KEY` (spaces stripped), `LOCALAGENT_GEMINI_MODELS` (comma list; empty keeps the default
  chain), `LOCALAGENT_GEMINI_LIMITS` (comma list of `model=rpm/rpd`), `LOCALAGENT_REASONING`, `LOCALAGENT_FALLBACK`, `LOCALAGENT_SANDBOX`. The Docker image sets several of these.

## Shared rules used by every entry point (chat, server, evals)
```python
116 | GEMINI_CONTEXT = 32_768   # history budget for Gemini (built for long prompts; 8K suits local models)
117 | 
118 | 
```
- `GEMINI_CONTEXT` (32 768 tokens) is the history budget for Gemini, which is built for long prompts; the 8K default
  (`num_ctx`) suits small local models.

```python
119 | def overlay(config: Config, **given: Any) -> Config:
120 |     """Replace only the settings that were actually given (``None`` = not given)."""
121 |     return replace(config, **{key: value for key, value in given.items() if value is not None})
122 | 
123 | 
```
- Returns a copy of the config with only the given settings replaced; a value of `None` means "this flag was not given"
  and is skipped. This is how flags beat environment variables, which beat defaults. `**given: Any` accepts any
  setting name; `replace` itself rejects names that are not settings.

```python
124 | def gemini_history(config: Config, chosen: bool = False) -> Config:
125 |     """Give Gemini the 32K history budget, unless a size was chosen (flag or environment)."""
126 |     chosen = chosen or "LOCALAGENT_NUM_CTX" in os.environ
127 |     if config.backend == "gemini" and not chosen and config.num_ctx < GEMINI_CONTEXT:
128 |         return replace(config, num_ctx=GEMINI_CONTEXT)
129 |     return config
```
- Raises `num_ctx` to `GEMINI_CONTEXT` when the backend is Gemini, unless you chose a size yourself (`chosen` = the
  `--num-ctx` flag was given, or `LOCALAGENT_NUM_CTX` is set). The chat, the HTTP server and the evals all call it,
  so all three behave the same. Before, the rule lived in the chat only: the server and the evals ran Gemini with
  the 8K budget, so long sessions summarised early (extra calls) and the evals measured a different setup than the
  one you use.

