# `tools/build_release.py` — building the download files

**Role:** turns the project into the files a website offers for download. One command, run from the project folder:
```
.venv\Scripts\python tools\build_release.py
     0.1 MB  dist\localagent-0.6.0-py3-none-any.whl       pip/pipx, any system with Python 3.10+
    35.7 MB  dist\localagent-0.6.0-windows-x64.zip        portable: nothing needs to be installed
     0.0 MB  dist\SHA256SUMS.txt                          checksums to publish next to them
```
**The Windows zip** holds everything: Python itself (the official "embeddable" Python 3.14.10 from python.org, about
12 MB), the agent, ruff and mypy (its verification layers), launchers and two double-clickable helpers. A user
unzips it, double-clicks `install.cmd` (it adds the folder to their PATH), opens a new terminal and types
`localagent setup`. Tested exactly like that (20 Nov 2026): unzipped into a fresh folder, `--version`, the bundled
Python found both checkers, broken code was rejected, `--doctor` reported correctly, and a real request was answered
in 1 call.

```
localagent-0.6.0-windows-x64\
  localagent.cmd        runs python\python.exe -m localagent with your arguments
  install.cmd/.ps1      adds this folder to YOUR user PATH (nothing else changes)
  uninstall.cmd/.ps1    removes it again
  README.txt            the four steps above
  LICENSE, THIRD_PARTY_NOTICES.md
  python\               embeddable Python 3.14.10 (its python314._pth also lists ..\lib)
  lib\                  localagent, ruff, mypy, and docker\ (for the sandbox image)
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
  7 | 
  8 | dist/localagent-<version>-py3-none-any.whl     pip install / pipx install, any system
  9 | dist/localagent-<version>-windows-x64.zip      unzip, double-click install.cmd, done
 10 | dist/SHA256SUMS.txt                            checksums to publish next to the downloads
 11 | build/RELEASE_NOTES.md                         this version's CHANGELOG section, for the release page
 12 | The embeddable Python is downloaded once (checksum-pinned) into build/cache/.
 13 | """
 14 | from __future__ import annotations
 15 | 
 16 | import hashlib
 17 | import shutil
 18 | import subprocess
 19 | import sys
 20 | import zipfile
 21 | from pathlib import Path
 22 | 
 23 | ROOT = Path(__file__).resolve().parent.parent
 24 | sys.path.insert(0, str(ROOT))
 25 | from localagent import __version__  # noqa: E402  (after the path is set)
 26 | from localagent.fetch import download, extract_archive  # noqa: E402
 27 | 
 28 | PYTHON_VERSION = "3.14.10"                     # python.org's newest 3.14 with a Windows embeddable zip
 29 | PYTHON_URL = f"https://www.python.org/ftp/python/{PYTHON_VERSION}/python-{PYTHON_VERSION}-embed-amd64.zip"
 30 | PYTHON_SHA256 = "90905e64a840557d8c1bc4f5b74db3d7ee85e3ffb217611cac1c7813acea4159"   # from python.org
 31 | PYTHON_SIZE = 12_619_664
 32 | LAUNCHER = '@"%~dp0python\\python.exe" -m localagent %*\r\n'
 33 | INSTALL_CMD = '@powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1"\r\n@pause\r\n'
 34 | UNINSTALL_CMD = '@powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0uninstall.ps1"\r\n@pause\r\n'
 35 | INSTALL_PS1 = """# Adds this folder to YOUR user PATH, so `localagent` works in every new terminal.
 36 | # Nothing else on the computer is changed. Undo: uninstall.cmd
 37 | $here = $PSScriptRoot
 38 | $path = [Environment]::GetEnvironmentVariable("Path", "User")
 39 | if (-not $path) { $path = "" }
 40 | if (($path -split ";") -contains $here) {
 41 |     Write-Host "localagent is already on your PATH ($here)."
 42 | } else {
 43 |     $new = (($path.TrimEnd(";") + ";" + $here).TrimStart(";"))
 44 |     [Environment]::SetEnvironmentVariable("Path", $new, "User")
 45 |     Write-Host "Added $here to your PATH."
 46 | }
 47 | Write-Host "Open a NEW terminal and type:  localagent setup"
 48 | """
 49 | UNINSTALL_PS1 = """# Removes this folder from YOUR user PATH. Then delete the folder itself.
 50 | # Your settings, logs and downloaded models stay in %USERPROFILE%\\.localagent (delete it too to remove them).
 51 | $here = $PSScriptRoot
 52 | $path = [Environment]::GetEnvironmentVariable("Path", "User")
 53 | if (-not $path) { $path = "" }
 54 | $kept = ($path -split ";" | Where-Object { $_ -and $_ -ne $here }) -join ";"
 55 | [Environment]::SetEnvironmentVariable("Path", $kept, "User")
 56 | Write-Host "Removed $here from your PATH. You can now delete this folder."
 57 | """
 58 | README_TXT = """localagent {version} for Windows (64-bit), portable
 59 | 
 60 | A coding agent like Claude Code: online with Google Gemini, offline with a model on your PC.
 61 | 
 62 | 1. Unzip this folder somewhere permanent, for example C:\\Tools\\localagent (not Downloads).
 63 | 2. Double-click install.cmd once. It adds this folder to your PATH (only for your account).
 64 | 3. Open a NEW terminal (PowerShell) and run:   localagent setup
 65 |    It asks for a free Gemini key and offers to download the offline model for your PC.
 66 | 4. Then, in any project folder:               localagent
 67 | 
 68 | Python {python} is included; nothing else has to be installed. Docker Desktop is optional (a sealed
 69 | box for running code). Check the setup at any time:  localagent --doctor
 70 | Uninstall: double-click uninstall.cmd, then delete this folder (and %USERPROFILE%\\.localagent).
 71 | License: MIT (LICENSE). Included third-party software: THIRD_PARTY_NOTICES.md.
 72 | """
 73 | 
 74 | 
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
- `INSTALL_PS1` adds the folder to the user PATH only when missing; `UNINSTALL_PS1` removes exactly that entry. Both
  touch only the current user's PATH — no administrator rights needed.
- `README_TXT` is the four-step guide inside the zip.

## Steps
```python
 75 | def run(*argv: str) -> None:
 76 |     """Run a build step; stop the build with its output when it fails."""
 77 |     done = subprocess.run(argv, capture_output=True, text=True)
 78 |     if done.returncode != 0:
 79 |         raise SystemExit(f"failed: {' '.join(argv)}\n{done.stdout}{done.stderr}")
 80 | 
 81 | 
