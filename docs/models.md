# `localagent/models.py` — local models and the guided first run

**Role:** what turns a fresh download into a working setup without reading any guide. Two commands:

```
localagent setup                      the guided first run (about a minute, plus downloads)
localagent models                     installed models, the catalog, the suggestion for this computer
localagent models download qwen3-4b   fetch a catalog model (resumable, verified)
localagent models use qwen3-4b        make it the model offline mode uses (also a path to any .gguf)
```

`localagent setup`, as it looks on this laptop (everything already present, so nothing to download):
```
1/3  Online model: Gemini (fast, a free daily quota)
  ok    your GEMINI_API_KEY works
2/3  Offline model: runs on this computer (private, no quota, slower)
  This computer has 15.7 GB of memory: the suggested model is qwen3-8b (4.7 GB download) - ...
  ok    llama.cpp found: ...\bin\llamacpp-vulkan\llama-server.exe
  ok    model found: ...\models\Qwen3-8B-Q4_K_M.gguf
3/3  Sandbox: Docker runs the agent's code in a sealed box (optional)
  note  Docker is not running. ...
Ready, online and offline. In a project folder, type: localagent   (/model switches)
```
On a new computer step 1 asks for the key (hidden while typed, checked with Google before it is saved) and step 2
offers to download llama.cpp and the suggested model.

## Module header
```python
  1 | """Local models and the guided first run: which model suits this computer, which are installed,
  2 | and fetching llama.cpp and a model (resumable, checked against the publisher's checksum).
  3 | 
  4 |   localagent setup                      the guided first run: Gemini key, offline model, Docker
  5 |   localagent models                     installed models, the catalog, the recommendation
  6 |   localagent models download qwen3-4b   fetch a catalog model (about 2.5 GB)
  7 |   localagent models use qwen3-4b        make it the model offline mode uses
  8 | """
  9 | from __future__ import annotations
 10 | 
 11 | import ctypes
 12 | import getpass
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
 24 | from .localserver import CHECKOUT, HOME, find_llama_server, find_model_file
 25 | from .runners import docker_available, sandbox_image_ready
 26 | from .settings import USER_SETTINGS, save_user_env
 27 | 
 28 | LLAMA_RELEASES = "https://api.github.com/repos/ggml-org/llama.cpp/releases?per_page=15"
 29 | KEY_PAGE = "https://aistudio.google.com/apikey"
 30 | GiB = 1024 ** 3
 31 | Say = Callable[[str], None]
 32 | 
 33 | 
```
- Imports: `ctypes` (memory size on Windows), `getpass` (typing the key hidden), `os`, `platform` (processor type),
  `stat` (making downloaded programs runnable on Linux/macOS), `sys`, `time`, `dataclass`, `Path`, `Callable`, and
  from the agent: the config, the download functions, where models live (`HOME`, `CHECKOUT`, the finders), the Docker
  checks and `save_user_env`.
