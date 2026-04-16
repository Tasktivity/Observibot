from __future__ import annotations

import pytest

from observibot.connectors.postgresql import PostgreSQLConnector
from observibot.connectors.supabase import SUPABASE_INTERNAL_SCHEMAS, SupabaseConnector


def test_postgresql_requires_connection_string() -> None:
    with pytest.raises(ValueError):
        PostgreSQLConnector(name="x", config={})


def test_schema_filter_clause_with_included() -> None:
    conn = PostgreSQLConnector(
        name="x",
        config={"connection_string": "postgres://u:p@h/db", "schemas": ["public"]},
    )
    clause, params = conn._schema_filter_clause("schemaname")
    assert "schemaname = ANY" in clause
    assert params[0] == ["public"]


def test_supabase_excluded_schemas_include_auth() -> None:
    conn = SupabaseConnector(
        name="x", config={"connection_string": "postgres://u:p@h/db"}
    )
    excluded = conn._excluded_schemas()
    for schema in SUPABASE_INTERNAL_SCHEMAS:
        assert schema in excluded


def test_postgres_default_excluded_schemas() -> None:
    conn = PostgreSQLConnector(
        name="x", config={"connection_string": "postgres://u:p@h/db"}
    )
    excluded = conn._excluded_schemas()
    assert "pg_catalog" in excluded
    # Supabase-specific schemas should NOT be excluded for raw postgres
    assert "auth" not in excluded


def test_postgres_required_permissions_lists_tables() -> None:
    conn = PostgreSQLConnector(
        name="x", config={"connection_string": "postgres://u:p@h/db"}
    )
    perms = conn.required_permissions()
    assert any("information_schema" in p for p in perms)


def test_quote_ident_escapes_embedded_quote() -> None:
    """Dynamic SQL for value sampling relies on this for identifier safety."""
    from observibot.connectors.postgresql import _quote_ident

    assert _quote_ident("status") == '"status"'
    assert _quote_ident('weird"name') == '"weird""name"'
    # Schema-qualified via dot is handled by the caller, not this function
    assert _quote_ident("public.tasks") == '"public.tasks"'


class _FakeConn:
    """Minimal asyncpg-connection stand-in for _enrich_with_value_distributions.

    Records executed SQL so tests can assert on query shape. Returns canned
    rows for GROUP BY queries via ``fetch_responses``.
    """

    def __init__(self, fetch_responses: dict[str, list[dict]] | None = None,
                 fail_columns: set[str] | None = None) -> None:
        self.executed: list[str] = []
        self.fetched: list[str] = []
        self._fetch_responses = fetch_responses or {}
        self._fail_columns = fail_columns or set()

    async def execute(self, sql: str) -> None:
        self.executed.append(sql)

    async def fetch(self, sql: str, *args):
        self.fetched.append(sql)
        for col_name, rows in self._fetch_responses.items():
            if f'"{col_name}"' in sql:
                if col_name in self._fail_columns:
                    raise RuntimeError("simulated sampling failure")
                return rows
        return []


@pytest.mark.asyncio
async def test_enrich_with_value_distributions_attaches_top_values() -> None:
    """When a status column is present, sampled values must land on the
    column dict so the LLM sees them in the planning prompt."""
    from observibot.connectors.postgresql import PostgreSQLConnector
    from observibot.core.models import TableInfo

    conn_obj = PostgreSQLConnector(
        name="t", config={"connection_string": "postgres://u:p@h/db"}
    )
    tables = {
        ("public", "jobs"): TableInfo(
            name="jobs", schema="public", row_count=200,
            columns=[
                {"name": "id", "type": "uuid"},
                {"name": "status", "type": "text"},
                {"name": "payload", "type": "text"},  # not a status column
            ],
        ),
    }
    fake = _FakeConn(fetch_responses={
        "status": [
            {"v": "complete", "n": 170},
            {"v": "cancelled", "n": 25},
            {"v": "error", "n": 5},
        ],
    })

    await conn_obj._enrich_with_value_distributions(fake, tables)

    jobs = tables[("public", "jobs")]
    status_col = next(c for c in jobs.columns if c["name"] == "status")
    payload_col = next(c for c in jobs.columns if c["name"] == "payload")
    assert "top_values" in status_col
    assert status_col["values_exhaustive"] is True  # 3 rows < LIMIT 20
    values = [tv["value"] for tv in status_col["top_values"]]
    assert values == ["complete", "cancelled", "error"]
    # Non-status columns must not be sampled
    assert "top_values" not in payload_col
    # statement_timeout must be set before the sampling queries
    assert any("statement_timeout" in s for s in fake.executed)


