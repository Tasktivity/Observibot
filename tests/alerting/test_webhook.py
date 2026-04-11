from __future__ import annotations

import httpx
import pytest

from observibot.alerting.webhook import WebhookAlertChannel, build_channels
from observibot.core.config import AlertChannelConfig
from observibot.core.models import Insight


@pytest.mark.asyncio
async def test_webhook_send_success(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}

    class FakeResp:
        status_code = 200
        text = "ok"

    class FakeClient:
        def __init__(self, *a, **kw): ...
        async def request(self, method, url, json, headers):
            captured["method"] = method
            captured["url"] = url
            captured["json"] = json
            return FakeResp()
        async def aclose(self): ...

    monkeypatch.setattr("httpx.AsyncClient", FakeClient)
    channel = WebhookAlertChannel(
        options={"url": "https://example.com/hook", "headers": {"X-A": "1"}},
        severity_filter=["warning"],
    )
    result = await channel.send(Insight(title="t", severity="warning", summary="s"))
    assert result.success
    assert captured["url"] == "https://example.com/hook"


@pytest.mark.asyncio
async def test_webhook_send_missing_url() -> None:
    channel = WebhookAlertChannel(options={}, severity_filter=["warning"])
    result = await channel.send(Insight(title="t", severity="warning"))
    assert not result.success
    assert "missing url" in result.message.lower()


@pytest.mark.asyncio
async def test_webhook_send_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    class FailClient:
        def __init__(self, *a, **kw): ...
        async def request(self, *a, **kw):
            raise httpx.ConnectError("no network", request=None)
        async def aclose(self): ...

    monkeypatch.setattr("httpx.AsyncClient", FailClient)
    channel = WebhookAlertChannel(
        options={"url": "http://fake"}, severity_filter=["warning"]
    )
    result = await channel.send(Insight(title="t", severity="warning"))
    assert not result.success


def test_build_channels_supports_webhook_and_slack() -> None:
    cfgs = [
        AlertChannelConfig(type="slack", options={"webhook_url": "x"}, severity_filter=["warning"]),
        AlertChannelConfig(type="webhook", options={"url": "y"}, severity_filter=["critical"]),
        AlertChannelConfig(type="unknown", options={}, severity_filter=[]),
    ]
    channels = build_channels(cfgs)
    types = [c.type for c in channels]
    assert "slack" in types
    assert "webhook" in types
    assert "unknown" not in types
