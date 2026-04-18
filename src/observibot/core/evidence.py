"""Unified evidence carriers attached to anomaly-driven insights.

Prior to Step 3.3, evidence flowed through the pipeline as a loose dict
(``Insight.recurrence_context``) containing just recurrence counts. Step
3.3 unifies that under :class:`EvidenceBundle`, which can also carry
change-event correlations (wired in Step 3.4) and diagnostic query
results (generated in Step 3.4 via the sandbox).

The bundle is intentionally schema-light: every evidence type is a
small dataclass with an ``isoformat``-friendly shape so persistence is
trivial JSON. The Insight store column is ``TEXT`` and we round-trip
via :meth:`EvidenceBundle.to_dict` / :meth:`EvidenceBundle.from_dict`.

Step 3.3 does NOT emit ``DiagnosticEvidence`` from the monitor — the
dataclass exists so Step 3.4's sandboxed SQL path can plug in without
changing the carrier or the store schema again.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


def _parse_iso(value: Any) -> datetime:
    """Parse an ISO-8601 timestamp into a timezone-aware UTC datetime.

    Raises ``ValueError`` on ``None`` input. Silent timestamp fabrication
    (returning ``datetime.now(UTC)`` for missing fields) is a trap that
    made a persisted evidence record indistinguishable from a freshly-
    executed one. Callers must pass a real timestamp or explicitly
    handle the malformed case via :meth:`DiagnosticEvidence.from_dict`.
    """
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value
    if value is None:
        raise ValueError("timestamp is required")
    dt = datetime.fromisoformat(str(value))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


@dataclass
class RecurrenceEvidence:
    """Recurrence stats for one metric, sourced from the events table.

    Mirrors the shape of ``Store.get_event_recurrence_summaries`` so the
    monitor can convert its dict-of-dicts result into bundle entries
    without reshaping individual fields.
    """

    metric_name: str
    count: int
    first_seen: str = ""
    last_seen: str = ""
    common_hours: list[int] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric_name": self.metric_name,
            "count": self.count,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
            "common_hours": list(self.common_hours),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RecurrenceEvidence:
        return cls(
            metric_name=data.get("metric_name", ""),
            count=int(data.get("count", 0)),
            first_seen=data.get("first_seen", "") or "",
            last_seen=data.get("last_seen", "") or "",
            common_hours=[int(h) for h in data.get("common_hours") or []],
        )


@dataclass
class CorrelationEvidence:
    """A time-proximate change event correlated with an anomaly.

    Populated by Step 3.4's monitor-side invocation of
    :class:`CorrelationDetector`. Step 3.3 leaves this list empty but the
    bundle carries the slot so downstream consumers do not need to be
    retrofitted.
    """

    metric_name: str
    change_event_id: str
    change_type: str
    change_summary: str
    time_delta_seconds: float
    severity_score: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric_name": self.metric_name,
            "change_event_id": self.change_event_id,
            "change_type": self.change_type,
            "change_summary": self.change_summary,
            "time_delta_seconds": float(self.time_delta_seconds),
            "severity_score": float(self.severity_score),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CorrelationEvidence:
        return cls(
            metric_name=data.get("metric_name", ""),
            change_event_id=data.get("change_event_id", ""),
            change_type=data.get("change_type", ""),
            change_summary=data.get("change_summary", ""),
            time_delta_seconds=float(data.get("time_delta_seconds", 0.0)),
            severity_score=float(data.get("severity_score", 0.0)),
        )


@dataclass
class EvidenceError:
    """A degraded-state note attached to the bundle.

    Phase 4.5 Step 4 adds several enrichment stages (correlation
    detection, fact retrieval, diagnostic SQL, freshness lookup) that
    can each fail independently. Instead of masking failures by
    dropping the affected section silently, the monitor appends an
    :class:`EvidenceError` to the bundle so the operator can always
    answer "what did Observibot try, and what failed?" — the insight
    card renders a visible strip per entry. See O3 in
    ``PROMPTS/STEP4_SEMANTIC_GROUNDING.md``.
    """

    stage: str
    reason: str
    occurred_at: datetime
    subject: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "reason": self.reason,
            "occurred_at": self.occurred_at.isoformat(),
            "subject": self.subject,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EvidenceError:
        raw_ts = data.get("occurred_at")
        try:
            occurred_at = _parse_iso(raw_ts)
        except (ValueError, TypeError):
            occurred_at = datetime.now(UTC)
        return cls(
            stage=str(data.get("stage", "")),
            reason=str(data.get("reason", "")),
            occurred_at=occurred_at,
            subject=data.get("subject"),
        )


@dataclass
class FactCitation:
    """A semantic fact that informed a diagnostic hypothesis.

    Stored on :class:`DiagnosticEvidence` so the operator can navigate
    from an insight back to the code that shaped its reasoning.
    ``path``/``lines``/``commit`` are rendered by the UI; the LLM is
    forbidden from quoting them in its narrative text (they may be
    stale — see the prompt's anti-hallucination guardrail).
    """

    fact_id: str
    concept: str
    claim: str
    source: str
    confidence: float
    path: str | None = None
    lines: str | None = None
    commit: str | None = None
    # Reserved for Phase 4.5 Step 7 multi-repo support; ``None`` today.
    repo: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "fact_id": self.fact_id,
            "concept": self.concept,
            "claim": self.claim,
            "source": self.source,
            "confidence": float(self.confidence),
            "path": self.path,
            "lines": self.lines,
            "commit": self.commit,
            "repo": self.repo,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FactCitation:
        return cls(
            fact_id=str(data.get("fact_id", "")),
            concept=str(data.get("concept", "")),
            claim=str(data.get("claim", "")),
            source=str(data.get("source", "")),
            confidence=float(data.get("confidence", 0.0)),
            path=data.get("path"),
            lines=data.get("lines"),
            commit=data.get("commit"),
            repo=data.get("repo"),
        )


@dataclass
class DiagnosticEvidence:
    """Reserved for Step 3.4. Structured result of a sandboxed SQL
    diagnostic query run against the application database.

    The Step 3.4 flow will:
    - generate a diagnostic query from an anomaly + hypothesis
    - send it through the 5-layer SQL sandbox (app_db, not store)
    - sample/redact the result via ``prompt_utils._sample_rows``
    - attach one :class:`DiagnosticEvidence` per query to the bundle

    In Step 3.3 no instance of this class is ever produced; the field
    exists on :class:`EvidenceBundle` so the carrier is forward-compatible.
    """

    hypothesis: str
    sql: str
    row_count: int
    rows: list[dict[str, Any]] = field(default_factory=list)
    explanation: str = ""
    executed_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    error: str | None = None
    # Stage 2: fact citations that informed this hypothesis. Populated
    # by Stage 6 (semantic facts into diagnostic hypothesis). Legacy
    # records without this field deserialize with an empty list.
    fact_citations: list[FactCitation] = field(default_factory=list)
    # Stage 2: freshness state for the code-intelligence context this
    # diagnostic was grounded in. One of
    # ``"current" | "stale" | "unavailable" | "error"``. ``None`` in
    # legacy records or when the analyzer ran without a code service.
    code_freshness: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "hypothesis": self.hypothesis,
            "sql": self.sql,
            "row_count": int(self.row_count),
            "rows": [dict(r) for r in self.rows],
            "explanation": self.explanation,
            "executed_at": self.executed_at.isoformat(),
            "error": self.error,
            "fact_citations": [c.to_dict() for c in self.fact_citations],
            "code_freshness": self.code_freshness,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DiagnosticEvidence:
        raw_ts = data.get("executed_at")
        error = data.get("error")
        try:
            executed_at = _parse_iso(raw_ts)
        except (ValueError, TypeError):
            executed_at = datetime.now(UTC)
            malformed_note = "malformed evidence record: missing executed_at"
            error = f"{error}; {malformed_note}" if error else malformed_note
        return cls(
            hypothesis=data.get("hypothesis", ""),
            sql=data.get("sql", ""),
            row_count=int(data.get("row_count", 0)),
            rows=[dict(r) for r in data.get("rows") or []],
            explanation=data.get("explanation", "") or "",
            executed_at=executed_at,
            error=error,
            fact_citations=[
                FactCitation.from_dict(c)
                for c in (data.get("fact_citations") or [])
            ],
            code_freshness=data.get("code_freshness"),
        )


@dataclass
class EvidenceBundle:
    """All evidence attached to one anomaly-cluster insight.

    Three independent evidence slots — populated incrementally as the
    monitor's analysis cycle progresses. Each slot is optional; a bundle
    with only recurrence (the Step 3.3 steady state) is valid.
    """

    recurrence: dict[str, RecurrenceEvidence] = field(default_factory=dict)
    correlations: list[CorrelationEvidence] = field(default_factory=list)
    diagnostics: list[DiagnosticEvidence] = field(default_factory=list)
    # Stage 2: degraded-state notes. When an enrichment stage fails
    # (fact retrieval unavailable, correlation pass errored, freshness
    # lookup raised), the monitor appends an :class:`EvidenceError`
    # here. The insight card renders them as a visible warning strip
    # so failures are fail-visible, not fail-silent.
    errors: list[EvidenceError] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not (
            self.recurrence
            or self.correlations
            or self.diagnostics
            or self.errors
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "recurrence": {
                k: v.to_dict() for k, v in self.recurrence.items()
            },
            "correlations": [c.to_dict() for c in self.correlations],
            "diagnostics": [d.to_dict() for d in self.diagnostics],
            "errors": [e.to_dict() for e in self.errors],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> EvidenceBundle:
        if not data:
            return cls()
        recurrence_raw = data.get("recurrence") or {}
        recurrence: dict[str, RecurrenceEvidence] = {}
        for k, v in recurrence_raw.items():
            if isinstance(v, dict):
                recurrence[k] = RecurrenceEvidence.from_dict(
                    {"metric_name": k, **v}
                )
        return cls(
            recurrence=recurrence,
            correlations=[
                CorrelationEvidence.from_dict(c)
                for c in data.get("correlations") or []
            ],
            diagnostics=[
                DiagnosticEvidence.from_dict(d)
                for d in data.get("diagnostics") or []
            ],
            errors=[
                EvidenceError.from_dict(e)
                for e in data.get("errors") or []
            ],
        )

    @classmethod
    def from_recurrence_map(
        cls, recurrence_map: dict[str, dict[str, Any]] | None
    ) -> EvidenceBundle:
        """Build a bundle from the legacy recurrence dict-of-dicts shape.

        Used when integrating with existing store helpers whose return
        type predates :class:`EvidenceBundle`. Ignores entries whose
        ``count`` is 0 (nothing to report).
        """
        bundle = cls()
        if not recurrence_map:
            return bundle
        for metric, rec in recurrence_map.items():
            count = int(rec.get("count", 0)) if isinstance(rec, dict) else 0
            if count <= 0:
                continue
            first_seen = rec.get("first_seen") or ""
            last_seen = rec.get("last_seen") or ""
            hours = [int(h) for h in (rec.get("common_hours") or [])]
            bundle.recurrence[metric] = RecurrenceEvidence(
                metric_name=metric,
                count=count,
                first_seen=str(first_seen),
                last_seen=str(last_seen),
                common_hours=hours,
            )
        return bundle
