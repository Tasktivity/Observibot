"""Agentic chat pipeline — multi-domain tool calling with plan-then-interpret."""
from __future__ import annotations

import contextlib
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any

import sqlalchemy as sa
import sqlglot
from sqlglot import exp as sqlglot_exp

from observibot.agent.infra_query import execute_infra_query
from observibot.agent.llm_provider import LLMProvider
from observibot.agent.schema_catalog import (
    _is_sensitive_column,
    build_app_schema_description,
    build_observability_schema_description,
    get_app_table_names,
)
from observibot.core.app_db import AppDatabasePool
from observibot.core.code_intelligence.service import CodeKnowledgeService
from observibot.core.models import SystemModel
from observibot.core.sql_sandbox import (
    DEFAULT_EXPLAIN_COST_THRESHOLD,
    QueryValidationError,
    explain_check,
    validate_query,
)
from observibot.core.store import Store

log = logging.getLogger(__name__)

OBSERVABILITY_TABLES = {
    "system_snapshots", "metric_snapshots", "change_events",
    "insights", "alert_history", "business_context",
    "llm_usage",
    # metric_baselines: re-add when seasonal baselines (Step 3) populate it.
    # Today the table is empty, so leaving it in the allowlist lets the LLM
    # generate queries that always return zero rows.
}

# Per-section token budgets for the planning prompt. Total ~17k tokens which
# is well under any model limit and leaves headroom for response + synthesis.
OBS_SCHEMA_BUDGET_TOKENS = 2_000
APP_SCHEMA_BUDGET_TOKENS = 8_000
BUSINESS_CONTEXT_BUDGET_TOKENS = 3_000
# Synthesis: tool results can be wide. Cap before sending to LLM.
TOOL_RESULTS_BUDGET_TOKENS = 15_000
# Thresholds for _log_prompt_size. Warning fires well before any real limit;
# error fires close to the 200k context window so it shows up in logs.
PROMPT_WARN_TOKENS = 30_000
PROMPT_ERROR_TOKENS = 150_000


def _estimate_tokens(text: str) -> int:
    """Rough token estimate: ~4 chars per token. Cheap and good enough."""
    return len(text) // 4


def _enforce_budget(text: str, max_tokens: int, label: str) -> str:
    """Truncate ``text`` so it fits within ``max_tokens`` tokens.

    Truncation is char-based (~4 chars/token). Trims to the last newline in
    the final 30% of the cut so we don't slice mid-entry, then appends a
    visible note so the LLM knows the section was truncated (not silent
    data loss). Returns the original text unchanged if already under budget.
    """
    est = _estimate_tokens(text)
    if est <= max_tokens:
        return text
    max_chars = max_tokens * 4
    truncated = text[:max_chars]
    last_nl = truncated.rfind("\n")
    if last_nl > max_chars * 0.7:
        truncated = truncated[:last_nl]
    log.warning(
        "Prompt section '%s' truncated: ~%d tokens → ~%d tokens (budget=%d)",
        label, est, _estimate_tokens(truncated), max_tokens,
    )
    return (
        truncated
        + f"\n[Truncated: '{label}' exceeded {max_tokens}-token budget]"
    )


def _log_prompt_size(
    prompt: str,
    label: str,
    sections: dict[str, str] | None = None,
) -> None:
    """Log the assembled prompt size with a per-section breakdown.

    DEBUG at normal sizes, WARNING above ``PROMPT_WARN_TOKENS``, ERROR above
    ``PROMPT_ERROR_TOKENS``. The breakdown turns a future 'mystery 400 from
    Anthropic' into a single grep-able log line.
    """
    total_chars = len(prompt)
    total_tokens = total_chars // 4

    if total_tokens > PROMPT_ERROR_TOKENS:
        level = logging.ERROR
    elif total_tokens > PROMPT_WARN_TOKENS:
        level = logging.WARNING
    else:
        level = logging.DEBUG

    breakdown = ""
    if sections:
        parts = [
            f"{k}=~{_estimate_tokens(v)}tok"
            for k, v in sections.items() if v
        ]
        if parts:
            breakdown = f" [{', '.join(parts)}]"

    log.log(
        level,
        "%s prompt: ~%d tokens (%d chars)%s",
        label, total_tokens, total_chars, breakdown,
    )