- `LLAMA_RELEASES` is GitHub's list of llama.cpp's newest releases; `KEY_PAGE` where Gemini keys are made. `GiB` is
  1024³ bytes — the unit Windows itself shows as "GB" (Task Manager's 15.7 GB). `Say` is the type of the printing
  function (tests collect the lines instead).

## The catalog
```python
 34 | @dataclass(frozen=True)
 35 | class CatalogModel:
 36 |     """A local model the agent knows how to fetch and check."""
 37 | 
 38 |     name: str            # what you type: `localagent models download qwen3-4b`
 39 |     repo: str            # the Hugging Face repository it comes from
 40 |     file: str            # the .gguf file in that repository
 41 |     size: int            # bytes, as published
 42 |     sha256: str          # checksum, as published (Hugging Face's large-file record)
 43 |     memory_gb: int       # computers with at least this much memory get it recommended
 44 |     note: str            # one line for the list
 45 | 
```
```python
 46 |     @property
 47 |     def url(self) -> str:
 48 |         """Where Hugging Face serves the file."""
 49 |         return f"https://huggingface.co/{self.repo}/resolve/main/{self.file}"
 50 | 
 51 | 
```
- One model the agent can fetch: its short name, Hugging Face repository and file, the **published size and SHA-256**
  (copied from Hugging Face's API on 20 Nov 2026; the 8B size matched the copy on this laptop byte for byte), the
  memory a computer needs to get it suggested, and one line for the list. `url` is Hugging Face's download address.

```python
 52 | CATALOG = (   # biggest first; sizes and checksums from Hugging Face's API (20 Nov 2026)
 53 |     CatalogModel("qwen3-8b", "Qwen/Qwen3-8B-GGUF", "Qwen3-8B-Q4_K_M.gguf", 5_027_783_488,
 54 |                  "d98cdcbd03e17ce47681435b5150e34c1417f50b5c0019dd560e4882c5745785", 14,
 55 |                  "the best that fits 16 GB of memory (12/15 on the evals)"),
 56 |     CatalogModel("qwen3-4b", "Qwen/Qwen3-4B-GGUF", "Qwen3-4B-Q4_K_M.gguf", 2_497_280_256,
 57 |                  "7485fe6f11af29433bc51cab58009521f205840f5b4ae3a32fa7f92e8534fdf5", 7,
 58 |                  "for 8 GB computers: twice as fast, weaker on bigger tasks"),
 59 |     CatalogModel("qwen3-1.7b", "Qwen/Qwen3-1.7B-GGUF", "Qwen3-1.7B-Q8_0.gguf", 1_834_426_016,
 60 |                  "061b54daade076b5d3362dac252678d17da8c68f07560be70818cace6590cb1a", 0,
 61 |                  "for small computers: questions and very small tasks only"),
 62 | )
 63 | 
 64 | 
```
- `CATALOG`, biggest first: Qwen3-8B (4-bit, 4.7 GB) for 16 GB computers — the model measured in the evals; Qwen3-4B
  (2.3 GB) for 8 GB; Qwen3-1.7B (8-bit, 1.7 GB) for smaller ones, honestly labelled as good for questions and very
  small tasks only. All three come from Qwen's own repositories and support tool calls.

## Choosing
```python
 65 | def total_memory_gb() -> float | None:
 66 |     """This computer's memory in GB, or None when it cannot be told."""
 67 |     if sys.platform == "win32":
 68 |         class Status(ctypes.Structure):          # Windows' MEMORYSTATUSEX
 69 |             _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong),
 70 |                         ("total", ctypes.c_ulonglong), ("available", ctypes.c_ulonglong),
 71 |                         ("page_total", ctypes.c_ulonglong), ("page_free", ctypes.c_ulonglong),
 72 |                         ("virtual_total", ctypes.c_ulonglong), ("virtual_free", ctypes.c_ulonglong),
 73 |                         ("extended_free", ctypes.c_ulonglong)]
 74 | 
 75 |         status = Status()
 76 |         status.length = ctypes.sizeof(Status)
 77 |         if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
 78 |             return status.total / GiB
 79 |         return None
 80 |     else:
 81 |         try:                                     # Linux and macOS: page size x number of pages
 82 |             return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / GiB
 83 |         except (ValueError, OSError, AttributeError):
 84 |             return None
 85 | 
 86 | 
```
- Windows: `GlobalMemoryStatusEx` fills a `MEMORYSTATUSEX` structure (described with `ctypes`; `length` must be set
  first); `total` is the installed memory. Linux and macOS: page size × number of pages. `None` when it cannot be
  told.

```python
 87 | def recommend(memory_gb: float | None) -> CatalogModel:
 88 |     """The biggest catalog model this much memory runs well (the middle one when unknown)."""
 89 |     if memory_gb is None:
 90 |         return CATALOG[1]
 91 |     return next(model for model in CATALOG if memory_gb >= model.memory_gb)
 92 | 
 93 | 
```
```python
 94 | def catalog_entry(name: str) -> CatalogModel | None:
 95 |     """The catalog model called ``name`` (any letter case), or None."""
 96 |     return next((model for model in CATALOG if model.name == name.strip().lower()), None)
 97 | 
 98 | 
```
- `recommend`: the first (biggest) model whose memory need is met — 14 GB for the 8B model (a "16 GB" computer reports
  about 15.7), 7 GB for the 4B one; the middle model when the memory is unknown. `catalog_entry` finds a model by
  name, ignoring letter case and spaces.

```python
 99 | def installed_models(config: Config) -> list[Path]:
100 |     """Every .gguf model where offline mode looks, plus a configured one elsewhere."""
101 |     found: list[Path] = []
102 |     for folder in (HOME / "models", CHECKOUT / "models"):
103 |         if folder.is_dir():
104 |             found += sorted(p for p in folder.glob("*.gguf") if not p.name.lower().startswith("mmproj"))
105 |     given = Path(config.model_file).expanduser() if config.model_file else None
106 |     if given is not None and given.is_file() and given not in found:
107 |         found.insert(0, given)
108 |     return found
109 | 
110 | 
```
- Every `.gguf` in the two model folders (vision add-ons left out) plus a model configured elsewhere — what `localagent
  models` lists.

## Fetching
```python
111 | def install_model(model: CatalogModel, on_progress: Progress) -> Path:
112 |     """Fetch a catalog model into ~/.localagent/models (resumable, checksum-verified)."""
113 |     return download(model.url, HOME / "models" / model.file, model.sha256, model.size, on_progress)
114 | 
115 | 
```
- Downloads a catalog model into `%USERPROFILE%\.localagent\models`, where offline mode finds it, through
  `fetch.download` (resumable, verified).

```python
116 | def llama_variants() -> tuple[str, ...]:
117 |     """The llama.cpp builds that fit this computer, best first. Windows: the Vulkan build (uses
118 |     most graphics cards and carries the processor-only libraries too); Linux: the plain build
119 |     (the Vulkan one needs extra system libraries); macOS: its one build (Metal included)."""
120 |     arm = platform.machine().lower() in ("arm64", "aarch64")
121 |     if sys.platform == "win32":
122 |         return ("win-vulkan-arm64", "win-cpu-arm64") if arm else ("win-vulkan-x64", "win-cpu-x64")
123 |     if sys.platform == "darwin":
124 |         return ("macos-arm64",) if arm else ("macos-x64",)
125 |     return ("ubuntu-arm64",) if arm else ("ubuntu-x64", "ubuntu-vulkan-x64")
126 | 
127 | 
```
- Which llama.cpp build to fetch, best first: on Windows the Vulkan build (it uses most graphics cards and also
  carries the processor-only libraries, so it works without one too), then the processor-only build; on Linux the
  plain build first (the Vulkan one needs extra system libraries); on macOS its one build (Metal included). ARM
  computers get the ARM builds.

```python
128 | def find_llama_release(variants: tuple[str, ...]) -> tuple[str, str, int, str]:
129 |     """(file name, URL, size, sha256) of the newest llama.cpp build in ``variants``.
130 | 
131 |     The newest releases are searched because llama.cpp's "latest" release can be a placeholder
132 |     without programs (seen in Nov 2026); only a file with a published checksum is accepted.
133 |     """
134 |     releases = get_json(LLAMA_RELEASES)
135 |     for release in releases if isinstance(releases, list) else []:
136 |         tag = str(release.get("tag_name", ""))
137 |         assets = {str(a.get("name")): a for a in release.get("assets", [])}
138 |         for variant in variants:
139 |             for extension in ("zip", "tar.gz"):
140 |                 asset = assets.get(f"llama-{tag}-bin-{variant}.{extension}")
141 |                 digest = str((asset or {}).get("digest") or "")
142 |                 if asset is not None and digest.startswith("sha256:"):
143 |                     return (str(asset["name"]), str(asset["browser_download_url"]),
144 |                             int(asset["size"]), digest.removeprefix("sha256:"))
145 |     raise DownloadError("no llama.cpp build for this computer was found among the newest releases; "
146 |                         "download one by hand from https://github.com/ggml-org/llama.cpp/releases "
147 |                         "and unzip it into ~/.localagent/llama/")
148 | 
149 | 
```
- Reads the 15 newest releases and returns the first file named `llama-<tag>-bin-<variant>.zip` or `.tar.gz` **that has
  a published checksum**. Searching several releases matters: in November 2026 llama.cpp's "latest" release was a
  placeholder (`v1.0.0`, holding only a text file) while the real builds were `b12588` and so on.

```python
150 | def install_llama(on_progress: Progress, variants: tuple[str, ...] | None = None) -> Path:
151 |     """Fetch and unpack the newest llama.cpp build into ~/.localagent/llama/<build>; returns the
152 |     folder. The archive is checked against GitHub's published checksum, then deleted."""
153 |     name, url, size, sha256 = find_llama_release(variants or llama_variants())
154 |     folder = HOME / "llama" / name.removesuffix(".zip").removesuffix(".tar.gz")
155 |     archive = download(url, HOME / "llama" / "downloads" / name, sha256, size, on_progress)
156 |     extract_archive(archive, folder)
157 |     archive.unlink()
158 |     if sys.platform != "win32":                  # archives may lose the "may run" mark
159 |         for program in folder.rglob("llama-*"):
160 |             if program.is_file():
161 |                 program.chmod(program.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
162 |     return folder
163 | 
164 | 
```
- Downloads that file into `~/.localagent/llama/downloads`, unpacks it into its own folder named after the build
  (where `find_llama_server` looks — GPU builds first), deletes the archive, and on Linux/macOS marks the `llama-*`
  programs as runnable (archives can lose that mark).

## Checking a key, showing progress
```python
165 | def check_gemini_key(config: Config, key: str) -> str:
166 |     """"ok", "rejected" or "unreachable" for a Gemini key (listing models costs no quota)."""
167 |     try:
168 |         get_json(config.gemini_base + "/models", {"Authorization": f"Bearer {key}"}, timeout=15)
169 |     except DownloadError as exc:
170 |         return "rejected" if "HTTP 4" in str(exc) else "unreachable"
171 |     return "ok"
172 | 
173 | 
```
- Lists Gemini's models with the key: costs no quota. An HTTP 4xx answer means the key was rejected; no answer means
  Google cannot be reached right now (the key is then saved anyway, unchecked).

```python
174 | def progress_printer(say: Say = print) -> Progress:
175 |     """Shows a download's progress: in place once a second in a terminal, else every 30 s."""
176 |     started, shown = time.monotonic(), [0.0]
177 |     live = sys.stdout.isatty()
178 | 
179 |     def show(done: int, total: int | None) -> None:
180 |         now = time.monotonic()
181 |         finished = total is not None and done >= total
182 |         if now - shown[0] < (1 if live else 30) and not finished:
183 |             return
184 |         shown[0] = now
185 |         speed = done / max(now - started, 0.001)
186 |         text = f"  {done / GiB:.2f} GB"
187 |         if total:
188 |             left = (total - done) / max(speed, 1) / 60
189 |             text += f" of {total / GiB:.2f} GB ({done * 100 // total}%), about {left:.0f} min left"
190 |         text += f" at {speed / 1e6:.1f} MB/s"
191 |         if live and say is print:
192 |             print(f"\r{text:<78}", end="\n" if finished else "", flush=True)
193 |         else:
194 |             say(text)
195 |     return show
196 | 
197 | 
```
- A download's progress — `1.20 GB of 4.68 GB (25%), about 6 min left at 12.3 MB/s` — rewritten in place once a second
  in a terminal, or as a new line every 30 seconds when the output goes to a file. The last line always appears.

```python
198 | def _sandbox_hint() -> str:
199 |     """The command that builds the sandbox image, with this copy's Dockerfile when it has one."""
200 |     dockerfile = CHECKOUT / "docker" / "sandbox.Dockerfile"
201 |     if dockerfile.is_file():
202 |         return (f'docker build -t localagent-sandbox:latest -f "{dockerfile}" '
203 |                 f'"{dockerfile.parent}"')
204 |     return "see docs/docker.md for the sandbox image"
205 | 
206 | 
```
- The exact `docker build` command for this copy's Dockerfile (the release bundle carries one too), or a pointer to
  the docs.

## `localagent setup`
```python
207 | def run_setup(config: Config, ask: Callable[[str], str] = input,
208 |               secret: Callable[[str], str] = getpass.getpass, say: Say = print,
209 |               settings_file: Path = USER_SETTINGS, progress: Progress | None = None) -> int:
210 |     """`localagent setup`: the guided first run. Returns 0 when at least one model is ready."""
211 |     def yes(question: str) -> bool:
212 |         try:
213 |             answer = ask(question).strip().lower()
214 |         except (EOFError, KeyboardInterrupt):
215 |             return False
216 |         return answer in ("", "y", "yes")                # Enter accepts the suggestion
217 | 
218 |     say("localagent setup: three short steps; Enter accepts the suggestion in [brackets].")
219 |     say("\n1/3  Online model: Gemini (fast, a free daily quota)")
220 |     online = _setup_gemini(config, secret, say, settings_file)
221 |     say("\n2/3  Offline model: runs on this computer (private, no quota, slower)")
222 |     offline = _setup_local(config, yes, say, progress or progress_printer(say))
223 |     say("\n3/3  Sandbox: Docker runs the agent's code in a sealed box (optional)")
224 |     if not docker_available():
225 |         say("  note  Docker is not running. Install Docker Desktop (docker.com) and start it - or work "
226 |             "without it: code then runs on this computer, after your approval.")
227 |     elif sandbox_image_ready(config.sandbox_image):
228 |         say("  ok    Docker and the sandbox image are ready")
229 |     else:
230 |         say(f"  note  Docker runs; build the sandbox image once with:\n        {_sandbox_hint()}")
231 |     say("")
232 |     if online and offline:
233 |         say("Ready, online and offline. In a project folder, type: localagent   (/model switches)")
234 |     elif online:
235 |         say("Ready (online only). For offline work, run localagent setup again later.")
236 |     elif offline:
237 |         say("Ready (offline only). In a project folder, type: localagent --offline")
238 |     else:
239 |         say("No model is ready yet. Run localagent setup again (a Gemini key is the quickest start).")
240 |     say("Check everything at any time with: localagent --doctor")
241 |     return 0 if online or offline else 1
242 | 
243 | 
```
- The three steps and a summary. `yes` treats Enter as "yes" (the suggestion in brackets) and no input or Ctrl+C as
  "no". Returns 0 when at least one model — online or offline — is ready, else 1. Every input and output is
  injectable, so the tests drive it without a keyboard.

```python
244 | def _setup_gemini(config: Config, secret: Callable[[str], str], say: Say,
245 |                   settings_file: Path) -> bool:
246 |     """Step 1: check the Gemini key, or ask for one and save it. True when online can work."""
247 |     if config.gemini_api_key:
248 |         state = check_gemini_key(config, config.gemini_api_key)
249 |         say({"ok": "  ok    your GEMINI_API_KEY works",
250 |              "rejected": f"  FAIL  your GEMINI_API_KEY was rejected; create a new one at {KEY_PAGE}",
251 |              "unreachable": "  note  a key is set, but Google cannot be reached right now (offline?)"}[state])
252 |         return state != "rejected"
253 |     say(f"  Get a free key at {KEY_PAGE} (sign in with Google, then 'Create API key').")
254 |     try:
255 |         key = secret("  Paste the key (it stays hidden; Enter skips): ").strip()
256 |     except (EOFError, KeyboardInterrupt):
257 |         key = ""
258 |     if not key:
259 |         say("  skipped: without a key the agent works offline only")
260 |         return False
261 |     state = check_gemini_key(config, key)
262 |     if state == "rejected":
263 |         say("  FAIL  Google rejected that key; nothing was saved. Run setup again with a new key.")
264 |         return False
265 |     path = save_user_env("GEMINI_API_KEY", key, settings_file)
266 |     os.environ["GEMINI_API_KEY"] = key
267 |     say(f"  ok    key saved in {path} (only your user account can read that folder)"
268 |         + ("" if state == "ok" else "; not checked, Google cannot be reached right now"))
269 |     return True
270 | 
271 | 
```
- With a key already set: checks it and says what it found. Without one: says where to get one, asks for it hidden
  (`getpass`), checks it, and only if Google does not reject it saves it in your **user** settings file
  (`%USERPROFILE%\.localagent\settings.json`, under `env`) and in this run's environment. The folder belongs to your
  account; the key is never written into a project.

```python
272 | def _setup_local(config: Config, yes: Callable[[str], bool], say: Say, progress: Progress) -> bool:
273 |     """Step 2: find or fetch llama.cpp and a model sized for this computer. True when ready."""
274 |     memory = total_memory_gb()
275 |     pick = recommend(memory)
276 |     size = f"{memory:.1f} GB of memory" if memory else "an unknown amount of memory"
277 |     say(f"  This computer has {size}: the suggested model is {pick.name} "
278 |         f"({pick.size / GiB:.1f} GB download) - {pick.note}")
279 |     program = find_llama_server(config)
280 |     if program is not None:
281 |         say(f"  ok    llama.cpp found: {program}")
282 |     elif yes("  llama.cpp, the program that runs the model (25-40 MB), is missing. Download it? [Y/n] "):
283 |         try:
284 |             say(f"  ok    llama.cpp installed in {install_llama(progress)}")
285 |             program = find_llama_server(config)
286 |         except DownloadError as exc:
287 |             say(f"  FAIL  {exc}")
288 |     model = find_model_file(config)
289 |     if model is not None:
290 |         say(f"  ok    model found: {model}")
291 |     elif yes(f"  No model yet. Download {pick.name} ({pick.size / GiB:.1f} GB; it can be stopped and "
292 |              "continued later)? [Y/n] "):
293 |         try:
294 |             model = install_model(pick, progress)
295 |             say(f"  ok    model saved: {model}")
296 |         except DownloadError as exc:
297 |             say(f"  FAIL  {exc}")
298 |     return program is not None and model is not None
299 | 
300 | 
```
- Shows the computer's memory and the suggested model, then for llama.cpp and for the model: found → ok; missing →
  offers the download (Enter = yes) and reports the result. A failed download is reported and setup carries on.

## `localagent models`
```python
301 | def run_models(words: list[str], config: Config, say: Say = print,
302 |                settings_file: Path = USER_SETTINGS, progress: Progress | None = None) -> int:
303 |     """`localagent models` (list), `models download NAME`, `models use NAME-or-FILE`."""
304 |     if not words:
305 |         used = find_model_file(config)
306 |         installed = installed_models(config)
307 |         say("Installed models:" if installed else "No model is installed yet.")
308 |         for found in installed:
309 |             mark = "*" if used is not None and found.resolve() == used.resolve() else " "
310 |             say(f"  {mark} {found}  ({found.stat().st_size / GiB:.1f} GB)")
311 |         pick = recommend(total_memory_gb())
312 |         say("\nAvailable (localagent models download NAME):")
313 |         for entry in CATALOG:
314 |             say(f"  {entry.name:<11} {entry.size / GiB:4.1f} GB  {entry.note}"
315 |                 + ("   <- suggested for this computer" if entry == pick else ""))
316 |         if used is not None:
317 |             say("\n* = used by offline mode. Another one: localagent models use NAME-or-FILE")
318 |         return 0
319 |     action, target = words[0].lower(), " ".join(words[1:])
320 |     model = catalog_entry(target)
321 |     if action == "download":
322 |         if model is None:
323 |             say(f"unknown model '{target}'; choose one of: {', '.join(m.name for m in CATALOG)}")
324 |             return 2
325 |         try:
326 |             say(f"saved {install_model(model, progress or progress_printer(say))}")
327 |         except DownloadError as exc:
328 |             say(f"download failed: {exc}")
329 |             return 1
330 |         return 0
331 |     if action == "use":
332 |         places = [HOME / "models" / model.file, CHECKOUT / "models" / model.file] if model else \
333 |             [Path(target).expanduser()]
334 |         path = next((p for p in places if p.is_file()), None)
335 |         if path is None:
336 |             say(f"{target} is not there" + (f"; download it first: localagent models download "
337 |                                             f"{model.name}" if model else ""))
338 |             return 2
339 |         saved = save_user_env("LOCALAGENT_MODEL_FILE", str(path.resolve()), settings_file)
340 |         say(f"offline mode now uses {path} (saved in {saved})")
341 |         if os.environ.get("LOCALAGENT_MODEL_FILE"):
342 |             say("note: LOCALAGENT_MODEL_FILE is also set in your environment, and that one wins; "
343 |                 "remove it to use this choice")
344 |         return 0
345 |     say("usage: localagent models [download NAME | use NAME-or-FILE]")
346 |     return 2
```
- No words: the installed models (`*` marks the one offline mode uses) and the catalog with the suggestion for this
  computer.
- `download NAME`: fetches a catalog model with progress; a broken download says how to continue it.
- `use NAME-or-FILE`: finds the catalog file in either model folder, or takes any `.gguf` path, and saves it as
  `LOCALAGENT_MODEL_FILE` in the user settings file — warning when a real environment variable of that name would
  still win.
- Anything else prints the usage line (exit code 2).
