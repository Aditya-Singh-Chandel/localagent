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
  6 |   * kind "tests"   -> run the project's tests     (args: [], or [your test command])
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
 18 | import json
 19 | import os
 20 | import shutil
 21 | import subprocess
 22 | import sys
 23 | import uuid
 24 | from pathlib import Path
 25 | from typing import Protocol
 26 | 
 27 | from .config import Config
 28 | from .sandbox import run_command, shell_argv
 29 | 
 30 | CONTAINER_WORKDIR = "/work"
 31 | VENV_FOLDERS = (".venv", "venv", "env")
 32 | NO_TEST_SCRIPT = "no test specified"            # npm's placeholder "test" script
 33 | # Inside the container: pytest when it is installed (it also runs unittest tests), else unittest.
 34 | PYTHON_TESTS_SH = ('if python -c "import pytest" 2>/dev/null; then python -m pytest -q --tb=short; '
 35 |                    'else python -m unittest discover {start}; fi')
 36 | 
 37 | 
```
- `os` tells Windows from other systems; `atexit` registers the clean-up that removes the container when Python exits;
  `json` reads `package.json`; `shutil` finds `npm`; `subprocess` runs `docker info`; `sys` gives the agent's own
  Python; `uuid` makes a unique container name; `run_command` / `shell_argv` (from `sandbox.py`) run a program with a
  timeout and output cap.
- `CONTAINER_WORKDIR = "/work"` is where the workspace appears inside the container. `VENV_FOLDERS` are the usual names
  of a project's virtual environment. `NO_TEST_SCRIPT` is the text of the `"test"` script `npm init` writes, which only
  prints an error. `PYTHON_TESTS_SH` is the container's Python test command: pytest when installed, else unittest.

## Running the project's code with the project's Python
```python
 49 | def project_python(root: Path) -> str:
 50 |     """The Python of the project: a virtual environment in the project folder (``.venv``,
 51 |     ``venv``, ``env``), else an activated one, else the agent's own. The project's packages
 52 |     are installed there, so its code and tests can import them."""
 53 |     inside = "Scripts/python.exe" if os.name == "nt" else "bin/python"
 54 |     active = os.environ.get("VIRTUAL_ENV")
 55 |     candidates = [root / f / inside for f in VENV_FOLDERS] + ([Path(active) / inside] if active else [])
 56 |     for candidate in candidates:
 57 |         if candidate.is_file():
 58 |             return str(candidate)
 59 |     return sys.executable
 60 | 
 61 | 
```
- **Before 0.6 every Python run used the agent's own Python** — the one in the agent's `.venv`, which knows nothing of
  your project's packages, so `import flask` failed in a project that had Flask installed in its own `.venv`. Now a
  virtual environment in the project folder (`.venv`, `venv` or `env`) is used first, then one you activated before
  starting the agent (`VIRTUAL_ENV`), and only then the agent's own. The project's own folder comes first because an
  activated environment is often an unrelated one (for example the agent's).

## Running the project's tests with the project's tool
```python
 62 | def _unittest_start(root: Path) -> str:
 63 |     """Where unittest should look: a ``tests`` folder that is not a package can only be found
 64 |     by starting there (Python 3.11+ no longer enters folders without __init__.py)."""
 65 |     tests = root / "tests"
 66 |     plain = tests.is_dir() and not (tests / "__init__.py").exists() and not any(root.glob("test*.py"))
 67 |     return "-s tests" if plain else ""
 68 | 
 69 | 
```
- Python 3.11 stopped `unittest` from entering folders without `__init__.py`, and pytest-style projects usually have
  none in `tests/` — so `python -m unittest discover` from the project folder found **0 tests** and failed with exit
  code 5 ("NO TESTS RAN"), a confusing message for the model. When there is such a plain `tests` folder (and no test
  files at the top), discovery now starts there: `-s tests`. `python -m` keeps the project folder importable, so the
  tests can still import the project's modules.

```python
 70 | PYTHON_TEST_GLOBS = ("test*.py", "*_test.py", "tests/test*.py", "tests/*_test.py",
 71 |                      "tests/*/test*.py", "test/test*.py")   # a fixed depth: never node_modules
 72 | 
 73 | 
```
```python
 74 | def test_kind(root: Path) -> str:
 75 |     """Which test tool fits the project: "python", "npm", "go" or "cargo"."""
 76 |     python_tests = any(next(root.glob(pattern), None) for pattern in PYTHON_TEST_GLOBS)
 77 |     if not python_tests:
 78 |         package = root / "package.json"
 79 |         if package.is_file():
 80 |             try:
 81 |                 script = json.loads(package.read_text(encoding="utf-8")).get("scripts", {}).get("test", "")
 82 |             except (ValueError, OSError, AttributeError):
 83 |                 script = ""
 84 |             if script and NO_TEST_SCRIPT not in script:
 85 |                 return "npm"
 86 |         if (root / "go.mod").is_file():
 87 |             return "go"
 88 |         if (root / "Cargo.toml").is_file():
 89 |             return "cargo"
 90 |     return "python"
 91 | 
 92 | 
