"""Tests for the agentic chat pipeline."""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from observibot.agent.analyzer import Analyzer
from observibot.agent.llm_provider import MockProvider
from observibot.api.app import create_app
from observibot.api.deps import set_analyzer, set_store
from observibot.core.models import MetricSnapshot
from observibot.core.store import Store

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def chat_client(tmp_path: Path):
    """Client WITHOUT LLM — uses deterministic fallback."""
    db_path = tmp_path / "chat_test.db"
    async with Store(db_path) as store:
        set_store(store)
        set_analyzer(None)
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


@pytest.fixture
async def chat_client_with_llm(tmp_path: Path):
    """Client WITH mock LLM — uses agentic pipeline."""
    db_path = tmp_path / "chat_llm_test.db"
    async with Store(db_path) as store:
        set_store(store)
        provider = MockProvider(model="mock-model")
        analyzer = Analyzer(provider=provider, store=store)
        set_analyzer(analyzer)
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
        set_analyzer(None)


async def test_fallback_returns_narrative(chat_client):
    """Without LLM, fallback returns narrative not 'Found N results.'"""
    client, _ = chat_client
    resp = await client.post(
        "/api/chat/query",
        json={"question": "Show me recent metrics"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "Found" not in data["answer"] or "monitoring records" in data["answer"]
    assert "observability" in data["domains_hit"]


async def test_fallback_warns_about_no_llm(chat_client):
    client, _ = chat_client
    resp = await client.post(
        "/api/chat/query",
        json={"question": "Show me recent metrics"},
    )
    data = resp.json()
    assert any("LLM" in w for w in data.get("warnings", []))


async def test_fallback_insights_query(chat_client):
    client, _ = chat_client
    resp = await client.post(
        "/api/chat/query",
        json={"question": "Show me the latest insights"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["sql_query"] is not None
    assert "insights" in data["sql_query"]


async def test_fallback_cost_query(chat_client):
    client, _ = chat_client
    resp = await client.post(
        "/api/chat/query",
        json={"question": "Show LLM usage and cost"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "llm_usage" in data["sql_query"]


async def test_agentic_pipeline_with_llm(chat_client_with_llm):
    """With LLM, uses agentic tool-calling pipeline."""
    client, _ = chat_client_with_llm
    resp = await client.post(
        "/api/chat/query",
        json={"question": "What do my metrics look like?"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["answer"]
    assert "Found" not in data["answer"] or "records" not in data["answer"]
    assert data["domains_hit"]


async def test_agentic_returns_widget_plan(chat_client_with_llm):
    client, _ = chat_client_with_llm
    resp = await client.post(
        "/api/chat/query",
        json={"question": "Show metrics over time"},
    )
    data = resp.json()
    assert data["widget_plan"] is not None
    plan = data["widget_plan"]
    assert "widget_type" in plan


async def test_agentic_returns_sql(chat_client_with_llm):
    client, _ = chat_client_with_llm
    resp = await client.post(
        "/api/chat/query",
        json={"question": "Recent metrics"},
    )
    data = resp.json()
    assert data["sql_query"] is not None
    assert "metric_snapshots" in data["sql_query"]


async def test_agentic_domains_populated(chat_client_with_llm):
    client, _ = chat_client_with_llm
    resp = await client.post(
        "/api/chat/query",
        json={"question": "Show recent metrics"},
    )
    data = resp.json()
    assert len(data["domains_hit"]) > 0


async def test_agentic_bad_llm_falls_back(tmp_path: Path):
    """If LLM throws, falls back to deterministic."""
    from observibot.agent.llm_provider import LLMSoftError

    class FailingProvider(MockProvider):
        async def _call(self, system_prompt, user_prompt):
            raise LLMSoftError("test failure")

    db_path = tmp_path / "chat_fail.db"
    async with Store(db_path) as store:
        set_store(store)
        provider = FailingProvider(model="mock")
        analyzer = Analyzer(provider=provider, store=store)
        set_analyzer(analyzer)
        app = create_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            await client.post(
                "/api/auth/register",
                json={"email": "a@test.com", "password": "pass"},
            )
            resp = await client.post(
                "/api/chat/query",
                json={"question": "Show metrics"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["answer"]
            assert "observability" in data["domains_hit"]
        set_analyzer(None)


async def test_empty_results_narrative(chat_client):
    """Empty results get human-readable message, not 'Found 0 results.'"""
    client, _ = chat_client
    resp = await client.post(
        "/api/chat/query",
        json={"question": "Show me baselines"},
    )
    data = resp.json()
    assert "Found 0" not in data["answer"]
