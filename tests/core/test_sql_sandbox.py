"""Tests for the SQL sandbox."""
from __future__ import annotations

import pytest

from observibot.core.sql_sandbox import QueryValidationError, validate_query

ALLOWED = {"metric_snapshots", "insights", "users"}


def test_select_allowed():
    result = validate_query("SELECT * FROM metric_snapshots", ALLOWED)
    assert "metric_snapshots" in result
    assert "LIMIT" in result.upper()


def test_non_select_rejected():
    with pytest.raises(QueryValidationError, match="SELECT"):
        validate_query("DELETE FROM metric_snapshots", ALLOWED)


def test_insert_rejected():
    with pytest.raises(QueryValidationError, match="SELECT"):
        validate_query("INSERT INTO users (id) VALUES ('x')", ALLOWED)


def test_update_rejected():
    with pytest.raises(QueryValidationError, match="SELECT"):
        validate_query("UPDATE users SET email='x'", ALLOWED)


def test_drop_rejected():
    with pytest.raises(QueryValidationError, match="SELECT"):
        validate_query("DROP TABLE users", ALLOWED)


def test_blocked_table():
    with pytest.raises(QueryValidationError, match="allowlist"):
        validate_query("SELECT * FROM secrets", ALLOWED)


def test_limit_injected():
    result = validate_query("SELECT * FROM insights", ALLOWED)
    assert "1000" in result


def test_limit_enforced_when_too_high():
    result = validate_query("SELECT * FROM insights LIMIT 9999", ALLOWED, max_limit=500)
    assert "500" in result


def test_existing_limit_kept_when_within_bounds():
    result = validate_query("SELECT * FROM insights LIMIT 10", ALLOWED)
    assert "10" in result


def test_invalid_sql_raises():
    with pytest.raises(QueryValidationError, match="parse"):
        validate_query("NOT VALID SQL AT ALL ;;; {{", ALLOWED)


def test_subquery_table_check():
    with pytest.raises(QueryValidationError, match="allowlist"):
        validate_query(
            "SELECT * FROM metric_snapshots WHERE id IN (SELECT id FROM secrets)",
            ALLOWED,
        )


def test_multiple_tables_all_allowed():
    result = validate_query(
        "SELECT m.* FROM metric_snapshots m JOIN insights i ON m.id = i.id",
        ALLOWED,
    )
    assert "metric_snapshots" in result


# H2: schema-qualified table bypass


def test_auth_schema_rejected():
    """auth.users contains password hashes on Supabase — never allow."""
    with pytest.raises(QueryValidationError, match="Schema"):
        validate_query("SELECT * FROM auth.users", ALLOWED)


def test_pg_catalog_rejected():
    """S0.4: ``pg_catalog`` is now a recognized schema qualifier so
    autonomous diagnostics can target read-only monitoring views like
    ``pg_stat_database``, but the TABLE-NAME allowlist remains the real
    gate: ``pg_authid`` is not in a chat caller's allowlist and must be
    rejected there.
    """
    with pytest.raises(QueryValidationError, match="not in the allowlist"):
        validate_query("SELECT * FROM pg_catalog.pg_authid", ALLOWED)


def test_pg_catalog_monitoring_view_allowed_when_explicitly_listed():
    """S0.4: when a caller (autonomous diagnostics) includes
    ``pg_stat_database`` in the allowlist, the sandbox accepts it with
    or without the ``pg_catalog.`` qualifier.
    """
    diag_allow = ALLOWED | {"pg_stat_database"}
    out = validate_query(
        "SELECT datname FROM pg_catalog.pg_stat_database LIMIT 10",
        diag_allow,
    )
    assert "pg_stat_database" in out
    out2 = validate_query(
        "SELECT datname FROM pg_stat_database LIMIT 10",
        diag_allow,
    )
    assert "pg_stat_database" in out2


def test_information_schema_rejected():
    with pytest.raises(QueryValidationError, match="Schema"):
        validate_query("SELECT * FROM information_schema.tables", ALLOWED)


def test_public_schema_allowed():
    """Explicit public.* qualifier is fine."""
    result = validate_query("SELECT * FROM public.users", ALLOWED)
    assert "users" in result


def test_unqualified_table_allowed_as_before():
    result = validate_query("SELECT * FROM users", ALLOWED)
    assert "users" in result


def test_subquery_in_forbidden_schema_rejected():
    with pytest.raises(QueryValidationError, match="Schema"):
        validate_query(
            "SELECT id FROM users WHERE id IN (SELECT id FROM auth.users)",
            ALLOWED,
        )


# H3: blocked functions


def test_pg_read_file_rejected():
    with pytest.raises(QueryValidationError, match="blocked"):
        validate_query("SELECT pg_read_file('/etc/passwd')", ALLOWED)


def test_pg_read_file_case_insensitive():
    with pytest.raises(QueryValidationError, match="blocked"):
        validate_query("SELECT PG_READ_FILE('/etc/passwd')", ALLOWED)


def test_current_setting_rejected():
    with pytest.raises(QueryValidationError, match="blocked"):
        validate_query("SELECT current_setting('server_version')", ALLOWED)


def test_generate_series_rejected():
    with pytest.raises(QueryValidationError, match="blocked"):
        validate_query("SELECT * FROM generate_series(1, 1000000000)", ALLOWED)


def test_generate_series_in_select_rejected():
    with pytest.raises(QueryValidationError, match="blocked"):
        validate_query("SELECT generate_series(1, 10)", ALLOWED)


def test_pg_ls_dir_rejected():
    with pytest.raises(QueryValidationError, match="blocked"):
        validate_query("SELECT pg_ls_dir('/tmp')", ALLOWED)


def test_pg_advisory_lock_rejected():
    with pytest.raises(QueryValidationError, match="blocked"):
        validate_query("SELECT pg_advisory_lock(1)", ALLOWED)


def test_pg_sleep_still_rejected():
    """Regression: existing blocked functions still rejected."""
    with pytest.raises(QueryValidationError, match="blocked"):
        validate_query("SELECT pg_sleep(10)", ALLOWED)