CORRECTION_PATTERNS = [
    re.compile(r"actually,?\s+(\w[\w\s]*?)\s+means?\s+(.+)", re.IGNORECASE),
    re.compile(r"(\w[\w\s]*?)\s+should be defined as\s+(.+)", re.IGNORECASE),
    re.compile(r"no,?\s+(\w[\w\s]*?)\s+(?:is|means?)\s+(.+)", re.IGNORECASE),
    re.compile(r"correct(?:ion)?:?\s+(\w[\w\s]*?)\s+(?:=|means?|is)\s+(.+)", re.IGNORECASE),
]

# Recognised timeframe phrases. Order matters — most-specific first so "last
# 7 days" isn't shadowed by "last". We normalise to a compact token the planner
# can see at a glance.
_TIMEFRAME_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"interval\s+'?(\d+)\s*(day|hour|week|month|year)s?'?", re.I),
     lambda m: f"last {m.group(1)} {m.group(2).lower()}s"),
    (re.compile(r"(\d+)\s*(day|hour|week|month|year)s?\s+ago", re.I),
     lambda m: f"{m.group(1)} {m.group(2).lower()}s ago"),
    (re.compile(r"\bnow\(\)\s*-\s*interval\s+'?(\d+)\s*(\w+)", re.I),
     lambda m: f"last {m.group(1)} {m.group(2).lower()}"),
    (re.compile(r"current_date|current_timestamp", re.I),
     lambda _m: "today"),
]

_METRIC_NAME_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"metric_name\s*=\s*'([^']+)'", re.I),
    re.compile(r"metric_name\s*=\s*\"([^\"]+)\"", re.I),
    re.compile(r"metric_name\s+IN\s*\(\s*'([^']+)'", re.I),
]


def _extract_metric_from_sql(sql: str) -> str | None:
    """Find the first ``metric_name = '...'`` style filter in a query.

    Returns None if no metric filter is found. This is a regex-based helper —
    it's OK to miss exotic forms (e.g. bound parameters) because missing
    entities are always allowed.
    """
    if not sql:
        return None
    for pat in _METRIC_NAME_PATTERNS:
        m = pat.search(sql)
        if m:
            return m.group(1)
    return None


def _extract_timeframe_from_sql(sql: str) -> str | None:
    """Extract a normalised timeframe hint from SQL, e.g. ``last 7 days``.

    Returns None if no recognisable timeframe is present.
    """
    if not sql:
        return None
    for pat, formatter in _TIMEFRAME_PATTERNS:
        m = pat.search(sql)
        if m:
            try:
                return formatter(m)
            except Exception:
                return None
    return None


def _extract_entities(
    question: str,  # noqa: ARG001  reserved for future NL-driven extraction
    result: ChatResult,
) -> dict:
    """Extract structured entities from a completed chat exchange.

    Every field is optional and defaults to None. The planning prompt uses
    these to resolve references across turns ("break that down by month"
    needs to know *that* = the previous turn's table).
    """
    entities: dict[str, Any] = {
        "domain": None,
        "table": None,
        "all_tables": None,
        "metric": None,
        "timeframe": None,
        "widget_type": None,
    }

    if result.domains_hit:
        entities["domain"] = result.domains_hit[0]

    primary_sql = result.sql_queries[0] if result.sql_queries else ""

    # Tables from sqlglot. If the parse fails, entities stay None — we never
    # want entity extraction to break the exchange.
    if primary_sql:
        try:
            parsed = sqlglot.parse_one(primary_sql, dialect="postgres")
            tables = [t.name for t in parsed.find_all(sqlglot_exp.Table) if t.name]
            if tables:
                # Deduplicate preserving order; keep the first as the primary.
                seen: list[str] = []
                for t in tables:
                    if t not in seen:
                        seen.append(t)
                entities["table"] = seen[0]
                entities["all_tables"] = seen
        except Exception as exc:
            log.debug("Entity table extraction failed: %s", exc)

    if primary_sql:
        entities["metric"] = _extract_metric_from_sql(primary_sql)
        entities["timeframe"] = _extract_timeframe_from_sql(primary_sql)

    if result.widget_plan:
        entities["widget_type"] = result.widget_plan.get("widget_type")

    return entities


