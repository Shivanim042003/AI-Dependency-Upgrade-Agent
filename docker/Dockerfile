# Runner image for sandboxed test execution. Build once:  docker build -t pmigrate-runner docker/
# Python interpreters are provisioned per task by uv (cached in the mounted /uvpython volume),
# so one image serves every task's Python version.
FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
      git ca-certificates curl build-essential libffi-dev libssl-dev ripgrep \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/
# Non-root user is supplied at `docker run --user`; keep the filesystem world-readable.
ENV UV_LINK_MODE=copy PYTHONDONTWRITEBYTECODE=1
