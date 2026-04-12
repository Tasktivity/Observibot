"""Minimal health endpoint for container orchestrators.

Phase 2 only — this is NOT the future Phase 3 web UI. It exposes a single
``/health`` endpoint so Railway, Docker, and Kubernetes can probe liveness.
"""
from __future__ import annotations

import asyncio
import logging

from fastapi import FastAPI

from observibot import __version__

log = logging.getLogger(__name__)

health_app = FastAPI(
    title="Observibot Health",
    version=__version__,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


@health_app.get("/health")
async def health() -> dict[str, str]:
    """Liveness probe used by Railway/Docker/Kubernetes."""
    return {"status": "ok", "version": __version__}


@health_app.get("/")
async def root() -> dict[str, str]:
    """Friendly index so curl-ing the bare host returns something."""
    return {
        "service": "observibot",
        "version": __version__,
        "health": "/health",
    }


async def serve_health(host: str = "0.0.0.0", port: int = 8080) -> None:
    """Run the full FastAPI app (API + web UI + health) under uvicorn.

    Falls back to the minimal health_app if the full API can't be loaded.
    Imported lazily so unit tests don't pay the uvicorn import cost. The
    coroutine returns when the server stops; the caller is responsible for
    cancellation on shutdown.
    """
    import uvicorn

    try:
        from observibot.api.app import create_app

        app = create_app()
        log.info("Serving full web UI + API on http://%s:%s", host, port)
    except Exception as exc:
        log.warning("Could not load full API (%s), falling back to health-only", exc)
        app = health_app

    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_level="warning",
        access_log=False,
    )
    server = uvicorn.Server(config)
    log.info("Health endpoint listening on http://%s:%s/health", host, port)
    await server.serve()


def start_health_server(
    host: str = "0.0.0.0", port: int = 8080
) -> asyncio.Task[None]:
    """Schedule the health server on the current event loop and return its task."""
    return asyncio.create_task(serve_health(host=host, port=port))
