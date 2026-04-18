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

from observibot.agent.analyzer import Analyzer, DiagnosticGenerationResult
from observibot.agent.llm_provider import LLMHardError, LLMSoftError, MockProvider
from observibot.alerting.base import AlertManager
from observibot.core.anomaly import compute_anomaly_signature
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
    invocation invokes Call A again. Stage 5: cache is store-backed,
    so we age the row by UPDATEing its ``cached_at`` directly.
    """
    import sqlalchemy as sa

    from observibot.core.store import diagnostic_cooldown

    cfg = _make_cfg(cooldown_minutes=10)
    loop = await _build_loop(
        tmp_store, cfg, system_model=medical_records_schema(),
    )
    anomalies = [medical_anomaly()]
    loop.analyzer.generate_diagnostic_queries = AsyncMock(return_value=[])
    loop.analyzer.execute_diagnostics = AsyncMock(return_value=[])

    await loop._maybe_run_diagnostics(anomalies, EvidenceBundle())
    assert loop.analyzer.generate_diagnostic_queries.await_count == 1

    # Age the row's cached_at so the next get_diagnostic_cooldown_entry
    # call returns None (stale entry).
    old_when = (datetime.now(UTC) - timedelta(minutes=30)).isoformat()
    async with tmp_store.engine.begin() as conn:
        await conn.execute(
            diagnostic_cooldown.update().values(cached_at=old_when)
        )

    await loop._maybe_run_diagnostics(anomalies, EvidenceBundle())
    assert loop.analyzer.generate_diagnostic_queries.await_count == 2


async def test_diagnostic_cache_evicts_stale_entries(tmp_store) -> None:
    """Stale entries are dropped whenever any diagnostic cycle runs.
    Stage 5: inline eviction is now a store-backed call
    (``evict_diagnostic_cooldown``); we seed an old row directly and
    confirm it's gone after a cycle completes.
    """
    import sqlalchemy as sa

    from observibot.core.store import diagnostic_cooldown

    cfg = _make_cfg(cooldown_minutes=10)
    loop = await _build_loop(
        tmp_store, cfg, system_model=ecommerce_schema(),
    )
    old_when = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
    async with tmp_store.engine.begin() as conn:
        await conn.execute(
            diagnostic_cooldown.insert().values(
                anomaly_signature="old-sig",
                cached_at=old_when,
                evidence_json="[]",
            )
        )
    loop.analyzer.generate_diagnostic_queries = AsyncMock(return_value=[])
    loop.analyzer.execute_diagnostics = AsyncMock(return_value=[])

    await loop._maybe_run_diagnostics(
        [ecommerce_anomaly(severity="critical")], EvidenceBundle(),
    )
    async with tmp_store.engine.begin() as conn:
        result = await conn.execute(
            sa.select(diagnostic_cooldown.c.anomaly_signature).where(
                diagnostic_cooldown.c.anomaly_signature == "old-sig"
            )
        )
        assert result.fetchone() is None


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


async def test_monitor_cooldown_survives_restart(tmp_store) -> None:
    """Stage 5 O9 — cooldown state survives process restart.

    Build a MonitorLoop, run one diagnostic cycle so the store is
    seeded, construct a *fresh* MonitorLoop against the same store
    (simulating a process restart — no shared in-memory state), and
    confirm the next cycle hits the cooldown cache without generating
    a second set of diagnostic queries.
    """
    cfg = _make_cfg()
    anomalies = [ecommerce_anomaly(severity="critical")]

    # --- First loop: seed the cooldown ---
    loop1 = await _build_loop(
        tmp_store, cfg, system_model=ecommerce_schema(),
    )
    loop1.analyzer.generate_diagnostic_queries = AsyncMock(return_value=[])
    loop1.analyzer.execute_diagnostics = AsyncMock(
        return_value=[
            DiagnosticEvidence(
                hypothesis="persisted hypothesis",
                sql="SELECT 1 FROM orders LIMIT 1",
                row_count=1,
                rows=[{"v": 1}],
            )
        ]
    )
    bundle1 = EvidenceBundle()
    await loop1._maybe_run_diagnostics(anomalies, bundle1)
    assert [e.hypothesis for e in bundle1.diagnostics] == [
        "persisted hypothesis"
    ]

    # --- Second loop: fresh instance, same store ---
    loop2 = await _build_loop(
        tmp_store, cfg, system_model=ecommerce_schema(),
    )
    gen_spy = AsyncMock(return_value=[])
    loop2.analyzer.generate_diagnostic_queries = gen_spy
    loop2.analyzer.execute_diagnostics = AsyncMock(return_value=[])

    bundle2 = EvidenceBundle()
    await loop2._maybe_run_diagnostics(anomalies, bundle2)

    # Cached evidence was reloaded from the store — Call A never fired.
    gen_spy.assert_not_called()
    assert [e.hypothesis for e in bundle2.diagnostics] == [
        "persisted hypothesis"
    ]
    # And the skip event was emitted on the second loop.
    events = await tmp_store.get_events(event_type="diagnostic_skipped")
    assert events


async def test_store_diagnostic_cooldown_roundtrip(tmp_store) -> None:
    """Stage 5: set + get returns the same DiagnosticEvidence list."""
    evidence = [
        DiagnosticEvidence(
            hypothesis="h1",
            sql="SELECT 1",
            row_count=3,
            rows=[{"a": 1}],
            explanation="why",
        ),
        DiagnosticEvidence(
            hypothesis="h2",
            sql="SELECT 2",
            row_count=0,
            error="sandbox rejected: something",
        ),
    ]
    await tmp_store.set_diagnostic_cooldown_entry("sig-abc", evidence)
    got = await tmp_store.get_diagnostic_cooldown_entry(
        "sig-abc", within_seconds=600,
    )
    assert got is not None
    assert [e.hypothesis for e in got] == ["h1", "h2"]
    assert got[1].error == "sandbox rejected: something"
    assert got[0].rows == [{"a": 1}]


async def test_store_diagnostic_cooldown_stale_returns_none(tmp_store) -> None:
    """Stage 5: an entry older than ``within_seconds`` reads as absent."""
    import sqlalchemy as sa

    from observibot.core.store import diagnostic_cooldown

    evidence = [
        DiagnosticEvidence(
            hypothesis="old one", sql="SELECT 1", row_count=0,
        )
    ]
    await tmp_store.set_diagnostic_cooldown_entry("sig-old", evidence)
    # Age the row so it's older than a 60s window.
    old_when = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
    async with tmp_store.engine.begin() as conn:
        await conn.execute(
            diagnostic_cooldown.update()
            .where(diagnostic_cooldown.c.anomaly_signature == "sig-old")
            .values(cached_at=old_when)
        )
    assert (
        await tmp_store.get_diagnostic_cooldown_entry(
            "sig-old", within_seconds=60,
        )
        is None
    )


async def test_store_diagnostic_cooldown_evict(tmp_store) -> None:
    """Stage 5: evict deletes rows older than the cutoff, preserves fresh."""
    import sqlalchemy as sa

    from observibot.core.store import diagnostic_cooldown

    fresh = [
        DiagnosticEvidence(
            hypothesis="fresh", sql="SELECT 1", row_count=0,
        )
    ]
    stale = [
        DiagnosticEvidence(
            hypothesis="stale", sql="SELECT 1", row_count=0,
        )
    ]
    await tmp_store.set_diagnostic_cooldown_entry("sig-fresh", fresh)
    await tmp_store.set_diagnostic_cooldown_entry("sig-stale", stale)

    old_when = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
    async with tmp_store.engine.begin() as conn:
        await conn.execute(
            diagnostic_cooldown.update()
            .where(diagnostic_cooldown.c.anomaly_signature == "sig-stale")
            .values(cached_at=old_when)
        )

    # Evict anything older than 30 minutes.
    evicted = await tmp_store.evict_diagnostic_cooldown(
        older_than_seconds=30 * 60,
    )
    assert evicted == 1

    async with tmp_store.engine.begin() as conn:
        result = await conn.execute(
            sa.select(diagnostic_cooldown.c.anomaly_signature).order_by(
                diagnostic_cooldown.c.anomaly_signature
            )
        )
        remaining = [r[0] for r in result.fetchall()]
    assert remaining == ["sig-fresh"]


async def test_store_diagnostic_cooldown_upsert_overwrites(tmp_store) -> None:
    """Stage 5: ``set_diagnostic_cooldown_entry`` overwrites prior rows."""
    v1 = [DiagnosticEvidence(hypothesis="v1", sql="SELECT 1", row_count=0)]
    v2 = [
        DiagnosticEvidence(hypothesis="v2-a", sql="SELECT 2", row_count=1),
        DiagnosticEvidence(hypothesis="v2-b", sql="SELECT 3", row_count=2),
    ]
    await tmp_store.set_diagnostic_cooldown_entry("sig-up", v1)
    await tmp_store.set_diagnostic_cooldown_entry("sig-up", v2)
    got = await tmp_store.get_diagnostic_cooldown_entry(
        "sig-up", within_seconds=600,
    )
    assert got is not None
    assert [e.hypothesis for e in got] == ["v2-a", "v2-b"]


async def test_apply_retention_evicts_diagnostic_cooldown(tmp_store) -> None:
    """Stage 5: ``apply_retention`` also trims old cooldown rows using
    the same window as events."""
    import sqlalchemy as sa

    from observibot.core.store import diagnostic_cooldown

    evidence = [
        DiagnosticEvidence(
            hypothesis="ancient", sql="SELECT 1", row_count=0,
        )
    ]
    await tmp_store.set_diagnostic_cooldown_entry("sig-retain", evidence)
    old = (datetime.now(UTC) - timedelta(days=120)).isoformat()
    async with tmp_store.engine.begin() as conn:
        await conn.execute(
            diagnostic_cooldown.update().values(cached_at=old)
        )

    result = await tmp_store.apply_retention(
        metrics_days=30,
        events_days=90,
        insights_days=90,
        max_snapshots=10,
    )
    assert result["diagnostic_cooldown"] >= 1
    async with tmp_store.engine.begin() as conn:
        row = (
            await conn.execute(
                sa.select(diagnostic_cooldown.c.anomaly_signature)
            )
        ).fetchone()
    assert row is None


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


# ---------------------------------------------------------------------------
# Hotfix item 1 — diagnostic-generation failures surface as EvidenceError,
# don't poison cooldown, and emit a distinguishable event
# ---------------------------------------------------------------------------


async def test_diagnostic_generation_hard_error_surfaces_as_evidence_error(
    tmp_store,
) -> None:
    """LLMHardError after successful fact retrieval must produce exactly
    one EvidenceError(stage="diagnostic_generation") on the bundle.
    Fact-retrieval succeeded, so no fact_retrieval entry should appear.
    """
    cfg = _make_cfg()
    loop = await _build_loop(
        tmp_store, cfg, system_model=ecommerce_schema(),
    )
    loop.analyzer.generate_diagnostic_queries = AsyncMock(
        return_value=DiagnosticGenerationResult(
            queries=[],
            facts=[],
            freshness="current",
            error_reason=None,
            generation_error="LLM hard failure: 401 unauthorized",
        )
    )
    loop.analyzer.execute_diagnostics = AsyncMock(return_value=[])

    bundle = EvidenceBundle()
    await loop._maybe_run_diagnostics(
        [ecommerce_anomaly(severity="critical")], bundle,
    )

    stages = [e.stage for e in bundle.errors]
    assert stages == ["diagnostic_generation"]
    assert "401 unauthorized" in bundle.errors[0].reason
    # Generation failed, so execute_diagnostics must NOT have been called.
    loop.analyzer.execute_diagnostics.assert_not_called()


async def test_diagnostic_generation_hard_error_does_not_poison_cooldown(
    tmp_store,
) -> None:
    """Generation failure MUST NOT write an empty evidence_list into the
    cooldown cache — that would suppress diagnostics on the same
    anomaly signature for the full cooldown window, hiding the anomaly.
    """
    cfg = _make_cfg(cooldown_minutes=10)
    loop = await _build_loop(
        tmp_store, cfg, system_model=ecommerce_schema(),
    )
    anomalies = [ecommerce_anomaly(severity="critical")]
    loop.analyzer.generate_diagnostic_queries = AsyncMock(
        return_value=DiagnosticGenerationResult(
            queries=[],
            facts=[],
            freshness="current",
            error_reason=None,
            generation_error="LLM hard failure: quota exceeded",
        )
    )
    loop.analyzer.execute_diagnostics = AsyncMock(return_value=[])

    await loop._maybe_run_diagnostics(anomalies, EvidenceBundle())

    # Cooldown table must not carry a row for this signature — the next
    # cycle should be free to run diagnostics again once the LLM
    # recovers, instead of serving up the empty cached result.
    sig = compute_anomaly_signature(anomalies)
    cached = await tmp_store.get_diagnostic_cooldown_entry(
        sig, within_seconds=int(cfg.monitor.diagnostics.cooldown_minutes * 60),
    )
    assert cached is None


async def test_diagnostic_generation_soft_error_surfaces_as_evidence_error(
    tmp_store,
) -> None:
    """LLMError (soft) after successful fact retrieval must also surface
    as an EvidenceError(stage="diagnostic_generation")."""
    cfg = _make_cfg()
    loop = await _build_loop(
        tmp_store, cfg, system_model=ecommerce_schema(),
    )
    loop.analyzer.generate_diagnostic_queries = AsyncMock(
        return_value=DiagnosticGenerationResult(
            queries=[],
            facts=[],
            freshness="current",
            error_reason=None,
            generation_error="LLM soft failure: 429 rate limit",
        )
    )
    loop.analyzer.execute_diagnostics = AsyncMock(return_value=[])

    bundle = EvidenceBundle()
    await loop._maybe_run_diagnostics(
        [ecommerce_anomaly(severity="critical")], bundle,
    )

    stages = [e.stage for e in bundle.errors]
    assert stages == ["diagnostic_generation"]
    assert "429" in bundle.errors[0].reason


async def test_diagnostic_validation_error_surfaces_as_evidence_error(
    tmp_store,
) -> None:
    """A Pydantic validation failure after the LLM call must also
    surface as an EvidenceError(stage="diagnostic_generation") — it's
    the same class of post-fact-retrieval failure the operator needs
    to see.
    """
    cfg = _make_cfg()
    loop = await _build_loop(
        tmp_store, cfg, system_model=ecommerce_schema(),
    )
    loop.analyzer.generate_diagnostic_queries = AsyncMock(
        return_value=DiagnosticGenerationResult(
            queries=[],
            facts=[],
            freshness="current",
            error_reason=None,
            generation_error=(
                "LLM response failed schema validation: 1 validation error"
            ),
        )
    )
    loop.analyzer.execute_diagnostics = AsyncMock(return_value=[])

    bundle = EvidenceBundle()
    await loop._maybe_run_diagnostics(
        [ecommerce_anomaly(severity="critical")], bundle,
    )

    stages = [e.stage for e in bundle.errors]
    assert stages == ["diagnostic_generation"]
    assert "schema validation" in bundle.errors[0].reason


async def test_diagnostic_run_event_distinguishes_success_from_generation_failure(
    tmp_store,
) -> None:
    """The ``diagnostic_run`` event summary must be textually
    distinguishable between the "ran N diagnostics, all failed" case
    (which we accept as signal) and the "couldn't generate queries at
    all" case (which indicates the LLM is down and operators should
    see that instead of silence).
    """
    # --- Run 1: successful generation, 2 queries executed ---
    cfg = _make_cfg()
    loop1 = await _build_loop(
        tmp_store, cfg, system_model=ecommerce_schema(),
    )
    loop1.analyzer.generate_diagnostic_queries = AsyncMock(
        return_value=DiagnosticGenerationResult(
            queries=[],
            facts=[],
            freshness="current",
            error_reason=None,
            generation_error=None,
        )
    )
    loop1.analyzer.execute_diagnostics = AsyncMock(
        return_value=[
            DiagnosticEvidence(
                hypothesis="h1", sql="SELECT 1 LIMIT 1", row_count=1,
                rows=[{"n": 1}],
            ),
            DiagnosticEvidence(
                hypothesis="h2", sql="SELECT 2 LIMIT 1", row_count=0,
            ),
        ]
    )
    await loop1._maybe_run_diagnostics(
        [ecommerce_anomaly(severity="critical", metric="m.success")],
        EvidenceBundle(),
    )

    # --- Run 2: generation failed (distinct anomaly signature so we
    # don't hit Run 1's cooldown cache). ---
    loop2 = await _build_loop(
        tmp_store, cfg, system_model=ecommerce_schema(),
    )
    loop2.analyzer.generate_diagnostic_queries = AsyncMock(
        return_value=DiagnosticGenerationResult(
            queries=[],
            facts=[],
            freshness="current",
            error_reason=None,
            generation_error="LLM hard failure: 401 unauthorized",
        )
    )
    loop2.analyzer.execute_diagnostics = AsyncMock(return_value=[])
    await loop2._maybe_run_diagnostics(
        [ecommerce_anomaly(severity="critical", metric="m.failed")],
        EvidenceBundle(),
    )

    events = await tmp_store.get_events(event_type="diagnostic_run")
    summaries = [e["summary"] for e in events]
    # Each run emitted exactly one diagnostic_run event.
    assert len(summaries) == 2
    success = [s for s in summaries if "succeeded" in s]
    failure = [s for s in summaries if "generation failed" in s]
    assert success, f"expected a success-case summary in {summaries}"
    assert failure, f"expected a failure-case summary in {summaries}"
    # And the two summaries must not be textually identical.
    assert success[0] != failure[0]


async def test_fact_retrieval_error_plus_generation_error_produces_two_evidence_errors(
    tmp_store,
) -> None:
    """When both failure modes fire in the same cycle — the fact
    index was degraded AND the LLM call then failed — the operator
    must see both as distinct degraded-state notes. Collapsing them
    into one entry hides the fault boundary.
    """
    cfg = _make_cfg()
    loop = await _build_loop(
        tmp_store, cfg, system_model=ecommerce_schema(),
    )
    loop.analyzer.generate_diagnostic_queries = AsyncMock(
        return_value=DiagnosticGenerationResult(
            queries=[],
            facts=[],
            freshness="error",
            error_reason="code index error: connection refused",
            generation_error="LLM hard failure: 401 unauthorized",
        )
    )
    loop.analyzer.execute_diagnostics = AsyncMock(return_value=[])

    bundle = EvidenceBundle()
    await loop._maybe_run_diagnostics(
        [ecommerce_anomaly(severity="critical")], bundle,
    )

    stages = sorted(e.stage for e in bundle.errors)
    assert stages == ["diagnostic_generation", "fact_retrieval"]
    # Reasons carry the respective root causes so operators can tell
    # which subsystem actually broke.
    by_stage = {e.stage: e.reason for e in bundle.errors}
    assert "connection refused" in by_stage["fact_retrieval"]
    assert "401" in by_stage["diagnostic_generation"]


# ---------------------------------------------------------------------------
# Hotfix item 2 — events that claim ref_table="monitor_runs" must point at
# real monitor_runs rows, not phantom UUIDs
# ---------------------------------------------------------------------------


async def _monitor_run_ids(tmp_store) -> set[str]:
    """Read every row from ``monitor_runs`` — used to prove that an
    event's ref_id maps to a real row, not a phantom."""
    import sqlalchemy as sa

    from observibot.core.store import monitor_runs

    async with tmp_store.engine.begin() as conn:
        result = await conn.execute(sa.select(monitor_runs.c.id))
        return {r[0] for r in result.fetchall()}


