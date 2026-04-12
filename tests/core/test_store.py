from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
import sqlalchemy as sa

from observibot.core.models import (
    ChangeEvent,
    Insight,
    MetricSnapshot,
    SystemModel,
    TableInfo,
)
from observibot.core.store import Store, build_engine, metadata


pytestmark = pytest.mark.asyncio


async def test_store_auto_creates_parent(tmp_path: Path) -> None:
    target = tmp_path / "deep" / "nested" / "store.db"
    async with Store(target) as store:
        assert target.parent.exists()
        assert store.conn is not None


async def test_save_and_fetch_system_snapshot(tmp_store, sample_system_model: SystemModel) -> None:
    await tmp_store.save_system_snapshot(sample_system_model)
    fetched = await tmp_store.get_latest_system_snapshot()
    assert fetched is not None
    assert fetched.fingerprint == sample_system_model.fingerprint
    assert len(fetched.tables) == len(sample_system_model.tables)


async def test_batch_metric_insert(tmp_store) -> None:
    now = datetime.now(timezone.utc)
    metrics = [
        MetricSnapshot(
            connector_name="c",
            metric_name="x",
            value=float(i),
            collected_at=now,
        )
        for i in range(25)
    ]
    written = await tmp_store.save_metrics(metrics)
    assert written == 25
    fetched = await tmp_store.get_metrics(metric_name="x")
    assert len(fetched) == 25


async def test_get_metrics_time_range(tmp_store) -> None:
    now = datetime.now(timezone.utc)
    await tmp_store.save_metrics(
        [
            MetricSnapshot(
                connector_name="c",
                metric_name="x",
                value=1,
                collected_at=now - timedelta(hours=2),
            ),
            MetricSnapshot(
                connector_name="c",
                metric_name="x",
                value=2,
                collected_at=now,
            ),
        ]
    )
    recent = await tmp_store.get_metrics(
        metric_name="x", since=now - timedelta(minutes=30)
    )
    assert len(recent) == 1
    assert recent[0].value == 2


async def test_insight_dedup(tmp_store) -> None:
    a = Insight(title="same", summary="same", severity="warning")
    b = Insight(title="same", summary="same", severity="warning")
    assert await tmp_store.save_insight(a) is True
    assert await tmp_store.save_insight(b) is False


async def test_change_event_crud(tmp_store) -> None:
    e = ChangeEvent(connector_name="c", event_type="deploy", summary="s")
    await tmp_store.save_change_event(e)
    recent = await tmp_store.get_recent_change_events()
    assert len(recent) == 1


async def test_business_context_roundtrip(tmp_store) -> None:
    await tmp_store.set_business_context("app_type", "task management")
    assert await tmp_store.get_business_context("app_type") == "task management"
    all_context = await tmp_store.get_all_business_context()
    assert all_context["app_type"] == "task management"


async def test_llm_usage_summary(tmp_store) -> None:
    await tmp_store.record_llm_usage(
        provider="mock",
        model="m",
        prompt_tokens=100,
        completion_tokens=50,
        cost_usd=0.01,
        purpose="analysis",
    )
    summary = await tmp_store.get_llm_usage_summary()
    assert summary["calls"] == 1
    assert summary["total_tokens"] == 150


async def test_baseline_upsert(tmp_store) -> None:
    await tmp_store.upsert_baseline("x", "c", {"table": "t"}, 20, 10.0, 1.5)
    row = await tmp_store.get_baseline("x", "c", {"table": "t"})
    assert row is not None
    assert row["mean"] == 10.0
    assert row["stddev"] == 1.5


async def test_retention_cleanup(tmp_store) -> None:
    now = datetime.now(timezone.utc)
    # Old metric
    await tmp_store.save_metric(
        MetricSnapshot(
            connector_name="c",
            metric_name="x",
            value=1,
            collected_at=now - timedelta(days=60),
        )
    )
    # Fresh metric
    await tmp_store.save_metric(
        MetricSnapshot(
            connector_name="c",
            metric_name="x",
            value=2,
            collected_at=now,
        )
    )
    deleted = await tmp_store.apply_retention(
        metrics_days=30, events_days=90, insights_days=90, max_snapshots=5
    )
    assert deleted["metrics"] == 1
    remaining = await tmp_store.get_metrics(metric_name="x")
    assert len(remaining) == 1
    assert remaining[0].value == 2


async def test_alert_history(tmp_store) -> None:
    await tmp_store.record_alert(
        insight_id="x", channel="slack", severity="warning", status="ok", message="sent"
    )
    count = await tmp_store.count_alerts_since(datetime.now(timezone.utc) - timedelta(hours=1))
    assert count == 1


def test_build_engine_sqlite_url() -> None:
    engine = build_engine("sqlite+aiosqlite:///test.db")
    assert "sqlite" in str(engine.url)
    assert engine.url.drivername == "sqlite+aiosqlite"


def test_build_engine_postgres_url() -> None:
    engine = build_engine("postgres://user:pass@host/db")
    assert engine.url.drivername == "postgresql+asyncpg"


def test_build_engine_postgresql_url() -> None:
    engine = build_engine("postgresql://user:pass@host/db")
    assert engine.url.drivername == "postgresql+asyncpg"


def test_build_engine_defaults_to_env_var() -> None:
    with patch.dict("os.environ", {"DATABASE_URL": "sqlite+aiosqlite:///env.db"}):
        engine = build_engine()
        assert "env.db" in str(engine.url)


def test_metadata_has_phase3_tables() -> None:
    table_names = set(metadata.tables.keys())
    assert "users" in table_names
    assert "dashboard_widgets" in table_names
    assert "query_cache" in table_names


async def test_table_creation_via_metadata(tmp_path: Path) -> None:
    db_path = tmp_path / "schema_test.db"
    async with Store(db_path) as store:
        async with store.engine.begin() as conn:
            result = await conn.execute(
                sa.text("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
            )
            tables = {r[0] for r in result.fetchall()}
    assert "system_snapshots" in tables
    assert "metric_snapshots" in tables
    assert "users" in tables
    assert "dashboard_widgets" in tables
    assert "query_cache" in tables


async def test_store_engine_property(tmp_path: Path) -> None:
    db_path = tmp_path / "engine_test.db"
    async with Store(db_path) as store:
        assert store.engine is not None
        assert store.conn is not None


async def test_store_not_connected_raises() -> None:
    store = Store("unused.db")
    with pytest.raises(RuntimeError, match="not connected"):
        _ = store.conn
    with pytest.raises(RuntimeError, match="not connected"):
        _ = store.engine
