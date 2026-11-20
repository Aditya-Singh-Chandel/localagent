# The optional offline model (a brain on your own computer)

The agent's main brain is Gemini (`04-start-from-zero.md`, Step 6). This page is about the **backup**: a model that
runs on your own PC. It costs nothing, works without internet and has no daily quota, but it is slower and weaker.
Use it when Gemini's quota is used up, when you are offline, or for private code you must not send to Google.

## Your machine (measured)
15.7 GB RAM · Intel i7-1255U · NVIDIA MX550 with 2 GB of graphics memory · Python 3.14.0b1 (a beta build; the code
also runs on 3.10-3.13, which CI tests).
Consequence: models run mostly on the processor from normal memory. Budget about 6 GB for the model, so an **8B
model at 4-bit** (a compressed format) is the right size. 9 of its layers fit on the graphics card.

## What is installed here, and how it runs
| Part | Where | Why |
|---|---|---|
| The model `Qwen3-8B-Q4_K_M.gguf` (5 GB) | `models\` | good tool calling for its size; from huggingface.co/Qwen/Qwen3-8B-GGUF |
| llama.cpp `llama-server`, Vulkan build | `bin\llamacpp-vulkan\` | serves the model with the same API shape as OpenAI, using the graphics card for 9 layers |
| llama.cpp CPU build | `bin\llamacpp\` | used for `-GpuLayers 0`, or when the Vulkan build is missing |
| `serve.ps1` | project folder | starts the server with the right options |

Why llama.cpp and not Ollama: Ollama's downloads ran at ~40 KB/s here, Hugging Face at 16-28 MB/s. The agent supports
both (`--backend ollama` or `--backend openai`).

## Using it
1. Start it in its own PowerShell window and leave that open: `.\serve.ps1` (CPU only: `.\serve.ps1 -GpuLayers 0`).
   It needs about 6 GB of free memory; Windows may stop it when memory runs low.
2. Start `localagent` as usual. While the server runs, the agent adds it automatically as the **last fallback** after
   the Gemini models (the banner says `fallback: the local model server ...`).
3. Only the local model: `localagent --backend openai`.
4. Measure it: `.\.venv\Scripts\python -m evals.run_evals --backend openai --sandbox docker` (last result: 12/15,
   1-5 minutes per task; Gemini: 15/15 in seconds).

## What `serve.ps1` sets, and why
- `--jinja`: use the model's own chat template, which is what makes tool calls work.
- `--reasoning-budget 0`: skip Qwen3's long "thinking" text, which is slow on a laptop.
- `-GpuLayers 9`: measured on the MX550 — more layers were faster up to 11, but 13+ ran out of its 2 GB; 9 leaves room.
- `--alias qwen3-8b`: the model name the agent sends (`Config.model`). Port 8080 = `Config.api_base`.
Full measurements: `03-build-log.md`.

## Other models
Newer local models appear constantly. Any model served by llama.cpp, Ollama, LM Studio or vLLM works if it supports
tool calls: point the agent at it with `--backend openai --api-base <url> --model <name>` (or `--backend ollama --model
<tag>`) and compare with the eval suite before trusting it.
