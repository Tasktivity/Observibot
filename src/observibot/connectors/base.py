"""Connector abstract base class and capability declarations."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Flag, auto
from typing import Any

from observibot.core.models import (
    ChangeEvent,
    HealthStatus,
    MetricSnapshot,
    SystemFragment,
)


class Capability(Flag):
    """Individual capabilities a connector may advertise.

    The monitor loop checks capabilities before calling methods so that
    asymmetric connectors (e.g. Railway has deploys but no CPU metrics)
    can coexist with rich ones without the core assuming feature parity.
    """

    DISCOVERY = auto()
    METRICS = auto()
    CHANGES = auto()
    HEALTH = auto()
    RESOURCE_METRICS = auto()  # CPU/memory/network
    CODE_ACCESS = auto()       # source code file retrieval
    CODE_CHANGES = auto()      # commit/PR change detection


@dataclass(frozen=True)
class ConnectorCapabilities:
    """What a connector actually supports.

    Attributes:
        capabilities: Bitmask of :class:`Capability` values.
        requires_elevated_role: True if the connector needs a non-default
            DB role (e.g. ``pg_monitor``) or elevated API scope.
        has_rate_limits: True if the connector hits an external API with
            rate limits; the monitor will serialize calls.
        notes: Human-readable notes surfaced to the user in ``health`` output.
    """

    capabilities: Capability
    requires_elevated_role: bool = False
    has_rate_limits: bool = False
    notes: list[str] = field(default_factory=list)

    def supports(self, capability: Capability) -> bool:
        return capability in self.capabilities


class BaseConnector(ABC):
    """Abstract connector to an external system.

    Subclasses implement discovery, metric collection, change detection, and
    health checks. Connectors are read-only by contract — they MUST NOT mutate
    state in the systems they observe.

    Lifecycle: call :meth:`connect` on startup (once), :meth:`close` on
    shutdown. Individual methods (``discover``, ``collect_metrics``, etc.)
    may be called many times in between and must tolerate being called
    repeatedly without reconnecting.
    """

    type: str = "base"

    def __init__(self, name: str, config: dict[str, Any]) -> None:
        self.name = name
        self.config = config

    @abstractmethod
    def get_capabilities(self) -> ConnectorCapabilities:
        """Declare what this connector supports."""

    @abstractmethod
    async def connect(self) -> None:
        """Open any persistent resources (pools, clients). Idempotent."""

    @abstractmethod
    async def discover(self) -> SystemFragment:
        """Discover the structure of this system."""

    @abstractmethod
    async def collect_metrics(self) -> list[MetricSnapshot]:
        """Collect metric snapshots for this system."""

    @abstractmethod
    async def get_recent_changes(self, since: datetime) -> list[ChangeEvent]:
        """Return change events that occurred since ``since``."""

    @abstractmethod
    async def health_check(self) -> HealthStatus:
        """Quick check that this connector can reach its target."""

    @abstractmethod
    def required_permissions(self) -> list[str]:
        """List of human-readable permissions this connector needs."""

    async def close(self) -> None:
        """Release any resources (connection pools, HTTP clients, etc.)."""
        return None
