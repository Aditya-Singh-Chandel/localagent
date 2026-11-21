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
```python
  1 | """Local models: which model suits this computer, which are installed, and fetching llama.cpp
  2 | and a model (resumable, checked against the publisher's checksum).
  3 | 
  4 |   localagent models                     installed models, the catalog, the recommendation
  5 |   localagent models download qwen3-4b   fetch a catalog model (about 2.5 GB)
  6 |   localagent models use qwen3-4b        make it the model offline mode uses
  7 | 
  8 | The guided first run (`localagent setup`) lives in wizard.py and uses these helpers.
  9 | """
 10 | from __future__ import annotations
 11 | 
 12 | import ctypes
 13 | import os
 14 | import platform
 15 | import stat
 16 | import sys
 17 | import time
 18 | from dataclasses import dataclass
 19 | from pathlib import Path
 20 | from typing import Callable
 21 | 
 22 | from .config import Config
 23 | from .fetch import DownloadError, Progress, download, extract_archive, get_json
 24 | from .localserver import CHECKOUT, HOME, find_model_file
 25 | from .settings import USER_SETTINGS, save_user_env
 26 | 
 27 | LLAMA_RELEASES = "https://api.github.com/repos/ggml-org/llama.cpp/releases?per_page=15"
 28 | GiB = 1024 ** 3
 29 | Say = Callable[[str], None]
 30 | 
 31 | 
```
- Imports: `ctypes` (memory size on Windows), `os`, `platform` (processor type), `stat` (making downloaded programs
  runnable on Linux/macOS), `sys`, `time`, `dataclass`, `Path`, `Callable`, and from the agent: the config, the
  download functions, where models live (`HOME`, `CHECKOUT`) with the model-file finder, and `save_user_env`.
