# Docker files — the sandbox image, the agent image, and the service

**What Docker is, in one paragraph:** Docker runs programs inside *containers*: small, separate Linux environments with
their own files, processes and network. An *image* is the recipe-built template (made from a `Dockerfile`); a
*container* is a running copy of an image. Containers start in about a second and can be thrown away, which makes them
ideal for running code you do not fully trust. On Windows, Docker Desktop provides the Linux engine.

This project uses Docker in two independent ways — pick either or both:

| Use | File | What runs inside | When to use |
|---|---|---|---|
| **Code sandbox** | `docker/sandbox.Dockerfile` | only the code the agent *runs* (python, tests, shell) | agent on your PC, but its code isolated: `--sandbox docker` |
| **Agent container** | `Dockerfile` (+ `.dockerignore`) | the whole agent | nothing of the agent touches your PC except the mounted workspace folder |
| **Agent service** | `docker-compose.yml` | the HTTP server (`server.py`) | other programs/people use the agent over HTTP |

These files are not Python, so the line-by-line audit does not cover them; every line is explained here instead.

---
## `docker/sandbox.Dockerfile` — the code sandbox image

```dockerfile
FROM python:3.12-slim
RUN apt-get update \
    && apt-get install -y --no-install-recommends git nodejs gcc g++ libc6-dev golang-go \
       rustc cargo ca-certificates \
    && rm -rf /var/lib/apt/lists/*
ENV GOCACHE=/tmp/go-cache GOPATH=/tmp/go CARGO_HOME=/tmp/cargo
RUN pip install --no-cache-dir pytest==8.3.5 ruff==0.11.13 mypy==1.15.0
RUN useradd --create-home --uid 1000 agent
USER agent
WORKDIR /work
CMD ["sleep", "infinity"]
```
- `FROM python:3.12-slim` — start from the official small Debian Linux image that already contains Python 3.12 (a
  stable version, unlike the beta on this PC).
- `RUN apt-get ...` — install, from Debian's package list, what the agent's code may need **to run**, not only to be
  checked: `git`, Node.js, the C/C++ compilers (`gcc`, `g++`, `libc6-dev`), Go (`golang-go`) and Rust (`rustc`,
  `cargo`); `ca-certificates` lets HTTPS work when `--network` is on. `--no-install-recommends` and deleting the
  package lists keep the image as small as these compilers allow (several hundred MB; built once).
- `ENV GOCACHE ... CARGO_HOME ...` — Go and Rust write build caches; these point them at `/tmp`, which the unprivileged
  user may write to.
- `RUN pip install ...` — install test and lint tools the agent's code commonly needs (`pytest`, `ruff`, `mypy`). Exact
  versions (`==`) make the image identical every time you build it. `--no-cache-dir` keeps the image smaller.
- `RUN useradd ... agent` / `USER agent` — create and switch to an ordinary user (id 1000). Code in the container is
  then **not root**, so it cannot modify the image's system files.
- `WORKDIR /work` — the folder commands start in; `runners.py` mounts your workspace here.
- `CMD ["sleep", "infinity"]` — the container's main program just waits forever; real commands are started inside it
  with `docker exec` (see `runners.md`).

Build it (once — a few minutes the first time because of the compilers; again only if you edit the file):
```
docker build -t localagent-sandbox:latest -f docker/sandbox.Dockerfile docker
```
`-t` names the image; `-f` says which Dockerfile; the last argument (`docker`) is the folder Docker may copy files from.

Runtime limits are not in the image but in the `docker run` command in `runners.py`: no network, 1 GB memory, 1 CPU,
256 processes, `no-new-privileges`, only the workspace mounted.

---
## `Dockerfile` — the agent itself in a container

```dockerfile
FROM python:3.12-slim
RUN pip install --no-cache-dir ruff==0.11.13 mypy==1.15.0
RUN useradd --create-home --uid 1000 agent
WORKDIR /app
COPY --chown=agent:agent localagent ./localagent
COPY --chown=agent:agent evals ./evals
RUN mkdir -p /app/workspace && chown -R agent:agent /app
USER agent
ENV LOCALAGENT_BACKEND=gemini LOCALAGENT_SANDBOX=local LOCALAGENT_WORKSPACE=/app/workspace \
    LOCALAGENT_FALLBACK=openai LOCALAGENT_API_BASE=http://host.docker.internal:8080/v1 PYTHONUNBUFFERED=1
ENTRYPOINT ["python", "-m", "localagent"]
```
- Same base image. `ruff` and `mypy` are installed because the agent's **write gate** uses them.
- `WORKDIR /app` — the program lives in `/app`.
- `COPY ... localagent` / `evals` — copy only the program code (the agent needs no other project files). `--chown`
  makes the `agent` user the owner.