```
- `PYTHON_TEST_GLOBS` are the places Python tests usually live, checked to a fixed depth so the check never wanders
  into `node_modules`.
- `test_kind` decides which tool runs the tests: Python tests anywhere in those places → `python`. Otherwise a
  `package.json` with a real `"test"` script → `npm`; a `go.mod` → `go`; a `Cargo.toml` → `cargo`; else `python`. Python
  tests win in mixed projects because `run_tests` verifies Python first.
- A settings file's `test_command` replaces all of this (`tools.py` passes it as the argument of kind `tests`).

## `Runner` — the shared interface
```python
 38 | class Runner(Protocol):
 39 |     """Anything that can run the three kinds of command."""
 40 | 
 41 |     supports_shell: bool
 42 |     shell_label: str
 43 | 
```
```python
 44 |     def run(self, kind: str, args: list[str]) -> tuple[int, str]: ...
 45 | 
```
```python
 46 |     def stop(self) -> None: ...
 47 | 
 48 | 
```
- A `Protocol` (like `ChatBackend` in `llm.py`): anything with `supports_shell`, `shell_label` (where commands run,
  shown to the model and in approval questions), `run(kind, args)` and `stop()` is a runner. `kind` is `"python"`, `"tests"` or `"shell"`; the result is `(exit code, output text)`.

## `LocalRunner`
```python
 93 | class LocalRunner:
 94 |     """Runs commands directly on this computer with the project's Python."""
 95 | 
```
```python
 96 |     def __init__(self, config: Config, root: Path) -> None:
 97 |         self._cfg = config
 98 |         self._root = root
 99 |         self._pytest: bool | None = None          # is pytest installed? (asked once)
100 |         # A shell on the real computer is powerful, so it is opt-in (--allow-shell) and
101 |         # every command still needs the user's approval in the toolbox.
102 |         self.supports_shell = config.allow_host_shell
103 |         self.shell_label = ("PowerShell on this Windows computer" if os.name == "nt"
104 |                             else "sh on this computer")
105 | 
```
- The shell is **opt-in**: `supports_shell` comes from `allow_host_shell` (`--allow-shell`). Claude Code also runs
  commands on your computer, always asking first; we do the same — the toolbox asks for approval for every command.
  Without the flag the shell tool is not offered, and a shell request is refused with code 126.
- `_pytest` remembers whether pytest is installed (asked once per session).

```python
106 |     def _has_pytest(self, python: str) -> bool:
107 |         """True when the project's Python can run pytest."""
108 |         if self._pytest is None:
109 |             code, _ = run_command([python, "-c", "import pytest"], self._root, 30, 200)
110 |             self._pytest = code == 0
111 |         return self._pytest
112 | 
```
- Asks the project's Python whether it can `import pytest`, once.

```python
113 |     def _tests_argv(self) -> list[str]:
114 |         """The project's test command (see ``test_kind``)."""
115 |         python = project_python(self._root)
116 |         kind = test_kind(self._root)
117 |         if kind == "npm":
118 |             return [shutil.which("npm") or "npm", "test"]
119 |         if kind == "go":
120 |             return ["go", "test", "./..."]
121 |         if kind == "cargo":
122 |             return ["cargo", "test"]
123 |         if self._has_pytest(python):
124 |             return [python, "-m", "pytest", "-q", "--tb=short"]
125 |         return [python, "-m", "unittest", "discover", *_unittest_start(self._root).split()]
126 | 
```
- The test command for `test_kind`: `npm test`, `go test ./...`, `cargo test`, or for Python `python -m pytest -q
  --tb=short` when pytest is installed (it also runs unittest-style tests; `-q --tb=short` keeps the output, and so the
  tokens, small) and `python -m unittest discover` (with `-s tests` when needed) otherwise.

```python
127 |     def run(self, kind: str, args: list[str]) -> tuple[int, str]:
128 |         """Run on the host inside the workspace folder, with the timeout and output cap."""
129 |         timeout = self._cfg.command_timeout
130 |         if kind == "python":
131 |             argv = [project_python(self._root), *args]
132 |         elif kind == "tests":
133 |             argv = shell_argv(args[0]) if args else self._tests_argv()   # your command, or found
134 |             timeout = max(self._cfg.test_timeout, timeout)
135 |         elif not self.supports_shell:
136 |             return 126, "The shell tool needs --allow-shell or the Docker sandbox (--sandbox docker)."
137 |         else:
138 |             argv = shell_argv(args[0])
139 |         return run_command(argv, self._root, timeout, self._cfg.max_output_chars)
140 | 
```
```python
141 |     def stop(self) -> None:
142 |         """Nothing to clean up for local runs."""
143 | 
144 | 
```
- `python` runs `<the project's Python> <file> <args>`; `tests` runs your `test_command` through the shell when given,
  else the found test command, with the longer `test_timeout`; `shell` goes through `shell_argv` (`sandbox.py`):
  PowerShell on Windows, `sh -c` elsewhere. Everything runs inside the workspace folder with the output cap. `stop` has
  nothing to do.

