"""Generic JSON webhook alert channel."""
from __future__ import annotations

import logging
from typing import Any

import httpx

from observibot.alerting.base import AlertChannel, AlertResult
from observibot.core.models import Insight

log = logging.getLogger(__name__)


class WebhookAlertChannel(AlertChannel):
    """POSTs insights as JSON to an arbitrary URL."""

    type = "webhook"

    def __init__(self, options: dict[str, Any], severity_filter: list[str]) -> None:
        super().__init__(options=options, severity_filter=severity_filter)
        self.url: str | None = options.get("url")
        self.headers: dict[str, str] = options.get("headers") or {}
        self.method: str = (options.get("method") or "POST").upper()
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

    async def send(self, insight: Insight) -> AlertResult:
        if not self.url:
            return AlertResult(
                channel=self.type,
                severity=insight.severity,
                success=False,
                message="missing url",
            )
        client = self._ensure_client()
        payload = insight.to_dict()
        try:
            resp = await client.request(
                self.method, self.url, json=payload, headers=self.headers
            )
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


def build_channels(channel_configs: list[Any]) -> list[AlertChannel]:
    """Build alert channels from config dicts.

    Args:
        channel_configs: list of :class:`AlertChannelConfig`.
    """
    from observibot.alerting.ntfy import NtfyAlertChannel
    from observibot.alerting.slack import SlackAlertChannel

    channels: list[AlertChannel] = []
    for cfg in channel_configs:
        ctype = cfg.type.lower()
        if ctype == "slack":
            channels.append(
                SlackAlertChannel(options=cfg.options, severity_filter=cfg.severity_filter)
            )
        elif ctype == "ntfy":
            channels.append(
                NtfyAlertChannel(options=cfg.options, severity_filter=cfg.severity_filter)
            )
        elif ctype == "webhook":
            channels.append(
                WebhookAlertChannel(options=cfg.options, severity_filter=cfg.severity_filter)
            )
        else:
            log.warning("Unknown alert channel type: %s", ctype)
    return channels
