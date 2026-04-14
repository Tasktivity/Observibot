from __future__ import annotations

import math
from datetime import UTC, datetime

import pytest

from observibot.core.anomaly import AnomalyDetector
from observibot.core.models import MetricSnapshot


def _m(value: float, name: str = "x", labels: dict | None = None) -> MetricSnapshot:
    return MetricSnapshot(
        connector_name="c",
        metric_name=name,
        value=value,
        labels=labels or {},
        collected_at=datetime.now(UTC),
    )


# ---------- baseline behavior ----------


def test_normal_value_within_baseline() -> None:
    det = AnomalyDetector(min_samples=5, min_absolute_diff=5.0)
    history = [_m(100.0 + (i % 5) * 0.5) for i in range(40)]
    latest = [_m(101.0)]
    assert det.evaluate(history, latest) == []


def test_cold_start_skips() -> None:
    det = AnomalyDetector(min_samples=12, min_absolute_diff=1.0)
    history = [_m(10.0) for _ in range(5)]
    latest = [_m(1_000_000.0)]
    assert det.evaluate(history, latest) == []


def test_nan_value_skipped() -> None:
    det = AnomalyDetector(min_samples=5, min_absolute_diff=0.0)
    history = [_m(10.0 + (i % 3) * 0.1) for i in range(20)]
    latest = [_m(float("nan"))]
    assert det.evaluate(history, latest) == []


def test_inf_value_skipped() -> None:
    det = AnomalyDetector(min_samples=5, min_absolute_diff=0.0)
    history = [_m(10.0 + (i % 3) * 0.1) for i in range(20)]
    latest = [_m(math.inf)]
    assert det.evaluate(history, latest) == []


# ---------- single-spike vs sustained ----------


def test_single_spike_detected_but_not_alerted() -> None:
    det = AnomalyDetector(
        min_samples=5,
        mad_threshold=3.0,
        min_absolute_diff=10.0,
        sustained_intervals_warning=2,
        sustained_intervals_critical=3,
    )
    history = [_m(100.0 + (i % 5) * 0.5) for i in range(40)]
    latest = [_m(1000.0)]
    result = det.evaluate(history, latest)
    # First anomaly is recorded internally but not returned — severity "info".
    assert result == []
    # But the internal counter moved.
    assert det.consecutive_count(latest[0]) == 1


def test_two_consecutive_escalates_to_warning() -> None:
    det = AnomalyDetector(
        min_samples=5,
        mad_threshold=3.0,
        min_absolute_diff=10.0,
        sustained_intervals_warning=2,
        sustained_intervals_critical=3,
    )
    history = [_m(100.0 + (i % 5) * 0.5) for i in range(40)]
    # First spike — tracked, not alerted.
    assert det.evaluate(history, [_m(1000.0)]) == []
    # Second spike — escalates.
    result = det.evaluate(history, [_m(1000.0)])
    assert len(result) == 1
    assert result[0].severity == "warning"
    assert result[0].consecutive_count == 2


def test_three_consecutive_escalates_to_critical() -> None:
    det = AnomalyDetector(
        min_samples=5,
        mad_threshold=3.0,
        min_absolute_diff=10.0,
        sustained_intervals_warning=2,
        sustained_intervals_critical=3,
    )
    history = [_m(100.0 + (i % 5) * 0.5) for i in range(40)]
    det.evaluate(history, [_m(1000.0)])
    det.evaluate(history, [_m(1000.0)])
    result = det.evaluate(history, [_m(1000.0)])
    assert len(result) == 1
    assert result[0].severity == "critical"
    assert result[0].consecutive_count == 3


def test_recovery_resets_counter() -> None:
    det = AnomalyDetector(
        min_samples=5,
        mad_threshold=3.0,
        min_absolute_diff=10.0,
        sustained_intervals_warning=2,
        sustained_intervals_critical=3,
    )
    history = [_m(100.0 + (i % 5) * 0.5) for i in range(40)]
    det.evaluate(history, [_m(1000.0)])  # count → 1
    det.evaluate(history, [_m(1000.0)])  # count → 2, warning
    det.evaluate(history, [_m(101.0)])  # normal, counter resets
    assert det.consecutive_count(_m(1000.0)) == 0
    # Next spike is back to "info" / not alerted.
    assert det.evaluate(history, [_m(1000.0)]) == []


# ---------- min_absolute_diff gate ----------


