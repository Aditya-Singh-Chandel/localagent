# `localagent/runners.py` — where code runs: your computer or a Docker container

**Role in the flow:** when the model calls `run_python`, `run_tests` or `run_shell`, the toolbox (after its
verification gate and your approval) hands the command to a **runner**. There are two:

| Runner | Chosen by | Where code runs | Shell tool? |
|---|---|---|---|
| `LocalRunner` | `--sandbox local` (default) | on your computer, with your user's permissions | only with `--allow-shell` (asks every time) |
| `DockerRunner` | `--sandbox docker` | inside a locked-down Linux container | yes |

**Why a container?** Code written by a model can be wrong in harmful ways (deleting files, filling the disk, endless
loops). In the container it can only see the workspace folder, cannot use the internet (unless you pass `--network`),
cannot use more than 1 GB of memory, 1 CPU and 256 processes, runs as an ordinary user, and is killed at the timeout.
This is also what makes the general **shell tool** safe enough to offer: the model can `pip install`, run `pytest`, use
`ls`/`grep`/`git` — all inside the box.

How the container is used: it is started **once**, the first time code needs to run, as
`docker run -d ... localagent-sandbox:latest sleep infinity` (a container that just waits). Each command is then
`docker exec <name> timeout -s KILL <seconds> <command>`, which is much faster than starting a new container each time.
The workspace folder on your disk is *mounted* at `/work` inside, so files the agent writes are immediately visible to
the code it runs, and results stay on your disk. The container is removed when the agent exits.

The image is built from `docker/sandbox.Dockerfile` (explained in `docker.md`).

## Module header
```python
  1 | """Where the agent's code actually runs: on this machine, or inside a Docker container.
  2 | 
  3 | Both runners offer the same method, ``run(kind, args)``, so the tools do not care
  4 | which one is active:
  5 |   * kind "python"  -> run a Python file          (args: [file, extra args...])
  6 |   * kind "tests"   -> run unittest discovery      (args: [])
  7 |   * kind "shell"   -> run a shell command line    (args: [command])  -- Docker, or --allow-shell
  8 | 
  9 | The Docker runner starts ONE long-lived container per agent the first time it is
 10 | needed (``docker run -d ... sleep infinity``) and then uses ``docker exec`` for
 11 | each command, which is much faster than a new container per command. The
 12 | container has no network (unless allowed), a memory/CPU/process cap, runs as a
 13 | normal user, and only sees the workspace folder (mounted at /work).
 14 | """
 15 | from __future__ import annotations
 16 | 
 17 | import atexit
 18 | import os
 19 | import subprocess
 20 | import sys
 21 | import uuid
 22 | from pathlib import Path
 23 | from typing import Protocol
 24 | 
 25 | from .config import Config
 26 | from .sandbox import run_command, shell_argv
 27 | 
 28 | CONTAINER_WORKDIR = "/work"
 29 | 
 30 | 
```
- `os` tells Windows from other systems; `atexit` registers the clean-up that removes the container when Python exits; `subprocess` runs `docker info`;
  `uuid` makes a unique container name; `run_command` (from `sandbox.py`) runs a program with a timeout and output cap.
- `CONTAINER_WORKDIR = "/work"` is where the workspace appears inside the container.

## `Runner` — the shared interface
```python
 31 | class Runner(Protocol):
 32 |     """Anything that can run the three kinds of command."""
 33 | 
 34 |     supports_shell: bool
 35 |     shell_label: str
 36 | 
```
```python
 37 |     def run(self, kind: str, args: list[str]) -> tuple[int, str]: ...
 38 | 
```
```python
 39 |     def stop(self) -> None: ...
 40 | 
 41 | 
```
- A `Protocol` (like `ChatBackend` in `llm.py`): anything with `supports_shell`, `shell_label` (where commands run,
  shown to the model and in approval questions), `run(kind, args)` and `stop()` is a runner. `kind` is `"python"`, `"tests"` or `"shell"`; the result is `(exit code, output text)`.