- `LLAMA_RELEASES` is GitHub's list of llama.cpp's newest releases. `GiB` is 1024³ bytes — the unit Windows itself
  shows as "GB" (Task Manager's 15.7 GB). `Say` is the type of the printing function (tests collect the lines instead).

## The catalog
```python
 32 | @dataclass(frozen=True)
 33 | class CatalogModel:
 34 |     """A local model the agent knows how to fetch and check."""
 35 | 
 36 |     name: str            # what you type: `localagent models download qwen3-4b`
 37 |     repo: str            # the Hugging Face repository it comes from
 38 |     file: str            # the .gguf file in that repository
 39 |     size: int            # bytes, as published
 40 |     sha256: str          # checksum, as published (Hugging Face's large-file record)
 41 |     memory_gb: int       # computers with at least this much memory get it recommended
 42 |     note: str            # one line for the list
 43 | 
```
```python
 44 |     @property
 45 |     def url(self) -> str:
 46 |         """Where Hugging Face serves the file."""
 47 |         return f"https://huggingface.co/{self.repo}/resolve/main/{self.file}"
 48 | 
 49 | 
```
- One model the agent can fetch: its short name, Hugging Face repository and file, the **published size and SHA-256**
  (copied from Hugging Face's API on 20 Nov 2026; the 8B size matched the copy on this laptop byte for byte), the
  memory a computer needs to get it suggested, and one line for the list. `url` is Hugging Face's download address.

```python
 50 | CATALOG = (   # biggest first; sizes and checksums from Hugging Face's API (20 Nov 2026)
 51 |     CatalogModel("qwen3-8b", "Qwen/Qwen3-8B-GGUF", "Qwen3-8B-Q4_K_M.gguf", 5_027_783_488,
 52 |                  "d98cdcbd03e17ce47681435b5150e34c1417f50b5c0019dd560e4882c5745785", 14,
 53 |                  "the best that fits 16 GB of memory (12/15 on the evals)"),
 54 |     CatalogModel("qwen3-4b", "Qwen/Qwen3-4B-GGUF", "Qwen3-4B-Q4_K_M.gguf", 2_497_280_256,
 55 |                  "7485fe6f11af29433bc51cab58009521f205840f5b4ae3a32fa7f92e8534fdf5", 7,
 56 |                  "for 8 GB computers: twice as fast, weaker on bigger tasks"),
 57 |     CatalogModel("qwen3-1.7b", "Qwen/Qwen3-1.7B-GGUF", "Qwen3-1.7B-Q8_0.gguf", 1_834_426_016,
 58 |                  "061b54daade076b5d3362dac252678d17da8c68f07560be70818cace6590cb1a", 0,
 59 |                  "for small computers: questions and very small tasks only"),
 60 | )
 61 | 
 62 | 
```
- `CATALOG`, biggest first: Qwen3-8B (4-bit, 4.7 GB) for 16 GB computers — the model measured in the evals; Qwen3-4B
  (2.3 GB) for 8 GB; Qwen3-1.7B (8-bit, 1.7 GB) for smaller ones, honestly labelled as good for questions and very
  small tasks only. All three come from Qwen's own repositories and support tool calls.

## Choosing
```python
 63 | def total_memory_gb() -> float | None:
 64 |     """This computer's memory in GB, or None when it cannot be told."""
 65 |     if sys.platform == "win32":
 66 |         class Status(ctypes.Structure):          # Windows' MEMORYSTATUSEX
 67 |             _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong),
 68 |                         ("total", ctypes.c_ulonglong), ("available", ctypes.c_ulonglong),
 69 |                         ("page_total", ctypes.c_ulonglong), ("page_free", ctypes.c_ulonglong),
 70 |                         ("virtual_total", ctypes.c_ulonglong), ("virtual_free", ctypes.c_ulonglong),
 71 |                         ("extended_free", ctypes.c_ulonglong)]
 72 | 
 73 |         status = Status()
 74 |         status.length = ctypes.sizeof(Status)
 75 |         if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
 76 |             return status.total / GiB
 77 |         return None
 78 |     else:
 79 |         try:                                     # Linux and macOS: page size x number of pages
 80 |             return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / GiB
 81 |         except (ValueError, OSError, AttributeError):
 82 |             return None
 83 | 
 84 | 
```
- Windows: `GlobalMemoryStatusEx` fills a `MEMORYSTATUSEX` structure (described with `ctypes`; `length` must be set
  first); `total` is the installed memory. Linux and macOS: page size × number of pages. `None` when it cannot be
  told.

```python
 85 | def recommend(memory_gb: float | None) -> CatalogModel:
 86 |     """The biggest catalog model this much memory runs well (the middle one when unknown)."""
 87 |     if memory_gb is None:
 88 |         return CATALOG[1]
 89 |     return next(model for model in CATALOG if memory_gb >= model.memory_gb)
 90 | 
 91 | 
```
```python
 92 | def catalog_entry(name: str) -> CatalogModel | None:
 93 |     """The catalog model called ``name`` (any letter case), or None."""
 94 |     return next((model for model in CATALOG if model.name == name.strip().lower()), None)
 95 | 
 96 | 
```
- `recommend`: the first (biggest) model whose memory need is met — 14 GB for the 8B model (a "16 GB" computer reports
  about 15.7), 7 GB for the 4B one; the middle model when the memory is unknown. `catalog_entry` finds a model by
  name, ignoring letter case and spaces.

```python
 97 | def installed_models(config: Config) -> list[Path]:
 98 |     """Every .gguf model where offline mode looks, plus a configured one elsewhere."""
 99 |     found: list[Path] = []
100 |     for folder in (HOME / "models", CHECKOUT / "models"):
101 |         if folder.is_dir():
102 |             found += sorted(p for p in folder.glob("*.gguf") if not p.name.lower().startswith("mmproj"))
103 |     given = Path(config.model_file).expanduser() if config.model_file else None
104 |     if given is not None and given.is_file() and given not in found:
105 |         found.insert(0, given)
106 |     return found
107 | 
108 | 
```
- Every `.gguf` in the two model folders (vision add-ons left out) plus a model configured elsewhere — what `localagent
  models` lists.

## Fetching
```python
109 | def install_model(model: CatalogModel, on_progress: Progress) -> Path:
110 |     """Fetch a catalog model into ~/.localagent/models (resumable, checksum-verified)."""
111 |     return download(model.url, HOME / "models" / model.file, model.sha256, model.size, on_progress)
112 | 
113 | 
```
- Downloads a catalog model into `%USERPROFILE%\.localagent\models`, where offline mode finds it, through
  `fetch.download` (resumable, verified).

```python
114 | def llama_variants() -> tuple[str, ...]:
115 |     """The llama.cpp builds that fit this computer, best first. Windows: the Vulkan build (uses
116 |     most graphics cards and carries the processor-only libraries too); Linux: the plain build
117 |     (the Vulkan one needs extra system libraries); macOS: its one build (Metal included)."""
118 |     arm = platform.machine().lower() in ("arm64", "aarch64")
119 |     if sys.platform == "win32":
120 |         return ("win-vulkan-arm64", "win-cpu-arm64") if arm else ("win-vulkan-x64", "win-cpu-x64")
121 |     if sys.platform == "darwin":
122 |         return ("macos-arm64",) if arm else ("macos-x64",)
123 |     return ("ubuntu-arm64",) if arm else ("ubuntu-x64", "ubuntu-vulkan-x64")
124 | 
125 | 
```
- Which llama.cpp build to fetch, best first: on Windows the Vulkan build (it uses most graphics cards and also
  carries the processor-only libraries, so it works without one too), then the processor-only build; on Linux the
  plain build first (the Vulkan one needs extra system libraries); on macOS its one build (Metal included). ARM
  computers get the ARM builds.

```python
126 | def find_llama_release(variants: tuple[str, ...]) -> tuple[str, str, int, str]:
127 |     """(file name, URL, size, sha256) of the newest llama.cpp build in ``variants``.
128 | 
129 |     The newest releases are searched because llama.cpp's "latest" release can be a placeholder
130 |     without programs (seen in Nov 2026); only a file with a published checksum is accepted.
131 |     """
132 |     releases = get_json(LLAMA_RELEASES)
133 |     for release in releases if isinstance(releases, list) else []:
134 |         tag = str(release.get("tag_name", ""))
135 |         assets = {str(a.get("name")): a for a in release.get("assets", [])}
136 |         for variant in variants:
137 |             for extension in ("zip", "tar.gz"):
138 |                 asset = assets.get(f"llama-{tag}-bin-{variant}.{extension}")
139 |                 digest = str((asset or {}).get("digest") or "")
140 |                 if asset is not None and digest.startswith("sha256:"):
141 |                     return (str(asset["name"]), str(asset["browser_download_url"]),
142 |                             int(asset["size"]), digest.removeprefix("sha256:"))
143 |     raise DownloadError("no llama.cpp build for this computer was found among the newest releases; "
144 |                         "download one by hand from https://github.com/ggml-org/llama.cpp/releases "
145 |                         "and unzip it into ~/.localagent/llama/")
146 | 
147 | 
```
- Reads the 15 newest releases and returns the first file named `llama-<tag>-bin-<variant>.zip` or `.tar.gz` **that has
  a published checksum**. Searching several releases matters: in November 2026 llama.cpp's "latest" release was a
  placeholder (`v1.0.0`, holding only a text file) while the real builds were `b12588` and so on.

```python
148 | def install_llama(on_progress: Progress, variants: tuple[str, ...] | None = None) -> Path:
149 |     """Fetch and unpack the newest llama.cpp build into ~/.localagent/llama/<build>; returns the
150 |     folder. The archive is checked against GitHub's published checksum, then deleted."""
151 |     name, url, size, sha256 = find_llama_release(variants or llama_variants())
152 |     folder = HOME / "llama" / name.removesuffix(".zip").removesuffix(".tar.gz")
153 |     archive = download(url, HOME / "llama" / "downloads" / name, sha256, size, on_progress)
154 |     extract_archive(archive, folder)
155 |     archive.unlink()
156 |     if sys.platform != "win32":                  # archives may lose the "may run" mark
157 |         for program in folder.rglob("llama-*"):
158 |             if program.is_file():
159 |                 program.chmod(program.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
160 |     return folder
161 | 
162 | 
```
- Downloads that file into `~/.localagent/llama/downloads`, unpacks it into its own folder named after the build
  (where `find_llama_server` looks — GPU builds first), deletes the archive, and on Linux/macOS marks the `llama-*`
  programs as runnable (archives can lose that mark).

## Showing progress, the sandbox hint
```python
163 | def progress_printer(say: Say = print) -> Progress:
164 |     """Shows a download's progress: in place once a second in a terminal, else every 30 s."""
165 |     started, shown = time.monotonic(), [0.0]
166 |     live = sys.stdout.isatty()
167 | 
168 |     def show(done: int, total: int | None) -> None:
169 |         now = time.monotonic()
170 |         finished = total is not None and done >= total
171 |         if now - shown[0] < (1 if live else 30) and not finished:
172 |             return
173 |         shown[0] = now
174 |         speed = done / max(now - started, 0.001)
175 |         text = f"  {done / GiB:.2f} GB"
176 |         if total:
177 |             left = (total - done) / max(speed, 1) / 60
178 |             text += f" of {total / GiB:.2f} GB ({done * 100 // total}%), about {left:.0f} min left"
179 |         text += f" at {speed / 1e6:.1f} MB/s"
180 |         if live and say is print:
181 |             print(f"\r{text:<78}", end="\n" if finished else "", flush=True)
182 |         else:
183 |             say(text)
184 |     return show
185 | 
186 | 
```
- A download's progress — `1.20 GB of 4.68 GB (25%), about 6 min left at 12.3 MB/s` — rewritten in place once a second
  in a terminal, or as a new line every 30 seconds when the output goes to a file. The last line always appears.

```python
187 | def sandbox_hint() -> str:
188 |     """The command that builds the sandbox image, with this copy's Dockerfile when it has one."""
189 |     dockerfile = CHECKOUT / "docker" / "sandbox.Dockerfile"
190 |     if dockerfile.is_file():
191 |         return (f'docker build -t localagent-sandbox:latest -f "{dockerfile}" '
192 |                 f'"{dockerfile.parent}"')
193 |     return "see docs/docker.md for the sandbox image"
194 | 
195 | 
```
- The exact `docker build` command for this copy's Dockerfile (the release bundle carries one too), or a pointer to
  the docs. Used by the setup's sandbox step.

## `localagent models`
```python
196 | def run_models(words: list[str], config: Config, say: Say = print,
197 |                settings_file: Path = USER_SETTINGS, progress: Progress | None = None) -> int:
198 |     """`localagent models` (list), `models download NAME`, `models use NAME-or-FILE`."""
199 |     if not words:
200 |         used = find_model_file(config)
201 |         installed = installed_models(config)
202 |         say("Installed models:" if installed else "No model is installed yet.")
203 |         for found in installed:
204 |             mark = "*" if used is not None and found.resolve() == used.resolve() else " "
205 |             say(f"  {mark} {found}  ({found.stat().st_size / GiB:.1f} GB)")
206 |         pick = recommend(total_memory_gb())
207 |         say("\nAvailable (localagent models download NAME):")
208 |         for entry in CATALOG:
209 |             say(f"  {entry.name:<11} {entry.size / GiB:4.1f} GB  {entry.note}"
210 |                 + ("   <- suggested for this computer" if entry == pick else ""))
211 |         if used is not None:
212 |             say("\n* = used by offline mode. Another one: localagent models use NAME-or-FILE")
213 |         return 0
214 |     action, target = words[0].lower(), " ".join(words[1:])
215 |     model = catalog_entry(target)
216 |     if action == "download":
217 |         if model is None:
218 |             say(f"unknown model '{target}'; choose one of: {', '.join(m.name for m in CATALOG)}")
219 |             return 2
220 |         try:
221 |             say(f"saved {install_model(model, progress or progress_printer(say))}")
222 |         except DownloadError as exc:
223 |             say(f"download failed: {exc}")
224 |             return 1
225 |         return 0
226 |     if action == "use":
227 |         places = [HOME / "models" / model.file, CHECKOUT / "models" / model.file] if model else \
228 |             [Path(target).expanduser()]
229 |         path = next((p for p in places if p.is_file()), None)
230 |         if path is None:
231 |             say(f"{target} is not there" + (f"; download it first: localagent models download "
232 |                                             f"{model.name}" if model else ""))
233 |             return 2
234 |         saved = save_user_env("LOCALAGENT_MODEL_FILE", str(path.resolve()), settings_file)
235 |         say(f"offline mode now uses {path} (saved in {saved})")
236 |         if os.environ.get("LOCALAGENT_MODEL_FILE"):
237 |             say("note: LOCALAGENT_MODEL_FILE is also set in your environment, and that one wins; "
238 |                 "remove it to use this choice")
239 |         return 0
240 |     say("usage: localagent models [download NAME | use NAME-or-FILE]")
241 |     return 2
```
- No words: the installed models (`*` marks the one offline mode uses) and the catalog with the suggestion for this
  computer.
- `download NAME`: fetches a catalog model with progress; a broken download says how to continue it.
- `use NAME-or-FILE`: finds the catalog file in either model folder, or takes any `.gguf` path, and saves it as
  `LOCALAGENT_MODEL_FILE` in the user settings file — warning when a real environment variable of that name would
  still win.
- Anything else prints the usage line (exit code 2).
