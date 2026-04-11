"""Alerting base classes — channel interface, manager, rate limiting, and
incident aggregation.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from abc import ABC, abstractmethod
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from observibot.core.models import Insight

log = logging.getLogger(__name__)


@dataclass
class AlertResult:
    """Outcome of attempting to send a single alert."""

    channel: str
    severity: str
    success: bool
    message: str = ""


@dataclass
class RateLimitState:
    max_per_hour: int = 10
    cooldown_seconds: int = 300
    sent_times: deque[float] = field(default_factory=deque)
    last_sent_per_fp: dict[str, float] = field(default_factory=dict)

    def allow(self, fingerprint: str) -> bool:
        now = time.time()
        cutoff = now - 3600
        while self.sent_times and self.sent_times[0] < cutoff:
            self.sent_times.popleft()
        if len(self.sent_times) >= self.max_per_hour:
            return False
        last = self.last_sent_per_fp.get(fingerprint)
        return not (last is not None and now - last < self.cooldown_seconds)

    def record(self, fingerprint: str) -> None:
        now = time.time()
        self.sent_times.append(now)
        self.last_sent_per_fp[fingerprint] = now


class AlertChannel(ABC):
    """Base alert channel."""

    type: str = "base"

    def __init__(self, options: dict[str, Any], severity_filter: list[str]) -> None:
        self.options = options
        self.severity_filter = [s.lower() for s in severity_filter]

    def accepts(self, severity: str) -> bool:
        return severity.lower() in self.severity_filter

    @abstractmethod
    async def send(self, insight: Insight) -> AlertResult:
        """Deliver the insight to the underlying channel."""

    async def close(self) -> None:
        """Release any resources held by this channel."""
        return None


def _build_incident_insight(
    insights: list[Insight], connector_hint: str | None = None
) -> Insight:
    """Collapse a list of related insights into one 'incident' insight."""
    severities = [i.severity.lower() for i in insights]
    if "critical" in severities:
        severity = "critical"
    elif "warning" in severities:
        severity = "warning"
    else:
        severity = "info"
    first_tables = (i.related_tables[0] if i.related_tables else "" for i in insights)
    table_set = sorted({t for t in first_tables if t})
    scope = connector_hint or ", ".join(table_set) or "monitored systems"
    emoji = {"critical": "🔴", "warning": "🟡", "info": "🔵"}.get(severity, "⚪")
    title = f"{emoji} Incident: {len(insights)} anomalies detected across {scope}"
    bullet_summary = "\n".join(f"• [{i.severity}] {i.title}" for i in insights)
    actions_set: list[str] = []
    for i in insights:
        for action in i.recommended_actions:
            if action not in actions_set:
                actions_set.append(action)
    metrics_set: list[str] = []
    for i in insights:
        for m in i.related_metrics:
            if m not in metrics_set:
                metrics_set.append(m)
    tables_set: list[str] = []
    for i in insights:
        for t in i.related_tables:
            if t not in tables_set:
                tables_set.append(t)
    confidence = (
        sum(i.confidence for i in insights) / len(insights) if insights else 0.5
    )
    incident = Insight(
        title=title,
        severity=severity,
        summary=(
            f"{len(insights)} related anomalies fired in the same collection cycle. "
            "Aggregated into one incident to reduce alert noise."
        ),
        details=bullet_summary,
        recommended_actions=actions_set,
        related_metrics=metrics_set,
        related_tables=tables_set,
        confidence=confidence,
        source="incident",
    )
    incident.fingerprint = incident.compute_fingerprint()
    return incident


class AlertManager:
    """Routes insights to channels with global rate limiting and aggregation.

    Insights are buffered for ``aggregation_window_seconds`` before being
    dispatched. If three or more accumulate within the window, they are
    merged into a single "incident" alert. Single isolated insights still
    send individually after the window expires.

    Tests and CLI commands that need synchronous behavior can pass
    ``aggregation_window_seconds=0`` — in that case every ``dispatch`` call
    flushes immediately and the manager behaves like the pre-aggregation
    version.
    """

    def __init__(
        self,
        channels: list[AlertChannel],
        max_alerts_per_hour: int = 10,
        cooldown_seconds: int = 300,
        aggregation_window_seconds: float = 0.0,
        aggregation_min_incidents: int = 3,
    ) -> None:
        self.channels = channels
        self.rate_limit = RateLimitState(
            max_per_hour=max_alerts_per_hour, cooldown_seconds=cooldown_seconds
        )
        self.aggregation_window_seconds = float(aggregation_window_seconds)
        self.aggregation_min_incidents = int(aggregation_min_incidents)
        self._buffer: list[Insight] = []
        self._buffer_lock = asyncio.Lock()
        self._flush_task: asyncio.Task | None = None

    async def dispatch(self, insight: Insight) -> list[AlertResult]:
        """Send an insight, possibly buffering it for incident aggregation.

        If aggregation is disabled (window == 0) we dispatch immediately.
        Otherwise we push into the buffer and schedule a flush; the flush
        groups everything that arrived during the window and decides whether
        to send one incident or individual alerts.
        """
        if self.aggregation_window_seconds <= 0:
            return await self._send_one(insight)

        async with self._buffer_lock:
            self._buffer.append(insight)
            if self._flush_task is None or self._flush_task.done():
                self._flush_task = asyncio.create_task(self._flush_after_window())
        return []

    async def _flush_after_window(self) -> None:
        try:
            await asyncio.sleep(self.aggregation_window_seconds)
        except asyncio.CancelledError:  # pragma: no cover
            return
        await self.flush()

    async def flush(self) -> list[AlertResult]:
        """Drain the buffer now and dispatch the result.

        Called automatically after the aggregation window. Can also be
        invoked directly by tests or shutdown logic to force immediate
        delivery.
        """
        async with self._buffer_lock:
            pending = list(self._buffer)
            self._buffer.clear()
        if not pending:
            return []
        results: list[AlertResult] = []
        if len(pending) >= self.aggregation_min_incidents:
            incident = _build_incident_insight(pending)
            results.extend(await self._send_one(incident))
        else:
            for insight in pending:
                results.extend(await self._send_one(insight))
        return results

    async def _send_one(self, insight: Insight) -> list[AlertResult]:
        """Route a single insight to all matching channels with rate limiting."""
        if not self.channels:
            return []
        if not self.rate_limit.allow(insight.fingerprint):
            log.info("Rate limited: %s (%s)", insight.title, insight.severity)
            return [
                AlertResult(
                    channel="manager",
                    severity=insight.severity,
                    success=False,
                    message="rate limited",
                )
            ]
        results: list[AlertResult] = []
        sent_anywhere = False
        for channel in self.channels:
            if not channel.accepts(insight.severity):
                continue
            try:
                result = await channel.send(insight)
            except Exception as exc:
                log.warning("Channel %s failed: %s", channel.type, exc)
                result = AlertResult(
                    channel=channel.type,
                    severity=insight.severity,
                    success=False,
                    message=f"{type(exc).__name__}: {exc}",
                )
            results.append(result)
            if result.success:
                sent_anywhere = True
        if sent_anywhere:
            self.rate_limit.record(insight.fingerprint)
        return results

    async def close(self) -> None:
        if self._flush_task is not None and not self._flush_task.done():
            self._flush_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._flush_task
        # Drain anything still buffered before shutting down.
        await self.flush()
        await asyncio.gather(
            *(c.close() for c in self.channels), return_exceptions=True
        )


def utcnow() -> datetime:
    return datetime.now(UTC)