## `LocalRunner`
```python
 42 | class LocalRunner:
 43 |     """Runs commands directly on this computer with the agent's own Python."""
 44 | 
```
```python
 45 |     def __init__(self, config: Config, root: Path) -> None:
 46 |         self._cfg = config
 47 |         self._root = root
 48 |         # A shell on the real computer is powerful, so it is opt-in (--allow-shell) and
 49 |         # every command still needs the user's approval in the toolbox.
 50 |         self.supports_shell = config.allow_host_shell
 51 |         self.shell_label = ("PowerShell on this Windows computer" if os.name == "nt"
 52 |                             else "sh on this computer")
 53 | 
```
```python
 54 |     def run(self, kind: str, args: list[str]) -> tuple[int, str]:
 55 |         """Run on the host inside the workspace folder, with the timeout and output cap."""
 56 |         if kind == "python":
 57 |             argv = [sys.executable, *args]
 58 |         elif kind == "tests":
 59 |             argv = [sys.executable, "-m", "unittest", "discover"]
 60 |         elif not self.supports_shell:
 61 |             return 126, "The shell tool needs --allow-shell or the Docker sandbox (--sandbox docker)."
 62 |         else:
 63 |             argv = shell_argv(args[0])
 64 |         return run_command(argv, self._root, self._cfg.command_timeout, self._cfg.max_output_chars)
 65 | 
```
```python
 66 |     def stop(self) -> None:
 67 |         """Nothing to clean up for local runs."""
 68 | 
 69 | 
```
- The shell is **opt-in**: `supports_shell` comes from `allow_host_shell` (`--allow-shell`). Claude Code also runs
  commands on your computer, always asking first; we do the same — the toolbox asks for approval for every command.
  Without the flag the shell tool is not offered, and a shell request is refused with code 126.
- Shell commands go through `shell_argv` (`sandbox.py`): PowerShell on Windows, `sh -c` elsewhere.
- `python` runs `<this Python> <file> <args>`; `tests` runs `python -m unittest discover`; both inside the workspace
  folder with the configured timeout and output cap. `stop` has nothing to do.

## `DockerRunner`
```python
 70 | class DockerRunner:
 71 |     """Runs commands inside a locked-down container that only sees the workspace."""
 72 | 
 73 |     supports_shell = True
 74 |     shell_label = "sh inside an isolated Linux container"
 75 | 
```
```python
 76 |     def __init__(self, config: Config, root: Path) -> None:
 77 |         self._cfg = config
 78 |         self._root = root
 79 |         self.name = f"localagent-{uuid.uuid4().hex[:10]}"
 80 |         self._started = False
 81 | 
```
- `shell_label` says commands run in an isolated Linux container. Picks a unique container name like `localagent-3f9c0a1b2d` so several agents can run at once.

```python
 82 |     def _docker(self, *args: str, timeout: int = 120) -> tuple[int, str]:
 83 |         return run_command(["docker", *args], self._root, timeout, 4000)
 84 | 
```
- Small helper: run `docker <args...>` with a timeout.

```python
 85 |     def start(self) -> None:
 86 |         """Create the container (once). Raises RuntimeError with advice when Docker fails."""
 87 |         if self._started:
 88 |             return
 89 |         code, out = self._docker("image", "inspect", self._cfg.sandbox_image)
 90 |         if code != 0:
 91 |             raise RuntimeError(
 92 |                 f"Docker image {self._cfg.sandbox_image} not found ({out.strip()[:200]}). Build it with:\n"
 93 |                 "  docker build -t localagent-sandbox:latest -f docker/sandbox.Dockerfile docker")
 94 |         argv = ["run", "-d", "--rm", "--name", self.name,
 95 |                 "--memory", self._cfg.sandbox_memory, "--cpus", self._cfg.sandbox_cpus,
 96 |                 "--pids-limit", "256", "--security-opt", "no-new-privileges",
 97 |                 "-v", f"{self._root}:{CONTAINER_WORKDIR}", "-w", CONTAINER_WORKDIR]
 98 |         if not self._cfg.sandbox_network:
 99 |             argv += ["--network", "none"]
100 |         code, out = self._docker(*argv, self._cfg.sandbox_image)
101 |         if code != 0:
102 |             raise RuntimeError(f"Could not start the sandbox container: {out.strip()[:300]}\n"
103 |                                "Is Docker Desktop running?")
104 |         self._started = True
105 |         atexit.register(self.stop)
106 | 
```
- Does nothing if already started.
- Checks that the sandbox image exists; if not, raises an error that contains the exact build command.
- Builds the `docker run` command:
  - `-d` run in the background, `--rm` delete the container automatically once it stops, `--name` our unique name;
  - `--memory 1g`, `--cpus 1.0` resource caps (settings `sandbox_memory`, `sandbox_cpus`);
  - `--pids-limit 256` stops "fork bombs" (programs that create processes endlessly);
  - `--security-opt no-new-privileges` stops programs from gaining extra rights;
  - `-v <workspace>:/work -w /work` mounts the workspace and starts there;
  - `--network none` unless `sandbox_network` is on (`--network` flag).
