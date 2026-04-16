from __future__ import annotations

import math
from datetime import UTC, datetime

import pytest

from observibot.core.anomaly import Anomaly, AnomalyDetector, compute_anomaly_signature
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


def test_zero_mad_relative_floor_blocks_tiny_drift_on_large_metric() -> None:
    """When MAD=0, a fixed absolute floor is not enough — a 10-row nudge on a
    20k-row table is 0.05%, operationally meaningless, but an absolute-only
    gate fires anyway. The relative floor must suppress it.
    """
    det = AnomalyDetector(
        min_samples=5,
        min_absolute_diff=10.0,
        min_relative_diff=0.02,
        sustained_intervals_warning=1,
        sustained_intervals_critical=2,
    )
    history = [_m(20_000.0) for _ in range(20)]
    # diff=10 passes absolute floor (>=10) but fails relative floor
    # (0.02 * 20_000 = 400).
    assert det.evaluate(history, [_m(20_010.0)]) == []


def test_zero_mad_relative_floor_lets_meaningful_drop_through() -> None:
    """A 35% drop on a flat-baseline metric must still fire even with the
    relative floor in place — only trivial drifts should be suppressed.
    """
    det = AnomalyDetector(
        min_samples=5,
        min_absolute_diff=10.0,
        min_relative_diff=0.02,
        sustained_intervals_warning=1,
        sustained_intervals_critical=2,
    )
    history = [_m(234.0) for _ in range(20)]
    # diff = 83, floor = 0.02 * 234 = 4.68 → passes.
    result = det.evaluate(history, [_m(151.0)])
    assert len(result) == 1
    assert result[0].direction == "dip"


def test_zero_mad_relative_floor_disabled_when_zero() -> None:
    """Setting min_relative_diff=0 reverts to absolute-only gating."""
    det = AnomalyDetector(
        min_samples=5,
        min_absolute_diff=10.0,
        min_relative_diff=0.0,
        sustained_intervals_warning=1,
        sustained_intervals_critical=2,
    )
    history = [_m(20_000.0) for _ in range(20)]
    result = det.evaluate(history, [_m(20_015.0)])
    assert len(result) == 1


def test_relative_floor_does_not_gate_nonzero_mad() -> None:
    """When MAD > 0 the z-gate already scales with spread; relative floor
    must not add a second layer that surprises operators on normal metrics.
    """
    det = AnomalyDetector(
        min_samples=5,
        mad_threshold=3.0,
        min_absolute_diff=10.0,
        min_relative_diff=0.10,  # deliberately aggressive
        sustained_intervals_warning=1,
    )
    # Nearly-flat but non-zero MAD history → z-gate is easy to pass.
    history = [_m(20_000.0 + (i % 2) * 1.0) for i in range(40)]
    # diff = ~30 > min_absolute_diff=10, modified-z huge because MAD=0.5,
    # but 30 < 0.10 * 20000 = 2000. Relative floor would kill it if applied
    # on the non-zero-MAD path — it must not.
    result = det.evaluate(history, [_m(20_030.0)])
    assert len(result) == 1


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


def _anomaly(
    name: str = "table_row_count",
    labels: dict | None = None,
    value: float = 100.0,
    median: float = 50.0,
    direction: str = "spike",
) -> Anomaly:
    return Anomaly(
        metric_name=name,
        connector_name="c",
        labels=labels or {"table": "t"},
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


def test_anomaly_signature_is_stable_across_identical_sets() -> None:
    s1 = compute_anomaly_signature([_anomaly(value=151, median=234, direction="dip")])
    s2 = compute_anomaly_signature([_anomaly(value=151, median=234, direction="dip")])
    assert s1 == s2
    assert len(s1) == 16


def test_anomaly_signature_ignores_value_and_counter() -> None:
    """Same bucket firing repeatedly must yield the same signature regardless
    of the value drift or consecutive-count escalation — that's what makes
    dedup collapse re-firings.
    """
    a1 = _anomaly(value=151, median=234, direction="dip")
    a2 = _anomaly(value=148, median=234, direction="dip")
    a2.consecutive_count = 9
    assert compute_anomaly_signature([a1]) == compute_anomaly_signature([a2])


def test_anomaly_signature_order_insensitive() -> None:
    a = _anomaly(labels={"table": "users"})
    b = _anomaly(labels={"table": "orders"})
    assert compute_anomaly_signature([a, b]) == compute_anomaly_signature([b, a])


def test_anomaly_signature_differs_by_direction() -> None:
    """A dip and a spike on the same bucket are operationally different."""
    up = _anomaly(direction="spike")
    down = _anomaly(direction="dip")
    assert compute_anomaly_signature([up]) != compute_anomaly_signature([down])


def test_anomaly_signature_differs_by_labels() -> None:
    a = _anomaly(labels={"table": "users"})
    b = _anomaly(labels={"table": "orders"})
    assert compute_anomaly_signature([a]) != compute_anomaly_signature([b])


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
    with pytest.raises(ValueError):
        AnomalyDetector(min_relative_diff=-0.1)
