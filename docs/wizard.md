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
```python
  1 | """`localagent setup`: the guided first run, in three short steps.
  2 | 
  3 | 1/3  Online model: Google Gemini (its key is free), another service with your own key (OpenAI,
  4 |      Anthropic, OpenRouter, Groq, Mistral, DeepSeek, any OpenAI-compatible address) - or none.
  5 | 2/3  Offline model: a model server you already run (Ollama, LM Studio, llama.cpp, vLLM, Jan), a
  6 |      free model this program downloads and runs for you, a server at another address - or none.
  7 | 3/3  Sandbox: whether Docker is ready to run the agent's code in a sealed box (optional).
  8 | 
  9 | Every key is checked before it is saved (listing a service's models costs no quota). Choices go
 10 | into your user settings file (``~/.localagent/settings.json``, its "env" part), so they apply in
 11 | every folder; a real environment variable still wins. Enter accepts the suggestion shown in
 12 | [brackets]; when input ends (Ctrl+Z, a script), nothing more is changed.
 13 | """
 14 | from __future__ import annotations
 15 | 
 16 | import getpass
 17 | import os
 18 | import sys
 19 | from functools import partial
 20 | from pathlib import Path
 21 | from typing import Callable
 22 | 
 23 | from .config import Config, online_problem
 24 | from .fetch import DownloadError, Progress
 25 | from .localserver import can_start, find_llama_server, find_model_file, running_local
 26 | from .models import (GiB, CatalogModel, Say, install_llama, install_model, progress_printer,
 27 |                      recommend, sandbox_hint, total_memory_gb)
 28 | from .providers import (CUSTOM_KEY, PROVIDERS, LocalServer, Provider, chat_models, check_key,
 29 |                         find_provider, key_headers, pick_local_model, pick_model,
 30 |                         scan_local_servers, server_models)
 31 | from .runners import docker_available, sandbox_image_ready
 32 | from .settings import USER_SETTINGS, read_json, save_user_env
 33 | 
 34 | Scan = Callable[[tuple[LocalServer, ...]], list[tuple[LocalServer, list[str]]]]
 35 | SHOWN_MODELS = 8                                   # models listed as examples
 36 | 
 37 | 
```
- Imports `getpass` (keys typed without being shown), `os` (the environment), `sys` (is a person typing?), `partial`
  (a menu action with its arguments already filled in), `Path`, `Callable`; from the agent: the config and
  `online_problem`, `DownloadError`/`Progress`, the local-model finders and checks, the catalog and download helpers
  (`models.py`), the provider tables and helpers (`providers.py`), the Docker checks, and the settings-file functions.
- `Scan` is the type of the local-server scan (tests give a fake one). `SHOWN_MODELS`: how many model names are listed
  as examples — OpenRouter offers hundreds.

## `read_secret`
```python
 38 | def read_secret(question: str) -> str:
 39 |     """A key typed by a person is not shown (getpass). Keys piped in by a script are read like
 40 |     any answer: Windows' getpass reads only the keyboard and would wait forever."""
 41 |     return getpass.getpass(question) if sys.stdin.isatty() else input(question)
 42 | 
 43 | 
```
- How a key is read. A person at the keyboard gets `getpass` (nothing appears while typing). When the answers come from
  a pipe (a script), plain `input` reads them: Windows' `getpass` reads the keyboard only, so a scripted setup that
  needed a key waited forever — found by the live test.