```
- Runs one build step and stops with its output if it fails.

```python
 82 | def build_wheel(dist: Path) -> Path:
 83 |     """The pure-Python wheel (works on every system with Python 3.10+)."""
 84 |     run(sys.executable, "-m", "pip", "wheel", str(ROOT), "--no-deps", "-q", "-w", str(dist))
 85 |     return dist / f"localagent-{__version__}-py3-none-any.whl"
 86 | 
 87 | 
```
- `pip wheel` builds the standard package file from `pyproject.toml` (no dependencies to include: the agent uses only
  the standard library).

```python
 88 | def add_lib_to_pth(python_dir: Path) -> None:
 89 |     """The embeddable Python finds modules only where its ``python3xx._pth`` file says; add
 90 |     ``..\\lib`` (relative to that file), where the agent and its checkers are installed."""
 91 |     pth = next(python_dir.glob("python3*._pth"))
 92 |     lines = pth.read_text(encoding="utf-8").splitlines()
 93 |     if "..\\lib" not in lines:
 94 |         lines.insert(lines.index(".") + 1 if "." in lines else 0, "..\\lib")
 95 |     pth.write_text("\n".join(lines) + "\n", encoding="utf-8")
 96 | 
 97 | 
```
- The embeddable Python ignores `PYTHONPATH` and the registry and imports only from the folders its `python3xx._pth`
  file lists — which keeps it isolated from whatever else is installed. Adding `..\lib` (relative to that file) lets it
  find the agent.

```python
 98 | def build_windows_bundle(wheel: Path, build: Path, dist: Path) -> Path:
 99 |     """The portable folder (embedded Python + agent + ruff/mypy + launchers), zipped."""
