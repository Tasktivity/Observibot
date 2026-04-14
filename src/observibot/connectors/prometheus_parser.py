"""Prometheus text format parser -- shared utility for connectors.

Parses the standard Prometheus exposition format into structured metrics,
then converts them to MetricSnapshot objects.  Supports include/exclude
regex filtering so each connector selects the metrics that matter.

Reference: https://prometheus.io/docs/instrumenting/exposition_formats/
"""
from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass
from datetime import UTC, datetime

from observibot.core.models import MetricSnapshot

logger = logging.getLogger(__name__)

# Matches: metric_name{label1="val1",label2="val2"} value [timestamp]
# Also handles metric_name value [timestamp] (no labels).
_SAMPLE_RE = re.compile(
    r'^(?P<name>[a-zA-Z_:][a-zA-Z0-9_:]*)'
    r'(?:\{(?P<labels>[^}]*)\})?\s+'
    r'(?P<value>[^\s]+)'
    r'(?:\s+(?P<timestamp>\d+))?$'
)

_LABEL_RE = re.compile(r'(\w+)="((?:[^"\\]|\\.)*)"')


@dataclass
class PrometheusMetric:
    """A single parsed Prometheus sample."""

    name: str
    labels: dict[str, str]
    value: float
    metric_type: str | None  # gauge, counter, histogram, summary, untyped


def parse_prometheus_text(text: str) -> list[PrometheusMetric]:
    """Parse Prometheus exposition format into structured metrics.

    Iterates lines, tracks current TYPE declarations, and parses data lines
    with or without labels.  Malformed lines are logged and skipped.
    """
    metrics: list[PrometheusMetric] = []
    type_map: dict[str, str] = {}

    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue

        # TYPE declaration
        if line.startswith("# TYPE "):
            parts = line.split(None, 4)
            if len(parts) >= 4:
                type_map[parts[2]] = parts[3]
            continue

        # HELP or other comments
        if line.startswith("#"):
            continue

        m = _SAMPLE_RE.match(line)
        if not m:
            logger.debug("Skipping malformed Prometheus line: %s", line[:120])
            continue

        name = m.group("name")
        labels_str = m.group("labels") or ""
        value_str = m.group("value")

        # Parse value -- handle special Prometheus values and scientific notation
        try:
            value = float(value_str)
        except ValueError:
            logger.debug("Skipping line with unparseable value: %s", line[:120])
            continue

        # Parse labels and unescape Prometheus escape sequences
        labels: dict[str, str] = {}
        if labels_str:
            for lm in _LABEL_RE.finditer(labels_str):
                raw_val = lm.group(2)
                # Unescape Prometheus label escapes: \\ → \, \" → ", \n → newline
                unescaped = raw_val.replace("\\\\", "\x00").replace('\\"', '"')
                unescaped = unescaped.replace("\\n", "\n").replace("\x00", "\\")
                labels[lm.group(1)] = unescaped

        # Determine metric type from the base name (strip _total, _sum, _count, _bucket)
        base_name = name
        for suffix in ("_total", "_sum", "_count", "_bucket"):
            if base_name.endswith(suffix):
                base_name = base_name[: -len(suffix)]
                break
        metric_type = type_map.get(name) or type_map.get(base_name)

        metrics.append(
            PrometheusMetric(
                name=name,
                labels=labels,
                value=value,
                metric_type=metric_type,
            )
        )

    return metrics


def prometheus_to_snapshots(
    text: str,
    connector_name: str,
    collected_at: datetime | None = None,
    include_patterns: list[str] | None = None,
    exclude_patterns: list[str] | None = None,
) -> list[MetricSnapshot]:
    """Parse Prometheus text and convert to MetricSnapshot objects.

    Parameters
    ----------
    text:
        Raw Prometheus exposition text.
    connector_name:
        Connector name to tag each snapshot with.
    collected_at:
        Timestamp override; defaults to ``datetime.now(UTC)``.
    include_patterns:
        If set, only metrics whose name matches at least one regex are kept.
    exclude_patterns:
        Metrics whose name matches any of these regexes are dropped.
    """
    if collected_at is None:
        collected_at = datetime.now(UTC)

    raw = parse_prometheus_text(text)

    # Pre-compile filter patterns
    compiled_include = [re.compile(p) for p in (include_patterns or [])]
    compiled_exclude = [re.compile(p) for p in (exclude_patterns or [])]

    snapshots: list[MetricSnapshot] = []
    for pm in raw:
        # Skip non-finite values (NaN, Inf) — not useful for MAD
        if not math.isfinite(pm.value):
            continue
        # Include filter: if patterns specified, at least one must match
        if compiled_include and not any(r.search(pm.name) for r in compiled_include):
            continue
        # Exclude filter: if any pattern matches, skip
        if compiled_exclude and any(r.search(pm.name) for r in compiled_exclude):
            continue

        snapshots.append(
            MetricSnapshot(
                connector_name=connector_name,
                metric_name=pm.name,
                value=pm.value,
                labels=dict(pm.labels),
                collected_at=collected_at,
            )
        )

    return snapshots
