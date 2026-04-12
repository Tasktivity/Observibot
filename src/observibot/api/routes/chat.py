"""Chat routes — agentic multi-domain tool-calling pipeline."""
from __future__ import annotations

import hashlib
import logging
import time
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa
from fastapi import APIRouter, Depends

from observibot.api.deps import (
    get_analyzer,
    get_app_db,
    get_current_user,
    get_store,
)
from observibot.api.schemas import ChatRequest, ChatResponse
from observibot.core.sql_sandbox import QueryValidationError, validate_query
from observibot.core.store import Store, query_cache

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/chat", tags=["chat"])

OBSERVABILITY_TABLES = {
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
    expires = (
        datetime.now(UTC) + timedelta(seconds=CACHE_TTL_SECONDS)
    ).isoformat()
    async with store.engine.begin() as conn:
        await conn.execute(
            query_cache.delete().where(query_cache.c.hash == sql_hash)
        )
        await conn.execute(
            query_cache.insert().values(
                hash=sql_hash,
                sql_text=sql_text,
                result_json=result_json,
                row_count=row_count,
                execution_ms=execution_ms,
                created_at=now,
                expires_at=expires,
            )
        )


def _build_sql_for_question(question: str) -> str:
    """Deterministic fallback when no LLM is available."""
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
            "SELECT metric_name, connector_name, mean, stddev, "
            "sample_count FROM metric_baselines LIMIT 20"
        )

    return (
        "SELECT metric_name, value, collected_at "
        "FROM metric_snapshots ORDER BY collected_at DESC LIMIT 20"
    )


async def _deterministic_fallback(
    question: str, store: Store,
) -> ChatResponse:
    """Execute without LLM — keyword-based SQL against the store."""
    start = time.monotonic()
    raw_sql = _build_sql_for_question(question)

    try:
        validated = validate_query(raw_sql, OBSERVABILITY_TABLES)
    except QueryValidationError as e:
        return ChatResponse(answer=f"Query validation failed: {e}")

    sql_hash = _query_hash(validated)
    cached = await _check_cache(store, sql_hash)
    if cached:
        rows = (
            cached["result_json"]
            if isinstance(cached["result_json"], list) else []
        )
        return ChatResponse(
            answer=_narrate_fallback(question, rows),
            widget_plan=_fallback_widget(question, rows),
            sql_query=validated,
            execution_ms=cached["execution_ms"],
            domains_hit=["observability"],
        )

    try:
        async with store.engine.begin() as conn:
            result = await conn.execute(sa.text(validated))
            raw_rows = result.fetchall()
            columns = list(result.keys())
    except Exception as e:
        return ChatResponse(
            answer=f"Query failed: {e}", sql_query=validated,
        )

    elapsed_ms = (time.monotonic() - start) * 1000
    rows = [dict(zip(columns, r, strict=False)) for r in raw_rows]
    for row in rows:
        for k, v in row.items():
            if not isinstance(v, (str, int, float, bool, type(None))):
                row[k] = str(v)

    await _save_cache(
        store, sql_hash, validated, rows, len(rows), elapsed_ms,
    )

    return ChatResponse(
        answer=_narrate_fallback(question, rows),
        widget_plan=_fallback_widget(question, rows),
        sql_query=validated,
        execution_ms=round(elapsed_ms, 1),
        domains_hit=["observability"],
        warnings=["LLM not available — showing raw monitoring data."],
    )


def _narrate_fallback(question: str, rows: list[dict]) -> str:
    if not rows:
        return (
            "No data found for your query. The monitor may not have "
            "collected data yet, or try rephrasing your question."
        )
    count = len(rows)
    return (
        f"Here are {count} monitoring records matching your question. "
        "Note: LLM analysis is not available, so this is raw data "
        "from the observability store."
    )


def _fallback_widget(
    question: str, rows: list[dict],
) -> dict | None:
    if not rows:
        return None
    columns = list(rows[0].keys())
    return {
        "widget_type": "table",
        "title": question[:50],
        "data": rows,
        "config": {"columns": columns},
    }


@router.post("/query")
async def chat_query(
    req: ChatRequest,
    user: dict = Depends(get_current_user),
    store: Store = Depends(get_store),
) -> ChatResponse:
    """Process a natural language question via the agentic pipeline."""
    analyzer = get_analyzer()

    if analyzer is None:
        return await _deterministic_fallback(req.question, store)

    app_db = get_app_db()
    system_model = await store.get_latest_system_snapshot()

    try:
        from observibot.agent.chat_agent import run_chat_agent
        result = await run_chat_agent(
            question=req.question,
            provider=analyzer.provider,
            store=store,
            app_db=app_db,
            system_model=system_model,
        )
    except Exception as exc:
        log.warning("Agentic chat failed, using fallback: %s", exc)
        return await _deterministic_fallback(req.question, store)

    return ChatResponse(
        answer=result.answer,
        widget_plan=result.widget_plan,
        vega_lite_spec=result.vega_lite_spec,
        sql_query=(
            "; ".join(result.sql_queries)
            if result.sql_queries else None
        ),
        execution_ms=result.execution_ms,
        domains_hit=result.domains_hit,
        warnings=result.warnings,
    )
