"""Tests for the shared Prometheus text format parser."""
from __future__ import annotations

from datetime import UTC, datetime

from observibot.connectors.prometheus_parser import (
    PrometheusMetric,
    parse_prometheus_text,
    prometheus_to_snapshots,
)

SAMPLE_PROMETHEUS_TEXT = """\
# HELP node_cpu_seconds_total Seconds the CPUs spent in each mode.
# TYPE node_cpu_seconds_total counter
node_cpu_seconds_total{cpu="0",mode="idle"} 78032.5
node_cpu_seconds_total{cpu="0",mode="user"} 12045.3
node_cpu_seconds_total{cpu="0",mode="system"} 4523.1

# HELP node_memory_MemAvailable_bytes Memory available in bytes.
# TYPE node_memory_MemAvailable_bytes gauge
node_memory_MemAvailable_bytes 4294967296

# HELP process_cpu_seconds_total Total user and system CPU time in seconds.
# TYPE process_cpu_seconds_total counter
process_cpu_seconds_total{service="gotrue"} 1234.5

# HELP supavisor_connections_active Active pooled connections.
# TYPE supavisor_connections_active gauge
supavisor_connections_active{pool="default"} 42

# HELP go_memstats_alloc_bytes Go runtime memory allocation.
# TYPE go_memstats_alloc_bytes gauge
go_memstats_alloc_bytes 8388608

# HELP http_request_duration_seconds A histogram of response latency.
# TYPE http_request_duration_seconds histogram
http_request_duration_seconds_bucket{le="0.1"} 500
http_request_duration_seconds_bucket{le="0.5"} 800
http_request_duration_seconds_sum 120.5
http_request_duration_seconds_count 1000
"""


class TestParsePrometheusText:
    def test_parses_all_metric_types(self):
        metrics = parse_prometheus_text(SAMPLE_PROMETHEUS_TEXT)
        names = [m.name for m in metrics]
        assert "node_cpu_seconds_total" in names
        assert "node_memory_MemAvailable_bytes" in names
        assert "supavisor_connections_active" in names
        assert "go_memstats_alloc_bytes" in names
        assert "http_request_duration_seconds_bucket" in names
        assert "http_request_duration_seconds_sum" in names
        assert "http_request_duration_seconds_count" in names

    def test_parses_labels_correctly(self):
        metrics = parse_prometheus_text(SAMPLE_PROMETHEUS_TEXT)
        cpu_idle = [
            m
            for m in metrics
            if m.name == "node_cpu_seconds_total" and m.labels.get("mode") == "idle"
        ]
        assert len(cpu_idle) == 1
        assert cpu_idle[0].labels == {"cpu": "0", "mode": "idle"}
        assert cpu_idle[0].value == 78032.5

    def test_parses_metric_without_labels(self):
        metrics = parse_prometheus_text(SAMPLE_PROMETHEUS_TEXT)
        mem = [m for m in metrics if m.name == "node_memory_MemAvailable_bytes"]
        assert len(mem) == 1
        assert mem[0].labels == {}
        assert mem[0].value == 4294967296

    def test_detects_metric_types(self):
        metrics = parse_prometheus_text(SAMPLE_PROMETHEUS_TEXT)
        cpu = [m for m in metrics if m.name == "node_cpu_seconds_total"][0]
        assert cpu.metric_type == "counter"

        mem = [m for m in metrics if m.name == "node_memory_MemAvailable_bytes"][0]
        assert mem.metric_type == "gauge"

        hist_sum = [m for m in metrics if m.name == "http_request_duration_seconds_sum"][0]
        assert hist_sum.metric_type == "histogram"

    def test_skips_malformed_lines(self):
        text = """\
# TYPE good_metric gauge
good_metric 42
this is not a valid prometheus line
another bad line {
good_metric_two{label="val"} 100
"""
        metrics = parse_prometheus_text(text)
        assert len(metrics) == 2
        assert metrics[0].name == "good_metric"
        assert metrics[1].name == "good_metric_two"

    def test_parses_nan_and_inf_as_floats(self):
        """Fix 9: NaN/Inf should be parsed (not skipped at parse level)."""
        text = """\
# TYPE m gauge
m_normal 42
m_nan NaN
m_inf +Inf
m_neg_inf -Inf
m_ok 7
"""
        metrics = parse_prometheus_text(text)
        assert len(metrics) == 5
        import math
        nan_m = [m for m in metrics if m.name == "m_nan"]
        assert len(nan_m) == 1
        assert math.isnan(nan_m[0].value)
        inf_m = [m for m in metrics if m.name == "m_inf"]
        assert math.isinf(inf_m[0].value)

    def test_escaped_quotes_in_labels(self):
        """Fix 9: Escaped quotes in label values."""
        text = '# TYPE m gauge\nm{path="/foo\\"bar"} 42\n'
        metrics = parse_prometheus_text(text)
        assert len(metrics) == 1
        assert metrics[0].labels["path"] == '/foo"bar'

    def test_scientific_notation(self):
        """Fix 9: Scientific notation values."""
        text = """\
# TYPE m gauge
m_sci 1.23e+04
m_sci2 4.5e-3
"""
        metrics = parse_prometheus_text(text)
        assert len(metrics) == 2
        assert metrics[0].value == 12300.0
        assert abs(metrics[1].value - 0.0045) < 1e-10

    def test_nan_inf_filtered_in_snapshots(self):
        """Fix 9: NaN/Inf parsed but filtered out of snapshots."""
        text = """\
# TYPE m gauge
m_normal 42
m_nan NaN
m_inf +Inf
"""
        snapshots = prometheus_to_snapshots(text, connector_name="test")
        assert len(snapshots) == 1
        assert snapshots[0].value == 42.0

    def test_empty_text(self):
        assert parse_prometheus_text("") == []
        assert parse_prometheus_text("# just comments\n# HELP foo bar") == []


