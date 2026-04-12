"""Agentic chat pipeline — multi-domain tool calling with plan-then-interpret."""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

import sqlalchemy as sa

from observibot.agent.infra_query import execute_infra_query
from observibot.agent.llm_provider import LLMProvider
from observibot.agent.schema_catalog import (
    build_app_schema_description,
    build_observability_schema_description,
    get_app_table_names,
)
from observibot.core.app_db import AppDatabasePool
from observibot.core.models import SystemModel
from observibot.core.sql_sandbox import QueryValidationError, validate_query
from observibot.core.store import Store

log = logging.getLogger(__name__)

OBSERVABILITY_TABLES = {
    "system_snapshots", "metric_snapshots", "change_events",
    "insights", "alert_history", "business_context",
    "llm_usage", "metric_baselines",
}

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
) -> ChatResult:
    """Execute the agentic chat pipeline."""
    start = time.monotonic()

    app_enabled = app_db is not None and app_db.is_connected
    obs_schema = build_observability_schema_description()
    app_section = ""
    if app_enabled:
        app_desc = build_app_schema_description(system_model)
        app_section = (
            "2. query_application(sql) — Query the monitored application's "
            "production database (read-only). Use for app-specific data.\n"
            f"   Available tables:\n{app_desc}"
        )
    else:
        app_section = (
            "2. query_application — NOT AVAILABLE. App database queries "
            "are disabled. If the user asks about application data, explain "
            "that this feature needs to be enabled in config."
        )

    planning_prompt = PLANNING_PROMPT.format(
        obs_schema=obs_schema,
        app_tool_section=app_section,
        question=question,
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
        )
        tool_results.append(result)

    results_text = _format_tool_results(tool_results)

    synthesis_prompt = SYNTHESIS_PROMPT.format(
        question=question,
        tool_results_text=results_text,
    )

    synth_response = await provider.analyze(
        system_prompt="You are Observibot. Output only JSON.",
        user_prompt=synthesis_prompt,
    )

    narrative = synth_response.data.get("narrative", "")
    widget_config = synth_response.data.get("widget_config")
    domains = synth_response.data.get("domains", [])
    warnings = synth_response.data.get("warnings", [])

    all_data: list[dict] = []
    for tr in tool_results:
        if tr.success:
            all_data.extend(tr.rows)

    widget_plan = _build_widget_plan(widget_config, all_data)
    vega = _build_vega_spec(widget_plan) if widget_plan else None

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
) -> ToolResult:
    if name == "query_observability":
        return await _exec_observability(params, store)
    if name == "query_application":
        return await _exec_application(params, app_db, system_model)
    if name == "query_infrastructure":
        return await _exec_infrastructure(params, store, system_model)
    return ToolResult(
        domain="unknown", success=False,
        error=f"Unknown tool: {name}",
    )


async def _exec_observability(params: dict, store: Store) -> ToolResult:
    sql = params.get("sql", "")
    try:
        validated = validate_query(sql, OBSERVABILITY_TABLES)
    except QueryValidationError as e:
        return ToolResult(
            domain="observability", sql=sql, success=False,
            error=f"Query validation failed: {e}",
        )
    try:
        async with store.engine.begin() as conn:
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
    try:
        rows = await app_db.execute_sandboxed(validated)
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
            sample = r.rows[:20]
            parts.append(
                f"{header} {len(r.rows)} rows. Sample:\n"
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


def _elapsed(start: float) -> float:
    return round((time.monotonic() - start) * 1000, 1)
