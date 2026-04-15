"""Seasonal MAD baseline computation using per-bucket sample ring buffers.

The detector maintains one bucket per
(tenant, metric, connector, normalized_labels, hour_of_week). Each bucket holds
a ring buffer of the most recent ``max_samples`` float observations along with
``weeks_observed`` — the number of distinct ISO weeks that have contributed at
least one sample. Only buckets with ``weeks_observed >= min_seasonal_weeks``
are considered "trusted" and used in anomaly evaluation; everything else falls
back to the existing rolling-window MAD path.
"""
from __future__ import annotations

import json
import logging
import math
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from observibot.core.models import MetricSnapshot
    from observibot.core.store import Store

log = logging.getLogger(__name__)


def hour_of_week(dt: datetime) -> int:
    """Return 0-167 for a UTC datetime (Monday 00:00 = 0, Sunday 23:xx = 167)."""
    d = dt.astimezone(UTC)
    return d.weekday() * 24 + d.hour


def iso_week_key(dt: datetime) -> str:
    """ISO year-week string, e.g. '2026-W15'. Used to detect week transitions."""
    d = dt.astimezone(UTC)
    iso = d.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def seasonal_labels_key(
    labels: dict[str, str], strip_set: frozenset[str]
) -> str:
    """Strip identity-only labels and return a normalized JSON key.

    Only the caller-provided strip_set (instance/job/pid/cpu by default) is
    removed. Semantic labels like mode, device, le, quantile are NEVER stripped
    — they distinguish different Prometheus series and merging them would
    produce nonsense medians.
    """
    if not labels:
        return ""
    normalized = {k: v for k, v in labels.items() if k not in strip_set}
    return json.dumps(normalized, sort_keys=True) if normalized else ""


async def compute_seasonal_updates(
    store: Store,
    metrics: list[MetricSnapshot],
    identity_strip_set: frozenset[str],
    max_samples: int = 30,
) -> int:
    """Update seasonal baseline buckets for the current collection cycle.

    Algorithm (Fetch → Append → Prune → Recompute → Upsert):

    1. Build the set of (metric, connector, norm_labels, hour_of_week) buckets
       touched by this cycle's metrics, using per-metric ``collected_at``.
    2. Batch-fetch the existing ``samples_json`` / ``weeks_observed`` /
       ``last_week`` for those buckets from the DB.
    3. For each bucket: append the new value(s), prune the ring buffer to
       ``max_samples``, increment ``weeks_observed`` when the ISO week changes,
       and recompute median / MAD.
    4. Bulk-upsert all rows in one DB round-trip.

    Returns the number of buckets upserted.
    """
    if not metrics:
        return 0

    # Step 1: bucket → [(value, iso_week_key), ...]
    bucket_new_values: dict[tuple, list[tuple[float, str]]] = {}
    for m in metrics:
        if m.collected_at is None:
            continue
        v = float(m.value)
        if not math.isfinite(v):
            continue
        how = hour_of_week(m.collected_at)
        norm_key = seasonal_labels_key(m.labels, identity_strip_set)
        bucket = (m.metric_name, m.connector_name, norm_key, how)
        wk = iso_week_key(m.collected_at)
        bucket_new_values.setdefault(bucket, []).append((v, wk))

    if not bucket_new_values:
        return 0

    # Step 2: batch-load existing bucket state
    existing = await store.fetch_seasonal_buckets(list(bucket_new_values.keys()))

    # Step 3: compute updates
    updates: list[dict] = []
    for bucket, new_entries in bucket_new_values.items():
        old = existing.get(
            bucket,
            {"samples": [], "weeks_observed": 0, "last_week": None},
        )
        samples: list[float] = list(old["samples"])
        weeks_obs: int = int(old["weeks_observed"])
        last_week: str | None = old["last_week"]

        for val, wk in new_entries:
            samples.append(val)
            if last_week is None or wk != last_week:
                weeks_obs += 1
                last_week = wk

        samples = samples[-max_samples:]
        arr = np.asarray(samples, dtype=float)
        median = float(np.median(arr))
        mad = float(np.median(np.abs(arr - median)))

        updates.append(
            {
                "metric_name": bucket[0],
                "connector_name": bucket[1],
                "labels_key": bucket[2],
                "hour_of_week": bucket[3],
                "samples_json": json.dumps(samples),
                "sample_count": len(samples),
                "weeks_observed": weeks_obs,
                "last_week": last_week,
                "median": median,
                "mad": mad,
            }
        )

    # Step 4: bulk upsert
    await store.bulk_upsert_seasonal_baselines(updates)
    return len(updates)
