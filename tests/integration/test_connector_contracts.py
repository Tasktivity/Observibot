"""Contract tests — validate real external API response shapes.

These tests skip cleanly if credentials aren't present so CI stays green.
Run with::

    pytest tests/integration/ -v

Each test makes ONE real call and asserts that the response contains the
fields the code parses, not specific values. They exist to catch upstream
schema changes (Railway GraphQL schema updates, Supabase metric renames)
that mock-based unit tests cannot detect.
"""
from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import httpx
import pytest

pytestmark = pytest.mark.asyncio

RAILWAY_TOKEN = os.getenv("RAILWAY_API_TOKEN")
RAILWAY_PROJECT = os.getenv("RAILWAY_PROJECT_ID")
SUPABASE_REF = os.getenv("SUPABASE_PROJECT_REF")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_KEY")


@pytest.mark.skipif(
    not RAILWAY_TOKEN or not RAILWAY_PROJECT,
    reason="No Railway credentials in environment",
)
async def test_railway_project_query_shape():
    """PROJECT_QUERY response must contain services.edges and environments.edges."""
    from observibot.connectors.railway import PROJECT_QUERY

    headers = {
        "Authorization": f"Bearer {RAILWAY_TOKEN}",
        "Content-Type": "application/json",
    }
    payload = {"query": PROJECT_QUERY, "variables": {"id": RAILWAY_PROJECT}}
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            "https://backboard.railway.app/graphql/v2",
            json=payload,
            headers=headers,
        )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "errors" not in data, f"GraphQL errors: {data['errors']}"
    project = data["data"]["project"]
    assert "id" in project and "name" in project
    assert "services" in project and "edges" in project["services"]
    assert "environments" in project and "edges" in project["environments"]


@pytest.mark.skipif(
    not RAILWAY_TOKEN or not RAILWAY_PROJECT,
    reason="No Railway credentials in environment",
)
async def test_railway_service_metrics_query_shape():
    """SERVICE_METRICS_QUERY must return list of {measurement, values: [{ts, value}]}."""
    from observibot.connectors.railway import (
        PROJECT_QUERY,
        SERVICE_METRICS_QUERY,
    )

    headers = {
        "Authorization": f"Bearer {RAILWAY_TOKEN}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=15.0) as client:
        # Discover one service and environment.
        proj = await client.post(
            "https://backboard.railway.app/graphql/v2",
            json={"query": PROJECT_QUERY, "variables": {"id": RAILWAY_PROJECT}},
            headers=headers,
        )
        proj_data = proj.json()["data"]["project"]
        services = proj_data["services"]["edges"]
        environments = proj_data["environments"]["edges"]
        if not services or not environments:
            pytest.skip("Project has no services or environments")
        service_id = services[0]["node"]["id"]
        env_id = environments[0]["node"]["id"]
        start = (datetime.now(UTC) - timedelta(hours=1)).isoformat()

        resp = await client.post(
            "https://backboard.railway.app/graphql/v2",
            json={
                "query": SERVICE_METRICS_QUERY,
                "variables": {
                    "serviceId": service_id,
                    "environmentId": env_id,
                    "startDate": start,
                },
            },
            headers=headers,
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "errors" not in body, f"GraphQL errors: {body['errors']}"
    metrics = body["data"]["metrics"]
    assert isinstance(metrics, list)
    if metrics:
        # If the service has any data, validate the inner shape.
        sample = metrics[0]
        assert "measurement" in sample
        assert "values" in sample and isinstance(sample["values"], list)
        if sample["values"]:
            v = sample["values"][0]
            assert "ts" in v and "value" in v


@pytest.mark.skipif(
    not SUPABASE_REF or not SUPABASE_KEY,
    reason="No Supabase credentials in environment",
)
async def test_supabase_metrics_api_shape():
    """Supabase Metrics API returns parseable Prometheus text + node_* metrics."""
    url = f"https://{SUPABASE_REF}.supabase.co/customer/v1/privileged/metrics"
    auth = httpx.BasicAuth("service_role", SUPABASE_KEY)

    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.get(url, auth=auth)

    assert resp.status_code == 200, resp.text[:500]
    text = resp.text
    # Prometheus format: "# HELP" / "# TYPE" comments and metric lines.
    assert "# HELP" in text or "node_" in text, "Response not in Prometheus format"
    # node_cpu_seconds_total is one of the load-bearing metrics our anomaly
    # detector relies on. If it disappears, the anomaly path goes silent.
    assert "node_cpu_seconds_total" in text, "node_cpu_seconds_total missing"
    # Rough family count — they advertise ~200 metric families.
    families = sum(1 for line in text.splitlines() if line.startswith("# HELP"))
    assert families >= 50, f"Only {families} metric families exposed (< 50)"
