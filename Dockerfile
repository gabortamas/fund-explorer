# Recipe for the fund explorer API image.
#
# Build:  docker build -t fund-explorer-api .
# Run:    docker run --rm -p 8000:8000 -v "${PWD}/data:/data" fund-explorer-api
#
# Each instruction below creates a "layer". Docker caches layers and reuses them when
# nothing they depend on has changed, so the order matters: things that rarely change
# (the Python packages) come first, things that change often (your code) come last.
#
# This is a two-stage build. Stage 1 ("builder") has the tools needed to install
# packages. Stage 2 copies only the finished result, so the final image is smaller
# and contains no build tools.

# ---------------------------------------------------------------- stage 1: builder
FROM python:3.14-slim AS builder

# Borrow the uv program from its official image (no need to pip-install it).
COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /uvx /bin/

WORKDIR /app
# Compile .py files to bytecode now, so the container starts faster later.
# Copy files instead of hard-linking them (links do not survive the copy to stage 2).
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy

# 1) Install the dependencies only. These two files rarely change, so this slow step
#    is cached and skipped on most rebuilds. --locked: fail if uv.lock is out of date
#    instead of silently resolving new versions.
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-install-project

# 2) Install your own code. --no-editable copies it into the virtual environment,
#    so stage 2 does not need the src/ folder at all.
COPY README.md ./
COPY src ./src
RUN uv sync --locked --no-editable

# ---------------------------------------------------------------- stage 2: runtime
FROM python:3.14-slim

# Run as an ordinary user, not as root: if someone ever broke into the API, they
# would not be the all-powerful user inside the container.
RUN useradd --create-home --uid 1000 app

COPY --from=builder --chown=app:app /app/.venv /app/.venv

# PATH: so the `fundrag` command is found.
# FUNDRAG_DATA_DIR: where the code looks for data (see src/fundrag/config.py).
#   The image contains no data; you attach your data folder at /data when you run it.
ENV PATH="/app/.venv/bin:$PATH" \
    FUNDRAG_DATA_DIR=/data \
    PYTHONUNBUFFERED=1

USER app
WORKDIR /home/app

# Documentation only: the API listens on port 8000 inside the container.
EXPOSE 8000

# Docker calls /health every 30 s and marks the container healthy or unhealthy.
# (The slim image has no curl, so Python does the request.)
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)"]

# 0.0.0.0 = accept connections from outside the container (127.0.0.1 would only
# accept connections from inside it, and the API would be unreachable).
CMD ["fundrag", "serve", "--host", "0.0.0.0", "--port", "8000"]
