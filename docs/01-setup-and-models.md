# Online and offline: Gemini, or a brain on your own computer

The agent has two kinds of brain and switches between them for you:

| | **Online** — Gemini | **Offline** — the local model |
|---|---|---|
| Runs on | Google's servers | your own PC (llama.cpp, or Ollama / LM Studio) |
| Needs | internet + a free key (`04-start-from-zero.md`, Step 6) | ~6 GB of free memory, two files (below) |
| Speed (this laptop) | 1-3 s per step | 20-60 s per step |
| Quality (15 eval tasks) | 15/15 | 12/15 |
| Limits | daily quota per model | none |
| Your code goes to | Google | nowhere — it stays on your computer |

## The three modes
- **auto** (the default): Gemini answers. When Gemini cannot be reached — no internet — or its daily quota is gone,
  the local model answers instead, **by itself and immediately**, and Gemini takes over again when it is back. Without
  a Gemini key, auto simply uses the local model.
- **online**: only Gemini. Nothing local is started.
- **offline**: only the local model. Nothing is sent to the internet.

Choose when you start (`localagent --offline`, `localagent --online`), make it your default (`setx LOCALAGENT_MODE
offline`, or `"env": {"LOCALAGENT_MODE": "offline"}` in a settings file), or switch at any time in the chat:
```
you> /model                 shows the mode, who answered last, and what online and offline would use
you> /model offline         (or /offline) the local model from now on - started now if needed
you> /model online          (or /online)  Gemini from now on - a local model the agent started is stopped
you> /model auto            (or /auto)    back to automatic
```
The conversation and your files stay as they are; only who answers changes. After every request the last line names
the model that answered (`... | gemini-3.5-flash-lite` or `... | local:Qwen3-8B-Q4_K_M`), so you always know whether
the internet was used.

## What offline needs, and where to put it
Two files: the **llama.cpp server program** (`llama-server.exe`) and a **model** (a `.gguf` file). The agent looks in
these places, first match wins:

| | 1. your setting | 2. the installed place | 3. this project's folders |
|---|---|---|---|
| program | `LOCALAGENT_LLAMA_SERVER` | `%USERPROFILE%\.localagent\llama\` (any sub-folder) | `bin\llamacpp-vulkan\`, `bin\llamacpp\` |
| model | `LOCALAGENT_MODEL_FILE` | `%USERPROFILE%\.localagent\models\*.gguf` | `models\*.gguf` |

**The easy way:** `localagent setup` (or `localagent models download <name>`) downloads both, checks them against
their publishers' checksums, and puts them where the agent looks. A broken download continues where it stopped.
```
localagent models                     what is installed, what fits this computer
localagent models download qwen3-4b   qwen3-8b (4.7 GB, 16 GB memory) / qwen3-4b (2.3 GB, 8 GB) / qwen3-1.7b (1.7 GB)
localagent models use qwen3-4b        offline mode uses this one from now on
```
On this computer both are already in the project's `bin\` and `models\` folders, so nothing needs doing.
`localagent --doctor` shows what it found:
```
ok    local model: Qwen3-8B-Q4_K_M.gguf (5027 MB), started with ...\bin\llamacpp-vulkan\llama-server.exe when needed
```
**By hand, on another computer:** download a llama.cpp release for Windows from github.com/ggml-org/llama.cpp/releases (the
`win-vulkan-x64` zip works with most graphics cards; `win-cpu-x64` without one), unzip it into
`%USERPROFILE%\.localagent\llama\`, and put a model into `%USERPROFILE%\.localagent\models\`. A good size for 16 GB of
memory is an 8B model at 4-bit, for example `Qwen3-8B-Q4_K_M.gguf` from huggingface.co/Qwen/Qwen3-8B-GGUF (5 GB). These
folders are outside every project, so they are never committed to git (the project's own `bin\` and `models\` are
excluded by `.gitignore` too).

## How the local model is started (you do not have to)
When the local model is needed and no local server is running, the agent starts llama.cpp itself, waits until the model
is loaded (10-60 seconds; a notice says so), answers, and **stops it when you leave** — even if you just close the
terminal window (Windows ends it together with the agent). Its messages go to `%USERPROFILE%\.localagent\llama-server.log`.
It listens only on `127.0.0.1`, so no other computer can use it. Details: `localserver.md`.

- **Graphics card:** llama.cpp measures the card's free memory and puts as many of the model's layers there as fit
  (`--fit`, on by default in this build). On this laptop's 2 GB MX550 hand-tuning showed 9-11 layers fit; to force a
  number, `setx LOCALAGENT_GPU_LAYERS 9` (`0` = processor only).
- **Context:** the local model gets an 8K window (`LOCALAGENT_LOCAL_CTX`). A long conversation that grew with Gemini is
  shortened automatically before it is sent to the local model.
- **Already running a server?** `serve.ps1`, Ollama or LM Studio — the agent uses it instead of starting one, and does
  not stop it.

## Your machine (measured)
15.7 GB RAM · Intel i7-1255U · NVIDIA MX550 with 2 GB of graphics memory · Python 3.14.0b1 (a beta build; the code
also runs on 3.10-3.13, which CI tests).
Consequence: models run mostly on the processor from normal memory. Budget about 6 GB for the model, so an **8B
model at 4-bit** (a compressed format) is the right size. Close big programs (browsers) before going offline: with
only ~4 GB free the model loads very slowly and Windows may stop it.

Why llama.cpp and not Ollama: Ollama's downloads ran at ~40 KB/s here, Hugging Face at 16-28 MB/s. The agent supports
both, and uses Ollama when it finds it running.

## `serve.ps1` — starting the server by hand (optional)
Still useful to keep one model loaded across several `localagent` runs: `.\serve.ps1` in its own window (CPU only:
`.\serve.ps1 -GpuLayers 0`). What it sets, and why:
- `--jinja`: use the model's own chat template, which is what makes tool calls work.
- `--reasoning-budget 0`: skip Qwen3's long "thinking" text, which is slow on a laptop.
- `-GpuLayers 9`: measured on the MX550 — more layers were faster up to 11, but 13+ ran out of its 2 GB; 9 leaves room.
- `--alias qwen3-8b`: the model name the agent sends. Port 8080 = `Config.api_base`.
Full measurements: `03-build-log.md`.

## Measuring a model
`.\.venv\Scripts\python -m evals.run_evals --offline --sandbox docker` (last result for Qwen3-8B: 12/15, 1-5 minutes
per task; Gemini: 15/15 in seconds).

## Other models
Newer local models appear constantly. Any `.gguf` model with tool-call support works: put it into the models folder (the
first one alphabetically is used, or name it with `LOCALAGENT_MODEL_FILE`), or point the agent at any server with
`--backend openai --api-base <url> --model <name>` (or `--backend ollama --model <tag>`). Compare with the eval suite
before trusting it.
