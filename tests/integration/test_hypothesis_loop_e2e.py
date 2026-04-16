"""Step 3.4 end-to-end integration tests for the hypothesis-test loop.

Exercises the full analysis cycle against synthetic schemas with a
mocked LLM provider and a stubbed AppDatabasePool. Verifies:

- anomaly → diagnostic queries → sandboxed execution → evidence on the
  persisted Insight (ecommerce synthetic domain)
- cooldown reuse of the first cycle's evidence across two cycles
  without re-calling the LLM (medical synthetic domain)
- sandbox-rejected queries surface as ``DiagnosticEvidence`` with
  populated ``error`` (event_stream synthetic domain)

These tests use SQLite via :class:`Store` (real) and mock the LLM
provider and application DB.
"""
from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from observibot.agent.analyzer import Analyzer
from observibot.agent.llm_provider import MockProvider
from observibot.alerting.base import AlertManager
from observibot.core.anomaly import Anomaly
from observibot.core.config import DiagnosticsConfig, MonitorConfig, ObservibotConfig
from observibot.core.evidence import EvidenceBundle
from observibot.core.monitor import build_monitor_loop
from observibot.core.store import Store
from tests.fixtures.synthetic_schemas import (
    ecommerce_anomaly,
    ecommerce_schema,
    event_stream_anomaly,
    event_stream_schema,
    medical_anomaly,
    medical_records_schema,
)

pytestmark = pytest.mark.asyncio


class _StubConn:
    """Pretend asyncpg connection that returns canned rows for
    recognized SQL patterns."""

    def __init__(self, row_map: dict[str, list[dict[str, Any]]]) -> None:
        self._row_map = row_map

    async def fetch(self, sql: str) -> list[Any]:
        for pattern, rows in self._row_map.items():
            if pattern.lower() in sql.lower():
                return list(rows)
        return [{"result": "no match"}]

    async def fetchrow(self, sql: str) -> Any:
        # Always return a tiny positive EXPLAIN cost so fail-closed lets
        # queries through.
        return (json.dumps([{"Plan": {"Total Cost": 25.0}}]),)


class _StubAppDb:
    def __init__(self, conn: _StubConn) -> None:
        self._conn = conn
        self.is_connected = True

    @asynccontextmanager
    async def acquire(self):
        yield self._conn


def _diag_cfg() -> DiagnosticsConfig:
    return DiagnosticsConfig(
        enabled=True,
        cooldown_minutes=10,
        hypothesis_timeout_s=5.0,
        execution_timeout_s=5.0,
    )


async def _make_loop(
    store: Store,
    *,
    provider: MockProvider,
    app_db: Any,
    system_model: Any,
):
    analyzer = Analyzer(provider=provider, store=store)
    cfg = ObservibotConfig()
    cfg.monitor = MonitorConfig(
        collection_interval_seconds=60,
        analysis_interval_seconds=120,
        discovery_interval_seconds=60,
        min_samples_for_baseline=3,
    )
    cfg.monitor.diagnostics = _diag_cfg()
    loop = build_monitor_loop(
        config=cfg, connectors=[], store=store,
        analyzer=analyzer, alert_manager=AlertManager(channels=[]),
    )
    loop._cached_model = system_model
    loop._app_db = app_db
    return loop


async def test_e2e_anomaly_to_evidence_backed_insight_ecommerce(
    tmp_path: Path,
) -> None:
    """Full cycle: critical ecommerce anomaly → LLM hypotheses → sandboxed
    queries → persisted Insight carries evidence.diagnostics with real
    rows. Validates that evidence survives save_insight.
    """
    db_path = tmp_path / "e2e_ecom.db"

    diag_canned = {
        "queries": [
            {
                "hypothesis": "Pending orders piling up at checkout step",
                "sql": (
                    "SELECT order_status AS status, count(*) AS n "
                    "FROM orders GROUP BY order_status LIMIT 20"
                ),
                "explanation": "Large count on 'pending' confirms the spike.",
            }
        ]
    }
    provider = _PhasedMockProvider(
        diag_response=diag_canned,
    )
    app_db = _StubAppDb(
        _StubConn({
            "from orders": [
                {"status": "pending", "n": 2_400},
                {"status": "paid", "n": 3_100},
            ],
        }),
    )

    async with Store(db_path) as store:
        loop = await _make_loop(
            store, provider=provider, app_db=app_db,
            system_model=ecommerce_schema(),
        )
        crit = ecommerce_anomaly(severity="critical")
        loop._pending_anomalies = [crit]
        insights = await loop.run_analysis_cycle()

    assert insights, "expected at least one insight"
    evidence = insights[0].evidence
    assert evidence and evidence.get("diagnostics")
    diags = evidence["diagnostics"]
    assert len(diags) == 1
    assert diags[0]["row_count"] == 2
    assert diags[0]["error"] in (None,)
    assert "orders" in diags[0]["sql"].lower()

    async with Store(db_path) as store:
        events = await store.get_events(event_type="diagnostic_run")
    assert events, "expected diagnostic_run event to be persisted"


