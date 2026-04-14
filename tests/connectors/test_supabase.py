from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from observibot.connectors.base import Capability
from observibot.connectors.supabase import SupabaseConnector


def test_supabase_inherits_connection_validation() -> None:
    with pytest.raises(ValueError):
        SupabaseConnector(name="x", config={})


def test_supabase_required_permissions_includes_rls() -> None:
    conn = SupabaseConnector(
        name="x", config={"connection_string": "postgres://u:p@h/db"}
    )
    perms = conn.required_permissions()
    assert any("pg_policies" in p or "RLS" in p for p in perms)


def test_metrics_api_capability_when_configured() -> None:
    conn = SupabaseConnector(
        name="x",
        config={
            "connection_string": "postgres://u:p@h/db",
            "options": {
                "project_ref": "test-ref",
                "service_key": "test-key",
            },
        },
    )
    caps = conn.get_capabilities()
    assert caps.supports(Capability.RESOURCE_METRICS)


def test_no_resource_metrics_without_config() -> None:
    conn = SupabaseConnector(
        name="x", config={"connection_string": "postgres://u:p@h/db"}
    )
    caps = conn.get_capabilities()
    assert not caps.supports(Capability.RESOURCE_METRICS)


MOCK_PROMETHEUS_TEXT = """\
# HELP node_cpu_seconds_total CPU time in seconds.
# TYPE node_cpu_seconds_total counter
node_cpu_seconds_total{cpu="0",mode="idle"} 100000
node_cpu_seconds_total{cpu="0",mode="user"} 5000
# HELP node_memory_MemAvailable_bytes Memory available.
# TYPE node_memory_MemAvailable_bytes gauge
node_memory_MemAvailable_bytes 4294967296
# HELP go_memstats_alloc_bytes Go runtime memory.
# TYPE go_memstats_alloc_bytes gauge
go_memstats_alloc_bytes 8388608
"""


@pytest.mark.asyncio
async def test_first_scrape_emits_zero_counter_snapshots():
    """Fix 2: First scrape must NOT emit raw cumulative counter values."""
    conn = SupabaseConnector(
        name="supa-test",
        config={
            "connection_string": "postgres://u:p@h/db",
            "options": {
                "project_ref": "test-ref",
                "service_key": "test-key",
            },
        },
    )

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.text = MOCK_PROMETHEUS_TEXT
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_response)
    conn._http_client = mock_client

    # First call: counters should NOT be emitted (no previous value)
    metrics = await conn._collect_prometheus_metrics()

    # Gauges should still be emitted
    gauge_names = [m.metric_name for m in metrics if "memory" in m.metric_name.lower()]
    assert len(gauge_names) > 0

    # go_memstats should be filtered out by default excludes
    assert not any("go_memstats" in m.metric_name for m in metrics)

    # Counter metrics (node_cpu_seconds_total) must NOT appear on first scrape
    counter_metrics = [m for m in metrics if "node_cpu" in m.metric_name]
    assert len(counter_metrics) == 0, (
        f"First scrape should emit zero counter snapshots, got {len(counter_metrics)}"
    )


@pytest.mark.asyncio
async def test_second_scrape_emits_counter_deltas():
    """Fix 2: Second scrape with same values should produce delta=0."""
    conn = SupabaseConnector(
        name="supa-test",
        config={
            "connection_string": "postgres://u:p@h/db",
            "options": {
                "project_ref": "test-ref",
                "service_key": "test-key",
            },
        },
    )

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.text = MOCK_PROMETHEUS_TEXT
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_response)
    conn._http_client = mock_client

    # First call primes the counter cache
    await conn._collect_prometheus_metrics()

    # Second call: deltas emitted for counters (same text = delta 0)
    metrics2 = await conn._collect_prometheus_metrics()
    cpu_metrics = [m for m in metrics2 if "node_cpu" in m.metric_name]
    assert len(cpu_metrics) > 0
    for m in cpu_metrics:
        assert m.value == 0.0  # no change between cycles


@pytest.mark.asyncio
async def test_gauge_always_emitted_with_raw_value():
    """Fix 2: Gauge metrics emit raw values in all cycles."""
    conn = SupabaseConnector(
        name="supa-test",
        config={
            "connection_string": "postgres://u:p@h/db",
            "options": {
                "project_ref": "test-ref",
                "service_key": "test-key",
            },
        },
    )

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.text = MOCK_PROMETHEUS_TEXT
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_response)
    conn._http_client = mock_client

    # Both first and second scrapes should emit gauge with raw value
    m1 = await conn._collect_prometheus_metrics()
    m2 = await conn._collect_prometheus_metrics()

    for metrics in (m1, m2):
        mem = [m for m in metrics if m.metric_name == "node_memory_MemAvailable_bytes"]
        assert len(mem) == 1
        assert mem[0].value == 4294967296