def test_tiny_absolute_change_not_anomalous() -> None:
    det = AnomalyDetector(
        min_samples=5,
        mad_threshold=3.0,
        min_absolute_diff=10.0,
    )
    # Baseline around 1.0; value jumps to 5.0. Huge percentage but only 4 in
    # absolute terms → should NOT register.
    history = [_m(1.0 + (i % 3) * 0.01) for i in range(40)]
    latest = [_m(5.0)]
    # Run twice to rule out sustained escalation masking the gate.
    assert det.evaluate(history, latest) == []
    assert det.evaluate(history, latest) == []
    # Counter was never incremented because neither statistical nor
    # operational gate fired.
    assert det.consecutive_count(latest[0]) == 0


def test_large_absolute_change_is_anomalous() -> None:
    det = AnomalyDetector(
        min_samples=5,
        mad_threshold=3.0,
        min_absolute_diff=10.0,
        sustained_intervals_warning=2,
        sustained_intervals_critical=3,
    )
    history = [_m(1.0 + (i % 3) * 0.01) for i in range(40)]
    # 100 - 1 = 99 > min_absolute_diff and statistically significant.
    det.evaluate(history, [_m(100.0)])
    result = det.evaluate(history, [_m(100.0)])
    assert len(result) == 1
    assert result[0].severity == "warning"


# ---------- MAD robustness ----------


def test_zero_mad_flat_history_alerts_on_meaningful_change() -> None:
    det = AnomalyDetector(
        min_samples=5,
        min_absolute_diff=5.0,
        sustained_intervals_warning=1,
        sustained_intervals_critical=2,
    )
    history = [_m(42.0) for _ in range(20)]
    result = det.evaluate(history, [_m(60.0)])
    assert len(result) == 1
    assert math.isinf(result[0].modified_z)


def test_zero_mad_flat_history_ignores_noise_below_threshold() -> None:
    det = AnomalyDetector(
        min_samples=5,
        min_absolute_diff=10.0,
    )
    history = [_m(42.0) for _ in range(20)]
    # 45 - 42 = 3 < 10 → ignored.
    assert det.evaluate(history, [_m(45.0)]) == []


def test_mad_robust_to_prior_outlier() -> None:
    """MAD should remain small even if the history contains an earlier spike,
    where a stddev-based detector would be numb for hours.
    """
    det = AnomalyDetector(
        min_samples=5,
        mad_threshold=3.0,
        min_absolute_diff=10.0,
        sustained_intervals_warning=2,
    )
    history = [_m(100.0 + (i % 5) * 0.5) for i in range(40)]
    history.append(_m(1_000_000.0))  # one prior catastrophic outlier
    # A real anomaly should still be detected twice → warning.
    det.evaluate(history, [_m(1000.0)])
    result = det.evaluate(history, [_m(1000.0)])
    assert len(result) == 1
    assert result[0].severity == "warning"


def test_sustained_drift_via_window() -> None:
    det = AnomalyDetector(min_samples=10, mad_threshold=3.0, min_absolute_diff=2.0)
    history = [_m(10.0 + (i % 3) * 0.2) for i in range(40)]
    recent = [_m(20.0) for _ in range(5)]
    drift = det.detect_sustained_drift(history, recent, drift_window=5)
    assert len(drift) == 1
    assert drift[0].direction == "spike"


def test_labels_partition_baselines() -> None:
    det = AnomalyDetector(
        min_samples=5,
        mad_threshold=3.0,
        min_absolute_diff=2.0,
    )
    history = [_m(10.0 + (i % 5) * 0.5, labels={"table": "users"}) for i in range(40)]
    history.extend(
        _m(1000.0 + (i % 5) * 5.0, labels={"table": "tasks"}) for i in range(40)
    )
    latest = [
        _m(10.4, labels={"table": "users"}),
        _m(1005.0, labels={"table": "tasks"}),
    ]
    assert det.evaluate(history, latest) == []


def test_threshold_validation() -> None:
    with pytest.raises(ValueError):
        AnomalyDetector(mad_threshold=-1.0)
    with pytest.raises(ValueError):
        AnomalyDetector(sustained_intervals_warning=0)
    with pytest.raises(ValueError):
        AnomalyDetector(sustained_intervals_warning=3, sustained_intervals_critical=2)
    with pytest.raises(ValueError):
        AnomalyDetector(min_samples=1)
    with pytest.raises(ValueError):
        AnomalyDetector(min_absolute_diff=-0.5)
