# `tools/build_release.py` — building the download files

**Role:** turns the project into the files a website offers for download. One command, run from the project folder:
```
.venv\Scripts\python tools\build_release.py
     0.1 MB  dist\localagent-0.6.0-py3-none-any.whl       pip/pipx, any system with Python 3.10+
    35.7 MB  dist\localagent-0.6.0-windows-x64.zip        portable: nothing needs to be installed
     0.0 MB  dist\SHA256SUMS.txt                          checksums to publish next to them
```
**The Windows zip** holds everything: Python itself (the official "embeddable" Python 3.14.10 from python.org, about
12 MB), the agent, ruff (its lint layer), launchers and two double-clickable helpers. A user
unzips it, double-clicks `install.cmd` (it adds the folder to their PATH and starts the guided setup right there),
opens a new terminal and types
`localagent setup`. Tested exactly like that (20 Nov 2026): unzipped into a fresh folder, `--version`, the bundled
Python found both checkers, broken code was rejected, `--doctor` reported correctly, and a real request was answered
in 1 call.

```
localagent-0.6.0-windows-x64\
  localagent.cmd        runs python\python.exe -m localagent with your arguments
  install.cmd/.ps1      adds this folder to YOUR user PATH (nothing else changes), then runs the setup
  uninstall.cmd/.ps1    removes it again
  README.txt            the four steps above
  LICENSE, THIRD_PARTY_NOTICES.md
  python\               embeddable Python 3.14.10 (its python314._pth also lists ..\lib)
  lib\                  localagent, ruff, and docker\ (for the sandbox image)
```

## Publishing a release (`.github/workflows/release.yml`)
Raise the version in `localagent/__init__.py`, write its section in `CHANGELOG.md`, merge, then push a tag:
```
git tag v0.6.0
git push origin v0.6.0
```
The tag starts the release workflow on a clean Windows machine of GitHub's: it checks out that exact commit, installs
Python 3.14, runs `python tools/build_release.py --tag v0.6.0` and publishes a GitHub release named after the tag,
with the release notes and every file in `dist\` attached. The workflow may only write releases
(`permissions: contents: write`); its token is GitHub's own and expires with the run. The same files can be put on any
website: `tools/build_site.py` (see `build_site.md`) builds a complete, self-contained download site from them.

## Module header
```python
  1 | """Build the release files: a wheel for pip/pipx, and a portable Windows zip that needs nothing
  2 | installed (it carries its own Python).
  3 | 
  4 | Usage (from the project folder, with the project's .venv):
  5 |     python tools/build_release.py              # writes dist/ and prints what it made
  6 |     python tools/build_release.py --tag v0.6.0 # the same, after checking the tag is this version
  7 |     python tools/build_release.py --with-mypy  # the Windows zip with mypy too (about twice the size)
  8 | 
  9 | dist/localagent-<version>-py3-none-any.whl     pip install / pipx install, any system
 10 | dist/localagent-<version>-windows-x64.zip      unzip, double-click install.cmd, done
 11 | dist/SHA256SUMS.txt                            checksums to publish next to the downloads
 12 | build/RELEASE_NOTES.md                         this version's CHANGELOG section, for the release page
 13 | The embeddable Python is downloaded once (checksum-pinned) into build/cache/.
 14 | """
 15 | from __future__ import annotations
 16 | 
 17 | import argparse
 18 | import hashlib
 19 | import shutil
 20 | import subprocess
 21 | import sys
 22 | import zipfile
 23 | from pathlib import Path
 24 | 
 25 | ROOT = Path(__file__).resolve().parent.parent
 26 | sys.path.insert(0, str(ROOT))
 27 | from localagent import __version__  # noqa: E402  (after the path is set)
 28 | from localagent.fetch import download, extract_archive  # noqa: E402
 29 | 
 30 | PYTHON_VERSION = "3.14.10"                     # python.org's newest 3.14 with a Windows embeddable zip
 31 | PYTHON_URL = f"https://www.python.org/ftp/python/{PYTHON_VERSION}/python-{PYTHON_VERSION}-embed-amd64.zip"
 32 | PYTHON_SHA256 = "90905e64a840557d8c1bc4f5b74db3d7ee85e3ffb217611cac1c7813acea4159"   # from python.org
 33 | PYTHON_SIZE = 12_619_664
 34 | LAUNCHER = '@"%~dp0python\\python.exe" -m localagent %*\r\n'
 35 | INSTALL_CMD = '@powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1"\r\n@pause\r\n'
 36 | UNINSTALL_CMD = '@powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0uninstall.ps1"\r\n@pause\r\n'
 37 | INSTALL_PS1 = """# Adds this folder to YOUR user PATH, so `localagent` works in every new terminal.
 38 | # Nothing else on the computer is changed. Undo: uninstall.cmd
 39 | $here = $PSScriptRoot
 40 | $path = [Environment]::GetEnvironmentVariable("Path", "User")
 41 | if (-not $path) { $path = "" }
 42 | if (($path -split ";") -contains $here) {
 43 |     Write-Host "localagent is already on your PATH ($here)."
 44 | } else {
 45 |     $new = (($path.TrimEnd(";") + ";" + $here).TrimStart(";"))
 46 |     [Environment]::SetEnvironmentVariable("Path", $new, "User")
 47 |     Write-Host "Added $here to your PATH."
 48 | }
 49 | Write-Host ""
 50 | Write-Host "Next, the guided setup (about a minute; run it again any time with: localagent setup)"
 51 | & "$here\\python\\python.exe" -m localagent setup
 52 | Write-Host ""
 53 | Write-Host "Done. Open a NEW terminal in a project folder and type:  localagent"
 54 | """
 55 | UNINSTALL_PS1 = """# Removes this folder from YOUR user PATH. Then delete the folder itself.
 56 | # Your settings, logs and downloaded models stay in %USERPROFILE%\\.localagent (delete it too to remove them).
 57 | $here = $PSScriptRoot
 58 | $path = [Environment]::GetEnvironmentVariable("Path", "User")
 59 | if (-not $path) { $path = "" }
 60 | $kept = ($path -split ";" | Where-Object { $_ -and $_ -ne $here }) -join ";"
 61 | [Environment]::SetEnvironmentVariable("Path", $kept, "User")
 62 | Write-Host "Removed $here from your PATH. You can now delete this folder."
 63 | """
 64 | README_TXT = """localagent {version} for Windows (64-bit), portable
 65 | 
 66 | A coding agent for your terminal: online with Google Gemini (free key) or your own key for
 67 | OpenAI, Anthropic, OpenRouter, Groq, Mistral, DeepSeek; offline with a model on your PC.
 68 | 
 69 | 1. Unzip this folder somewhere permanent, for example C:\\Tools\\localagent (not Downloads).
 70 | 2. Double-click install.cmd once. It adds this folder to your PATH (only for your account)
 71 |    and starts the guided setup: an online service and key, and/or a model on this PC
 72 |    (one you already run - Ollama, LM Studio - or a free one it downloads for you).
 73 | 3. Open a NEW terminal (PowerShell) in a project folder and type:   localagent
 74 | 4. Change the setup any time with:   localagent setup
 75 | 
 76 | Python {python} is included; nothing else has to be installed. Docker Desktop is optional (a sealed
 77 | box for running code). Check the setup at any time:  localagent --doctor
 78 | Type checks (--type-check) need mypy, which this download leaves out to stay small.
 79 | Uninstall: double-click uninstall.cmd, then delete this folder (and %USERPROFILE%\\.localagent).
 80 | License: MIT (LICENSE). Included third-party software: THIRD_PARTY_NOTICES.md.
 81 | """
 82 | 
 83 | 