## `run_setup`
```python
 44 | def run_setup(config: Config, ask: Callable[[str], str] = input,
 45 |               secret: Callable[[str], str] = read_secret, say: Say = print,
 46 |               settings_file: Path = USER_SETTINGS, progress: Progress | None = None,
 47 |               scan: Scan = scan_local_servers) -> int:
 48 |     """`localagent setup`. Returns 0 when at least one model (online or offline) is ready."""
 49 |     wizard = Wizard(config, ask, secret, say, settings_file, progress or progress_printer(say), scan)
 50 |     say("localagent setup: three short steps; Enter accepts the suggestion in [brackets].")
 51 |     say("\n1/3  Online model: fast and strong; needs the internet and an account (Gemini's is free)")
 52 |     online = wizard.online()
 53 |     say("\n2/3  Offline model: runs on this computer - private, no quota, slower")
 54 |     offline = wizard.offline()
 55 |     say("\n3/3  Sandbox: Docker runs the agent's code in a sealed box (optional)")
 56 |     wizard.sandbox()
 57 |     say("")
 58 |     if online and offline:
 59 |         say("Ready, online and offline. In a project folder, type: localagent   (/model switches)")
 60 |     elif online:
 61 |         say("Ready (online only). For offline work, run localagent setup again later.")
 62 |     elif offline:
 63 |         say("Ready (offline only). In a project folder, type: localagent")
 64 |     else:
 65 |         say("No model is ready yet. Run localagent setup again (a Gemini key is the quickest start).")
 66 |     say("Check everything at any time with: localagent --doctor")
 67 |     return 0 if online or offline else 1
 68 | 
 69 | 
```
- The three steps and a summary. Every input and output can be replaced (`ask`, `secret`, `say`, the settings file, the
  progress display, the server scan), so the tests drive it without a keyboard, a network or downloads. Returns 0 when
  at least one model — online or offline — is ready, else 1.

## `Wizard` — asking
```python
 70 | class Wizard:
 71 |     """The questions of `localagent setup`, and what the answers save."""
 72 | 
```
```python
 73 |     def __init__(self, config: Config, ask: Callable[[str], str], secret: Callable[[str], str],
 74 |                  say: Say, settings_file: Path, progress: Progress, scan: Scan) -> None:
 75 |         self.config = config
 76 |         self._ask = ask
 77 |         self._secret = secret
 78 |         self.say = say
 79 |         self.settings_file = settings_file
 80 |         self.progress = progress
 81 |         self.scan = scan
 82 |         saved = read_json(settings_file).get("env", {})
 83 |         self._real = {name for name, value in os.environ.items() if saved.get(name) != value}
 84 | 
 85 |     # ---- asking ---------------------------------------------------------------------
```
- Keeps the config and the injected functions. `_real` remembers which variables come from your **real** environment:
  the ones whose value differs from the settings file's (the file's values were copied into this run's environment
  before the setup started).

```python
 86 |     def answer(self, question: str) -> str | None:
 87 |         """The typed answer without surrounding spaces, or None when input has ended. A
 88 |         byte-order mark is dropped: Windows PowerShell puts one before piped answers."""
 89 |         try:
 90 |             return self._ask(question).replace("\ufeff", "").strip()
 91 |         except (EOFError, KeyboardInterrupt):
 92 |             return None
 93 | 
```
```python
 94 |     def hidden(self, question: str) -> str:
 95 |         """A typed secret, not shown on the screen; "" when skipped or input has ended."""
 96 |         try:
 97 |             return self._secret(question).replace("\ufeff", "").strip()
 98 |         except (EOFError, KeyboardInterrupt):
 99 |             return ""
100 | 
```
- An answer without surrounding spaces; `None` (or `""` for a key) when input has ended — Ctrl+Z, Ctrl+C, the end of
  a script — so nothing more is changed. A byte-order mark is removed: Windows PowerShell 5.1 puts one in front of
  text piped into a program, and the first answer `1` arrived as `﻿1` and was refused — found by the live test.

```python
101 |     def yes(self, question: str) -> bool:
102 |         """A [Y/n] question: Enter or "y" is yes; input that has ended is no."""
103 |         typed = self.answer(question)
104 |         return typed is not None and typed.lower() in ("", "y", "yes")
105 | 
```
- A [Y/n] question: Enter or `y` is yes; ended input is no (a download never starts by accident in a script).

