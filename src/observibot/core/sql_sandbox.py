"""SQL sandbox for validating LLM-generated queries.

Five-layer defence for every query the LLM generates:

1. SELECT-only (no INSERT/UPDATE/DELETE/DROP) — ``validate_query``
2. Table allowlist — ``validate_query``
3. LIMIT injection/enforcement — ``validate_query``
4. EXPLAIN cost gating — ``explain_check`` (PostgreSQL only)
5. ``statement_timeout`` enforced at the engine level — see ``AppDatabasePool``
   and ``_exec_observability`` in ``chat_agent``

Layers 1-3 are sqlglot AST checks. Layer 4 asks the database what the query
will cost *before* we execute it, so an LLM-generated full-table scan on a
150k-row table is rejected with a user-facing message instead of running
until the timeout fires.
"""
from __future__ import annotations

import json
import logging
from typing import Any

import sqlalchemy as sa
import sqlglot
from sqlglot import exp

log = logging.getLogger(__name__)


# Default PostgreSQL planner "total cost" above which a query is rejected.
# PostgreSQL cost is in arbitrary units (roughly: sequential page reads), and
# 100k is comfortable for most OLTP queries with appropriate indexes but
# unacceptably high for full table scans on metric_snapshots.
DEFAULT_EXPLAIN_COST_THRESHOLD = 100_000.0


class QueryValidationError(Exception):
    """Raised when an LLM-generated query fails sandbox validation."""


BLOCKED_FUNCTIONS = frozenset({
    # Process/connection control
    "pg_sleep", "pg_terminate_backend", "pg_cancel_backend",
    "pg_reload_conf", "set_config", "lo_import", "lo_export",
    "dblink", "dblink_exec",
    # File system access
    "pg_read_file", "pg_read_binary_file", "pg_ls_dir",
    "pg_stat_file", "pg_ls_logdir", "pg_ls_waldir",
    # Config / info leakage
    "current_setting",
    # Lock / transaction abuse
    "pg_advisory_lock", "pg_advisory_xact_lock",
    "pg_advisory_unlock", "pg_try_advisory_lock",
    # DoS vectors (unbounded set-returning functions)
    "generate_series",
    # Other admin functions
    "pg_switch_wal", "pg_create_restore_point",
    "pg_start_backup", "pg_stop_backup",
})

# sqlglot transforms some PostgreSQL functions into specialized expression
# classes. When checking ``exp.Func`` we map the class key back to the
# original function name so the BLOCKED_FUNCTIONS allowlist catches them.
_FUNC_KEY_TO_NAME = {
    "explodinggenerateseries": "generate_series",
    "generateseries": "generate_series",
}

# Only this schema qualifier is allowed on table references. Supabase exposes
# password hashes and tokens in ``auth.users``; PostgreSQL internals live in
# ``pg_catalog`` and ``information_schema`` — all of which would be an
# LLM-driven privacy disaster if reachable through the chat agent.
_ALLOWED_SCHEMAS = frozenset({"", "public"})


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
        schema = (table.db or "").lower()
        if schema not in _ALLOWED_SCHEMAS:
            raise QueryValidationError(
                f"Schema '{table.db}' is not allowed. "
                "Only public schema tables are accessible."
            )
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
        if isinstance(func, exp.Anonymous):
            continue  # already covered above
        key = getattr(func, "key", "").lower()
        normalized = _FUNC_KEY_TO_NAME.get(key, "")
        if normalized and normalized in BLOCKED_FUNCTIONS:
            raise QueryValidationError(
                f"Function '{normalized}' is blocked for security"
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


async def explain_check(
    conn_runner: Any,
    sql: str,
    cost_threshold: float = DEFAULT_EXPLAIN_COST_THRESHOLD,
) -> tuple[bool, float]:
    """Run EXPLAIN on a query and check estimated cost.

    Returns ``(is_acceptable, total_cost)``.

    ``conn_runner`` may be either:
      * a SQLAlchemy ``AsyncEngine`` — we open a transient transaction for the
        EXPLAIN and discard it
      * an awaitable callable ``async fn(sql) -> dict`` that returns the first
        row's JSON-decoded plan (used by callers that manage their own pool
        like ``AppDatabasePool``)

    Only meaningful on PostgreSQL. For SQLite, returns ``(True, 0.0)`` because
    SQLite's ``EXPLAIN QUERY PLAN`` does not produce a cost estimate.

    If EXPLAIN itself fails (parse error in the target query, unsupported
    syntax, permissions), returns ``(True, 0.0)`` so that the check never
    *blocks* a query that would otherwise succeed. The subsequent execute
    will surface any real error. This is deliberate: EXPLAIN is a safety
    *layer*, not a gate — failing open lets us get real feedback in logs
    while we tune the cost threshold, without breaking the happy path.
    """
    # Case 1: SQLAlchemy AsyncEngine — check URL to short-circuit SQLite.
    url = getattr(conn_runner, "url", None)
    if url is not None:
        if "sqlite" in str(url):
            return True, 0.0
        try:
            async with conn_runner.begin() as conn:
                result = await conn.execute(
                    sa.text(f"EXPLAIN (FORMAT JSON) {sql}")
                )
                plan = result.scalar()
            return _plan_total_cost_ok(plan, cost_threshold)
        except Exception as exc:
            log.warning("EXPLAIN (engine) failed; allowing query: %s", exc)
            return True, 0.0

    # Case 2: async callable plan-fetcher (custom pool). The callable should
    # return the JSON plan structure (str or list).
    if callable(conn_runner):
        try:
            plan = await conn_runner(f"EXPLAIN (FORMAT JSON) {sql}")
            return _plan_total_cost_ok(plan, cost_threshold)
        except Exception as exc:
            log.warning("EXPLAIN (callable) failed; allowing query: %s", exc)
            return True, 0.0

    log.debug("EXPLAIN: unknown conn_runner type %r; allowing query", type(conn_runner))
    return True, 0.0


def _plan_total_cost_ok(
    plan: Any, cost_threshold: float,
) -> tuple[bool, float]:
    """Parse a Postgres ``EXPLAIN (FORMAT JSON)`` result and compare cost."""
    if isinstance(plan, str):
        try:
            plan_data = json.loads(plan)
        except json.JSONDecodeError:
            return True, 0.0
    else:
        plan_data = plan
    # PostgreSQL returns a list of one dict with a "Plan" key.
    try:
        total_cost = float(plan_data[0]["Plan"]["Total Cost"])
    except (KeyError, IndexError, TypeError, ValueError):
        return True, 0.0
    return total_cost <= cost_threshold, total_cost
