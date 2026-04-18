from __future__ import annotations

from datetime import UTC, datetime

import pytest

from observibot.agent.analyzer import Analyzer, summarize_anomalies, summarize_system
from observibot.agent.llm_provider import LLMHardError, LLMSoftError, MockProvider
from observibot.agent.prompts import ANOMALY_ANALYSIS_PROMPT
from observibot.core.anomaly import Anomaly
from observibot.core.models import SystemModel


def _anomaly(severity: str = "critical") -> Anomaly:
    return Anomaly(
        metric_name="table_inserts",
        connector_name="mock-supabase",
        labels={"table": "tasks"},
        value=500.0,
        median=10.0,
        mad=2.0,
        modified_z=245.0,
        absolute_diff=490.0,
        severity=severity,
        direction="spike",
        consecutive_count=3,
        detected_at=datetime.now(UTC),
        sample_count=20,
    )


@pytest.mark.asyncio
async def test_analyze_anomalies_produces_insights(
    tmp_store, sample_system_model: SystemModel
) -> None:
    analyzer = Analyzer(provider=MockProvider(), store=tmp_store)
    insights = await analyzer.analyze_anomalies(
        anomalies=[_anomaly()],
        system_model=sample_system_model,
    )
    assert insights
    assert insights[0].source == "llm"


@pytest.mark.asyncio
async def test_analyze_anomalies_fallback_on_soft_error(
    tmp_store, sample_system_model: SystemModel
) -> None:
    """A soft (non-auth) provider failure should yield a deterministic
    fallback insight instead of silently dropping the anomaly.
    """
    class FailingProvider(MockProvider):
        async def _call(self, system_prompt, user_prompt):
            raise RuntimeError("transient network blip")

    analyzer = Analyzer(provider=FailingProvider(), store=tmp_store)
    insights = await analyzer.analyze_anomalies(
        anomalies=[_anomaly()],
        system_model=sample_system_model,
    )
    assert len(insights) == 1
    assert insights[0].source == "anomaly"
    assert "LLM analysis was skipped" in insights[0].summary


@pytest.mark.asyncio
async def test_analyze_anomalies_hard_error_propagates(
    tmp_store, sample_system_model: SystemModel
) -> None:
    """S0.5 — a hard (auth/quota) failure propagates so the monitor's
    circuit breaker can switch to long-backoff mode. The fallback
    insight rides along on ``exc.fallback_insights``; persistence is the
    monitor's responsibility.
    """
    class HardFailingProvider(MockProvider):
        async def _call(self, system_prompt, user_prompt):
            raise LLMHardError("401 unauthorized: bad api key")

    analyzer = Analyzer(provider=HardFailingProvider(), store=tmp_store)
    with pytest.raises(LLMHardError) as exc_info:
        await analyzer.analyze_anomalies(
            anomalies=[_anomaly()],
            system_model=sample_system_model,
        )
    fallback = getattr(exc_info.value, "fallback_insights", None)
    assert fallback is not None
    assert any(i.source == "anomaly" for i in fallback)
    # The analyzer does NOT persist on the failure path (S0.5).
    stored = await tmp_store.get_recent_insights()
    assert not any(i.source == "anomaly" for i in stored)


@pytest.mark.asyncio
async def test_analyze_anomalies_invalid_schema_attaches_fallback_no_persist(
    tmp_store, sample_system_model: SystemModel
) -> None:
    """S0.5 — validation failure raises LLMSoftError with the fallback
    attached as ``.fallback_insights``. The analyzer never persists; the
    monitor enriches+saves+dispatches so the circuit breaker still counts
    the failure toward its threshold.
    """
    from unittest.mock import AsyncMock

    bad = MockProvider(canned={"insights": [{"severity": "bogus"}]})
    spy_store = AsyncMock(wraps=tmp_store)
    analyzer = Analyzer(provider=bad, store=spy_store)
    with pytest.raises(LLMSoftError) as exc_info:
        await analyzer.analyze_anomalies(
            anomalies=[_anomaly()],
            system_model=sample_system_model,
        )
    fallback = getattr(exc_info.value, "fallback_insights", None)
    assert fallback is not None
    assert any(i.source == "anomaly" for i in fallback)
    spy_store.save_insight.assert_not_called()


