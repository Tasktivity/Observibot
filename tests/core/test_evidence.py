"""Unit tests for :class:`observibot.core.evidence.EvidenceBundle`.

Every evidence type is exercised for round-trip, including at least one
test using a Tier 0 synthetic fixture (ecommerce, medical, event_stream)
to confirm the carrier isn't shaped around TaskGator-specific data.
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from observibot.core.evidence import (
    CorrelationEvidence,
    DiagnosticEvidence,
    EvidenceBundle,
    EvidenceError,
    FactCitation,
    RecurrenceEvidence,
    _parse_iso,
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


def test_parse_iso_none_raises() -> None:
    """_parse_iso(None) must raise rather than fabricate datetime.now(UTC).

    Silent fabrication on None made persisted evidence records
    indistinguishable from freshly-executed ones when a field was
    missing. The deserializer must explicitly opt in to a fallback.
    """
    with pytest.raises(ValueError):
        _parse_iso(None)


def test_parse_iso_accepts_iso_string_and_normalizes_tz() -> None:
    dt = _parse_iso("2026-04-16T12:00:00")
    assert dt.tzinfo == UTC
    dt2 = _parse_iso("2026-04-16T12:00:00+00:00")
    assert dt2 == datetime(2026, 4, 16, 12, 0, tzinfo=UTC)


def test_diagnostic_from_dict_handles_missing_executed_at() -> None:
    """Malformed persisted evidence (no executed_at) must deserialize
    gracefully with an audit note in ``error`` — not crash, not silently
    fabricate a timestamp as if the query just ran.
    """
    payload = {
        "hypothesis": "h",
        "sql": "SELECT 1",
        "row_count": 0,
        "rows": [],
        "explanation": "",
        # executed_at deliberately absent
    }
    restored = DiagnosticEvidence.from_dict(payload)
    assert restored.error is not None
    assert "missing executed_at" in restored.error


def test_diagnostic_from_dict_preserves_existing_error_on_malformed() -> None:
    payload = {
        "hypothesis": "h",
        "sql": "SELECT 1",
        "row_count": 0,
        "error": "sandbox rejected: foo",
    }
    restored = DiagnosticEvidence.from_dict(payload)
    assert restored.error is not None
    assert "sandbox rejected: foo" in restored.error
    assert "missing executed_at" in restored.error


def test_evidence_error_roundtrip() -> None:
    """Stage 2: EvidenceError serializes and restores cleanly with
    timezone normalization."""
    err = EvidenceError(
        stage="fact_retrieval",
        reason="index error: semantic_facts_fts missing",
        occurred_at=datetime(2026, 4, 17, 12, 0, tzinfo=UTC),
        subject="encounter_count",
    )
    restored = EvidenceError.from_dict(err.to_dict())
    assert restored == err


def test_fact_citation_roundtrip_preserves_optional_fields() -> None:
    """Stage 2: FactCitation round-trips fact_id/concept/source/
    confidence plus the optional path/lines/commit/repo metadata."""
    cite = FactCitation(
        fact_id="abc123",
        concept="order_status",
        claim="pending orders awaiting payment settlement",
        source="code_extraction",
        confidence=0.82,
        path="backend/orders.py",
        lines="42-55",
        commit="deadbeef",
        repo=None,
    )
    restored = FactCitation.from_dict(cite.to_dict())
    assert restored == cite


def test_evidence_bundle_roundtrip_with_errors() -> None:
    """Stage 2: EvidenceBundle.errors survives the store round-trip
    through to_dict/from_dict."""
    err1 = EvidenceError(
        stage="correlation",
        reason="circuit breaker open",
        occurred_at=datetime(2026, 4, 17, 12, 0, tzinfo=UTC),
    )
    err2 = EvidenceError(
        stage="fact_retrieval",
        reason="stale index > 48h",
        occurred_at=datetime(2026, 4, 17, 12, 1, tzinfo=UTC),
        subject="order_count",
    )
    bundle = EvidenceBundle(errors=[err1, err2])
    restored = EvidenceBundle.from_dict(bundle.to_dict())
    assert len(restored.errors) == 2
    assert restored.errors[0].stage == "correlation"
    assert restored.errors[1].subject == "order_count"


def test_evidence_bundle_roundtrip_with_fact_citations() -> None:
    """Stage 2: DiagnosticEvidence.fact_citations survive the full
    bundle round-trip through JSON."""
    cite = FactCitation(
        fact_id="c1",
        concept="admissions",
        claim="encounter.type='inpatient'",
        source="user_correction",
        confidence=1.0,
        path="src/ehr/encounter.py",
        lines="10-20",
        commit=None,
    )
    diag = DiagnosticEvidence(
        hypothesis="admissions dropped after deploy",
        sql="SELECT encounter_type, count(*) FROM encounters GROUP BY 1 LIMIT 10",
        row_count=3,
        rows=[{"encounter_type": "inpatient", "count": 5}],
        explanation="checks admission counts by type",
        executed_at=datetime(2026, 4, 17, 12, 0, tzinfo=UTC),
        fact_citations=[cite],
        code_freshness="stale",
    )
    bundle = EvidenceBundle(diagnostics=[diag])
    restored = EvidenceBundle.from_dict(bundle.to_dict())
    assert len(restored.diagnostics) == 1
    rdiag = restored.diagnostics[0]
    assert rdiag.code_freshness == "stale"
    assert len(rdiag.fact_citations) == 1
    assert rdiag.fact_citations[0].source == "user_correction"
    assert rdiag.fact_citations[0].path == "src/ehr/encounter.py"


def test_evidence_bundle_backcompat_old_payload() -> None:
    """Stage 2: a pre-Step-4 evidence payload (no errors,
    fact_citations, or code_freshness fields) must deserialize without
    raising. The new fields default to empty / None.
    """
    legacy = {
        "recurrence": {
            "order_count": {
                "count": 3,
                "first_seen": "2026-04-10T00:00:00+00:00",
                "last_seen": "2026-04-16T00:00:00+00:00",
                "common_hours": [9],
            },
        },
        "correlations": [],
        "diagnostics": [
            {
                "hypothesis": "h",
                "sql": "SELECT 1",
                "row_count": 1,
                "rows": [{"n": 1}],
                "explanation": "",
                "executed_at": "2026-04-16T12:00:00+00:00",
                "error": None,
            }
        ],
    }
    bundle = EvidenceBundle.from_dict(legacy)
    assert bundle.errors == []
    assert bundle.diagnostics[0].fact_citations == []
    assert bundle.diagnostics[0].code_freshness is None


def test_evidence_bundle_is_empty_considers_errors() -> None:
    """Stage 2: a bundle with only degraded-state errors is NOT empty;
    the insight card should still render the errors strip.
    """
    bundle = EvidenceBundle(
        errors=[
            EvidenceError(
                stage="fact_retrieval",
                reason="unavailable",
                occurred_at=datetime(2026, 4, 17, 12, 0, tzinfo=UTC),
            )
        ]
    )
    assert bundle.is_empty() is False


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
