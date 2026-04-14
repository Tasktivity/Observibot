"""Pydantic request/response models for the REST API."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

OUTCOME_TYPE = Literal["noise", "actionable", "investigating", "resolved"]


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