```python
106 |     def choose(self, labels: list[str], default: int) -> int | None:
107 |         """Numbered choices; the number picked (from 1), or None when input has ended."""
108 |         for number, label in enumerate(labels, 1):
109 |             self.say(f"  {number}) {label}")
110 |         while True:
111 |             typed = self.answer(f"  Choose 1-{len(labels)} [{default}]: ")
112 |             if typed is None:
113 |                 return None
114 |             if not typed:
115 |                 return default
116 |             if typed.isdigit() and 1 <= int(typed) <= len(labels):
117 |                 return int(typed)
118 |             self.say(f"  please type a number from 1 to {len(labels)}")
119 | 
```
- Prints the numbered choices and asks until it gets a number in range (Enter = the default); `None` when input ends.

```python
120 |     def save(self, name: str, value: str | None) -> None:
121 |         """Remember a setting in the user settings file (None forgets it) and use it at once."""
122 |         save_user_env(name, value, self.settings_file)
123 |         if value is None:
124 |             os.environ.pop(name, None)
125 |         else:
126 |             os.environ[name] = value
127 |         if name in self._real:
128 |             self.say(f"  note  {name} is also set in your environment, and that one wins: remove "
129 |                      "it there for this choice to apply")
130 | 
131 |     # ---- step 1: online ---------------------------------------------------------------
```
- Writes the setting to the user settings file (`None` forgets it), puts it into this run's environment at once, and
  warns when a real environment variable of that name would still win next time.

## Step 1: the online model
```python
132 |     def online(self) -> bool:
133 |         """Step 1: the online service, its key (checked, or asked for) and its model."""
134 |         current = find_provider(self.config.provider) or PROVIDERS[0]
135 |         ready = not online_problem(self.config)
136 |         labels = []
137 |         for provider in PROVIDERS:
138 |             keyed = provider.key_env != CUSTOM_KEY and bool(os.environ.get(provider.key_env))
139 |             mark = "  [in use]" if ready and provider == current else "  [key found]" if keyed else ""
140 |             labels.append(f"{provider.title:<34} {provider.note}{mark}")
141 |         labels.append("skip - keep what is set up now" if ready else "skip - no online model")
142 |         keyed_first = next((number for number, provider in enumerate(PROVIDERS, 1)
143 |                             if provider.key_env != CUSTOM_KEY and os.environ.get(provider.key_env)), 1)
144 |         choice = self.choose(labels, PROVIDERS.index(current) + 1 if ready else keyed_first)
145 |         if choice is None or choice > len(PROVIDERS):
146 |             self.say("  skipped" + ("" if ready else ": the agent works offline only"))
147 |             return ready
148 |         return self.use_provider(PROVIDERS[choice - 1])
149 | 
```
- Lists every service with its price; `[in use]` marks the one that works now, `[key found]` every service whose key
  variable is already set. The suggestion is the one in use, else the first with a key, else Gemini. The last line,
  "skip", keeps what is set up now (or leaves the agent offline-only). Returns whether an online model is ready.

