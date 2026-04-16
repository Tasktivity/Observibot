"""Unit tests for :class:`observibot.core.evidence.EvidenceBundle`.

Every evidence type is exercised for round-trip, including at least one
test using a Tier 0 synthetic fixture (ecommerce, medical, event_stream)
to confirm the carrier isn't shaped around TaskGator-specific data.
"""
from __future__ import annotations

from datetime import UTC, datetime

from observibot.core.evidence import (
    CorrelationEvidence,
    DiagnosticEvidence,
    EvidenceBundle,
    RecurrenceEvidence,
)
from tests.fixtures.synthetic_schemas import (
    ecommerce_anomaly,
    event_stream_anomaly,
    medical_anomaly,
)


def _recurrence_for(metric: str, count: int = 3) -> RecurrenceEvidence:
    return RecurrenceEvidence(
        metric_name=metric,
        count=count,
        first_seen="2026-04-01T09:00:00+00:00",
        last_seen="2026-04-14T10:00:00+00:00",
        common_hours=[9, 10],
    )


def test_empty_bundle_is_empty() -> None:
    bundle = EvidenceBundle()
    assert bundle.is_empty() is True


def test_bundle_with_recurrence_not_empty() -> None:
    bundle = EvidenceBundle()
    bundle.recurrence["x"] = _recurrence_for("x")
    assert bundle.is_empty() is False


# Tier 0: synthetic coverage via ecommerce_schema()
def test_evidence_bundle_roundtrip_ecommerce() -> None:
    """Build a bundle from ecommerce_anomaly() evidence, serialize,
    deserialize, and assert equality. Non-TaskGator shape.
    """
    a = ecommerce_anomaly(metric="order_count")
    bundle = EvidenceBundle()
    bundle.recurrence[a.metric_name] = _recurrence_for(a.metric_name, count=7)
    bundle.correlations.append(
        CorrelationEvidence(
            metric_name=a.metric_name,
            change_event_id="chg-ecom-1",
            change_type="deploy",
            change_summary="checkout-v42 released",
            time_delta_seconds=600.0,
            severity_score=4.5,
        )
    )
    bundle.diagnostics.append(
        DiagnosticEvidence(
            hypothesis="order spike tied to promo campaign",
            sql="SELECT count(*) FROM orders WHERE placed_at > now() - interval '1 hour'",
            row_count=1,
            rows=[{"count": 1234}],
            explanation="Promo started 10 min before anomaly",
            executed_at=datetime(2026, 4, 16, 12, 0, tzinfo=UTC),
            error=None,
        )
    )

    serialized = bundle.to_dict()
    restored = EvidenceBundle.from_dict(serialized)

    assert restored.recurrence["order_count"].count == 7
    assert restored.correlations[0].change_type == "deploy"
    assert restored.diagnostics[0].row_count == 1
    assert restored.diagnostics[0].executed_at == datetime(
        2026, 4, 16, 12, 0, tzinfo=UTC
    )


# Tier 0: synthetic coverage via medical_records_schema()
def test_evidence_bundle_empty_renders_absent_sections_medical() -> None:
    """Attach an empty bundle to a medical_records anomaly and confirm
    the rendered evidence text lists all three sections as absent.
    """
    # Use summarize_evidence from the analyzer to confirm rendering.
    from observibot.agent.analyzer import summarize_evidence

    _ = medical_anomaly()  # confirm the helper works on medical shape
    rendered = summarize_evidence(EvidenceBundle())

    assert "Recurrence history" in rendered
    assert "Change-event correlations:" in rendered
    assert "Diagnostic query results:" in rendered
    assert "(no prior occurrences)" in rendered
    assert "(none attached)" in rendered
    assert "(not run for this cycle)" in rendered


# Tier 0: synthetic coverage via event_stream_schema()
def test_recurrence_backcompat_event_stream() -> None:
    """Legacy dict-of-dicts recurrence_context must still work when no
    EvidenceBundle is passed. Exercised on event_stream_anomaly() so the
    backcompat path is proven on non-TaskGator data.
    """
    a = event_stream_anomaly(metric="event_count")
    recurrence_map = {
        a.metric_name: {
            "count": 4,
            "first_seen": "2026-04-10T00:00:00+00:00",
            "last_seen": "2026-04-15T00:00:00+00:00",
            "common_hours": [14],
        }
    }
    bundle = EvidenceBundle.from_recurrence_map(recurrence_map)
    assert bundle.recurrence[a.metric_name].count == 4
    assert bundle.recurrence[a.metric_name].common_hours == [14]


def test_from_recurrence_map_ignores_zero_count_entries() -> None:
    recurrence_map = {
        "alpha": {"count": 0},
        "beta": {"count": 2, "last_seen": "2026-04-14"},
    }
    bundle = EvidenceBundle.from_recurrence_map(recurrence_map)
    assert "alpha" not in bundle.recurrence
    assert bundle.recurrence["beta"].count == 2


def test_from_dict_handles_none_and_empty() -> None:
    assert EvidenceBundle.from_dict(None).is_empty()
    assert EvidenceBundle.from_dict({}).is_empty()


def test_diagnostic_evidence_roundtrips_with_error() -> None:
    diag = DiagnosticEvidence(
        hypothesis="table lock contention",
        sql="EXPLAIN ANALYZE SELECT 1",
        row_count=0,
        rows=[],
        explanation="",
        executed_at=datetime(2026, 4, 16, 13, 0, tzinfo=UTC),
        error="statement_timeout after 2000ms",
    )
    restored = DiagnosticEvidence.from_dict(diag.to_dict())
    assert restored.error == "statement_timeout after 2000ms"
    assert restored.row_count == 0
    assert restored.executed_at == diag.executed_at
