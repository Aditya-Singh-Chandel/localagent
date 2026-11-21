# `localagent/localserver.py` — the offline model

**Role in the flow:** everything the agent needs to work **without internet**. It finds a local model that is already
running, or finds llama.cpp's server program and a model file and starts them, waits until the model is loaded, and
answers through the same `chat` method as every other backend. `/model offline` uses it as the only model; in `auto`
mode it is the last link of the Gemini chain, so a lost connection or a used-up quota no longer stops your work.

```
LocalModel.chat ─► ensure_ready ─► a server answers at api_base (llama.cpp, LM Studio)? ──► use it ("openai")
                                 ├► Ollama answers at host? ─────────────────────────────► use it ("ollama")
                                 ├► llama-server + a .gguf model found? ─► LlamaServer.start ─► wait for /health
                                 └► nothing: LLMError (final) saying what to install
                ─► fit_history (8K window) ─► client.chat ─► Reply
```

**Where the files are looked for** (first match wins):

| What | 1 | 2 | 3 | 4 |
|---|---|---|---|---|
| server program | `LOCALAGENT_LLAMA_SERVER` | `%USERPROFILE%\.localagent\llama\` (any sub-folder, GPU builds first) | this checkout's `bin\llamacpp-vulkan\`, then `bin\llamacpp\` | `llama-server` on the PATH |
| model | `LOCALAGENT_MODEL_FILE` | first `*.gguf` in `%USERPROFILE%\.localagent\models\` | first `*.gguf` in this checkout's `models\` | — |

The `%USERPROFILE%\.localagent` places are what an installed copy uses; the checkout folders are what this project
already had (`scripts\serve.ps1` uses them), so nothing had to move.

## Module header
```python
  1 | """The offline model: find, start and stop a llama.cpp server, and talk to whichever local
  2 | model server is running (llama.cpp, LM Studio or Ollama).
  3 | 
  4 | Offline work needs two files, the llama.cpp server program and a model, looked for in order:
  5 |   program: $LOCALAGENT_LLAMA_SERVER, ~/.localagent/llama/ (any sub-folder, GPU builds first),
  6 |            this checkout's bin/llamacpp-vulkan and bin/llamacpp folders, then the PATH
  7 |   model:   $LOCALAGENT_MODEL_FILE, the first *.gguf in ~/.localagent/models/, then in this
  8 |            checkout's models/ folder
  9 | A server is started only when the local model is really needed; loading a model takes
 10 | 10-60 s. It listens on 127.0.0.1 only (nothing outside this computer can use it), writes its
 11 | output to ~/.localagent/llama-server.log, and is stopped when the agent ends - on Windows even
 12 | when the terminal window is simply closed.
 13 | """
 14 | from __future__ import annotations
 15 | 
 16 | import atexit
 17 | import ctypes
 18 | import json
 19 | import os
 20 | import shutil
 21 | import subprocess
 22 | import sys
 23 | import time
 24 | import urllib.error
 25 | import urllib.request
 26 | from dataclasses import replace
 27 | from pathlib import Path
 28 | from typing import Callable
 29 | from urllib.parse import urlsplit
 30 | 
 31 | from .config import Config
 32 | from .llm import ChatBackend, LLMError, OllamaClient, Reply, TokenSink, fit_history
 33 | from .openai_compat import OpenAICompatClient
 34 | 
 35 | HOME = Path.home() / ".localagent"
 36 | CHECKOUT = Path(__file__).resolve().parent.parent       # the source folder, when run from it
 37 | PROGRAM = "llama-server.exe" if os.name == "nt" else "llama-server"
 38 | LOG_FILE = HOME / "llama-server.log"
 39 | START_TIMEOUT = 300.0                                   # seconds a model may take to load
 40 | GPU_WORDS = ("cuda", "vulkan", "metal", "rocm", "hip", "sycl")
 41 | THIS_COMPUTER = frozenset({"127.0.0.1", "localhost", "::1"})
 42 | NO_LOCAL_MODEL = (
 43 |     "No local model is available. Offline work needs llama.cpp's llama-server program and a "
 44 |     ".gguf model file. The quickest way: run  localagent setup  (it downloads both, verified). "
 45 |     "By hand: put them in ~/.localagent/llama/ and ~/.localagent/models/ (or set "
 46 |     "LOCALAGENT_LLAMA_SERVER and LOCALAGENT_MODEL_FILE), or start Ollama or LM Studio.")
 47 | SETUP_HINT = "run: localagent setup"
 48 | 
 49 | 
