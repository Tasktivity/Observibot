"""Anomaly detector — MAD (median absolute deviation) based with sustained-interval gating.

This replaces the original z-score approach, which assumed Gaussian-distributed metrics
and was susceptible to single-spike distortion for 24 hours. MAD is robust to outliers,
works for skewed and count distributions, and — combined with a minimum absolute
difference gate and a sustained-interval escalation policy — produces the kind of signal
a human operator can trust.

Severity escalation ladder:
    1st anomalous reading    → detected, severity "info"    (recorded, not alerted)
    2nd consecutive          → escalated, severity "warning"
    3rd+ consecutive         → escalated, severity "critical"
    metric returns to normal → counter resets to zero
"""
from __future__ import annotations

import logging
import math
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import numpy as np

from observibot.core.models import MetricSnapshot

log = logging.getLogger(__name__)

# 0.6745 scales MAD to be a consistent estimator of the population stddev
# for normally distributed data. We still use it for non-normal data as a
# convenient unit of spread — the user-facing threshold lives in config.
MAD_SCALE = 0.6745


@dataclass
class Anomaly:
    """A detected anomaly for a single metric value."""

    metric_name: str
    connector_name: str
    labels: dict[str, str]
    value: float
    median: float
    mad: float
    modified_z: float
    absolute_diff: float
    severity: str  # info | warning | critical
    direction: str  # spike | dip
    consecutive_count: int
    detected_at: datetime
    sample_count: int

    @property
    def is_alertable(self) -> bool:
        """True once sustained-interval escalation has raised severity."""
        return self.severity in ("warning", "critical")


BucketKey = tuple[str, str, tuple[tuple[str, str], ...]]


def _bucket_key(metric: MetricSnapshot) -> BucketKey:
    return (
        metric.metric_name,
        metric.connector_name,
        tuple(sorted(metric.labels.items())),
    )


def _median_and_mad(values: Iterable[float]) -> tuple[float, float]:
    """Return ``(median, mad)`` for a sequence of values.

    Returns ``(0.0, 0.0)`` for empty input. A MAD of zero means the values are
    perfectly flat — callers must then use the ``min_absolute_diff`` gate alone.
    """
    arr = np.asarray(list(values), dtype=float)
    if arr.size == 0:
        return 0.0, 0.0
    median = float(np.median(arr))
    mad = float(np.median(np.abs(arr - median)))
    return median, mad


