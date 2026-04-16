"""Unit tests for the Step 3.4 hypothesis-test loop.

Coverage:
- ``Analyzer.generate_diagnostic_queries`` (Call A) — Pydantic validation,
  empty-schema short-circuit, hard-cap at 3, anomaly-scoped schema
  retrieval, budget enforcement, soft-failure returns [].
- ``Analyzer.execute_diagnostics`` (Call B) — sandbox rejection is
  recorded, not dropped; EXPLAIN fails CLOSED; per-query timeout yields
  error evidence without killing the batch; sensitive-column redaction;
  row cap; single connection reuse across the batch.

Every path exercises a synthetic fixture from
``tests/fixtures/synthetic_schemas.py`` (Tier 0 generality firewall).
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from observibot.agent.analyzer import Analyzer
from observibot.agent.llm_provider import MockProvider
from observibot.agent.schemas import DiagnosticQuery
from observibot.core.config import DiagnosticsConfig
from tests.fixtures.synthetic_schemas import (
    ecommerce_anomaly,
    ecommerce_schema,
    event_stream_anomaly,
    event_stream_schema,
    medical_anomaly,
    medical_records_schema,
)


# ---------------------------------------------------------------------------
# Fake app_db infrastructure
# ---------------------------------------------------------------------------


class _FakeConn:
    """Minimal asyncpg-shaped connection stub for sandbox testing.

    The analyzer only uses ``conn.fetch`` (for SELECT execution) and
    ``conn.fetchrow`` (indirectly via ``_explain_check_fail_closed``).
    Both are overridable per-test so we can simulate timeouts, EXPLAIN
    failures, and structured return rows without a real database.
    """

    def __init__(
        self,
        explain_cost: float = 100.0,
        rows: list[dict] | None = None,
        fetch_impl: Any = None,
        fetchrow_impl: Any = None,
    ) -> None:
        self.explain_cost = explain_cost
        self._rows = rows or [{"count": 1}]
        self._fetch_impl = fetch_impl
        self._fetchrow_impl = fetchrow_impl
        self.fetch_calls: list[str] = []
        self.fetchrow_calls: list[str] = []

    async def fetch(self, sql: str) -> list[Any]:
        self.fetch_calls.append(sql)
        if self._fetch_impl is not None:
            return await self._fetch_impl(sql)
        return list(self._rows)

    async def fetchrow(self, sql: str) -> Any:
        self.fetchrow_calls.append(sql)
        if self._fetchrow_impl is not None:
            return await self._fetchrow_impl(sql)
        # Mimic Postgres EXPLAIN (FORMAT JSON) returning (jsonstr,)
        import json as _json

        return (
            _json.dumps([{"Plan": {"Total Cost": self.explain_cost}}]),
        )


class _FakeAppDb:
    """Implements just enough of ``AppDatabasePool`` for the analyzer's
    diagnostic path: ``is_connected`` and ``acquire()`` context manager.
    Counts how many times ``acquire`` was entered to verify batching.
    """

    def __init__(self, conn: _FakeConn) -> None:
        self._conn = conn
        self.is_connected = True
        self.acquire_count = 0

    @asynccontextmanager
    async def acquire(self):
        self.acquire_count += 1
        yield self._conn


def _default_cfg(**overrides: Any) -> DiagnosticsConfig:
    cfg = DiagnosticsConfig(enabled=True)
    for k, v in overrides.items():
        setattr(cfg, k, v)
    return cfg


# ---------------------------------------------------------------------------
# D3: generate_diagnostic_queries (Call A)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_diagnostic_queries_empty_model_returns_empty(
    tmp_store,
) -> None:
    """With no schema the LLM would hallucinate tables. The analyzer
    must short-circuit *before* burning an LLM call.
    """
    analyzer = Analyzer(provider=MockProvider(), store=tmp_store)
    with patch.object(analyzer.provider, "analyze") as spy_analyze:
        result = await analyzer.generate_diagnostic_queries(
            anomalies=[medical_anomaly()],
            system_model=None,
        )
    assert result == []
    spy_analyze.assert_not_called()


@pytest.mark.asyncio
async def test_generate_diagnostic_queries_truncates_to_three(
    tmp_store,
) -> None:
    """Pydantic enforces max_length=3; the analyzer also defensively
    truncates so a future lib bump cannot quietly raise fan-out.
    """
    # Build 4 queries; Pydantic will raise, analyzer records failure and
    # returns []. To test the defensive truncation specifically, patch
    # Pydantic validation to allow 5 and assert we still return 3.
    canned_queries = [
        {
            "hypothesis": f"h{i}",
            "sql": f"SELECT 1 AS c FROM orders LIMIT {i + 1}",
            "explanation": "",
        }
        for i in range(5)
    ]
    provider = MockProvider(canned={"queries": canned_queries})
    analyzer = Analyzer(provider=provider, store=tmp_store)

    from observibot.agent import analyzer as analyzer_module
    from observibot.agent.schemas import DiagnosticHypothesisResponse

    original_validate = DiagnosticHypothesisResponse.model_validate

    def permissive_validate(data):  # noqa: ANN001
        # Strip max_length by constructing via the inner class directly.
        return DiagnosticHypothesisResponse.model_construct(
            queries=[DiagnosticQuery(**q) for q in data.get("queries", [])]
        )

    with patch.object(
        analyzer_module.DiagnosticHypothesisResponse,
        "model_validate",
        staticmethod(permissive_validate),
    ):
        result = await analyzer.generate_diagnostic_queries(
            anomalies=[ecommerce_anomaly()],
            system_model=ecommerce_schema(),
        )
    assert len(result) == 3
    assert original_validate  # referenced to keep linter happy


@pytest.mark.asyncio
async def test_generate_diagnostic_queries_validation_error_returns_empty(
    tmp_store,
) -> None:
    """Malformed JSON from the LLM must not raise — caller expects []."""
    provider = MockProvider(
        canned={"queries": [{"missing_sql": True}]}
    )
    analyzer = Analyzer(provider=provider, store=tmp_store)
    result = await analyzer.generate_diagnostic_queries(
        anomalies=[medical_anomaly()],
        system_model=medical_records_schema(),
    )
    assert result == []


@pytest.mark.asyncio
async def test_generate_diagnostic_queries_uses_anomaly_scoped_schema(
    tmp_store,
) -> None:
    """The schema section handed to the LLM must be scoped to the
    anomaly's metric/labels so only relevant tables consume prompt budget.
    Uses the ecommerce synthetic schema per Tier 0.
    """
    analyzer = Analyzer(
        provider=MockProvider(canned={"queries": []}), store=tmp_store,
    )
    from observibot.agent import analyzer as analyzer_module

    captured: dict[str, Any] = {}

    original = analyzer_module.build_app_schema_description

    def spy(model, **kwargs):  # noqa: ANN001
        captured["kwargs"] = dict(kwargs)
        return original(model, **kwargs)

    with patch.object(
        analyzer_module, "build_app_schema_description", spy,
    ):
        await analyzer.generate_diagnostic_queries(
            anomalies=[ecommerce_anomaly()],
            system_model=ecommerce_schema(),
        )
    # Anomaly-scoped: the anomaly summary text is passed as `question`, and
    # `full_detail_tables` is narrower than the chat path's default of 15.
    assert "question" in captured["kwargs"]
    assert "orders" in captured["kwargs"]["question"].lower()
    assert captured["kwargs"]["full_detail_tables"] == 8


@pytest.mark.asyncio
async def test_generate_diagnostic_queries_respects_schema_token_budget(
    tmp_store,
) -> None:
    """A pathologically large schema must be truncated to roughly
    ``max_schema_tokens`` before the LLM call.
    """
    analyzer = Analyzer(
        provider=MockProvider(canned={"queries": []}), store=tmp_store,
    )

    captured_prompts: list[str] = []

    class Capturing(MockProvider):
        async def _call(self, system_prompt, user_prompt):
            captured_prompts.append(user_prompt)
            return await super()._call(system_prompt, user_prompt)

    analyzer.provider = Capturing(canned={"queries": []})

    huge_schema = "X" * 200_000  # ~50k tokens
    from observibot.agent import analyzer as analyzer_module

    with patch.object(
        analyzer_module, "build_app_schema_description",
        lambda *_args, **_kwargs: huge_schema,
    ):
        await analyzer.generate_diagnostic_queries(
            anomalies=[ecommerce_anomaly()],
            system_model=ecommerce_schema(),
            max_schema_tokens=500,
        )
    prompt = captured_prompts[0]
    # Budget is 500 tokens ≈ 2000 chars; allow 2x for surrounding prompt.
    assert len(prompt) < 10_000, f"prompt too long: {len(prompt)} chars"
    assert "Truncated" in prompt


@pytest.mark.asyncio
async def test_generate_diagnostic_queries_synthetic_medical(
    tmp_store,
) -> None:
    """End-to-end: medical-records schema + medical anomaly → the LLM's
    canned 2-query response round-trips through validation."""
    canned = {
        "queries": [
            {
                "hypothesis": "RLS policy dropped org encounters",
                "sql": (
                    "SELECT encounter_type, count(*) FROM encounters "
                    "GROUP BY encounter_type LIMIT 20"
                ),
                "explanation": "Non-zero counts rule out total RLS drop.",
            },
            {
                "hypothesis": "Schedule backlog",
                "sql": (
                    "SELECT date_trunc('hour', scheduled_at) AS h, count(*) "
                    "FROM encounters GROUP BY h ORDER BY h DESC LIMIT 24"
                ),
                "explanation": "Reveals where the dip originates.",
            },
        ]
    }
    provider = MockProvider(canned=canned)
    analyzer = Analyzer(provider=provider, store=tmp_store)
    result = await analyzer.generate_diagnostic_queries(
        anomalies=[medical_anomaly()],
        system_model=medical_records_schema(),
    )
    assert len(result) == 2
    assert all(isinstance(q, DiagnosticQuery) for q in result)
    assert "encounter" in result[0].sql.lower()


# ---------------------------------------------------------------------------
# D4: execute_diagnostics (Call B)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_diagnostics_no_app_db_returns_unavailable_evidence(
    tmp_store,
) -> None:
    """With no app_db attached, every query must return evidence carrying
    a clear unavailable reason — no silent drops.
    """
    analyzer = Analyzer(provider=MockProvider(), store=tmp_store)
    queries = [
        DiagnosticQuery(
            hypothesis="encounter dip from RLS",
            sql="SELECT count(*) FROM encounters LIMIT 50",
        ),
        DiagnosticQuery(
            hypothesis="scheduling backlog",
            sql="SELECT count(*) FROM encounters LIMIT 50",
        ),
    ]
    evidence = await analyzer.execute_diagnostics(
        queries=queries,
        app_db=None,
        system_model=medical_records_schema(),
        cfg=_default_cfg(),
    )
    assert len(evidence) == 2
    for ev in evidence:
        assert ev.error == "application database unavailable"
        assert ev.rows == []


@pytest.mark.asyncio
async def test_execute_diagnostics_sandbox_rejection_records_evidence(
    tmp_store,
) -> None:
    """A query referencing a non-allowlisted table must surface as error
    evidence (O5 auditability), not be dropped silently.
    """
    analyzer = Analyzer(provider=MockProvider(), store=tmp_store)
    conn = _FakeConn()
    db = _FakeAppDb(conn)
    queries = [
        DiagnosticQuery(
            hypothesis="unauthorized probe",
            sql="SELECT * FROM pg_catalog.pg_user LIMIT 10",
        ),
    ]
    evidence = await analyzer.execute_diagnostics(
        queries=queries, app_db=db,
        system_model=medical_records_schema(),
        cfg=_default_cfg(),
    )
    assert len(evidence) == 1
    assert evidence[0].error and evidence[0].error.startswith("sandbox rejected:")
    assert conn.fetch_calls == []  # never executed


@pytest.mark.asyncio
async def test_execute_diagnostics_explain_failure_fails_closed(
    tmp_store,
) -> None:
    """Autonomous diagnostics must fail CLOSED on EXPLAIN error — the
    opposite of chat's fail-open. If the planner can't give us a cost,
    the query does not run.
    """
    analyzer = Analyzer(provider=MockProvider(), store=tmp_store)

    async def _boom(_sql: str) -> Any:
        raise RuntimeError("pretend EXPLAIN blew up")

    conn = _FakeConn(fetchrow_impl=_boom)
    db = _FakeAppDb(conn)
    queries = [
        DiagnosticQuery(
            hypothesis="RLS",
            sql="SELECT count(*) FROM encounters LIMIT 10",
        )
    ]
    evidence = await analyzer.execute_diagnostics(
        queries=queries, app_db=db,
        system_model=medical_records_schema(),
        cfg=_default_cfg(),
    )
    assert len(evidence) == 1
    assert evidence[0].error and evidence[0].error.startswith("EXPLAIN rejected:")
    assert conn.fetch_calls == []  # never executed


@pytest.mark.asyncio
async def test_execute_diagnostics_timeout_records_evidence(
    tmp_store,
) -> None:
    """A per-query timeout must not kill the batch — subsequent queries
    still run."""
    analyzer = Analyzer(provider=MockProvider(), store=tmp_store)

    call = {"n": 0}

    async def slow_then_fast(sql: str) -> list[Any]:
        call["n"] += 1
        if call["n"] == 1:
            await asyncio.sleep(2.0)  # exceeds 0.05s timeout
            return []
        return [{"count": 3}]

    conn = _FakeConn(explain_cost=100.0, fetch_impl=slow_then_fast)
    db = _FakeAppDb(conn)
    queries = [
        DiagnosticQuery(
            hypothesis="slow",
            sql="SELECT count(*) FROM orders LIMIT 10",
        ),
        DiagnosticQuery(
            hypothesis="fast",
            sql="SELECT count(*) FROM orders LIMIT 10",
        ),
    ]
    # 50ms timeout keeps the test fast while exercising the code path.
    cfg = _default_cfg(statement_timeout_ms=50)
    evidence = await analyzer.execute_diagnostics(
        queries=queries, app_db=db,
        system_model=ecommerce_schema(), cfg=cfg,
    )
    assert len(evidence) == 2
    assert evidence[0].error and evidence[0].error.startswith("timeout")
    assert evidence[1].error is None
    assert evidence[1].row_count == 1


@pytest.mark.asyncio
async def test_execute_diagnostics_sensitive_column_redacted(
    tmp_store,
) -> None:
    """A column whose name matches SENSITIVE_COLUMN_PATTERNS (e.g.
    ``api_token``) must have its value replaced with ``[REDACTED]``
    before the row leaves the analyzer."""
    analyzer = Analyzer(provider=MockProvider(), store=tmp_store)
    conn = _FakeConn(rows=[{"id": "x", "api_token": "sk-deadbeef"}])
    db = _FakeAppDb(conn)
    queries = [
        DiagnosticQuery(
            hypothesis="inspect orders token",
            sql="SELECT id FROM orders LIMIT 1",
        ),
    ]
    evidence = await analyzer.execute_diagnostics(
        queries=queries, app_db=db,
        system_model=ecommerce_schema(),
        cfg=_default_cfg(),
    )
    assert len(evidence) == 1
    assert evidence[0].rows[0]["api_token"] == "[REDACTED]"
    assert evidence[0].rows[0]["id"] == "x"


@pytest.mark.asyncio
async def test_execute_diagnostics_row_cap(tmp_store) -> None:
    """row_count reflects the full returned set; ``rows`` is sampled to
    at most ``max_rows_per_query`` entries."""
    analyzer = Analyzer(provider=MockProvider(), store=tmp_store)
    big = [{"i": i} for i in range(200)]
    conn = _FakeConn(rows=big)
    db = _FakeAppDb(conn)
    queries = [
        DiagnosticQuery(
            hypothesis="too many rows",
            sql="SELECT i FROM orders LIMIT 200",
        )
    ]
    cfg = _default_cfg(max_rows_per_query=50)
    evidence = await analyzer.execute_diagnostics(
        queries=queries, app_db=db,
        system_model=ecommerce_schema(), cfg=cfg,
    )
    assert evidence[0].row_count == 200
    assert len(evidence[0].rows) <= 50


@pytest.mark.asyncio
async def test_execute_diagnostics_single_connection_reused(
    tmp_store,
) -> None:
    """A batch of N queries must acquire exactly one connection."""
    analyzer = Analyzer(provider=MockProvider(), store=tmp_store)
    conn = _FakeConn()
    db = _FakeAppDb(conn)
    queries = [
        DiagnosticQuery(
            hypothesis=f"h{i}",
            sql=f"SELECT count(*) FROM orders WHERE subtotal_cents > {i} LIMIT 5",
        )
        for i in range(3)
    ]
    await analyzer.execute_diagnostics(
        queries=queries, app_db=db,
        system_model=ecommerce_schema(),
        cfg=_default_cfg(),
    )
    assert db.acquire_count == 1


@pytest.mark.asyncio
async def test_execute_diagnostics_synthetic_ecommerce(tmp_store) -> None:
    """End-to-end against the ecommerce synthetic schema with valid SELECT
    queries — evidence must come back populated with rows.
    """
    analyzer = Analyzer(provider=MockProvider(), store=tmp_store)
    conn = _FakeConn(rows=[{"status": "pending", "n": 12}])
    db = _FakeAppDb(conn)
    queries = [
        DiagnosticQuery(
            hypothesis="stuck pending orders",
            sql=(
                "SELECT order_status AS status, count(*) AS n "
                "FROM orders GROUP BY order_status LIMIT 10"
            ),
        )
    ]
    evidence = await analyzer.execute_diagnostics(
        queries=queries, app_db=db,
        system_model=ecommerce_schema(),
        cfg=_default_cfg(),
    )
    assert evidence[0].error is None
    assert evidence[0].row_count == 1
    assert evidence[0].rows[0]["status"] == "pending"


@pytest.mark.asyncio
async def test_execute_diagnostics_event_stream_mixed_outcomes(
    tmp_store,
) -> None:
    """A batch containing a rejection + a success still runs the second
    query — rejection doesn't abort the batch (Tier 0 event_stream)."""
    analyzer = Analyzer(provider=MockProvider(), store=tmp_store)
    conn = _FakeConn(rows=[{"event_count": 9}])
    db = _FakeAppDb(conn)
    queries = [
        DiagnosticQuery(
            hypothesis="cross-schema probe",
            sql="SELECT * FROM pg_catalog.pg_tables LIMIT 5",
        ),
        DiagnosticQuery(
            hypothesis="events spike",
            sql=(
                "SELECT event_count FROM aggregates_hourly "
                "ORDER BY bucket_hour DESC LIMIT 10"
            ),
        ),
    ]
    evidence = await analyzer.execute_diagnostics(
        queries=queries, app_db=db,
        system_model=event_stream_schema(),
        cfg=_default_cfg(),
    )
    assert evidence[0].error and "sandbox rejected" in evidence[0].error
    assert evidence[1].error is None
    # Sanity: an event-stream anomaly builder exists and is wired in D9.
    assert event_stream_anomaly is not None
    # Only the good query actually reached conn.fetch.
    assert len(conn.fetch_calls) == 1