@pytest.mark.asyncio
async def test_graceful_degradation_on_403():
    conn = SupabaseConnector(
        name="supa-test",
        config={
            "connection_string": "postgres://u:p@h/db",
            "options": {
                "project_ref": "test-ref",
                "service_key": "test-key",
            },
        },
    )

    mock_response = MagicMock()
    mock_response.status_code = 403

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_response)
    conn._http_client = mock_client

    metrics = await conn._collect_prometheus_metrics()
    assert metrics == []
    # API should be disabled after 403
    assert not conn._should_scrape_prometheus()


@pytest.mark.asyncio
async def test_counter_reset_handling():
    conn = SupabaseConnector(
        name="supa-test",
        config={
            "connection_string": "postgres://u:p@h/db",
            "options": {
                "project_ref": "test-ref",
                "service_key": "test-key",
                "metrics_api_include": [r"test_counter.*"],
                "metrics_api_exclude": [],
            },
        },
    )

    text1 = """\
# TYPE test_counter_total counter
test_counter_total{service="web"} 100
"""
    text2 = """\
# TYPE test_counter_total counter
test_counter_total{service="web"} 150
"""
    text3 = """\
# TYPE test_counter_total counter
test_counter_total{service="web"} 10
"""

    async def _mock_scrape(text):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = text
        mock_response.raise_for_status = MagicMock()
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        conn._http_client = mock_client
        return await conn._collect_prometheus_metrics()

    # Cycle 1: first collection — no delta, zero counter snapshots
    m1 = await _mock_scrape(text1)
    counter_m1 = [m for m in m1 if m.metric_name == "test_counter_total"]
    assert len(counter_m1) == 0, "First scrape must NOT emit counter snapshots"

    # Cycle 2: delta of 50
    m2 = await _mock_scrape(text2)
    counter_m2 = [m for m in m2 if m.metric_name == "test_counter_total"]
    assert len(counter_m2) == 1
    assert counter_m2[0].value == 50.0  # 150 - 100

    # Cycle 3: reset (10 < 150) — zero counter snapshots
    m3 = await _mock_scrape(text3)
    counter_m3 = [m for m in m3 if m.metric_name == "test_counter_total"]
    assert len(counter_m3) == 0, "Counter reset must NOT emit snapshots"


@pytest.mark.asyncio
async def test_include_exclude_patterns():
    conn = SupabaseConnector(
        name="supa-test",
        config={
            "connection_string": "postgres://u:p@h/db",
            "options": {
                "project_ref": "test-ref",
                "service_key": "test-key",
                "metrics_api_include": [r"node_memory_.*"],
                "metrics_api_exclude": [],
            },
        },
    )

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.text = MOCK_PROMETHEUS_TEXT
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_response)
    conn._http_client = mock_client

    metrics = await conn._collect_prometheus_metrics()
    assert all("node_memory" in m.metric_name for m in metrics)
    assert len(metrics) == 1


PROM_TEXT_WITH_NAN_INF = """\
# HELP node_memory_MemAvailable_bytes Memory available.
# TYPE node_memory_MemAvailable_bytes gauge
node_memory_MemAvailable_bytes 4294967296
# HELP node_cpu_seconds_total CPU time.
# TYPE node_cpu_seconds_total counter
node_cpu_seconds_total{cpu="0",mode="idle"} NaN
node_cpu_seconds_total{cpu="0",mode="user"} +Inf
# HELP bad_gauge A gauge with Inf.
# TYPE bad_gauge gauge
bad_gauge -Inf
# HELP good_gauge A normal gauge.
# TYPE good_gauge gauge
good_gauge 42
"""


@pytest.mark.asyncio
async def test_nan_inf_not_emitted_in_single_pass():
    """Hotfix 2 Fix 1: NaN/Inf must not enter metric_snapshots."""
    conn = SupabaseConnector(
        name="supa-test",
        config={
            "connection_string": "postgres://u:p@h/db",
            "options": {
                "project_ref": "test-ref",
                "service_key": "test-key",
                "metrics_api_include": [".*"],
                "metrics_api_exclude": [],
            },
        },
    )

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.text = PROM_TEXT_WITH_NAN_INF
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_response)
    conn._http_client = mock_client

    metrics = await conn._collect_prometheus_metrics()
    names = [m.metric_name for m in metrics]

    # Finite gauges emitted
    assert "node_memory_MemAvailable_bytes" in names
    assert "good_gauge" in names

    # NaN/Inf gauges NOT emitted
    assert "bad_gauge" not in names

    # NaN/Inf counters NOT emitted (and not cached)
    assert "node_cpu_seconds_total" not in names
    assert len(conn._previous_prometheus_counters) == 0
