"""LLM analyzer — turns anomalies + context into Insight objects."""
from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from dataclasses import dataclass
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
from observibot.core.anomaly import Anomaly, compute_anomaly_signature
from observibot.core.evidence import EvidenceBundle
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
    """Render anomalies for the LLM, calling out *direction* explicitly.

    The LLM has been observed to narrate an increase as a "data loss" event
    when it sees only ``value``/``median`` plus an ``inf`` modified-z from a
    MAD=0 baseline. Prefixing each line with a direction word (INCREASE or
    DECREASE) and showing a signed ``delta`` removes the ambiguity without
    relying on the model to infer direction from two numbers.
    """
    rows = []
    for a in anomalies:
        labels = ",".join(f"{k}={v}" for k, v in a.labels.items())
        delta = a.value - a.median
        if a.direction == "spike":
            direction_word = "INCREASE"
        elif a.direction == "dip":
            direction_word = "DECREASE"
        else:
            direction_word = a.direction.upper()
        rows.append(
            f"- {a.severity.upper()} {direction_word} {a.metric_name} "
            f"({labels}) value={a.value:.4g} median={a.median:.4g} "
            f"delta={delta:+.4g} MAD={a.mad:.4g} "
            f"modified-z={a.modified_z:+.2f} "
            f"consecutive={a.consecutive_count}"
        )
    return "\n".join(rows) if rows else "(none)"


def summarize_evidence(bundle: EvidenceBundle | None) -> str:
    """Render an :class:`EvidenceBundle` for the LLM anomaly-analysis prompt.

    Always emits three clearly-labeled sections — recurrence,
    change-event correlations, diagnostic query results — so the LLM
    can tell what evidence is present versus absent. Absent sections
    say "(none attached)" or "(not run for this cycle)" rather than
    being dropped, which prevents the model from inventing correlations
    that don't exist.
    """
    if bundle is None:
        bundle = EvidenceBundle()

    lines: list[str] = ["## Evidence attached to this anomaly set", ""]

    lines.append("Recurrence history (last 30 days):")
    recurrence_lines: list[str] = []
    for metric, rec in sorted(bundle.recurrence.items()):
        if rec.count <= 0:
            continue
        hrs = ", ".join(str(h) for h in rec.common_hours)
        line = f"  - {metric}: seen {rec.count} times"
        if hrs:
            line += f", most common at hours [{hrs}] UTC"
        if rec.last_seen and len(rec.last_seen) >= 10:
            line += f", last seen {rec.last_seen[:10]}"
        recurrence_lines.append(line)
    if recurrence_lines:
        lines.extend(recurrence_lines)
    else:
        lines.append("  (no prior occurrences)")
    lines.append("")

    lines.append("Change-event correlations:")
    if bundle.correlations:
        for corr in bundle.correlations:
            minutes = corr.time_delta_seconds / 60.0
            lines.append(
                f"  - {corr.metric_name} anomaly {minutes:.0f} min after "
                f"{corr.change_type}: {corr.change_summary} "
                f"(severity_score={corr.severity_score:.2f})"
            )
    else:
        lines.append("  (none attached)")
    lines.append("")

    lines.append("Diagnostic query results:")
    if bundle.diagnostics:
        for diag in bundle.diagnostics:
            status = f" ERROR: {diag.error}" if diag.error else ""
            lines.append(
                f"  - hypothesis: {diag.hypothesis}\n"
                f"    rows returned: {diag.row_count}{status}"
            )
            if diag.explanation:
                lines.append(f"    explanation: {diag.explanation}")
    else:
        lines.append("  (not run for this cycle)")

    return "\n".join(lines)