@pytest.mark.asyncio
async def test_analyze_system_returns_validated_model(
    sample_system_model: SystemModel, tmp_store
) -> None:
    analyzer = Analyzer(provider=MockProvider(), store=tmp_store)
    result = await analyzer.analyze_system(sample_system_model)
    assert result.app_type
    assert isinstance(result.critical_tables, list)


@pytest.mark.asyncio
async def test_answer_question(sample_system_model: SystemModel, tmp_store) -> None:
    analyzer = Analyzer(provider=MockProvider(), store=tmp_store)
    result = await analyzer.answer_question(
        question="How many users?",
        system_model=sample_system_model,
        recent_metrics=[],
        recent_insights=[],
    )
    assert result.answer


def test_summarize_system_with_none() -> None:
    assert "no system" in summarize_system(None).lower()


def test_summarize_system_includes_tables(sample_system_model: SystemModel) -> None:
    text = summarize_system(sample_system_model)
    assert "public.users" in text
    assert "public.tasks" in text


@pytest.mark.asyncio
async def test_analyze_anomalies_returns_unsaved_insights(
    tmp_store, sample_system_model: SystemModel
) -> None:
    """Happy path: analyze_anomalies must NOT call save_insight — the caller
    is responsible for enriching and persisting."""
    from unittest.mock import AsyncMock

    spy_store = AsyncMock(wraps=tmp_store)
    analyzer = Analyzer(provider=MockProvider(), store=spy_store)
    insights = await analyzer.analyze_anomalies(
        anomalies=[_anomaly()],
        system_model=sample_system_model,
    )
    assert insights
    assert insights[0].source == "llm"
    spy_store.save_insight.assert_not_called()


@pytest.mark.asyncio
async def test_analyze_anomalies_hard_failure_attaches_fallback_does_not_persist(
    tmp_store, sample_system_model: SystemModel
) -> None:
    """S0.5 — the analyzer never persists. On hard failure, it attaches
    the fallback to the exception and re-raises; the monitor is the
    single persistence+enrichment+dispatch site.
    """
    from unittest.mock import AsyncMock

    class HardFailingProvider(MockProvider):
        async def _call(self, system_prompt, user_prompt):
            raise LLMHardError("401 unauthorized")

    spy_store = AsyncMock(wraps=tmp_store)
    analyzer = Analyzer(provider=HardFailingProvider(), store=spy_store)
    with pytest.raises(LLMHardError) as exc_info:
        await analyzer.analyze_anomalies(
            anomalies=[_anomaly()],
            system_model=sample_system_model,
        )
    fallback = getattr(exc_info.value, "fallback_insights", None)
    assert fallback is not None
    assert any(i.source == "anomaly" for i in fallback)
    spy_store.save_insight.assert_not_called()


@pytest.mark.asyncio
async def test_analyze_anomalies_soft_failure_returns_unsaved_fallback(
    tmp_store, sample_system_model: SystemModel
) -> None:
    """Soft failure: returns unsaved fallback. The caller is responsible for
    enriching and persisting it."""
    from unittest.mock import AsyncMock

    class FailingProvider(MockProvider):
        async def _call(self, system_prompt, user_prompt):
            raise RuntimeError("transient timeout")

    spy_store = AsyncMock(wraps=tmp_store)
    analyzer = Analyzer(provider=FailingProvider(), store=spy_store)
    insights = await analyzer.analyze_anomalies(
        anomalies=[_anomaly()],
        system_model=sample_system_model,
    )
    assert len(insights) == 1
    assert insights[0].source == "anomaly"
    spy_store.save_insight.assert_not_called()


def _directed_anomaly(value: float, median: float, direction: str) -> Anomaly:
    return Anomaly(
        metric_name="table_row_count",
        connector_name="c",
        labels={"table": "t"},
        value=value,
        median=median,
        mad=0.0,
        modified_z=float("inf"),
        absolute_diff=abs(value - median),
        severity="critical",
        direction=direction,
        consecutive_count=3,
        detected_at=datetime.now(UTC),
        sample_count=20,
    )