@pytest.mark.asyncio
async def test_enrich_skips_empty_and_huge_tables() -> None:
    from observibot.connectors.postgresql import PostgreSQLConnector
    from observibot.core.models import TableInfo

    conn_obj = PostgreSQLConnector(
        name="t", config={"connection_string": "postgres://u:p@h/db"}
    )
    tables = {
        ("public", "empty_jobs"): TableInfo(
            name="empty_jobs", schema="public", row_count=0,
            columns=[{"name": "status", "type": "text"}],
        ),
        ("public", "huge_events"): TableInfo(
            name="huge_events", schema="public", row_count=50_000_000,
            columns=[{"name": "status", "type": "text"}],
        ),
    }
    fake = _FakeConn(fetch_responses={"status": [{"v": "x", "n": 1}]})

    await conn_obj._enrich_with_value_distributions(fake, tables)

    # Neither table should have been sampled
    assert fake.fetched == []
    for tbl in tables.values():
        for col in tbl.columns:
            assert "top_values" not in col


@pytest.mark.asyncio
async def test_enrich_ignores_non_text_status_columns() -> None:
    """Integer status codes don't exhibit the English-past-tense failure
    mode and aren't worth a GROUP BY — skip them to keep this narrow."""
    from observibot.connectors.postgresql import PostgreSQLConnector
    from observibot.core.models import TableInfo

    conn_obj = PostgreSQLConnector(
        name="t", config={"connection_string": "postgres://u:p@h/db"}
    )
    tables = {
        ("public", "events"): TableInfo(
            name="events", schema="public", row_count=100,
            columns=[
                {"name": "status", "type": "integer"},
                {"name": "sub_status", "type": "bigint"},
            ],
        ),
    }
    fake = _FakeConn(fetch_responses={"status": [{"v": 1, "n": 10}]})

    await conn_obj._enrich_with_value_distributions(fake, tables)

    assert fake.fetched == []


@pytest.mark.asyncio
async def test_enrich_samples_enum_like_columns_beyond_status() -> None:
    """Same failure mode applies to type/kind/state/role/etc — LLM guesses
    conventional values when the real ones differ. The sampler should cover
    these too."""
    from observibot.connectors.postgresql import PostgreSQLConnector
    from observibot.core.models import TableInfo

    conn_obj = PostgreSQLConnector(
        name="t", config={"connection_string": "postgres://u:p@h/db"}
    )
    tables = {
        ("public", "t"): TableInfo(
            name="t", schema="public", row_count=50,
            columns=[
                {"name": "kind", "type": "text"},
                {"name": "user_role", "type": "text"},
                {"name": "severity", "type": "varchar"},
                {"name": "note", "type": "text"},  # must be skipped
            ],
        ),
    }
    fake = _FakeConn(fetch_responses={
        "kind": [{"v": "A", "n": 50}],
        "user_role": [{"v": "admin", "n": 50}],
        "severity": [{"v": "low", "n": 50}],
        "note": [{"v": "should-not-be-sampled", "n": 1}],
    })

    await conn_obj._enrich_with_value_distributions(fake, tables)

    cols = {c["name"]: c for c in tables[("public", "t")].columns}
    assert "top_values" in cols["kind"]
    assert "top_values" in cols["user_role"]
    assert "top_values" in cols["severity"]
    assert "top_values" not in cols["note"]


@pytest.mark.asyncio
async def test_enrich_tolerates_per_column_failure() -> None:
    """A single flaky column must not block sampling of the others."""
    from observibot.connectors.postgresql import PostgreSQLConnector
    from observibot.core.models import TableInfo

    conn_obj = PostgreSQLConnector(
        name="t", config={"connection_string": "postgres://u:p@h/db"}
    )
    tables = {
        ("public", "jobs"): TableInfo(
            name="jobs", schema="public", row_count=100,
            columns=[{"name": "status", "type": "text"}],
        ),
        ("public", "runs"): TableInfo(
            name="runs", schema="public", row_count=100,
            columns=[{"name": "run_status", "type": "text"}],
        ),
    }
    fake = _FakeConn(
        fetch_responses={
            "status": [{"v": "ok", "n": 100}],
            "run_status": [{"v": "done", "n": 100}],
        },
        fail_columns={"status"},
    )

    await conn_obj._enrich_with_value_distributions(fake, tables)

    jobs_status = tables[("public", "jobs")].columns[0]
    runs_status = tables[("public", "runs")].columns[0]
    assert "top_values" not in jobs_status  # sampling failed for this one
    assert runs_status.get("top_values") is not None  # other column still worked
