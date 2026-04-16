"""Tests for Step 3.4 hypothesis-test monitor wiring.

Covers the diagnostic cooldown cache (D5), the monitor loop wiring
(D6) including cold-start gating, seasonal/critical gating, hard
wall-clock ceilings, event emission, and cache hits.

Every test uses a synthetic fixture from
``tests/fixtures/synthetic_schemas.py`` (Tier 0 generality firewall).
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock

import pytest

from observibot.agent.analyzer import Analyzer
from observibot.agent.llm_provider import MockProvider
from observibot.alerting.base import AlertManager
from observibot.core.config import DiagnosticsConfig, MonitorConfig, ObservibotConfig
from observibot.core.evidence import DiagnosticEvidence, EvidenceBundle
from observibot.core.monitor import build_monitor_loop
from tests.fixtures.synthetic_schemas import (
    ecommerce_anomaly,
    ecommerce_schema,
    event_stream_anomaly,
    event_stream_schema,
    medical_anomaly,
    medical_records_schema,
)

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeAppDb:
    """Minimal AppDatabasePool stand-in. No real connection — the
    analyzer is mocked before any query would be executed."""

    def __init__(self) -> None:
        self.is_connected = True

    @asynccontextmanager
    async def acquire(self):
        yield object()


def _make_cfg(**diag_overrides: Any) -> ObservibotConfig:
    cfg = ObservibotConfig()
    cfg.monitor = MonitorConfig(
        collection_interval_seconds=60,
        analysis_interval_seconds=120,
        discovery_interval_seconds=60,
        min_samples_for_baseline=3,
    )
    cfg.monitor.diagnostics = DiagnosticsConfig(
        enabled=diag_overrides.pop("enabled", True),
        cooldown_minutes=diag_overrides.pop("cooldown_minutes", 10),
        hypothesis_timeout_s=diag_overrides.pop("hypothesis_timeout_s", 5.0),
        execution_timeout_s=diag_overrides.pop("execution_timeout_s", 5.0),
        **diag_overrides,
    )
    return cfg


async def _build_loop(
    tmp_store,
    cfg: ObservibotConfig,
    *,
    system_model,
    attach_app_db: bool = True,
):
    analyzer = Analyzer(provider=MockProvider(), store=tmp_store)
    loop = build_monitor_loop(
        config=cfg, connectors=[], store=tmp_store,
        analyzer=analyzer, alert_manager=AlertManager(channels=[]),
    )
    loop._cached_model = system_model
    if attach_app_db:
        loop._app_db = _FakeAppDb()
    return loop


# ---------------------------------------------------------------------------
# D5 — cooldown cache
# ---------------------------------------------------------------------------


async def test_diagnostic_cache_skips_on_repeat_signature(tmp_store) -> None:
    """Second invocation within cooldown reuses cached evidence and
    emits a ``diagnostic_skipped`` event — LLM not called a second time.
    """
    cfg = _make_cfg()
    loop = await _build_loop(
        tmp_store, cfg, system_model=ecommerce_schema(),
    )
    anomalies = [ecommerce_anomaly(severity="critical")]
    # Pre-seed the analyzer with mock hypothesis/evidence responses.
    loop.analyzer.generate_diagnostic_queries = AsyncMock(return_value=[])
    loop.analyzer.execute_diagnostics = AsyncMock(
        return_value=[DiagnosticEvidence(hypothesis="h", sql="s", row_count=0)]
    )

    bundle1 = EvidenceBundle()
    await loop._maybe_run_diagnostics(anomalies, bundle1)
    assert len(bundle1.diagnostics) == 1
    assert loop.analyzer.generate_diagnostic_queries.await_count == 1

    bundle2 = EvidenceBundle()
    await loop._maybe_run_diagnostics(anomalies, bundle2)
    assert len(bundle2.diagnostics) == 1
    # Second call hit cache; Call A was not invoked again.
    assert loop.analyzer.generate_diagnostic_queries.await_count == 1


async def test_diagnostic_cache_runs_again_after_cooldown(tmp_store) -> None:
    """When the cached entry ages past the cooldown window, the next
    invocation invokes Call A again."""
    cfg = _make_cfg(cooldown_minutes=10)
    loop = await _build_loop(
        tmp_store, cfg, system_model=medical_records_schema(),
    )
    anomalies = [medical_anomaly()]
    loop.analyzer.generate_diagnostic_queries = AsyncMock(return_value=[])
    loop.analyzer.execute_diagnostics = AsyncMock(return_value=[])

    await loop._maybe_run_diagnostics(anomalies, EvidenceBundle())
    assert loop.analyzer.generate_diagnostic_queries.await_count == 1

    # Manually age the cache entry past the cooldown.
    sig = next(iter(loop._diagnostic_cache))
    old_when = datetime.now(UTC) - timedelta(minutes=30)
    loop._diagnostic_cache[sig] = (old_when, loop._diagnostic_cache[sig][1])

    await loop._maybe_run_diagnostics(anomalies, EvidenceBundle())
    assert loop.analyzer.generate_diagnostic_queries.await_count == 2


async def test_diagnostic_cache_evicts_stale_entries(tmp_store) -> None:
    """Stale entries must be dropped whenever any diagnostic cycle runs."""
    cfg = _make_cfg(cooldown_minutes=10)
    loop = await _build_loop(
        tmp_store, cfg, system_model=ecommerce_schema(),
    )
    loop._diagnostic_cache["old-sig"] = (
        datetime.now(UTC) - timedelta(hours=2), [],
    )
    loop.analyzer.generate_diagnostic_queries = AsyncMock(return_value=[])
    loop.analyzer.execute_diagnostics = AsyncMock(return_value=[])

    await loop._maybe_run_diagnostics(
        [ecommerce_anomaly(severity="critical")], EvidenceBundle(),
    )
    assert "old-sig" not in loop._diagnostic_cache


async def test_diagnostic_cache_emits_skip_event(tmp_store) -> None:
    """Cached path emits a ``diagnostic_skipped`` event."""
    cfg = _make_cfg()
    loop = await _build_loop(
        tmp_store, cfg, system_model=medical_records_schema(),
    )
    anomalies = [medical_anomaly()]
    loop.analyzer.generate_diagnostic_queries = AsyncMock(return_value=[])
    loop.analyzer.execute_diagnostics = AsyncMock(return_value=[])

    await loop._maybe_run_diagnostics(anomalies, EvidenceBundle())
    await loop._maybe_run_diagnostics(anomalies, EvidenceBundle())

    events = await tmp_store.get_events(event_type="diagnostic_skipped")
    assert events, "expected a diagnostic_skipped event"


# ---------------------------------------------------------------------------
# D6 — monitor wiring + timeouts
# ---------------------------------------------------------------------------


async def test_monitor_skips_diagnostics_when_disabled(tmp_store) -> None:
    cfg = _make_cfg(enabled=False)
    loop = await _build_loop(
        tmp_store, cfg, system_model=ecommerce_schema(),
    )
    spy = AsyncMock(return_value=[])
    loop.analyzer.generate_diagnostic_queries = spy
    await loop._maybe_run_diagnostics(
        [ecommerce_anomaly(severity="critical")], EvidenceBundle(),
    )
    spy.assert_not_called()


async def test_monitor_skips_when_app_db_missing(tmp_store) -> None:
    cfg = _make_cfg()
    loop = await _build_loop(
        tmp_store, cfg, system_model=medical_records_schema(),
        attach_app_db=False,
    )
    spy = AsyncMock(return_value=[])
    loop.analyzer.generate_diagnostic_queries = spy
    await loop._maybe_run_diagnostics(
        [medical_anomaly()], EvidenceBundle(),
    )
    spy.assert_not_called()


async def test_monitor_skips_cold_start_rolling_anomalies(tmp_store) -> None:
    """A warning-level rolling anomaly must not consume diagnostic
    budget. Only seasonal or critical-rolling advance to Call A.
    """
    cfg = _make_cfg()
    loop = await _build_loop(
        tmp_store, cfg, system_model=event_stream_schema(),
    )
    spy = AsyncMock(return_value=[])
    loop.analyzer.generate_diagnostic_queries = spy
    warn_rolling = event_stream_anomaly(
        severity="warning", direction="spike",
    )
    assert warn_rolling.baseline_source == "rolling"
    await loop._maybe_run_diagnostics([warn_rolling], EvidenceBundle())
    spy.assert_not_called()


async def test_monitor_diagnoses_critical_rolling_anomaly(tmp_store) -> None:
    cfg = _make_cfg()
    loop = await _build_loop(
        tmp_store, cfg, system_model=ecommerce_schema(),
    )
    spy_gen = AsyncMock(return_value=[])
    spy_exec = AsyncMock(return_value=[])
    loop.analyzer.generate_diagnostic_queries = spy_gen
    loop.analyzer.execute_diagnostics = spy_exec
    await loop._maybe_run_diagnostics(
        [ecommerce_anomaly(severity="critical")], EvidenceBundle(),
    )
    spy_gen.assert_awaited_once()
    spy_exec.assert_awaited_once()


async def test_monitor_diagnoses_seasonal_anomaly(tmp_store) -> None:
    cfg = _make_cfg()
    loop = await _build_loop(
        tmp_store, cfg, system_model=medical_records_schema(),
    )
    spy_gen = AsyncMock(return_value=[])
    spy_exec = AsyncMock(return_value=[])
    loop.analyzer.generate_diagnostic_queries = spy_gen
    loop.analyzer.execute_diagnostics = spy_exec
    warn_seasonal = medical_anomaly(severity="warning")
    assert warn_seasonal.baseline_source == "seasonal"
    await loop._maybe_run_diagnostics([warn_seasonal], EvidenceBundle())
    spy_gen.assert_awaited_once()


async def test_monitor_hypothesis_timeout_degrades_gracefully(tmp_store) -> None:
    cfg = _make_cfg(hypothesis_timeout_s=0.05)
    loop = await _build_loop(
        tmp_store, cfg, system_model=ecommerce_schema(),
    )

    async def slow_generate(*_a, **_kw):
        await asyncio.sleep(2.0)
        return []

    loop.analyzer.generate_diagnostic_queries = slow_generate
    loop.analyzer.execute_diagnostics = AsyncMock(return_value=[])
    bundle = EvidenceBundle()
    await loop._maybe_run_diagnostics(
        [ecommerce_anomaly(severity="critical")], bundle,
    )
    assert bundle.diagnostics == []
    events = await tmp_store.get_events(event_type="diagnostic_timeout")
    assert events, "expected diagnostic_timeout event"


async def test_monitor_execution_timeout_degrades_gracefully(tmp_store) -> None:
    cfg = _make_cfg(execution_timeout_s=0.05)
    loop = await _build_loop(
        tmp_store, cfg, system_model=ecommerce_schema(),
    )
    loop.analyzer.generate_diagnostic_queries = AsyncMock(return_value=[])

    async def slow_exec(*_a, **_kw):
        await asyncio.sleep(2.0)
        return []

    loop.analyzer.execute_diagnostics = slow_exec
    bundle = EvidenceBundle()
    await loop._maybe_run_diagnostics(
        [ecommerce_anomaly(severity="critical")], bundle,
    )
    assert bundle.diagnostics == []
    events = await tmp_store.get_events(event_type="diagnostic_timeout")
    assert events, "expected diagnostic_timeout event"


async def test_monitor_cache_hit_populates_evidence(tmp_store) -> None:
    """Second cycle with the same signature populates the bundle from
    the cache without calling the LLM."""
    cfg = _make_cfg()
    loop = await _build_loop(
        tmp_store, cfg, system_model=ecommerce_schema(),
    )
    expected = [
        DiagnosticEvidence(
            hypothesis="first-run hypothesis",
            sql="SELECT 1 FROM orders LIMIT 1",
            row_count=1,
            rows=[{"v": 1}],
        )
    ]
    loop.analyzer.generate_diagnostic_queries = AsyncMock(return_value=[])
    loop.analyzer.execute_diagnostics = AsyncMock(return_value=expected)

    bundle1 = EvidenceBundle()
    await loop._maybe_run_diagnostics(
        [ecommerce_anomaly(severity="critical")], bundle1,
    )
    assert [e.hypothesis for e in bundle1.diagnostics] == [
        "first-run hypothesis"
    ]

    bundle2 = EvidenceBundle()
    await loop._maybe_run_diagnostics(
        [ecommerce_anomaly(severity="critical")], bundle2,
    )
    assert [e.hypothesis for e in bundle2.diagnostics] == [
        "first-run hypothesis"
    ]
    assert loop.analyzer.generate_diagnostic_queries.await_count == 1


async def test_monitor_skips_when_circuit_breaker_open(tmp_store) -> None:
    """If the circuit breaker is already open, diagnostics should not
    consume an LLM call."""
    cfg = _make_cfg()
    loop = await _build_loop(
        tmp_store, cfg, system_model=ecommerce_schema(),
    )
    loop.circuit_breaker.record_hard_failure()  # open immediately
    spy = AsyncMock(return_value=[])
    loop.analyzer.generate_diagnostic_queries = spy
    await loop._maybe_run_diagnostics(
        [ecommerce_anomaly(severity="critical")], EvidenceBundle(),
    )
    spy.assert_not_called()
