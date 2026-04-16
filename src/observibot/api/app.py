"""FastAPI application factory."""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from observibot import __version__
from observibot.api.routes import (
    auth,
    chat,
    diagnostics,
    discovery,
    events,
    insights,
    knowledge,
    metrics,
    system,
    widgets,
)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_DOCKER_DIST = Path("/app/frontend/dist")
FRONTEND_DIST = _DOCKER_DIST if _DOCKER_DIST.is_dir() else _PROJECT_ROOT / "frontend" / "dist"


def create_app() -> FastAPI:
    """Build the FastAPI application with all routes mounted."""
    app = FastAPI(
        title="Observibot",
        version=__version__,
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )

    app.include_router(auth.router)
    app.include_router(system.router)
    app.include_router(system.health_router)
    app.include_router(discovery.router)
    app.include_router(metrics.router)
    app.include_router(insights.router)
    app.include_router(events.router)
    app.include_router(widgets.router)
    app.include_router(chat.router)
    app.include_router(knowledge.router)
    app.include_router(diagnostics.router)

    if FRONTEND_DIST.is_dir():
        app.mount("/", StaticFiles(directory=str(FRONTEND_DIST), html=True), name="frontend")

    return app
