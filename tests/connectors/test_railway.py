from __future__ import annotations

import pytest

from observibot.connectors.railway import RailwayConnector

pytestmark = pytest.mark.asyncio


async def test_railway_missing_credentials_graceful() -> None:
    conn = RailwayConnector(name="r", config={})
    frag = await conn.discover()
    assert frag.errors  # records a friendly error rather than crashing

    metrics = await conn.collect_metrics()
    assert metrics == []


async def test_railway_parses_timestamp() -> None:
    conn = RailwayConnector(name="r", config={"api_token": "x", "project_id": "p"})
    dt = conn._parse_timestamp("2026-04-10T12:00:00Z")
    assert dt is not None
    assert dt.tzinfo is not None


async def test_railway_health_check_without_credentials_returns_unhealthy() -> None:
    conn = RailwayConnector(name="r", config={})
    health = await conn.health_check()
    assert health.healthy is False
