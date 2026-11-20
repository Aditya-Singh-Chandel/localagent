# `localagent/models.py` — local models: the catalog and the downloads

**Role:** which free model suits this computer, which models are installed, and fetching llama.cpp and a model —
resumable, and checked against the publisher's checksum. One command of its own:

```
localagent models                     installed models, the catalog, the suggestion for this computer
localagent models download qwen3-4b   fetch a catalog model (resumable, verified)
localagent models use qwen3-4b        make it the model offline mode uses (also a path to any .gguf)
```

The guided first run, `localagent setup`, lives in `wizard.py` (see `wizard.md`); its "download a free model for me"
choice uses the helpers here.

## Module header
@@ localagent/models.py header @@
- Imports: `ctypes` (memory size on Windows), `os`, `platform` (processor type), `stat` (making downloaded programs
  runnable on Linux/macOS), `sys`, `time`, `dataclass`, `Path`, `Callable`, and from the agent: the config, the
  download functions, where models live (`HOME`, `CHECKOUT`) with the model-file finder, and `save_user_env`.
- `LLAMA_RELEASES` is GitHub's list of llama.cpp's newest releases. `GiB` is 1024³ bytes — the unit Windows itself
  shows as "GB" (Task Manager's 15.7 GB). `Say` is the type of the printing function (tests collect the lines instead).

## The catalog
@@ localagent/models.py CatalogModel @@
@@ localagent/models.py CatalogModel.url @@
- One model the agent can fetch: its short name, Hugging Face repository and file, the **published size and SHA-256**
  (copied from Hugging Face's API on 20 Nov 2026; the 8B size matched the copy on this laptop byte for byte), the
  memory a computer needs to get it suggested, and one line for the list. `url` is Hugging Face's download address.

@@ localagent/models.py after_CatalogModel @@
- `CATALOG`, biggest first: Qwen3-8B (4-bit, 4.7 GB) for 16 GB computers — the model measured in the evals; Qwen3-4B
  (2.3 GB) for 8 GB; Qwen3-1.7B (8-bit, 1.7 GB) for smaller ones, honestly labelled as good for questions and very
  small tasks only. All three come from Qwen's own repositories and support tool calls.

## Choosing
@@ localagent/models.py total_memory_gb @@
- Windows: `GlobalMemoryStatusEx` fills a `MEMORYSTATUSEX` structure (described with `ctypes`; `length` must be set
  first); `total` is the installed memory. Linux and macOS: page size × number of pages. `None` when it cannot be
  told.

@@ localagent/models.py recommend @@
@@ localagent/models.py catalog_entry @@
- `recommend`: the first (biggest) model whose memory need is met — 14 GB for the 8B model (a "16 GB" computer reports
  about 15.7), 7 GB for the 4B one; the middle model when the memory is unknown. `catalog_entry` finds a model by
  name, ignoring letter case and spaces.

@@ localagent/models.py installed_models @@
- Every `.gguf` in the two model folders (vision add-ons left out) plus a model configured elsewhere — what `localagent
  models` lists.

## Fetching
@@ localagent/models.py install_model @@
- Downloads a catalog model into `%USERPROFILE%\.localagent\models`, where offline mode finds it, through
  `fetch.download` (resumable, verified).

@@ localagent/models.py llama_variants @@
- Which llama.cpp build to fetch, best first: on Windows the Vulkan build (it uses most graphics cards and also
  carries the processor-only libraries, so it works without one too), then the processor-only build; on Linux the
  plain build first (the Vulkan one needs extra system libraries); on macOS its one build (Metal included). ARM
  computers get the ARM builds.

@@ localagent/models.py find_llama_release @@
- Reads the 15 newest releases and returns the first file named `llama-<tag>-bin-<variant>.zip` or `.tar.gz` **that has
  a published checksum**. Searching several releases matters: in November 2026 llama.cpp's "latest" release was a
  placeholder (`v1.0.0`, holding only a text file) while the real builds were `b12588` and so on.

@@ localagent/models.py install_llama @@
- Downloads that file into `~/.localagent/llama/downloads`, unpacks it into its own folder named after the build
  (where `find_llama_server` looks — GPU builds first), deletes the archive, and on Linux/macOS marks the `llama-*`
  programs as runnable (archives can lose that mark).

## Showing progress, the sandbox hint
@@ localagent/models.py progress_printer @@
- A download's progress — `1.20 GB of 4.68 GB (25%), about 6 min left at 12.3 MB/s` — rewritten in place once a second
  in a terminal, or as a new line every 30 seconds when the output goes to a file. The last line always appears.

@@ localagent/models.py sandbox_hint @@
- The exact `docker build` command for this copy's Dockerfile (the release bundle carries one too), or a pointer to
  the docs. Used by the setup's sandbox step.

## `localagent models`
@@ localagent/models.py run_models @@
- No words: the installed models (`*` marks the one offline mode uses) and the catalog with the suggestion for this
  computer.
- `download NAME`: fetches a catalog model with progress; a broken download says how to continue it.
- `use NAME-or-FILE`: finds the catalog file in either model folder, or takes any `.gguf` path, and saves it as
  `LOCALAGENT_MODEL_FILE` in the user settings file — warning when a real environment variable of that name would
  still win.
- Anything else prints the usage line (exit code 2).
