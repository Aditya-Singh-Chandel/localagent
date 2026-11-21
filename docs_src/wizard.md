# `localagent/wizard.py` — `localagent setup`, the guided first run

**Role:** turns a fresh install into a working agent without reading anything: three short steps, Enter accepts the
suggestion in [brackets]. It also starts by itself the very first time you type `localagent` with nothing set up
(`guided_first_run` in `cli.md`).

A real run (21 Nov 2026, a llama.cpp server started by hand on port 8181, the answers piped in by a script):
```
localagent setup: three short steps; Enter accepts the suggestion in [brackets].

1/3  Online model: fast and strong; needs the internet and an account (Gemini's is free)
  1) Google Gemini                      free daily quota, no card needed  [in use]
  2) OpenAI                             pay per use
  3) Anthropic (Claude)                 pay per use
  4) OpenRouter                         one key, hundreds of models, some free
  5) Groq                               free tier, very fast
  6) Mistral                            free tier
  7) DeepSeek                           very low prices
  8) another OpenAI-compatible service  any address you give
  9) skip - keep what is set up now
  Choose 1-9 [1]:
  ok    GEMINI_API_KEY is set; checking it
  ok    the key works (65 models offered)
  ok    online model: Google Gemini (saved in ...\.localagent\settings.json)

2/3  Offline model: runs on this computer - private, no quota, slower
  1) the downloaded model Qwen3-8B-Q4_K_M.gguf (started when needed)
  2) a model server at another address (you type it)
  3) skip - keep what is set up now
  Choose 1-3 [1]: 2
  Its address, e.g. http://192.168.1.20:8080/v1 (for Ollama: http://host:11434): http://127.0.0.1:8181/v1
  Models there: qwen3-8b-mine
  Model [qwen3-8b-mine]:
  ok    offline model: qwen3-8b-mine on the model server at http://127.0.0.1:8181/v1

3/3  Sandbox: Docker runs the agent's code in a sealed box (optional)
  note  Docker is not running. ...

Ready, online and offline. In a project folder, type: localagent   (/model switches)
```
A local server that is already running (Ollama, LM Studio, llama.cpp, vLLM, Jan) is found by itself and offered
first, with its models. Without one, the choice is "download a free model for me": llama.cpp and the Qwen3 model sized
for this computer's memory, both checked against their publisher's checksum (`models.md`).

**What is saved, and where.** Only in your user settings file (`%USERPROFILE%\.localagent\settings.json`, its `env`
part), so the choice applies in every folder and never lands in a project. A key is saved only when you pasted it — a
key already in your environment stays where it is. A real environment variable still beats the file, and the setup
says so when that would hide your choice.

| You choose | Saved |
|---|---|
| Gemini | `LOCALAGENT_PROVIDER=gemini` (and `GEMINI_API_KEY` if pasted) |
| another service | `LOCALAGENT_PROVIDER`, `LOCALAGENT_PROVIDER_MODEL` (and its key if pasted; for `custom` also `LOCALAGENT_PROVIDER_BASE`) |
| Ollama | `LOCALAGENT_LOCAL_SERVER=ollama`, `LOCALAGENT_MODEL` (and `OLLAMA_HOST` for an unusual address) |
| LM Studio, llama.cpp, vLLM, Jan, another address | `LOCALAGENT_LOCAL_SERVER=openai`, `LOCALAGENT_MODEL`, `LOCALAGENT_API_BASE` (unless it is the default) |
| the downloaded model | `LOCALAGENT_LOCAL_SERVER=openai`; the server address and model name of an earlier choice are forgotten |

## Module header
@@ localagent/wizard.py header @@
- Imports `getpass` (keys typed without being shown), `os` (the environment), `sys` (is a person typing?), `partial`
  (a menu action with its arguments already filled in), `Path`, `Callable`; from the agent: the config and
  `online_problem`, `DownloadError`/`Progress`, the local-model finders and checks, the catalog and download helpers
  (`models.py`), the provider tables and helpers (`providers.py`), the Docker checks, and the settings-file functions.
- `Scan` is the type of the local-server scan (tests give a fake one). `SHOWN_MODELS`: how many model names are listed
  as examples — OpenRouter offers hundreds.

## `read_secret`
@@ localagent/wizard.py read_secret @@
- How a key is read. A person at the keyboard gets `getpass` (nothing appears while typing). When the answers come from
  a pipe (a script), plain `input` reads them: Windows' `getpass` reads the keyboard only, so a scripted setup that
  needed a key waited forever — found by the live test.

## `run_setup`
@@ localagent/wizard.py run_setup @@
- The three steps and a summary. Every input and output can be replaced (`ask`, `secret`, `say`, the settings file, the
  progress display, the server scan), so the tests drive it without a keyboard, a network or downloads. Returns 0 when
  at least one model — online or offline — is ready, else 1.

