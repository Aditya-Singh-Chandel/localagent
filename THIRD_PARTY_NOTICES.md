# Third-party software

localagent itself is under the MIT License (`LICENSE`). Its Python code uses only Python's standard library.
The parts below are **not** in this repository; they are downloaded separately (see
`docs/01-setup-and-models.md`). Anyone who passes them on together with localagent — for example in a download
bundle — must include their licenses as listed here.

| Part | Used for | License | Where its license text is |
|---|---|---|---|
| llama.cpp (`llama-server` and its libraries) | running the local model | MIT | `LICENSE` in https://github.com/ggml-org/llama.cpp |
| LLVM OpenMP runtime (`libomp.dll`, shipped inside llama.cpp's Windows builds) | parallel processing in llama.cpp | Apache-2.0 with LLVM exception | `LICENSE-LLVM-OpenMP` inside the llama.cpp zip |
| Qwen3-8B model (`Qwen3-8B-Q4_K_M.gguf`) | the recommended local model | Apache-2.0 | https://huggingface.co/Qwen/Qwen3-8B-GGUF |
| ruff (verification layer; in the Windows download) | lint checks of Python code | MIT | https://github.com/astral-sh/ruff |
| mypy (optional verification layer, `--type-check`; not in the Windows download) | type checks of Python code | MIT | https://github.com/python/mypy |
| Python embeddable package (Windows release bundle) | running localagent without installing Python | Python Software Foundation License | `LICENSE.txt` inside the bundle's `python` folder |

Google Gemini is a service used through its API under Google's terms (https://ai.google.dev/gemini-api/terms); nothing
of it is distributed with localagent.