def summarize_recurrence(recurrence: dict[str, dict] | None) -> str:
    """Backwards-compatible recurrence renderer.

    Step 3.3 prefers :func:`summarize_evidence`, which renders a full
    :class:`EvidenceBundle`. This shim is retained for any external
    caller still passing the legacy dict-of-dicts shape.
    """
    return summarize_evidence(EvidenceBundle.from_recurrence_map(recurrence))


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
        recurrence_context: dict[str, dict] | None = None,
        evidence: EvidenceBundle | None = None,
    ) -> list[Insight]:
        """Produce unsaved :class:`Insight` objects for the given anomalies.

        Returns unsaved Insight objects. The caller is responsible for enriching
        (recurrence_context, diagnostic_evidence, etc.) and persisting via
        store.save_insight(). This ordering is REQUIRED — save_insight has a
        fingerprint-based dedup check that prevents re-saving an enriched
        version of an already-persisted insight.

        On soft LLM failure (bad JSON, timeout) the fallback insight is
        returned unsaved — the caller enriches and persists it like any other.

        Raises:
            LLMHardError: propagated when the provider returned a hard failure,
                so the monitor's circuit breaker can switch to long-backoff mode.
                The fallback insight is still saved before re-raising (the
                caller's except block doesn't run the enrichment path).
        """
        if not anomalies:
            return []
        # Prefer an explicit EvidenceBundle when provided (Step 3.3+); fall
        # back to the legacy dict-of-dicts recurrence_context so existing
        # callers and tests continue to work unchanged.
        if evidence is None:
            evidence = EvidenceBundle.from_recurrence_map(recurrence_context)
        prompt = ANOMALY_ANALYSIS_PROMPT.format(
            anomalies=summarize_anomalies(anomalies),
            evidence=summarize_evidence(evidence),
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
            return [self._fallback_insight(anomalies, str(exc))]

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

        # All LLM-synthesized insights for this cycle share the same
        # underlying anomaly set, so they share the same signature — this is
        # exactly what makes the 1-hour dedup window collapse re-firings
        # across monitor cycles.
        signature = compute_anomaly_signature(anomalies)
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
                anomaly_signature=signature,
            )
            insight.fingerprint = insight.compute_fingerprint()
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
        business_context: str = "",
    ) -> LLMQueryResponse:
        prompt = ON_DEMAND_QUERY_PROMPT.format(
            question=question,
            system_summary=summarize_system(system_model),
            metrics_summary=summarize_metrics(recent_metrics),
            insights_summary=summarize_insights(recent_insights),
            business_context=business_context,
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
            anomaly_signature=compute_anomaly_signature(anomalies),
        )
        insight.fingerprint = insight.compute_fingerprint()
        return insight

    async def _maybe_save(self, insight: Insight) -> bool:
        if self.store is None:
            return True
        return await self.store.save_insight(insight)


CORRELATION_PROMPT = """\
You are analyzing the relationship between a recent code/deploy change and a
performance anomaly that occurred shortly after.

Change event:
  Type: {change_type}
  Time: {change_time}
  Summary: {change_summary}
  Details: {change_details}

Anomaly:
  Metric: {anomaly_metric}
  Severity: {anomaly_severity}
  Value: {anomaly_value} (baseline median: {anomaly_median})
  Direction: {anomaly_direction}
  Time proximity: {time_delta_minutes:.0f} minutes after the change

System context:
{system_summary}

Analyze whether this change is likely related to this anomaly. Consider:
1. Could this type of change affect the observed metric?
2. Is the timing consistent with a causal relationship?
3. What mechanism could link them?

Respond with JSON:
{{
  "likely_related": true|false,
  "confidence": 0.0-1.0,
  "mechanism": "explanation of potential causal link",
  "recommendation": "what to investigate or do"
}}
"""


@dataclass
class ChangePerformanceCorrelation:
    """A potential correlation between a change event and a metric anomaly."""
    change_event: ChangeEvent
    anomaly: Any
    time_delta_seconds: float
    severity_score: float

    @property
    def time_delta_minutes(self) -> float:
        return self.time_delta_seconds / 60.0


