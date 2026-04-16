"""Tier 2 integration test: verify that recurrence_context survives the
full anomaly→insight pipeline and is persisted in the database.

Uses a real SQLite store (no mocks on the persistence layer).
"""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa

from observibot.agent.analyzer import Analyzer
from observibot.agent.llm_provider import MockProvider
from observibot.alerting.base import AlertManager
from observibot.core.anomaly import Anomaly
from observibot.core.config import MonitorConfig, ObservibotConfig
from observibot.core.monitor import build_monitor_loop
from observibot.core.store import Store, insights_table

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def seeded_store(tmp_path):
    """Real SQLite store pre-seeded with anomaly events for table_inserts.

    Uses table_inserts because MockProvider's canned anomaly response returns
    related_metrics=["table_inserts"] — the recurrence lookup must match.
    """
    path = tmp_path / "integration.db"
    async with Store(path) as store:
        for i in range(5):
            await store.emit_event(
                event_type="anomaly",
                source="monitor_loop",
                subject="table_inserts",
                ref_table="metric_snapshots",
                ref_id=f"snap_{i}",
                severity="warning",
                summary=f"table_inserts anomaly #{i}",
                agent="sre",
                run_id=f"run_{i}",
            )
        yield store


async def test_pipeline_persists_recurrence_context(seeded_store) -> None:
    """End-to-end: anomaly events → recurrence lookup → LLM analysis →
    enrichment → save_insight → DB row has recurrence_context populated."""
    from tests.conftest import FakeSupabaseConnector

    connector = FakeSupabaseConnector()
    await connector.connect()

    anomaly = Anomaly(
        metric_name="table_inserts",
        connector_name="mock-supabase",
        labels={"table": "tasks"},
        value=500.0,
        median=10.0,
        mad=2.0,
        modified_z=245.0,
        absolute_diff=490.0,
        severity="critical",
        direction="spike",
        consecutive_count=3,
        detected_at=datetime.now(UTC),
        sample_count=20,
    )

    analyzer = Analyzer(provider=MockProvider(), store=seeded_store)
    alert_manager = AlertManager(channels=[])
    cfg = ObservibotConfig()
    cfg.monitor = MonitorConfig(
        collection_interval_seconds=60,
        analysis_interval_seconds=120,
        discovery_interval_seconds=60,
        min_samples_for_baseline=3,
    )
    loop = build_monitor_loop(
        config=cfg,
        connectors=[connector],
        store=seeded_store,
        analyzer=analyzer,
        alert_manager=alert_manager,
    )
    loop._pending_anomalies = [anomaly]

    insights = await loop.run_analysis_cycle()

    assert len(insights) >= 1, "Pipeline must produce at least one insight"

    async with seeded_store.engine.begin() as conn:
        result = await conn.execute(
            sa.select(
                insights_table.c.id,
                insights_table.c.title,
                insights_table.c.recurrence_context,
            )
            .where(insights_table.c.recurrence_context.isnot(None))
            .order_by(insights_table.c.created_at.desc())
            .limit(5)
        )
        rows = result.fetchall()

    assert len(rows) >= 1, (
        "Expected at least 1 insight with recurrence_context IS NOT NULL"
    )
    ctx = json.loads(rows[0][2])
    assert ctx["count"] == 5
    assert "table_inserts" in [i.related_metrics[0] for i in insights]
