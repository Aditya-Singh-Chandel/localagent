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

## Module header
```python
  1 | """Build the release files: a wheel for pip/pipx, and a portable Windows zip that needs nothing
  2 | installed (it carries its own Python).
  3 | 
  4 | Usage (from the project folder, with the project's .venv):
  5 |     python tools/build_release.py          # writes dist/ and prints what it made
  6 | 
  7 | dist/localagent-<version>-py3-none-any.whl     pip install / pipx install, any system
  8 | dist/localagent-<version>-windows-x64.zip      unzip, double-click install.cmd, done
  9 | dist/SHA256SUMS.txt                            checksums to publish next to the downloads
 10 | The embeddable Python is downloaded once (checksum-pinned) into build/cache/.
 11 | """
 12 | from __future__ import annotations
 13 | 
 14 | import hashlib
 15 | import shutil
 16 | import subprocess
 17 | import sys
 18 | import zipfile
 19 | from pathlib import Path
 20 | 
 21 | ROOT = Path(__file__).resolve().parent.parent
 22 | sys.path.insert(0, str(ROOT))
 23 | from localagent import __version__  # noqa: E402  (after the path is set)
 24 | from localagent.fetch import download, extract_archive  # noqa: E402
 25 | 
 26 | PYTHON_VERSION = "3.14.10"                     # python.org's newest 3.14 with a Windows embeddable zip
 27 | PYTHON_URL = f"https://www.python.org/ftp/python/{PYTHON_VERSION}/python-{PYTHON_VERSION}-embed-amd64.zip"
 28 | PYTHON_SHA256 = "90905e64a840557d8c1bc4f5b74db3d7ee85e3ffb217611cac1c7813acea4159"   # from python.org
 29 | PYTHON_SIZE = 12_619_664
 30 | LAUNCHER = '@"%~dp0python\\python.exe" -m localagent %*\r\n'
 31 | INSTALL_CMD = '@powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1"\r\n@pause\r\n'
 32 | UNINSTALL_CMD = '@powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0uninstall.ps1"\r\n@pause\r\n'
 33 | INSTALL_PS1 = """# Adds this folder to YOUR user PATH, so `localagent` works in every new terminal.
 34 | # Nothing else on the computer is changed. Undo: uninstall.cmd
 35 | $here = $PSScriptRoot
 36 | $path = [Environment]::GetEnvironmentVariable("Path", "User")
 37 | if (-not $path) { $path = "" }
 38 | if (($path -split ";") -contains $here) {
 39 |     Write-Host "localagent is already on your PATH ($here)."
 40 | } else {
 41 |     $new = (($path.TrimEnd(";") + ";" + $here).TrimStart(";"))
 42 |     [Environment]::SetEnvironmentVariable("Path", $new, "User")
 43 |     Write-Host "Added $here to your PATH."
 44 | }
 45 | Write-Host "Open a NEW terminal and type:  localagent setup"
 46 | """
 47 | UNINSTALL_PS1 = """# Removes this folder from YOUR user PATH. Then delete the folder itself.
 48 | # Your settings, logs and downloaded models stay in %USERPROFILE%\\.localagent (delete it too to remove them).
 49 | $here = $PSScriptRoot
 50 | $path = [Environment]::GetEnvironmentVariable("Path", "User")
 51 | if (-not $path) { $path = "" }
 52 | $kept = ($path -split ";" | Where-Object { $_ -and $_ -ne $here }) -join ";"
 53 | [Environment]::SetEnvironmentVariable("Path", $kept, "User")
 54 | Write-Host "Removed $here from your PATH. You can now delete this folder."
 55 | """
 56 | README_TXT = """localagent {version} for Windows (64-bit), portable
 57 | 
 58 | A coding agent like Claude Code: online with Google Gemini, offline with a model on your PC.
 59 | 
 60 | 1. Unzip this folder somewhere permanent, for example C:\\Tools\\localagent (not Downloads).
 61 | 2. Double-click install.cmd once. It adds this folder to your PATH (only for your account).
 62 | 3. Open a NEW terminal (PowerShell) and run:   localagent setup
 63 |    It asks for a free Gemini key and offers to download the offline model for your PC.
 64 | 4. Then, in any project folder:               localagent
 65 | 
 66 | Python {python} is included; nothing else has to be installed. Docker Desktop is optional (a sealed
 67 | box for running code). Check the setup at any time:  localagent --doctor
 68 | Uninstall: double-click uninstall.cmd, then delete this folder (and %USERPROFILE%\\.localagent).
 69 | License: MIT (LICENSE). Included third-party software: THIRD_PARTY_NOTICES.md.
 70 | """
 71 | 
 72 | 
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
 73 | def run(*argv: str) -> None:
 74 |     """Run a build step; stop the build with its output when it fails."""
 75 |     done = subprocess.run(argv, capture_output=True, text=True)
 76 |     if done.returncode != 0:
 77 |         raise SystemExit(f"failed: {' '.join(argv)}\n{done.stdout}{done.stderr}")
 78 | 
 79 | 
```
- Runs one build step and stops with its output if it fails.

