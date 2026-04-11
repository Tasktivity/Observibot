# syntax=docker/dockerfile:1.7
FROM python:3.12-slim AS base

# Avoid prompts and shrink image
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# System packages: nothing fancy, but libpq is handy if any future
# connector wants psycopg.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
        ca-certificates \
        tini \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps first so the layer caches across source changes.
COPY pyproject.toml README.md LICENSE ./
COPY src/ src/
RUN pip install --no-cache-dir .

# Persistent state — Railway/Docker volumes mount over this directory.
RUN mkdir -p /app/data

# Default config: env-var placeholders are resolved at runtime by load_config().
COPY config/observibot.example.yaml /app/config/observibot.yaml

ENV OBSERVIBOT_CONFIG=/app/config/observibot.yaml

EXPOSE 8080

# tini reaps zombies and forwards SIGTERM cleanly to observibot,
# which the monitor loop already handles via signal handlers.
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["observibot", "run"]