```
- Imports: `atexit` (stop the server when Python ends), `ctypes` (the Windows job, below), `json`, `os`, `shutil`
  (`which`), `subprocess`, `sys`, `time`, `urllib` (health checks), `replace`, `Path`, `Callable`, `urlsplit`, and from
  the agent the config, `LLMError`, the two clients and `fit_history`.
- `HOME` is `%USERPROFILE%\.localagent`. `CHECKOUT` is the folder above the package — this project's folder when the agent
  runs from it. `PROGRAM` is the server's file name (`.exe` on Windows). `LOG_FILE` receives the server's output.
- `START_TIMEOUT`: a model may take up to 5 minutes to load from a slow disk. `GPU_WORDS` recognise GPU builds of
  llama.cpp by their folder or file name. `THIS_COMPUTER`: the only addresses for which a server may be started.
- `NO_LOCAL_MODEL` is the message when nothing local exists: what is needed, where to put it, and the guide to follow.

## Is something running?
```python
 50 | def _get(url: str, timeout: float = 1.0) -> int:
 51 |     """HTTP status of a quick GET request (0 when nothing answers)."""
 52 |     try:
 53 |         with urllib.request.urlopen(url, timeout=timeout) as response:
 54 |             return int(response.status)
 55 |     except urllib.error.HTTPError as exc:
 56 |         exc.close()
 57 |         return exc.code
 58 |     except (OSError, ValueError):
 59 |         return 0
 60 | 
 61 | 
```
- A quick GET request (1 second), returning the HTTP status — also for error statuses — or 0 when nothing answers.

```python
 62 | def server_up(api_base: str) -> bool:
 63 |     """True when an OpenAI-style model server answers at ``api_base`` within a second."""
 64 |     return _get(api_base + "/models") == 200
 65 | 
 66 | 
```
```python
 67 | def ollama_up(host: str) -> bool:
 68 |     """True when Ollama answers at ``host`` within a second."""
 69 |     return _get(host + "/api/tags") == 200
 70 | 
 71 | 
```
```python
 72 | def running_local(config: Config) -> str:
 73 |     """Which local model server answers now: "openai" (llama.cpp, LM Studio...), "ollama" or "".
 74 |     With ``local_server`` set, only that kind counts."""
 75 |     if config.local_server in ("", "openai") and server_up(config.api_base):
 76 |         return "openai"
 77 |     if config.local_server in ("", "ollama") and ollama_up(config.host):
 78 |         return "ollama"
 79 |     return ""
 80 | 
 81 | 
```
- `server_up`: an OpenAI-style server answers `/models` (llama.cpp, LM Studio, vLLM). `ollama_up`: Ollama answers
  `/api/tags`. `running_local` asks both, the OpenAI-style address first. (`server_up` used to live in `cli.py`; the CLI
  now imports it from here.) With `local_server` set (`localagent setup` sets it), only that kind is asked — a chosen
  Ollama is not replaced by some other server that happens to answer.

## Finding the files
```python
 82 | def _gpu_build(path: Path) -> bool:
 83 |     """True for a llama.cpp build that can use the graphics card."""
 84 |     return any(word in str(path).lower() for word in GPU_WORDS)
 85 | 
 86 | 
```
- True for a llama.cpp folder or file whose name mentions a graphics API (CUDA, Vulkan, Metal, ROCm, HIP, SYCL).

```python
 87 | def find_llama_server(config: Config) -> Path | None:
 88 |     """The llama.cpp server program, or None."""
 89 |     if config.llama_server:
 90 |         given = Path(config.llama_server).expanduser()
 91 |         return given if given.is_file() else None
 92 |     home = HOME / "llama"
 93 |     installed = sorted(home.rglob(PROGRAM), key=lambda p: (not _gpu_build(p), str(p))) \
 94 |         if home.is_dir() else []
 95 |     checkout = [CHECKOUT / "bin" / "llamacpp-vulkan" / PROGRAM, CHECKOUT / "bin" / "llamacpp" / PROGRAM]
 96 |     for path in installed + checkout:
 97 |         if path.is_file():
 98 |             return path
 99 |     on_path = shutil.which("llama-server")
