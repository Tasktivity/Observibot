"""Pydantic schemas for LLM response validation.

LLMs do not reliably produce well-formed JSON just because a prompt says so.
These schemas let the analyzer distinguish between a provider-side problem
(failed API call, retriable) and a content-side problem (invalid payload,
should not silently drop the underlying signal).
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Severity = Literal["critical", "warning", "info", "discovery"]


class LLMInsightResponse(BaseModel):
    """A single insight in an LLM analysis response."""

    model_config = ConfigDict(extra="ignore")

    severity: Severity
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=4000)
    summary: str = Field(default="", max_length=2000)
    suggested_action: str = Field(default="", max_length=2000)
    recommended_actions: list[str] = Field(default_factory=list)
    related_metrics: list[str] = Field(default_factory=list)
    related_tables: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0, default=0.5)
    uncertainty_reason: str | None = None

    @field_validator("recommended_actions", mode="before")
    @classmethod
    def _coerce_actions(cls, value: object) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            return [value]
        if isinstance(value, list):
            return [str(v) for v in value]
        return [str(value)]

    @field_validator("related_metrics", "related_tables", mode="before")
    @classmethod
    def _coerce_list(cls, value: object) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            return [value]
        if isinstance(value, list):
            return [str(v) for v in value]
        return [str(value)]

    def merged_description(self) -> str:
        """Return the best available text for the Insight body."""
        return self.description or self.summary


class LLMAnalysisResponse(BaseModel):
    """Top-level anomaly-analysis response."""

    model_config = ConfigDict(extra="ignore")

    insights: list[LLMInsightResponse] = Field(default_factory=list)


class LLMSystemAnalysis(BaseModel):
    """Top-level semantic system-analysis response."""

    model_config = ConfigDict(extra="ignore")

    app_type: str = ""
    app_description: str = ""
    summary: str = ""
    critical_tables: list[str] = Field(default_factory=list)
    core_entities: list[dict] = Field(default_factory=list)
    key_metrics: list[str] = Field(default_factory=list)
    business_metrics: list[dict] = Field(default_factory=list)
    monitoring_rules: list[dict] = Field(default_factory=list)
    correlations: list[dict] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    questions: list[str] = Field(default_factory=list)


class LLMQueryResponse(BaseModel):
    """Top-level ad-hoc query response."""

    model_config = ConfigDict(extra="ignore")

    answer: str = ""
    evidence: list[str] = Field(default_factory=list)
    follow_ups: list[str] = Field(default_factory=list)


class DiagnosticQuery(BaseModel):
    """One hypothesis + the SQL the LLM wants to run to confirm or rule it out.

    Step 3.4 hypothesis-test loop: the LLM emits up to three of these per
    alertable anomaly set, and the monitor pushes them through the 5-layer
    sandbox before feeding the results back into synthesis. SQL is hard-
    capped at 2000 chars because anything longer is almost certainly a
    hallucinated pasted schema and should be rejected early.
    """

    model_config = ConfigDict(extra="ignore")

    hypothesis: str = Field(..., min_length=1, max_length=500)
    sql: str = Field(..., min_length=1, max_length=2000)
    explanation: str = Field(default="", max_length=500)


class DiagnosticHypothesisResponse(BaseModel):
    """LLM response shape for the diagnostic-query generation (Call A) step.

    Pydantic's ``max_length=3`` enforces the hard cap; the analyzer
    additionally truncates ``validated.queries[:3]`` after validation so a
    future Pydantic behavior change can never quietly raise the fan-out.
    """

    model_config = ConfigDict(extra="ignore")

    queries: list[DiagnosticQuery] = Field(default_factory=list, max_length=3)