async def test_correlation_run_event_ref_id_matches_monitor_run(
    tmp_store,
) -> None:
    """A full collection cycle that triggers analysis must emit a
    ``correlation_run`` event whose ref_id is a real monitor_runs row
    — not a fresh UUID that no other table ever saw.
    """
    from observibot.connectors.base import BaseConnector, ConnectorCapabilities
    from observibot.core.models import MetricSnapshot

    cfg = _make_cfg()
    loop = await _build_loop(
        tmp_store, cfg, system_model=ecommerce_schema(),
    )

    # Force the collection cycle to call run_analysis_cycle by
    # handing it a pre-populated anomaly via the pending queue and
    # mocking the analyzer to a no-op. We invoke
    # run_analysis_cycle directly with a parent_run_id that DOES
    # have a monitor_runs row — the same shape collection takes.
    run_id = "real-run-id1"
    await tmp_store.create_monitor_run(run_id, datetime.now(UTC))

    loop._pending_anomalies = [ecommerce_anomaly(severity="critical")]
    loop.analyzer.analyze_anomalies = AsyncMock(return_value=[])
    loop.analyzer.generate_diagnostic_queries = AsyncMock(return_value=[])
    loop.analyzer.execute_diagnostics = AsyncMock(return_value=[])

    await loop.run_analysis_cycle(parent_run_id=run_id)

    run_ids = await _monitor_run_ids(tmp_store)
    events = await tmp_store.get_events(event_type="correlation_run")
    assert events, "expected a correlation_run event"
    for evt in events:
        assert evt["ref_table"] == "monitor_runs"
        assert evt["ref_id"] in run_ids, (
            f"phantom ref_id {evt['ref_id']} on correlation_run event — "
            f"no matching monitor_runs row"
        )