## `DockerRunner`
```python
145 | class DockerRunner:
146 |     """Runs commands inside a locked-down container that only sees the workspace."""
147 | 
148 |     supports_shell = True
149 |     shell_label = "sh inside an isolated Linux container"
150 | 
```
```python
151 |     def __init__(self, config: Config, root: Path) -> None:
152 |         self._cfg = config
153 |         self._root = root
154 |         self.name = f"localagent-{uuid.uuid4().hex[:10]}"
155 |         self._started = False
156 | 
```
- `shell_label` says commands run in an isolated Linux container. Picks a unique container name like `localagent-3f9c0a1b2d` so several agents can run at once.

```python
157 |     def _docker(self, *args: str, timeout: int = 120) -> tuple[int, str]:
158 |         return run_command(["docker", *args], self._root, timeout, 4000)
159 | 
```
- Small helper: run `docker <args...>` with a timeout.

```python
160 |     def start(self) -> None:
161 |         """Create the container (once). Raises RuntimeError with advice when Docker fails."""
162 |         if self._started:
163 |             return
164 |         code, out = self._docker("image", "inspect", self._cfg.sandbox_image)
165 |         if code != 0:
166 |             raise RuntimeError(
167 |                 f"Docker image {self._cfg.sandbox_image} not found ({out.strip()[:200]}). Build it with:\n"
168 |                 "  docker build -t localagent-sandbox:latest -f docker/sandbox.Dockerfile docker")
169 |         argv = ["run", "-d", "--rm", "--name", self.name,
170 |                 "--memory", self._cfg.sandbox_memory, "--cpus", self._cfg.sandbox_cpus,
171 |                 "--pids-limit", "256", "--security-opt", "no-new-privileges",
172 |                 "-v", f"{self._root}:{CONTAINER_WORKDIR}", "-w", CONTAINER_WORKDIR]
173 |         if not self._cfg.sandbox_network:
174 |             argv += ["--network", "none"]
175 |         if sys.platform != "win32":
176 |             uid, gid = os.getuid(), os.getgid()
177 |             if uid != 0:                           # Linux/macOS: the files stay yours (see docs)
178 |                 argv += ["--user", f"{uid}:{gid}", "-e", "HOME=/tmp"]
179 |         code, out = self._docker(*argv, self._cfg.sandbox_image)
180 |         if code != 0:
181 |             raise RuntimeError(f"Could not start the sandbox container: {out.strip()[:300]}\n"
182 |                                "Is Docker Desktop running?")
183 |         self._started = True
184 |         atexit.register(self.stop)
185 | 
```
- Does nothing if already started.
- Checks that the sandbox image exists; if not, raises an error that contains the exact build command.
- Builds the `docker run` command:
  - `-d` run in the background, `--rm` delete the container automatically once it stops, `--name` our unique name;
  - `--memory 1g`, `--cpus 1.0` resource caps (settings `sandbox_memory`, `sandbox_cpus`);
  - `--pids-limit 256` stops "fork bombs" (programs that create processes endlessly);
  - `--security-opt no-new-privileges` stops programs from gaining extra rights;
  - `-v <workspace>:/work -w /work` mounts the workspace and starts there;
  - `--network none` unless `sandbox_network` is on (`--network` flag);
  - on Linux and macOS, `--user <your user id>:<your group id>` and `HOME=/tmp`: there the shared folder keeps the
    host's file ownership, so the image's own user (id 1000) could not read a workspace belonging to another user id,
    and files the code creates would belong to a stranger. Running as you keeps every file yours — and still not as
    root (when you yourself are root, the image's normal user is kept). Docker Desktop on Windows does not enforce
    ownership, so nothing is added there. Found while adding the CI job that builds and runs the sandbox on Linux,
    where the runner's user id is 1001.
- If Docker fails (for example Docker Desktop is not running) the error says so. On success it remembers it started
  and registers `stop` to run at exit.

```python
186 |     def stop(self) -> None:
187 |         """Remove the container (safe to call more than once, even after the folder is gone)."""
188 |         if not self._started:
189 |             return
190 |         self._started = False
191 |         try:                                       # no cwd: the workspace may already be deleted
192 |             subprocess.run(["docker", "rm", "-f", self.name], capture_output=True, timeout=60)
193 |         except (OSError, subprocess.TimeoutExpired):
194 |             pass                                   # best effort; `docker ps` shows leftovers
195 | 
```
- Removes the container with `docker rm -f`. It deliberately runs **without** a working folder: in the eval harness the
  temporary workspace may already be deleted when clean-up runs, and Windows refuses to start a program in a folder that
  no longer exists (this was a real bug: containers were left running). Errors are ignored — clean-up is best effort,
  and `docker ps` shows any leftovers.

```python
196 |     def _tests_command(self) -> list[str]:
197 |         """The project's test command inside the container (see ``test_kind``)."""
198 |         kind = test_kind(self._root)
199 |         if kind == "npm":
200 |             return ["npm", "test"]
201 |         if kind == "go":
202 |             return ["go", "test", "./..."]
203 |         if kind == "cargo":
204 |             return ["cargo", "test"]
205 |         return ["sh", "-c", PYTHON_TESTS_SH.format(start=_unittest_start(self._root))]
206 | 
```
- The same choice as on the host, inside the container: `npm test`, `go test ./...`, `cargo test`, or the Python
  command `PYTHON_TESTS_SH` (pytest when installed — the sandbox image has it — else unittest). The container's own
  Python is used: a Windows virtual environment cannot run on Linux.

```python
207 |     def run(self, kind: str, args: list[str]) -> tuple[int, str]:
208 |         """Run inside the container; a timeout also kills the processes inside it."""
209 |         try:
210 |             self.start()
211 |         except RuntimeError as exc:
212 |             return 125, str(exc)
213 |         seconds = self._cfg.command_timeout
214 |         if kind == "python":
215 |             inner = ["python", *args]
216 |         elif kind == "tests":
217 |             inner = ["sh", "-c", args[0]] if args else self._tests_command()
218 |             seconds = max(self._cfg.test_timeout, seconds)
219 |         else:
220 |             inner = ["sh", "-c", args[0]]
221 |         argv = ["docker", "exec", self.name, "timeout", "-s", "KILL", str(seconds), *inner]
222 |         code, out = run_command(argv, self._root, seconds + 15, self._cfg.max_output_chars)
223 |         if code == 137:                           # killed by `timeout -s KILL`
224 |             return 124, f"Command timed out after {seconds}s and was killed.\n{out}"
225 |         return code, out
226 | 
227 | 
```
- Starts the container if needed (a start failure becomes exit code 125 with the advice text, so the model and you
  see it instead of a crash).
- Chooses the inner command: `python file args`; for tests your `test_command` (through `sh -c`) or
  `_tests_command()`, with the longer `test_timeout`; or `sh -c "<command line>"`.
- Wraps it in `timeout -s KILL <seconds>` *inside* the container, so a runaway program is really killed there (killing
  only the `docker exec` client on Windows would leave it running). The outer timeout has 15 s of slack for Docker.
- Exit code 137 means "killed by KILL", reported as our usual timeout code 124 with a clear message.

## Choosing and checking
```python
228 | def make_runner(config: Config, root: Path) -> Runner:
229 |     """Pick the runner named by ``config.sandbox``."""
230 |     if config.sandbox == "docker":
231 |         return DockerRunner(config, root)
232 |     if config.sandbox == "local":
233 |         return LocalRunner(config, root)
234 |     raise ValueError(f"unknown sandbox '{config.sandbox}'. Choose 'local' or 'docker'.")
235 | 
236 | 
```
- Builds the runner named by `config.sandbox`, or explains the valid names.

```python
237 | def sandbox_image_ready(image: str) -> bool:
238 |     """True when Docker answers AND the sandbox image exists (one quick command)."""
239 |     try:
240 |         done = subprocess.run(["docker", "image", "inspect", image], capture_output=True,
241 |                               timeout=20)
242 |     except (OSError, subprocess.TimeoutExpired):
243 |         return False
244 |     return done.returncode == 0
245 | 
246 | 
```
- One `docker image inspect <image>`: succeeds only when the Docker engine answers **and** the image exists. The CLI uses
  it to pick the Docker sandbox automatically.

```python
247 | def docker_available() -> bool:
248 |     """True when the docker command exists and its daemon answers."""
249 |     try:
250 |         done = subprocess.run(["docker", "info", "--format", "{{.ServerVersion}}"],
251 |                               capture_output=True, text=True, timeout=20)
252 |     except (OSError, subprocess.TimeoutExpired):
253 |         return False
254 |     return done.returncode == 0
```
- Runs `docker info`; `True` only if the `docker` program exists **and** the Docker engine answers. Used by
  `--doctor` and to skip the Docker test when Docker is not running.
