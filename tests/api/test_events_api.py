"""Tests for the events API endpoints."""
from __future__ import annotations

from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from observibot.api.app import create_app
from observibot.api.deps import set_store
from observibot.core.store import Store

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def app_client(tmp_path: Path):
    db_path = tmp_path / "events_api_test.db"
    async with Store(db_path) as store:
        set_store(store)
        app = create_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client, store


async def _register(client: AsyncClient) -> None:
    await client.post(
        "/api/auth/register",
        json={"email": "admin@test.com", "password": "testpass123"},
    )


async def _seed_events(store: Store) -> None:
    await store.emit_event(
        event_type="anomaly", source="monitor_loop",
        subject="db_conn_pool_util", ref_table="metric_snapshots",
        ref_id="snap001", severity="warning",
        summary="db_conn_pool_util exceeded threshold: 92.3",
        run_id="run001",
    )
    await store.emit_event(
        event_type="insight", source="monitor_loop",
        subject="db_conn_pool_util", ref_table="insights",
        ref_id="ins001", severity="warning",
        summary="Connection pool nearing exhaustion",
        run_id="run001",
    )
    await store.emit_event(
        event_type="deploy", source="monitor_loop",
        subject="web", ref_table="change_events",
        ref_id="ce001", severity="info",
        summary="web deploy SUCCESS",
    )


async def test_list_events_requires_auth(app_client):
    client, _ = app_client
    resp = await client.get("/api/events")
    assert resp.status_code == 401


async def test_list_events(app_client):
    client, store = app_client
    await _register(client)
    await _seed_events(store)
    resp = await client.get("/api/events")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 3


async def test_list_events_filter_type(app_client):
    client, store = app_client
    await _register(client)
    await _seed_events(store)
    resp = await client.get("/api/events", params={"event_type": "anomaly"})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["event_type"] == "anomaly"


async def test_list_events_filter_subject(app_client):
    client, store = app_client
    await _register(client)
    await _seed_events(store)
    resp = await client.get("/api/events", params={"subject": "web"})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["subject"] == "web"


async def test_events_for_subject(app_client):
    client, store = app_client
    await _register(client)
    await _seed_events(store)
    resp = await client.get("/api/events/subject/db_conn_pool_util")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    assert all(e["subject"] == "db_conn_pool_util" for e in data)


async def test_subject_recurrence(app_client):
    client, store = app_client
    await _register(client)
    for i in range(3):
        await store.emit_event(
            event_type="anomaly", source="monitor_loop",
            subject="cpu_usage", ref_table="metric_snapshots",
            ref_id=f"s{i}", severity="warning",
            summary=f"CPU spike #{i}",
        )
    resp = await client.get(
        "/api/events/subject/cpu_usage/recurrence",
        params={"event_type": "anomaly"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 3
    assert data["first_seen"] is not None


async def test_subject_recurrence_no_data(app_client):
    client, _ = app_client
    await _register(client)
    resp = await client.get("/api/events/subject/nonexistent/recurrence")
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 0


async def test_search_events(app_client):
    client, store = app_client
    await _register(client)
    await _seed_events(store)
    resp = await client.get("/api/events/search", params={"q": "threshold"})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert "threshold" in data[0]["summary"]


async def test_timeline(app_client):
    client, store = app_client
    await _register(client)
    await _seed_events(store)
    resp = await client.get(
        "/api/events/timeline",
        params={"since": "2020-01-01T00:00:00+00:00"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 3
