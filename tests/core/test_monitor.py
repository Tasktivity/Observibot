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


async def test_run_analysis_cycle_persists_recurrence_context(
    tmp_store, mock_supabase_connector, mock_llm_provider
) -> None:
    """Enrichment must happen BEFORE save_insight so recurrence_context is
    actually written to the database."""
    from datetime import UTC, datetime
    from unittest.mock import AsyncMock, patch

    from observibot.core.anomaly import Anomaly
    from observibot.core.models import Insight

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

    recurrence_data = {"count": 5, "common_hours": [14, 15], "last_seen": "2026-04-14"}

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
    loop._pending_anomalies = [anomaly]

    with patch.object(
        loop.store, "get_event_recurrence_summaries",
        new_callable=AsyncMock,
        return_value={"table_inserts": recurrence_data},
    ):
        insights = await loop.run_analysis_cycle()

    assert len(insights) >= 1
    import json
    import sqlalchemy as sa
    from observibot.core.store import insights_table
    async with tmp_store.engine.begin() as conn:
        result = await conn.execute(
            sa.select(insights_table.c.recurrence_context)
            .where(insights_table.c.id == insights[0].id)
        )
        row = result.fetchone()
    assert row is not None
    ctx = json.loads(row[0])
    assert ctx["count"] == 5
    assert ctx["common_hours"] == [14, 15]


