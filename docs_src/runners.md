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
@@ localagent/runners.py header @@
- `os` tells Windows from other systems; `atexit` registers the clean-up that removes the container when Python exits; `subprocess` runs `docker info`;
  `uuid` makes a unique container name; `run_command` (from `sandbox.py`) runs a program with a timeout and output cap.
- `CONTAINER_WORKDIR = "/work"` is where the workspace appears inside the container.

## `Runner` — the shared interface
@@ localagent/runners.py Runner @@
@@ localagent/runners.py Runner.run @@
@@ localagent/runners.py Runner.stop @@
- A `Protocol` (like `ChatBackend` in `llm.py`): anything with `supports_shell`, `shell_label` (where commands run,
  shown to the model and in approval questions), `run(kind, args)` and `stop()` is a runner. `kind` is `"python"`, `"tests"` or `"shell"`; the result is `(exit code, output text)`.

## `LocalRunner`
@@ localagent/runners.py LocalRunner @@
@@ localagent/runners.py LocalRunner.__init__ @@
@@ localagent/runners.py LocalRunner.run @@
@@ localagent/runners.py LocalRunner.stop @@
- The shell is **opt-in**: `supports_shell` comes from `allow_host_shell` (`--allow-shell`). Claude Code also runs
  commands on your computer, always asking first; we do the same — the toolbox asks for approval for every command.
  Without the flag the shell tool is not offered, and a shell request is refused with code 126.
- Shell commands go through `shell_argv` (`sandbox.py`): PowerShell on Windows, `sh -c` elsewhere.
- `python` runs `<this Python> <file> <args>`; `tests` runs `python -m unittest discover`; both inside the workspace
  folder with the configured timeout and output cap. `stop` has nothing to do.

## `DockerRunner`
@@ localagent/runners.py DockerRunner @@
@@ localagent/runners.py DockerRunner.__init__ @@
- `shell_label` says commands run in an isolated Linux container. Picks a unique container name like `localagent-3f9c0a1b2d` so several agents can run at once.

@@ localagent/runners.py DockerRunner._docker @@
- Small helper: run `docker <args...>` with a timeout.

@@ localagent/runners.py DockerRunner.start @@
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

@@ localagent/runners.py DockerRunner.stop @@
- Removes the container with `docker rm -f`. It deliberately runs **without** a working folder: in the eval harness the
  temporary workspace may already be deleted when clean-up runs, and Windows refuses to start a program in a folder that
  no longer exists (this was a real bug: containers were left running). Errors are ignored — clean-up is best effort,
  and `docker ps` shows any leftovers.

@@ localagent/runners.py DockerRunner.run @@
- Starts the container if needed (a start failure becomes exit code 125 with the advice text, so the model and you
  see it instead of a crash).
- Chooses the inner command: `python file args`, `python -m unittest discover`, or `sh -c "<command line>"`.
- Wraps it in `timeout -s KILL <seconds>` *inside* the container, so a runaway program is really killed there (killing
  only the `docker exec` client on Windows would leave it running). The outer timeout has 15 s of slack for Docker.
- Exit code 137 means "killed by KILL", reported as our usual timeout code 124 with a clear message.

## Choosing and checking
@@ localagent/runners.py make_runner @@
- Builds the runner named by `config.sandbox`, or explains the valid names.

@@ localagent/runners.py sandbox_image_ready @@
- One `docker image inspect <image>`: succeeds only when the Docker engine answers **and** the image exists. The CLI uses
  it to pick the Docker sandbox automatically.

@@ localagent/runners.py docker_available @@
- Runs `docker info`; `True` only if the `docker` program exists **and** the Docker engine answers. Used by
  `--doctor` and to skip the Docker test when Docker is not running.