@dataclass
class AnomalyDetector:
    """Detect anomalies using MAD and sustained-interval escalation.

    Args:
        mad_threshold: Modified z-score threshold above which a reading is
            considered statistically anomalous. Defaults to 3.0 (roughly 2σ
            of a Gaussian tail when scaled by 0.6745).
        min_absolute_diff: Absolute difference the value must deviate from the
            baseline median before it counts as an anomaly. Prevents alerting
            on trivially tiny changes like 1→5 that have huge percentage
            moves but no operational meaning.
        sustained_intervals_warning: Number of *consecutive* anomalous
            readings required to escalate a metric to ``warning``.
        sustained_intervals_critical: Number of consecutive anomalous readings
            required to escalate to ``critical``.
        min_samples: Minimum history size before detection runs at all.

    The detector is stateful in exactly one respect: it keeps a dict
    mapping each ``(metric, labels)`` bucket to the current consecutive
    anomaly count, so escalation persists across ``evaluate()`` calls.
    """

    mad_threshold: float = 3.0
    min_absolute_diff: float = 10.0
    sustained_intervals_warning: int = 2
    sustained_intervals_critical: int = 3
    min_samples: int = 12
    _consecutive: dict[BucketKey, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.mad_threshold <= 0:
            raise ValueError("mad_threshold must be positive")
        if self.min_absolute_diff < 0:
            raise ValueError("min_absolute_diff must be >= 0")
        if self.sustained_intervals_warning < 1:
            raise ValueError("sustained_intervals_warning must be >= 1")
        if self.sustained_intervals_critical < self.sustained_intervals_warning:
            raise ValueError(
                "sustained_intervals_critical must be >= sustained_intervals_warning"
            )
        if self.min_samples < 2:
            raise ValueError("min_samples must be >= 2")

    def reset(self, bucket: BucketKey | None = None) -> None:
        """Reset sustained-anomaly counters for one bucket or all buckets."""
        if bucket is None:
            self._consecutive.clear()
        else:
            self._consecutive.pop(bucket, None)

    def consecutive_count(self, metric: MetricSnapshot) -> int:
        """Return the current consecutive-anomaly count for a metric bucket."""
        return self._consecutive.get(_bucket_key(metric), 0)

    def evaluate(
        self,
        history: Iterable[MetricSnapshot],
        latest: Iterable[MetricSnapshot],
    ) -> list[Anomaly]:
        """Evaluate ``latest`` metrics against rolling MAD baselines from ``history``.

        Only returns anomalies whose severity has been escalated past ``info``
        (i.e. the sustained-interval policy has fired). First-time spikes are
        tracked internally but not returned until they persist.
        """
        buckets: dict[BucketKey, list[float]] = {}
        for m in history:
            buckets.setdefault(_bucket_key(m), []).append(float(m.value))

        now = datetime.now(UTC)
        results: list[Anomaly] = []
        for m in latest:
            value = float(m.value)
            if not math.isfinite(value):
                log.warning(
                    "Skipping non-finite metric %s value=%s", m.metric_name, value
                )
                continue
            bucket = _bucket_key(m)
            samples = buckets.get(bucket, [])
            if len(samples) < self.min_samples:
                # Cold start — do not touch the counter.
                continue

            median, mad = _median_and_mad(samples)
            absolute_diff = abs(value - median)

            # Two gates must agree for a reading to count as anomalous:
            #   1. Statistical: modified-z exceeds threshold
            #   2. Operational: absolute difference exceeds minimum
            if mad == 0.0:
                # Perfectly flat history — modified-z is undefined.
                is_statistically_anomalous = absolute_diff > 0
                modified_z = float("inf") if absolute_diff > 0 else 0.0
            else:
                modified_z = MAD_SCALE * (value - median) / mad
                is_statistically_anomalous = abs(modified_z) >= self.mad_threshold

            is_meaningful = absolute_diff >= self.min_absolute_diff
            anomalous = is_statistically_anomalous and is_meaningful

            if not anomalous:
                # Recovery — clear the sustained counter for this bucket.
                if bucket in self._consecutive:
                    self._consecutive.pop(bucket)
                continue

            self._consecutive[bucket] = self._consecutive.get(bucket, 0) + 1
            count = self._consecutive[bucket]

            if count >= self.sustained_intervals_critical:
                severity = "critical"
            elif count >= self.sustained_intervals_warning:
                severity = "warning"
            else:
                # First anomalous reading — recorded but not alerted yet.
                severity = "info"

            direction = "spike" if value > median else "dip"
            anomaly = Anomaly(
                metric_name=m.metric_name,
                connector_name=m.connector_name,
                labels=dict(m.labels),
                value=value,
                median=median,
                mad=mad,
                modified_z=modified_z,
                absolute_diff=absolute_diff,
                severity=severity,
                direction=direction,
                consecutive_count=count,
                detected_at=m.collected_at or now,
                sample_count=len(samples),
            )
            if anomaly.is_alertable:
                results.append(anomaly)
            else:
                log.debug(
                    "Anomaly detected but not yet sustained: %s=%s (count=%d)",
                    m.metric_name,
                    value,
                    count,
                )
        return results

    def detect_sustained_drift(
        self,
        history: Iterable[MetricSnapshot],
        recent: Iterable[MetricSnapshot],
        drift_window: int = 5,
    ) -> list[Anomaly]:
        """Detect a sustained shift in median over the most recent window.

        Returns one anomaly per bucket whose recent-window median differs from
        the historical median by more than ``mad_threshold`` MAD units *and*
        exceeds ``min_absolute_diff``.
        """
        history_buckets: dict[BucketKey, list[float]] = {}
        for m in history:
            history_buckets.setdefault(_bucket_key(m), []).append(float(m.value))

        recent_buckets: dict[BucketKey, list[MetricSnapshot]] = {}
        for m in recent:
            recent_buckets.setdefault(_bucket_key(m), []).append(m)

        results: list[Anomaly] = []
        for bucket, recent_metrics in recent_buckets.items():
            window = recent_metrics[-drift_window:]
            if len(window) < drift_window:
                continue
            history_samples = history_buckets.get(bucket, [])
            if len(history_samples) < self.min_samples:
                continue

            window_median, _ = _median_and_mad(float(m.value) for m in window)
            median, mad = _median_and_mad(history_samples)
            absolute_diff = abs(window_median - median)
            if absolute_diff < self.min_absolute_diff:
                continue
            if mad == 0.0:
                modified_z = float("inf")
            else:
                modified_z = MAD_SCALE * (window_median - median) / mad
                if abs(modified_z) < self.mad_threshold:
                    continue

            last = window[-1]
            severity = (
                "critical"
                if drift_window >= self.sustained_intervals_critical
                else "warning"
            )
            results.append(
                Anomaly(
                    metric_name=last.metric_name,
                    connector_name=last.connector_name,
                    labels=dict(last.labels),
                    value=window_median,
                    median=median,
                    mad=mad,
                    modified_z=modified_z,
                    absolute_diff=absolute_diff,
                    severity=severity,
                    direction="spike" if window_median > median else "dip",
                    consecutive_count=drift_window,
                    detected_at=last.collected_at,
                    sample_count=len(history_samples),
                )
            )
        return results


def build_detector_from_config(monitor_cfg: Any) -> AnomalyDetector:
    """Build an :class:`AnomalyDetector` from a :class:`MonitorConfig`."""
    return AnomalyDetector(
        mad_threshold=getattr(monitor_cfg, "mad_threshold", 3.0),
        min_absolute_diff=getattr(monitor_cfg, "min_absolute_diff", 10.0),
        sustained_intervals_warning=getattr(
            monitor_cfg, "sustained_intervals_warning", 2
        ),
        sustained_intervals_critical=getattr(
            monitor_cfg, "sustained_intervals_critical", 3
        ),
        min_samples=getattr(monitor_cfg, "min_samples_for_baseline", 12),
    )
