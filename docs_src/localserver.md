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
@@ localagent/localserver.py header @@
- Imports: `atexit` (stop the server when Python ends), `ctypes` (the Windows job, below), `json`, `os`, `shutil`
  (`which`), `subprocess`, `sys`, `time`, `urllib` (health checks), `replace`, `Path`, `Callable`, `urlsplit`, and from
  the agent the config, `LLMError`, the two clients and `fit_history`.
- `HOME` is `%USERPROFILE%\.localagent`. `CHECKOUT` is the folder above the package — this project's folder when the agent
  runs from it. `PROGRAM` is the server's file name (`.exe` on Windows). `LOG_FILE` receives the server's output.
- `START_TIMEOUT`: a model may take up to 5 minutes to load from a slow disk. `GPU_WORDS` recognise GPU builds of
  llama.cpp by their folder or file name. `THIS_COMPUTER`: the only addresses for which a server may be started.
- `NO_LOCAL_MODEL` is the message when nothing local exists: what is needed, where to put it, and the guide to follow.

## Is something running?
@@ localagent/localserver.py _get @@
- A quick GET request (1 second), returning the HTTP status — also for error statuses — or 0 when nothing answers.

@@ localagent/localserver.py server_up @@
@@ localagent/localserver.py ollama_up @@
@@ localagent/localserver.py running_local @@
- `server_up`: an OpenAI-style server answers `/models` (llama.cpp, LM Studio, vLLM). `ollama_up`: Ollama answers
  `/api/tags`. `running_local` asks both, the OpenAI-style address first. (`server_up` used to live in `cli.py`; the CLI
  now imports it from here.) With `local_server` set (`localagent setup` sets it), only that kind is asked — a chosen
  Ollama is not replaced by some other server that happens to answer.

## Finding the files
@@ localagent/localserver.py _gpu_build @@
- True for a llama.cpp folder or file whose name mentions a graphics API (CUDA, Vulkan, Metal, ROCm, HIP, SYCL).

@@ localagent/localserver.py find_llama_server @@
- An explicitly configured path wins — and if it does not exist the answer is "not found" rather than silently using
  another program. Otherwise: installed copies under `%USERPROFILE%\.localagent\llama` (searched in all sub-folders,
  GPU builds before CPU builds), then this checkout's Vulkan and CPU builds, then the PATH.

@@ localagent/localserver.py find_model_file @@
- An explicitly configured file wins. Otherwise the first `.gguf` (alphabetically) in `%USERPROFILE%\.localagent\models`,
  then in this checkout's `models`. `mmproj-*.gguf` files are vision add-ons, not models, and are never chosen. A model
  split into parts (`...-00001-of-00003.gguf`) sorts with its first part first, which is the one llama.cpp wants.

@@ localagent/localserver.py can_start @@
- A server can be started only for an address on **this computer** and only when both files exist. (A container whose
  `api_base` points at `host.docker.internal` cannot start a program on the host.)

@@ localagent/localserver.py describe_local @@
- One line for `/model`, `--doctor` and the start-up notes: the running server, or the model that would be started, or
  "none" with the guide to follow. A chosen server that is down is named as such: Ollama "not running now: start
  Ollama", or the server at your address "not answering now" (when no model could be started there instead).

## Never leaving a 5 GB model behind
@@ localagent/localserver.py after_describe_local @@
@@ localagent/localserver.py _end_with_this_program @@
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

@@ localagent/localserver.py _log_tail @@
- The last eight lines of the server's log — llama.cpp explains there why it stopped (a missing file, too little
  memory).

## `LlamaServer`
@@ localagent/localserver.py LlamaServer @@
@@ localagent/localserver.py LlamaServer.__init__ @@
- Remembers the program, the model, the address, the context size, GPU layers and threads. `alias` is the model's
  name in the server's API (the file name without `.gguf`).

@@ localagent/localserver.py LlamaServer.argv @@
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

@@ localagent/localserver.py LlamaServer.running @@
@@ localagent/localserver.py LlamaServer.ready @@
- `running`: the process this program started is still alive. `ready`: the model is loaded — llama.cpp's `/health`
  answers 503 while loading and 200 when ready (at the server's root, so a trailing `/v1` is removed first).

@@ localagent/localserver.py LlamaServer.start @@
- Starts the program unless it already runs (a second call — for example after Ctrl+C during loading — just waits
  again). On Windows `CREATE_NO_WINDOW` gives it no console window, which also means Ctrl+C in your terminal stops the
  agent's current task but not the model; elsewhere `start_new_session` does the same. Its output goes to the log file,
  it is tied to this program (`_end_with_this_program`, `atexit`), and a notice says that loading has begun.
- Then it checks `ready()` twice a second. If the process ends meanwhile, the error includes its exit code and the end
  of its log. After `START_TIMEOUT` the server is stopped and an error names the log. Both errors are `final`: retrying
  cannot help, so the model chain moves on at once.

@@ localagent/localserver.py LlamaServer.stop @@
- Ends the process (politely, then by force after 10 seconds). Safe to call twice.

## One server per program
@@ localagent/localserver.py after_LlamaServer @@
@@ localagent/localserver.py server_for @@
- `_SERVERS` holds the servers this program started, one per program + model + address, so switching modes back and
  forth (or many HTTP-server sessions) never starts a second copy of the model. `server_for` returns the existing one
  or creates it; without the files it raises the final `NO_LOCAL_MODEL` error.

@@ localagent/localserver.py stop_local_servers @@
- Stops every server this program started and returns how many were running. Used by `/model online` (to free the
  memory) and when the CLI or the HTTP server ends.

## `LocalModel` — the local model as a backend
@@ localagent/localserver.py LocalModel @@
@@ localagent/localserver.py LocalModel.__init__ @@
- `kind` is `"openai"` or `"ollama"` to insist on that kind of server (as `--fallback openai` does), or `""` to use
  whatever is available. `_client` is the real client once connected; `_server` is set when this program started
  llama.cpp. `last_model` says which local model answered (shown after each request).

@@ localagent/localserver.py LocalModel.ensure_ready @@
- Done already, unless the server this program started has died since (then everything is found again — and a dead
  server is simply restarted).
- Otherwise, in order: a server at `api_base`; Ollama at `host`; starting llama.cpp. A demanded kind that is not
  running gives a final error naming the address; nothing at all gives `NO_LOCAL_MODEL`.
- The client gets a copy of the config whose `num_ctx` is the **local** context, so Ollama is never asked for Gemini's
  32K window; with a server it started, the model name is the server's alias.
- `/model offline` calls this right away, so the model loads when you switch rather than in the middle of your next task.

@@ localagent/localserver.py LocalModel.chat @@
- Makes sure a server is ready, then sends the conversation **fitted to the local window**: 70 % of `local_ctx` tokens
  (×4 characters each) minus the size of the tool definitions, never less than 4 000 characters. This is what lets a
  conversation that grew with Gemini continue on an 8K local model when the internet goes away.
