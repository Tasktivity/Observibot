from __future__ import annotations

import pytest

from observibot.alerting.slack import SlackAlertChannel
from observibot.core.models import Insight

pytestmark = pytest.mark.asyncio


async def test_slack_missing_url() -> None:
    channel = SlackAlertChannel(options={}, severity_filter=["warning"])
    result = await channel.send(Insight(title="t", severity="warning"))
    assert not result.success


async def test_slack_payload_shape() -> None:
    channel = SlackAlertChannel(
        options={"webhook_url": "https://example.com/hook"},
        severity_filter=["critical"],
    )
    payload = channel._build_payload(
        Insight(
            title="DB spike",
            severity="critical",
            summary="Row count exploded",
            recommended_actions=["check deploys"],
            related_tables=["public.tasks"],
            related_metrics=["table_inserts"],
        )
    )
    assert "blocks" in payload
    assert any("DB spike" in block.get("text", {}).get("text", "") for block in payload["blocks"])


async def test_slack_sends(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}

    class FakeResp:
        status_code = 200
        text = "ok"

    class FakeClient:
        def __init__(self, *a, **kw): ...
        async def post(self, url, json):
            captured["url"] = url
            captured["payload"] = json
            return FakeResp()
        async def aclose(self): ...

    monkeypatch.setattr("httpx.AsyncClient", FakeClient)
    channel = SlackAlertChannel(
        options={"webhook_url": "https://example.com/hook"},
        severity_filter=["warning"],
    )
    result = await channel.send(Insight(title="t", severity="warning", summary="s"))
    assert result.success
    assert captured["url"] == "https://example.com/hook"
