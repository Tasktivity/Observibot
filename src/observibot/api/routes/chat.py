"""Chat routes — text-to-SQL with visualization."""
from __future__ import annotations

import hashlib
import logging
import time
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa
from fastapi import APIRouter, Depends

from observibot.api.deps import get_current_user, get_store
from observibot.api.schemas import ChatRequest, ChatResponse
from observibot.core.sql_sandbox import QueryValidationError, validate_query
from observibot.core.store import Store, query_cache

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/chat", tags=["chat"])

ALLOWED_TABLES = {
    "system_snapshots", "metric_snapshots", "change_events",
    "insights", "alert_history", "business_context",
    "llm_usage", "metric_baselines",
}

CACHE_TTL_SECONDS = 120


def _query_hash(sql: str) -> str:
    return hashlib.sha256(sql.encode()).hexdigest()[:16]


def _utcnow_iso() -> str:
    return datetime.now(UTC).isoformat()


async def _check_cache(store: Store, sql_hash: str) -> dict | None:
    async with store.engine.begin() as conn:
        result = await conn.execute(
            sa.select(
                query_cache.c.result_json,
                query_cache.c.row_count,
                query_cache.c.execution_ms,
                query_cache.c.expires_at,
            ).where(query_cache.c.hash == sql_hash)
        )
        row = result.fetchone()
    if row is None:
        return None
    if row[3] and datetime.fromisoformat(row[3]) < datetime.now(UTC):
        return None
    return {
        "result_json": row[0],
        "row_count": row[1],
        "execution_ms": row[2],
    }


async def _save_cache(
    store: Store, sql_hash: str, sql_text: str,
    result_json: list, row_count: int, execution_ms: float,
) -> None:
    now = _utcnow_iso()
    expires = (datetime.now(UTC) + timedelta(seconds=CACHE_TTL_SECONDS)).isoformat()
    async with store.engine.begin() as conn:
        stmt = (
            sa.dialects.sqlite.insert(query_cache)
            .values(
                hash=sql_hash,
                sql_text=sql_text,
                result_json=result_json,
                row_count=row_count,
                execution_ms=execution_ms,
                created_at=now,
                expires_at=expires,
            )
            .on_conflict_do_update(
                index_elements=["hash"],
                set_=dict(
                    result_json=result_json,
                    row_count=row_count,
                    execution_ms=execution_ms,
                    created_at=now,
                    expires_at=expires,
                ),
            )
        )
        await conn.execute(stmt)


def _infer_widget_plan(
    question: str, columns: list[str], rows: list[dict],
) -> dict:
    """Infer a widget plan from query results."""
    if len(rows) == 1 and len(columns) <= 2:
        value = list(rows[0].values())[-1]
        return {
            "widget_type": "kpi_number",
            "title": question[:50],
            "encoding": {},
            "data": rows,
            "config": {"value": value if isinstance(value, (int, float)) else 0},
        }

    time_cols = ("collected_at", "occurred_at", "created_at", "recorded_at")
    has_time = any(c for c in columns if c in time_cols)
    has_numeric = any(
        isinstance(rows[0].get(c), (int, float)) for c in columns if rows
    )

    if has_time and has_numeric:
        time_col = next(c for c in columns if c in time_cols)
        value_col = next(
            (c for c in columns
             if isinstance(rows[0].get(c), (int, float)) and c != time_col),
            columns[-1],
        )
        return {
            "widget_type": "time_series",
            "title": question[:50],
            "encoding": {"x": time_col, "y": value_col},
            "data": rows,
        }

    if len(columns) == 2 and has_numeric:
        cat_col = next(
            (c for c in columns if not isinstance(rows[0].get(c), (int, float))),
            columns[0],
        )
        val_col = next(
            (c for c in columns if isinstance(rows[0].get(c), (int, float))),
            columns[1],
        )
        return {
            "widget_type": "categorical_bar",
            "title": question[:50],
            "encoding": {"x": cat_col, "y": val_col},
            "data": rows,
        }

    return {
        "widget_type": "table",
        "title": question[:50],
        "encoding": {},
        "data": rows,
        "config": {"columns": columns},
    }