```python
150 |     def use_provider(self, provider: Provider) -> bool:
151 |         """Check (or ask for) the key of ``provider``, choose its model, save the choice."""
152 |         base = provider.base
153 |         if provider.name == "custom":
154 |             known = self.config.provider_base if self.config.provider == "custom" else ""
155 |             typed = self.answer(f"  The service's address, usually ending in /v1 [{known}]: ")
156 |             base = (typed or known).rstrip("/") if typed is not None else ""
157 |             if not base:
158 |                 self.say("  skipped: no address given")
159 |                 return False
160 |         key = os.environ.get(provider.key_env, "").strip()
161 |         pasted = False
162 |         if key:
163 |             self.say(f"  ok    {provider.key_env} is set; checking it")
164 |         else:
165 |             if provider.key_page:
166 |                 self.say(f"  Get a key at {provider.key_page}")
167 |             key = self.hidden("  Paste the key (it stays hidden; Enter skips): ")
168 |             pasted = bool(key)
169 |             if not key and provider.name != "custom":      # a custom service may need no key
170 |                 self.say("  skipped: without a key there is no online model")
171 |                 return False
172 |         state, offered, detail = check_key(base, key_headers(provider, key))
173 |         if state == "rejected":
174 |             again = f" Make a new key at {provider.key_page}" if provider.key_page else ""
175 |             self.say(f"  FAIL  {provider.title} refused the key ({detail}); nothing was saved.{again}")
176 |             return False
177 |         if state == "unreachable":
178 |             self.say(f"  note  {provider.title} cannot be reached right now ({detail}); the choice "
179 |                      "is saved unchecked")
180 |         else:
181 |             self.say(f"  ok    the key works ({len(offered)} models offered)")
182 |         model = ""
183 |         if provider.name != "gemini":                  # Gemini uses its own chain of models
184 |             model = self.choose_model(provider, offered)
185 |             if not model:
186 |                 self.say("  skipped: no model chosen, nothing was saved")
187 |                 return False
188 |         if pasted:
189 |             self.save(provider.key_env, key)
190 |         self.save("LOCALAGENT_PROVIDER", provider.name)
191 |         self.save("LOCALAGENT_PROVIDER_MODEL", model or None)
192 |         self.save("LOCALAGENT_PROVIDER_BASE", base if provider.name == "custom" else None)
193 |         chosen = provider.title + (f", model {model}" if model else "")
194 |         self.say(f"  ok    online model: {chosen} (saved in {self.settings_file})")
195 |         return True
196 | 
```
- `custom` first asks for the address (Enter keeps the one saved before).
- The key: found in its variable, or pasted (hidden). Without one there is no online model — except for `custom`.
- The check lists the service's models: a refused key saves **nothing** and says where to make a new one; an
  unreachable service keeps the choice unchecked (you may simply be offline right now).
- Every service but Gemini (which has its own chain of models) then gets a model chosen; skipping that saves nothing.
- Only then is anything saved: the pasted key, the service, its model, and the address for `custom` — and a model or
  address left from an earlier choice is forgotten.

```python
197 |     def choose_model(self, provider: Provider, offered: list[str]) -> str:
198 |         """Suggest a model (a good one for this agent, when offered) and let the user type another."""
199 |         current = self.config.provider_model if self.config.provider == provider.name else ""
200 |         suggestion = current if current and (not offered or current in offered) else \
201 |             pick_model(provider, offered)
202 |         if not suggestion and provider.suggested:
203 |             suggestion = provider.suggested[0]
204 |         if offered:
205 |             examples = [suggestion] + [name for name in chat_models(offered) if name != suggestion]
206 |             self.say(f"  {len(offered)} models offered, for example: "
207 |                      + ", ".join(examples[:SHOWN_MODELS]))
208 |         typed = self.answer(f"  Model [{suggestion}]: ")
209 |         if typed is None:
210 |             return ""
211 |         model = typed or suggestion
212 |         if model and offered and model not in offered:
213 |             self.say(f"  note  {model} is not in {provider.title}'s list; it is used anyway")
214 |         return model
215 | 
216 |     # ---- step 2: offline --------------------------------------------------------------
```
- The suggestion: the model chosen before for this service if it is still offered, else `pick_model` (a good one for
  this agent), else the table's first suggestion. Up to `SHOWN_MODELS` chat models are listed as examples. A name the
  service does not list is accepted with a note — some services accept more names than they list.

## Step 2: the offline model
```python
217 |     def offline(self) -> bool:
218 |         """Step 2: a local server that runs now, the downloaded model (or a download), or a
219 |         server at another address."""
220 |         found = self.scan(self.own_servers())
221 |         program, model_file = find_llama_server(self.config), find_model_file(self.config)
222 |         choices: list[tuple[str, Callable[[], bool]]] = []
223 |         for server, models in found:
224 |             listed = ", ".join(models[:3]) + (f" and {len(models) - 3} more" if len(models) > 3 else "")
225 |             choices.append((f"{server.title} at {server.base} ({listed or 'no model loaded yet'})",
226 |                             partial(self.use_server, server, models)))
227 |         if program is not None and model_file is not None:
228 |             choices.append((f"the downloaded model {model_file.name} (started when needed)",
229 |                             self.use_downloaded))
230 |         else:
231 |             memory = total_memory_gb()
232 |             pick = recommend(memory)
233 |             choices.append((f"download a free model for me: {pick.name}, {pick.size / GiB:.1f} GB "
234 |                             f"({pick.note})", partial(self.download, pick, memory)))
235 |         choices.append(("a model server at another address (you type it)", self.use_address))
236 |         ready = bool(running_local(self.config)) or can_start(self.config)
237 |         labels = [label for label, _ in choices]
238 |         labels.append("skip - keep what is set up now" if ready else "skip - no offline model")
239 |         choice = self.choose(labels, 1)
240 |         if choice is None or choice > len(choices):
241 |             self.say("  skipped" + ("" if ready else ": without internet nothing will answer"))
242 |             return ready
243 |         return choices[choice - 1][1]()
244 | 
```
- Scans for running local servers (your own addresses first) and offers each with up to three of its models. Then the
  downloaded model — or, when llama.cpp or the model is missing, "download a free model for me" with the model this
  computer's memory suits. Then "another address", then "skip". Enter takes the first: what you already run beats a
  download.
