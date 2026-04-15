"""Tests for Layer 4 (EXPLAIN cost gating) of the SQL sandbox."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from observibot.core.sql_sandbox import (
    DEFAULT_EXPLAIN_COST_THRESHOLD,
    explain_check,
)


class _FakeAsyncContextManager:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *_):
        return False


class _FakeResult:
    def __init__(self, plan):
        self._plan = plan

    def scalar(self):
        return self._plan


def _fake_engine_with_plan(plan, url="postgresql+asyncpg://x/y"):
    """Build a mock AsyncEngine whose begin() yields a conn returning ``plan``."""
    conn = MagicMock()
    conn.execute = AsyncMock(return_value=_FakeResult(plan))
    engine = MagicMock()
    engine.url = url
    engine.begin = MagicMock(return_value=_FakeAsyncContextManager(conn))
    return engine


def _plan(total_cost: float) -> str:
    return json.dumps([{"Plan": {"Total Cost": total_cost, "Node Type": "Seq Scan"}}])


async def test_explain_accepts_cheap_query():
    engine = _fake_engine_with_plan(_plan(50.0))
    ok, cost = await explain_check(engine, "SELECT 1", cost_threshold=100.0)
    assert ok is True
    assert cost == pytest.approx(50.0)


async def test_explain_rejects_expensive_query():
    engine = _fake_engine_with_plan(_plan(250_000.0))
    ok, cost = await explain_check(
        engine, "SELECT * FROM metric_snapshots", cost_threshold=100_000.0,
    )
    assert ok is False
    assert cost == pytest.approx(250_000.0)


async def test_explain_uses_default_threshold():
    engine = _fake_engine_with_plan(_plan(50_000.0))
    ok, cost = await explain_check(engine, "SELECT 1")
    # Default is 100_000; 50k is under it.
    assert ok is True
    assert cost == pytest.approx(50_000.0)
    # Assert the threshold we'd reject at for regression safety.
    assert DEFAULT_EXPLAIN_COST_THRESHOLD == 100_000.0


async def test_explain_short_circuits_on_sqlite():
    engine = MagicMock()
    engine.url = "sqlite+aiosqlite:///:memory:"
    # If the engine is used at all, this mock will fail — proves no call.
    engine.begin = MagicMock(side_effect=AssertionError("should not be called"))
    ok, cost = await explain_check(engine, "SELECT 1")
    assert ok is True
    assert cost == 0.0


async def test_explain_fails_open_on_error():
    """If EXPLAIN itself raises, the query should NOT be blocked."""
    engine = MagicMock()
    engine.url = "postgresql://x/y"
    engine.begin = MagicMock(side_effect=RuntimeError("connection lost"))
    ok, cost = await explain_check(engine, "SELECT 1")
    assert ok is True  # fail open
    assert cost == 0.0


async def test_explain_handles_list_plan_structure():
    """Postgres returns plan already parsed in some drivers — accept list."""
    engine = _fake_engine_with_plan(
        [{"Plan": {"Total Cost": 75.0, "Node Type": "Index Scan"}}],
    )
    ok, cost = await explain_check(engine, "SELECT 1", cost_threshold=100.0)
    assert ok is True
    assert cost == pytest.approx(75.0)


async def test_explain_handles_malformed_plan():
    """Unexpected plan shape fails open, does not crash."""
    engine = _fake_engine_with_plan("not json {{")
    ok, cost = await explain_check(engine, "SELECT 1")
    assert ok is True
    assert cost == 0.0


async def test_explain_handles_missing_total_cost():
    engine = _fake_engine_with_plan(json.dumps([{"Plan": {}}]))
    ok, cost = await explain_check(engine, "SELECT 1")
    assert ok is True
    assert cost == 0.0


async def test_explain_callable_runner():
    """The callable form (for custom pools like AppDatabasePool)."""
    async def runner(sql: str):
        assert sql.startswith("EXPLAIN")
        return _plan(200_000.0)

    ok, cost = await explain_check(runner, "SELECT 1", cost_threshold=100_000.0)
    assert ok is False
    assert cost == pytest.approx(200_000.0)


async def test_explain_callable_fails_open():
    async def runner(_sql: str):
        raise RuntimeError("pool exhausted")

    ok, cost = await explain_check(runner, "SELECT 1")
    assert ok is True
    assert cost == 0.0


async def test_explain_engine_failure_logs_warning(caplog):
    """Layer 4 silently disabling itself used to hide behind DEBUG logs.

    H8: promote EXPLAIN failures to WARNING so an ops dashboard notices when
    infrastructure hiccups quietly break query cost-gating.
    """
    import logging

    engine = MagicMock()
    engine.url = "postgresql://x/y"
    engine.begin = MagicMock(side_effect=RuntimeError("connection lost"))

    with caplog.at_level(logging.WARNING, logger="observibot.core.sql_sandbox"):
        ok, _ = await explain_check(engine, "SELECT 1")
        assert ok is True
    assert any(
        r.levelno >= logging.WARNING and "EXPLAIN" in r.getMessage()
        for r in caplog.records
    ), f"expected WARNING-level EXPLAIN log; got {[r.getMessage() for r in caplog.records]}"


async def test_explain_callable_failure_logs_warning(caplog):
    import logging

    async def runner(_sql: str):
        raise RuntimeError("pool exhausted")

    with caplog.at_level(logging.WARNING, logger="observibot.core.sql_sandbox"):
        ok, _ = await explain_check(runner, "SELECT 1")
        assert ok is True
    assert any(
        r.levelno >= logging.WARNING and "EXPLAIN" in r.getMessage()
        for r in caplog.records
    )
