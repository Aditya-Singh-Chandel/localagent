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
 44 |     ".gguf model file: put them in ~/.localagent/llama/ and ~/.localagent/models/ (or set "
 45 |     "LOCALAGENT_LLAMA_SERVER and LOCALAGENT_MODEL_FILE), or start Ollama or LM Studio. "
 46 |     "Step by step: docs/01-setup-and-models.md")
 47 | 
 48 | 
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
 49 | def _get(url: str, timeout: float = 1.0) -> int:
 50 |     """HTTP status of a quick GET request (0 when nothing answers)."""
 51 |     try:
 52 |         with urllib.request.urlopen(url, timeout=timeout) as response:
 53 |             return int(response.status)
 54 |     except urllib.error.HTTPError as exc:
 55 |         exc.close()
 56 |         return exc.code
 57 |     except (OSError, ValueError):
 58 |         return 0
 59 | 
 60 | 
```
- A quick GET request (1 second), returning the HTTP status — also for error statuses — or 0 when nothing answers.

```python
 61 | def server_up(api_base: str) -> bool:
 62 |     """True when an OpenAI-style model server answers at ``api_base`` within a second."""
 63 |     return _get(api_base + "/models") == 200
 64 | 
 65 | 
```
```python
 66 | def ollama_up(host: str) -> bool:
 67 |     """True when Ollama answers at ``host`` within a second."""
 68 |     return _get(host + "/api/tags") == 200
 69 | 
 70 | 
```
```python
 71 | def running_local(config: Config) -> str:
 72 |     """Which local model server answers now: "openai" (llama.cpp, LM Studio...), "ollama" or ""."""
 73 |     if server_up(config.api_base):
 74 |         return "openai"
 75 |     if ollama_up(config.host):
 76 |         return "ollama"
 77 |     return ""
 78 | 
 79 | 
```
- `server_up`: an OpenAI-style server answers `/models` (llama.cpp, LM Studio, vLLM). `ollama_up`: Ollama answers
  `/api/tags`. `running_local` asks both, the OpenAI-style address first. (`server_up` used to live in `cli.py`; the CLI
  now imports it from here.)

## Finding the files
```python
 80 | def _gpu_build(path: Path) -> bool:
 81 |     """True for a llama.cpp build that can use the graphics card."""
 82 |     return any(word in str(path).lower() for word in GPU_WORDS)
 83 | 
 84 | 
```
- True for a llama.cpp folder or file whose name mentions a graphics API (CUDA, Vulkan, Metal, ROCm, HIP, SYCL).

```python
 85 | def find_llama_server(config: Config) -> Path | None:
 86 |     """The llama.cpp server program, or None."""
 87 |     if config.llama_server:
 88 |         given = Path(config.llama_server).expanduser()
 89 |         return given if given.is_file() else None
 90 |     home = HOME / "llama"
 91 |     installed = sorted(home.rglob(PROGRAM), key=lambda p: (not _gpu_build(p), str(p))) \
 92 |         if home.is_dir() else []
 93 |     checkout = [CHECKOUT / "bin" / "llamacpp-vulkan" / PROGRAM, CHECKOUT / "bin" / "llamacpp" / PROGRAM]
 94 |     for path in installed + checkout:
 95 |         if path.is_file():
 96 |             return path
 97 |     on_path = shutil.which("llama-server")
 98 |     return Path(on_path) if on_path else None
 99 | 
