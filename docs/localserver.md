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
already had (`serve.ps1` uses them), so nothing had to move.

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
 73 |     """Which local model server answers now: "openai" (llama.cpp, LM Studio...), "ollama" or ""."""
 74 |     if server_up(config.api_base):
 75 |         return "openai"
 76 |     if ollama_up(config.host):
 77 |         return "ollama"
 78 |     return ""
 79 | 
 80 | 
```
- `server_up`: an OpenAI-style server answers `/models` (llama.cpp, LM Studio, vLLM). `ollama_up`: Ollama answers
  `/api/tags`. `running_local` asks both, the OpenAI-style address first. (`server_up` used to live in `cli.py`; the CLI
  now imports it from here.)

## Finding the files
```python
 81 | def _gpu_build(path: Path) -> bool:
 82 |     """True for a llama.cpp build that can use the graphics card."""
 83 |     return any(word in str(path).lower() for word in GPU_WORDS)
 84 | 
 85 | 
```
- True for a llama.cpp folder or file whose name mentions a graphics API (CUDA, Vulkan, Metal, ROCm, HIP, SYCL).

```python
 86 | def find_llama_server(config: Config) -> Path | None:
 87 |     """The llama.cpp server program, or None."""
 88 |     if config.llama_server:
 89 |         given = Path(config.llama_server).expanduser()
 90 |         return given if given.is_file() else None
 91 |     home = HOME / "llama"
 92 |     installed = sorted(home.rglob(PROGRAM), key=lambda p: (not _gpu_build(p), str(p))) \
 93 |         if home.is_dir() else []
 94 |     checkout = [CHECKOUT / "bin" / "llamacpp-vulkan" / PROGRAM, CHECKOUT / "bin" / "llamacpp" / PROGRAM]
 95 |     for path in installed + checkout:
 96 |         if path.is_file():
 97 |             return path
 98 |     on_path = shutil.which("llama-server")
 99 |     return Path(on_path) if on_path else None