## `Wizard` — asking
@@ localagent/wizard.py Wizard @@
@@ localagent/wizard.py Wizard.__init__ @@
- Keeps the config and the injected functions. `_real` remembers which variables come from your **real** environment:
  the ones whose value differs from the settings file's (the file's values were copied into this run's environment
  before the setup started).

@@ localagent/wizard.py Wizard.answer @@
@@ localagent/wizard.py Wizard.hidden @@
- An answer without surrounding spaces; `None` (or `""` for a key) when input has ended — Ctrl+Z, Ctrl+C, the end of
  a script — so nothing more is changed. A byte-order mark is removed: Windows PowerShell 5.1 puts one in front of
  text piped into a program, and the first answer `1` arrived as `﻿1` and was refused — found by the live test.

@@ localagent/wizard.py Wizard.yes @@
- A [Y/n] question: Enter or `y` is yes; ended input is no (a download never starts by accident in a script).

@@ localagent/wizard.py Wizard.choose @@
- Prints the numbered choices and asks until it gets a number in range (Enter = the default); `None` when input ends.

@@ localagent/wizard.py Wizard.save @@
- Writes the setting to the user settings file (`None` forgets it), puts it into this run's environment at once, and
  warns when a real environment variable of that name would still win next time.

## Step 1: the online model
@@ localagent/wizard.py Wizard.online @@
- Lists every service with its price; `[in use]` marks the one that works now, `[key found]` every service whose key
  variable is already set. The suggestion is the one in use, else the first with a key, else Gemini. The last line,
  "skip", keeps what is set up now (or leaves the agent offline-only). Returns whether an online model is ready.

@@ localagent/wizard.py Wizard.use_provider @@
- `custom` first asks for the address (Enter keeps the one saved before).
- The key: found in its variable, or pasted (hidden). Without one there is no online model — except for `custom`.
- The check lists the service's models: a refused key saves **nothing** and says where to make a new one; an
  unreachable service keeps the choice unchecked (you may simply be offline right now).
- Every service but Gemini (which has its own chain of models) then gets a model chosen; skipping that saves nothing.
- Only then is anything saved: the pasted key, the service, its model, and the address for `custom` — and a model or
  address left from an earlier choice is forgotten.

@@ localagent/wizard.py Wizard.choose_model @@
- The suggestion: the model chosen before for this service if it is still offered, else `pick_model` (a good one for
  this agent), else the table's first suggestion. Up to `SHOWN_MODELS` chat models are listed as examples. A name the
  service does not list is accepted with a note — some services accept more names than they list.

## Step 2: the offline model
@@ localagent/wizard.py Wizard.offline @@
- Scans for running local servers (your own addresses first) and offers each with up to three of its models. Then the
  downloaded model — or, when llama.cpp or the model is missing, "download a free model for me" with the model this
  computer's memory suits. Then "another address", then "skip". Enter takes the first: what you already run beats a
  download.
- `partial(self.use_server, server, models)` makes one menu action per server without the lambda-in-a-loop trap (all
  lambdas would see the last server).

@@ localagent/wizard.py Wizard.own_servers @@
- `OLLAMA_HOST` or `LOCALAGENT_API_BASE` set to something other than the default are looked at first: you told the
  agent about them.

@@ localagent/wizard.py Wizard.use_server @@
- Lists the server's models and suggests one (`pick_local_model`); a server without models says how to get one
  (`ollama pull qwen3:8b`). Saves the kind, the model and — only when it is not the default — the address. Choosing
  Ollama forgets a saved `LOCALAGENT_API_BASE`, so an old address cannot be asked first.

@@ localagent/wizard.py Wizard.use_downloaded @@
- The model this program downloads and starts itself: kind `openai` at the default address; an earlier server's
  address and model name are forgotten, or they would point somewhere else.

@@ localagent/wizard.py Wizard.download @@
- Shows the memory and the suggested model, then for llama.cpp and for the model: found → ok; missing → asks (Enter =
  yes) and downloads with progress. A failed download is reported and setup carries on. When both are there, they are
  used.

@@ localagent/wizard.py Wizard.use_address @@
- Asks for an address; `host:port` alone gets `http://` in front. It first asks it as Ollama (without a trailing
  `/v1`): Ollama's own API sets the context size, its OpenAI-style one cannot. Otherwise as an OpenAI-style server. When
  nothing answers, it says so and saves nothing.

## Step 3: the sandbox
@@ localagent/wizard.py Wizard.sandbox @@
- Only reports: Docker not running (and that the agent works without it), ready, or running without the sandbox image
  (with the exact build command).
