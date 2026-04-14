"""Tests for Railway resource metrics collection (GraphQL + Prometheus)."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from observibot.connectors.base import Capability
from observibot.connectors.railway import PermanentGraphQLError, RailwayConnector

pytestmark = pytest.mark.asyncio

MOCK_PROJECT_DATA = {
    "project": {
        "id": "proj-1",
        "name": "my-project",
        "services": {
            "edges": [
                {"node": {"id": "svc-1", "name": "web"}},
                {"node": {"id": "svc-2", "name": "worker"}},
            ]
        },
        "environments": {
            "edges": [
                {"node": {"id": "env-1", "name": "production"}},
            ]
        },
    }
}

MOCK_METRICS_DATA = {
    "metrics": [
        {
            "measurement": "CPU_USAGE",
            "values": [
                {"ts": "2026-04-13T12:00:00Z", "value": 0.45},
                {"ts": "2026-04-13T12:05:00Z", "value": 0.52},
            ],
        },
        {
            "measurement": "MEMORY_USAGE_GB",
            "values": [
                {"ts": "2026-04-13T12:05:00Z", "value": 0.256},
            ],
        },
        {
            "measurement": "NETWORK_RX_GB",
            "values": [
                {"ts": "2026-04-13T12:05:00Z", "value": 1.2},
            ],
        },
    ]
}


def _make_connector(**extra_config):
    config = {
        "api_token": "test-token",
        "project_id": "proj-1",
        **extra_config,
    }
    return RailwayConnector(name="railway-test", config=config)


def test_capability_includes_resource_metrics():
    conn = _make_connector()
    caps = conn.get_capabilities()
    assert caps.supports(Capability.RESOURCE_METRICS)
    assert caps.supports(Capability.METRICS)
    assert caps.supports(Capability.DISCOVERY)
    assert caps.supports(Capability.CHANGES)


async def test_service_count_still_emitted():
    conn = _make_connector()

    async def mock_graphql(query, variables):
        if "Project" in query:
            return MOCK_PROJECT_DATA
        return {"metrics": []}

    conn._graphql = mock_graphql
    metrics = await conn.collect_metrics()
    svc_count = [m for m in metrics if m.metric_name == "service_count"]
    assert len(svc_count) == 1
    assert svc_count[0].value == 2.0


async def test_graphql_resource_metrics_parsed():
    conn = _make_connector()
    call_count = 0

    async def mock_graphql(query, variables):
        nonlocal call_count
        call_count += 1
        if "Project" in query:
            return MOCK_PROJECT_DATA
        if "ServiceMetrics" in query:
            return MOCK_METRICS_DATA
        return {}

    conn._graphql = mock_graphql
    metrics = await conn.collect_metrics()

    cpu_metrics = [m for m in metrics if m.metric_name == "service_cpu_usage"]
    assert len(cpu_metrics) > 0
    # Latest value from the time series
    assert cpu_metrics[0].value == 0.52
    assert cpu_metrics[0].labels["service"] in ("web", "worker")

    mem_metrics = [m for m in metrics if m.metric_name == "service_memory_usage_gb"]
    assert len(mem_metrics) > 0

    net_metrics = [m for m in metrics if m.metric_name == "service_network_rx_gb"]
    assert len(net_metrics) > 0


async def test_transient_error_does_not_permanently_disable():
    """Fix 3: Transient errors should NOT set _graphql_metrics_available=False."""
    conn = _make_connector()

    async def mock_graphql(query, variables):
        if "Project" in query:
            return MOCK_PROJECT_DATA
        if "ServiceMetrics" in query:
            raise RuntimeError("timeout")
        return {}

    conn._graphql = mock_graphql
    metrics = await conn.collect_metrics()

    svc_count = [m for m in metrics if m.metric_name == "service_count"]
    assert len(svc_count) == 1
    # Should NOT have permanently disabled — transient error
    assert conn._graphql_metrics_available is not False


async def test_schema_error_permanently_disables():
    """Schema errors (field not found) should permanently disable."""
    conn = _make_connector()

    async def mock_graphql(query, variables):
        if "Project" in query:
            return MOCK_PROJECT_DATA
        if "ServiceMetrics" in query:
            raise PermanentGraphQLError(
                "GraphQL errors: [{'message': 'Cannot query field \"metrics\"'}]"
            )
        return {}

    conn._graphql = mock_graphql
    await conn.collect_metrics()
    assert conn._graphql_metrics_available is False

    # Second call should skip
    metrics2 = await conn.collect_metrics()
    assert len(metrics2) == 1  # Only service_count


async def test_http_400_permanently_disables():
    """HTTP 400 on metrics query should permanently disable (Railway schema drift)."""
    conn = _make_connector()

    async def mock_graphql(query, variables):
        if "Project" in query:
            return MOCK_PROJECT_DATA
        if "ServiceMetrics" in query:
            raise PermanentGraphQLError(
                "HTTP 400: {\"errors\":[{\"message\":"
                "\"Cannot represent null for non-null DateTime\"}]}"
            )
        return {}

    conn._graphql = mock_graphql
    metrics = await conn.collect_metrics()
    assert conn._graphql_metrics_available is False
    # Only service_count should have been emitted
    resource_metrics = [m for m in metrics if m.metric_name != "service_count"]
    assert resource_metrics == []


async def test_one_service_failure_does_not_block_others():
    """Fix 3: Failure for one service should not prevent metrics for others."""
    conn = _make_connector()
    call_count = 0

    async def mock_graphql(query, variables):
        nonlocal call_count
        if "Project" in query:
            return MOCK_PROJECT_DATA
        if "ServiceMetrics" in query:
            call_count += 1
            svc_id = variables.get("serviceId")
            if svc_id == "svc-1":
                raise RuntimeError("timeout for web")
            return MOCK_METRICS_DATA
        return {}

    conn._graphql = mock_graphql
    metrics = await conn.collect_metrics()

    # svc-2 (worker) should still have metrics even though svc-1 (web) failed
    cpu = [m for m in metrics if m.metric_name == "service_cpu_usage"]
    assert len(cpu) > 0
    assert cpu[0].labels["service"] == "worker"


async def test_production_env_preferred():
    """Hotfix 2 Fix 2: 'production' environment preferred over others."""
    conn = _make_connector()
    env_ids_queried = []

    multi_env_project = {
        "project": {
            "id": "proj-1",
            "name": "my-project",
            "services": {
                "edges": [{"node": {"id": "svc-1", "name": "web"}}]
            },
            "environments": {
                "edges": [
                    {"node": {"id": "env-staging", "name": "staging"}},
                    {"node": {"id": "env-prod", "name": "production"}},
                ]
            },
        }
    }

    async def mock_graphql(query, variables):
        if "Project" in query:
            return multi_env_project
        if "ServiceMetrics" in query:
            env_ids_queried.append(variables.get("environmentId"))
            return MOCK_METRICS_DATA
        return {}

    conn._graphql = mock_graphql
    await conn.collect_metrics()

    # Should have queried with the production environment, not staging
    assert all(eid == "env-prod" for eid in env_ids_queried)


async def test_fallback_to_first_env_when_no_production():
    """Hotfix 2 Fix 2: Falls back to first env when no 'production'."""
    conn = _make_connector()
    env_ids_queried = []

    no_prod_project = {
        "project": {
            "id": "proj-1",
            "name": "my-project",
            "services": {
                "edges": [{"node": {"id": "svc-1", "name": "web"}}]
            },
            "environments": {
                "edges": [
                    {"node": {"id": "env-dev", "name": "development"}},
                    {"node": {"id": "env-stg", "name": "staging"}},
                ]
            },
        }
    }

    async def mock_graphql(query, variables):
        if "Project" in query:
            return no_prod_project
        if "ServiceMetrics" in query:
            env_ids_queried.append(variables.get("environmentId"))
            return MOCK_METRICS_DATA
        return {}

    conn._graphql = mock_graphql
    await conn.collect_metrics()

    # Should fall back to the first environment
    assert len(env_ids_queried) == 1
    assert env_ids_queried[0] in ("env-dev", "env-stg")


async def test_cache_refresh_picks_up_new_service():
    """Hotfix 2 Fix 2: New services appear after cache refresh."""
    conn = _make_connector()
    call_number = [0]

    project_v1 = {
        "project": {
            "id": "proj-1",
            "name": "my-project",
            "services": {
                "edges": [{"node": {"id": "svc-1", "name": "web"}}]
            },
            "environments": {
                "edges": [{"node": {"id": "env-1", "name": "production"}}]
            },
        }
    }
    project_v2 = {
        "project": {
            "id": "proj-1",
            "name": "my-project",
            "services": {
                "edges": [
                    {"node": {"id": "svc-1", "name": "web"}},
                    {"node": {"id": "svc-3", "name": "cron"}},
                ]
            },
            "environments": {
                "edges": [{"node": {"id": "env-1", "name": "production"}}]
            },
        }
    }

    async def mock_graphql(query, variables):
        if "Project" in query:
            call_number[0] += 1
            # First collect_metrics cycle returns v1, second returns v2
            return project_v1 if call_number[0] <= 2 else project_v2
        if "ServiceMetrics" in query:
            return MOCK_METRICS_DATA
        return {}

    conn._graphql = mock_graphql

    # First cycle: only "web"
    metrics1 = await conn.collect_metrics()
    svc_names1 = {m.labels["service"] for m in metrics1 if "service" in m.labels}
    assert "web" in svc_names1
    assert "cron" not in svc_names1

    # Second cycle: "web" + "cron" after cache refresh
    metrics2 = await conn.collect_metrics()
    svc_names2 = {m.labels["service"] for m in metrics2 if "service" in m.labels}
    assert "web" in svc_names2
    assert "cron" in svc_names2


MOCK_PROMETHEUS_TEXT = """\
# TYPE service_cpu_usage gauge
service_cpu_usage{service="web"} 0.75
service_cpu_usage{service="worker"} 0.30
# TYPE service_memory_bytes gauge
service_memory_bytes{service="web"} 268435456
"""


async def test_prometheus_endpoint_scraping():
    conn = _make_connector(options={"prometheus_endpoint": "http://prom:9090/metrics"})

    async def mock_graphql(query, variables):
        if "Project" in query:
            return MOCK_PROJECT_DATA
        return {"metrics": []}

    conn._graphql = mock_graphql

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = MOCK_PROMETHEUS_TEXT
    mock_resp.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_resp)
    conn._prom_client = mock_client

    metrics = await conn.collect_metrics()
    prom_metrics = [m for m in metrics if "cpu_usage" in m.metric_name or "memory" in m.metric_name]
    assert len(prom_metrics) > 0
    assert any(m.labels.get("service") == "web" for m in prom_metrics)


async def test_prometheus_endpoint_unreachable():
    conn = _make_connector(options={"prometheus_endpoint": "http://prom:9090/metrics"})

    async def mock_graphql(query, variables):
        if "Project" in query:
            return MOCK_PROJECT_DATA
        return {"metrics": []}

    conn._graphql = mock_graphql

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(side_effect=Exception("Connection refused"))
    conn._prom_client = mock_client

    # Should not raise
    metrics = await conn.collect_metrics()
    # Only service_count should be present
    svc_count = [m for m in metrics if m.metric_name == "service_count"]
    assert len(svc_count) == 1


# ---------- _graphql() retry policy tests ----------


def _mock_resp(status_code: int, body_text: str = "", json_payload=None):
    """Build a MagicMock httpx Response with the given status + body."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = body_text
    resp.request = MagicMock()
    resp.response = resp
    if json_payload is not None:
        resp.json = MagicMock(return_value=json_payload)
    else:
        resp.json = MagicMock(return_value={})
    # raise_for_status behaves like httpx: raises on 4xx/5xx
    def _rfs():
        if status_code >= 400:
            import httpx as _httpx
            raise _httpx.HTTPStatusError(
                f"HTTP {status_code}", request=resp.request, response=resp,
            )
    resp.raise_for_status = _rfs
    return resp


