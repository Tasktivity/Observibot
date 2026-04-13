"""Connector registry and factory."""
from __future__ import annotations

from typing import Any

from observibot.connectors.base import BaseConnector


class UnknownConnectorError(ValueError):
    """Raised when an unknown connector type is requested."""


def get_connector(name: str, type: str, config: dict[str, Any]) -> BaseConnector:
    """Build a connector instance for the given type.

    Args:
        name: Human-friendly name from config.
        type: Connector type identifier (e.g. ``supabase``).
        config: Full connector config dict from YAML.

    Returns:
        A constructed :class:`BaseConnector` subclass.

    Raises:
        UnknownConnectorError: If ``type`` is not registered.
    """
    # Imports are local to avoid pulling drivers at package import time.
    from observibot.connectors.github import GitHubConnector
    from observibot.connectors.postgresql import PostgreSQLConnector
    from observibot.connectors.railway import RailwayConnector
    from observibot.connectors.supabase import SupabaseConnector

    registry: dict[str, type[BaseConnector]] = {
        "supabase": SupabaseConnector,
        "postgresql": PostgreSQLConnector,
        "postgres": PostgreSQLConnector,
        "railway": RailwayConnector,
        "github": GitHubConnector,
    }
    cls = registry.get(type.lower())
    if cls is None:
        known = ", ".join(sorted(registry.keys()))
        raise UnknownConnectorError(
            f"Unknown connector type '{type}' for '{name}'. Known types: {known}"
        )
    return cls(name=name, config=config)


__all__ = ["BaseConnector", "UnknownConnectorError", "get_connector"]
