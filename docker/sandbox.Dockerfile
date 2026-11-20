# Image for the code sandbox: every command the agent runs (python, tests, shell) runs in here.
# Build: docker build -t localagent-sandbox:latest -f docker/sandbox.Dockerfile docker
FROM python:3.12-slim

# Languages the agent's code may need, so it can RUN what it writes, not only check it:
# git, Node.js (JavaScript), gcc/g++ (C/C++), Go, Rust (rustc + cargo). Clean apt lists to stay small.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git nodejs gcc g++ libc6-dev golang-go \
       rustc cargo ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Go and Rust keep build caches in writable places (the user below has no other writable folders).
ENV GOCACHE=/tmp/go-cache GOPATH=/tmp/go CARGO_HOME=/tmp/cargo

# Tools the agent's code commonly needs: test runners and the same linters the agent uses.
RUN pip install --no-cache-dir pytest==8.3.5 ruff==0.11.13 mypy==1.15.0

# Run as an ordinary user, never root, so a bad command cannot change the image's system files.
RUN useradd --create-home --uid 1000 agent
USER agent

# The workspace folder from the host is mounted here.
WORKDIR /work

# Keep the container alive; commands are started inside it with `docker exec`.
CMD ["sleep", "infinity"]