async def test_graphql_400_raises_permanent_without_retry():
    """Fix 3: HTTP 400 must raise PermanentGraphQLError on the first attempt,
    with zero retries and zero backoff sleeps."""
    conn = _make_connector()
    call_count = 0

    mock_resp = _mock_resp(
        400,
        body_text='{"errors":[{"message":"Cannot represent null for non-null DateTime"}]}',
    )

    async def fake_post(_path, json):
        nonlocal call_count
        call_count += 1
        return mock_resp

    mock_client = MagicMock()
    mock_client.post = fake_post
    conn._client = mock_client

    with pytest.raises(PermanentGraphQLError) as exc_info:
        await conn._graphql("query {}", {})
    assert call_count == 1  # zero retries
    assert "400" in str(exc_info.value)


async def test_graphql_400_logs_response_body(caplog):
    """Fix 2: 4xx response body is logged at INFO for diagnosability."""
    import logging
    conn = _make_connector()
    body = (
        '{"errors":[{"message":"Cannot represent null for non-null DateTime"}]}'
    )
    mock_resp = _mock_resp(400, body_text=body)

    async def fake_post(_path, json):
        return mock_resp

    mock_client = MagicMock()
    mock_client.post = fake_post
    conn._client = mock_client

    with (
        caplog.at_level(logging.INFO, logger="observibot.connectors.railway"),
        pytest.raises(PermanentGraphQLError),
    ):
        await conn._graphql("query {}", {})

    # Body content must appear in the log record so operators can diagnose
    # schema drift without attaching a debugger.
    assert any(
        "Cannot represent null" in record.message
        for record in caplog.records
    )