def test_summarize_anomalies_includes_direction_word() -> None:
    """A spike must be labeled INCREASE with a positive signed delta so the
    LLM cannot silently narrate it as data loss.
    """
    summary = summarize_anomalies(
        [_directed_anomaly(value=100.0, median=40.0, direction="spike")]
    )
    assert "INCREASE" in summary
    assert "delta=+60" in summary


def test_summarize_anomalies_dip_direction() -> None:
    """A dip must be labeled DECREASE with a negative signed delta so a drop
    is never mistaken for a rise.
    """
    summary = summarize_anomalies(
        [_directed_anomaly(value=40.0, median=100.0, direction="dip")]
    )
    assert "DECREASE" in summary
    assert "delta=-60" in summary


def test_prompt_contains_direction_guidance() -> None:
    """The ANOMALY_ANALYSIS_PROMPT must forbid mis-narrating direction."""
    assert "INCREASE" in ANOMALY_ANALYSIS_PROMPT
    assert "DECREASE" in ANOMALY_ANALYSIS_PROMPT
    assert "Do NOT describe an INCREASE as a drop" in ANOMALY_ANALYSIS_PROMPT


def test_prompt_has_evidence_placeholder() -> None:
    """Step 3.3: the prompt body must use the unified {evidence} slot so
    recurrence, correlations, and diagnostics render together.
    """
    assert "{evidence}" in ANOMALY_ANALYSIS_PROMPT
    assert "{recurrence_history}" not in ANOMALY_ANALYSIS_PROMPT


def test_summarize_evidence_renders_all_three_sections_when_empty() -> None:
    """An empty bundle must still render all three sections so the LLM
    can see what is and isn't available.
    """
    from observibot.agent.analyzer import summarize_evidence
    from observibot.core.evidence import EvidenceBundle

    out = summarize_evidence(EvidenceBundle())
    assert "Recurrence history" in out
    assert "Change-event correlations:" in out
    assert "Diagnostic query results:" in out
    assert "(no prior occurrences)" in out
    assert "(none attached)" in out
    assert "(not run for this cycle)" in out


def test_summarize_evidence_renders_recurrence_entries() -> None:
    from observibot.agent.analyzer import summarize_evidence
    from observibot.core.evidence import EvidenceBundle, RecurrenceEvidence

    bundle = EvidenceBundle()
    bundle.recurrence["payouts"] = RecurrenceEvidence(
        metric_name="payouts",
        count=12,
        last_seen="2026-04-15T08:00:00+00:00",
        common_hours=[9, 10],
    )
    out = summarize_evidence(bundle)
    assert "payouts: seen 12 times" in out
    assert "hours [9, 10]" in out
    assert "last seen 2026-04-15" in out


def test_summarize_evidence_renders_citations() -> None:
    """Stage 2: fact citations render in the evidence block with
    concept + truncated claim. Path/lines/commit are NOT in the prompt
    text — those go to the UI only so the LLM can't quote a stale
    code reference.
    """
    from observibot.agent.analyzer import summarize_evidence
    from observibot.core.evidence import (
        DiagnosticEvidence,
        EvidenceBundle,
        FactCitation,
    )

    from datetime import UTC as _UTC
    from datetime import datetime as _dt

    diag = DiagnosticEvidence(
        hypothesis="admissions dropped after deploy",
        sql="SELECT 1 LIMIT 10",
        row_count=0,
        executed_at=_dt(2026, 4, 17, 12, 0, tzinfo=_UTC),
        fact_citations=[
            FactCitation(
                fact_id="c1",
                concept="encounter_type",
                claim="distinguishes inpatient vs outpatient admissions",
                source="code_extraction",
                confidence=0.8,
                path="src/ehr/encounter.py",
                lines="42-55",
                commit="deadbeef",
            ),
        ],
    )
    bundle = EvidenceBundle(diagnostics=[diag])
    out = summarize_evidence(bundle)
    assert "code context cited" in out
    assert "encounter_type" in out
    assert "distinguishes inpatient" in out
    # Path/lines/commit must NOT appear in the LLM-facing text.
    assert "src/ehr/encounter.py" not in out
    assert "42-55" not in out
    assert "deadbeef" not in out


