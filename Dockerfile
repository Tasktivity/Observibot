# syntax=docker/dockerfile:1.7

# Stage 1: Build frontend
FROM node:20-alpine AS frontend-build
WORKDIR /frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ .
RUN npm run build

# Stage 2: Python app
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
        ca-certificates \
        tini \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md LICENSE ./
COPY src/ src/
RUN pip install --no-cache-dir .

RUN mkdir -p /app/data

COPY config/observibot.example.yaml /app/config/observibot.yaml

COPY --from=frontend-build /frontend/dist /app/frontend/dist

ENV OBSERVIBOT_CONFIG=/app/config/observibot.yaml

EXPOSE 8080

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["observibot", "run"]
