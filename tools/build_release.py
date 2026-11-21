"""Build the release files: a wheel for pip/pipx, and a portable Windows zip that needs nothing
installed (it carries its own Python).

Usage (from the project folder, with the project's .venv):
    python tools/build_release.py              # writes dist/ and prints what it made
    python tools/build_release.py --tag v0.6.0 # the same, after checking the tag is this version
    python tools/build_release.py --with-mypy  # the Windows zip with mypy too (about twice the size)

dist/localagent-<version>-py3-none-any.whl     pip install / pipx install, any system
dist/localagent-<version>-windows-x64.zip      unzip, double-click install.cmd, done
dist/SHA256SUMS.txt                            checksums to publish next to the downloads
build/RELEASE_NOTES.md                         this version's CHANGELOG section, for the release page
The embeddable Python is downloaded once (checksum-pinned) into build/cache/.
"""
from __future__ import annotations

import argparse
import hashlib
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from localagent import __version__  # noqa: E402  (after the path is set)
from localagent.fetch import download, extract_archive  # noqa: E402

PYTHON_VERSION = "3.14.10"                     # python.org's newest 3.14 with a Windows embeddable zip
PYTHON_URL = f"https://www.python.org/ftp/python/{PYTHON_VERSION}/python-{PYTHON_VERSION}-embed-amd64.zip"
PYTHON_SHA256 = "90905e64a840557d8c1bc4f5b74db3d7ee85e3ffb217611cac1c7813acea4159"   # from python.org
PYTHON_SIZE = 12_619_664
LAUNCHER = '@"%~dp0python\\python.exe" -m localagent %*\r\n'
INSTALL_CMD = '@powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1"\r\n@pause\r\n'
UNINSTALL_CMD = '@powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0uninstall.ps1"\r\n@pause\r\n'
INSTALL_PS1 = """# Adds this folder to YOUR user PATH, so `localagent` works in every new terminal.
# Nothing else on the computer is changed. Undo: uninstall.cmd
$here = $PSScriptRoot
$path = [Environment]::GetEnvironmentVariable("Path", "User")
if (-not $path) { $path = "" }
if (($path -split ";") -contains $here) {
    Write-Host "localagent is already on your PATH ($here)."
} else {
    $new = (($path.TrimEnd(";") + ";" + $here).TrimStart(";"))
    [Environment]::SetEnvironmentVariable("Path", $new, "User")
    Write-Host "Added $here to your PATH."
}
Write-Host ""
Write-Host "Next, the guided setup (about a minute; run it again any time with: localagent setup)"
& "$here\\python\\python.exe" -m localagent setup
Write-Host ""
Write-Host "Done. Open a NEW terminal in a project folder and type:  localagent"
"""
UNINSTALL_PS1 = """# Removes this folder from YOUR user PATH. Then delete the folder itself.
# Your settings, logs and downloaded models stay in %USERPROFILE%\\.localagent (delete it too to remove them).
$here = $PSScriptRoot
$path = [Environment]::GetEnvironmentVariable("Path", "User")
if (-not $path) { $path = "" }
$kept = ($path -split ";" | Where-Object { $_ -and $_ -ne $here }) -join ";"
[Environment]::SetEnvironmentVariable("Path", $kept, "User")
Write-Host "Removed $here from your PATH. You can now delete this folder."
"""
README_TXT = """localagent {version} for Windows (64-bit), portable

A coding agent for your terminal: online with Google Gemini (free key) or your own key for
OpenAI, Anthropic, OpenRouter, Groq, Mistral, DeepSeek; offline with a model on your PC.

1. Unzip this folder somewhere permanent, for example C:\\Tools\\localagent (not Downloads).
2. Double-click install.cmd once. It adds this folder to your PATH (only for your account)
   and starts the guided setup: an online service and key, and/or a model on this PC
   (one you already run - Ollama, LM Studio - or a free one it downloads for you).
3. Open a NEW terminal (PowerShell) in a project folder and type:   localagent
4. Change the setup any time with:   localagent setup

Python {python} is included; nothing else has to be installed. Docker Desktop is optional (a sealed
box for running code). Check the setup at any time:  localagent --doctor
Type checks (--type-check) need mypy, which this download leaves out to stay small.
Uninstall: double-click uninstall.cmd, then delete this folder (and %USERPROFILE%\\.localagent).
License: MIT (LICENSE). Included third-party software: THIRD_PARTY_NOTICES.md.
"""


def run(*argv: str) -> None:
    """Run a build step; stop the build with its output when it fails."""
    done = subprocess.run(argv, capture_output=True, text=True)
    if done.returncode != 0:
        raise SystemExit(f"failed: {' '.join(argv)}\n{done.stdout}{done.stderr}")


def build_wheel(dist: Path) -> Path:
    """The pure-Python wheel (works on every system with Python 3.10+)."""
    run(sys.executable, "-m", "pip", "wheel", str(ROOT), "--no-deps", "-q", "-w", str(dist))
    return dist / f"localagent-{__version__}-py3-none-any.whl"