- If Docker fails (for example Docker Desktop is not running) the error says so. On success it remembers it started
  and registers `stop` to run at exit.

```python
107 |     def stop(self) -> None:
108 |         """Remove the container (safe to call more than once, even after the folder is gone)."""
109 |         if not self._started:
110 |             return
111 |         self._started = False
112 |         try:                                       # no cwd: the workspace may already be deleted
113 |             subprocess.run(["docker", "rm", "-f", self.name], capture_output=True, timeout=60)
114 |         except (OSError, subprocess.TimeoutExpired):
115 |             pass                                   # best effort; `docker ps` shows leftovers
116 | 
```
- Removes the container with `docker rm -f`. It deliberately runs **without** a working folder: in the eval harness the
  temporary workspace may already be deleted when clean-up runs, and Windows refuses to start a program in a folder that
  no longer exists (this was a real bug: containers were left running). Errors are ignored — clean-up is best effort,
  and `docker ps` shows any leftovers.

```python
117 |     def run(self, kind: str, args: list[str]) -> tuple[int, str]:
118 |         """Run inside the container; a timeout also kills the processes inside it."""
119 |         try:
120 |             self.start()
121 |         except RuntimeError as exc:
122 |             return 125, str(exc)
123 |         if kind == "python":
124 |             inner = ["python", *args]
125 |         elif kind == "tests":
126 |             inner = ["python", "-m", "unittest", "discover"]
127 |         else:
128 |             inner = ["sh", "-c", args[0]]
129 |         argv = ["docker", "exec", self.name, "timeout", "-s", "KILL",
130 |                 str(self._cfg.command_timeout), *inner]
131 |         code, out = run_command(argv, self._root, self._cfg.command_timeout + 15,
132 |                                 self._cfg.max_output_chars)
133 |         if code == 137:                           # killed by `timeout -s KILL`
134 |             return 124, f"Command timed out after {self._cfg.command_timeout}s and was killed.\n{out}"
135 |         return code, out
136 | 
137 | 
```
- Starts the container if needed (a start failure becomes exit code 125 with the advice text, so the model and you
  see it instead of a crash).
- Chooses the inner command: `python file args`, `python -m unittest discover`, or `sh -c "<command line>"`.
- Wraps it in `timeout -s KILL <seconds>` *inside* the container, so a runaway program is really killed there (killing
  only the `docker exec` client on Windows would leave it running). The outer timeout has 15 s of slack for Docker.
- Exit code 137 means "killed by KILL", reported as our usual timeout code 124 with a clear message.

## Choosing and checking
```python
138 | def make_runner(config: Config, root: Path) -> Runner:
139 |     """Pick the runner named by ``config.sandbox``."""
140 |     if config.sandbox == "docker":
141 |         return DockerRunner(config, root)
142 |     if config.sandbox == "local":
143 |         return LocalRunner(config, root)
144 |     raise ValueError(f"unknown sandbox '{config.sandbox}'. Choose 'local' or 'docker'.")
145 | 
146 | 
```
- Builds the runner named by `config.sandbox`, or explains the valid names.

```python
147 | def sandbox_image_ready(image: str) -> bool:
148 |     """True when Docker answers AND the sandbox image exists (one quick command)."""
149 |     try:
150 |         done = subprocess.run(["docker", "image", "inspect", image], capture_output=True,
151 |                               timeout=20)
152 |     except (OSError, subprocess.TimeoutExpired):
153 |         return False
154 |     return done.returncode == 0
155 | 
156 | 
```
- One `docker image inspect <image>`: succeeds only when the Docker engine answers **and** the image exists. The CLI uses
  it to pick the Docker sandbox automatically.

```python
157 | def docker_available() -> bool:
158 |     """True when the docker command exists and its daemon answers."""
159 |     try:
160 |         done = subprocess.run(["docker", "info", "--format", "{{.ServerVersion}}"],
161 |                               capture_output=True, text=True, timeout=20)
162 |     except (OSError, subprocess.TimeoutExpired):
163 |         return False
164 |     return done.returncode == 0
```
- Runs `docker info`; `True` only if the `docker` program exists **and** the Docker engine answers. Used by
  `--doctor` and to skip the Docker test when Docker is not running.
