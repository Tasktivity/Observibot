"""ntfy.sh push notification alert channel."""
from __future__ import annotations

import logging
from typing import Any

import httpx

from observibot.alerting.base import AlertChannel, AlertResult
from observibot.core.models import Insight

log = logging.getLogger(__name__)

# Map severity to ntfy priority (1-5)
SEVERITY_PRIORITY = {
    "critical": "5",  # max/urgent
    "warning": "4",   # high
    "info": "3",      # default
    "discovery": "2", # low
    "ok": "1",        # min
}

SEVERITY_EMOJI = {
    "critical": "rotating_light",
    "warning": "warning",
    "info": "information_source",
    "discovery": "mag",
    "ok": "white_check_mark",
}


class NtfyAlertChannel(AlertChannel):
    """Sends push notifications via ntfy.sh (or self-hosted ntfy)."""

    type = "ntfy"

    def __init__(self, options: dict[str, Any],
                 severity_filter: list[str]) -> None:
        super().__init__(options=options,
                         severity_filter=severity_filter)
        self.topic_url: str | None = options.get("url")
        self._client: httpx.AsyncClient | None = None

    def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(10.0))
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            try:
                await self._client.aclose()
            finally:
                self._client = None

    def _format_message(self, insight: Insight) -> str:
        """Format an Insight into a human-readable plain-text
        message for ntfy."""
        lines = []
        if insight.summary:
            lines.append(insight.summary)
        if insight.details:
            lines.append("")
            lines.append(insight.details)
        if insight.recommended_actions:
            lines.append("")
            lines.append("Suggested actions:")
            for action in insight.recommended_actions:
                lines.append(f"  • {action}")
        if hasattr(insight, "uncertainty_reason") and \
                insight.uncertainty_reason:
            lines.append("")
            lines.append(
                f"⚠️ Uncertainty: {insight.uncertainty_reason}")
        return "\n".join(lines) or insight.title

    async def send(self, insight: Insight) -> AlertResult:
        if not self.topic_url:
            return AlertResult(
                channel=self.type,
                severity=insight.severity,
                success=False,
                message="missing ntfy url/topic",
            )

        client = self._ensure_client()
        title = getattr(insight, "display_title",
                        lambda: insight.title)()
        body = self._format_message(insight)
        priority = SEVERITY_PRIORITY.get(
            insight.severity, "3")
        tag = SEVERITY_EMOJI.get(
            insight.severity, "bell")

        headers = {
            "Title": title[:256],
            "Priority": priority,
            "Tags": tag,
        }

        try:
            resp = await client.post(
                self.topic_url,
                content=body.encode("utf-8"),
                headers=headers,
            )
            if resp.status_code >= 400:
                return AlertResult(
                    channel=self.type,
                    severity=insight.severity,
                    success=False,
                    message=f"HTTP {resp.status_code}",
                )
        except httpx.HTTPError as exc:
            return AlertResult(
                channel=self.type,
                severity=insight.severity,
                success=False,
                message=f"{type(exc).__name__}: {exc}",
            )
        return AlertResult(
            channel=self.type,
            severity=insight.severity,
            success=True,
            message="ok",
        )