def add_lib_to_pth(python_dir: Path) -> None:
    """The embeddable Python finds modules only where its ``python3xx._pth`` file says; add
    ``..\\lib`` (relative to that file), where the agent and its checkers are installed."""
    pth = next(python_dir.glob("python3*._pth"))
    lines = pth.read_text(encoding="utf-8").splitlines()
    if "..\\lib" not in lines:
        lines.insert(lines.index(".") + 1 if "." in lines else 0, "..\\lib")
    pth.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_windows_bundle(wheel: Path, build: Path, dist: Path, with_mypy: bool = False) -> Path:
    """The portable folder (embedded Python + agent + ruff + launchers), zipped; mypy only when
    asked for - it would double the download and is used only with --type-check."""
    name = f"localagent-{__version__}-windows-x64"
    stage = build / name
    if stage.exists():
        shutil.rmtree(stage)
    python_zip = download(PYTHON_URL, build / "cache" / Path(PYTHON_URL).name, PYTHON_SHA256,
                          PYTHON_SIZE)
    extract_archive(python_zip, stage / "python")
    add_lib_to_pth(stage / "python")
    tools = ["ruff", "mypy"] if with_mypy else ["ruff"]
    run(sys.executable, "-m", "pip", "install", "-q", "--target", str(stage / "lib"),
        "--python-version", PYTHON_VERSION.rsplit(".", 1)[0], "--only-binary=:all:",
        "--platform", "win_amd64", str(wheel), *tools)
    shutil.copytree(ROOT / "docker", stage / "lib" / "docker")   # for the sandbox-image hint
    files = {"localagent.cmd": LAUNCHER, "install.cmd": INSTALL_CMD, "uninstall.cmd": UNINSTALL_CMD,
             "install.ps1": INSTALL_PS1, "uninstall.ps1": UNINSTALL_PS1,
             "README.txt": README_TXT.format(version=__version__, python=PYTHON_VERSION)}
    for file_name, text in files.items():
        (stage / file_name).write_bytes(text.replace("\r\n", "\n").replace("\n", "\r\n").encode("utf-8"))
    for legal in ("LICENSE", "THIRD_PARTY_NOTICES.md"):
        shutil.copy2(ROOT / legal, stage / legal)
    archive = dist / f"{name}.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as bundle:
        for path in sorted(stage.rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts:
                bundle.write(path, Path(name) / path.relative_to(stage))
    return archive


def write_checksums(files: list[Path], dist: Path) -> Path:
    """SHA256SUMS.txt in the usual `<checksum>  <file name>` form (`sha256sum -c` reads it), with
    Unix line endings on every system: written with Windows' "\\r\\n", `sha256sum -c` on Linux and
    macOS looks for file names that end in an invisible carriage return, and fails."""
    lines = [f"{hashlib.sha256(f.read_bytes()).hexdigest()}  {f.name}" for f in files]
    target = dist / "SHA256SUMS.txt"
    target.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return target


def release_notes(version: str, changelog: str) -> str:
    """This version's section of CHANGELOG.md (from its "## <version>" heading to the next one),
    with a line on checking the downloads."""
    lines = changelog.splitlines()
    start = next((i for i, line in enumerate(lines) if line.startswith(f"## {version} ")), None)
    if start is None:
        raise SystemExit(f"CHANGELOG.md has no section '## {version} ...': write it before releasing")
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    body = "\n".join(lines[start + 1:end]).strip()
    return (f"{body}\n\nCheck a download against `SHA256SUMS.txt`: on Windows "
            "`certutil -hashfile <file> SHA256`, elsewhere `sha256sum -c SHA256SUMS.txt`.\n")


def main(argv: list[str]) -> int:
    """Build everything into dist/ (and the release notes into build/) and list it."""
    parser = argparse.ArgumentParser(description="Build the wheel and the portable Windows zip.")
    parser.add_argument("--tag", help="stop unless this tag (e.g. v0.6.0) names this version")
    parser.add_argument("--with-mypy", action="store_true", help="put mypy into the Windows zip too")
    args = parser.parse_args(argv)
    if args.tag is not None and args.tag != f"v{__version__}":
        raise SystemExit(f"tag {args.tag} does not match the version in localagent/__init__.py "
                         f"(v{__version__})")
    dist, build = ROOT / "dist", ROOT / "build"
    dist.mkdir(exist_ok=True)
    build.mkdir(exist_ok=True)
    for old in dist.glob(f"localagent-{__version__}*"):
        old.unlink()
    notes = build / "RELEASE_NOTES.md"
    notes.write_text(release_notes(__version__, (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")),
                     encoding="utf-8")
    wheel = build_wheel(dist)
    bundle = build_windows_bundle(wheel, build, dist, args.with_mypy)
    sums = write_checksums([wheel, bundle], dist)
    for made in (wheel, bundle, sums, notes):
        print(f"{made.stat().st_size / 1e6:8.1f} MB  {made.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