async def test_e2e_cooldown_reuses_evidence_medical(tmp_path: Path) -> None:
    """Two back-to-back analysis cycles with identical anomaly signature
    share one LLM invocation; the second cycle reads evidence from the
    cooldown cache and emits a ``diagnostic_skipped`` event.
    """
    db_path = tmp_path / "e2e_medical.db"
    diag_canned = {
        "queries": [
            {
                "hypothesis": "encounter drop from RLS policy",
                "sql": (
                    "SELECT count(*) AS n FROM encounters "
                    "WHERE scheduled_at > now() - INTERVAL '1 hour' LIMIT 5"
                ),
                "explanation": "0 = RLS; many = scheduling",
            }
        ]
    }
    provider = _PhasedMockProvider(diag_response=diag_canned)
    app_db = _StubAppDb(
        _StubConn({"from encounters": [{"n": 14}]}),
    )

    async with Store(db_path) as store:
        loop = await _make_loop(
            store, provider=provider, app_db=app_db,
            system_model=medical_records_schema(),
        )
        # Use exactly the same anomaly (same signature) twice.
        first = medical_anomaly()
        second = Anomaly(
            metric_name=first.metric_name,
            connector_name=first.connector_name,
            labels=dict(first.labels),
            value=first.value,
            median=first.median,
            mad=first.mad,
            modified_z=first.modified_z,
            absolute_diff=first.absolute_diff,
            severity=first.severity,
            direction=first.direction,
            consecutive_count=first.consecutive_count,
            detected_at=datetime.now(UTC),
            sample_count=first.sample_count,
            baseline_source=first.baseline_source,
        )
        loop._pending_anomalies = [first]
        first_insights = await loop.run_analysis_cycle()
        loop._pending_anomalies = [second]
        second_insights = await loop.run_analysis_cycle()

    # The first cycle saves an insight; the second is deduped by
    # fingerprint (same anomaly_signature → same fingerprint) so
    # ``second_insights`` may be empty. What must hold is that Call A
    # was invoked exactly once — the cooldown cache blocked the repeat.
    assert first_insights
    assert provider.diag_call_count == 1
    del second_insights  # intentionally unused beyond the cycle call

    async with Store(db_path) as store:
        skipped = await store.get_events(event_type="diagnostic_skipped")
    assert skipped, "expected diagnostic_skipped on cooldown reuse"


async def test_e2e_sandbox_rejection_surfaces_error_evidence_event_stream(
    tmp_path: Path,
) -> None:
    """Sandbox rejection (cross-schema probe) must persist as error-bearing
    evidence on the Insight so the operator can see what the LLM tried.
    Uses the event_stream synthetic schema.
    """
    db_path = tmp_path / "e2e_events.db"
    diag_canned = {
        "queries": [
            {
                "hypothesis": "probe pg_catalog for partition state",
                "sql": "SELECT relname FROM pg_catalog.pg_class LIMIT 10",
                "explanation": "Would reveal partition bloat",
            }
        ]
    }
    provider = _PhasedMockProvider(diag_response=diag_canned)
    app_db = _StubAppDb(_StubConn({}))

    async with Store(db_path) as store:
        loop = await _make_loop(
            store, provider=provider, app_db=app_db,
            system_model=event_stream_schema(),
        )
        crit = event_stream_anomaly(severity="critical")
        loop._pending_anomalies = [crit]
        insights = await loop.run_analysis_cycle()

    assert insights
    diags = insights[0].evidence["diagnostics"]
    assert len(diags) == 1
    assert diags[0]["error"] and diags[0]["error"].startswith("sandbox rejected")
    assert diags[0]["row_count"] == 0


# ---------------------------------------------------------------------------
# Multi-phase mock provider
# ---------------------------------------------------------------------------


class _PhasedMockProvider(MockProvider):
    """MockProvider that returns different canned payloads for the
    diagnostic-hypothesis call vs. the anomaly-analysis call.

    The diagnostic prompt contains the marker "You are Observibot's
    diagnostic generator"; the anomaly-analysis prompt contains
    "Detected anomalies:" only. This lets a single provider instance
    serve both LLM calls in the analysis cycle.
    """

    def __init__(self, diag_response: dict[str, Any]) -> None:
        super().__init__(model="mock-phased")
        self._diag_response = diag_response
        self.diag_call_count = 0

    async def _call(self, system_prompt: str, user_prompt: str):
        if "diagnostic generator" in user_prompt.lower():
            self.diag_call_count += 1
            text = json.dumps(self._diag_response)
            prompt_tokens = max(1, (len(system_prompt) + len(user_prompt)) // 4)
            return text, prompt_tokens, max(1, len(text) // 4)
        return await super()._call(system_prompt, user_prompt)


# Silence unused-import warnings for helpers referenced via AsyncMock in
# future test extensions.
_ = AsyncMock