def test_summarize_evidence_renders_freshness_when_not_current() -> None:
    """Stage 2: non-current code freshness is rendered as a tag on the
    diagnostic; ``current`` is silent."""
    from observibot.agent.analyzer import summarize_evidence
    from observibot.core.evidence import DiagnosticEvidence, EvidenceBundle

    from datetime import UTC as _UTC
    from datetime import datetime as _dt

    stale = DiagnosticEvidence(
        hypothesis="h",
        sql="SELECT 1",
        row_count=0,
        executed_at=_dt(2026, 4, 17, 12, 0, tzinfo=_UTC),
        code_freshness="stale",
    )
    current = DiagnosticEvidence(
        hypothesis="h2",
        sql="SELECT 1",
        row_count=0,
        executed_at=_dt(2026, 4, 17, 12, 0, tzinfo=_UTC),
        code_freshness="current",
    )
    out_stale = summarize_evidence(EvidenceBundle(diagnostics=[stale]))
    assert "code context: stale" in out_stale

    out_current = summarize_evidence(EvidenceBundle(diagnostics=[current]))
    assert "code context:" not in out_current


def test_summarize_evidence_renders_errors_section_when_non_empty() -> None:
    """Stage 2: degraded-state entries render as a Degraded signals
    section so the LLM can see Observibot's uncertainty."""
    from observibot.agent.analyzer import summarize_evidence
    from observibot.core.evidence import EvidenceBundle, EvidenceError

    from datetime import UTC as _UTC
    from datetime import datetime as _dt

    bundle = EvidenceBundle(
        errors=[
            EvidenceError(
                stage="fact_retrieval",
                reason="index unavailable",
                occurred_at=_dt(2026, 4, 17, 12, 0, tzinfo=_UTC),
                subject="encounter_count",
            ),
            EvidenceError(
                stage="correlation",
                reason="circuit breaker open",
                occurred_at=_dt(2026, 4, 17, 12, 1, tzinfo=_UTC),
            ),
        ],
    )
    out = summarize_evidence(bundle)
    assert "Degraded signals:" in out
    assert "fact_retrieval" in out
    assert "index unavailable" in out
    assert "[encounter_count]" in out
    assert "correlation" in out
    assert "circuit breaker open" in out


def test_summarize_evidence_silent_when_clean() -> None:
    """Stage 2: with clean recurrence/no citations/current freshness/
    no errors, the Degraded signals section must NOT render at all."""
    from observibot.agent.analyzer import summarize_evidence
    from observibot.core.evidence import EvidenceBundle

    out = summarize_evidence(EvidenceBundle())
    assert "Degraded signals:" not in out
    assert "code context:" not in out
    assert "code context cited" not in out


def test_summarize_evidence_renders_correlations() -> None:
    from observibot.agent.analyzer import summarize_evidence
    from observibot.core.evidence import CorrelationEvidence, EvidenceBundle

    bundle = EvidenceBundle()
    bundle.correlations.append(
        CorrelationEvidence(
            metric_name="latency_p95_ms",
            change_event_id="ch-1",
            change_type="deploy",
            change_summary="api-gw v9",
            time_delta_seconds=600.0,
            severity_score=4.5,
        )
    )
    out = summarize_evidence(bundle)
    assert "latency_p95_ms anomaly 10 min after deploy" in out
    assert "api-gw v9" in out


@pytest.mark.asyncio
async def test_analyze_anomalies_accepts_evidence_kwarg(
    tmp_store, sample_system_model: SystemModel
) -> None:
    """When evidence kwarg is supplied, analyze_anomalies should succeed
    without falling back to the legacy recurrence_context path.
    """
    from observibot.core.evidence import EvidenceBundle, RecurrenceEvidence

    analyzer = Analyzer(provider=MockProvider(), store=tmp_store)
    bundle = EvidenceBundle()
    bundle.recurrence["table_inserts"] = RecurrenceEvidence(
        metric_name="table_inserts",
        count=4,
    )
    insights = await analyzer.analyze_anomalies(
        anomalies=[_anomaly()],
        system_model=sample_system_model,
        evidence=bundle,
    )
    assert insights