async def test_graphql_401_raises_permanent_without_retry():
    """4xx other than 429 are all permanent — 401 (bad token) included."""
    conn = _make_connector()
    call_count = 0
    mock_resp = _mock_resp(401, body_text='{"message":"Unauthorized"}')

    async def fake_post(_path, json):
        nonlocal call_count
        call_count += 1
        return mock_resp

    mock_client = MagicMock()
    mock_client.post = fake_post
    conn._client = mock_client

    with pytest.raises(PermanentGraphQLError):
        await conn._graphql("query {}", {})
    assert call_count == 1


async def test_graphql_429_retries(monkeypatch):
    """429 Too Many Requests IS retryable — should exhaust retries, not raise
    PermanentGraphQLError."""
    conn = _make_connector(max_retries=3)
    call_count = 0
    mock_resp = _mock_resp(429, body_text="rate limited")

    async def fake_post(_path, json):
        nonlocal call_count
        call_count += 1
        return mock_resp

    # Patch asyncio.sleep so the test doesn't actually wait 1+2+4 seconds
    import asyncio as _asyncio
    sleep_calls: list[float] = []

    async def fake_sleep(secs):
        sleep_calls.append(secs)

    monkeypatch.setattr(_asyncio, "sleep", fake_sleep)

    mock_client = MagicMock()
    mock_client.post = fake_post
    conn._client = mock_client

    with pytest.raises(Exception) as exc_info:
        await conn._graphql("query {}", {})
    # Should NOT be PermanentGraphQLError — 429 is retryable.
    assert not isinstance(exc_info.value, PermanentGraphQLError)
    assert call_count == 3  # all retries exhausted
    assert sleep_calls == [1, 2, 4]  # exponential backoff