100 |     return Path(on_path) if on_path else None
101 | 
102 | 
```
- An explicitly configured path wins — and if it does not exist the answer is "not found" rather than silently using
  another program. Otherwise: installed copies under `%USERPROFILE%\.localagent\llama` (searched in all sub-folders,
  GPU builds before CPU builds), then this checkout's Vulkan and CPU builds, then the PATH.

```python
103 | def find_model_file(config: Config) -> Path | None:
104 |     """The local model, a .gguf file (a vision add-on "mmproj" file is never chosen), or None."""
105 |     if config.model_file:
106 |         given = Path(config.model_file).expanduser()
107 |         return given if given.is_file() else None
108 |     for folder in (HOME / "models", CHECKOUT / "models"):
109 |         if folder.is_dir():
110 |             models = sorted(p for p in folder.glob("*.gguf") if not p.name.lower().startswith("mmproj"))
111 |             if models:
112 |                 return models[0]
113 |     return None
114 | 
115 | 
```
- An explicitly configured file wins. Otherwise the first `.gguf` (alphabetically) in `%USERPROFILE%\.localagent\models`,
  then in this checkout's `models`. `mmproj-*.gguf` files are vision add-ons, not models, and are never chosen. A model
  split into parts (`...-00001-of-00003.gguf`) sorts with its first part first, which is the one llama.cpp wants.

```python
116 | def can_start(config: Config) -> bool:
117 |     """True when a llama.cpp server could be started on this computer for ``config.api_base``."""
118 |     return ((urlsplit(config.api_base).hostname or "") in THIS_COMPUTER
119 |             and find_llama_server(config) is not None and find_model_file(config) is not None)
120 | 
121 | 
```
- A server can be started only for an address on **this computer** and only when both files exist. (A container whose
  `api_base` points at `host.docker.internal` cannot start a program on the host.)

```python
122 | def describe_local(config: Config) -> str:
123 |     """One line for /model and --doctor: which local model would answer, and its state."""
124 |     kind = running_local(config)
125 |     if kind == "openai":
126 |         return f"the model server at {config.api_base} (running)"
127 |     if kind == "ollama":
128 |         return f"Ollama at {config.host}, model {config.model} (running)"
129 |     if config.local_server == "ollama":
130 |         return f"Ollama at {config.host}, model {config.model} (not running now: start Ollama)"
131 |     if config.local_server == "openai" and not can_start(config):
132 |         return f"the model server at {config.api_base} (not answering now)"
133 |     model = find_model_file(config)
134 |     if can_start(config) and model is not None:
135 |         return f"{model.name} with llama.cpp (started when needed)"
136 |     return f"none set up ({SETUP_HINT})"
137 | 
138 | 
```
- One line for `/model`, `--doctor` and the start-up notes: the running server, or the model that would be started, or
  "none" with the guide to follow. A chosen server that is down is named as such: Ollama "not running now: start
  Ollama", or the server at your address "not answering now" (when no model could be started there instead).

## Never leaving a 5 GB model behind
```python
139 | _JOBS: list[int] = []                                   # Windows job handles kept open (see below)
140 | 
141 | 
```
```python
142 | def _end_with_this_program(process: subprocess.Popen) -> None:
143 |     """Windows: put ``process`` in a job that Windows closes when this program ends, however it
144 |     ends. Closing the terminal window skips Python's atexit, so without the job a 5 GB model
145 |     would stay in memory. (Elsewhere atexit is relied on.)"""
146 |     if sys.platform != "win32":
147 |         return
148 |     from ctypes import wintypes
149 | 
150 |     class Basic(ctypes.Structure):
151 |         _fields_ = [("user_time", ctypes.c_int64), ("job_time", ctypes.c_int64),
152 |                     ("flags", wintypes.DWORD), ("min_set", ctypes.c_size_t),
153 |                     ("max_set", ctypes.c_size_t), ("processes", wintypes.DWORD),
154 |                     ("affinity", ctypes.c_size_t), ("priority", wintypes.DWORD),
155 |                     ("scheduling", wintypes.DWORD)]
156 | 
157 |     class Extended(ctypes.Structure):
158 |         _fields_ = [("basic", Basic), ("io", ctypes.c_ulonglong * 6),
159 |                     ("process_memory", ctypes.c_size_t), ("job_memory", ctypes.c_size_t),
160 |                     ("peak_process", ctypes.c_size_t), ("peak_job", ctypes.c_size_t)]
161 | 
162 |     kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
163 |     kernel32.CreateJobObjectW.restype = wintypes.HANDLE
164 |     kernel32.OpenProcess.restype = wintypes.HANDLE
165 |     kernel32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
166 |                                                  wintypes.DWORD]
167 |     kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
168 |     kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
169 |     job = kernel32.CreateJobObjectW(None, None)
170 |     limits = Extended()
171 |     limits.basic.flags = 0x2000                         # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
172 |     handle = kernel32.OpenProcess(0x0101, False, process.pid)   # SET_QUOTA | TERMINATE
173 |     if job and handle and kernel32.SetInformationJobObject(job, 9, ctypes.byref(limits),
174 |                                                            ctypes.sizeof(limits)):
175 |         if kernel32.AssignProcessToJobObject(job, handle):
176 |             _JOBS.append(job)                           # open until this program ends
177 |     if handle:
178 |         kernel32.CloseHandle(handle)                    # the job keeps its own reference
179 | 
180 | 
```
- `atexit` stops the server when Python ends normally. But closing the terminal window ends Python *without* running
  `atexit`, and the model would stay in memory (about 5 GB on this laptop) until the next restart.
- On Windows a **job object** solves this: a group of processes that Windows itself ends when the last handle to the
  group closes — and this program's handles close however it ends. The function describes the two Windows structures
  it needs with `ctypes` (only the `flags` field matters: `0x2000` = `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`), creates a
  job, gets a handle to the server process (rights `0x0101` = set quota + terminate), sets the flag (information class 9
  = extended limits) and assigns the process. The job handle is kept in `_JOBS` for the rest of the run; the process
  handle is closed again (the job holds its own).
- Every Windows call is checked and nothing fails loudly: at worst the server is stopped by `atexit` only, as before.
  On Linux and macOS the function does nothing.

```python
181 | def _log_tail(lines: int = 8) -> str:
182 |     """The last lines of the server's log, to explain why it stopped."""
183 |     try:
184 |         text = LOG_FILE.read_text(encoding="utf-8", errors="replace")
185 |     except OSError:
186 |         return "(no log)"
187 |     return "\n".join(text.strip().splitlines()[-lines:])
188 | 
189 | 
```
- The last eight lines of the server's log — llama.cpp explains there why it stopped (a missing file, too little
  memory).

## `LlamaServer`
```python
190 | class LlamaServer:
191 |     """A llama.cpp server process started by this program (and stopped when it ends)."""
192 | 
```
```python
193 |     def __init__(self, program: Path, model: Path, api_base: str, context: int,
194 |                  gpu_layers: int = -1, threads: int = 0) -> None:
195 |         self.program, self.model, self.api_base = program, model, api_base.rstrip("/")
196 |         self.context, self.gpu_layers, self.threads = context, gpu_layers, threads
197 |         self.alias = model.stem                         # the model's name in the server's API
198 |         self.process: subprocess.Popen | None = None
199 | 
```
- Remembers the program, the model, the address, the context size, GPU layers and threads. `alias` is the model's
  name in the server's API (the file name without `.gguf`).

```python
200 |     def argv(self) -> list[str]:
201 |         """Its command line: the model, this computer only, the context size, tool calls."""
202 |         port = urlsplit(self.api_base).port or 8080
203 |         argv = [str(self.program), "-m", str(self.model), "--host", "127.0.0.1",
204 |                 "--port", str(port), "-c", str(self.context), "--jinja",
205 |                 "--reasoning-budget", "0", "--alias", self.alias]
206 |         if self.gpu_layers >= 0:                        # else llama.cpp fits the GPU itself
207 |             argv += ["-ngl", str(self.gpu_layers)]
208 |         if self.threads > 0:
209 |             argv += ["-t", str(self.threads)]
210 |         return argv
211 | 
```
- The command line: the model; `--host 127.0.0.1` (only this computer can connect); the port from `api_base`; `-c` the
  context size; `--jinja` (use the model's own chat template — needed for tool calls); `--reasoning-budget 0` (Qwen3
  and similar models otherwise "think" for hundreds of tokens before every answer — measured on this laptop: 3.5 minutes
  for one call); `--alias`.
- `-ngl` (layers on the GPU) is passed **only** when you set `LOCALAGENT_GPU_LAYERS`. Otherwise llama.cpp's own `--fit`
  (on by default since build 12099, the one in `bin\`) measures the graphics card's free memory and puts as many layers
  there as fit. `scripts\serve.ps1`'s hand-tuned `-ngl 9` was right for this laptop's 2 GB card but would waste a bigger card
  and crash a smaller one; a downloadable product cannot know the card in advance.
- Verified against the real `llama-server.exe`: it accepts every argument above (started with a missing model file,
  it failed with "failed to load model", not with an argument error).

```python
212 |     def running(self) -> bool:
213 |         """True while the process this program started is alive."""
214 |         return self.process is not None and self.process.poll() is None
215 | 
```
```python
216 |     def ready(self) -> bool:
217 |         """True once the model is loaded (llama.cpp's /health says 200; 503 while loading)."""
218 |         root = self.api_base[:-3] if self.api_base.endswith("/v1") else self.api_base
219 |         return _get(root + "/health") == 200
220 | 
```
- `running`: the process this program started is still alive. `ready`: the model is loaded — llama.cpp's `/health`
  answers 503 while loading and 200 when ready (at the server's root, so a trailing `/v1` is removed first).

```python
221 |     def start(self, on_notice: Callable[[str], None]) -> None:
222 |         """Start the server unless it already runs, then wait until the model is loaded."""
223 |         if not self.running():
224 |             LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
225 |             options: dict = {}
226 |             if sys.platform == "win32":                 # no window; Ctrl+C stays with the agent
227 |                 options["creationflags"] = subprocess.CREATE_NO_WINDOW
228 |             else:
229 |                 options["start_new_session"] = True
230 |             with LOG_FILE.open("wb") as log:
231 |                 self.process = subprocess.Popen(self.argv(), stdin=subprocess.DEVNULL, stdout=log,
232 |                                                 stderr=subprocess.STDOUT, cwd=self.program.parent,
233 |                                                 **options)
234 |             _end_with_this_program(self.process)
235 |             atexit.register(self.stop)
236 |             on_notice(f"starting the local model {self.model.name} "
237 |                       f"(loading takes 10-60 s; log: {LOG_FILE})")
238 |         started = time.monotonic()
239 |         while not self.ready():
240 |             if self.process is not None and self.process.poll() is not None:
241 |                 code, self.process = self.process.returncode, None
242 |                 raise LLMError(f"the local model server stopped while starting (exit code {code}):"
243 |                                f"\n{_log_tail()}", final=True)
244 |             if time.monotonic() - started > START_TIMEOUT:
245 |                 self.stop()
246 |                 raise LLMError(f"the local model did not load within {START_TIMEOUT:.0f}s; "
247 |                                f"see {LOG_FILE}", final=True)
248 |             time.sleep(0.5)
249 |         on_notice(f"local model ready: {self.alias}")
250 | 
```
- Starts the program unless it already runs (a second call — for example after Ctrl+C during loading — just waits
  again). On Windows `CREATE_NO_WINDOW` gives it no console window, which also means Ctrl+C in your terminal stops the
  agent's current task but not the model; elsewhere `start_new_session` does the same. Its output goes to the log file,
  it is tied to this program (`_end_with_this_program`, `atexit`), and a notice says that loading has begun.
- Then it checks `ready()` twice a second. If the process ends meanwhile, the error includes its exit code and the end
  of its log. After `START_TIMEOUT` the server is stopped and an error names the log. Both errors are `final`: retrying
  cannot help, so the model chain moves on at once.

```python
251 |     def stop(self) -> None:
252 |         """Stop the server (frees its memory); safe to call more than once."""
253 |         process, self.process = self.process, None
254 |         if process is not None and process.poll() is None:
255 |             process.terminate()
256 |             try:
257 |                 process.wait(timeout=10)
258 |             except subprocess.TimeoutExpired:
259 |                 process.kill()
260 |                 process.wait()
261 | 
262 | 
```
- Ends the process (politely, then by force after 10 seconds). Safe to call twice.

## One server per program
```python
263 | _SERVERS: dict[tuple[str, str, str], LlamaServer] = {}
264 | 
265 | 
```
```python
266 | def server_for(config: Config) -> LlamaServer:
267 |     """The llama.cpp server for this program, model and address (one per program run)."""
268 |     program, model = find_llama_server(config), find_model_file(config)
269 |     if program is None or model is None:
270 |         raise LLMError(NO_LOCAL_MODEL, final=True)
271 |     key = (str(program), str(model), config.api_base)
272 |     if key not in _SERVERS:
273 |         _SERVERS[key] = LlamaServer(program, model, config.api_base, config.local_ctx,
274 |                                     config.gpu_layers, config.local_threads)
275 |     return _SERVERS[key]
276 | 
277 | 
```
- `_SERVERS` holds the servers this program started, one per program + model + address, so switching modes back and
  forth (or many HTTP-server sessions) never starts a second copy of the model. `server_for` returns the existing one
  or creates it; without the files it raises the final `NO_LOCAL_MODEL` error.

```python
278 | def stop_local_servers() -> int:
279 |     """Stop every llama.cpp server this program started; returns how many were running."""
280 |     running = [server for server in _SERVERS.values() if server.running()]
281 |     for server in running:
282 |         server.stop()
283 |     return len(running)
284 | 
285 | 
```
- Stops every server this program started and returns how many were running. Used by `/model online` (to free the
  memory) and when the CLI or the HTTP server ends.

## `LocalModel` — the local model as a backend
```python
286 | class LocalModel:
287 |     """The local model as a ChatBackend.
288 | 
289 |     It uses the local server that is running (``kind`` "openai" or "ollama", or "" for
290 |     whichever answers), starts llama.cpp's when none is, and fits the conversation into the
291 |     local context window - a history built for Gemini's 32K budget would not fit 8K.
292 |     """
293 | 
```
```python
294 |     def __init__(self, config: Config, kind: str = "",
295 |                  on_notice: Callable[[str], None] = lambda _m: None) -> None:
296 |         self._config = config
297 |         self._kind = kind
298 |         self._notice = on_notice
299 |         self._client: ChatBackend | None = None
300 |         self._server: LlamaServer | None = None
301 |         self.last_model = ""                            # what answered (shown after a request)
302 | 
```
- `kind` is `"openai"` or `"ollama"` to insist on that kind of server (as `--fallback openai` does), or `""` to use
  whatever is available. `_client` is the real client once connected; `_server` is set when this program started
  llama.cpp. `last_model` says which local model answered (shown after each request).

```python
303 |     def ensure_ready(self) -> None:
304 |         """Connect to a running local server or start one; raises LLMError when impossible."""
305 |         if self._client is not None and (self._server is None or self._server.running()):
306 |             return
307 |         self._client, kind = None, self._kind
308 |         if kind in ("", "openai") and server_up(self._config.api_base):
309 |             kind = "openai"
310 |         elif kind in ("", "ollama") and ollama_up(self._config.host):
311 |             kind = "ollama"
312 |         elif kind in ("", "openai") and can_start(self._config):
313 |             self._server = server_for(self._config)
314 |             self._server.start(self._notice)
315 |             kind = "openai"
316 |         elif kind:
317 |             where = self._config.host if kind == "ollama" else self._config.api_base
318 |             raise LLMError(f"the local {kind} model server is not running at {where}", final=True)
319 |         else:
320 |             raise LLMError(NO_LOCAL_MODEL, final=True)
321 |         local = replace(self._config, num_ctx=self._config.local_ctx)
322 |         if self._server is not None:
323 |             local = replace(local, model=self._server.alias)
324 |         self._client = OllamaClient(local) if kind == "ollama" else OpenAICompatClient(local)
325 |         self.last_model = (f"ollama:{local.model}" if kind == "ollama"
326 |                            else f"local:{self._server.alias if self._server else local.model}")
327 | 
```
- Done already, unless the server this program started has died since (then everything is found again — and a dead
  server is simply restarted).
- Otherwise, in order: a server at `api_base`; Ollama at `host`; starting llama.cpp. A demanded kind that is not
  running gives a final error naming the address; nothing at all gives `NO_LOCAL_MODEL`.
- The client gets a copy of the config whose `num_ctx` is the **local** context, so Ollama is never asked for Gemini's
  32K window; with a server it started, the model name is the server's alias.
- `/model offline` calls this right away, so the model loads when you switch rather than in the middle of your next task.

```python
328 |     def chat(self, messages: list[dict], tools: list[dict],
329 |              on_token: TokenSink | None = None) -> Reply:
330 |         """Answer with the local model, the conversation shortened to fit its context."""
331 |         self.ensure_ready()
332 |         assert self._client is not None
333 |         budget = int(self._config.local_ctx * 0.7) * 4 - len(json.dumps(tools))
334 |         return self._client.chat(fit_history(messages, max(budget, 4000)), tools, on_token)
```
- Makes sure a server is ready, then sends the conversation **fitted to the local window**: 70 % of `local_ctx` tokens
  (×4 characters each) minus the size of the tool definitions, never less than 4 000 characters. This is what lets a
  conversation that grew with Gemini continue on an 8K local model when the internet goes away.
