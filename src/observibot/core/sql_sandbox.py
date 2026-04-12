"""SQL sandbox for validating LLM-generated queries.

Every query goes through sqlglot AST parsing to enforce:
1. SELECT-only (no INSERT/UPDATE/DELETE/DROP)
2. Table allowlist
3. LIMIT injection/enforcement
4. Dangerous function blocking
"""
from __future__ import annotations

import sqlglot
from sqlglot import exp


class QueryValidationError(Exception):
    """Raised when an LLM-generated query fails sandbox validation."""


BLOCKED_FUNCTIONS = frozenset({
    "pg_sleep", "pg_terminate_backend", "pg_cancel_backend",
    "pg_reload_conf", "set_config", "lo_import", "lo_export",
    "dblink", "dblink_exec",
})


def validate_query(
    sql: str,
    allowed_tables: set[str],
    max_limit: int = 1000,
) -> str:
    """Parse, validate, and return sanitized SQL.

    Raises QueryValidationError if the query is unsafe.
    """
    try:
        parsed = sqlglot.parse_one(sql, dialect="postgres")
    except sqlglot.errors.ParseError as e:
        raise QueryValidationError(f"Failed to parse SQL: {e}") from e

    if not isinstance(parsed, exp.Select):
        raise QueryValidationError("Only SELECT queries are allowed")

    for table in parsed.find_all(exp.Table):
        table_name = table.name
        if table_name and table_name not in allowed_tables:
            raise QueryValidationError(
                f"Table '{table_name}' is not in the allowlist"
            )

    for func in parsed.find_all(exp.Anonymous):
        if func.name.lower() in BLOCKED_FUNCTIONS:
            raise QueryValidationError(
                f"Function '{func.name}' is blocked for security"
            )

    for func in parsed.find_all(exp.Func):
        func_name = getattr(func, "sql_name", lambda: "")()
        if func_name.lower() in BLOCKED_FUNCTIONS:
            raise QueryValidationError(
                f"Function '{func_name}' is blocked for security"
            )

    limit_node = parsed.args.get("limit")
    if limit_node is None:
        parsed = parsed.limit(max_limit)
    else:
        try:
            existing = int(limit_node.expression.this)
            if existing > max_limit:
                limit_node.expression.args["this"] = str(max_limit)
        except (ValueError, AttributeError):
            pass

    return parsed.sql(dialect="postgres")