def _smart_truncate(text: str, max_chars: int) -> str:
    """Truncate at a word boundary instead of cutting mid-word.

    Falls back to a hard char cut only if no space exists in the last half
    of the window (prevents truncating a single long token to almost nothing).
    Returns the text unchanged if it's already within budget.
    """
    if not text:
        return text
    if len(text) <= max_chars:
        return text
    truncated = text[:max_chars]
    last_space = truncated.rfind(" ")
    if last_space > max_chars * 0.5:
        truncated = truncated[:last_space]
    return truncated + "..."


def _build_session_context(
    turns: list[dict], max_tokens: int = 1000,
) -> str:
    """Render prior turns into a compact prompt section with a token budget.

    Turns are formatted newest-last (time-ordered) but budgeted newest-first
    so the most recent turn is always included. The trailing ``Current
    conversation state`` block surfaces the latest entities explicitly so the
    planner can reference them without re-parsing the summary text.
    """
    if not turns:
        return ""

    token_count = 0
    included: list[dict] = []
    for turn in reversed(turns):
        line = " | ".join(_turn_to_parts(turn))
        est = _estimate_tokens(line)
        if token_count + est > max_tokens:
            break
        included.append(turn)
        token_count += est
    included.reverse()

    if not included:
        return ""

    numbered = [
        f"Turn {i + 1}: " + " | ".join(_turn_to_parts(t))
        for i, t in enumerate(included)
    ]
    last_entities = included[-1].get("entities") or {}
    state_block = _format_state_block(included[-1], last_entities)

    return (
        "\n## Conversation Context (prior turns)\n"
        + "\n".join(numbered)
        + "\n"
        + state_block
    )


def _turn_to_parts(turn: dict) -> list[str]:
    """Shared renderer used both for budgeting and final layout."""
    parts = [f"User asked: {turn.get('question_summary', '')}"]
    entities = turn.get("entities") or {}
    if entities.get("table"):
        parts.append(f"Table: {entities['table']}")
    if entities.get("domain"):
        parts.append(f"Domain: {entities['domain']}")
    if entities.get("metric"):
        parts.append(f"Metric: {entities['metric']}")
    if entities.get("timeframe"):
        parts.append(f"Timeframe: {entities['timeframe']}")
    if turn.get("sql"):
        parts.append(f"SQL: {_smart_truncate(turn['sql'], 120)}")
    if turn.get("answer_summary"):
        parts.append(f"Answered: {_smart_truncate(turn['answer_summary'], 120)}")
    return parts


def _format_state_block(latest_turn: dict, entities: dict) -> str:
    """Render the latest turn as explicit defaults for reference resolution."""
    if not entities and not latest_turn.get("sql"):
        return ""
    lines = ["\n## Current conversation state"]
    lines.append(f"Last table: {entities.get('table') or 'unknown'}")
    lines.append(f"Last domain: {entities.get('domain') or 'unknown'}")
    sql = latest_turn.get("sql") or ""
    lines.append(f"Last SQL: {_smart_truncate(sql, 200) if sql else 'none'}")
    lines.append(f"Last timeframe: {entities.get('timeframe') or 'none specified'}")
    if entities.get("metric"):
        lines.append(f"Last metric: {entities['metric']}")
    return "\n".join(lines) + "\n"

MULTI_TURN_INSTRUCTIONS = """
## Multi-turn resolution
If conversation context is provided below, use it to resolve references:
- "that", "those", "the same" → refer to the previous query's subject
  (see "Last table" and "Last SQL" in the state block)
- "break it down", "by month", "by category" → add a GROUP BY on the previous
  query's primary table; keep the same metric/entity
- "last 7 days", "this week", "today" → modify the previous query's time
  filter; add a WHERE clause if none existed
- "more detail", "elaborate", "expand" → re-query with a higher LIMIT or add
  columns the previous query omitted
- "the same but for X" → reuse the prior query structure and swap the entity

When prior context includes a SQL query, prefer adapting it rather than
starting from scratch. Default to the entities in the "Current conversation
state" block when the current question is ambiguous. If the user asks a
standalone question (no pronouns, new subject), ignore the prior context.
"""

