"""FastAPI application factory."""
from __future__ import annotations

from fastapi import FastAPI

from observibot import __version__
from observibot.api.routes import auth, chat, discovery, insights, metrics, system, widgets


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
    app.include_router(discovery.router)
    app.include_router(metrics.router)
    app.include_router(insights.router)
    app.include_router(widgets.router)
    app.include_router(chat.router)

    return app
