"""Dependency injection for FastAPI routes."""
from __future__ import annotations

import os
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import Cookie, HTTPException, status
from jose import JWTError, jwt

from observibot.core.store import Store

SECRET_KEY = os.getenv("OBSERVIBOT_SECRET_KEY", "")
ALGORITHM = "HS256"
TOKEN_EXPIRE_HOURS = 24

_store_instance: Store | None = None
_analyzer_instance: object | None = None
_app_db_instance: object | None = None
_monitor_loop_instance: object | None = None
_chat_config_instance: object | None = None


def _get_secret_key() -> str:
    global SECRET_KEY
    if not SECRET_KEY:
        SECRET_KEY = secrets.token_urlsafe(32)
    return SECRET_KEY


def set_store(store: Store) -> None:
    """Set the shared Store instance for dependency injection."""
    global _store_instance
    _store_instance = store


def set_analyzer(analyzer: object) -> None:
    """Set the shared Analyzer instance."""
    global _analyzer_instance
    _analyzer_instance = analyzer


def get_analyzer():
    """Return the shared Analyzer, or None if not configured."""
    return _analyzer_instance


def set_app_db(app_db: object) -> None:
    """Set the shared AppDatabasePool instance."""
    global _app_db_instance
    _app_db_instance = app_db


def get_app_db():
    """Return the shared AppDatabasePool, or None if disabled."""
    return _app_db_instance


def set_monitor_loop(monitor_loop: object) -> None:
    """Set the shared MonitorLoop instance."""
    global _monitor_loop_instance
    _monitor_loop_instance = monitor_loop


def get_monitor_loop():
    """Return the shared MonitorLoop, or None if not running."""
    return _monitor_loop_instance


def set_chat_config(chat_config: object) -> None:
    """Set the shared ChatConfig instance used by the chat route."""
    global _chat_config_instance
    _chat_config_instance = chat_config


def get_chat_config():
    """Return the shared ChatConfig, or None if monitor hasn't registered one."""
    return _chat_config_instance


async def get_store() -> Store:
    """Return the shared Store instance."""
    if _store_instance is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Store not initialized",
        )
    return _store_instance


def create_access_token(data: dict[str, Any]) -> str:
    """Create a JWT access token."""
    to_encode = data.copy()
    expire = datetime.now(UTC) + timedelta(hours=TOKEN_EXPIRE_HOURS)
    to_encode["exp"] = expire
    return jwt.encode(to_encode, _get_secret_key(), algorithm=ALGORITHM)


async def get_current_user(
    access_token: str | None = Cookie(default=None),
) -> dict[str, Any]:
    """Validate JWT from cookie and return user info."""
    if not access_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )
    try:
        payload = jwt.decode(access_token, _get_secret_key(), algorithms=[ALGORITHM])
        user_id: str | None = payload.get("sub")
        if user_id is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token",
            )
        return {
            "id": user_id,
            "email": payload.get("email", ""),
            "is_admin": payload.get("is_admin", False),
        }
    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        ) from exc
