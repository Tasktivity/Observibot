"""Async store for Observibot backed by SQLAlchemy 2.x.

Supports SQLite (dev/demo) and PostgreSQL (production) via the DATABASE_URL
environment variable. Schema is managed by Alembic in production; for SQLite
the tables are auto-created on first use.
"""
from __future__ import annotations

import json
import os
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from sqlalchemy import (
    Boolean,
    Column,
    Float,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    event,
)
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine
from sqlalchemy.types import JSON

from observibot.core.models import (
    ChangeEvent,
    Insight,
    MetricSnapshot,
    SystemModel,
)

metadata = MetaData()

system_snapshots = Table(
    "system_snapshots",
    metadata,
    Column("id", String, primary_key=True),
    Column("fingerprint", String, nullable=False),
    Column("payload", Text, nullable=False),
    Column("created_at", String, nullable=False),
)

metric_snapshots = Table(
    "metric_snapshots",
    metadata,
    Column("id", String, primary_key=True),
    Column("connector_name", String, nullable=False),
    Column("metric_name", String, nullable=False),
    Column("value", Float, nullable=False),
    Column("labels", Text),
    Column("collected_at", String, nullable=False),
)

change_events = Table(
    "change_events",
    metadata,
    Column("id", String, primary_key=True),
    Column("connector_name", String, nullable=False),
    Column("event_type", String, nullable=False),
    Column("summary", Text),
    Column("details", Text),
    Column("occurred_at", String, nullable=False),
)

insights_table = Table(
    "insights",
    metadata,
    Column("id", String, primary_key=True),
    Column("severity", String, nullable=False),
    Column("title", Text),
    Column("summary", Text),
    Column("details", Text),
    Column("recommended_actions", Text),
    Column("related_metrics", Text),
    Column("related_tables", Text),
    Column("confidence", Float),
    Column("source", String),
    Column("fingerprint", String),
    Column("created_at", String, nullable=False),
)

alert_history = Table(
    "alert_history",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("insight_id", String),
    Column("channel", String),
    Column("severity", String),
    Column("status", String),
    Column("message", Text),
    Column("sent_at", String, nullable=False),
)

business_context = Table(
    "business_context",
    metadata,
    Column("key", String, primary_key=True),
    Column("value", Text, nullable=False),
    Column("updated_at", String, nullable=False),
)

llm_usage = Table(
    "llm_usage",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("provider", String),
    Column("model", String),
    Column("prompt_tokens", Integer),
    Column("completion_tokens", Integer),
    Column("total_tokens", Integer),
    Column("cost_usd", Float),
    Column("purpose", String),
    Column("recorded_at", String, nullable=False),
)

metric_baselines = Table(
    "metric_baselines",
    metadata,
    Column("metric_name", String, primary_key=True),
    Column("connector_name", String, primary_key=True),
    Column("labels_key", String, primary_key=True),
    Column("sample_count", Integer, nullable=False),
    Column("mean", Float, nullable=False),
    Column("stddev", Float, nullable=False),
    Column("last_updated", String, nullable=False),
)

# Phase 3 tables

users_table = Table(
    "users",
    metadata,
    Column("id", String, primary_key=True),
    Column("email", String, unique=True, nullable=False),
    Column("password_hash", String, nullable=False),
    Column("is_admin", Boolean, default=True),
    Column("tenant_id", Integer, default=1),
    Column("created_at", String),
)

dashboard_widgets = Table(
    "dashboard_widgets",
    metadata,
    Column("id", String, primary_key=True),
    Column("user_id", String, sa.ForeignKey("users.id")),
    Column("tenant_id", Integer, default=1),
    Column("widget_type", String, nullable=False),
    Column("title", String),
    Column("config", JSON),
    Column("layout", JSON),
    Column("data_source", JSON),
    Column("schema_version", Integer, default=1),
    Column("pinned", Boolean, default=True),
    Column("created_at", String),
    Column("updated_at", String),
)

query_cache = Table(
    "query_cache",
    metadata,
    Column("hash", String, primary_key=True),
    Column("sql_text", String, nullable=False),
    Column("result_json", JSON),
    Column("row_count", Integer),
    Column("execution_ms", Float),
    Column("created_at", String),
    Column("expires_at", String),
)

