"""LLM analyzer — turns anomalies + context into Insight objects."""
from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError

from observibot.agent.llm_provider import (
    LLMError,
    LLMHardError,
    LLMProvider,
    LLMSoftError,
    estimate_cost_usd,
)
from observibot.agent.prompts import (
    ANOMALY_ANALYSIS_PROMPT,
    ON_DEMAND_QUERY_PROMPT,
    SYSTEM_ANALYSIS_PROMPT,
    TEXT_TO_SQL_PROMPT,
)
from observibot.agent.schemas import (
    LLMAnalysisResponse,
    LLMQueryResponse,
    LLMSystemAnalysis,
)
from observibot.core.anomaly import Anomaly
from observibot.core.models import ChangeEvent, Insight, MetricSnapshot, SystemModel
from observibot.core.store import Store

log = logging.getLogger(__name__)


def summarize_system(model: SystemModel | None) -> str:
    """Build a compact text summary of a SystemModel for prompt context."""
    if model is None:
        return "(no system model available)"
    parts = [
        f"Tables ({len(model.tables)}):",
    ]
    for tbl in sorted(model.tables, key=lambda t: t.fqn)[:40]:
        cols = ", ".join(c["name"] for c in tbl.columns[:8])
        if len(tbl.columns) > 8:
            cols += ", ..."
        rc = f" rows={tbl.row_count}" if tbl.row_count is not None else ""
        parts.append(f"  - {tbl.fqn}{rc}: {cols}")
    if model.relationships:
        parts.append(f"Relationships ({len(model.relationships)}):")
        for rel in model.relationships[:30]:
            parts.append(
                f"  - {rel.from_table}.{rel.from_column} -> "
                f"{rel.to_table}.{rel.to_column}"
            )
    if model.services:
        parts.append(f"Services ({len(model.services)}):")
        for svc in model.services:
            env = f" [{svc.environment}]" if svc.environment else ""
            status = f" status={svc.status}" if svc.status else ""
            parts.append(f"  - {svc.name}{env}{status}")
    return "\n".join(parts)


def summarize_anomalies(anomalies: Iterable[Anomaly]) -> str:
    rows = []
    for a in anomalies:
        labels = ",".join(f"{k}={v}" for k, v in a.labels.items())
        rows.append(
            f"- {a.severity.upper()} {a.metric_name} "
            f"({labels}) value={a.value:.4g} median={a.median:.4g} "
            f"MAD={a.mad:.4g} modified-z={a.modified_z:.2f} "
            f"consecutive={a.consecutive_count} dir={a.direction}"
        )
    return "\n".join(rows) if rows else "(none)"


def summarize_changes(events: Iterable[ChangeEvent]) -> str:
    rows = [
        f"- {e.occurred_at.isoformat()} {e.connector_name} {e.event_type}: {e.summary}"
        for e in events
    ]
    return "\n".join(rows) if rows else "(none)"


def summarize_metrics(metrics: Iterable[MetricSnapshot]) -> str:
    rows = []
    for m in list(metrics)[-30:]:
        labels = ",".join(f"{k}={v}" for k, v in m.labels.items())
        rows.append(f"- {m.metric_name}({labels}) = {m.value}")
    return "\n".join(rows) if rows else "(none)"


def summarize_insights(insights: Iterable[Insight]) -> str:
    return "\n".join(f"- [{i.severity}] {i.title}: {i.summary}" for i in insights) or "(none)"


