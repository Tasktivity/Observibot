from __future__ import annotations

import pytest

from observibot.agent.analyzer import Analyzer
from observibot.alerting.base import AlertManager
from observibot.core.config import ObservibotConfig, MonitorConfig
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