- `partial(self.use_server, server, models)` makes one menu action per server without the lambda-in-a-loop trap (all
  lambdas would see the last server).

```python
245 |     def own_servers(self) -> tuple[LocalServer, ...]:
246 |         """Addresses you set yourself (OLLAMA_HOST, LOCALAGENT_API_BASE), looked at first."""
247 |         own = []
248 |         if self.config.host != Config.host:
249 |             own.append(LocalServer("Ollama", self.config.host, "ollama"))
250 |         if self.config.api_base != Config.api_base:
251 |             own.append(LocalServer("your model server", self.config.api_base, "openai"))
252 |         return tuple(own)
253 | 
```
- `OLLAMA_HOST` or `LOCALAGENT_API_BASE` set to something other than the default are looked at first: you told the
  agent about them.

```python
254 |     def use_server(self, server: LocalServer, models: list[str]) -> bool:
255 |         """Use a model server that runs on this computer, or at an address you gave."""
256 |         suggestion = pick_local_model(models)
257 |         if models:
258 |             more = " ..." if len(models) > SHOWN_MODELS else ""
259 |             self.say(f"  Models there: {', '.join(models[:SHOWN_MODELS])}{more}")
260 |         else:
261 |             how = "ollama pull qwen3:8b" if server.kind == "ollama" else "load one in it first"
262 |             self.say(f"  note  {server.title} has no model yet ({how})")
263 |         typed = self.answer(f"  Model [{suggestion}]: ")
264 |         model = (typed or suggestion) if typed is not None else ""
265 |         if not model:
266 |             self.say("  skipped: no model chosen")
267 |             return False
268 |         if server.kind == "ollama":
269 |             self.save("LOCALAGENT_LOCAL_SERVER", "ollama")
270 |             self.save("OLLAMA_HOST", None if server.base == Config.host else server.base)
271 |             self.save("LOCALAGENT_API_BASE", None)
272 |         else:
273 |             self.save("LOCALAGENT_LOCAL_SERVER", "openai")
274 |             self.save("LOCALAGENT_API_BASE", None if server.base == Config.api_base else server.base)
275 |         self.save("LOCALAGENT_MODEL", model)
276 |         self.say(f"  ok    offline model: {model} on {server.title} at {server.base}")
277 |         return True
278 | 
```
- Lists the server's models and suggests one (`pick_local_model`); a server without models says how to get one
  (`ollama pull qwen3:8b`). Saves the kind, the model and — only when it is not the default — the address. Choosing
  Ollama forgets a saved `LOCALAGENT_API_BASE`, so an old address cannot be asked first.

```python
279 |     def use_downloaded(self) -> bool:
280 |         """Use the model downloaded by setup, started with llama.cpp when it is needed."""
281 |         self.save("LOCALAGENT_LOCAL_SERVER", "openai")
282 |         self.save("LOCALAGENT_API_BASE", None)
283 |         self.save("LOCALAGENT_MODEL", None)
284 |         model = find_model_file(self.config)
285 |         name = model.name if model is not None else "the downloaded model"
286 |         self.say(f"  ok    offline model: {name} with llama.cpp (started when needed)")
287 |         return True
288 | 
```
- The model this program downloads and starts itself: kind `openai` at the default address; an earlier server's
  address and model name are forgotten, or they would point somewhere else.