PLANNING_PROMPT = """\
You are Observibot, an AI SRE assistant. The user asked a question about their
system. You have access to these tools:

1. query_observability(sql) — Query Observibot's monitoring store for metrics,
   anomalies, alerts, insights, and LLM usage.
   Available tables:
{obs_schema}

{app_tool_section}

3. query_infrastructure(action, params) — Get service status, deployment
   history, or service details from the infrastructure platform.
   Actions: service_status, deployment_history, service_details.
   Params: service_name (optional), since_hours (optional, default 48).

{business_context_section}

Decide which tool(s) to call. Output VALID JSON ONLY:
{{
  "tool_calls": [
    {{"name": "query_observability|query_application|query_infrastructure",
      "parameters": {{"sql": "SELECT ..."}} or {{"action": "...", "params": {{}}}} }}
  ],
  "reasoning": "Why these tools answer the question"
}}

Rules:
- Generate ONLY SELECT statements for SQL tools
- Always include LIMIT (max 500)
- For trends, include time columns and ORDER BY them
- If uncertain which domain, prefer query_observability first
- You can call multiple tools if the question spans domains

User question: {question}
"""

SYNTHESIS_PROMPT = """\
You are Observibot, an AI SRE assistant. The user asked a question and tools
were executed. Interpret the results into a clear, actionable answer.

Respond with VALID JSON ONLY:
{{
  "narrative": "2-3 sentence answer with actual values. Be specific about
    numbers, trends, and health status. Format for humans (percentages,
    relative times). Never say 'Found N results.'",
  "widget_config": {{
    "widget_type": "kpi_number|time_series|categorical_bar|table|status|text_summary",
    "title": "Human-readable title",
    "value_field": "column name containing the primary value",
    "value": null,
    "format": "percent|number|bytes|duration",
    "x_field": "column for x-axis (charts only)",
    "y_field": "column for y-axis (charts only)",
    "columns": ["col1", "col2"]
  }},
  "domains": ["observability", "application", "infrastructure"],
  "freshness": "from latest data|stale",
  "warnings": []
}}

If data is empty, say so honestly. Do NOT fabricate values.

User question: {question}

Tool results:
{tool_results_text}
"""


@dataclass
class ToolResult:
    domain: str
    rows: list[dict] = field(default_factory=list)
    sql: str | None = None
    success: bool = True
    error: str | None = None


@dataclass
class ChatResult:
    answer: str
    widget_plan: dict | None = None
    vega_lite_spec: dict | None = None
    sql_queries: list[str] = field(default_factory=list)
    domains_hit: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    execution_ms: float | None = None
    data: list[dict] = field(default_factory=list)