```
- `ROOT` is the project folder, put on the import path so the script can use the agent's own `__version__` and its
  verified download (`fetch.download`) — the build uses the same trust rules as the product.
- `PYTHON_VERSION`, `PYTHON_URL`, `PYTHON_SHA256`, `PYTHON_SIZE`: the embeddable Python, **pinned with python.org's
  published checksum**. 3.14 was chosen because python.org publishes embeddable zips only for versions still
  receiving bug fixes (3.12.16 and 3.13.18 were source-only security releases); it is also what CI tests and what
  this computer uses.
- `LAUNCHER`: `%~dp0` is the folder the `.cmd` file is in, so the launcher works wherever the folder is unzipped (also
  in paths with spaces, thanks to the quotes); `%*` passes all arguments on.
- `INSTALL_CMD`/`UNINSTALL_CMD`: double-clickable wrappers that run the PowerShell scripts with `-ExecutionPolicy
  Bypass` (scripts from the internet are blocked by default) and pause so the message can be read.
- `INSTALL_PS1` adds the folder to the user PATH only when missing, then starts `localagent setup` with the bundled
  Python (named by its full path: the new PATH only reaches terminals opened later), so a double-click ends with a
  working agent; `UNINSTALL_PS1` removes exactly that entry. Both
  touch only the current user's PATH — no administrator rights needed.
- `README_TXT` is the four-step guide inside the zip; it names every kind of brain the setup offers.

## Steps
```python
 84 | def run(*argv: str) -> None:
 85 |     """Run a build step; stop the build with its output when it fails."""
 86 |     done = subprocess.run(argv, capture_output=True, text=True)
 87 |     if done.returncode != 0:
 88 |         raise SystemExit(f"failed: {' '.join(argv)}\n{done.stdout}{done.stderr}")
 89 | 
 90 | 
