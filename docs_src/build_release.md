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
@@ tools/build_release.py header @@
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
@@ tools/build_release.py run @@
- Runs one build step and stops with its output if it fails.

@@ tools/build_release.py build_wheel @@
- `pip wheel` builds the standard package file from `pyproject.toml` (no dependencies to include: the agent uses only
  the standard library).

@@ tools/build_release.py add_lib_to_pth @@
- The embeddable Python ignores `PYTHONPATH` and the registry and imports only from the folders its `python3xx._pth`
  file lists — which keeps it isolated from whatever else is installed. Adding `..\lib` (relative to that file) lets it
  find the agent.

@@ tools/build_release.py build_windows_bundle @@
- Starts from an empty staging folder in `build\`. Downloads the embeddable Python (verified, cached in
  `build\cache\`) and unpacks it into `python\`.
- `pip install --target lib` installs the wheel *with its `dev` extra* (ruff, mypy) into `lib\`. `--platform win_amd64
  --python-version 3.14 --only-binary=:all:` makes pip choose the files for the **bundled** Python, not for the one
  running the build. ruff finds its program in `lib\bin\` (it looks next to its package for exactly this kind of
  install).
- Copies the Dockerfile folder (for the sandbox-image hint), writes the launchers and texts with Windows line breaks
  (`.cmd` files need them), copies the license files, and zips the folder (skipping `__pycache__`) with maximum
  compression.

@@ tools/build_release.py write_checksums @@
- `SHA256SUMS.txt` in the standard format, so anyone can check a download (`certutil -hashfile <file> SHA256` on
  Windows, `sha256sum -c SHA256SUMS.txt` elsewhere).

@@ tools/build_release.py release_notes @@
- The text of a release page: this version's part of `CHANGELOG.md` (from its `## 0.6.0 — date` heading to the next
  `## ` heading), plus one line on checking the downloads against `SHA256SUMS.txt`. The heading must start with
  `## <version> ` — the space after the version keeps `0.6.0` from also matching a `0.6.01`. A missing section stops
  the build and says what to write, so no release goes out without notes.

@@ tools/build_release.py main @@
@@ tools/build_release.py footer @@
- `--tag v0.6.0` (what the release workflow passes) first checks that the tag names the version in
  `localagent/__init__.py`. A tag that does not match stops everything before a single file is built: a release can
  never carry the files of another version.
- Then it writes the release notes to `build\RELEASE_NOTES.md`, removes this version's old files from `dist\`, builds
  the wheel, the bundle and the checksums, and lists them all with their sizes. `dist\` and `build\` are in
  `.gitignore`: release files are published, not committed.
- The footer hands the command-line words after the script's name (`sys.argv[1:]`) to `main`.