async def test_diagnostic_run_event_ref_id_matches_monitor_run(
    tmp_store,
) -> None:
    """Same property for ``diagnostic_run``: ref_id must name a real
    monitor_runs row. Covers both the success-summary branch and the
    generation-failure-summary branch (item 1 also emits
    diagnostic_run)."""
    cfg = _make_cfg()
    loop = await _build_loop(
        tmp_store, cfg, system_model=ecommerce_schema(),
    )
    run_id = "real-run-id2"
    await tmp_store.create_monitor_run(run_id, datetime.now(UTC))

    loop.analyzer.generate_diagnostic_queries = AsyncMock(
        return_value=DiagnosticGenerationResult(
            queries=[],
            facts=[],
            freshness="current",
            error_reason=None,
            generation_error=None,
        )
    )
    loop.analyzer.execute_diagnostics = AsyncMock(
        return_value=[
            DiagnosticEvidence(
                hypothesis="h1", sql="SELECT 1 LIMIT 1", row_count=1,
            )
        ]
    )

    await loop._maybe_run_diagnostics(
        [ecommerce_anomaly(severity="critical")],
        EvidenceBundle(),
        run_id=run_id,
    )

    run_ids = await _monitor_run_ids(tmp_store)
    events = await tmp_store.get_events(event_type="diagnostic_run")
    assert events
    for evt in events:
        assert evt["ref_table"] == "monitor_runs"
        assert evt["ref_id"] in run_ids, (
            f"phantom ref_id {evt['ref_id']} on diagnostic_run event"
        )


