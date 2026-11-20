# Installs the `localagent` command so it works in ANY folder, like `claude`.
#   Run once from this project folder:   .\install.ps1
#   Then open a NEW terminal, cd into any project, and type:   localagent
# What it does (and nothing else):
#   1. creates .venv here if missing and installs this project into it (editable: code changes apply at once)
#   2. writes %USERPROFILE%\.localagent\bin\localagent.cmd, a 1-line launcher that uses that .venv
#   3. adds %USERPROFILE%\.localagent\bin to YOUR user PATH (only if it is not there yet)
# Uninstall: delete %USERPROFILE%\.localagent\bin and remove it from PATH in "Edit environment variables".
$ErrorActionPreference = "Stop"
$project = $PSScriptRoot
$python = Join-Path $project ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    Write-Host "creating virtual environment..."
    python -m venv (Join-Path $project ".venv")
}
Write-Host "installing localagent into the virtual environment..."
& $python -m pip install --quiet -e "$project[dev]"
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }

$bin = Join-Path $env:USERPROFILE ".localagent\bin"
New-Item -ItemType Directory -Force $bin | Out-Null
$launcher = Join-Path $bin "localagent.cmd"
# %* passes every argument through; the venv's Python runs the package.
Set-Content -Path $launcher -Encoding ascii -Value "@`"$python`" -m localagent %*"
Write-Host "launcher written: $launcher"

$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
if (-not $userPath) { $userPath = "" }
if (($userPath -split ";") -notcontains $bin) {
    $newPath = if ($userPath) { "$userPath;$bin" } else { $bin }
    [Environment]::SetEnvironmentVariable("Path", $newPath, "User")
    Write-Host "added $bin to your user PATH"
} else {
    Write-Host "$bin is already on your user PATH"
}
Write-Host "done. Open a NEW terminal, cd into a project folder and run:  localagent --backend gemini"