class Analyzer:
    """LLM-powered analyzer that produces and stores :class:`Insight` objects."""

    def __init__(self, provider: LLMProvider, store: Store | None = None) -> None:
        self.provider = provider
        self.store = store

    async def analyze_anomalies(
        self,
        anomalies: list[Anomaly],
        system_model: SystemModel | None,
        recent_changes: list[ChangeEvent] | None = None,
        business_context: dict[str, Any] | None = None,
    ) -> list[Insight]:
        """Produce :class:`Insight` objects for the given anomalies.

        On any LLM failure — hard (auth/quota) or soft (bad JSON, validation,
        timeout) — fall back to a deterministic raw-alert Insight so the
        underlying signal is never silently dropped.

        Raises:
            LLMHardError: propagated when the provider returned a hard failure,
                so the monitor's circuit breaker can switch to long-backoff mode.
                The fallback insight is still saved before re-raising.
        """
        if not anomalies:
            return []
        prompt = ANOMALY_ANALYSIS_PROMPT.format(
            anomalies=summarize_anomalies(anomalies),
            changes=summarize_changes(recent_changes or []),
            business_context=json.dumps(business_context or {}, indent=2),
            system_summary=summarize_system(system_model),
        )
        try:
            response = await self.provider.analyze(
                system_prompt="You are Observibot. Output only JSON.",
                user_prompt=prompt,
            )
        except LLMHardError as exc:
            log.warning("LLM hard failure during anomaly analysis: %s", exc)
            await self._record_failure("anomaly_analysis", "hard", str(exc))
            fallback = [self._fallback_insight(anomalies, str(exc))]
            await self._persist(fallback)
            raise
        except LLMError as exc:
            log.warning("LLM soft failure during anomaly analysis: %s", exc)
            await self._record_failure("anomaly_analysis", "soft", str(exc))
            fallback = [self._fallback_insight(anomalies, str(exc))]
            await self._persist(fallback)
            return fallback

        await self._record_usage(response, purpose="anomaly_analysis")

        try:
            validated = LLMAnalysisResponse.model_validate(response.data)
        except ValidationError as exc:
            log.warning("LLM response failed schema validation: %s", exc)
            await self._record_failure(
                "anomaly_analysis", "soft", f"validation error: {exc}"
            )
            fallback = [self._fallback_insight(anomalies, f"invalid schema: {exc}")]
            await self._persist(fallback)
            # Soft failure — propagate so the monitor's circuit breaker can
            # count it toward the retry threshold.
            raise LLMSoftError(f"LLM response failed validation: {exc}") from exc

        results: list[Insight] = []
        for raw in validated.insights:
            insight = Insight(
                title=raw.title,
                severity=raw.severity,
                summary=raw.merged_description() or raw.title,
                details=raw.description,
                related_metrics=list(raw.related_metrics),
                related_tables=list(raw.related_tables),
                recommended_actions=list(raw.recommended_actions) + (
                    [raw.suggested_action] if raw.suggested_action else []
                ),
                confidence=raw.confidence,
                uncertainty_reason=raw.uncertainty_reason,
                source="llm",
            )
            insight.fingerprint = insight.compute_fingerprint()
            if self.store is not None:
                stored = await self.store.save_insight(insight)
                if not stored:
                    continue
            results.append(insight)
        return results

    async def analyze_system(self, system_model: SystemModel) -> LLMSystemAnalysis:
        """Produce a :class:`LLMSystemAnalysis` for the given model."""
        prompt = SYSTEM_ANALYSIS_PROMPT.format(
            system_summary=summarize_system(system_model)
        )
        response = await self.provider.analyze(
            system_prompt="You are Observibot. Output only JSON.",
            user_prompt=prompt,
        )
        await self._record_usage(response, purpose="system_analysis")
        try:
            return LLMSystemAnalysis.model_validate(response.data)
        except ValidationError as exc:
            log.warning("System-analysis validation failed: %s", exc)
            await self._record_failure(
                "system_analysis", "soft", f"validation error: {exc}"
            )
            raise LLMSoftError(f"System analysis validation failed: {exc}") from exc

    async def answer_question(
        self,
        question: str,
        system_model: SystemModel | None,
        recent_metrics: list[MetricSnapshot],
        recent_insights: list[Insight],
    ) -> LLMQueryResponse:
        prompt = ON_DEMAND_QUERY_PROMPT.format(
            question=question,
            system_summary=summarize_system(system_model),
            metrics_summary=summarize_metrics(recent_metrics),
            insights_summary=summarize_insights(recent_insights),
        )
        response = await self.provider.analyze(
            system_prompt="You are Observibot. Output only JSON.",
            user_prompt=prompt,
        )
        await self._record_usage(response, purpose="ad_hoc_query")
        try:
            return LLMQueryResponse.model_validate(response.data)
        except ValidationError as exc:
            log.warning("Query validation failed: %s", exc)
            await self._record_failure(
                "ad_hoc_query", "soft", f"validation error: {exc}"
            )
            raise LLMSoftError(f"Query validation failed: {exc}") from exc

    async def generate_sql(
        self,
        question: str,
        table_allowlist: set[str],
    ) -> tuple[str, dict | None]:
        """Generate SQL + widget hints from a natural language question.

        Returns (sql_query, widget_hints_dict_or_None).
        """
        schema_desc = _describe_store_schema(table_allowlist)
        prompt = TEXT_TO_SQL_PROMPT.format(
            schema_description=schema_desc,
            question=question,
        )
        response = await self.provider.analyze(
            system_prompt="You are Observibot. Output only JSON.",
            user_prompt=prompt,
        )
        await self._record_usage(response, purpose="text_to_sql")
        sql = response.data.get("sql", "")
        if not sql:
            raise LLMSoftError("LLM returned empty SQL")
        widget_hints = {
            "widget_type": response.data.get("widget_type", "table"),
            "title": response.data.get("title", question[:50]),
            "encoding": response.data.get("encoding", {}),
        }
        return sql, widget_hints

    async def _persist(self, insights: list[Insight]) -> None:
        if self.store is None:
            return
        for insight in insights:
            await self.store.save_insight(insight)

    async def _record_failure(self, purpose: str, kind: str, message: str) -> None:
        """Log a failed LLM interaction to the usage table for transparency."""
        if self.store is None:
            return
        await self.store.record_llm_usage(
            provider=self.provider.name,
            model=self.provider.model,
            prompt_tokens=0,
            completion_tokens=0,
            cost_usd=0.0,
            purpose=f"{purpose}:{kind}_failure:{message[:180]}",
        )

    async def _record_usage(self, response: Any, purpose: str) -> None:
        if self.store is None:
            return
        cost = estimate_cost_usd(
            self.provider.name,
            self.provider.model,
            response.prompt_tokens,
            response.completion_tokens,
        )
        await self.store.record_llm_usage(
            provider=self.provider.name,
            model=self.provider.model,
            prompt_tokens=response.prompt_tokens,
            completion_tokens=response.completion_tokens,
            cost_usd=cost,
            purpose=purpose,
        )

    def _fallback_insight(self, anomalies: list[Anomaly], reason: str) -> Insight:
        """Deterministic raw-alert Insight used when the LLM is unavailable.

        This is the "never silently drop an anomaly" safety net. We pick the
        worst-by-consecutive-count anomaly as the primary signal and attach
        the full anomaly list in ``details``.
        """
        def worst_key(a: Anomaly) -> float:
            return (
                a.consecutive_count * 1e6
                + (abs(a.modified_z) if a.modified_z != float("inf") else 1e9)
            )

        worst = max(anomalies, key=worst_key)
        insight = Insight(
            title=f"Anomaly in {worst.metric_name} (LLM unavailable)",
            severity=worst.severity if worst.is_alertable else "warning",
            summary=(
                f"{worst.metric_name} ({worst.labels}) value={worst.value:.4g} "
                f"deviates from baseline median {worst.median:.4g} "
                f"(MAD={worst.mad:.4g}, modified-z={worst.modified_z:.2f}, "
                f"consecutive={worst.consecutive_count}). "
                f"LLM analysis was skipped: {reason}"
            ),
            details=summarize_anomalies(anomalies),
            related_metrics=[a.metric_name for a in anomalies],
            related_tables=[
                a.labels.get("table") for a in anomalies if a.labels.get("table")
            ],
            recommended_actions=[
                "Investigate recent deploys and metric history",
                "Re-enable LLM analysis once provider is reachable",
            ],
            confidence=0.4,
            uncertainty_reason=(
                "LLM unavailable — fallback insight generated from raw anomaly data."
            ),
            source="anomaly",
        )
        insight.fingerprint = insight.compute_fingerprint()
        return insight

    async def _maybe_save(self, insight: Insight) -> bool:
        if self.store is None:
            return True
        return await self.store.save_insight(insight)


def _describe_store_schema(allowed_tables: set[str]) -> str:
    """Build a compact schema description of Observibot's store tables."""
    from observibot.core.store import metadata as store_metadata

    lines = []
    for name in sorted(allowed_tables):
        table = store_metadata.tables.get(name)
        if table is None:
            continue
        cols = ", ".join(
            f"{c.name} ({c.type})" for c in table.columns
        )
        lines.append(f"- {name}: {cols}")
    return "\n".join(lines) if lines else "(no tables)"


def utcnow() -> datetime:
    return datetime.now(UTC)
