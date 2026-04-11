"""Slack alert channel using a webhook URL with Block Kit messages."""
from __future__ import annotations

import logging
from typing import Any

import httpx

from observibot.alerting.base import AlertChannel, AlertResult
from observibot.core.models import Insight

log = logging.getLogger(__name__)

SEVERITY_EMOJI = {
    "critical": "🔴",
    "warning": "🟡",
    "info": "🔵",
    "ok": "🟢",
}


class SlackAlertChannel(AlertChannel):
    """Posts insights to a Slack incoming webhook."""

    type = "slack"

    def __init__(self, options: dict[str, Any], severity_filter: list[str]) -> None:
        super().__init__(options=options, severity_filter=severity_filter)
        self.webhook_url: str | None = options.get("webhook_url")
        self.username: str = options.get("username", "Observibot")
        self._client: httpx.AsyncClient | None = None

    def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(10.0))
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            try:
                await self._client.aclose()
            finally:
                self._client = None

    def _build_payload(self, insight: Insight) -> dict[str, Any]:
        emoji = SEVERITY_EMOJI.get(insight.severity.lower(), "⚪")
        header = f"{emoji} {insight.severity.upper()}: {insight.title}"
        actions = "\n".join(f"• {a}" for a in insight.recommended_actions)
        related = ", ".join(insight.related_metrics + insight.related_tables) or "(none)"
        return {
            "username": self.username,
            "blocks": [
                {
                    "type": "header",
                    "text": {"type": "plain_text", "text": header[:150]},
                },
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": insight.summary or "(no summary)"},
                },
                {
                    "type": "context",
                    "elements": [
                        {"type": "mrkdwn", "text": f"*Related:* {related}"},
                        {"type": "mrkdwn", "text": f"*Confidence:* {insight.confidence:.2f}"},
                    ],
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*Recommended actions:*\n{actions or '_none_'}",
                    },
                },
            ],
            "text": header,
        }

    async def send(self, insight: Insight) -> AlertResult:
        if not self.webhook_url:
            return AlertResult(
                channel=self.type,
                severity=insight.severity,
                success=False,
                message="missing webhook_url",
            )
        client = self._ensure_client()
        payload = self._build_payload(insight)
        try:
            resp = await client.post(self.webhook_url, json=payload)
            if resp.status_code >= 400:
                return AlertResult(
                    channel=self.type,
                    severity=insight.severity,
                    success=False,
                    message=f"HTTP {resp.status_code}: {resp.text[:200]}",
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