```
- Runs one build step and stops with its output if it fails.

```python
 91 | def build_wheel(dist: Path) -> Path:
 92 |     """The pure-Python wheel (works on every system with Python 3.10+)."""
 93 |     run(sys.executable, "-m", "pip", "wheel", str(ROOT), "--no-deps", "-q", "-w", str(dist))
 94 |     return dist / f"localagent-{__version__}-py3-none-any.whl"
 95 | 
 96 | 
```
- `pip wheel` builds the standard package file from `pyproject.toml` (no dependencies to include: the agent uses only
  the standard library).

```python
 97 | def add_lib_to_pth(python_dir: Path) -> None:
 98 |     """The embeddable Python finds modules only where its ``python3xx._pth`` file says; add
 99 |     ``..\\lib`` (relative to that file), where the agent and its checkers are installed."""
100 |     pth = next(python_dir.glob("python3*._pth"))
101 |     lines = pth.read_text(encoding="utf-8").splitlines()
102 |     if "..\\lib" not in lines:
103 |         lines.insert(lines.index(".") + 1 if "." in lines else 0, "..\\lib")
104 |     pth.write_text("\n".join(lines) + "\n", encoding="utf-8")
105 | 
106 | 
```
- The embeddable Python ignores `PYTHONPATH` and the registry and imports only from the folders its `python3xx._pth`
  file lists — which keeps it isolated from whatever else is installed. Adding `..\lib` (relative to that file) lets it
  find the agent.

```python
107 | def build_windows_bundle(wheel: Path, build: Path, dist: Path, with_mypy: bool = False) -> Path:
108 |     """The portable folder (embedded Python + agent + ruff + launchers), zipped; mypy only when
109 |     asked for - it would double the download and is used only with --type-check."""
110 |     name = f"localagent-{__version__}-windows-x64"
111 |     stage = build / name
112 |     if stage.exists():
113 |         shutil.rmtree(stage)
114 |     python_zip = download(PYTHON_URL, build / "cache" / Path(PYTHON_URL).name, PYTHON_SHA256,
115 |                           PYTHON_SIZE)
116 |     extract_archive(python_zip, stage / "python")
117 |     add_lib_to_pth(stage / "python")
118 |     tools = ["ruff", "mypy"] if with_mypy else ["ruff"]
119 |     run(sys.executable, "-m", "pip", "install", "-q", "--target", str(stage / "lib"),
120 |         "--python-version", PYTHON_VERSION.rsplit(".", 1)[0], "--only-binary=:all:",
121 |         "--platform", "win_amd64", str(wheel), *tools)
122 |     shutil.copytree(ROOT / "docker", stage / "lib" / "docker")   # for the sandbox-image hint
123 |     files = {"localagent.cmd": LAUNCHER, "install.cmd": INSTALL_CMD, "uninstall.cmd": UNINSTALL_CMD,
124 |              "install.ps1": INSTALL_PS1, "uninstall.ps1": UNINSTALL_PS1,
125 |              "README.txt": README_TXT.format(version=__version__, python=PYTHON_VERSION)}
126 |     for file_name, text in files.items():
127 |         (stage / file_name).write_bytes(text.replace("\r\n", "\n").replace("\n", "\r\n").encode("utf-8"))
128 |     for legal in ("LICENSE", "THIRD_PARTY_NOTICES.md"):
129 |         shutil.copy2(ROOT / legal, stage / legal)
130 |     archive = dist / f"{name}.zip"
131 |     with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as bundle:
132 |         for path in sorted(stage.rglob("*")):
133 |             if path.is_file() and "__pycache__" not in path.parts:
134 |                 bundle.write(path, Path(name) / path.relative_to(stage))
135 |     return archive
136 | 
137 | 
```
- Starts from an empty staging folder in `build\`. Downloads the embeddable Python (verified, cached in
  `build\cache\`) and unpacks it into `python\`.
- `pip install --target lib` installs the wheel and ruff into `lib\` — and mypy only with `--with-mypy`: mypy and its
  compiled parts are about 48 MB unpacked, which doubled the download (35.7 MB with it), yet it runs only with
  `--type-check`. Without it the zip is about half the size and fits hosts that limit a file to 25 MB. `--platform win_amd64
  --python-version 3.14 --only-binary=:all:` makes pip choose the files for the **bundled** Python, not for the one
  running the build. ruff finds its program in `lib\bin\` (it looks next to its package for exactly this kind of
  install).
- Copies the Dockerfile folder (for the sandbox-image hint), writes the launchers and texts with Windows line breaks
  (`.cmd` files need them), copies the license files, and zips the folder (skipping `__pycache__`) with maximum
  compression.

```python
138 | def write_checksums(files: list[Path], dist: Path) -> Path:
139 |     """SHA256SUMS.txt in the usual `<checksum>  <file name>` form (`sha256sum -c` reads it), with
140 |     Unix line endings on every system: written with Windows' "\\r\\n", `sha256sum -c` on Linux and
141 |     macOS looks for file names that end in an invisible carriage return, and fails."""
142 |     lines = [f"{hashlib.sha256(f.read_bytes()).hexdigest()}  {f.name}" for f in files]
143 |     target = dist / "SHA256SUMS.txt"
144 |     target.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
145 |     return target
146 | 
147 | 
```
- `SHA256SUMS.txt` in the standard format, so anyone can check a download (`certutil -hashfile <file> SHA256` on
  Windows, `sha256sum -c SHA256SUMS.txt` elsewhere).
- Written with Unix line endings (`newline="\n"`) on every system. Python's text mode on Windows writes `\r\n`, and
  `sha256sum -c` on Linux and macOS then looks for files whose names end in an invisible carriage return — and fails.
  Found by checking the published 0.6.1 release the way a Linux user would (its file, built on GitHub's Windows
  machine, had the Windows endings; the release pages got fixed copies).

```python
148 | def release_notes(version: str, changelog: str) -> str:
149 |     """This version's section of CHANGELOG.md (from its "## <version>" heading to the next one),
150 |     with a line on checking the downloads."""
151 |     lines = changelog.splitlines()
152 |     start = next((i for i, line in enumerate(lines) if line.startswith(f"## {version} ")), None)
153 |     if start is None:
154 |         raise SystemExit(f"CHANGELOG.md has no section '## {version} ...': write it before releasing")
155 |     end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
156 |     body = "\n".join(lines[start + 1:end]).strip()
157 |     return (f"{body}\n\nCheck a download against `SHA256SUMS.txt`: on Windows "
158 |             "`certutil -hashfile <file> SHA256`, elsewhere `sha256sum -c SHA256SUMS.txt`.\n")
159 | 
160 | 
```
- The text of a release page: this version's part of `CHANGELOG.md` (from its `## 0.6.0 — date` heading to the next
  `## ` heading), plus one line on checking the downloads against `SHA256SUMS.txt`. The heading must start with
  `## <version> ` — the space after the version keeps `0.6.0` from also matching a `0.6.01`. A missing section stops
  the build and says what to write, so no release goes out without notes.

