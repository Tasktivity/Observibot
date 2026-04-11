"""Async SQLite store for Observibot.

Holds discovery snapshots, metrics, change events, insights, alert history,
business context, LLM usage, and metric baselines. The schema is auto-created
on first use; the parent directory is created automatically as well.
"""
from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import aiosqlite

from observibot.core.models import (
    ChangeEvent,
    Insight,
    MetricSnapshot,
    SystemModel,
)

SCHEMA_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS system_snapshots (
        id TEXT PRIMARY KEY,
        fingerprint TEXT NOT NULL,
        payload TEXT NOT NULL,
        created_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_snap_created ON system_snapshots(created_at)",
    """
    CREATE TABLE IF NOT EXISTS metric_snapshots (
        id TEXT PRIMARY KEY,
        connector_name TEXT NOT NULL,
        metric_name TEXT NOT NULL,
        value REAL NOT NULL,
        labels TEXT,
        collected_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_metrics_name_time ON metric_snapshots"
    "(metric_name, collected_at)",
    "CREATE INDEX IF NOT EXISTS idx_metrics_time ON metric_snapshots(collected_at)",
    """
    CREATE TABLE IF NOT EXISTS change_events (
        id TEXT PRIMARY KEY,
        connector_name TEXT NOT NULL,
        event_type TEXT NOT NULL,
        summary TEXT,
        details TEXT,
        occurred_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_events_time ON change_events(occurred_at)",
    """
    CREATE TABLE IF NOT EXISTS insights (
        id TEXT PRIMARY KEY,
        severity TEXT NOT NULL,
        title TEXT,
        summary TEXT,
        details TEXT,
        recommended_actions TEXT,
        related_metrics TEXT,
        related_tables TEXT,
        confidence REAL,
        source TEXT,
        fingerprint TEXT,
        created_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_insights_fp ON insights(fingerprint)",
    "CREATE INDEX IF NOT EXISTS idx_insights_time ON insights(created_at)",
    """
    CREATE TABLE IF NOT EXISTS alert_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        insight_id TEXT,
        channel TEXT,
        severity TEXT,
        status TEXT,
        message TEXT,
        sent_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS business_context (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS llm_usage (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        provider TEXT,
        model TEXT,
        prompt_tokens INTEGER,
        completion_tokens INTEGER,
        total_tokens INTEGER,
        cost_usd REAL,
        purpose TEXT,
        recorded_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS metric_baselines (
        metric_name TEXT NOT NULL,
        connector_name TEXT NOT NULL,
        labels_key TEXT NOT NULL,
        sample_count INTEGER NOT NULL,
        mean REAL NOT NULL,
        stddev REAL NOT NULL,
        last_updated TEXT NOT NULL,
        PRIMARY KEY (metric_name, connector_name, labels_key)
    )
    """,
]


def _utcnow_iso() -> str:
    return datetime.now(UTC).isoformat()


def _labels_key(labels: dict[str, str]) -> str:
    """Stable string key for grouping metrics by their labels."""
    if not labels:
        return ""
    return json.dumps(labels, sort_keys=True)


class Store:
    """Async SQLite store.

    Use as an async context manager::

        async with Store("data/observibot.db") as store:
            await store.save_metric(...)
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._conn: aiosqlite.Connection | None = None

    async def __aenter__(self) -> Store:
        await self.connect()
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        await self.close()

    async def connect(self) -> None:
        """Open the SQLite connection and create the schema if needed."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self.path)
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA foreign_keys=ON")
        for stmt in SCHEMA_STATEMENTS:
            await self._conn.execute(stmt)
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Store is not connected. Call connect() or use 'async with'.")
        return self._conn

    # ---------- system snapshots ----------

    async def save_system_snapshot(self, model: SystemModel) -> None:
        """Persist a SystemModel snapshot."""
        await self.conn.execute(
            "INSERT OR REPLACE INTO system_snapshots(id, fingerprint, payload, created_at) "
            "VALUES (?, ?, ?, ?)",
            (
                model.id,
                model.fingerprint or model.compute_fingerprint(),
                json.dumps(model.to_dict()),
                model.created_at.isoformat(),
            ),
        )
        await self.conn.commit()

    async def get_latest_system_snapshot(self) -> SystemModel | None:
        async with self.conn.execute(
            "SELECT payload FROM system_snapshots ORDER BY created_at DESC LIMIT 1"
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return None
        return SystemModel.from_dict(json.loads(row[0]))

    # ---------- metrics ----------

    async def save_metric(self, metric: MetricSnapshot) -> None:
        await self.save_metrics([metric])

    async def save_metrics(self, metrics: Iterable[MetricSnapshot]) -> int:
        """Bulk insert metric snapshots. Returns the number written."""
        rows = [
            (
                m.id,
                m.connector_name,
                m.metric_name,
                float(m.value),
                json.dumps(m.labels) if m.labels else None,
                m.collected_at.isoformat(),
            )
            for m in metrics
        ]
        if not rows:
            return 0
        await self.conn.executemany(
            "INSERT OR REPLACE INTO metric_snapshots"
            "(id, connector_name, metric_name, value, labels, collected_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            rows,
        )
        await self.conn.commit()
        return len(rows)

    async def get_metrics(
        self,
        metric_name: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        connector_name: str | None = None,
        limit: int | None = None,
    ) -> list[MetricSnapshot]:
        clauses: list[str] = []
        params: list[Any] = []
        if metric_name:
            clauses.append("metric_name = ?")
            params.append(metric_name)
        if connector_name:
            clauses.append("connector_name = ?")
            params.append(connector_name)
        if since is not None:
            clauses.append("collected_at >= ?")
            params.append(since.isoformat())
        if until is not None:
            clauses.append("collected_at <= ?")
            params.append(until.isoformat())
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = (
            "SELECT id, connector_name, metric_name, value, labels, collected_at "
            f"FROM metric_snapshots {where} ORDER BY collected_at ASC"
        )
        if limit is not None:
            sql += " LIMIT ?"
            params.append(int(limit))
        async with self.conn.execute(sql, params) as cur:
            rows = await cur.fetchall()
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

    async def save_change_event(self, event: ChangeEvent) -> None:
        await self.conn.execute(
            "INSERT OR REPLACE INTO change_events"
            "(id, connector_name, event_type, summary, details, occurred_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                event.id,
                event.connector_name,
                event.event_type,
                event.summary,
                json.dumps(event.details),
                event.occurred_at.isoformat(),
            ),
        )
        await self.conn.commit()

    async def get_recent_change_events(
        self, since: datetime | None = None, limit: int = 100
    ) -> list[ChangeEvent]:
        if since:
            sql = (
                "SELECT id, connector_name, event_type, summary, details, occurred_at "
                "FROM change_events WHERE occurred_at >= ? "
                "ORDER BY occurred_at DESC LIMIT ?"
            )
            params: tuple[Any, ...] = (since.isoformat(), limit)
        else:
            sql = (
                "SELECT id, connector_name, event_type, summary, details, occurred_at "
                "FROM change_events ORDER BY occurred_at DESC LIMIT ?"
            )
            params = (limit,)
        async with self.conn.execute(sql, params) as cur:
            rows = await cur.fetchall()
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
        """Save an insight if a recent equivalent isn't already stored.

        Returns True if newly stored, False if a recent duplicate exists.
        """
        cutoff = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
        async with self.conn.execute(
            "SELECT id FROM insights WHERE fingerprint = ? AND created_at >= ? LIMIT 1",
            (insight.fingerprint, cutoff),
        ) as cur:
            row = await cur.fetchone()
        if row is not None:
            return False
        await self.conn.execute(
            "INSERT OR REPLACE INTO insights"
            "(id, severity, title, summary, details, recommended_actions, "
            "related_metrics, related_tables, confidence, source, fingerprint, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                insight.id,
                insight.severity,
                insight.title,
                insight.summary,
                insight.details,
                json.dumps(insight.recommended_actions),
                json.dumps(insight.related_metrics),
                json.dumps(insight.related_tables),
                insight.confidence,
                insight.source,
                insight.fingerprint,
                insight.created_at.isoformat(),
            ),
        )
        await self.conn.commit()
        return True

    async def get_recent_insights(self, limit: int = 20) -> list[Insight]:
        async with self.conn.execute(
            "SELECT id, severity, title, summary, details, recommended_actions, "
            "related_metrics, related_tables, confidence, source, fingerprint, created_at "
            "FROM insights ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ) as cur:
            rows = await cur.fetchall()
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
        await self.conn.execute(
            "INSERT INTO alert_history(insight_id, channel, severity, status, message, sent_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (insight_id, channel, severity, status, message, _utcnow_iso()),
        )
        await self.conn.commit()

    async def count_alerts_since(self, since: datetime) -> int:
        async with self.conn.execute(
            "SELECT COUNT(*) FROM alert_history WHERE sent_at >= ?",
            (since.isoformat(),),
        ) as cur:
            row = await cur.fetchone()
        return int(row[0]) if row else 0

    # ---------- business context ----------

    async def set_business_context(self, key: str, value: Any) -> None:
        await self.conn.execute(
            "INSERT OR REPLACE INTO business_context(key, value, updated_at) VALUES (?, ?, ?)",
            (key, json.dumps(value), _utcnow_iso()),
        )
        await self.conn.commit()

    async def get_business_context(self, key: str) -> Any:
        async with self.conn.execute(
            "SELECT value FROM business_context WHERE key = ?", (key,)
        ) as cur:
            row = await cur.fetchone()
        return json.loads(row[0]) if row else None

    async def get_all_business_context(self) -> dict[str, Any]:
        async with self.conn.execute(
            "SELECT key, value FROM business_context"
        ) as cur:
            rows = await cur.fetchall()
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
        await self.conn.execute(
            "INSERT INTO llm_usage(provider, model, prompt_tokens, completion_tokens, "
            "total_tokens, cost_usd, purpose, recorded_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                provider,
                model,
                prompt_tokens,
                completion_tokens,
                prompt_tokens + completion_tokens,
                cost_usd,
                purpose,
                _utcnow_iso(),
            ),
        )
        await self.conn.commit()

    async def get_llm_usage_summary(self, since: datetime | None = None) -> dict[str, Any]:
        if since is None:
            since = datetime.now(UTC) - timedelta(days=1)
        async with self.conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(total_tokens), 0), COALESCE(SUM(cost_usd), 0) "
            "FROM llm_usage WHERE recorded_at >= ?",
            (since.isoformat(),),
        ) as cur:
            row = await cur.fetchone()
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
        await self.conn.execute(
            "INSERT OR REPLACE INTO metric_baselines"
            "(metric_name, connector_name, labels_key, sample_count, mean, stddev, last_updated) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                metric_name,
                connector_name,
                _labels_key(labels),
                sample_count,
                mean,
                stddev,
                _utcnow_iso(),
            ),
        )
        await self.conn.commit()

    async def get_baseline(
        self, metric_name: str, connector_name: str, labels: dict[str, str]
    ) -> dict[str, Any] | None:
        async with self.conn.execute(
            "SELECT sample_count, mean, stddev, last_updated FROM metric_baselines "
            "WHERE metric_name = ? AND connector_name = ? AND labels_key = ?",
            (metric_name, connector_name, _labels_key(labels)),
        ) as cur:
            row = await cur.fetchone()
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
        """Delete old rows according to retention policy. Returns deleted counts."""
        now = datetime.now(UTC)
        results: dict[str, int] = {}
        cur = await self.conn.execute(
            "DELETE FROM metric_snapshots WHERE collected_at < ?",
            ((now - timedelta(days=metrics_days)).isoformat(),),
        )
        results["metrics"] = cur.rowcount or 0
        cur = await self.conn.execute(
            "DELETE FROM change_events WHERE occurred_at < ?",
            ((now - timedelta(days=events_days)).isoformat(),),
        )
        results["events"] = cur.rowcount or 0
        cur = await self.conn.execute(
            "DELETE FROM insights WHERE created_at < ?",
            ((now - timedelta(days=insights_days)).isoformat(),),
        )
        results["insights"] = cur.rowcount or 0
        # Trim system_snapshots to last N
        async with self.conn.execute(
            "SELECT id FROM system_snapshots ORDER BY created_at DESC"
        ) as snap_cur:
            snap_rows = await snap_cur.fetchall()
        excess = [r[0] for r in snap_rows[max_snapshots:]]
        if excess:
            placeholders = ",".join("?" * len(excess))
            await self.conn.execute(
                f"DELETE FROM system_snapshots WHERE id IN ({placeholders})", excess
            )
        results["snapshots"] = len(excess)
        await self.conn.commit()
        return results