async def run_chat_agent(
    question: str,
    provider: LLMProvider,
    store: Store,
    app_db: AppDatabasePool | None,
    system_model: SystemModel | None,
    session_context: list[dict] | None = None,
    explain_cost_threshold: float = DEFAULT_EXPLAIN_COST_THRESHOLD,
    statement_timeout_ms: int = 3000,
) -> ChatResult:
    """Execute the agentic chat pipeline.

    ``explain_cost_threshold`` and ``statement_timeout_ms`` come from
    ``ChatConfig`` and control layers 4 and 5 of the SQL sandbox.
    """
    start = time.monotonic()

    app_enabled = app_db is not None and app_db.is_connected
    obs_schema = _enforce_budget(
        build_observability_schema_description(),
        OBS_SCHEMA_BUDGET_TOKENS,
        "obs_schema",
    )
    app_section = ""
    if app_enabled:
        app_desc = build_app_schema_description(system_model, question=question)
        app_section = (
            "2. query_application(sql) — Query the monitored application's "
            "production database (read-only). Use for app-specific data.\n"
            f"   Available tables:\n{app_desc}"
        )
        app_section = _enforce_budget(
            app_section, APP_SCHEMA_BUDGET_TOKENS, "app_schema",
        )
    else:
        app_section = (
            "2. query_application — NOT AVAILABLE. App database queries "
            "are disabled. If the user asks about application data, explain "
            "that this feature needs to be enabled in config."
        )

    business_context_section = ""
    freshness_warning: str | None = None
    try:
        knowledge_service = CodeKnowledgeService(store)
        if await knowledge_service.should_inject_context(question):
            freshness = await knowledge_service.get_freshness_status()
            if freshness["status"] == "unavailable":
                pass
            else:
                facts = await knowledge_service.get_context_for_question(question)
                if facts:
                    business_context_section = _enforce_budget(
                        await knowledge_service.format_context_for_prompt(facts),
                        BUSINESS_CONTEXT_BUDGET_TOKENS,
                        "business_context",
                    )
                freshness_warning = await knowledge_service.get_freshness_warning()
    except Exception as exc:
        log.debug("Business context injection skipped: %s", exc)

    session_section = _build_session_context(session_context or [])
    if session_section:
        session_section = MULTI_TURN_INSTRUCTIONS + session_section

    planning_prompt = PLANNING_PROMPT.format(
        obs_schema=obs_schema,
        app_tool_section=app_section,
        business_context_section=business_context_section + session_section,
        question=question,
    )
    _log_prompt_size(
        planning_prompt,
        "Planning",
        {
            "obs_schema": obs_schema,
            "app_section": app_section,
            "business_context": business_context_section,
            "session": session_section,
            "question": question,
        },
    )

    plan_response = await provider.analyze(
        system_prompt="You are Observibot. Output only JSON.",
        user_prompt=planning_prompt,
    )

    tool_calls = plan_response.data.get("tool_calls", [])

    if not tool_calls:
        text = plan_response.data.get(
            "narrative",
            plan_response.data.get("reasoning", "I'm not sure how to answer that."),
        )
        return ChatResult(
            answer=text,
            execution_ms=_elapsed(start),
        )

    tool_results: list[ToolResult] = []
    for tc in tool_calls:
        name = tc.get("name", "")
        params = tc.get("parameters", {})
        result = await _execute_tool(
            name, params, store, app_db, system_model,
            explain_cost_threshold=explain_cost_threshold,
            statement_timeout_ms=statement_timeout_ms,
        )
        tool_results.append(result)

    results_text = _enforce_budget(
        _format_tool_results(tool_results),
        TOOL_RESULTS_BUDGET_TOKENS,
        "tool_results",
    )

    synthesis_prompt = SYNTHESIS_PROMPT.format(
        question=question,
        tool_results_text=results_text,
    )
    _log_prompt_size(
        synthesis_prompt,
        "Synthesis",
        {"tool_results": results_text, "question": question},
    )

    synth_response = await provider.analyze(
        system_prompt="You are Observibot. Output only JSON.",
        user_prompt=synthesis_prompt,
    )

    narrative = synth_response.data.get("narrative", "")
    widget_config = synth_response.data.get("widget_config")
    domains = synth_response.data.get("domains", [])
    warnings = synth_response.data.get("warnings", [])
    if freshness_warning:
        warnings.append(freshness_warning)

    all_data: list[dict] = []
    for tr in tool_results:
        if tr.success:
            all_data.extend(tr.rows)

    unsupported = _find_unsupported_numbers(narrative, tool_results)
    if unsupported:
        warnings.append(
            "Narrative cites numbers not found in query results: "
            + ", ".join(unsupported[:5])
            + ". Re-run the question if precision matters."
        )
        log.warning(
            "Synthesis included %d unsupported number(s): %s",
            len(unsupported), unsupported[:10],
        )

    widget_plan = _build_widget_plan(widget_config, all_data)
    vega = _build_vega_spec(widget_plan) if widget_plan else None

    try:
        await _detect_and_store_correction(question, store)
    except Exception as exc:
        log.debug("Correction detection skipped: %s", exc)

    return ChatResult(
        answer=narrative,
        widget_plan=widget_plan,
        vega_lite_spec=vega,
        sql_queries=[
            tr.sql for tr in tool_results if tr.sql
        ],
        domains_hit=domains or [
            tr.domain for tr in tool_results if tr.success
        ],
        warnings=warnings,
        execution_ms=_elapsed(start),
        data=all_data,
    )


async def _execute_tool(
    name: str,
    params: dict,
    store: Store,
    app_db: AppDatabasePool | None,
    system_model: SystemModel | None,
    *,
    explain_cost_threshold: float = DEFAULT_EXPLAIN_COST_THRESHOLD,
    statement_timeout_ms: int = 3000,
) -> ToolResult:
    if name == "query_observability":
        return await _exec_observability(
            params, store,
            explain_cost_threshold=explain_cost_threshold,
            statement_timeout_ms=statement_timeout_ms,
        )
    if name == "query_application":
        return await _exec_application(
            params, app_db, system_model,
            explain_cost_threshold=explain_cost_threshold,
        )
    if name == "query_infrastructure":
        return await _exec_infrastructure(params, store, system_model)
    return ToolResult(
        domain="unknown", success=False,
        error=f"Unknown tool: {name}",
    )


def _expensive_query_message(cost: float) -> str:
    """User-facing message when EXPLAIN rejects a query."""
    return (
        f"Query too expensive (estimated cost: {cost:.0f}). "
        "Try narrowing the time range or adding filters."
    )