@pytest.mark.asyncio
async def test_execute_diagnostics_empty_queries_is_noop(tmp_store) -> None:
    """Zero queries → empty evidence; no connection acquired."""
    analyzer = Analyzer(provider=MockProvider(), store=tmp_store)
    db = _FakeAppDb(_FakeConn())
    evidence = await analyzer.execute_diagnostics(
        queries=[], app_db=db,
        system_model=medical_records_schema(),
        cfg=_default_cfg(),
    )
    assert evidence == []
    assert db.acquire_count == 0


@pytest.mark.asyncio
async def test_execute_diagnostics_non_primitive_values_stringified(
    tmp_store,
) -> None:
    """asyncpg returns non-primitives (UUID, datetime, Decimal) that
    would break ``json.dumps`` when the evidence gets persisted. The
    sanitizer must coerce them to ``str`` before the bundle leaves the
    analyzer so ``Store.save_insight`` never sees an unserializable row.
    """
    import json as _json
    import uuid
    from datetime import UTC, datetime

    analyzer = Analyzer(provider=MockProvider(), store=tmp_store)
    row_id = uuid.uuid4()
    placed = datetime(2026, 4, 16, 12, 0, tzinfo=UTC)
    conn = _FakeConn(rows=[{"id": row_id, "placed_at": placed, "n": 7}])
    db = _FakeAppDb(conn)
    queries = [
        DiagnosticQuery(
            hypothesis="uuid coerce",
            sql="SELECT id, placed_at, 1 AS n FROM orders LIMIT 1",
        ),
    ]
    evidence = await analyzer.execute_diagnostics(
        queries=queries, app_db=db,
        system_model=ecommerce_schema(),
        cfg=_default_cfg(),
    )
    # The evidence bundle must round-trip through json.dumps without
    # raising — that is the exact failure the live pipeline hit.
    serialized = _json.dumps([e.to_dict() for e in evidence])
    assert str(row_id) in serialized
    assert "2026-04-16" in serialized
    assert evidence[0].rows[0]["n"] == 7  # primitive ints preserved


@pytest.mark.asyncio
async def test_execute_diagnostics_cost_above_threshold_rejects(
    tmp_store,
) -> None:
    """A query whose EXPLAIN cost exceeds ``cfg.explain_cost_threshold``
    is rejected before execution (fail-closed)."""
    analyzer = Analyzer(provider=MockProvider(), store=tmp_store)
    conn = _FakeConn(explain_cost=99_999.0)
    db = _FakeAppDb(conn)
    queries = [
        DiagnosticQuery(
            hypothesis="expensive",
            sql="SELECT count(*) FROM line_items LIMIT 10",
        )
    ]
    cfg = _default_cfg(explain_cost_threshold=10_000)
    evidence = await analyzer.execute_diagnostics(
        queries=queries, app_db=db,
        system_model=ecommerce_schema(), cfg=cfg,
    )
    assert evidence[0].error and "EXPLAIN rejected" in evidence[0].error
    assert conn.fetch_calls == []
    # Also assert `AsyncMock` fulfills isinstance without being needed:
    assert AsyncMock is not None
