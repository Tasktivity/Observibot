"""Tier 1 unit tests for seasonal MAD baselines.

Every test here directly exercises a reviewer-flagged correctness concern from
REVIEW_SYNTHESIS_STEP3.md. Keep them that way — if you add a test, cite which
reviewer finding it protects so a future refactor can't silently remove it.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from observibot.core.anomaly import AnomalyDetector
from observibot.core.models import Insight, MetricSnapshot
from observibot.core.seasonal import (
    compute_seasonal_updates,
    hour_of_week,
    iso_week_key,
    seasonal_labels_key,
)

pytestmark = pytest.mark.asyncio


def _m(
    value: float,
    collected_at: datetime,
    *,
    metric_name: str = "cpu",
    connector_name: str = "c1",
    labels: dict | None = None,
) -> MetricSnapshot:
    return MetricSnapshot(
        connector_name=connector_name,
        metric_name=metric_name,
        value=value,
        labels=labels or {},
        collected_at=collected_at,
    )


# ---------- hour_of_week + iso_week_key (C4 / H10) ----------


def test_hour_of_week_boundaries() -> None:
    # Sunday 23:59:59 UTC → bucket 167
    dt = datetime(2026, 4, 12, 23, 59, 59, tzinfo=UTC)  # Sunday
    assert hour_of_week(dt) == 167
    # Monday 00:00:01 UTC → bucket 0
    dt2 = datetime(2026, 4, 13, 0, 0, 1, tzinfo=UTC)  # Monday
    assert hour_of_week(dt2) == 0
    # Wednesday 12:xx UTC → weekday=2, 2*24+12 = 60
    dt3 = datetime(2026, 4, 15, 12, 30, tzinfo=UTC)  # Wednesday
    assert hour_of_week(dt3) == 60


def test_iso_week_key_transition() -> None:
    # Two metrics one second apart straddling an ISO week boundary MUST have
    # different iso_week_keys so compute_seasonal_updates increments
    # weeks_observed.
    last_monday_before = datetime(2026, 4, 6, 0, 0, 0, tzinfo=UTC)
    sunday_before = last_monday_before - timedelta(seconds=1)
    assert iso_week_key(sunday_before) != iso_week_key(last_monday_before)


# ---------- seasonal_labels_key (H1) ----------


def test_label_stripping_identity_only() -> None:
    strip = frozenset({"instance", "job", "pid", "cpu"})
    labels = {"cpu": "0", "mode": "user", "instance": "node-a"}
    # mode is semantic — must survive the strip
    key = seasonal_labels_key(labels, strip)
    decoded = json.loads(key)
    assert decoded == {"mode": "user"}


def test_label_stripping_different_series_preserved() -> None:
    # node_cpu{mode=user} and node_cpu{mode=system} must NOT collapse to the
    # same bucket — merging them produces garbage medians.
    strip = frozenset({"instance", "job", "pid", "cpu"})
    k_user = seasonal_labels_key({"mode": "user", "cpu": "0"}, strip)
    k_system = seasonal_labels_key({"mode": "system", "cpu": "0"}, strip)
    assert k_user != k_system


def test_label_stripping_empty_after_strip_returns_empty_string() -> None:
    strip = frozenset({"instance", "job"})
    assert seasonal_labels_key({"instance": "a", "job": "b"}, strip) == ""
    assert seasonal_labels_key({}, strip) == ""


# ---------- compute_seasonal_updates (C1 + C2 + ring buffer) ----------


async def test_compute_seasonal_updates_uses_db_not_history(tmp_store) -> None:
    # Sanity: calling compute_seasonal_updates with a fresh store creates rows
    # for every bucket and DOES NOT need a 48h history window. This is the C2
    # regression guard.
    now = datetime(2026, 4, 13, 12, 0, tzinfo=UTC)  # Monday 12:00 UTC
    metrics = [_m(10.0 + i, now, labels={"mode": "user"}) for i in range(3)]
    n = await compute_seasonal_updates(
        tmp_store, metrics, identity_strip_set=frozenset({"cpu"}), max_samples=30
    )
    assert n == 1
    buckets = await tmp_store.fetch_seasonal_buckets(
        [("cpu", "c1", json.dumps({"mode": "user"}), hour_of_week(now))]
    )
    assert len(buckets) == 1
    ((_key, state),) = buckets.items()
    assert state["samples"] == [10.0, 11.0, 12.0]
    # Three samples in the same cycle = same ISO week → weeks_observed == 1
    assert state["weeks_observed"] == 1


async def test_ring_buffer_trim(tmp_store) -> None:
    # Per C1: ring buffer must cap at max_samples. Sample 31 evicts sample 0.
    now = datetime(2026, 4, 13, 12, 30, tzinfo=UTC)
    metrics = [_m(float(i), now) for i in range(31)]
    await compute_seasonal_updates(
        tmp_store, metrics, identity_strip_set=frozenset(), max_samples=30
    )
    buckets = await tmp_store.fetch_seasonal_buckets(
        [("cpu", "c1", "", hour_of_week(now))]
    )
    ((_k, state),) = buckets.items()
    assert len(state["samples"]) == 30
    assert state["samples"][0] == 1.0   # value 0 was evicted
    assert state["samples"][-1] == 30.0


async def test_weeks_observed_increments_across_weeks(tmp_store) -> None:
    strip = frozenset()
    # Week 1: Wednesday 10:00 UTC
    w1 = datetime(2026, 4, 8, 10, 0, tzinfo=UTC)
    await compute_seasonal_updates(
        tmp_store, [_m(5.0, w1)], identity_strip_set=strip, max_samples=30
    )
    # Week 2: Wednesday 10:00 UTC (7 days later)
    w2 = w1 + timedelta(days=7)
    await compute_seasonal_updates(
        tmp_store, [_m(5.5, w2)], identity_strip_set=strip, max_samples=30
    )
    buckets = await tmp_store.fetch_seasonal_buckets(
        [("cpu", "c1", "", hour_of_week(w1))]
    )
    ((_k, state),) = buckets.items()
    assert state["weeks_observed"] == 2


async def test_per_metric_collected_at_used(tmp_store) -> None:
    # Per H10: the cycle may straddle midnight. Per-metric collected_at means
    # a Sunday-23:59 sample and a Monday-00:00 sample land in DIFFERENT
    # hour_of_week buckets even in the same call.
    strip = frozenset()
    sun = datetime(2026, 4, 12, 23, 59, tzinfo=UTC)
    mon = datetime(2026, 4, 13, 0, 0, 1, tzinfo=UTC)
    await compute_seasonal_updates(
        tmp_store,
        [_m(1.0, sun), _m(2.0, mon)],
        identity_strip_set=strip,
        max_samples=30,
    )
    sun_bkt = await tmp_store.fetch_seasonal_buckets(
        [("cpu", "c1", "", 167)]
    )
    mon_bkt = await tmp_store.fetch_seasonal_buckets(
        [("cpu", "c1", "", 0)]
    )
    assert len(sun_bkt) == 1 and len(mon_bkt) == 1


# ---------- get_seasonal_baselines_for_hour (H2 + C4) ----------


async def test_weeks_observed_gating(tmp_store) -> None:
    # Bucket with weeks_observed=3 is NOT trusted; weeks_observed=4 IS.
    await tmp_store.bulk_upsert_seasonal_baselines([
        {
            "metric_name": "m", "connector_name": "c", "labels_key": "",
            "hour_of_week": 5,
            "samples_json": json.dumps([1.0, 1.0, 1.0]),
            "sample_count": 3, "weeks_observed": 3, "last_week": "2026-W12",
            "median": 1.0, "mad": 0.0,
        },
        {
            "metric_name": "m", "connector_name": "c", "labels_key": "trust",
            "hour_of_week": 5,
            "samples_json": json.dumps([10.0, 10.0]),
            "sample_count": 2, "weeks_observed": 4, "last_week": "2026-W15",
            "median": 10.0, "mad": 0.0,
        },
    ])
    trusted = await tmp_store.get_seasonal_baselines_for_hour(
        5, min_weeks_observed=4
    )
    assert ("m", "c", "trust") in trusted
    assert ("m", "c", "") not in trusted


# ---------- AnomalyDetector.evaluate_seasonal (H4) ----------


def test_evaluate_seasonal_empty_lookup_matches_evaluate() -> None:
    det = AnomalyDetector(
        mad_threshold=3.0, min_absolute_diff=5.0, min_samples=5,
        sustained_intervals_warning=1, sustained_intervals_critical=2,
    )
    now = datetime.now(UTC)
    history = [_m(100.0 + (i % 3), now - timedelta(minutes=i)) for i in range(40)]
    latest = [_m(200.0, now)]

    det_a = AnomalyDetector(
        mad_threshold=3.0, min_absolute_diff=5.0, min_samples=5,
        sustained_intervals_warning=1, sustained_intervals_critical=2,
    )
    det_b = AnomalyDetector(
        mad_threshold=3.0, min_absolute_diff=5.0, min_samples=5,
        sustained_intervals_warning=1, sustained_intervals_critical=2,
    )
    a = det_a.evaluate(history=history, latest=latest)
    b = det_b.evaluate_seasonal(
        history=history, latest=latest, seasonal_lookup={},
    )
    assert [x.metric_name for x in a] == [x.metric_name for x in b]
    assert [x.severity for x in a] == [x.severity for x in b]
    assert [x.baseline_source for x in b] == ["rolling"] * len(b)


def test_evaluate_seasonal_uses_seasonal_baseline_when_trusted() -> None:
    # Seasonal bucket: median=100, mad=5, weeks_observed=5
    # A value of 200 should fire (|z| = 0.6745*100/5 = 13.49)
    # A value of 105 should NOT fire (below min_absolute_diff)
    det = AnomalyDetector(
        mad_threshold=3.0, min_absolute_diff=5.0, min_samples=5,
        sustained_intervals_warning=1, sustained_intervals_critical=2,
    )
    now = datetime.now(UTC)
    m = _m(200.0, now, metric_name="cpu", labels={"mode": "user"})
    strip = frozenset({"instance"})
    seasonal_lookup = {
        ("cpu", "c1", seasonal_labels_key({"mode": "user"}, strip)): (100.0, 5.0, 5),
    }
    res = det.evaluate_seasonal(
        history=[],
        latest=[m],
        seasonal_lookup=seasonal_lookup,
        identity_strip_set=strip,
    )
    assert len(res) == 1
    assert res[0].baseline_source == "seasonal"
    assert res[0].median == 100.0
    assert res[0].sample_count == 5  # carries weeks_observed, per M1

    # Small perturbation — should NOT alert.
    det2 = AnomalyDetector(
        mad_threshold=3.0, min_absolute_diff=5.0, min_samples=5,
        sustained_intervals_warning=1, sustained_intervals_critical=2,
    )
    low = _m(101.0, now, metric_name="cpu", labels={"mode": "user"})
    res_low = det2.evaluate_seasonal(
        history=[], latest=[low], seasonal_lookup=seasonal_lookup,
        identity_strip_set=strip,
    )
    assert res_low == []


def test_evaluate_seasonal_falls_back_for_untrusted_bucket() -> None:
    # No seasonal entry for this metric → falls back to rolling window path.
    det = AnomalyDetector(
        mad_threshold=3.0, min_absolute_diff=5.0, min_samples=5,
        sustained_intervals_warning=1, sustained_intervals_critical=2,
    )
    now = datetime.now(UTC)
    history = [_m(10.0 + (i % 3), now - timedelta(minutes=i)) for i in range(40)]
    latest = [_m(500.0, now)]
    # seasonal_lookup has a DIFFERENT metric → ours falls back
    seasonal_lookup = {("other_metric", "c1", ""): (1.0, 0.1, 5)}
    res = det.evaluate_seasonal(
        history=history, latest=latest, seasonal_lookup=seasonal_lookup,
        identity_strip_set=frozenset(),
    )
    assert len(res) == 1
    assert res[0].baseline_source == "rolling"


# ---------- recurrence_context persistence (H8) ----------


async def test_recurrence_context_persisted(tmp_store) -> None:
    # Pre-existing bug: recurrence_context was never written to the DB.
    insight = Insight(
        title="Repeated latency spike",
        severity="warning",
        summary="stuff",
        details="details",
        related_metrics=["db_latency"],
        confidence=0.8,
        recurrence_context={
            "count": 5,
            "first_seen": "2026-04-01T10:00:00+00:00",
            "last_seen": "2026-04-14T10:00:00+00:00",
            "common_hours": [10],
        },
    )
    assert await tmp_store.save_insight(insight) is True

    recents = await tmp_store.get_recent_insights(limit=1)
    assert len(recents) == 1
    loaded = recents[0]
    assert loaded.recurrence_context is not None
    assert loaded.recurrence_context["count"] == 5
    assert loaded.recurrence_context["common_hours"] == [10]


async def test_recurrence_context_dict_roundtrip() -> None:
    rec = {
        "count": 3,
        "first_seen": "2026-04-01T00:00:00+00:00",
        "last_seen": "2026-04-10T00:00:00+00:00",
        "common_hours": [9, 10],
    }
    insight = Insight(title="t", severity="info", summary="s", recurrence_context=rec)
    d = insight.to_dict()
    assert d["recurrence_context"] == rec
    reconstructed = Insight.from_dict(d)
    assert reconstructed.recurrence_context == rec


# ---------- batch recurrence (H9) ----------


async def test_get_event_recurrence_summaries_batch(tmp_store) -> None:
    for i in range(3):
        await tmp_store.emit_event(
            event_type="anomaly",
            source="test",
            subject="metric_a",
            ref_table="metric_snapshots",
            ref_id="x",
            severity="warning",
            summary=f"a {i}",
        )
    for i in range(2):
        await tmp_store.emit_event(
            event_type="anomaly",
            source="test",
            subject="metric_b",
            ref_table="metric_snapshots",
            ref_id="y",
            severity="warning",
            summary=f"b {i}",
        )
    summaries = await tmp_store.get_event_recurrence_summaries(
        subjects=["metric_a", "metric_b", "nope"],
        event_type="anomaly",
        days=30,
    )
    assert summaries["metric_a"]["count"] == 3
    assert summaries["metric_b"]["count"] == 2
    assert "nope" not in summaries


# ---------- coverage (M2) ----------


async def test_seasonal_coverage_returns_trusted_counts(tmp_store) -> None:
    rows = []
    for i in range(10):
        rows.append({
            "metric_name": f"m{i}",
            "connector_name": "c",
            "labels_key": "",
            "hour_of_week": i,
            "samples_json": json.dumps([1.0]),
            "sample_count": 1,
            "weeks_observed": 3,
            "last_week": "2026-W12",
            "median": 1.0,
            "mad": 0.0,
        })
    for i in range(10, 15):
        rows.append({
            "metric_name": f"m{i}",
            "connector_name": "c",
            "labels_key": "",
            "hour_of_week": i,
            "samples_json": json.dumps([1.0, 1.0]),
            "sample_count": 2,
            "weeks_observed": 4,
            "last_week": "2026-W15",
            "median": 1.0,
            "mad": 0.0,
        })
    await tmp_store.bulk_upsert_seasonal_baselines(rows)
    cov = await tmp_store.get_seasonal_coverage(min_weeks_observed=4)
    assert cov["total_buckets"] == 15
    assert cov["trusted_buckets"] == 5
    assert cov["pct_trusted"] == pytest.approx(33.3, rel=1e-2)