100 | 
101 | 
```
- An explicitly configured path wins — and if it does not exist the answer is "not found" rather than silently using
  another program. Otherwise: installed copies under `%USERPROFILE%\.localagent\llama` (searched in all sub-folders,
  GPU builds before CPU builds), then this checkout's Vulkan and CPU builds, then the PATH.

```python
102 | def find_model_file(config: Config) -> Path | None:
103 |     """The local model, a .gguf file (a vision add-on "mmproj" file is never chosen), or None."""
104 |     if config.model_file:
105 |         given = Path(config.model_file).expanduser()
106 |         return given if given.is_file() else None
107 |     for folder in (HOME / "models", CHECKOUT / "models"):
108 |         if folder.is_dir():
109 |             models = sorted(p for p in folder.glob("*.gguf") if not p.name.lower().startswith("mmproj"))
110 |             if models:
111 |                 return models[0]
112 |     return None
113 | 
114 | 
```
- An explicitly configured file wins. Otherwise the first `.gguf` (alphabetically) in `%USERPROFILE%\.localagent\models`,
  then in this checkout's `models`. `mmproj-*.gguf` files are vision add-ons, not models, and are never chosen. A model
  split into parts (`...-00001-of-00003.gguf`) sorts with its first part first, which is the one llama.cpp wants.

```python
115 | def can_start(config: Config) -> bool:
116 |     """True when a llama.cpp server could be started on this computer for ``config.api_base``."""
117 |     return ((urlsplit(config.api_base).hostname or "") in THIS_COMPUTER
118 |             and find_llama_server(config) is not None and find_model_file(config) is not None)
119 | 
120 | 
```
- A server can be started only for an address on **this computer** and only when both files exist. (A container whose
  `api_base` points at `host.docker.internal` cannot start a program on the host.)

```python
121 | def describe_local(config: Config) -> str:
122 |     """One line for /model and --doctor: which local model would answer, and its state."""
123 |     kind = running_local(config)
124 |     if kind == "openai":
125 |         return f"the model server at {config.api_base} (running)"
126 |     if kind == "ollama":
127 |         return f"Ollama at {config.host}, model {config.model} (running)"
128 |     model = find_model_file(config)
129 |     if can_start(config) and model is not None:
130 |         return f"{model.name} with llama.cpp (started when needed)"
131 |     return f"none set up ({SETUP_HINT})"
132 | 
133 | 
```
- One line for `/model`, `--doctor` and the start-up notes: the running server, or the model that would be started, or
  "none" with the guide to follow.

## Never leaving a 5 GB model behind
```python
134 | _JOBS: list[int] = []                                   # Windows job handles kept open (see below)
135 | 
136 | 
```
```python
137 | def _end_with_this_program(process: subprocess.Popen) -> None:
138 |     """Windows: put ``process`` in a job that Windows closes when this program ends, however it
139 |     ends. Closing the terminal window skips Python's atexit, so without the job a 5 GB model
140 |     would stay in memory. (Elsewhere atexit is relied on.)"""
141 |     if sys.platform != "win32":
142 |         return
143 |     from ctypes import wintypes
144 | 
145 |     class Basic(ctypes.Structure):
146 |         _fields_ = [("user_time", ctypes.c_int64), ("job_time", ctypes.c_int64),
147 |                     ("flags", wintypes.DWORD), ("min_set", ctypes.c_size_t),
148 |                     ("max_set", ctypes.c_size_t), ("processes", wintypes.DWORD),
149 |                     ("affinity", ctypes.c_size_t), ("priority", wintypes.DWORD),
150 |                     ("scheduling", wintypes.DWORD)]
151 | 
152 |     class Extended(ctypes.Structure):
153 |         _fields_ = [("basic", Basic), ("io", ctypes.c_ulonglong * 6),
154 |                     ("process_memory", ctypes.c_size_t), ("job_memory", ctypes.c_size_t),
155 |                     ("peak_process", ctypes.c_size_t), ("peak_job", ctypes.c_size_t)]
156 | 
157 |     kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
158 |     kernel32.CreateJobObjectW.restype = wintypes.HANDLE
159 |     kernel32.OpenProcess.restype = wintypes.HANDLE
160 |     kernel32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
161 |                                                  wintypes.DWORD]
162 |     kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
163 |     kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
164 |     job = kernel32.CreateJobObjectW(None, None)
165 |     limits = Extended()
166 |     limits.basic.flags = 0x2000                         # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
167 |     handle = kernel32.OpenProcess(0x0101, False, process.pid)   # SET_QUOTA | TERMINATE
168 |     if job and handle and kernel32.SetInformationJobObject(job, 9, ctypes.byref(limits),
169 |                                                            ctypes.sizeof(limits)):
170 |         if kernel32.AssignProcessToJobObject(job, handle):
171 |             _JOBS.append(job)                           # open until this program ends
172 |     if handle:
173 |         kernel32.CloseHandle(handle)                    # the job keeps its own reference
174 | 
175 | 
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
176 | def _log_tail(lines: int = 8) -> str:
177 |     """The last lines of the server's log, to explain why it stopped."""
178 |     try:
179 |         text = LOG_FILE.read_text(encoding="utf-8", errors="replace")
180 |     except OSError:
181 |         return "(no log)"
182 |     return "\n".join(text.strip().splitlines()[-lines:])
183 | 
184 | 
```
- The last eight lines of the server's log — llama.cpp explains there why it stopped (a missing file, too little
  memory).

## `LlamaServer`
```python
185 | class LlamaServer:
186 |     """A llama.cpp server process started by this program (and stopped when it ends)."""
187 | 
```
```python
188 |     def __init__(self, program: Path, model: Path, api_base: str, context: int,
189 |                  gpu_layers: int = -1, threads: int = 0) -> None:
190 |         self.program, self.model, self.api_base = program, model, api_base.rstrip("/")
191 |         self.context, self.gpu_layers, self.threads = context, gpu_layers, threads
192 |         self.alias = model.stem                         # the model's name in the server's API
193 |         self.process: subprocess.Popen | None = None
194 | 
```
- Remembers the program, the model, the address, the context size, GPU layers and threads. `alias` is the model's
  name in the server's API (the file name without `.gguf`).

```python
195 |     def argv(self) -> list[str]:
196 |         """Its command line: the model, this computer only, the context size, tool calls."""
197 |         port = urlsplit(self.api_base).port or 8080
198 |         argv = [str(self.program), "-m", str(self.model), "--host", "127.0.0.1",
199 |                 "--port", str(port), "-c", str(self.context), "--jinja",
200 |                 "--reasoning-budget", "0", "--alias", self.alias]
201 |         if self.gpu_layers >= 0:                        # else llama.cpp fits the GPU itself
202 |             argv += ["-ngl", str(self.gpu_layers)]
203 |         if self.threads > 0:
204 |             argv += ["-t", str(self.threads)]
205 |         return argv
206 | 
```
- The command line: the model; `--host 127.0.0.1` (only this computer can connect); the port from `api_base`; `-c` the
  context size; `--jinja` (use the model's own chat template — needed for tool calls); `--reasoning-budget 0` (Qwen3
  and similar models otherwise "think" for hundreds of tokens before every answer — measured on this laptop: 3.5 minutes
  for one call); `--alias`.
- `-ngl` (layers on the GPU) is passed **only** when you set `LOCALAGENT_GPU_LAYERS`. Otherwise llama.cpp's own `--fit`
  (on by default since build 12099, the one in `bin\`) measures the graphics card's free memory and puts as many layers
  there as fit. `serve.ps1`'s hand-tuned `-ngl 9` was right for this laptop's 2 GB card but would waste a bigger card
  and crash a smaller one; a downloadable product cannot know the card in advance.
- Verified against the real `llama-server.exe`: it accepts every argument above (started with a missing model file,
  it failed with "failed to load model", not with an argument error).

```python
207 |     def running(self) -> bool:
208 |         """True while the process this program started is alive."""
209 |         return self.process is not None and self.process.poll() is None
210 | 
```
```python
211 |     def ready(self) -> bool:
212 |         """True once the model is loaded (llama.cpp's /health says 200; 503 while loading)."""
213 |         root = self.api_base[:-3] if self.api_base.endswith("/v1") else self.api_base
214 |         return _get(root + "/health") == 200
215 | 
```
- `running`: the process this program started is still alive. `ready`: the model is loaded — llama.cpp's `/health`
  answers 503 while loading and 200 when ready (at the server's root, so a trailing `/v1` is removed first).

```python
216 |     def start(self, on_notice: Callable[[str], None]) -> None:
217 |         """Start the server unless it already runs, then wait until the model is loaded."""
218 |         if not self.running():
219 |             LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
220 |             options: dict = {}
221 |             if sys.platform == "win32":                 # no window; Ctrl+C stays with the agent
222 |                 options["creationflags"] = subprocess.CREATE_NO_WINDOW
223 |             else:
224 |                 options["start_new_session"] = True
225 |             with LOG_FILE.open("wb") as log:
226 |                 self.process = subprocess.Popen(self.argv(), stdin=subprocess.DEVNULL, stdout=log,
227 |                                                 stderr=subprocess.STDOUT, cwd=self.program.parent,
228 |                                                 **options)
229 |             _end_with_this_program(self.process)
230 |             atexit.register(self.stop)
231 |             on_notice(f"starting the local model {self.model.name} "
232 |                       f"(loading takes 10-60 s; log: {LOG_FILE})")
233 |         started = time.monotonic()
234 |         while not self.ready():
235 |             if self.process is not None and self.process.poll() is not None:
236 |                 code, self.process = self.process.returncode, None
237 |                 raise LLMError(f"the local model server stopped while starting (exit code {code}):"
238 |                                f"\n{_log_tail()}", final=True)
239 |             if time.monotonic() - started > START_TIMEOUT:
240 |                 self.stop()
241 |                 raise LLMError(f"the local model did not load within {START_TIMEOUT:.0f}s; "
242 |                                f"see {LOG_FILE}", final=True)
243 |             time.sleep(0.5)
244 |         on_notice(f"local model ready: {self.alias}")
245 | 
```
- Starts the program unless it already runs (a second call — for example after Ctrl+C during loading — just waits
  again). On Windows `CREATE_NO_WINDOW` gives it no console window, which also means Ctrl+C in your terminal stops the
  agent's current task but not the model; elsewhere `start_new_session` does the same. Its output goes to the log file,
  it is tied to this program (`_end_with_this_program`, `atexit`), and a notice says that loading has begun.
- Then it checks `ready()` twice a second. If the process ends meanwhile, the error includes its exit code and the end
  of its log. After `START_TIMEOUT` the server is stopped and an error names the log. Both errors are `final`: retrying
  cannot help, so the model chain moves on at once.

```python
246 |     def stop(self) -> None:
247 |         """Stop the server (frees its memory); safe to call more than once."""
248 |         process, self.process = self.process, None
249 |         if process is not None and process.poll() is None:
250 |             process.terminate()
251 |             try:
252 |                 process.wait(timeout=10)
253 |             except subprocess.TimeoutExpired:
254 |                 process.kill()
255 |                 process.wait()
256 | 
257 | 
```
- Ends the process (politely, then by force after 10 seconds). Safe to call twice.

## One server per program
```python
258 | _SERVERS: dict[tuple[str, str, str], LlamaServer] = {}
259 | 
260 | 
```
```python
261 | def server_for(config: Config) -> LlamaServer:
262 |     """The llama.cpp server for this program, model and address (one per program run)."""
263 |     program, model = find_llama_server(config), find_model_file(config)
264 |     if program is None or model is None:
265 |         raise LLMError(NO_LOCAL_MODEL, final=True)
266 |     key = (str(program), str(model), config.api_base)
267 |     if key not in _SERVERS:
268 |         _SERVERS[key] = LlamaServer(program, model, config.api_base, config.local_ctx,
269 |                                     config.gpu_layers, config.local_threads)
270 |     return _SERVERS[key]
271 | 
272 | 
```
- `_SERVERS` holds the servers this program started, one per program + model + address, so switching modes back and
  forth (or many HTTP-server sessions) never starts a second copy of the model. `server_for` returns the existing one
  or creates it; without the files it raises the final `NO_LOCAL_MODEL` error.

```python
273 | def stop_local_servers() -> int:
274 |     """Stop every llama.cpp server this program started; returns how many were running."""
275 |     running = [server for server in _SERVERS.values() if server.running()]
276 |     for server in running:
277 |         server.stop()
278 |     return len(running)
279 | 
280 | 
```
- Stops every server this program started and returns how many were running. Used by `/model online` (to free the
  memory) and when the CLI or the HTTP server ends.

## `LocalModel` — the local model as a backend
```python
281 | class LocalModel:
282 |     """The local model as a ChatBackend.
283 | 
284 |     It uses the local server that is running (``kind`` "openai" or "ollama", or "" for
285 |     whichever answers), starts llama.cpp's when none is, and fits the conversation into the
286 |     local context window - a history built for Gemini's 32K budget would not fit 8K.
287 |     """
288 | 
```
```python
289 |     def __init__(self, config: Config, kind: str = "",
290 |                  on_notice: Callable[[str], None] = lambda _m: None) -> None:
291 |         self._config = config
292 |         self._kind = kind
293 |         self._notice = on_notice
294 |         self._client: ChatBackend | None = None
295 |         self._server: LlamaServer | None = None
296 |         self.last_model = ""                            # what answered (shown after a request)
297 | 
```
- `kind` is `"openai"` or `"ollama"` to insist on that kind of server (as `--fallback openai` does), or `""` to use
  whatever is available. `_client` is the real client once connected; `_server` is set when this program started
  llama.cpp. `last_model` says which local model answered (shown after each request).

```python
298 |     def ensure_ready(self) -> None:
299 |         """Connect to a running local server or start one; raises LLMError when impossible."""
300 |         if self._client is not None and (self._server is None or self._server.running()):
301 |             return
302 |         self._client, kind = None, self._kind
303 |         if kind in ("", "openai") and server_up(self._config.api_base):
304 |             kind = "openai"
305 |         elif kind in ("", "ollama") and ollama_up(self._config.host):
306 |             kind = "ollama"
307 |         elif kind in ("", "openai") and can_start(self._config):
308 |             self._server = server_for(self._config)
309 |             self._server.start(self._notice)
310 |             kind = "openai"
311 |         elif kind:
312 |             where = self._config.host if kind == "ollama" else self._config.api_base
313 |             raise LLMError(f"the local {kind} model server is not running at {where}", final=True)
314 |         else:
315 |             raise LLMError(NO_LOCAL_MODEL, final=True)
316 |         local = replace(self._config, num_ctx=self._config.local_ctx)
317 |         if self._server is not None:
318 |             local = replace(local, model=self._server.alias)
319 |         self._client = OllamaClient(local) if kind == "ollama" else OpenAICompatClient(local)
320 |         self.last_model = (f"ollama:{local.model}" if kind == "ollama"
321 |                            else f"local:{self._server.alias if self._server else local.model}")
322 | 
```
- Done already, unless the server this program started has died since (then everything is found again — and a dead
  server is simply restarted).
- Otherwise, in order: a server at `api_base`; Ollama at `host`; starting llama.cpp. A demanded kind that is not
  running gives a final error naming the address; nothing at all gives `NO_LOCAL_MODEL`.
- The client gets a copy of the config whose `num_ctx` is the **local** context, so Ollama is never asked for Gemini's
  32K window; with a server it started, the model name is the server's alias.
- `/model offline` calls this right away, so the model loads when you switch rather than in the middle of your next task.

```python
323 |     def chat(self, messages: list[dict], tools: list[dict],
324 |              on_token: TokenSink | None = None) -> Reply:
325 |         """Answer with the local model, the conversation shortened to fit its context."""
326 |         self.ensure_ready()
327 |         assert self._client is not None
328 |         budget = int(self._config.local_ctx * 0.7) * 4 - len(json.dumps(tools))
329 |         return self._client.chat(fit_history(messages, max(budget, 4000)), tools, on_token)
```
- Makes sure a server is ready, then sends the conversation **fitted to the local window**: 70 % of `local_ctx` tokens
  (×4 characters each) minus the size of the tool definitions, never less than 4 000 characters. This is what lets a
  conversation that grew with Gemini continue on an 8K local model when the internet goes away.