```python
289 |     def download(self, pick: CatalogModel, memory: float | None) -> bool:
290 |         """Fetch llama.cpp and the model suggested for this computer (each asked first)."""
291 |         size = f"{memory:.1f} GB of memory" if memory else "an unknown amount of memory"
292 |         self.say(f"  This computer has {size}: the suggested model is {pick.name} "
293 |                  f"({pick.size / GiB:.1f} GB download) - {pick.note}")
294 |         program = find_llama_server(self.config)
295 |         if program is not None:
296 |             self.say(f"  ok    llama.cpp found: {program}")
297 |         elif self.yes("  llama.cpp, the program that runs the model (25-40 MB), is missing. "
298 |                       "Download it? [Y/n] "):
299 |             try:
300 |                 self.say(f"  ok    llama.cpp installed in {install_llama(self.progress)}")
301 |                 program = find_llama_server(self.config)
302 |             except DownloadError as exc:
303 |                 self.say(f"  FAIL  {exc}")
304 |         model = find_model_file(self.config)
305 |         if model is not None:
306 |             self.say(f"  ok    model found: {model}")
307 |         elif self.yes(f"  Download {pick.name} ({pick.size / GiB:.1f} GB; it can be stopped and "
308 |                       "continued later)? [Y/n] "):
309 |             try:
310 |                 model = install_model(pick, self.progress)
311 |                 self.say(f"  ok    model saved: {model}")
312 |             except DownloadError as exc:
313 |                 self.say(f"  FAIL  {exc}")
314 |         return program is not None and model is not None and self.use_downloaded()
315 | 
```
- Shows the memory and the suggested model, then for llama.cpp and for the model: found → ok; missing → asks (Enter =
  yes) and downloads with progress. A failed download is reported and setup carries on. When both are there, they are
  used.

```python
316 |     def use_address(self) -> bool:
317 |         """A model server at an address you type (another computer, another port)."""
318 |         typed = self.answer("  Its address, e.g. http://192.168.1.20:8080/v1 (for Ollama: "
319 |                             "http://host:11434): ")
320 |         if not typed:
321 |             self.say("  skipped: no address given")
322 |             return False
323 |         base = typed.rstrip("/") if "://" in typed else "http://" + typed.rstrip("/")
324 |         ollama = LocalServer("Ollama", base.removesuffix("/v1"), "ollama")
325 |         models = server_models(ollama, timeout=5)
326 |         if models is not None:                          # Ollama's own API sets the context size
327 |             return self.use_server(ollama, models)
328 |         server = LocalServer("the model server", base, "openai")
329 |         models = server_models(server, timeout=5)
330 |         if models is not None:
331 |             return self.use_server(server, models)
332 |         self.say(f"  FAIL  nothing answers at {base} (start the server first, or check the address)")
333 |         return False
334 | 
335 |     # ---- step 3: sandbox --------------------------------------------------------------
```
- Asks for an address; `host:port` alone gets `http://` in front. It first asks it as Ollama (without a trailing
  `/v1`): Ollama's own API sets the context size, its OpenAI-style one cannot. Otherwise as an OpenAI-style server. When
  nothing answers, it says so and saves nothing.

## Step 3: the sandbox
```python
336 |     def sandbox(self) -> None:
337 |         """Step 3: say whether Docker and the sandbox image are ready (nothing is changed)."""
338 |         if not docker_available():
339 |             self.say("  note  Docker is not running. Install Docker Desktop (docker.com) and start "
340 |                      "it - or work without it: code then runs on this computer, after your approval.")
341 |         elif sandbox_image_ready(self.config.sandbox_image):
342 |             self.say("  ok    Docker and the sandbox image are ready")
343 |         else:
344 |             self.say(f"  note  Docker runs; build the sandbox image once with:\n        {sandbox_hint()}")
```
- Only reports: Docker not running (and that the agent works without it), ready, or running without the sandbox image
  (with the exact build command).