class TestPrometheusToSnapshots:
    def test_converts_to_metric_snapshots(self):
        ts = datetime(2026, 4, 13, 12, 0, 0, tzinfo=UTC)
        snapshots = prometheus_to_snapshots(
            SAMPLE_PROMETHEUS_TEXT, connector_name="supabase-main", collected_at=ts
        )
        assert len(snapshots) > 0
        for s in snapshots:
            assert s.connector_name == "supabase-main"
            assert s.collected_at == ts

    def test_include_patterns_filter(self):
        snapshots = prometheus_to_snapshots(
            SAMPLE_PROMETHEUS_TEXT,
            connector_name="test",
            include_patterns=["node_cpu_.*"],
        )
        assert all("node_cpu" in s.metric_name for s in snapshots)
        assert len(snapshots) == 3  # 3 cpu mode lines

    def test_exclude_patterns_filter(self):
        snapshots = prometheus_to_snapshots(
            SAMPLE_PROMETHEUS_TEXT,
            connector_name="test",
            exclude_patterns=["go_memstats_.*", ".*_bucket$"],
        )
        names = [s.metric_name for s in snapshots]
        assert "go_memstats_alloc_bytes" not in names
        assert "http_request_duration_seconds_bucket" not in names
        # Other metrics survive
        assert "node_cpu_seconds_total" in names
        assert "supavisor_connections_active" in names

    def test_include_and_exclude_combined(self):
        snapshots = prometheus_to_snapshots(
            SAMPLE_PROMETHEUS_TEXT,
            connector_name="test",
            include_patterns=["http_request_.*"],
            exclude_patterns=[".*_bucket$"],
        )
        names = [s.metric_name for s in snapshots]
        assert "http_request_duration_seconds_sum" in names
        assert "http_request_duration_seconds_count" in names
        assert "http_request_duration_seconds_bucket" not in names

    def test_default_timestamp_is_now(self):
        before = datetime.now(UTC)
        snapshots = prometheus_to_snapshots(
            "# TYPE m gauge\nm 42", connector_name="test"
        )
        after = datetime.now(UTC)
        assert len(snapshots) == 1
        assert before <= snapshots[0].collected_at <= after

    def test_preserves_labels_in_snapshot(self):
        snapshots = prometheus_to_snapshots(
            SAMPLE_PROMETHEUS_TEXT,
            connector_name="test",
            include_patterns=["supavisor_connections_active"],
        )
        assert len(snapshots) == 1
        assert snapshots[0].labels == {"pool": "default"}
        assert snapshots[0].value == 42
