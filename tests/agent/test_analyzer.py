from __future__ import annotations

from datetime import UTC, datetime

import pytest

from observibot.agent.analyzer import Analyzer, summarize_system
from observibot.agent.llm_provider import LLMHardError, LLMSoftError, MockProvider
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
    """A hard (auth/quota) failure should raise so the circuit breaker can
    switch to long-backoff mode — but the fallback insight must still be
    persisted first.
    """
    class HardFailingProvider(MockProvider):
        async def _call(self, system_prompt, user_prompt):
            raise LLMHardError("401 unauthorized: bad api key")

    analyzer = Analyzer(provider=HardFailingProvider(), store=tmp_store)
    with pytest.raises(LLMHardError):
        await analyzer.analyze_anomalies(
            anomalies=[_anomaly()],
            system_model=sample_system_model,
        )
    stored = await tmp_store.get_recent_insights()
    assert any(i.source == "anomaly" for i in stored)


@pytest.mark.asyncio
async def test_analyze_anomalies_invalid_schema_uses_fallback(
    tmp_store, sample_system_model: SystemModel
) -> None:
    """If the LLM returns JSON that doesn't match our Pydantic schema, we
    must fall back to a raw alert, persist it, and raise a soft error so
    the circuit breaker counts it.
    """
    bad = MockProvider(canned={"insights": [{"severity": "bogus"}]})
    analyzer = Analyzer(provider=bad, store=tmp_store)
    with pytest.raises(LLMSoftError):
        await analyzer.analyze_anomalies(
            anomalies=[_anomaly()],
            system_model=sample_system_model,
        )
    stored = await tmp_store.get_recent_insights()
    assert any(i.source == "anomaly" for i in stored)


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
