"""Tests for the /api/diagnostics/recent observability endpoint.

Verifies zero-state, aggregated counters, parsed run summaries, and the
auth gate. The actual Step 3.4 diagnostic events are emitted by the
monitor; here we seed events directly to isolate API behavior.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from observibot.api.app import create_app
from observibot.api.deps import set_store
from observibot.core.store import Store

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def api_client(tmp_path: Path):
    db_path = tmp_path / "diag_api.db"
    async with Store(db_path) as store:
        set_store(store)
        app = create_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport, base_url="http://test",
        ) as client:
            yield client, store


async def _register(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/auth/register",
        json={"email": "admin@test.com", "password": "pw-for-tests"},
    )
    assert resp.status_code == 200


async def test_diagnostics_endpoint_returns_zero_state(api_client) -> None:
    client, _ = api_client
    await _register(client)
    resp = await client.get("/api/diagnostics/recent")
    assert resp.status_code == 200
    body = resp.json()
    assert body["last_24h"] == {
        "runs": 0,
        "skipped_cooldown": 0,
        "timed_out": 0,
        "queries_issued": 0,
        "queries_succeeded": 0,
        "queries_rejected": 0,
    }
    assert body["recent_runs"] == []


async def test_diagnostics_endpoint_aggregates_events(api_client) -> None:
    client, store = api_client
    await _register(client)
    # Seed three representative events.
    await store.emit_event(
        event_type="diagnostic_run", source="monitor_loop",
        subject="analysis_cycle", ref_table="monitor_runs",
        ref_id="run-1", severity="info",
        summary="3 diagnostic(s): 2 succeeded, 1 rejected/errored",
        run_id="run-1",
    )
    await store.emit_event(
        event_type="diagnostic_skipped", source="monitor_loop",
        subject="analysis_cycle", ref_table="monitor_runs",
        ref_id="run-2", severity="info",
        summary="diagnostic cooldown active (sig=abcdef01)",
        run_id="run-2",
    )
    await store.emit_event(
        event_type="diagnostic_timeout", source="monitor_loop",
        subject="analysis_cycle", ref_table="monitor_runs",
        ref_id="run-3", severity="warning",
        summary="diagnostic phase timed out", run_id="run-3",
    )

    resp = await client.get("/api/diagnostics/recent")
    assert resp.status_code == 200
    body = resp.json()
    assert body["last_24h"]["runs"] == 1
    assert body["last_24h"]["skipped_cooldown"] == 1
    assert body["last_24h"]["timed_out"] == 1
    assert body["last_24h"]["queries_issued"] == 3
    assert body["last_24h"]["queries_succeeded"] == 2
    assert body["last_24h"]["queries_rejected"] == 1
    assert len(body["recent_runs"]) == 3
    run_ids = {r["run_id"] for r in body["recent_runs"]}
    assert run_ids == {"run-1", "run-2", "run-3"}


async def test_diagnostics_endpoint_requires_auth(api_client) -> None:
    client, _ = api_client
    resp = await client.get("/api/diagnostics/recent")
    assert resp.status_code == 401
