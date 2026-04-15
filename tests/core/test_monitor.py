from __future__ import annotations

import pytest

from observibot.agent.analyzer import Analyzer
from observibot.alerting.base import AlertManager
from observibot.core.config import MonitorConfig, ObservibotConfig
from observibot.core.monitor import CircuitBreaker, build_monitor_loop

pytestmark = pytest.mark.asyncio


async def test_circuit_breaker_soft_failures_escalate() -> None:
    cb = CircuitBreaker()
    assert not cb.is_open()
    cb.record_soft_failure()
    cb.record_soft_failure()
    assert not cb.is_open()
    cb.record_soft_failure()  # third → open
    assert cb.is_open()
    cb.record_success()
    assert not cb.is_open()


async def test_circuit_breaker_hard_failure_opens_immediately() -> None:
    cb = CircuitBreaker()
    cb.record_hard_failure()
    assert cb.is_open()
    # hard cooldown grows with repeated failures
    assert cb.state.cooldown == CircuitBreaker.HARD_COOLDOWNS[0]
    cb.record_success()
    cb.record_hard_failure()
    cb.record_hard_failure()
    assert cb.state.cooldown == CircuitBreaker.HARD_COOLDOWNS[1]


async def test_monitor_collection_cycle(
    tmp_store, mock_supabase_connector, mock_llm_provider
) -> None:
    analyzer = Analyzer(provider=mock_llm_provider, store=tmp_store)
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
        connectors=[mock_supabase_connector],
        store=tmp_store,
        analyzer=analyzer,
        alert_manager=alert_manager,
    )
    await loop.run_discovery_cycle()
    metric_count = await loop.run_collection_cycle()
    assert metric_count >= 1


async def test_llm_used_true_when_analysis_attempted(
    tmp_store, mock_supabase_connector, mock_llm_provider
) -> None:
    """Hotfix 2 Fix 3: llm_used tracks attempt, not output."""
    from datetime import UTC, datetime
    from unittest.mock import patch

    from observibot.core.anomaly import Anomaly

    fake_anomaly = Anomaly(
        metric_name="active_connections",
        connector_name="mock-supabase",
        labels={},
        value=100.0,
        median=5.0,
        mad=2.0,
        modified_z=10.0,
        absolute_diff=95.0,
        severity="warning",
        direction="spike",
        consecutive_count=3,
        detected_at=datetime.now(UTC),
        sample_count=20,
    )

    analyzer = Analyzer(provider=mock_llm_provider, store=tmp_store)
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
        connectors=[mock_supabase_connector],
        store=tmp_store,
        analyzer=analyzer,
        alert_manager=alert_manager,
    )
    await loop.run_discovery_cycle()

    # Patch the detector to return an anomaly so analysis is triggered
    original_evaluate = loop.detector.evaluate
    def mock_evaluate(**kwargs):
        return [fake_anomaly]
    loop.detector.evaluate = mock_evaluate

    await loop.run_collection_cycle()

    # Restore
    loop.detector.evaluate = original_evaluate

    # Find the monitor run record
    from observibot.core.store import monitor_runs
    import sqlalchemy as sa
    async with tmp_store.engine.begin() as conn:
        result = await conn.execute(
            sa.select(monitor_runs.c.llm_used, monitor_runs.c.status)
            .order_by(monitor_runs.c.started_at.desc())
            .limit(1)
        )
        row = result.fetchone()

    assert row is not None
    # llm_used should be True because analysis was attempted
    assert row[0] is True
    assert row[1] == "completed"


async def test_llm_used_false_when_no_anomalies(
    tmp_store, mock_supabase_connector, mock_llm_provider
) -> None:
    """Hotfix 2 Fix 3: llm_used=False when no anomalies exist."""
    analyzer = Analyzer(provider=mock_llm_provider, store=tmp_store)
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
        connectors=[mock_supabase_connector],
        store=tmp_store,
        analyzer=analyzer,
        alert_manager=alert_manager,
    )
    await loop.run_discovery_cycle()
    await loop.run_collection_cycle()

    from observibot.core.store import monitor_runs
    import sqlalchemy as sa
    async with tmp_store.engine.begin() as conn:
        result = await conn.execute(
            sa.select(monitor_runs.c.llm_used, monitor_runs.c.status)
            .order_by(monitor_runs.c.started_at.desc())
            .limit(1)
        )
        row = result.fetchone()

    assert row is not None
    # No anomalies → no analysis → llm_used=False
    assert row[0] is not True
    assert row[1] == "completed"


async def test_baseline_loaded_before_current_batch_saved(
    tmp_store, mock_supabase_connector, mock_llm_provider
) -> None:
    """Pipeline-audit Fix 2: anomaly detection must evaluate against history
    that does NOT include the current batch.

    Why: if the current batch is saved first then loaded back as 'history',
    each value contributes to its own median/MAD, biasing toward false
    negatives (the outlier inflates the spread it's measured against).

    We capture the history snapshot the detector receives and assert that
    none of the current batch's collected_at timestamps appear in it.
    """
    captured = {}

    analyzer = Analyzer(provider=mock_llm_provider, store=tmp_store)
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
        connectors=[mock_supabase_connector],
        store=tmp_store,
        analyzer=analyzer,
        alert_manager=alert_manager,
    )
    await loop.run_discovery_cycle()

    # Seed two prior cycles so history is non-empty
    await loop.run_collection_cycle()
    await loop.run_collection_cycle()

    original_evaluate = loop.detector.evaluate

    def capturing_evaluate(*, history, latest):
        captured["history_ids"] = {
            (m.metric_name, m.collected_at) for m in history
        }
        captured["latest_ids"] = {
            (m.metric_name, m.collected_at) for m in latest
        }
        return original_evaluate(history=history, latest=latest)

    loop.detector.evaluate = capturing_evaluate
    await loop.run_collection_cycle()
    loop.detector.evaluate = original_evaluate

    history_ids = captured["history_ids"]
    latest_ids = captured["latest_ids"]
    assert latest_ids, "test fixture must produce at least one metric"
    overlap = history_ids & latest_ids
    assert not overlap, (
        f"Current batch leaked into baseline history: {sorted(overlap)[:3]}"
    )


async def test_monitor_handles_failing_connector(
    tmp_store, mock_supabase_connector, mock_llm_provider
) -> None:
    class Broken(type(mock_supabase_connector)):
        async def collect_metrics(self):  # type: ignore[override]
            raise RuntimeError("nope")

    broken = Broken(name="broken")
    analyzer = Analyzer(provider=mock_llm_provider, store=tmp_store)
    alert_manager = AlertManager(channels=[])
    cfg = ObservibotConfig()
    loop = build_monitor_loop(
        config=cfg,
        connectors=[mock_supabase_connector, broken],
        store=tmp_store,
        analyzer=analyzer,
        alert_manager=alert_manager,
    )
    await loop.run_discovery_cycle()
    # Should not raise even though `broken` errors
    count = await loop.run_collection_cycle()
    assert count >= 1