# Indexes (created alongside the tables)
sa.Index("idx_snap_created", system_snapshots.c.created_at)
sa.Index("idx_metrics_name_time", metric_snapshots.c.metric_name, metric_snapshots.c.collected_at)
sa.Index("idx_metrics_time", metric_snapshots.c.collected_at)
sa.Index("idx_events_time", change_events.c.occurred_at)
sa.Index("idx_insights_fp", insights_table.c.fingerprint)
sa.Index("idx_insights_time", insights_table.c.created_at)


def _utcnow_iso() -> str:
    return datetime.now(UTC).isoformat()


def _labels_key(labels: dict[str, str]) -> str:
    if not labels:
        return ""
    return json.dumps(labels, sort_keys=True)


def _dialect_insert(table: Table, engine: AsyncEngine):
    """Return the dialect-specific insert function for upsert support."""
    if "postgresql" in str(engine.url):
        from sqlalchemy.dialects.postgresql import insert
        return insert(table)
    from sqlalchemy.dialects.sqlite import insert
    return insert(table)


def build_engine(url: str | None = None) -> AsyncEngine:
    """Create an async engine from a URL or the DATABASE_URL env var."""
    db_url = url or os.getenv("DATABASE_URL", "sqlite+aiosqlite:///data/observibot.db")
    if db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql+asyncpg://", 1)
    elif db_url.startswith("postgresql://"):
        db_url = db_url.replace("postgresql://", "postgresql+asyncpg://", 1)

    engine = create_async_engine(db_url, pool_pre_ping=True)

    if "sqlite" in db_url:
        @event.listens_for(engine.sync_engine, "connect")
        def _set_sqlite_pragmas(dbapi_conn, _rec):
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