@pytest.mark.asyncio
async def test_analyze_anomalies_synthesizes_bundle_from_legacy_recurrence(
    tmp_store, sample_system_model: SystemModel
) -> None:
    """Absent evidence kwarg, the analyzer must synthesize a bundle from
    the legacy recurrence_context dict so existing callers still work.
    """
    analyzer = Analyzer(provider=MockProvider(), store=tmp_store)
    insights = await analyzer.analyze_anomalies(
        anomalies=[_anomaly()],
        system_model=sample_system_model,
        recurrence_context={
            "table_inserts": {"count": 2, "last_seen": "2026-04-15"}
        },
    )
    assert insights


@pytest.mark.asyncio
async def test_analyze_anomalies_enforces_section_budgets(
    tmp_store, sample_system_model: SystemModel
) -> None:
    """A diagnostic bundle with 100s of rows on multiple queries must not
    push the final prompt past the per-section evidence budget. Budget
    enforcement is the safety net that keeps the overall prompt inside
    any current model context window.
    """
    from observibot.agent.analyzer import (
        ANOMALIES_BUDGET_TOKENS,
        BUSINESS_CONTEXT_BUDGET_TOKENS,
        CHANGES_BUDGET_TOKENS,
        EVIDENCE_BUDGET_TOKENS,
        SYSTEM_SUMMARY_BUDGET_TOKENS,
    )
    from observibot.core.evidence import DiagnosticEvidence, EvidenceBundle
    from tests.fixtures.synthetic_schemas import ecommerce_anomaly

    captured_prompt: list[str] = []

    class CapturingProvider(MockProvider):
        async def _call(self, system_prompt, user_prompt):
            captured_prompt.append(user_prompt)
            return await super()._call(system_prompt, user_prompt)

    big_rows = [{"id": i, "value": "x" * 200} for i in range(100)]
    bundle = EvidenceBundle()
    for i in range(3):
        bundle.diagnostics.append(
            DiagnosticEvidence(
                hypothesis=f"hypothesis {i}",
                sql=f"SELECT * FROM t{i}",
                row_count=len(big_rows),
                rows=big_rows,
                explanation="x" * 2000,
            )
        )

    analyzer = Analyzer(provider=CapturingProvider(), store=tmp_store)
    await analyzer.analyze_anomalies(
        anomalies=[ecommerce_anomaly()],
        system_model=sample_system_model,
        evidence=bundle,
    )
    assert captured_prompt
    prompt = captured_prompt[0]
    # Very rough headroom: sum of section budgets plus prompt scaffolding
    # plus truncation markers. Token ≈ chars/4.
    total_budget_tokens = (
        ANOMALIES_BUDGET_TOKENS
        + EVIDENCE_BUDGET_TOKENS
        + CHANGES_BUDGET_TOKENS
        + BUSINESS_CONTEXT_BUDGET_TOKENS
        + SYSTEM_SUMMARY_BUDGET_TOKENS
    )
    ceiling_chars = (total_budget_tokens + 2_000) * 4
    assert len(prompt) < ceiling_chars, (
        f"Prompt {len(prompt)} chars exceeded ceiling {ceiling_chars}"
    )


@pytest.mark.asyncio
async def test_analyze_anomalies_logs_prompt_size(
    tmp_store, sample_system_model: SystemModel, caplog
) -> None:
    """A prompt-size breakdown line must be emitted for every anomaly
    analysis call so a future mystery overflow leaves a grep-able trace.
    Uses a synthetic e-commerce anomaly per Tier 0.
    """
    import logging as std_logging

    from tests.fixtures.synthetic_schemas import ecommerce_anomaly

    analyzer = Analyzer(provider=MockProvider(), store=tmp_store)
    with caplog.at_level(std_logging.DEBUG, logger="observibot.agent.prompt_utils"):
        await analyzer.analyze_anomalies(
            anomalies=[ecommerce_anomaly()],
            system_model=sample_system_model,
        )
    matched = [
        r for r in caplog.records
        if "anomaly_analysis prompt" in r.getMessage()
    ]
    assert matched, "Expected a log line with 'anomaly_analysis prompt' breakdown"
    # Breakdown must include every section label so operators can see where
    # tokens went when the prompt ever approaches a limit.
    msg = matched[-1].getMessage()
    for label in ("anomalies", "evidence", "changes", "system_summary"):
        assert f"{label}=~" in msg, f"missing label {label!r} in prompt breakdown"
