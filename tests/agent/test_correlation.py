"""Tests for change-to-performance correlation detection."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from observibot.agent.analyzer import CorrelationDetector
from observibot.core.anomaly import Anomaly
from observibot.core.models import ChangeEvent
from observibot.core.store import Store


def _make_anomaly(
    metric: str = "query_latency",
    severity: str = "warning",
    value: float = 100.0,
    median: float = 50.0,
    offset_minutes: int = 0,
) -> Anomaly:
    return Anomaly(
        metric_name=metric,
        connector_name="db",
        labels={"table": "users"},
        value=value,
        median=median,
        mad=5.0,
        modified_z=6.7,
        absolute_diff=50.0,
        severity=severity,
        direction="spike",
        consecutive_count=3,
        detected_at=datetime.now(UTC) - timedelta(minutes=offset_minutes),
        sample_count=30,
    )


def _make_change(
    event_type: str = "deploy",
    summary: str = "Deploy v1.2.3",
    offset_minutes: int = 10,
) -> ChangeEvent:
    return ChangeEvent(
        connector_name="railway",
        event_type=event_type,
        summary=summary,
        details={"service": "web"},
        occurred_at=datetime.now(UTC) - timedelta(minutes=offset_minutes),
    )


class TestDeterministicCorrelation:
    def test_detects_temporal_proximity(self):
        detector = CorrelationDetector()
        anomaly = _make_anomaly(offset_minutes=5)
        change = _make_change(offset_minutes=15)

        correlations = detector.detect_correlations([anomaly], [change])
        assert len(correlations) == 1
        assert correlations[0].time_delta_minutes == pytest.approx(10.0, abs=1.0)

    def test_no_correlation_when_far_apart(self):
        detector = CorrelationDetector(proximity_window_minutes=30)
        anomaly = _make_anomaly(offset_minutes=5)
        change = _make_change(offset_minutes=60)

        correlations = detector.detect_correlations([anomaly], [change])
        assert len(correlations) == 0

    def test_no_correlation_when_anomaly_before_change(self):
        detector = CorrelationDetector()
        anomaly = _make_anomaly(offset_minutes=20)
        change = _make_change(offset_minutes=5)

        correlations = detector.detect_correlations([anomaly], [change])
        assert len(correlations) == 0

    def test_no_correlation_with_no_changes(self):
        detector = CorrelationDetector()
        anomaly = _make_anomaly()
        correlations = detector.detect_correlations([anomaly], [])
        assert len(correlations) == 0

    def test_no_correlation_with_no_anomalies(self):
        detector = CorrelationDetector()
        change = _make_change()
        correlations = detector.detect_correlations([], [change])
        assert len(correlations) == 0

    def test_severity_score_increases_with_severity(self):
        detector = CorrelationDetector()
        warning = _make_anomaly(severity="warning", offset_minutes=5)
        critical = _make_anomaly(severity="critical", offset_minutes=5)
        change = _make_change(offset_minutes=15)

        warn_corrs = detector.detect_correlations([warning], [change])
        crit_corrs = detector.detect_correlations([critical], [change])
        assert crit_corrs[0].severity_score > warn_corrs[0].severity_score

    def test_zero_llm_cost_for_no_correlations(self):
        provider = MagicMock()
        detector = CorrelationDetector(provider=provider)
        detector.detect_correlations([], [])
        provider.analyze.assert_not_called()

    def test_multiple_correlations_sorted_by_score(self):
        detector = CorrelationDetector()
        a1 = _make_anomaly(metric="metric_a", severity="warning", offset_minutes=5)
        a2 = _make_anomaly(metric="metric_b", severity="critical", offset_minutes=5)
        change = _make_change(offset_minutes=15)

        correlations = detector.detect_correlations([a1, a2], [change])
        assert len(correlations) == 2
        assert correlations[0].severity_score >= correlations[1].severity_score


class TestDeterministicInsight:
    def test_generates_insight_for_low_score(self):
        detector = CorrelationDetector(escalation_threshold=100.0)
        anomaly = _make_anomaly(offset_minutes=5)
        change = _make_change(offset_minutes=15)
        correlations = detector.detect_correlations([anomaly], [change])

        insight = detector._deterministic_insight(correlations[0])
        assert insight.source == "code_correlation"
        assert insight.confidence == 0.3
        assert "query_latency" in insight.title

    async def test_low_score_skips_llm(self):
        provider = MagicMock()
        detector = CorrelationDetector(
            provider=provider, escalation_threshold=100.0,
        )
        anomaly = _make_anomaly(offset_minutes=5)
        change = _make_change(offset_minutes=15)
        correlations = detector.detect_correlations([anomaly], [change])

        insight = await detector.analyze_correlation(correlations[0])
        provider.analyze.assert_not_called()
        assert insight is not None
        assert insight.source == "code_correlation"


class TestLLMEscalation:
    async def test_llm_escalation_on_high_score(self):
        provider = MagicMock()
        provider.analyze = AsyncMock(return_value=MagicMock(
            data={
                "likely_related": True,
                "confidence": 0.8,
                "mechanism": "Deploy introduced a new N+1 query",
                "recommendation": "Review query patterns in v1.2.3",
            }
        ))
        detector = CorrelationDetector(
            provider=provider, escalation_threshold=0.0,
        )
        anomaly = _make_anomaly(severity="critical", offset_minutes=5)
        change = _make_change(offset_minutes=15)
        correlations = detector.detect_correlations([anomaly], [change])

        insight = await detector.analyze_correlation(correlations[0])
        provider.analyze.assert_called_once()
        assert insight is not None
        assert insight.source == "code_correlation"
        assert insight.confidence == 0.8
        assert "N+1" in insight.summary

    async def test_llm_not_related_returns_none(self):
        provider = MagicMock()
        provider.analyze = AsyncMock(return_value=MagicMock(
            data={"likely_related": False, "confidence": 0.2}
        ))
        detector = CorrelationDetector(
            provider=provider, escalation_threshold=0.0,
        )
        anomaly = _make_anomaly(offset_minutes=5)
        change = _make_change(offset_minutes=15)
        correlations = detector.detect_correlations([anomaly], [change])

        insight = await detector.analyze_correlation(correlations[0])
        assert insight is None

    async def test_llm_failure_falls_back_to_deterministic(self):
        provider = MagicMock()
        provider.analyze = AsyncMock(side_effect=Exception("LLM down"))
        detector = CorrelationDetector(
            provider=provider, escalation_threshold=0.0,
        )
        anomaly = _make_anomaly(offset_minutes=5)
        change = _make_change(offset_minutes=15)
        correlations = detector.detect_correlations([anomaly], [change])

        insight = await detector.analyze_correlation(correlations[0])
        assert insight is not None
        assert insight.confidence == 0.3
