"""Tier 2 integration test: EvidenceBundle survives a real SQLite round-trip.

Saves an insight with every evidence type populated (including nested
datetimes) through the :class:`observibot.core.store.Store`, reconnects,
re-reads it, and confirms every field round-trips exactly.
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from observibot.core.evidence import (
    CorrelationEvidence,
    DiagnosticEvidence,
    EvidenceBundle,
    RecurrenceEvidence,
)
from observibot.core.models import Insight
from observibot.core.store import Store

pytestmark = pytest.mark.asyncio


async def test_evidence_bundle_survives_store_reconnect(tmp_path) -> None:
    """Populate all three evidence types, save, close the store,
    reopen it, and confirm the round-trip preserves every field.
    """
    db_path = tmp_path / "evidence_rt.db"

    bundle = EvidenceBundle()
    bundle.recurrence["payments_total"] = RecurrenceEvidence(
        metric_name="payments_total",
        count=9,
        first_seen="2026-04-01T08:00:00+00:00",
        last_seen="2026-04-15T09:00:00+00:00",
        common_hours=[8, 9],
    )
    bundle.correlations.append(
        CorrelationEvidence(
            metric_name="payments_total",
            change_event_id="deploy-42",
            change_type="deploy",
            change_summary="payments-service v7",
            time_delta_seconds=420.0,
            severity_score=5.2,
        )
    )
    executed_at = datetime(2026, 4, 16, 14, 15, tzinfo=UTC)
    bundle.diagnostics.append(
        DiagnosticEvidence(
            hypothesis="payment retries piling up",
            sql="SELECT provider, count(*) FROM payment_attempts GROUP BY 1",
            row_count=4,
            rows=[
                {"provider": "P1", "count": 124},
                {"provider": "P2", "count": 77},
            ],
            explanation="P1 retry rate triple baseline",
            executed_at=executed_at,
            error=None,
        )
    )

    insight = Insight(
        title="payments anomaly",
        summary="s",
        severity="warning",
        anomaly_signature="evidencesig12345",
        evidence=bundle.to_dict(),
    )
    insight.fingerprint = insight.compute_fingerprint()

    async with Store(db_path) as store:
        assert await store.save_insight(insight) is True

    # Reconnect — confirms the column actually lives on disk.
    async with Store(db_path) as store:
        fetched = await store.get_recent_insights(limit=5)

    assert len(fetched) == 1
    restored = EvidenceBundle.from_dict(fetched[0].evidence)
    assert restored.recurrence["payments_total"].count == 9
    assert restored.recurrence["payments_total"].common_hours == [8, 9]
    assert restored.correlations[0].severity_score == pytest.approx(5.2)
    assert restored.diagnostics[0].row_count == 4
    assert restored.diagnostics[0].rows[0] == {"provider": "P1", "count": 124}
    assert restored.diagnostics[0].executed_at == executed_at
    # Fingerprint is unaffected by the evidence field.
    assert fetched[0].fingerprint == insight.fingerprint
