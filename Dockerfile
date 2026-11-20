# Image for the AGENT ITSELF (not the code sandbox). The whole agent then runs inside a
# container, so everything it does is isolated from your computer.
# Build:  docker build -t localagent:latest .
# Chat:   docker run -it --rm -e GEMINI_API_KEY -v "${PWD}:/app/workspace" -v "$HOME/.localagent:/home/agent/.localagent" localagent:latest
FROM python:3.12-slim

# Verification layers (ruff = lint, mypy = types) used by the agent's write gate.
RUN pip install --no-cache-dir ruff==0.11.13 mypy==1.15.0

# Ordinary user, never root.
RUN useradd --create-home --uid 1000 agent
WORKDIR /app

# Copy only the program (see .dockerignore for what is left out).
COPY --chown=agent:agent localagent ./localagent
COPY --chown=agent:agent evals ./evals
RUN mkdir -p /app/workspace && chown -R agent:agent /app
USER agent

# Defaults: Gemini models, code runs inside this same container (it is already isolated),
# and a local model server on the host machine as the last fallback.
ENV LOCALAGENT_BACKEND=gemini \
    LOCALAGENT_SANDBOX=local \
    LOCALAGENT_WORKSPACE=/app/workspace \
    LOCALAGENT_FALLBACK=openai \
    LOCALAGENT_API_BASE=http://host.docker.internal:8080/v1 \
    PYTHONUNBUFFERED=1

ENTRYPOINT ["python", "-m", "localagent"]

