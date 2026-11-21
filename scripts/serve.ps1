# Starts the local llama.cpp model server (OpenAI-compatible API) for localagent.
#   .\scripts\serve.ps1                      -> Qwen3-8B, 9 layers on the GPU, http://127.0.0.1:8080/v1
#   .\scripts\serve.ps1 -GpuLayers 0         -> CPU only
#   .\scripts\serve.ps1 -Model models\x.gguf -Ctx 4096 -Threads 4
# Then, in another terminal:
#   .\.venv\Scripts\python -m localagent --backend openai --api-base http://127.0.0.1:8080/v1 --model qwen3-8b
param(
    [string]$Model = "models\Qwen3-8B-Q4_K_M.gguf",
    [int]$Port = 8080,
    [int]$Ctx = 8192,
    [int]$Threads = 6,
    [int]$GpuLayers = 9
)
# Use the Vulkan build (can offload layers to the GPU) when present, else the CPU-only build.
$project = Split-Path -Parent $PSScriptRoot    # the project folder, above scripts\
$vulkan = Join-Path $project "bin\llamacpp-vulkan\llama-server.exe"
$cpu = Join-Path $project "bin\llamacpp\llama-server.exe"
if ($GpuLayers -gt 0 -and (Test-Path $vulkan)) { $server = $vulkan }
elseif (Test-Path $cpu) { $server = $cpu; $GpuLayers = 0 }
else { throw "llama-server.exe not found under bin\llamacpp or bin\llamacpp-vulkan" }
$modelPath = Join-Path $project $Model
if (-not (Test-Path $modelPath)) { throw "model file not found: $modelPath" }
# --jinja: use the model's own chat template (needed for tool calling).
# --reasoning-budget 0: skip Qwen3's long "thinking" text, which wastes tokens on a slow CPU.
# -ngl: number of layers placed in GPU memory (the 2 GB MX550 fits about 11 for an 8B model).
& $server -m $modelPath --jinja -c $Ctx --port $Port --host 127.0.0.1 -t $Threads `
    -ngl $GpuLayers --reasoning-budget 0 --alias qwen3-8b
