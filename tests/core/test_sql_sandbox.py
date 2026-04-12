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
