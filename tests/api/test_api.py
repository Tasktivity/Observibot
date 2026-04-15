"""Tests for the FastAPI REST API."""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from observibot.api.app import create_app
from observibot.api.deps import set_store
from observibot.core.models import Insight, MetricSnapshot
from observibot.core.store import Store

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def app_client(tmp_path: Path):
    db_path = tmp_path / "api_test.db"
    async with Store(db_path) as store:
        set_store(store)
        app = create_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client, store


async def _register_and_login(client: AsyncClient) -> dict:
    resp = await client.post(
        "/api/auth/register",
        json={"email": "admin@test.com", "password": "testpass123"},
    )
    assert resp.status_code == 200
    return resp.cookies


async def test_health_endpoint(app_client):
    client, _ = app_client
    resp = await client.get("/api/system/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"


async def test_unauthenticated_access_blocked(app_client):
    client, _ = app_client
    resp = await client.get("/api/insights")
    assert resp.status_code == 401


async def test_register_and_login_flow(app_client):
    client, _ = app_client
    resp = await client.post(
        "/api/auth/register",
        json={"email": "admin@test.com", "password": "testpass123"},
    )
    assert resp.status_code == 200
    assert resp.json()["email"] == "admin@test.com"
    assert "access_token" in resp.cookies

    resp = await client.post(
        "/api/auth/register",
        json={"email": "second@test.com", "password": "pass"},
    )
    assert resp.status_code == 403

    resp = await client.get("/api/auth/me")
    assert resp.status_code == 200
    assert resp.json()["email"] == "admin@test.com"


async def test_login_invalid_credentials(app_client):
    client, _ = app_client
    await _register_and_login(client)
    resp = await client.post(
        "/api/auth/login",
        json={"email": "admin@test.com", "password": "wrong"},
    )
    assert resp.status_code == 401


async def test_logout_clears_cookie(app_client):
    client, _ = app_client
    await _register_and_login(client)
    resp = await client.post("/api/auth/logout")
    assert resp.status_code == 200
    resp = await client.get("/api/auth/me")
    assert resp.status_code == 401


async def test_insights_endpoint(app_client):
    client, store = app_client
    await _register_and_login(client)
    insight = Insight(title="Test", summary="Test insight", severity="warning")
    await store.save_insight(insight)

    resp = await client.get("/api/insights")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["title"] == "Test"


async def test_metrics_endpoint(app_client):
    client, store = app_client
    await _register_and_login(client)
    metric = MetricSnapshot(
        connector_name="test",
        metric_name="cpu",
        value=42.0,
        collected_at=datetime.now(UTC),
    )
    await store.save_metric(metric)

    resp = await client.get("/api/metrics/recent")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["metric_name"] == "cpu"


async def test_metric_history_endpoint(app_client):
    client, store = app_client
    await _register_and_login(client)
    metric = MetricSnapshot(
        connector_name="test",
        metric_name="cpu",
        value=42.0,
        collected_at=datetime.now(UTC),
    )
    await store.save_metric(metric)

    resp = await client.get("/api/metrics/cpu/history")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1


async def test_system_status(app_client):
    client, _ = app_client
    await _register_and_login(client)
    resp = await client.get("/api/system/status")
    assert resp.status_code == 200


async def test_system_cost(app_client):
    client, store = app_client
    await _register_and_login(client)
    await store.record_llm_usage("mock", "m", 100, 50, 0.01, "test")

    resp = await client.get("/api/system/cost")
    assert resp.status_code == 200
    data = resp.json()
    assert data["calls"] == 1


async def test_discovery_model(app_client):
    client, _ = app_client
    await _register_and_login(client)
    resp = await client.get("/api/discovery/model")
    assert resp.status_code == 200
    data = resp.json()
    assert data["tables"] == 0


async def test_widget_crud(app_client):
    client, _ = app_client
    await _register_and_login(client)

    resp = await client.post(
        "/api/widgets",
        json={"widget_type": "kpi_number", "title": "CPU"},
    )
    assert resp.status_code == 201
    widget = resp.json()
    widget_id = widget["id"]
    assert widget["widget_type"] == "kpi_number"

    resp = await client.get("/api/widgets")
    assert resp.status_code == 200
    assert len(resp.json()) == 1

    resp = await client.patch(
        f"/api/widgets/{widget_id}",
        json={"title": "CPU Usage"},
    )
    assert resp.status_code == 200
    assert resp.json()["title"] == "CPU Usage"

    resp = await client.delete(f"/api/widgets/{widget_id}")
    assert resp.status_code == 204

    resp = await client.get("/api/widgets")
    assert len(resp.json()) == 0


async def test_widget_not_found(app_client):
    client, _ = app_client
    await _register_and_login(client)
    resp = await client.patch(
        "/api/widgets/nonexistent",
        json={"title": "x"},
    )
    assert resp.status_code == 404


async def test_batch_layout_update(app_client):
    client, _ = app_client
    await _register_and_login(client)

    resp = await client.post(
        "/api/widgets",
        json={"widget_type": "kpi_number", "title": "W1"},
    )
    w1_id = resp.json()["id"]

    resp = await client.patch(
        "/api/widgets/layout",
        json={"items": [{"id": w1_id, "x": 0, "y": 0, "w": 4, "h": 3}]},
    )
    assert resp.status_code == 200
    assert resp.json()["updated"] == 1


async def test_chat_query_returns_results(app_client):
    client, _ = app_client
    await _register_and_login(client)
    resp = await client.post(
        "/api/chat/query",
        json={"question": "Show recent metrics"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["sql_query"] is not None


async def test_insight_acknowledge(app_client):
    client, store = app_client
    await _register_and_login(client)

    # Ack for a non-existent insight should 404 now that we persist.
    resp = await client.patch("/api/insights/does-not-exist/ack")
    assert resp.status_code == 404

    insight = Insight(
        id="ins-ack-1",
        severity="warning",
        title="ack test",
        summary="x",
        source="anomaly",
        confidence=0.5,
        created_at=datetime.now(UTC),
    )
    await store.save_insight(insight)

    resp = await client.patch(f"/api/insights/{insight.id}/ack")
    assert resp.status_code == 200
    body = resp.json()
    assert body["acknowledged"] is True
    assert body["idempotent"] is False

    fb = await store.get_insight_feedback(insight.id)
    assert any(f["outcome"] == "acknowledged" for f in fb)

    events = await store.get_events(limit=50)
    assert any(
        e["event_type"] == "feedback"
        and e["subject"] == insight.id
        and "acknowledged" in (e.get("summary") or "").lower()
        for e in events
    )


async def test_insight_acknowledge_is_idempotent(app_client):
    client, store = app_client
    await _register_and_login(client)
    insight = Insight(
        id="ins-ack-2",
        severity="warning",
        title="ack idem",
        summary="x",
        source="anomaly",
        confidence=0.5,
        created_at=datetime.now(UTC),
    )
    await store.save_insight(insight)

    r1 = await client.patch(f"/api/insights/{insight.id}/ack")
    r2 = await client.patch(f"/api/insights/{insight.id}/ack")
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r2.json()["idempotent"] is True

    fb = await store.get_insight_feedback(insight.id)
    ack_rows = [f for f in fb if f["outcome"] == "acknowledged"]
    assert len(ack_rows) == 1


async def test_openapi_docs_available(app_client):
    client, _ = app_client
    resp = await client.get("/api/openapi.json")
    assert resp.status_code == 200
    assert "paths" in resp.json()