async def test_graphql_500_retries(monkeypatch):
    """5xx server errors ARE retryable (transient backend)."""
    conn = _make_connector(max_retries=3)
    call_count = 0
    mock_resp = _mock_resp(503, body_text="service unavailable")

    async def fake_post(_path, json):
        nonlocal call_count
        call_count += 1
        return mock_resp

    import asyncio as _asyncio

    async def fake_sleep(secs):
        return None

    monkeypatch.setattr(_asyncio, "sleep", fake_sleep)

    mock_client = MagicMock()
    mock_client.post = fake_post
    conn._client = mock_client

    with pytest.raises(Exception) as exc_info:
        await conn._graphql("query {}", {})
    assert not isinstance(exc_info.value, PermanentGraphQLError)
    assert call_count == 3


async def test_graphql_schema_error_raises_permanent():
    """GraphQL response errors matching schema keywords raise PermanentGraphQLError."""
    conn = _make_connector()
    call_count = 0
    mock_resp = _mock_resp(
        200,
        json_payload={"errors": [{"message": "Cannot query field \"metrics\" on type Query"}]},
    )

    async def fake_post(_path, json):
        nonlocal call_count
        call_count += 1
        return mock_resp

    mock_client = MagicMock()
    mock_client.post = fake_post
    conn._client = mock_client

    with pytest.raises(PermanentGraphQLError):
        await conn._graphql("query {}", {})
    assert call_count == 1


async def test_service_metrics_query_includes_start_date():
    """Fix 1: ServiceMetrics query must pass a non-null startDate variable."""
    conn = _make_connector()
    captured_vars: dict = {}

    async def mock_graphql(query, variables):
        if "Project" in query:
            return MOCK_PROJECT_DATA
        if "ServiceMetrics" in query:
            captured_vars.update(variables)
            return MOCK_METRICS_DATA
        return {}

    conn._graphql = mock_graphql
    await conn.collect_metrics()

    # Variable must be set and must be an ISO-8601 timestamp, not null
    assert "startDate" in captured_vars
    assert captured_vars["startDate"] is not None
    # RFC3339 format: 2026-04-13T12:00:00Z
    assert captured_vars["startDate"].endswith("Z")
    assert "T" in captured_vars["startDate"]


def test_service_metrics_query_has_correct_shape():
    """Fix 1: query string must match current Railway schema.

    - No outer 'measurements { ... }' wrapper
    - Uses 'measurement' (singular) field on MetricsResult
    - Declares $startDate: DateTime! variable (non-null)
    - Does NOT contain the old 'startDate: null' literal
    """
    from observibot.connectors.railway import SERVICE_METRICS_QUERY

    assert "$startDate: DateTime!" in SERVICE_METRICS_QUERY
    assert "startDate: null" not in SERVICE_METRICS_QUERY
    assert "measurement\n" in SERVICE_METRICS_QUERY or "measurement " in SERVICE_METRICS_QUERY
    # Old wrapper shape should be gone
    assert "measurements { metric values" not in SERVICE_METRICS_QUERY
