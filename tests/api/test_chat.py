"""Tests for the chat endpoint and text-to-SQL pipeline."""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from observibot.api.app import create_app
from observibot.api.deps import set_store
from observibot.core.models import MetricSnapshot
from observibot.core.store import Store

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def chat_client(tmp_path: Path):
    db_path = tmp_path / "chat_test.db"
    async with Store(db_path) as store:
        set_store(store)
        for i in range(5):
            await store.save_metric(MetricSnapshot(
                connector_name="test",
                metric_name="cpu",
                value=float(50 + i),
                collected_at=datetime.now(UTC),
            ))

        app = create_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            await client.post(
                "/api/auth/register",
                json={"email": "admin@test.com", "password": "pass123"},
            )
            yield client, store


async def test_chat_returns_results(chat_client):
    client, _ = chat_client
    resp = await client.post(
        "/api/chat/query",
        json={"question": "Show me recent metrics"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["sql_query"] is not None
    assert "metric_snapshots" in data["sql_query"]
    assert data["widget_plan"] is not None


async def test_chat_returns_widget_plan(chat_client):
    client, _ = chat_client
    resp = await client.post(
        "/api/chat/query",
        json={"question": "Show latest metrics"},
    )
    data = resp.json()
    plan = data["widget_plan"]
    assert plan["widget_type"] in ("time_series", "table", "kpi_number", "categorical_bar")
    assert "data" in plan


async def test_chat_cache_hit(chat_client):
    client, _ = chat_client
    resp1 = await client.post(
        "/api/chat/query",
        json={"question": "Show recent metrics"},
    )
    resp2 = await client.post(
        "/api/chat/query",
        json={"question": "Show recent metrics"},
    )
    assert resp1.status_code == 200
    assert resp2.status_code == 200
    assert "cached" in resp2.json()["answer"]


async def test_chat_insights_query(chat_client):
    client, _ = chat_client
    resp = await client.post(
        "/api/chat/query",
        json={"question": "Show me the latest insights"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "insights" in data["sql_query"]


async def test_chat_cost_query(chat_client):
    client, _ = chat_client
    resp = await client.post(
        "/api/chat/query",
        json={"question": "Show LLM usage and cost"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "llm_usage" in data["sql_query"]