def _widget_plan_to_vega_lite(plan: dict) -> dict | None:
    """Convert a widget plan to a Vega-Lite spec."""
    wtype = plan.get("widget_type", "")
    data = plan.get("data", [])
    encoding = plan.get("encoding", {})

    if wtype == "time_series" and encoding.get("x") and encoding.get("y"):
        return {
            "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
            "data": {"values": data},
            "mark": "line",
            "encoding": {
                "x": {"field": encoding["x"], "type": "temporal"},
                "y": {"field": encoding["y"], "type": "quantitative"},
            },
        }

    if wtype == "categorical_bar" and encoding.get("x") and encoding.get("y"):
        return {
            "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
            "data": {"values": data},
            "mark": "bar",
            "encoding": {
                "x": {"field": encoding["x"], "type": "nominal"},
                "y": {"field": encoding["y"], "type": "quantitative"},
            },
        }

    return None


def _build_sql_for_question(question: str) -> str | None:
    """Map common natural language patterns to SQL.

    This is a deterministic fallback when no LLM is available.
    """
    q = question.lower().strip()

    if "metric" in q and ("recent" in q or "latest" in q or "last" in q):
        return (
            "SELECT metric_name, value, collected_at "
            "FROM metric_snapshots ORDER BY collected_at DESC LIMIT 20"
        )
    if "insight" in q:
        return (
            "SELECT severity, title, summary, created_at "
            "FROM insights ORDER BY created_at DESC LIMIT 20"
        )
    if "alert" in q:
        return (
            "SELECT channel, severity, status, sent_at "
            "FROM alert_history ORDER BY sent_at DESC LIMIT 20"
        )
    if "change" in q or "deploy" in q:
        return (
            "SELECT event_type, summary, occurred_at "
            "FROM change_events ORDER BY occurred_at DESC LIMIT 20"
        )
    if "cost" in q or "usage" in q or "token" in q:
        return (
            "SELECT provider, model, total_tokens, cost_usd, recorded_at "
            "FROM llm_usage ORDER BY recorded_at DESC LIMIT 20"
        )
    if "baseline" in q:
        return (
            "SELECT metric_name, connector_name, mean, stddev, sample_count "
            "FROM metric_baselines LIMIT 20"
        )

    return (
        "SELECT metric_name, value, collected_at "
        "FROM metric_snapshots ORDER BY collected_at DESC LIMIT 20"
    )


@router.post("/query")
async def chat_query(
    req: ChatRequest,
    user: dict = Depends(get_current_user),
    store: Store = Depends(get_store),
) -> ChatResponse:
    """Process a natural language question and return results with visualization."""
    raw_sql = _build_sql_for_question(req.question)
    if raw_sql is None:
        return ChatResponse(
            answer="I couldn't understand that question. "
            "Try asking about metrics, insights, or deployments.",
        )

    try:
        validated_sql = validate_query(raw_sql, ALLOWED_TABLES)
    except QueryValidationError as e:
        return ChatResponse(answer=f"Query validation failed: {e}")

    sql_hash = _query_hash(validated_sql)

    cached = await _check_cache(store, sql_hash)
    if cached:
        rows = cached["result_json"] if isinstance(cached["result_json"], list) else []
        columns = list(rows[0].keys()) if rows else []
        plan = _infer_widget_plan(req.question, columns, rows)
        vega = _widget_plan_to_vega_lite(plan)
        return ChatResponse(
            answer=f"Found {len(rows)} results (cached).",
            widget_plan=plan,
            vega_lite_spec=vega,
            sql_query=validated_sql,
            execution_ms=cached["execution_ms"],
        )

    start = time.monotonic()
    try:
        async with store.engine.begin() as conn:
            result = await conn.execute(sa.text(validated_sql))
            raw_rows = result.fetchall()
            columns = list(result.keys())
    except Exception as e:
        log.warning("Query execution failed: %s", e)
        return ChatResponse(answer=f"Query failed: {e}", sql_query=validated_sql)

    elapsed_ms = (time.monotonic() - start) * 1000
    rows = [dict(zip(columns, r, strict=False)) for r in raw_rows]

    for row in rows:
        for k, v in row.items():
            if not isinstance(v, (str, int, float, bool, type(None))):
                row[k] = str(v)

    await _save_cache(store, sql_hash, validated_sql, rows, len(rows), elapsed_ms)

    plan = _infer_widget_plan(req.question, columns, rows)
    vega = _widget_plan_to_vega_lite(plan)

    return ChatResponse(
        answer=f"Found {len(rows)} results.",
        widget_plan=plan,
        vega_lite_spec=vega,
        sql_query=validated_sql,
        execution_ms=round(elapsed_ms, 1),
    )