- `mkdir -p /app/workspace` — the work folder (your project is mounted here). Conversation logs go into the project
  itself (`/app/workspace/.localagent/sessions`, so `--resume` works across container runs); the quota counts
  (`usage.json`) go to `/home/agent/.localagent`, which the run command below mounts from your computer.
- `USER agent` — run as the ordinary user.
- `ENV ...` — default settings (read by `Config.from_env`): use the Gemini chain; run code inside this same container
  (`local` — the container is already the isolation, and Docker-inside-Docker is not needed); workspace `/app/workspace`;
  as a last fallback use the llama.cpp server on **your PC**, which a container reaches as `host.docker.internal`.
  `PYTHONUNBUFFERED=1` makes output appear immediately.
- `ENTRYPOINT` — running the container runs `python -m localagent`; anything you add after the image name becomes
  extra flags (e.g. `--doctor`, `--auto-approve`).

Build: `docker build -t localagent:latest .`

Chat with the containerized agent (PowerShell, from the project folder):
```
docker run -it --rm -e GEMINI_API_KEY -v "${PWD}:/app/workspace" -v "$HOME\.localagent:/home/agent/.localagent" localagent:latest
```
- `-it` interactive terminal; `--rm` delete the container when you quit.
- `-e GEMINI_API_KEY` passes your key **from your environment** (the key is never stored in the image).
- First `-v` mounts the **current folder** (your project) as the workspace, so the agent works on your files.
- Second `-v` mounts `%USERPROFILE%\.localagent` (where `usage.json` lives) so the **daily quota count is shared** with
  the agent outside Docker. Without it, the container starts counting from 0 every time.

## `.dockerignore`
Lists what `docker build` must **not** copy into the build: the virtual environment, model files and llama.cpp binaries
(gigabytes), your workspace and sessions (private), docs and tests (not needed to run), caches and git data. This keeps
the build fast, the image small, and private files out of images.

---
## `docker-compose.yml` — the agent as a service

Docker Compose starts containers from a description file instead of a long command.
```yaml
services:
  agent-server:
    build: .                      # build the image from ./Dockerfile
    image: localagent:latest      # ...and call it this
    entrypoint: ["python", "-m", "localagent.server", "--host", "0.0.0.0", "--port", "8000",
                 "--root", "/app/sessions", "--auto-approve"]
    environment:
      GEMINI_API_KEY: ${GEMINI_API_KEY:?set GEMINI_API_KEY first}
      LOCALAGENT_TOKEN: ${LOCALAGENT_TOKEN:?set LOCALAGENT_TOKEN to a long random secret}
    ports: ["127.0.0.1:8000:8000"]
    volumes: ["./sessions:/app/sessions", "${USERPROFILE:-~}/.localagent:/home/agent/.localagent"]
    mem_limit: 2g
    cpus: 2
    restart: unless-stopped
```
- `entrypoint` — run the HTTP server instead of the chat. `--host 0.0.0.0` listens on all of the *container's* network
  interfaces (required so Docker can forward traffic in). `--auto-approve` lets sessions run code without a human —
  acceptable here only because everything runs inside this container.
- `environment` — `${NAME:?message}` takes the value from your environment and **refuses to start** with that message
  if it is missing, so the server can never run without a key or a token.
- `ports: 127.0.0.1:8000:8000` — publish port 8000 **only to this computer** (not your network).
- `volumes` — keep each session's files on your disk across restarts (`./sessions`), and share the quota counters
  with the host. (An earlier version also mounted `./.localagent` "for session logs", but the server keeps no
  conversation logs, so that mount only created an empty folder; it was removed in the final verification.)
- `mem_limit`, `cpus` — cap the whole service. `restart: unless-stopped` restarts it after crashes or reboots.

Start / stop (PowerShell):
```
$env:LOCALAGENT_TOKEN = "pick-a-long-random-secret"
docker compose up -d --build
docker compose logs -f          # watch it (Ctrl+C stops watching, not the server)
docker compose down
```
Talk to it — see `server.md` for the API (`POST /sessions`, then `POST /sessions/<id>/messages`).

## Housekeeping commands
| Command | Does |
|---|---|
| `docker ps` | list running containers (sandbox containers are named `localagent-...`) |
| `docker rm -f <name>` | remove a leftover container |
| `docker images` | list images |
| `docker image prune` | delete unused image layers to free disk space |
