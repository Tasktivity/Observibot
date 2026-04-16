"""Pydantic request/response models for the REST API."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

OUTCOME_TYPE = Literal[
    "noise", "actionable", "investigating", "resolved", "acknowledged",
]


class LoginRequest(BaseModel):
    email: str
    password: str


class RegisterRequest(BaseModel):
    email: str
    password: str


class UserResponse(BaseModel):
    id: str
    email: str
    is_admin: bool


class InsightResponse(BaseModel):
    id: str
    severity: str
    title: str
    summary: str
    details: str = ""
    recommended_actions: list[str] = []
    related_metrics: list[str] = []
    related_tables: list[str] = []
    confidence: float = 0.5
    source: str = "llm"
    is_hypothesis: bool = False
    created_at: str
    recurrence_context: dict | None = None
    # Step 3.4 unified evidence carrier, serialized from
    # :class:`observibot.core.evidence.EvidenceBundle`. May contain
    # ``recurrence``, ``correlations``, and ``diagnostics``. Rendered in
    # the Discovery Feed so the operator can inspect the actual queries
    # the agent ran rather than just its narrative.
    evidence: dict | None = None
    run_id: str | None = None


class MetricResponse(BaseModel):
    id: str
    connector_name: str
    metric_name: str
    value: float
    labels: dict[str, str] = {}
    collected_at: str


class WidgetCreate(BaseModel):
    widget_type: str
    title: str = ""
    config: dict | None = None
    layout: dict | None = None
    data_source: dict | None = None


class WidgetUpdate(BaseModel):
    title: str | None = None
    config: dict | None = None
    layout: dict | None = None
    data_source: dict | None = None
    pinned: bool | None = None


class WidgetResponse(BaseModel):
    id: str
    user_id: str | None = None
    widget_type: str
    title: str = ""
    config: dict | None = None
    layout: dict | None = None
    data_source: dict | None = None
    schema_version: int = 1
    pinned: bool = True
    created_at: str | None = None
    updated_at: str | None = None


class LayoutItem(BaseModel):
    id: str
    x: int
    y: int
    w: int
    h: int


class BatchLayoutUpdate(BaseModel):
    items: list[LayoutItem]


class InsightFeedbackRequest(BaseModel):
    outcome: OUTCOME_TYPE
    note: str | None = None


class InsightFeedbackResponse(BaseModel):
    id: int | None = None
    insight_id: str
    user_id: str | None = None
    outcome: str
    note: str | None = None
    created_at: str


class ChatRequest(BaseModel):
    question: str
    session_id: str | None = None


class ChatResponse(BaseModel):
    answer: str = ""
    widget_plan: dict | None = None
    vega_lite_spec: dict | None = None
    sql_query: str | None = None
    execution_ms: float | None = None
    domains_hit: list[str] = []
    warnings: list[str] = []
    session_id: str = ""
    # True when the LLM pipeline failed and the answer was produced by the
    # deterministic keyword-SQL fallback. Frontend surfaces a visual indicator
    # so users can tell "real LLM answer" from "fallback because things broke."
    fallback: bool = False


class SystemStatusResponse(BaseModel):
    status: str
    version: str
    connectors: list[dict] = []
    monitor_running: bool = False


class CostResponse(BaseModel):
    calls: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0
    since: str = ""


class HealthResponse(BaseModel):
    status: str
    version: str


class CodeIntelligenceStatusResponse(BaseModel):
    status: str  # "current", "stale", "unavailable", "error"
    last_indexed_commit: str | None = None
    last_index_time: str | None = None
    error_message: str | None = None


class MonitorIntervalsResponse(BaseModel):
    collection_interval_seconds: int
    analysis_interval_seconds: int


class MonitorIntervalsUpdate(BaseModel):
    collection_interval_seconds: int | None = None
    analysis_interval_seconds: int | None = None


class SemanticFactResponse(BaseModel):
    id: str
    fact_type: str
    concept: str
    claim: str
    tables: list[str] = []
    columns: list[str] = []
    sql_condition: str | None = None
    source: str
    confidence: float
    is_active: bool
    created_at: str | None = None
    updated_at: str | None = None


class FactUpdateRequest(BaseModel):
    is_active: bool | None = None
    claim: str | None = None
    confidence: float | None = None


class BusinessContextEntry(BaseModel):
    key: str
    value: str  # JSON string or plain text


class FeedbackSummaryResponse(BaseModel):
    total: int
    since_days: int
    by_outcome: dict[str, int]
    recent: list[dict]  # list of {insight_id, insight_title, outcome, note, created_at}


class KnowledgeStatsResponse(BaseModel):
    total_facts: int
    active_facts: int
    inactive_facts: int
    facts_by_source: dict[str, int]
    facts_by_type: dict[str, int]
    total_feedback: int
    feedback_by_outcome: dict[str, int]
    total_events: int
    code_intelligence_status: str
    last_indexed_commit: str | None = None
    last_index_time: str | None = None