100 |     name = f"localagent-{__version__}-windows-x64"
101 |     stage = build / name
102 |     if stage.exists():
103 |         shutil.rmtree(stage)
104 |     python_zip = download(PYTHON_URL, build / "cache" / Path(PYTHON_URL).name, PYTHON_SHA256,
105 |                           PYTHON_SIZE)
106 |     extract_archive(python_zip, stage / "python")
107 |     add_lib_to_pth(stage / "python")
108 |     run(sys.executable, "-m", "pip", "install", "-q", "--target", str(stage / "lib"),
109 |         "--python-version", PYTHON_VERSION.rsplit(".", 1)[0], "--only-binary=:all:",
110 |         "--platform", "win_amd64", f"{wheel}[dev]")
111 |     shutil.copytree(ROOT / "docker", stage / "lib" / "docker")   # for the sandbox-image hint
112 |     files = {"localagent.cmd": LAUNCHER, "install.cmd": INSTALL_CMD, "uninstall.cmd": UNINSTALL_CMD,
113 |              "install.ps1": INSTALL_PS1, "uninstall.ps1": UNINSTALL_PS1,
114 |              "README.txt": README_TXT.format(version=__version__, python=PYTHON_VERSION)}
115 |     for file_name, text in files.items():
116 |         (stage / file_name).write_bytes(text.replace("\r\n", "\n").replace("\n", "\r\n").encode("utf-8"))
117 |     for legal in ("LICENSE", "THIRD_PARTY_NOTICES.md"):
118 |         shutil.copy2(ROOT / legal, stage / legal)
119 |     archive = dist / f"{name}.zip"
120 |     with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as bundle:
121 |         for path in sorted(stage.rglob("*")):
122 |             if path.is_file() and "__pycache__" not in path.parts:
123 |                 bundle.write(path, Path(name) / path.relative_to(stage))
124 |     return archive
125 | 
126 | 
```
- Starts from an empty staging folder in `build\`. Downloads the embeddable Python (verified, cached in
  `build\cache\`) and unpacks it into `python\`.
- `pip install --target lib` installs the wheel *with its `dev` extra* (ruff, mypy) into `lib\`. `--platform win_amd64
  --python-version 3.14 --only-binary=:all:` makes pip choose the files for the **bundled** Python, not for the one
  running the build. ruff finds its program in `lib\bin\` (it looks next to its package for exactly this kind of
  install).
- Copies the Dockerfile folder (for the sandbox-image hint), writes the launchers and texts with Windows line breaks
  (`.cmd` files need them), copies the license files, and zips the folder (skipping `__pycache__`) with maximum
  compression.

```python
127 | def write_checksums(files: list[Path], dist: Path) -> Path:
128 |     """SHA256SUMS.txt in the usual `<checksum>  <file name>` form (`sha256sum -c` reads it)."""
129 |     lines = [f"{hashlib.sha256(f.read_bytes()).hexdigest()}  {f.name}" for f in files]
130 |     target = dist / "SHA256SUMS.txt"
131 |     target.write_text("\n".join(lines) + "\n", encoding="utf-8")
132 |     return target
133 | 
134 | 
```
- `SHA256SUMS.txt` in the standard format, so anyone can check a download (`certutil -hashfile <file> SHA256` on
  Windows, `sha256sum -c SHA256SUMS.txt` elsewhere).

```python
135 | def release_notes(version: str, changelog: str) -> str:
136 |     """This version's section of CHANGELOG.md (from its "## <version>" heading to the next one),
137 |     with a line on checking the downloads."""
138 |     lines = changelog.splitlines()
139 |     start = next((i for i, line in enumerate(lines) if line.startswith(f"## {version} ")), None)
140 |     if start is None:
141 |         raise SystemExit(f"CHANGELOG.md has no section '## {version} ...': write it before releasing")
142 |     end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
143 |     body = "\n".join(lines[start + 1:end]).strip()
144 |     return (f"{body}\n\nCheck a download against `SHA256SUMS.txt`: on Windows "
145 |             "`certutil -hashfile <file> SHA256`, elsewhere `sha256sum -c SHA256SUMS.txt`.\n")
146 | 
147 | 
```
- The text of a release page: this version's part of `CHANGELOG.md` (from its `## 0.6.0 — date` heading to the next
  `## ` heading), plus one line on checking the downloads against `SHA256SUMS.txt`. The heading must start with
  `## <version> ` — the space after the version keeps `0.6.0` from also matching a `0.6.01`. A missing section stops
  the build and says what to write, so no release goes out without notes.

```python
148 | def main(argv: list[str]) -> int:
149 |     """Build everything into dist/ (and the release notes into build/) and list it."""
150 |     if argv[:1] == ["--tag"] and argv[1:2] != [f"v{__version__}"]:
151 |         raise SystemExit(f"tag {argv[1:2]} does not match the version in localagent/__init__.py "
152 |                          f"(v{__version__})")
153 |     dist, build = ROOT / "dist", ROOT / "build"
154 |     dist.mkdir(exist_ok=True)
155 |     build.mkdir(exist_ok=True)
156 |     for old in dist.glob(f"localagent-{__version__}*"):
157 |         old.unlink()
158 |     notes = build / "RELEASE_NOTES.md"
159 |     notes.write_text(release_notes(__version__, (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")),
160 |                      encoding="utf-8")
161 |     wheel = build_wheel(dist)
162 |     bundle = build_windows_bundle(wheel, build, dist)
163 |     sums = write_checksums([wheel, bundle], dist)
164 |     for made in (wheel, bundle, sums, notes):
165 |         print(f"{made.stat().st_size / 1e6:8.1f} MB  {made.relative_to(ROOT)}")
166 |     return 0
167 | 
168 | 
```
```python
169 | if __name__ == "__main__":
170 |     raise SystemExit(main(sys.argv[1:]))
```
- `--tag v0.6.0` (what the release workflow passes) first checks that the tag names the version in
  `localagent/__init__.py`. A tag that does not match stops everything before a single file is built: a release can
  never carry the files of another version.
- Then it writes the release notes to `build\RELEASE_NOTES.md`, removes this version's old files from `dist\`, builds
  the wheel, the bundle and the checksums, and lists them all with their sizes. `dist\` and `build\` are in
  `.gitignore`: release files are published, not committed.
- The footer hands the command-line words after the script's name (`sys.argv[1:]`) to `main`.