```python
161 | def main(argv: list[str]) -> int:
162 |     """Build everything into dist/ (and the release notes into build/) and list it."""
163 |     parser = argparse.ArgumentParser(description="Build the wheel and the portable Windows zip.")
164 |     parser.add_argument("--tag", help="stop unless this tag (e.g. v0.6.0) names this version")
165 |     parser.add_argument("--with-mypy", action="store_true", help="put mypy into the Windows zip too")
166 |     args = parser.parse_args(argv)
167 |     if args.tag is not None and args.tag != f"v{__version__}":
168 |         raise SystemExit(f"tag {args.tag} does not match the version in localagent/__init__.py "
169 |                          f"(v{__version__})")
170 |     dist, build = ROOT / "dist", ROOT / "build"
171 |     dist.mkdir(exist_ok=True)
172 |     build.mkdir(exist_ok=True)
173 |     for old in dist.glob(f"localagent-{__version__}*"):
174 |         old.unlink()
175 |     notes = build / "RELEASE_NOTES.md"
176 |     notes.write_text(release_notes(__version__, (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")),
177 |                      encoding="utf-8")
178 |     wheel = build_wheel(dist)
179 |     bundle = build_windows_bundle(wheel, build, dist, args.with_mypy)
180 |     sums = write_checksums([wheel, bundle], dist)
181 |     for made in (wheel, bundle, sums, notes):
182 |         print(f"{made.stat().st_size / 1e6:8.1f} MB  {made.relative_to(ROOT)}")
183 |     return 0
184 | 
185 | 
```
```python
186 | if __name__ == "__main__":
187 |     raise SystemExit(main(sys.argv[1:]))
```
- The options are read with `argparse`: `--tag` and `--with-mypy`.
- `--tag v0.6.0` (what the release workflow passes) first checks that the tag names the version in
  `localagent/__init__.py`. A tag that does not match stops everything before a single file is built: a release can
  never carry the files of another version.
- Then it writes the release notes to `build\RELEASE_NOTES.md`, removes this version's old files from `dist\`, builds
  the wheel, the bundle and the checksums, and lists them all with their sizes. `dist\` and `build\` are in
  `.gitignore`: release files are published, not committed.
- The footer hands the command-line words after the script's name (`sys.argv[1:]`) to `main`, which passes
  `--with-mypy` on to `build_windows_bundle`.