async def _exec_observability(
    params: dict,
    store: Store,
    *,
    explain_cost_threshold: float = DEFAULT_EXPLAIN_COST_THRESHOLD,
    statement_timeout_ms: int = 3000,
) -> ToolResult:
    sql = params.get("sql", "")
    try:
        validated = validate_query(sql, OBSERVABILITY_TABLES)
    except QueryValidationError as e:
        return ToolResult(
            domain="observability", sql=sql, success=False,
            error=f"Query validation failed: {e}",
        )

    # Layer 4: EXPLAIN cost gating (no-op on SQLite).
    try:
        is_ok, cost = await explain_check(
            store.engine, validated, explain_cost_threshold,
        )
        if not is_ok:
            log.info(
                "EXPLAIN rejected observability query (cost=%.0f, threshold=%.0f)",
                cost, explain_cost_threshold,
            )
            return ToolResult(
                domain="observability", sql=validated, success=False,
                error=_expensive_query_message(cost),
            )
    except Exception as exc:  # never block execution on EXPLAIN failure
        log.debug("EXPLAIN check skipped: %s", exc)

    try:
        async with store.engine.begin() as conn:
            # Layer 5: statement_timeout for PostgreSQL. SQLite has no
            # equivalent pragma, so we rely on the Python-level timeout
            # surfaced by the driver.
            if "postgresql" in str(store.engine.url):
                await conn.execute(sa.text(
                    f"SET LOCAL statement_timeout = '{statement_timeout_ms}'"
                ))
            result = await conn.execute(sa.text(validated))
            raw_rows = result.fetchall()
            columns = list(result.keys())
        rows = [
            dict(zip(columns, r, strict=False)) for r in raw_rows
        ]
        for row in rows:
            for k, v in row.items():
                if not isinstance(
                    v, (str, int, float, bool, type(None))
                ):
                    row[k] = str(v)
        return ToolResult(
            domain="observability", sql=validated,
            rows=rows, success=True,
        )
    except Exception as e:
        return ToolResult(
            domain="observability", sql=validated, success=False,
            error=str(e),
        )


async def _exec_application(
    params: dict,
    app_db: AppDatabasePool | None,
    system_model: SystemModel | None,
    *,
    explain_cost_threshold: float = DEFAULT_EXPLAIN_COST_THRESHOLD,
) -> ToolResult:
    if app_db is None or not app_db.is_connected:
        return ToolResult(
            domain="application", success=False,
            error="Application database queries are not enabled. "
            "Enable in config: chat.enable_app_queries: true",
        )
    sql = params.get("sql", "")
    allowed = get_app_table_names(system_model)
    try:
        validated = validate_query(sql, allowed)
    except QueryValidationError as e:
        return ToolResult(
            domain="application", sql=sql, success=False,
            error=f"Query validation failed: {e}",
        )

    # Layer 4: EXPLAIN cost gating via the app pool. We run EXPLAIN on a
    # borrowed connection (same statement_timeout applies) so we share the
    # same pool slot as the ultimate query — this keeps the safety budget
    # tight and avoids asymmetry between the plan check and the real exec.
    try:
        async def _run_explain(sql_to_explain: str) -> Any:
            async with app_db.acquire() as conn:
                row = await conn.fetchrow(sql_to_explain)
                return row[0] if row else None

        is_ok, cost = await explain_check(
            _run_explain, validated, explain_cost_threshold,
        )
        if not is_ok:
            log.info(
                "EXPLAIN rejected application query (cost=%.0f, threshold=%.0f)",
                cost, explain_cost_threshold,
            )
            return ToolResult(
                domain="application", sql=validated, success=False,
                error=_expensive_query_message(cost),
            )
    except Exception as exc:
        log.debug("EXPLAIN check skipped (application): %s", exc)

    try:
        # Layer 5: statement_timeout is already set on every connection in
        # AppDatabasePool.acquire(); no additional wiring needed here.
        rows = await app_db.execute_sandboxed(validated)
        for row in rows:
            for key in list(row.keys()):
                if _is_sensitive_column(key):
                    row[key] = "[REDACTED]"
        return ToolResult(
            domain="application", sql=validated,
            rows=rows, success=True,
        )
    except Exception as e:
        return ToolResult(
            domain="application", sql=validated, success=False,
            error=str(e),
        )