100 | 
```
- An explicitly configured path wins — and if it does not exist the answer is "not found" rather than silently using
  another program. Otherwise: installed copies under `%USERPROFILE%\.localagent\llama` (searched in all sub-folders,
  GPU builds before CPU builds), then this checkout's Vulkan and CPU builds, then the PATH.

```python
101 | def find_model_file(config: Config) -> Path | None:
102 |     """The local model, a .gguf file (a vision add-on "mmproj" file is never chosen), or None."""
103 |     if config.model_file:
104 |         given = Path(config.model_file).expanduser()
105 |         return given if given.is_file() else None
106 |     for folder in (HOME / "models", CHECKOUT / "models"):
107 |         if folder.is_dir():
108 |             models = sorted(p for p in folder.glob("*.gguf") if not p.name.lower().startswith("mmproj"))
109 |             if models:
110 |                 return models[0]
111 |     return None
112 | 
113 | 
```
- An explicitly configured file wins. Otherwise the first `.gguf` (alphabetically) in `%USERPROFILE%\.localagent\models`,
  then in this checkout's `models`. `mmproj-*.gguf` files are vision add-ons, not models, and are never chosen. A model
  split into parts (`...-00001-of-00003.gguf`) sorts with its first part first, which is the one llama.cpp wants.

```python
114 | def can_start(config: Config) -> bool:
115 |     """True when a llama.cpp server could be started on this computer for ``config.api_base``."""
116 |     return ((urlsplit(config.api_base).hostname or "") in THIS_COMPUTER
117 |             and find_llama_server(config) is not None and find_model_file(config) is not None)
118 | 
119 | 
```
- A server can be started only for an address on **this computer** and only when both files exist. (A container whose
  `api_base` points at `host.docker.internal` cannot start a program on the host.)

```python
120 | def describe_local(config: Config) -> str:
121 |     """One line for /model and --doctor: which local model would answer, and its state."""
122 |     kind = running_local(config)
123 |     if kind == "openai":
124 |         return f"the model server at {config.api_base} (running)"
125 |     if kind == "ollama":
126 |         return f"Ollama at {config.host}, model {config.model} (running)"
127 |     model = find_model_file(config)
128 |     if can_start(config) and model is not None:
129 |         return f"{model.name} with llama.cpp (started when needed)"
130 |     return "none - see docs/01-setup-and-models.md"
131 | 
132 | 
```
- One line for `/model`, `--doctor` and the start-up notes: the running server, or the model that would be started, or
  "none" with the guide to follow.

## Never leaving a 5 GB model behind
```python
133 | _JOBS: list[int] = []                                   # Windows job handles kept open (see below)
134 | 
135 | 
```
```python
136 | def _end_with_this_program(process: subprocess.Popen) -> None:
137 |     """Windows: put ``process`` in a job that Windows closes when this program ends, however it
138 |     ends. Closing the terminal window skips Python's atexit, so without the job a 5 GB model
139 |     would stay in memory. (Elsewhere atexit is relied on.)"""
140 |     if sys.platform != "win32":
141 |         return
142 |     from ctypes import wintypes
143 | 
144 |     class Basic(ctypes.Structure):
145 |         _fields_ = [("user_time", ctypes.c_int64), ("job_time", ctypes.c_int64),
146 |                     ("flags", wintypes.DWORD), ("min_set", ctypes.c_size_t),
147 |                     ("max_set", ctypes.c_size_t), ("processes", wintypes.DWORD),
148 |                     ("affinity", ctypes.c_size_t), ("priority", wintypes.DWORD),
149 |                     ("scheduling", wintypes.DWORD)]
150 | 
151 |     class Extended(ctypes.Structure):
152 |         _fields_ = [("basic", Basic), ("io", ctypes.c_ulonglong * 6),
153 |                     ("process_memory", ctypes.c_size_t), ("job_memory", ctypes.c_size_t),
154 |                     ("peak_process", ctypes.c_size_t), ("peak_job", ctypes.c_size_t)]
155 | 
156 |     kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
157 |     kernel32.CreateJobObjectW.restype = wintypes.HANDLE
158 |     kernel32.OpenProcess.restype = wintypes.HANDLE
159 |     kernel32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
160 |                                                  wintypes.DWORD]
161 |     kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
162 |     kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
163 |     job = kernel32.CreateJobObjectW(None, None)
164 |     limits = Extended()
165 |     limits.basic.flags = 0x2000                         # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
166 |     handle = kernel32.OpenProcess(0x0101, False, process.pid)   # SET_QUOTA | TERMINATE
167 |     if job and handle and kernel32.SetInformationJobObject(job, 9, ctypes.byref(limits),
168 |                                                            ctypes.sizeof(limits)):
169 |         if kernel32.AssignProcessToJobObject(job, handle):
170 |             _JOBS.append(job)                           # open until this program ends
171 |     if handle:
172 |         kernel32.CloseHandle(handle)                    # the job keeps its own reference
173 | 
174 | 
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
175 | def _log_tail(lines: int = 8) -> str:
176 |     """The last lines of the server's log, to explain why it stopped."""
177 |     try:
178 |         text = LOG_FILE.read_text(encoding="utf-8", errors="replace")
179 |     except OSError:
180 |         return "(no log)"
181 |     return "\n".join(text.strip().splitlines()[-lines:])
182 | 
183 | 
```
- The last eight lines of the server's log — llama.cpp explains there why it stopped (a missing file, too little
  memory).

## `LlamaServer`
```python
184 | class LlamaServer:
185 |     """A llama.cpp server process started by this program (and stopped when it ends)."""
186 | 
```
```python
187 |     def __init__(self, program: Path, model: Path, api_base: str, context: int,
188 |                  gpu_layers: int = -1, threads: int = 0) -> None:
189 |         self.program, self.model, self.api_base = program, model, api_base.rstrip("/")
190 |         self.context, self.gpu_layers, self.threads = context, gpu_layers, threads
191 |         self.alias = model.stem                         # the model's name in the server's API
192 |         self.process: subprocess.Popen | None = None
193 | 
```
- Remembers the program, the model, the address, the context size, GPU layers and threads. `alias` is the model's
  name in the server's API (the file name without `.gguf`).

```python
194 |     def argv(self) -> list[str]:
195 |         """Its command line: the model, this computer only, the context size, tool calls."""
196 |         port = urlsplit(self.api_base).port or 8080
197 |         argv = [str(self.program), "-m", str(self.model), "--host", "127.0.0.1",
198 |                 "--port", str(port), "-c", str(self.context), "--jinja",
199 |                 "--reasoning-budget", "0", "--alias", self.alias]
200 |         if self.gpu_layers >= 0:                        # else llama.cpp fits the GPU itself
201 |             argv += ["-ngl", str(self.gpu_layers)]
202 |         if self.threads > 0:
203 |             argv += ["-t", str(self.threads)]
204 |         return argv
205 | 
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
206 |     def running(self) -> bool:
207 |         """True while the process this program started is alive."""
208 |         return self.process is not None and self.process.poll() is None
209 | 
```
```python
210 |     def ready(self) -> bool:
211 |         """True once the model is loaded (llama.cpp's /health says 200; 503 while loading)."""
212 |         root = self.api_base[:-3] if self.api_base.endswith("/v1") else self.api_base
213 |         return _get(root + "/health") == 200
214 | 
```
- `running`: the process this program started is still alive. `ready`: the model is loaded — llama.cpp's `/health`
  answers 503 while loading and 200 when ready (at the server's root, so a trailing `/v1` is removed first).

```python
215 |     def start(self, on_notice: Callable[[str], None]) -> None:
216 |         """Start the server unless it already runs, then wait until the model is loaded."""
217 |         if not self.running():
218 |             LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
219 |             options: dict = {}
220 |             if sys.platform == "win32":                 # no window; Ctrl+C stays with the agent
221 |                 options["creationflags"] = subprocess.CREATE_NO_WINDOW
222 |             else:
223 |                 options["start_new_session"] = True
224 |             with LOG_FILE.open("wb") as log:
225 |                 self.process = subprocess.Popen(self.argv(), stdin=subprocess.DEVNULL, stdout=log,
226 |                                                 stderr=subprocess.STDOUT, cwd=self.program.parent,
227 |                                                 **options)
228 |             _end_with_this_program(self.process)
229 |             atexit.register(self.stop)
230 |             on_notice(f"starting the local model {self.model.name} "
231 |                       f"(loading takes 10-60 s; log: {LOG_FILE})")
232 |         started = time.monotonic()
233 |         while not self.ready():
234 |             if self.process is not None and self.process.poll() is not None:
235 |                 code, self.process = self.process.returncode, None
236 |                 raise LLMError(f"the local model server stopped while starting (exit code {code}):"
237 |                                f"\n{_log_tail()}", final=True)
238 |             if time.monotonic() - started > START_TIMEOUT:
239 |                 self.stop()
240 |                 raise LLMError(f"the local model did not load within {START_TIMEOUT:.0f}s; "
241 |                                f"see {LOG_FILE}", final=True)
242 |             time.sleep(0.5)
243 |         on_notice(f"local model ready: {self.alias}")
244 | 
```
- Starts the program unless it already runs (a second call — for example after Ctrl+C during loading — just waits
  again). On Windows `CREATE_NO_WINDOW` gives it no console window, which also means Ctrl+C in your terminal stops the
  agent's current task but not the model; elsewhere `start_new_session` does the same. Its output goes to the log file,
  it is tied to this program (`_end_with_this_program`, `atexit`), and a notice says that loading has begun.
- Then it checks `ready()` twice a second. If the process ends meanwhile, the error includes its exit code and the end
  of its log. After `START_TIMEOUT` the server is stopped and an error names the log. Both errors are `final`: retrying
  cannot help, so the model chain moves on at once.

```python
245 |     def stop(self) -> None:
246 |         """Stop the server (frees its memory); safe to call more than once."""
247 |         process, self.process = self.process, None
248 |         if process is not None and process.poll() is None:
249 |             process.terminate()
250 |             try:
251 |                 process.wait(timeout=10)
252 |             except subprocess.TimeoutExpired:
253 |                 process.kill()
254 |                 process.wait()
255 | 
256 | 
```
- Ends the process (politely, then by force after 10 seconds). Safe to call twice.

## One server per program
```python
257 | _SERVERS: dict[tuple[str, str, str], LlamaServer] = {}
258 | 
259 | 
```
```python
260 | def server_for(config: Config) -> LlamaServer:
261 |     """The llama.cpp server for this program, model and address (one per program run)."""
262 |     program, model = find_llama_server(config), find_model_file(config)
263 |     if program is None or model is None:
264 |         raise LLMError(NO_LOCAL_MODEL, final=True)
265 |     key = (str(program), str(model), config.api_base)
266 |     if key not in _SERVERS:
267 |         _SERVERS[key] = LlamaServer(program, model, config.api_base, config.local_ctx,
268 |                                     config.gpu_layers, config.local_threads)
269 |     return _SERVERS[key]
270 | 
271 | 
```
- `_SERVERS` holds the servers this program started, one per program + model + address, so switching modes back and
  forth (or many HTTP-server sessions) never starts a second copy of the model. `server_for` returns the existing one
  or creates it; without the files it raises the final `NO_LOCAL_MODEL` error.

```python
272 | def stop_local_servers() -> int:
273 |     """Stop every llama.cpp server this program started; returns how many were running."""
274 |     running = [server for server in _SERVERS.values() if server.running()]
275 |     for server in running:
276 |         server.stop()
277 |     return len(running)
278 | 
279 | 
```
- Stops every server this program started and returns how many were running. Used by `/model online` (to free the
  memory) and when the CLI or the HTTP server ends.

## `LocalModel` — the local model as a backend
```python
280 | class LocalModel:
281 |     """The local model as a ChatBackend.
282 | 
283 |     It uses the local server that is running (``kind`` "openai" or "ollama", or "" for
284 |     whichever answers), starts llama.cpp's when none is, and fits the conversation into the
285 |     local context window - a history built for Gemini's 32K budget would not fit 8K.
286 |     """
287 | 
```
```python
288 |     def __init__(self, config: Config, kind: str = "",
289 |                  on_notice: Callable[[str], None] = lambda _m: None) -> None:
290 |         self._config = config
291 |         self._kind = kind
292 |         self._notice = on_notice
293 |         self._client: ChatBackend | None = None
294 |         self._server: LlamaServer | None = None
295 |         self.last_model = ""                            # what answered (shown after a request)
296 | 
```
- `kind` is `"openai"` or `"ollama"` to insist on that kind of server (as `--fallback openai` does), or `""` to use
  whatever is available. `_client` is the real client once connected; `_server` is set when this program started
  llama.cpp. `last_model` says which local model answered (shown after each request).

```python
297 |     def ensure_ready(self) -> None:
298 |         """Connect to a running local server or start one; raises LLMError when impossible."""
299 |         if self._client is not None and (self._server is None or self._server.running()):
300 |             return
301 |         self._client, kind = None, self._kind
302 |         if kind in ("", "openai") and server_up(self._config.api_base):
303 |             kind = "openai"
304 |         elif kind in ("", "ollama") and ollama_up(self._config.host):
305 |             kind = "ollama"
306 |         elif kind in ("", "openai") and can_start(self._config):
307 |             self._server = server_for(self._config)
308 |             self._server.start(self._notice)
309 |             kind = "openai"
310 |         elif kind:
311 |             where = self._config.host if kind == "ollama" else self._config.api_base
312 |             raise LLMError(f"the local {kind} model server is not running at {where}", final=True)
313 |         else:
314 |             raise LLMError(NO_LOCAL_MODEL, final=True)
315 |         local = replace(self._config, num_ctx=self._config.local_ctx)
316 |         if self._server is not None:
317 |             local = replace(local, model=self._server.alias)
318 |         self._client = OllamaClient(local) if kind == "ollama" else OpenAICompatClient(local)
319 |         self.last_model = (f"ollama:{local.model}" if kind == "ollama"
320 |                            else f"local:{self._server.alias if self._server else local.model}")
321 | 
```
- Done already, unless the server this program started has died since (then everything is found again — and a dead
  server is simply restarted).
- Otherwise, in order: a server at `api_base`; Ollama at `host`; starting llama.cpp. A demanded kind that is not
  running gives a final error naming the address; nothing at all gives `NO_LOCAL_MODEL`.
- The client gets a copy of the config whose `num_ctx` is the **local** context, so Ollama is never asked for Gemini's
  32K window; with a server it started, the model name is the server's alias.
- `/model offline` calls this right away, so the model loads when you switch rather than in the middle of your next task.

```python
322 |     def chat(self, messages: list[dict], tools: list[dict],
323 |              on_token: TokenSink | None = None) -> Reply:
324 |         """Answer with the local model, the conversation shortened to fit its context."""
325 |         self.ensure_ready()
326 |         assert self._client is not None
327 |         budget = int(self._config.local_ctx * 0.7) * 4 - len(json.dumps(tools))
328 |         return self._client.chat(fit_history(messages, max(budget, 4000)), tools, on_token)
```
- Makes sure a server is ready, then sends the conversation **fitted to the local window**: 70 % of `local_ctx` tokens
  (×4 characters each) minus the size of the tool definitions, never less than 4 000 characters. This is what lets a
  conversation that grew with Gemini continue on an 8K local model when the internet goes away.