class CorrelationDetector:
    """Cheap deterministic correlation detection with optional LLM escalation."""

    def __init__(
        self,
        provider: LLMProvider | None = None,
        store: Store | None = None,
        proximity_window_minutes: float = 30.0,
        escalation_threshold: float = 5.0,
    ) -> None:
        self.provider = provider
        self.store = store
        self.proximity_window_minutes = proximity_window_minutes
        self.escalation_threshold = escalation_threshold

    def detect_correlations(
        self,
        anomalies: list[Anomaly],
        recent_changes: list[ChangeEvent],
    ) -> list[ChangePerformanceCorrelation]:
        """Cheap deterministic pass: find anomalies near recent changes."""
        correlations: list[ChangePerformanceCorrelation] = []

        for anomaly in anomalies:
            for change in recent_changes:
                delta = (anomaly.detected_at - change.occurred_at).total_seconds()
                if 0 < delta <= self.proximity_window_minutes * 60:
                    severity_score = self._compute_severity_score(anomaly, delta)
                    correlations.append(ChangePerformanceCorrelation(
                        change_event=change,
                        anomaly=anomaly,
                        time_delta_seconds=delta,
                        severity_score=severity_score,
                    ))

        correlations.sort(key=lambda c: -c.severity_score)
        return correlations

    def _compute_severity_score(self, anomaly: Anomaly, delta_seconds: float) -> float:
        """Score: higher = more likely meaningful correlation."""
        severity_weight = {"critical": 3.0, "warning": 1.5}.get(anomaly.severity, 1.0)
        proximity_weight = max(0.1, 1.0 - (delta_seconds / (self.proximity_window_minutes * 60)))
        z_weight = min(abs(anomaly.modified_z) / 5.0, 2.0)
        return severity_weight * proximity_weight * z_weight

    async def analyze_correlation(
        self,
        correlation: ChangePerformanceCorrelation,
        system_model: SystemModel | None = None,
    ) -> Insight | None:
        """LLM escalation: only for high-confidence correlations."""
        if correlation.severity_score < self.escalation_threshold:
            return self._deterministic_insight(correlation)

        if self.provider is None:
            return self._deterministic_insight(correlation)

        anomaly = correlation.anomaly
        change = correlation.change_event

        prompt = CORRELATION_PROMPT.format(
            change_type=change.event_type,
            change_time=change.occurred_at.isoformat(),
            change_summary=change.summary,
            change_details=json.dumps(change.details, default=str)[:500],
            anomaly_metric=anomaly.metric_name,
            anomaly_severity=anomaly.severity,
            anomaly_value=f"{anomaly.value:.4g}",
            anomaly_median=f"{anomaly.median:.4g}",
            anomaly_direction=anomaly.direction,
            time_delta_minutes=correlation.time_delta_minutes,
            system_summary=summarize_system(system_model),
        )

        try:
            response = await self.provider.analyze(
                system_prompt="You analyze code-to-performance relationships. Output JSON.",
                user_prompt=prompt,
            )
            data = response.data
            if not data.get("likely_related", False):
                return None

            return Insight(
                title=(
                    f"Potential correlation: {anomaly.metric_name} anomaly "
                    f"after {change.event_type}"
                ),
                severity="warning",
                summary=data.get("mechanism", ""),
                details=data.get("recommendation", ""),
                related_metrics=[anomaly.metric_name],
                confidence=float(data.get("confidence", 0.5)),
                source="code_correlation",
            )
        except (LLMError, Exception) as exc:
            log.debug("LLM correlation analysis failed: %s", exc)
            return self._deterministic_insight(correlation)

    def _deterministic_insight(
        self, correlation: ChangePerformanceCorrelation,
    ) -> Insight:
        anomaly = correlation.anomaly
        change = correlation.change_event
        return Insight(
            title=(
                f"Metric shift in {anomaly.metric_name} "
                f"~{correlation.time_delta_minutes:.0f}min after {change.event_type}"
            ),
            severity="info",
            summary=(
                f"{anomaly.metric_name} ({anomaly.severity}) detected "
                f"{correlation.time_delta_minutes:.0f} minutes after "
                f"'{change.summary}'. Temporal proximity suggests possible correlation."
            ),
            details=f"Change: {change.summary}\nDelta: {correlation.time_delta_minutes:.0f}min",
            related_metrics=[anomaly.metric_name],
            confidence=0.3,
            source="code_correlation",
        )


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