async def _exec_infrastructure(
    params: dict,
    store: Store,
    system_model: SystemModel | None,
) -> ToolResult:
    action = params.get("action", "service_status")
    action_params = params.get("params", {})
    try:
        rows = await execute_infra_query(
            action, action_params, store, system_model,
        )
        return ToolResult(
            domain="infrastructure", rows=rows, success=True,
        )
    except Exception as e:
        return ToolResult(
            domain="infrastructure", success=False, error=str(e),
        )


_TIME_COLUMN_SUFFIXES = (
    "_at", "_date", "_time", "_ts", "day", "hour", "bucket", "month",
)


def _sample_rows(rows: list[dict], max_rows: int = 50) -> tuple[list[dict], str]:
    """Smart sampling based on result shape.

    Pipeline-audit Fix 5: replace the head-only 20-row sample, which silently
    deceived the LLM on time-series and distribution questions (it would
    narrate from 2-4% of the data as if it were the full picture). Returns
    ``(sample, description)`` so the LLM knows what kind of slice it's seeing.
    """
    n = len(rows)
    if n <= max_rows:
        return rows, f"{n} rows (complete)"

    cols = list(rows[0].keys()) if rows else []
    time_cols = [
        c for c in cols
        if any(c.lower().endswith(s) for s in _TIME_COLUMN_SUFFIXES)
    ]

    if time_cols:
        # Stratified time-series sample: first 10, last 10, ~10 evenly from the
        # middle. The LLM can see start, end, and trend without us paying for
        # the entire result set.
        head = 10
        tail = 10
        indices: set[int] = set(range(min(head, n)))
        indices |= set(range(max(0, n - tail), n))
        middle_lo, middle_hi = head, max(head, n - tail)
        if middle_hi > middle_lo:
            step = max(1, (middle_hi - middle_lo) // 10)
            indices |= set(range(middle_lo, middle_hi, step))
        sample = [rows[i] for i in sorted(indices) if i < n]
        return (
            sample,
            f"{len(sample)} of {n} rows (time-series sample: first/last/middle)",
        )

    numeric_cols = [
        c for c in cols if isinstance(rows[0].get(c), (int, float))
        and not isinstance(rows[0].get(c), bool)
    ]
    if numeric_cols:
        sample = rows[:20]
        stats: dict[str, dict[str, Any]] = {}
        for col in numeric_cols[:3]:
            values = [
                r[col] for r in rows
                if isinstance(r.get(col), (int, float))
                and not isinstance(r.get(col), bool)
            ]
            if values:
                stats[col] = {
                    "min": min(values),
                    "max": max(values),
                    "count": len(values),
                }
        return (
            sample,
            f"20 of {n} rows (head sample). Stats: {json.dumps(stats, default=str)}",
        )

    # Default: head + tail so the LLM at least sees both ends of the range.
    sample = rows[:15] + rows[-5:]
    return sample, f"20 of {n} rows (first 15 + last 5)"


_NARRATIVE_NUMBER_RE = re.compile(r"(?<![A-Za-z_])(\d[\d,]*(?:\.\d+)?)(?![A-Za-z_])")


def _find_unsupported_numbers(
    narrative: str, results: list[ToolResult],
) -> list[str]:
    """Flag numbers in the narrative that aren't traceable to tool results.

    A narrative like ``"117 jobs and 68 files stuck"`` where neither 117 nor
    68 appear in any fetched row or cell is a hallucination — the exact
    failure mode we saw in production. This doesn't block the response (that
    would be too aggressive for a chat UI) but surfaces the problem as a
    warning so the user and future tests can see it.

    Returns the list of suspicious number strings (empty if all accounted for).
    """
    if not narrative or not results:
        return []

    # Collect every numeric value and row-count from every successful tool
    # result. Row values are cast to string and matched by substring so
    # percentages, ratios, and formatted variants all hit.
    supported: set[float] = set()
    supported_strings: set[str] = set()
    total_rows_across_tools = 0
    column_sums: dict[str, float] = {}

    for tr in results:
        if not tr.success:
            continue
        total_rows_across_tools += len(tr.rows)
        supported.add(float(len(tr.rows)))
        for row in tr.rows:
            for k, v in row.items():
                if v is None:
                    continue
                if isinstance(v, bool):
                    continue
                if isinstance(v, (int, float)):
                    f = float(v)
                    supported.add(f)
                    # Percentages: if values look like fractions, also allow
                    # the 100x form that narratives usually prefer.
                    if 0 < abs(f) < 1:
                        supported.add(round(f * 100, 4))
                    column_sums[k] = column_sums.get(k, 0.0) + f
                else:
                    s = str(v)
                    supported_strings.add(s)
                    with contextlib.suppress(ValueError):
                        supported.add(float(s.replace(",", "")))
    for s in column_sums.values():
        supported.add(round(s, 4))
    supported.add(float(total_rows_across_tools))

    unsupported: list[str] = []
    for match in _NARRATIVE_NUMBER_RE.finditer(narrative):
        token = match.group(1)
        if token in supported_strings:
            continue
        # Skip very small integers: they're usually structural ("2-3 sentence
        # answer", "1 year", "first 10") rather than data claims.
        try:
            value = float(token.replace(",", ""))
        except ValueError:
            continue
        if value < 10 and value == int(value):
            continue
        # Allow rounding slop: narratives round 99.3% → 99% and 836 → 840.
        # We accept within ±2% of any supported value, or exact match on the
        # integer part.
        if any(
            abs(value - s) <= max(0.5, abs(s) * 0.02)
            for s in supported
        ):
            continue
        # Date-like years (1900-2100) are usually structural.
        if value == int(value) and 1900 <= value <= 2100:
            continue
        unsupported.append(token)
    return unsupported


def _format_tool_results(results: list[ToolResult]) -> str:
    parts = []
    for r in results:
        header = f"[{r.domain}]"
        if not r.success:
            parts.append(f"{header} ERROR: {r.error}")
            continue
        if r.sql:
            parts.append(f"{header} SQL: {r.sql}")
        if r.rows:
            sample, description = _sample_rows(r.rows)
            parts.append(
                f"{header} {description}\n"
                + json.dumps(sample, indent=2, default=str)
            )
        else:
            parts.append(f"{header} 0 rows returned.")
    return "\n\n".join(parts)


def _build_widget_plan(
    config: dict | None, data: list[dict],
) -> dict | None:
    if not config or not data:
        return None
    plan: dict[str, Any] = {
        "widget_type": config.get("widget_type", "table"),
        "title": config.get("title", ""),
        "data": data,
    }
    if config.get("value") is not None:
        plan.setdefault("config", {})["value"] = config["value"]
    if config.get("format"):
        plan.setdefault("config", {})["format"] = config["format"]
    if config.get("value_field"):
        plan.setdefault("config", {})["value_field"] = config[
            "value_field"
        ]
    if config.get("columns"):
        plan.setdefault("config", {})["columns"] = config["columns"]
    encoding = {}
    if config.get("x_field"):
        encoding["x"] = config["x_field"]
    if config.get("y_field"):
        encoding["y"] = config["y_field"]
    if encoding:
        plan["encoding"] = encoding
    return plan


def _build_vega_spec(plan: dict | None) -> dict | None:
    if not plan:
        return None
    wtype = plan.get("widget_type", "")
    data = plan.get("data", [])
    enc = plan.get("encoding", {})

    if wtype == "time_series" and enc.get("x") and enc.get("y"):
        return {
            "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
            "data": {"values": data},
            "mark": "line",
            "encoding": {
                "x": {"field": enc["x"], "type": "temporal"},
                "y": {"field": enc["y"], "type": "quantitative"},
            },
        }
    if (
        wtype == "categorical_bar"
        and enc.get("x")
        and enc.get("y")
    ):
        return {
            "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
            "data": {"values": data},
            "mark": "bar",
            "encoding": {
                "x": {"field": enc["x"], "type": "nominal"},
                "y": {"field": enc["y"], "type": "quantitative"},
            },
        }
    return None


async def _detect_and_store_correction(question: str, store: Store) -> None:
    """Detect user corrections in the question and store them."""
    for pattern in CORRECTION_PATTERNS:
        m = pattern.search(question)
        if m:
            concept = m.group(1).strip().lower()
            claim = m.group(2).strip().rstrip(".")
            await store.save_user_correction(
                concept=concept,
                claim=claim,
                tables=[],
                columns=[],
            )
            log.info("Stored user correction: '%s' = '%s'", concept, claim)
            return


def _elapsed(start: float) -> float:
    return round((time.monotonic() - start) * 1000, 1)