async def test_run_analysis_cycle_single_save_per_insight(
    tmp_store, mock_supabase_connector, mock_llm_provider
) -> None:
    """save_insight must be called exactly once per insight — not inside
    analyze_anomalies AND again in run_analysis_cycle."""
    from datetime import UTC, datetime
    from unittest.mock import AsyncMock, patch

    from observibot.core.anomaly import Anomaly

    anomaly = Anomaly(
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
    loop._pending_anomalies = [anomaly]

    save_calls = []
    original_save = tmp_store.save_insight

    async def tracking_save(insight):
        save_calls.append(insight.id)
        return await original_save(insight)

    with patch.object(tmp_store, "save_insight", side_effect=tracking_save):
        insights = await loop.run_analysis_cycle()

    assert len(insights) >= 1
    assert len(save_calls) == len(insights)


async def test_run_analysis_cycle_dedup_skips_emit_and_alert(
    tmp_store, mock_supabase_connector, mock_llm_provider
) -> None:
    """When save_insight returns False (dedup), _emit and alert_manager.dispatch
    must NOT be called for that insight."""
    from datetime import UTC, datetime
    from unittest.mock import AsyncMock, patch

    from observibot.core.anomaly import Anomaly

    anomaly = Anomaly(
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
    loop._pending_anomalies = [anomaly]

    with patch.object(
        tmp_store, "save_insight", new_callable=AsyncMock, return_value=False
    ), patch.object(
        loop, "_emit", new_callable=AsyncMock
    ) as mock_emit, patch.object(
        loop.alert_manager, "dispatch", new_callable=AsyncMock
    ) as mock_dispatch:
        insights = await loop.run_analysis_cycle()

    assert insights == []
    # Stage 7 emits a ``correlation_run`` event every cycle (including
    # when the detector found nothing) — separate from the per-insight
    # emissions. The dedup-skip invariant is about insight-specific
    # emissions + alert dispatch, neither of which should fire when
    # save_insight returned False.
    insight_emit_calls = [
        call for call in mock_emit.call_args_list
        if call.args and call.args[0] == "insight"
    ]
    assert insight_emit_calls == []
    mock_dispatch.assert_not_called()


async def test_hard_error_fallback_is_enriched_and_dispatched(
    tmp_store, mock_supabase_connector, mock_llm_provider
) -> None:
    """S0.5 — when the analyzer raises LLMHardError with a fallback
    attached, the monitor enriches the fallback with the evidence
    bundle, persists it, and dispatches to alerting. Previously the
    analyzer called _persist internally and enrichment + dispatch
    never ran on the failure path.
    """
    from datetime import UTC, datetime
    from unittest.mock import AsyncMock, patch

    from observibot.agent.llm_provider import LLMHardError, MockProvider
    from observibot.core.anomaly import Anomaly

    class HardFailingProvider(MockProvider):
        async def _call(self, system_prompt, user_prompt):
            raise LLMHardError("401 unauthorized")

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
    analyzer = Analyzer(provider=HardFailingProvider(), store=tmp_store)
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
    loop._pending_anomalies = [anomaly]
    with patch.object(
        loop.store, "get_event_recurrence_summaries",
        new_callable=AsyncMock,
        return_value={"table_inserts": {"count": 3, "common_hours": [10]}},
    ), patch.object(
        loop.alert_manager, "dispatch", new_callable=AsyncMock
    ) as mock_dispatch:
        insights = await loop.run_analysis_cycle()

    assert len(insights) == 1
    # Enrichment: evidence bundle must be attached on the persisted row.
    assert insights[0].evidence is not None
    assert insights[0].evidence.get("recurrence")
    # Alerting: fallback must be dispatched.
    mock_dispatch.assert_awaited()
    # Circuit breaker: hard failure recorded.
    assert loop.circuit_breaker.is_open()


async def test_validation_error_fallback_is_enriched_and_dispatched(
    tmp_store, mock_supabase_connector
) -> None:
    """S0.5 — when the analyzer raises LLMSoftError on Pydantic validation
    failure with a fallback attached, the monitor still enriches +
    persists + dispatches.
    """
    from datetime import UTC, datetime
    from unittest.mock import AsyncMock, patch

    from observibot.agent.llm_provider import MockProvider
    from observibot.core.anomaly import Anomaly

    bad_provider = MockProvider(canned={"insights": [{"severity": "bogus"}]})
    analyzer = Analyzer(provider=bad_provider, store=tmp_store)
    anomaly = Anomaly(
        metric_name="cache_misses",
        connector_name="mock-supabase",
        labels={"table": "sessions"},
        value=900.0,
        median=50.0,
        mad=5.0,
        modified_z=40.0,
        absolute_diff=850.0,
        severity="warning",
        direction="spike",
        consecutive_count=3,
        detected_at=datetime.now(UTC),
        sample_count=20,
    )
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
    loop._pending_anomalies = [anomaly]
    with patch.object(
        loop.alert_manager, "dispatch", new_callable=AsyncMock
    ) as mock_dispatch:
        insights = await loop.run_analysis_cycle()

    assert len(insights) == 1
    assert insights[0].source == "anomaly"  # fallback marker
    mock_dispatch.assert_awaited()


async def test_analysis_uses_snapshotted_model_even_if_discovery_mutates(
    tmp_store, mock_supabase_connector, mock_llm_provider
) -> None:
    """S0.6 — run_analysis_cycle snapshots ``self._cached_model`` at the
    top of the cycle so a concurrent discovery mutation cannot alter
    the model that analyzer/diagnostic code sees mid-flight.
    """
    import asyncio as _asyncio
    from datetime import UTC, datetime
    from unittest.mock import AsyncMock, patch

    from observibot.core.anomaly import Anomaly
    from observibot.core.models import Insight, SystemModel, TableInfo

    pre_model = SystemModel(
        tables=[
            TableInfo(name="pre_model_table", schema="public", columns=[]),
        ],
    )
    pre_model.compute_fingerprint()
    post_model = SystemModel(
        tables=[
            TableInfo(name="post_model_table", schema="public", columns=[]),
        ],
    )
    post_model.compute_fingerprint()

    anomaly = Anomaly(
        metric_name="x",
        connector_name="mock-supabase",
        labels={"table": "y"},
        value=10.0, median=1.0, mad=0.5, modified_z=10.0,
        absolute_diff=9.0, severity="warning", direction="spike",
        consecutive_count=3, detected_at=datetime.now(UTC), sample_count=20,
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
    loop._cached_model = pre_model
    loop._pending_anomalies = [anomaly]

    captured_models: list[SystemModel | None] = []

    async def spy_analyze(*args, **kwargs):
        captured_models.append(kwargs.get("system_model"))
        # Simulate discovery mutating the cache mid-analysis.
        loop._cached_model = post_model
        await _asyncio.sleep(0)
        return [
            Insight(
                title="Snapshot test insight",
                severity="info",
                summary="s",
                source="llm",
                related_metrics=["x"],
            )
        ]

    with patch.object(analyzer, "analyze_anomalies", side_effect=spy_analyze), \
            patch.object(loop.alert_manager, "dispatch", new_callable=AsyncMock):
        await loop.run_analysis_cycle()

    assert len(captured_models) == 1
    received = captured_models[0]
    assert received is pre_model
    # The real cache advanced because of the simulated concurrent
    # discovery, but analysis saw the pre-mutation snapshot.
    assert loop._cached_model is post_model


async def test_hard_error_fallback_not_double_persisted(
    tmp_store, mock_supabase_connector
) -> None:
    """S0.5 — the monitor persists fallback insights exactly once.
    Regression: the analyzer used to call its own ``_persist`` on the
    failure path, producing a double-save pair that could collide with
    the dedup window.
    """
    from datetime import UTC, datetime
    from unittest.mock import AsyncMock, patch

    from observibot.agent.llm_provider import LLMHardError, MockProvider
    from observibot.core.anomaly import Anomaly

    class HardFailingProvider(MockProvider):
        async def _call(self, system_prompt, user_prompt):
            raise LLMHardError("auth denied")

    anomaly = Anomaly(
        metric_name="cache_hits",
        connector_name="mock-supabase",
        labels={"table": "sessions"},
        value=42.0,
        median=10.0,
        mad=2.0,
        modified_z=10.0,
        absolute_diff=32.0,
        severity="warning",
        direction="spike",
        consecutive_count=3,
        detected_at=datetime.now(UTC),
        sample_count=20,
    )
    analyzer = Analyzer(provider=HardFailingProvider(), store=tmp_store)
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
    loop._pending_anomalies = [anomaly]
    calls: list[str] = []
    original_save = tmp_store.save_insight

    async def counted_save(insight):
        calls.append(insight.id)
        return await original_save(insight)

    with patch.object(tmp_store, "save_insight", side_effect=counted_save):
        insights = await loop.run_analysis_cycle()

    assert len(insights) == 1
    assert len(calls) == 1
