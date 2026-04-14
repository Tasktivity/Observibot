"""Tests for insight feedback API endpoint and store methods."""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from observibot.api.app import create_app
from observibot.api.deps import set_store
from observibot.core.models import Insight
from observibot.core.store import Store

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def app_client(tmp_path: Path):
    db_path = tmp_path / "feedback_test.db"
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


async def _seed_insight(store: Store, insight_id: str = "ins-001") -> Insight:
    insight = Insight(
        id=insight_id,
        severity="warning",
        title="High CPU usage",
        summary="CPU usage exceeded 90%",
        source="anomaly",
        confidence=0.85,
        created_at=datetime.now(UTC),
    )
    await store.save_insight(insight)
    return insight


# ---------- store-level tests ----------


async def test_record_and_get_feedback(tmp_path: Path):
    async with Store(tmp_path / "fb.db") as store:
        record = await store.record_insight_feedback(
            insight_id="ins-001",
            user_id="user-1",
            outcome="actionable",
            note="This was a real issue",
        )
        assert record["outcome"] == "actionable"
        assert record["insight_id"] == "ins-001"
        assert record["id"] is not None

        feedback = await store.get_insight_feedback("ins-001")
        assert len(feedback) == 1
        assert feedback[0]["outcome"] == "actionable"
        assert feedback[0]["note"] == "This was a real issue"


async def test_multiple_feedback_entries(tmp_path: Path):
    async with Store(tmp_path / "fb2.db") as store:
        await store.record_insight_feedback("ins-001", "user-1", "investigating")
        await store.record_insight_feedback("ins-001", "user-1", "resolved", "Fixed the leak")

        feedback = await store.get_insight_feedback("ins-001")
        assert len(feedback) == 2
        assert feedback[0]["outcome"] == "investigating"
        assert feedback[1]["outcome"] == "resolved"


async def test_feedback_summary(tmp_path: Path):
    async with Store(tmp_path / "fb3.db") as store:
        await store.record_insight_feedback("ins-001", "user-1", "noise")
        await store.record_insight_feedback("ins-002", "user-1", "noise")
        await store.record_insight_feedback("ins-003", "user-1", "actionable")

        summary = await store.get_feedback_summary()
        by_outcome = {s["outcome"]: s["count"] for s in summary}
        assert by_outcome["noise"] == 2
        assert by_outcome["actionable"] == 1


# ---------- API-level tests ----------


async def test_feedback_endpoint_success(app_client):
    client, store = app_client
    await _register_and_login(client)
    await _seed_insight(store)

    resp = await client.post(
        "/api/insights/ins-001/feedback",
        json={"outcome": "actionable", "note": "confirmed real issue"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["outcome"] == "actionable"
    assert data["insight_id"] == "ins-001"
    assert data["note"] == "confirmed real issue"


async def test_feedback_invalid_outcome_rejected(app_client):
    client, store = app_client
    await _register_and_login(client)
    await _seed_insight(store)

    resp = await client.post(
        "/api/insights/ins-001/feedback",
        json={"outcome": "invalid_value"},
    )
    assert resp.status_code == 422


async def test_feedback_requires_auth(app_client):
    client, store = app_client
    await _seed_insight(store)

    resp = await client.post(
        "/api/insights/ins-001/feedback",
        json={"outcome": "noise"},
    )
    assert resp.status_code == 401


async def test_multiple_feedback_via_api(app_client):
    client, store = app_client
    await _register_and_login(client)
    await _seed_insight(store)

    await client.post(
        "/api/insights/ins-001/feedback",
        json={"outcome": "investigating"},
    )
    resp = await client.post(
        "/api/insights/ins-001/feedback",
        json={"outcome": "resolved", "note": "Fixed"},
    )
    assert resp.status_code == 200

    feedback = await store.get_insight_feedback("ins-001")
    assert len(feedback) == 2


async def test_feedback_nonexistent_insight_returns_404(app_client):
    client, _ = app_client
    await _register_and_login(client)

    resp = await client.post(
        "/api/insights/nonexistent-id/feedback",
        json={"outcome": "noise"},
    )
    assert resp.status_code == 404


async def test_feedback_id_is_valid_integer(app_client):
    client, store = app_client
    await _register_and_login(client)
    await _seed_insight(store)

    resp = await client.post(
        "/api/insights/ins-001/feedback",
        json={"outcome": "actionable"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data["id"], int)
    assert data["id"] > 0


async def test_feedback_user_id_populated(app_client):
    """Fix 1: user_id must come from user['id'], not user.get('sub')."""
    client, store = app_client
    await _register_and_login(client)
    await _seed_insight(store)

    resp = await client.post(
        "/api/insights/ins-001/feedback",
        json={"outcome": "actionable"},
    )
    assert resp.status_code == 200
    data = resp.json()
    # user_id must not be None or empty
    assert data["user_id"] is not None
    assert data["user_id"] != ""