```python
 80 | def build_wheel(dist: Path) -> Path:
 81 |     """The pure-Python wheel (works on every system with Python 3.10+)."""
 82 |     run(sys.executable, "-m", "pip", "wheel", str(ROOT), "--no-deps", "-q", "-w", str(dist))
 83 |     return dist / f"localagent-{__version__}-py3-none-any.whl"
 84 | 
 85 | 
```
- `pip wheel` builds the standard package file from `pyproject.toml` (no dependencies to include: the agent uses only
  the standard library).

```python
 86 | def add_lib_to_pth(python_dir: Path) -> None:
 87 |     """The embeddable Python finds modules only where its ``python3xx._pth`` file says; add
 88 |     ``..\\lib`` (relative to that file), where the agent and its checkers are installed."""
 89 |     pth = next(python_dir.glob("python3*._pth"))
 90 |     lines = pth.read_text(encoding="utf-8").splitlines()
 91 |     if "..\\lib" not in lines:
 92 |         lines.insert(lines.index(".") + 1 if "." in lines else 0, "..\\lib")
 93 |     pth.write_text("\n".join(lines) + "\n", encoding="utf-8")
 94 | 
 95 | 
```
- The embeddable Python ignores `PYTHONPATH` and the registry and imports only from the folders its `python3xx._pth`
  file lists — which keeps it isolated from whatever else is installed. Adding `..\lib` (relative to that file) lets it
  find the agent.

```python
 96 | def build_windows_bundle(wheel: Path, build: Path, dist: Path) -> Path:
 97 |     """The portable folder (embedded Python + agent + ruff/mypy + launchers), zipped."""
 98 |     name = f"localagent-{__version__}-windows-x64"
 99 |     stage = build / name
100 |     if stage.exists():
101 |         shutil.rmtree(stage)
102 |     python_zip = download(PYTHON_URL, build / "cache" / Path(PYTHON_URL).name, PYTHON_SHA256,
103 |                           PYTHON_SIZE)
104 |     extract_archive(python_zip, stage / "python")
105 |     add_lib_to_pth(stage / "python")
106 |     run(sys.executable, "-m", "pip", "install", "-q", "--target", str(stage / "lib"),
107 |         "--python-version", PYTHON_VERSION.rsplit(".", 1)[0], "--only-binary=:all:",
108 |         "--platform", "win_amd64", f"{wheel}[dev]")
109 |     shutil.copytree(ROOT / "docker", stage / "lib" / "docker")   # for the sandbox-image hint
110 |     files = {"localagent.cmd": LAUNCHER, "install.cmd": INSTALL_CMD, "uninstall.cmd": UNINSTALL_CMD,
111 |              "install.ps1": INSTALL_PS1, "uninstall.ps1": UNINSTALL_PS1,
112 |              "README.txt": README_TXT.format(version=__version__, python=PYTHON_VERSION)}
113 |     for file_name, text in files.items():
114 |         (stage / file_name).write_bytes(text.replace("\r\n", "\n").replace("\n", "\r\n").encode("utf-8"))
115 |     for legal in ("LICENSE", "THIRD_PARTY_NOTICES.md"):
116 |         shutil.copy2(ROOT / legal, stage / legal)
117 |     archive = dist / f"{name}.zip"
118 |     with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as bundle:
119 |         for path in sorted(stage.rglob("*")):
120 |             if path.is_file() and "__pycache__" not in path.parts:
121 |                 bundle.write(path, Path(name) / path.relative_to(stage))
122 |     return archive
123 | 
124 | 
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
125 | def write_checksums(files: list[Path], dist: Path) -> Path:
126 |     """SHA256SUMS.txt in the usual `<checksum>  <file name>` form (`sha256sum -c` reads it)."""
127 |     lines = [f"{hashlib.sha256(f.read_bytes()).hexdigest()}  {f.name}" for f in files]
128 |     target = dist / "SHA256SUMS.txt"
129 |     target.write_text("\n".join(lines) + "\n", encoding="utf-8")
130 |     return target
131 | 
132 | 
```
- `SHA256SUMS.txt` in the standard format, so anyone can check a download (`certutil -hashfile <file> SHA256` on
  Windows, `sha256sum -c SHA256SUMS.txt` elsewhere).

```python
133 | def main() -> int:
134 |     """Build everything into dist/ and list it."""
135 |     dist, build = ROOT / "dist", ROOT / "build"
136 |     dist.mkdir(exist_ok=True)
137 |     for old in dist.glob(f"localagent-{__version__}*"):
138 |         old.unlink()
139 |     wheel = build_wheel(dist)
140 |     bundle = build_windows_bundle(wheel, build, dist)
141 |     sums = write_checksums([wheel, bundle], dist)
142 |     for made in (wheel, bundle, sums):
143 |         print(f"{made.stat().st_size / 1e6:8.1f} MB  {made.relative_to(ROOT)}")
144 |     return 0
145 | 
146 | 
```
```python
147 | if __name__ == "__main__":
148 |     raise SystemExit(main())
```
- Removes this version's old files from `dist\`, builds the wheel, the bundle and the checksums, and lists them with
  their sizes. `dist\` and `build\` are in `.gitignore`: release files are published, not committed.