async def test_trigger_analysis_direct_call_creates_monitor_run_row(
    tmp_store,
) -> None:
    """``trigger_analysis`` is a direct-caller path with no parent
    collection cycle. It still emits events claiming
    ``ref_table="monitor_runs"`` — so it must create its own
    ``monitor_runs`` row rather than mint a phantom UUID.
    """
    cfg = _make_cfg()
    loop = await _build_loop(
        tmp_store, cfg, system_model=ecommerce_schema(),
    )
    loop.analyzer.analyze_anomalies = AsyncMock(return_value=[])
    loop.analyzer.generate_diagnostic_queries = AsyncMock(
        return_value=DiagnosticGenerationResult(
            queries=[], facts=[], freshness="current",
            error_reason=None, generation_error=None,
        )
    )
    loop.analyzer.execute_diagnostics = AsyncMock(return_value=[])

    run_ids_before = await _monitor_run_ids(tmp_store)
    await loop.trigger_analysis(
        [ecommerce_anomaly(severity="critical")],
    )
    run_ids_after = await _monitor_run_ids(tmp_store)

    # Exactly one new monitor_runs row was created by the standalone
    # path (the one trigger_analysis/run_analysis_cycle inserted).
    new_ids = run_ids_after - run_ids_before
    assert len(new_ids) == 1, (
        f"expected exactly one new monitor_runs row; got {new_ids}"
    )
    new_id = next(iter(new_ids))

    # Downstream events must reference THAT id, not a different UUID.
    for evt_type in ("correlation_run", "diagnostic_run"):
        events = await tmp_store.get_events(event_type=evt_type)
        assert events, f"expected {evt_type} event"
        for evt in events:
            assert evt["ref_id"] == new_id, (
                f"{evt_type} ref_id {evt['ref_id']} != standalone "
                f"run_id {new_id}"
            )
