"""Async store for Observibot backed by SQLAlchemy 2.x.

Supports SQLite (dev/demo) and PostgreSQL (production) via the DATABASE_URL
environment variable. Schema is managed by Alembic in production; for SQLite
the tables are auto-created on first use.
"""
from __future__ import annotations

import json
import os
import uuid
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

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

if TYPE_CHECKING:
    from observibot.core.code_intelligence.models import SemanticFact

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

semantic_facts = Table(
    "semantic_facts",
    metadata,
    Column("id", String, primary_key=True),
    Column("fact_type", String, nullable=False),
    Column("concept", String, nullable=False, index=True),
    Column("claim", Text, nullable=False),
    Column("tables_json", Text, default="[]"),
    Column("columns_json", Text, default="[]"),
    Column("sql_condition", Text),
    Column("evidence_path", String),
    Column("evidence_lines", String),
    Column("evidence_commit", String),
    Column("source", String, nullable=False),
    Column("confidence", Float, default=0.8),
    Column("created_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
    Column("valid_from_commit", String),
    Column("valid_to_commit", String),
    Column("is_active", Boolean, default=True),
)

code_intelligence_meta = Table(
    "code_intelligence_meta",
    metadata,
    Column("key", String, primary_key=True),
    Column("value", Text, nullable=False),
    Column("updated_at", String, nullable=False),
)

# Phase 4.5 prerequisite tables

monitor_runs = Table(
    "monitor_runs",
    metadata,
    Column("id", String, primary_key=True),
    Column("started_at", String, nullable=False),
    Column("finished_at", String),
    Column("system_snapshot_id", String),
    Column("anomaly_count", Integer, default=0),
    Column("insight_count", Integer, default=0),
    Column("metric_count", Integer, default=0),
    Column("llm_used", Boolean, default=False),
    Column("llm_call_id", String),
    Column("status", String, default="running"),
    Column("error_message", Text),
)

insight_feedback = Table(
    "insight_feedback",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("insight_id", String, nullable=False),
    Column("user_id", String),
    Column("outcome", String, nullable=False),
    Column("note", Text),
    Column("created_at", String, nullable=False),
)

# Phase 4.5 Step 1: Events envelope
events_table = Table(
    "events",
    metadata,
    Column("id", String, primary_key=True),
    Column("event_type", String, nullable=False),
    Column("occurred_at", String, nullable=False),
    Column("severity", String),
    Column("source", String, nullable=False),
    Column("agent", String, nullable=False, server_default="sre"),
    Column("subject", String, nullable=False),
    Column("summary", Text),
    Column("ref_table", String, nullable=False),
    Column("ref_id", String, nullable=False),
    Column("run_id", String),
)

# Indexes (created alongside the tables)
sa.Index("idx_snap_created", system_snapshots.c.created_at)
sa.Index("idx_metrics_name_time", metric_snapshots.c.metric_name, metric_snapshots.c.collected_at)
sa.Index("idx_metrics_time", metric_snapshots.c.collected_at)
sa.Index("idx_events_time", change_events.c.occurred_at)
sa.Index("idx_insights_fp", insights_table.c.fingerprint)
sa.Index("idx_insights_time", insights_table.c.created_at)
sa.Index("idx_monitor_runs_time", monitor_runs.c.started_at)
sa.Index("idx_feedback_insight", insight_feedback.c.insight_id)
sa.Index("idx_feedback_time", insight_feedback.c.created_at)
sa.Index("idx_events_type_time", events_table.c.event_type, events_table.c.occurred_at.desc())
sa.Index("idx_events_subject_time", events_table.c.subject, events_table.c.occurred_at.desc())
sa.Index("idx_events_agent_time", events_table.c.agent, events_table.c.occurred_at.desc())
sa.Index("idx_events_run", events_table.c.run_id)
sa.Index("idx_events_ref", events_table.c.ref_table, events_table.c.ref_id)


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
            if "sqlite" in str(self._engine.url):
                await conn.execute(sa.text(
                    "CREATE VIRTUAL TABLE IF NOT EXISTS semantic_facts_fts "
                    "USING fts5(concept, claim, tables_json, columns_json, "
                    "content=semantic_facts, content_rowid=rowid)"
                ))
                await conn.execute(sa.text(
                    "CREATE VIRTUAL TABLE IF NOT EXISTS events_fts "
                    "USING fts5(summary, content=events, content_rowid=rowid)"
                ))

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

    # ---------- monitor runs ----------

    async def create_monitor_run(self, run_id: str, started_at: datetime) -> None:
        async with self.engine.begin() as conn:
            await conn.execute(
                monitor_runs.insert().values(
                    id=run_id,
                    started_at=started_at.isoformat(),
                    status="running",
                    anomaly_count=0,
                    insight_count=0,
                    metric_count=0,
                    llm_used=False,
                )
            )

    async def complete_monitor_run(
        self,
        run_id: str,
        finished_at: datetime,
        stats: dict[str, Any],
    ) -> None:
        async with self.engine.begin() as conn:
            await conn.execute(
                monitor_runs.update()
                .where(monitor_runs.c.id == run_id)
                .values(
                    finished_at=finished_at.isoformat(),
                    status="completed",
                    metric_count=stats.get("metric_count", 0),
                    anomaly_count=stats.get("anomaly_count", 0),
                    insight_count=stats.get("insight_count", 0),
                    llm_used=stats.get("llm_used", False),
                    system_snapshot_id=stats.get("system_snapshot_id"),
                    llm_call_id=stats.get("llm_call_id"),
                )
            )

    async def fail_monitor_run(self, run_id: str, error_message: str) -> None:
        async with self.engine.begin() as conn:
            await conn.execute(
                monitor_runs.update()
                .where(monitor_runs.c.id == run_id)
                .values(
                    finished_at=datetime.now(UTC).isoformat(),
                    status="failed",
                    error_message=error_message,
                )
            )

    async def get_monitor_run(self, run_id: str) -> dict[str, Any] | None:
        async with self.engine.begin() as conn:
            result = await conn.execute(
                sa.select(
                    monitor_runs.c.id,
                    monitor_runs.c.started_at,
                    monitor_runs.c.finished_at,
                    monitor_runs.c.status,
                    monitor_runs.c.metric_count,
                    monitor_runs.c.anomaly_count,
                    monitor_runs.c.insight_count,
                    monitor_runs.c.llm_used,
                    monitor_runs.c.error_message,
                ).where(monitor_runs.c.id == run_id)
            )
            row = result.fetchone()
        if row is None:
            return None
        return {
            "id": row[0],
            "started_at": row[1],
            "finished_at": row[2],
            "status": row[3],
            "metric_count": row[4],
            "anomaly_count": row[5],
            "insight_count": row[6],
            "llm_used": row[7],
            "error_message": row[8],
        }

    async def mark_stale_runs(self) -> int:
        """Mark any 'running' monitor runs as 'stale' (crash recovery)."""
        async with self.engine.begin() as conn:
            result = await conn.execute(
                monitor_runs.update()
                .where(monitor_runs.c.status == "running")
                .values(
                    status="stale",
                    finished_at=datetime.now(UTC).isoformat(),
                    error_message="Process restarted before cycle completed",
                )
            )
        return result.rowcount or 0

    # ---------- insight feedback ----------

    async def get_insight_by_id(self, insight_id: str) -> dict[str, Any] | None:
        async with self.engine.begin() as conn:
            result = await conn.execute(
                sa.select(insights_table.c.id, insights_table.c.title)
                .where(insights_table.c.id == insight_id)
            )
            row = result.fetchone()
        if row is None:
            return None
        return {"id": row[0], "title": row[1]}

    async def record_insight_feedback(
        self,
        insight_id: str,
        user_id: str | None,
        outcome: str,
        note: str | None = None,
    ) -> dict[str, Any]:
        now = _utcnow_iso()
        async with self.engine.begin() as conn:
            result = await conn.execute(
                insight_feedback.insert().values(
                    insight_id=insight_id,
                    user_id=user_id,
                    outcome=outcome,
                    note=note,
                    created_at=now,
                )
            )
            feedback_id = result.inserted_primary_key[0]
        return {
            "id": feedback_id,
            "insight_id": insight_id,
            "user_id": user_id,
            "outcome": outcome,
            "note": note,
            "created_at": now,
        }

    async def get_insight_feedback(self, insight_id: str) -> list[dict]:
        async with self.engine.begin() as conn:
            result = await conn.execute(
                sa.select(
                    insight_feedback.c.id,
                    insight_feedback.c.insight_id,
                    insight_feedback.c.user_id,
                    insight_feedback.c.outcome,
                    insight_feedback.c.note,
                    insight_feedback.c.created_at,
                )
                .where(insight_feedback.c.insight_id == insight_id)
                .order_by(insight_feedback.c.created_at.asc())
            )
            rows = result.fetchall()
        return [
            {
                "id": r[0], "insight_id": r[1], "user_id": r[2],
                "outcome": r[3], "note": r[4], "created_at": r[5],
            }
            for r in rows
        ]

    async def get_feedback_summary(
        self, since: datetime | None = None,
    ) -> list[dict]:
        stmt = (
            sa.select(
                insight_feedback.c.outcome,
                sa.func.count().label("count"),
            )
            .group_by(insight_feedback.c.outcome)
        )
        if since is not None:
            stmt = stmt.where(
                insight_feedback.c.created_at >= since.isoformat()
            )
        async with self.engine.begin() as conn:
            result = await conn.execute(stmt)
            rows = result.fetchall()
        return [{"outcome": r[0], "count": r[1]} for r in rows]

    # ---------- events envelope ----------

    async def emit_event(
        self,
        event_type: str,
        source: str,
        subject: str,
        ref_table: str,
        ref_id: str,
        severity: str | None = None,
        summary: str | None = None,
        agent: str = "sre",
        run_id: str | None = None,
    ) -> str:
        """Record an event in the envelope. Returns the event ID."""
        event_id = uuid.uuid4().hex[:12]
        now = _utcnow_iso()
        async with self.engine.begin() as conn:
            await conn.execute(
                events_table.insert().values(
                    id=event_id,
                    event_type=event_type,
                    occurred_at=now,
                    severity=severity,
                    source=source,
                    agent=agent,
                    subject=subject,
                    summary=summary,
                    ref_table=ref_table,
                    ref_id=ref_id,
                    run_id=run_id,
                )
            )
            if summary and "sqlite" in str(self.engine.url):
                await conn.execute(sa.text(
                    "INSERT OR REPLACE INTO events_fts"
                    "(rowid, summary) "
                    "SELECT rowid, summary "
                    "FROM events WHERE id = :id"
                ), {"id": event_id})
        return event_id

    async def get_events(
        self,
        event_type: str | None = None,
        subject: str | None = None,
        agent: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 50,
    ) -> list[dict]:
        """Query events with optional filters. Returns newest first."""
        stmt = sa.select(
            events_table.c.id,
            events_table.c.event_type,
            events_table.c.occurred_at,
            events_table.c.severity,
            events_table.c.source,
            events_table.c.agent,
            events_table.c.subject,
            events_table.c.summary,
            events_table.c.ref_table,
            events_table.c.ref_id,
            events_table.c.run_id,
        )
        if event_type:
            stmt = stmt.where(events_table.c.event_type == event_type)
        if subject:
            stmt = stmt.where(events_table.c.subject == subject)
        if agent:
            stmt = stmt.where(events_table.c.agent == agent)
        if since is not None:
            stmt = stmt.where(events_table.c.occurred_at >= since.isoformat())
        if until is not None:
            stmt = stmt.where(events_table.c.occurred_at <= until.isoformat())
        stmt = stmt.order_by(events_table.c.occurred_at.desc()).limit(limit)

        async with self.engine.begin() as conn:
            result = await conn.execute(stmt)
            rows = result.fetchall()
        return [
            {
                "id": r[0], "event_type": r[1], "occurred_at": r[2],
                "severity": r[3], "source": r[4], "agent": r[5],
                "subject": r[6], "summary": r[7], "ref_table": r[8],
                "ref_id": r[9], "run_id": r[10],
            }
            for r in rows
        ]

    async def get_events_for_subject(
        self, subject: str, limit: int = 20,
    ) -> list[dict]:
        """Get recent events for a specific metric/table/service."""
        return await self.get_events(subject=subject, limit=limit)

    async def get_events_near_time(
        self,
        timestamp: datetime,
        window_minutes: int = 30,
        subject: str | None = None,
    ) -> list[dict]:
        """Get events within a time window (+-window_minutes)."""
        delta = timedelta(minutes=window_minutes)
        return await self.get_events(
            subject=subject,
            since=timestamp - delta,
            until=timestamp + delta,
            limit=200,
        )

    async def search_events(self, query: str, limit: int = 10) -> list[dict]:
        """Full-text search over event summaries."""
        if "sqlite" in str(self.engine.url):
            # FTS5 search
            fts_query = " OR ".join(
                f'"{w}"' for w in query.split() if w.strip()
            )
            if not fts_query:
                return []
            sql = sa.text(
                "SELECT e.id, e.event_type, e.occurred_at, e.severity, "
                "e.source, e.agent, e.subject, e.summary, "
                "e.ref_table, e.ref_id, e.run_id "
                "FROM events e "
                "JOIN events_fts f ON e.rowid = f.rowid "
                "WHERE events_fts MATCH :query "
                "ORDER BY rank LIMIT :limit"
            )
            async with self.engine.begin() as conn:
                result = await conn.execute(sql, {"query": fts_query, "limit": limit})
                rows = result.fetchall()
        else:
            # PostgreSQL: tsvector search
            pattern = f"%{query}%"
            stmt = (
                sa.select(
                    events_table.c.id,
                    events_table.c.event_type,
                    events_table.c.occurred_at,
                    events_table.c.severity,
                    events_table.c.source,
                    events_table.c.agent,
                    events_table.c.subject,
                    events_table.c.summary,
                    events_table.c.ref_table,
                    events_table.c.ref_id,
                    events_table.c.run_id,
                )
                .where(events_table.c.summary.ilike(pattern))
                .order_by(events_table.c.occurred_at.desc())
                .limit(limit)
            )
            async with self.engine.begin() as conn:
                result = await conn.execute(stmt)
                rows = result.fetchall()

        return [
            {
                "id": r[0], "event_type": r[1], "occurred_at": r[2],
                "severity": r[3], "source": r[4], "agent": r[5],
                "subject": r[6], "summary": r[7], "ref_table": r[8],
                "ref_id": r[9], "run_id": r[10],
            }
            for r in rows
        ]

    async def count_events_for_subject(
        self,
        subject: str,
        event_type: str | None = None,
        since: datetime | None = None,
    ) -> int:
        """Count events for a subject."""
        stmt = (
            sa.select(sa.func.count())
            .select_from(events_table)
            .where(events_table.c.subject == subject)
        )
        if event_type:
            stmt = stmt.where(events_table.c.event_type == event_type)
        if since is not None:
            stmt = stmt.where(events_table.c.occurred_at >= since.isoformat())
        async with self.engine.begin() as conn:
            result = await conn.execute(stmt)
            row = result.fetchone()
        return int(row[0]) if row else 0

    async def get_event_recurrence_summary(
        self,
        subject: str,
        event_type: str = "anomaly",
        days: int = 30,
    ) -> dict | None:
        """Get recurrence stats: count, first/last seen, common hours."""
        cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat()
        stmt = sa.select(
            events_table.c.occurred_at,
        ).where(
            events_table.c.subject == subject,
        ).where(
            events_table.c.event_type == event_type,
        ).where(
            events_table.c.occurred_at >= cutoff,
        ).order_by(events_table.c.occurred_at.asc())

        async with self.engine.begin() as conn:
            result = await conn.execute(stmt)
            rows = result.fetchall()

        if not rows:
            return None

        timestamps = [r[0] for r in rows]
        first_seen = timestamps[0]
        last_seen = timestamps[-1]

        # Compute common hours
        hours: list[int] = []
        for ts in timestamps:
            try:
                dt = datetime.fromisoformat(ts)
                hours.append(dt.hour)
            except (ValueError, TypeError):
                pass

        hour_counts: dict[int, int] = {}
        for h in hours:
            hour_counts[h] = hour_counts.get(h, 0) + 1
        max_count = max(hour_counts.values()) if hour_counts else 0
        common_hours = sorted(
            h for h, c in hour_counts.items() if c == max_count
        ) if max_count > 0 else []

        return {
            "count": len(timestamps),
            "first_seen": first_seen,
            "last_seen": last_seen,
            "common_hours": common_hours,
        }

    # ---------- semantic facts ----------

    async def find_existing_fact_id(
        self, concept: str, source: str, fact_type: str,
    ) -> str | None:
        """Find an active fact by concept+source+fact_type. Returns its id or None."""
        async with self.engine.begin() as conn:
            result = await conn.execute(
                sa.select(semantic_facts.c.id)
                .where(semantic_facts.c.concept == concept)
                .where(semantic_facts.c.source == source)
                .where(semantic_facts.c.fact_type == fact_type)
                .where(semantic_facts.c.is_active == True)  # noqa: E712
                .limit(1)
            )
            row = result.fetchone()
        return row[0] if row else None

    async def save_semantic_fact(self, fact: SemanticFact) -> None:
        now = _utcnow_iso()
        tables_j = json.dumps(fact.tables)
        columns_j = json.dumps(fact.columns)
        fact_type_val = (
            fact.fact_type.value
            if hasattr(fact.fact_type, "value") else fact.fact_type
        )
        source_val = fact.source.value if hasattr(fact.source, "value") else fact.source

        # Upsert by concept+source+fact_type to prevent duplicates on re-seed
        existing_id = await self.find_existing_fact_id(
            fact.concept, source_val, fact_type_val,
        )
        fact_id = existing_id or fact.id

        async with self.engine.begin() as conn:
            stmt = (
                _dialect_insert(semantic_facts, self.engine)
                .values(
                    id=fact_id,
                    fact_type=fact_type_val,
                    concept=fact.concept,
                    claim=fact.claim,
                    tables_json=tables_j,
                    columns_json=columns_j,
                    sql_condition=fact.sql_condition,
                    evidence_path=fact.evidence_path,
                    evidence_lines=fact.evidence_lines,
                    evidence_commit=fact.evidence_commit,
                    source=source_val,
                    confidence=fact.confidence,
                    created_at=fact.created_at.isoformat() if fact.created_at else now,
                    updated_at=fact.updated_at.isoformat() if fact.updated_at else now,
                    valid_from_commit=fact.valid_from_commit,
                    valid_to_commit=fact.valid_to_commit,
                    is_active=fact.is_active,
                )
                .on_conflict_do_update(
                    index_elements=["id"],
                    set_=dict(
                        claim=fact.claim,
                        tables_json=tables_j,
                        columns_json=columns_j,
                        sql_condition=fact.sql_condition,
                        confidence=fact.confidence,
                        updated_at=now,
                        is_active=fact.is_active,
                    ),
                )
            )
            await conn.execute(stmt)
            if "sqlite" in str(self.engine.url):
                await conn.execute(sa.text(
                    "INSERT OR REPLACE INTO semantic_facts_fts"
                    "(rowid, concept, claim, tables_json, columns_json) "
                    "SELECT rowid, concept, claim, tables_json, columns_json "
                    "FROM semantic_facts WHERE id = :id"
                ), {"id": fact_id})

    async def get_semantic_facts(
        self,
        concept: str | None = None,
        fact_type: str | None = None,
        active_only: bool = True,
    ) -> list[dict]:
        stmt = sa.select(
            semantic_facts.c.id,
            semantic_facts.c.fact_type,
            semantic_facts.c.concept,
            semantic_facts.c.claim,
            semantic_facts.c.tables_json,
            semantic_facts.c.columns_json,
            semantic_facts.c.sql_condition,
            semantic_facts.c.source,
            semantic_facts.c.confidence,
            semantic_facts.c.is_active,
        )
        if active_only:
            stmt = stmt.where(semantic_facts.c.is_active == True)  # noqa: E712
        if concept:
            stmt = stmt.where(semantic_facts.c.concept == concept)
        if fact_type:
            stmt = stmt.where(semantic_facts.c.fact_type == fact_type)
        stmt = stmt.order_by(semantic_facts.c.confidence.desc())

        async with self.engine.begin() as conn:
            result = await conn.execute(stmt)
            rows = result.fetchall()
        return [
            {
                "id": r[0], "fact_type": r[1], "concept": r[2],
                "claim": r[3], "tables": json.loads(r[4]) if r[4] else [],
                "columns": json.loads(r[5]) if r[5] else [],
                "sql_condition": r[6], "source": r[7],
                "confidence": r[8], "is_active": r[9],
            }
            for r in rows
        ]

    async def search_semantic_facts(self, query: str, limit: int = 5) -> list[dict]:
        """Search semantic facts using FTS5 (SQLite) or ILIKE fallback."""
        from observibot.core.code_intelligence.retrieval import build_fts5_query

        if "sqlite" in str(self.engine.url):
            fts_query = build_fts5_query(query)
            sql = sa.text(
                "SELECT s.id, s.fact_type, s.concept, s.claim, "
                "s.tables_json, s.columns_json, s.sql_condition, "
                "s.source, s.confidence, s.is_active "
                "FROM semantic_facts s "
                "JOIN semantic_facts_fts f ON s.rowid = f.rowid "
                "WHERE semantic_facts_fts MATCH :query "
                "AND s.is_active = 1 "
                "ORDER BY rank LIMIT :limit"
            )
            async with self.engine.begin() as conn:
                result = await conn.execute(sql, {"query": fts_query, "limit": limit})
                rows = result.fetchall()
        else:
            pattern = f"%{query}%"
            stmt = (
                sa.select(
                    semantic_facts.c.id,
                    semantic_facts.c.fact_type,
                    semantic_facts.c.concept,
                    semantic_facts.c.claim,
                    semantic_facts.c.tables_json,
                    semantic_facts.c.columns_json,
                    semantic_facts.c.sql_condition,
                    semantic_facts.c.source,
                    semantic_facts.c.confidence,
                    semantic_facts.c.is_active,
                )
                .where(semantic_facts.c.is_active == True)  # noqa: E712
                .where(
                    sa.or_(
                        semantic_facts.c.concept.ilike(pattern),
                        semantic_facts.c.claim.ilike(pattern),
                    )
                )
                .order_by(semantic_facts.c.confidence.desc())
                .limit(limit)
            )
            async with self.engine.begin() as conn:
                result = await conn.execute(stmt)
                rows = result.fetchall()

        return [
            {
                "id": r[0], "fact_type": r[1], "concept": r[2],
                "claim": r[3], "tables": json.loads(r[4]) if r[4] else [],
                "columns": json.loads(r[5]) if r[5] else [],
                "sql_condition": r[6], "source": r[7],
                "confidence": r[8], "is_active": r[9],
            }
            for r in rows
        ]

    async def dedup_semantic_facts(self) -> int:
        """Remove duplicate active facts, keeping one per concept+source+fact_type."""
        async with self.engine.begin() as conn:
            groups = await conn.execute(sa.text(
                "SELECT concept, source, fact_type, COUNT(*) as cnt "
                "FROM semantic_facts WHERE is_active = 1 "
                "GROUP BY concept, source, fact_type HAVING cnt > 1"
            ))
            total_removed = 0
            for row in groups.fetchall():
                concept, source, fact_type = row[0], row[1], row[2]
                # Keep the one with the latest updated_at
                dupes = await conn.execute(sa.text(
                    "SELECT id FROM semantic_facts "
                    "WHERE concept = :concept AND source = :source "
                    "AND fact_type = :fact_type AND is_active = 1 "
                    "ORDER BY updated_at DESC"
                ), {"concept": concept, "source": source, "fact_type": fact_type})
                ids = [r[0] for r in dupes.fetchall()]
                if len(ids) > 1:
                    to_deactivate = ids[1:]  # keep first (latest), deactivate rest
                    for did in to_deactivate:
                        await conn.execute(
                            semantic_facts.update()
                            .where(semantic_facts.c.id == did)
                            .values(is_active=False, updated_at=_utcnow_iso())
                        )
                    total_removed += len(to_deactivate)
        return total_removed

    async def deactivate_semantic_fact(self, fact_id: str) -> None:
        async with self.engine.begin() as conn:
            await conn.execute(
                semantic_facts.update()
                .where(semantic_facts.c.id == fact_id)
                .values(is_active=False, updated_at=_utcnow_iso())
            )

    async def save_user_correction(
        self, concept: str, claim: str,
        tables: list[str], columns: list[str],
        sql_condition: str | None = None,
    ) -> None:
        """Convenience method to create a CORRECTION fact with highest confidence."""
        import uuid as _uuid

        from observibot.core.code_intelligence.models import (
            FactSource,
            FactType,
            SemanticFact,
        )

        fact = SemanticFact(
            id=_uuid.uuid4().hex[:12],
            fact_type=FactType.CORRECTION,
            concept=concept,
            claim=claim,
            tables=tables,
            columns=columns,
            sql_condition=sql_condition,
            source=FactSource.USER_CORRECTION,
            confidence=1.0,
            is_active=True,
        )
        await self.save_semantic_fact(fact)

    async def set_code_intelligence_meta(self, key: str, value: str) -> None:
        async with self.engine.begin() as conn:
            stmt = (
                _dialect_insert(code_intelligence_meta, self.engine)
                .values(key=key, value=value, updated_at=_utcnow_iso())
                .on_conflict_do_update(
                    index_elements=["key"],
                    set_=dict(value=value, updated_at=_utcnow_iso()),
                )
            )
            await conn.execute(stmt)

    async def get_code_intelligence_meta(self, key: str) -> str | None:
        async with self.engine.begin() as conn:
            result = await conn.execute(
                sa.select(code_intelligence_meta.c.value)
                .where(code_intelligence_meta.c.key == key)
            )
            row = result.fetchone()
        return row[0] if row else None

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

            # Retention for Phase 4.5 tables
            r = await conn.execute(
                monitor_runs.delete().where(
                    monitor_runs.c.started_at
                    < (now - timedelta(days=events_days)).isoformat()
                )
            )
            results["monitor_runs"] = r.rowcount or 0

            r = await conn.execute(
                insight_feedback.delete().where(
                    insight_feedback.c.created_at
                    < (now - timedelta(days=insights_days)).isoformat()
                )
            )
            results["insight_feedback"] = r.rowcount or 0

            r = await conn.execute(
                events_table.delete().where(
                    events_table.c.occurred_at
                    < (now - timedelta(days=events_days)).isoformat()
                )
            )
            results["observation_events"] = r.rowcount or 0

        return results