class Store:
    """Async store backed by SQLAlchemy.

    Use as an async context manager::

        async with Store("data/observibot.db") as store:
            await store.save_metric(...)
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._engine: AsyncEngine | None = None
        self._conn: AsyncConnection | None = None

    async def __aenter__(self) -> Store:
        await self.connect()
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        await self.close()

    async def connect(self) -> None:
        """Open the database connection and create the schema if needed."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db_url = f"sqlite+aiosqlite:///{self.path}"
        self._engine = build_engine(db_url)
        self._conn = await self._engine.connect()
        async with self._engine.begin() as conn:
            await conn.run_sync(metadata.create_all)

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None
        if self._engine is not None:
            await self._engine.dispose()
            self._engine = None

    @property
    def conn(self) -> AsyncConnection:
        if self._conn is None:
            raise RuntimeError("Store is not connected. Call connect() or use 'async with'.")
        return self._conn

    @property
    def engine(self) -> AsyncEngine:
        if self._engine is None:
            raise RuntimeError("Store is not connected. Call connect() or use 'async with'.")
        return self._engine

    # ---------- system snapshots ----------

    async def save_system_snapshot(self, model: SystemModel) -> None:
        fp = model.fingerprint or model.compute_fingerprint()
        async with self.engine.begin() as conn:
            stmt = (
                _dialect_insert(system_snapshots, self.engine)
                .values(
                    id=model.id,
                    fingerprint=fp,
                    payload=json.dumps(model.to_dict()),
                    created_at=model.created_at.isoformat(),
                )
                .on_conflict_do_update(
                    index_elements=["id"],
                    set_=dict(
                        fingerprint=fp,
                        payload=json.dumps(model.to_dict()),
                        created_at=model.created_at.isoformat(),
                    ),
                )
            )
            await conn.execute(stmt)

    async def get_latest_system_snapshot(self) -> SystemModel | None:
        async with self.engine.begin() as conn:
            result = await conn.execute(
                sa.select(system_snapshots.c.payload)
                .order_by(system_snapshots.c.created_at.desc())
                .limit(1)
            )
            row = result.fetchone()
        if row is None:
            return None
        return SystemModel.from_dict(json.loads(row[0]))

    # ---------- metrics ----------

    async def save_metric(self, metric: MetricSnapshot) -> None:
        await self.save_metrics([metric])

    async def save_metrics(self, metrics: Iterable[MetricSnapshot]) -> int:
        rows = [
            {
                "id": m.id,
                "connector_name": m.connector_name,
                "metric_name": m.metric_name,
                "value": float(m.value),
                "labels": json.dumps(m.labels) if m.labels else None,
                "collected_at": m.collected_at.isoformat(),
            }
            for m in metrics
        ]
        if not rows:
            return 0
        async with self.engine.begin() as conn:
            for row in rows:
                stmt = (
                    _dialect_insert(metric_snapshots, self.engine)
                    .values(**row)
                    .on_conflict_do_update(
                        index_elements=["id"],
                        set_=row,
                    )
                )
                await conn.execute(stmt)
        return len(rows)

    async def get_metrics(
        self,
        metric_name: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        connector_name: str | None = None,
        limit: int | None = None,
    ) -> list[MetricSnapshot]:
        stmt = sa.select(
            metric_snapshots.c.id,
            metric_snapshots.c.connector_name,
            metric_snapshots.c.metric_name,
            metric_snapshots.c.value,
            metric_snapshots.c.labels,
            metric_snapshots.c.collected_at,
        )
        if metric_name:
            stmt = stmt.where(metric_snapshots.c.metric_name == metric_name)
        if connector_name:
            stmt = stmt.where(metric_snapshots.c.connector_name == connector_name)
        if since is not None:
            stmt = stmt.where(metric_snapshots.c.collected_at >= since.isoformat())
        if until is not None:
            stmt = stmt.where(metric_snapshots.c.collected_at <= until.isoformat())
        stmt = stmt.order_by(metric_snapshots.c.collected_at.asc())
        if limit is not None:
            stmt = stmt.limit(limit)

        async with self.engine.begin() as conn:
            result = await conn.execute(stmt)
            rows = result.fetchall()
        return [
            MetricSnapshot(
                id=r[0],
                connector_name=r[1],
                metric_name=r[2],
                value=r[3],
                labels=json.loads(r[4]) if r[4] else {},
                collected_at=datetime.fromisoformat(r[5]),
            )
            for r in rows
        ]

    # ---------- change events ----------

    async def save_change_event(self, event_obj: ChangeEvent) -> None:
        async with self.engine.begin() as conn:
            stmt = (
                _dialect_insert(change_events, self.engine)
                .values(
                    id=event_obj.id,
                    connector_name=event_obj.connector_name,
                    event_type=event_obj.event_type,
                    summary=event_obj.summary,
                    details=json.dumps(event_obj.details),
                    occurred_at=event_obj.occurred_at.isoformat(),
                )
                .on_conflict_do_update(
                    index_elements=["id"],
                    set_=dict(
                        connector_name=event_obj.connector_name,
                        event_type=event_obj.event_type,
                        summary=event_obj.summary,
                        details=json.dumps(event_obj.details),
                        occurred_at=event_obj.occurred_at.isoformat(),
                    ),
                )
            )
            await conn.execute(stmt)

    async def get_recent_change_events(
        self, since: datetime | None = None, limit: int = 100
    ) -> list[ChangeEvent]:
        stmt = sa.select(
            change_events.c.id,
            change_events.c.connector_name,
            change_events.c.event_type,
            change_events.c.summary,
            change_events.c.details,
            change_events.c.occurred_at,
        )
        if since:
            stmt = stmt.where(change_events.c.occurred_at >= since.isoformat())
        stmt = stmt.order_by(change_events.c.occurred_at.desc()).limit(limit)

        async with self.engine.begin() as conn:
            result = await conn.execute(stmt)
            rows = result.fetchall()
        return [
            ChangeEvent(
                id=r[0],
                connector_name=r[1],
                event_type=r[2],
                summary=r[3] or "",
                details=json.loads(r[4]) if r[4] else {},
                occurred_at=datetime.fromisoformat(r[5]),
            )
            for r in rows
        ]

    # ---------- insights ----------

    async def save_insight(self, insight: Insight) -> bool:
        cutoff = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
        async with self.engine.begin() as conn:
            result = await conn.execute(
                sa.select(insights_table.c.id)
                .where(insights_table.c.fingerprint == insight.fingerprint)
                .where(insights_table.c.created_at >= cutoff)
                .limit(1)
            )
            if result.fetchone() is not None:
                return False
            stmt = (
                _dialect_insert(insights_table, self.engine)
                .values(
                    id=insight.id,
                    severity=insight.severity,
                    title=insight.title,
                    summary=insight.summary,
                    details=insight.details,
                    recommended_actions=json.dumps(insight.recommended_actions),
                    related_metrics=json.dumps(insight.related_metrics),
                    related_tables=json.dumps(insight.related_tables),
                    confidence=insight.confidence,
                    source=insight.source,
                    fingerprint=insight.fingerprint,
                    created_at=insight.created_at.isoformat(),
                )
                .on_conflict_do_update(
                    index_elements=["id"],
                    set_=dict(
                        severity=insight.severity,
                        title=insight.title,
                        summary=insight.summary,
                        details=insight.details,
                        recommended_actions=json.dumps(insight.recommended_actions),
                        related_metrics=json.dumps(insight.related_metrics),
                        related_tables=json.dumps(insight.related_tables),
                        confidence=insight.confidence,
                        source=insight.source,
                        fingerprint=insight.fingerprint,
                        created_at=insight.created_at.isoformat(),
                    ),
                )
            )
            await conn.execute(stmt)
        return True

    async def get_recent_insights(self, limit: int = 20) -> list[Insight]:
        async with self.engine.begin() as conn:
            result = await conn.execute(
                sa.select(
                    insights_table.c.id,
                    insights_table.c.severity,
                    insights_table.c.title,
                    insights_table.c.summary,
                    insights_table.c.details,
                    insights_table.c.recommended_actions,
                    insights_table.c.related_metrics,
                    insights_table.c.related_tables,
                    insights_table.c.confidence,
                    insights_table.c.source,
                    insights_table.c.fingerprint,
                    insights_table.c.created_at,
                )
                .order_by(insights_table.c.created_at.desc())
                .limit(limit)
            )
            rows = result.fetchall()
        return [
            Insight(
                id=r[0],
                severity=r[1],
                title=r[2] or "",
                summary=r[3] or "",
                details=r[4] or "",
                recommended_actions=json.loads(r[5]) if r[5] else [],
                related_metrics=json.loads(r[6]) if r[6] else [],
                related_tables=json.loads(r[7]) if r[7] else [],
                confidence=r[8] if r[8] is not None else 0.5,
                source=r[9] or "llm",
                fingerprint=r[10] or "",
                created_at=datetime.fromisoformat(r[11]),
            )
            for r in rows
        ]

    # ---------- alert history ----------

    async def record_alert(
        self,
        insight_id: str | None,
        channel: str,
        severity: str,
        status: str,
        message: str,
    ) -> None:
        async with self.engine.begin() as conn:
            await conn.execute(
                alert_history.insert().values(
                    insight_id=insight_id,
                    channel=channel,
                    severity=severity,
                    status=status,
                    message=message,
                    sent_at=_utcnow_iso(),
                )
            )

    async def count_alerts_since(self, since: datetime) -> int:
        async with self.engine.begin() as conn:
            result = await conn.execute(
                sa.select(sa.func.count())
                .select_from(alert_history)
                .where(alert_history.c.sent_at >= since.isoformat())
            )
            row = result.fetchone()
        return int(row[0]) if row else 0

    # ---------- business context ----------

    async def set_business_context(self, key: str, value: Any) -> None:
        async with self.engine.begin() as conn:
            stmt = (
                _dialect_insert(business_context, self.engine)
                .values(key=key, value=json.dumps(value), updated_at=_utcnow_iso())
                .on_conflict_do_update(
                    index_elements=["key"],
                    set_=dict(value=json.dumps(value), updated_at=_utcnow_iso()),
                )
            )
            await conn.execute(stmt)

    async def get_business_context(self, key: str) -> Any:
        async with self.engine.begin() as conn:
            result = await conn.execute(
                sa.select(business_context.c.value)
                .where(business_context.c.key == key)
            )
            row = result.fetchone()
        return json.loads(row[0]) if row else None

    async def get_all_business_context(self) -> dict[str, Any]:
        async with self.engine.begin() as conn:
            result = await conn.execute(
                sa.select(business_context.c.key, business_context.c.value)
            )
            rows = result.fetchall()
        return {r[0]: json.loads(r[1]) for r in rows}

    # ---------- LLM usage ----------

    async def record_llm_usage(
        self,
        provider: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        cost_usd: float,
        purpose: str,
    ) -> None:
        async with self.engine.begin() as conn:
            await conn.execute(
                llm_usage.insert().values(
                    provider=provider,
                    model=model,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    total_tokens=prompt_tokens + completion_tokens,
                    cost_usd=cost_usd,
                    purpose=purpose,
                    recorded_at=_utcnow_iso(),
                )
            )

    async def get_llm_usage_summary(self, since: datetime | None = None) -> dict[str, Any]:
        if since is None:
            since = datetime.now(UTC) - timedelta(days=1)
        async with self.engine.begin() as conn:
            result = await conn.execute(
                sa.select(
                    sa.func.count(),
                    sa.func.coalesce(sa.func.sum(llm_usage.c.total_tokens), 0),
                    sa.func.coalesce(sa.func.sum(llm_usage.c.cost_usd), 0),
                )
                .select_from(llm_usage)
                .where(llm_usage.c.recorded_at >= since.isoformat())
            )
            row = result.fetchone()
        return {
            "calls": int(row[0]) if row else 0,
            "total_tokens": int(row[1]) if row else 0,
            "cost_usd": float(row[2]) if row else 0.0,
            "since": since.isoformat(),
        }

    # ---------- baselines ----------

    async def upsert_baseline(
        self,
        metric_name: str,
        connector_name: str,
        labels: dict[str, str],
        sample_count: int,
        mean: float,
        stddev: float,
    ) -> None:
        lk = _labels_key(labels)
        async with self.engine.begin() as conn:
            stmt = (
                _dialect_insert(metric_baselines, self.engine)
                .values(
                    metric_name=metric_name,
                    connector_name=connector_name,
                    labels_key=lk,
                    sample_count=sample_count,
                    mean=mean,
                    stddev=stddev,
                    last_updated=_utcnow_iso(),
                )
                .on_conflict_do_update(
                    index_elements=["metric_name", "connector_name", "labels_key"],
                    set_=dict(
                        sample_count=sample_count,
                        mean=mean,
                        stddev=stddev,
                        last_updated=_utcnow_iso(),
                    ),
                )
            )
            await conn.execute(stmt)

    async def get_baseline(
        self, metric_name: str, connector_name: str, labels: dict[str, str]
    ) -> dict[str, Any] | None:
        async with self.engine.begin() as conn:
            result = await conn.execute(
                sa.select(
                    metric_baselines.c.sample_count,
                    metric_baselines.c.mean,
                    metric_baselines.c.stddev,
                    metric_baselines.c.last_updated,
                )
                .where(metric_baselines.c.metric_name == metric_name)
                .where(metric_baselines.c.connector_name == connector_name)
                .where(metric_baselines.c.labels_key == _labels_key(labels))
            )
            row = result.fetchone()
        if row is None:
            return None
        return {
            "sample_count": int(row[0]),
            "mean": float(row[1]),
            "stddev": float(row[2]),
            "last_updated": row[3],
        }

    # ---------- retention ----------

    async def apply_retention(
        self,
        metrics_days: int,
        events_days: int,
        insights_days: int,
        max_snapshots: int,
    ) -> dict[str, int]:
        now = datetime.now(UTC)
        results: dict[str, int] = {}
        async with self.engine.begin() as conn:
            r = await conn.execute(
                metric_snapshots.delete().where(
                    metric_snapshots.c.collected_at
                    < (now - timedelta(days=metrics_days)).isoformat()
                )
            )
            results["metrics"] = r.rowcount or 0

            r = await conn.execute(
                change_events.delete().where(
                    change_events.c.occurred_at
                    < (now - timedelta(days=events_days)).isoformat()
                )
            )
            results["events"] = r.rowcount or 0

            r = await conn.execute(
                insights_table.delete().where(
                    insights_table.c.created_at
                    < (now - timedelta(days=insights_days)).isoformat()
                )
            )
            results["insights"] = r.rowcount or 0

            snap_result = await conn.execute(
                sa.select(system_snapshots.c.id)
                .order_by(system_snapshots.c.created_at.desc())
            )
            snap_rows = snap_result.fetchall()
            excess = [r[0] for r in snap_rows[max_snapshots:]]
            if excess:
                await conn.execute(
                    system_snapshots.delete().where(
                        system_snapshots.c.id.in_(excess)
                    )
                )
            results["snapshots"] = len(excess)

        return results
