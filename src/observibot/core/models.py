"""Core data models for Observibot.

These dataclasses describe what we discover about user systems and the events,
metrics, and insights produced during monitoring. All datetimes are timezone-
aware UTC. All identifiers are short hex strings.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


def _new_id() -> str:
    """Return a fresh 12-char hex identifier."""
    return uuid.uuid4().hex[:12]


def _utcnow() -> datetime:
    """Return the current UTC time as a timezone-aware datetime."""
    return datetime.now(UTC)


def _to_iso(dt: datetime | None) -> str | None:
    """Serialize a datetime as an ISO-8601 string."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.isoformat()


def _from_iso(value: Any) -> datetime | None:
    """Parse an ISO-8601 string back into a UTC datetime."""
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


@dataclass
class TableInfo:
    """A database table discovered in a connector."""

    name: str
    schema: str = "public"
    columns: list[dict[str, Any]] = field(default_factory=list)
    row_count: int | None = None
    indexes: list[dict[str, Any]] = field(default_factory=list)
    rls_policies: list[dict[str, Any]] = field(default_factory=list)
    primary_key: list[str] = field(default_factory=list)
    estimated_size_bytes: int | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable dict for this table."""
        return {
            "name": self.name,
            "schema": self.schema,
            "columns": list(self.columns),
            "row_count": self.row_count,
            "indexes": list(self.indexes),
            "rls_policies": list(self.rls_policies),
            "primary_key": list(self.primary_key),
            "estimated_size_bytes": self.estimated_size_bytes,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TableInfo:
        """Reconstruct a :class:`TableInfo` from ``to_dict`` output."""
        return cls(
            name=data["name"],
            schema=data.get("schema", "public"),
            columns=list(data.get("columns") or []),
            row_count=data.get("row_count"),
            indexes=list(data.get("indexes") or []),
            rls_policies=list(data.get("rls_policies") or []),
            primary_key=list(data.get("primary_key") or []),
            estimated_size_bytes=data.get("estimated_size_bytes"),
        )

    @property
    def fqn(self) -> str:
        """Fully qualified ``schema.table`` name."""
        return f"{self.schema}.{self.name}"


@dataclass
class Relationship:
    """A foreign key relationship between two tables."""

    from_table: str
    from_column: str
    to_table: str
    to_column: str
    constraint_name: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "from_table": self.from_table,
            "from_column": self.from_column,
            "to_table": self.to_table,
            "to_column": self.to_column,
            "constraint_name": self.constraint_name,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Relationship:
        return cls(
            from_table=data["from_table"],
            from_column=data["from_column"],
            to_table=data["to_table"],
            to_column=data["to_column"],
            constraint_name=data.get("constraint_name"),
        )


@dataclass
class ServiceInfo:
    """A deployed service (e.g. a Railway app, a worker)."""

    name: str
    type: str  # web, worker, cron, db, etc.
    environment: str | None = None
    status: str | None = None  # running, deploying, crashed
    last_deploy_at: datetime | None = None
    last_deploy_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "type": self.type,
            "environment": self.environment,
            "status": self.status,
            "last_deploy_at": _to_iso(self.last_deploy_at),
            "last_deploy_id": self.last_deploy_id,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ServiceInfo:
        return cls(
            name=data["name"],
            type=data["type"],
            environment=data.get("environment"),
            status=data.get("status"),
            last_deploy_at=_from_iso(data.get("last_deploy_at")),
            last_deploy_id=data.get("last_deploy_id"),
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass
class SystemFragment:
    """A partial view of a system contributed by a single connector."""

    connector_name: str
    connector_type: str
    tables: list[TableInfo] = field(default_factory=list)
    relationships: list[Relationship] = field(default_factory=list)
    services: list[ServiceInfo] = field(default_factory=list)
    discovered_at: datetime = field(default_factory=_utcnow)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "connector_name": self.connector_name,
            "connector_type": self.connector_type,
            "tables": [t.to_dict() for t in self.tables],
            "relationships": [r.to_dict() for r in self.relationships],
            "services": [s.to_dict() for s in self.services],
            "discovered_at": _to_iso(self.discovered_at),
            "errors": list(self.errors),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SystemFragment:
        return cls(
            connector_name=data["connector_name"],
            connector_type=data["connector_type"],
            tables=[TableInfo.from_dict(t) for t in data.get("tables") or []],
            relationships=[
                Relationship.from_dict(r) for r in data.get("relationships") or []
            ],
            services=[ServiceInfo.from_dict(s) for s in data.get("services") or []],
            discovered_at=_from_iso(data.get("discovered_at")) or _utcnow(),
            errors=list(data.get("errors") or []),
        )


@dataclass
class SystemModel:
    """A merged, normalized view of all systems Observibot is monitoring.

    Built by :class:`observibot.core.discovery.DiscoveryEngine` from one or more
    :class:`SystemFragment` instances.
    """

    id: str = field(default_factory=_new_id)
    fragments: list[SystemFragment] = field(default_factory=list)
    tables: list[TableInfo] = field(default_factory=list)
    relationships: list[Relationship] = field(default_factory=list)
    services: list[ServiceInfo] = field(default_factory=list)
    fingerprint: str = ""
    created_at: datetime = field(default_factory=_utcnow)

    def to_dict(self) -> dict[str, Any]:
        """Recursively serialize the model to plain dicts/lists."""
        return {
            "id": self.id,
            "fragments": [f.to_dict() for f in self.fragments],
            "tables": [t.to_dict() for t in self.tables],
            "relationships": [r.to_dict() for r in self.relationships],
            "services": [s.to_dict() for s in self.services],
            "fingerprint": self.fingerprint,
            "created_at": _to_iso(self.created_at),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SystemModel:
        """Reconstruct a SystemModel from ``to_dict`` output."""
        return cls(
            id=data.get("id") or _new_id(),
            fragments=[
                SystemFragment.from_dict(f) for f in data.get("fragments") or []
            ],
            tables=[TableInfo.from_dict(t) for t in data.get("tables") or []],
            relationships=[
                Relationship.from_dict(r) for r in data.get("relationships") or []
            ],
            services=[ServiceInfo.from_dict(s) for s in data.get("services") or []],
            fingerprint=data.get("fingerprint", ""),
            created_at=_from_iso(data.get("created_at")) or _utcnow(),
        )

    def compute_fingerprint(self) -> str:
        """Compute a SHA256 fingerprint of the structural content.

        The fingerprint excludes ``id``, ``created_at``, and the existing
        ``fingerprint`` field so it is stable across snapshots when nothing has
        changed structurally.
        """
        snapshot = {
            "tables": sorted(
                (t.to_dict() for t in self.tables),
                key=lambda d: (d.get("schema", ""), d.get("name", "")),
            ),
            "relationships": sorted(
                (r.to_dict() for r in self.relationships),
                key=lambda d: (
                    d.get("from_table", ""),
                    d.get("from_column", ""),
                    d.get("to_table", ""),
                    d.get("to_column", ""),
                ),
            ),
            "services": sorted(
                (s.to_dict() for s in self.services),
                key=lambda d: (d.get("name", ""), d.get("environment") or ""),
            ),
        }
        # last_deploy_at varies between snapshots — strip it from fingerprint
        # input so structural compares stay stable.
        for svc in snapshot["services"]:
            svc.pop("last_deploy_at", None)
            svc.pop("last_deploy_id", None)
            svc.pop("status", None)
        # Row counts also fluctuate; exclude.
        for tbl in snapshot["tables"]:
            tbl.pop("row_count", None)
            tbl.pop("estimated_size_bytes", None)
        encoded = json.dumps(snapshot, sort_keys=True, default=str).encode("utf-8")
        digest = hashlib.sha256(encoded).hexdigest()
        self.fingerprint = digest
        return digest


@dataclass
class MetricSnapshot:
    """A single metric value collected at a point in time."""

    id: str = field(default_factory=_new_id)
    connector_name: str = ""
    metric_name: str = ""
    value: float = 0.0
    labels: dict[str, str] = field(default_factory=dict)
    collected_at: datetime = field(default_factory=_utcnow)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "connector_name": self.connector_name,
            "metric_name": self.metric_name,
            "value": self.value,
            "labels": dict(self.labels),
            "collected_at": _to_iso(self.collected_at),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MetricSnapshot:
        return cls(
            id=data.get("id") or _new_id(),
            connector_name=data.get("connector_name", ""),
            metric_name=data.get("metric_name", ""),
            value=float(data.get("value", 0.0)),
            labels=dict(data.get("labels") or {}),
            collected_at=_from_iso(data.get("collected_at")) or _utcnow(),
        )


@dataclass
class ChangeEvent:
    """A discrete change observed in a monitored system."""

    id: str = field(default_factory=_new_id)
    connector_name: str = ""
    event_type: str = ""  # deploy, schema_change, config_change, etc.
    summary: str = ""
    details: dict[str, Any] = field(default_factory=dict)
    occurred_at: datetime = field(default_factory=_utcnow)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "connector_name": self.connector_name,
            "event_type": self.event_type,
            "summary": self.summary,
            "details": dict(self.details),
            "occurred_at": _to_iso(self.occurred_at),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ChangeEvent:
        return cls(
            id=data.get("id") or _new_id(),
            connector_name=data.get("connector_name", ""),
            event_type=data.get("event_type", ""),
            summary=data.get("summary", ""),
            details=dict(data.get("details") or {}),
            occurred_at=_from_iso(data.get("occurred_at")) or _utcnow(),
        )


@dataclass
class HealthStatus:
    """Result of a connector health check."""

    connector_name: str
    healthy: bool
    latency_ms: float | None = None
    message: str = ""
    checked_at: datetime = field(default_factory=_utcnow)

    def to_dict(self) -> dict[str, Any]:
        return {
            "connector_name": self.connector_name,
            "healthy": self.healthy,
            "latency_ms": self.latency_ms,
            "message": self.message,
            "checked_at": _to_iso(self.checked_at),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> HealthStatus:
        return cls(
            connector_name=data["connector_name"],
            healthy=bool(data["healthy"]),
            latency_ms=data.get("latency_ms"),
            message=data.get("message", ""),
            checked_at=_from_iso(data.get("checked_at")) or _utcnow(),
        )


LOW_CONFIDENCE_THRESHOLD = 0.7


@dataclass
class Insight:
    """An LLM- or rule-generated finding about the monitored system."""

    id: str = field(default_factory=_new_id)
    severity: str = "info"  # critical, warning, info, ok, discovery
    title: str = ""
    summary: str = ""
    details: str = ""
    recommended_actions: list[str] = field(default_factory=list)
    related_metrics: list[str] = field(default_factory=list)
    related_tables: list[str] = field(default_factory=list)
    confidence: float = 0.5
    uncertainty_reason: str | None = None
    source: str = "llm"  # llm, anomaly, drift, rule, incident
    fingerprint: str = ""  # for de-dup
    created_at: datetime = field(default_factory=_utcnow)

    def __post_init__(self) -> None:
        if not self.fingerprint:
            self.fingerprint = self.compute_fingerprint()

    @property
    def is_hypothesis(self) -> bool:
        """True when the insight should be presented with a hedging prefix."""
        return self.confidence < LOW_CONFIDENCE_THRESHOLD

    def display_title(self) -> str:
        """Return the user-facing title, prefixed for low-confidence insights."""
        if self.is_hypothesis:
            return f"🟡 Hypothesis: {self.title}"
        return self.title

    def compute_fingerprint(self) -> str:
        """Stable fingerprint for de-dup — excludes LLM-generated text."""
        payload = json.dumps(
            {
                "severity": self.severity,
                "source": self.source,
                "tables": sorted(self.related_tables),
                "metrics": sorted(self.related_metrics),
            },
            sort_keys=True,
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()[:16]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "severity": self.severity,
            "title": self.title,
            "summary": self.summary,
            "details": self.details,
            "recommended_actions": list(self.recommended_actions),
            "related_metrics": list(self.related_metrics),
            "related_tables": list(self.related_tables),
            "confidence": self.confidence,
            "uncertainty_reason": self.uncertainty_reason,
            "source": self.source,
            "fingerprint": self.fingerprint,
            "created_at": _to_iso(self.created_at),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Insight:
        obj = cls(
            id=data.get("id") or _new_id(),
            severity=data.get("severity", "info"),
            title=data.get("title", ""),
            summary=data.get("summary", ""),
            details=data.get("details", ""),
            recommended_actions=list(data.get("recommended_actions") or []),
            related_metrics=list(data.get("related_metrics") or []),
            related_tables=list(data.get("related_tables") or []),
            confidence=float(data.get("confidence", 0.5)),
            uncertainty_reason=data.get("uncertainty_reason"),
            source=data.get("source", "llm"),
            fingerprint=data.get("fingerprint", ""),
            created_at=_from_iso(data.get("created_at")) or _utcnow(),
        )
        return obj
