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
