from __future__ import annotations

import pytest

from observibot.alerting.base import AlertChannel, AlertManager, AlertResult, RateLimitState
from observibot.core.models import Insight


class _CaptureChannel(AlertChannel):
    type = "capture"

    def __init__(self, severity_filter: list[str], succeed: bool = True) -> None:
        super().__init__(options={}, severity_filter=severity_filter)
        self.sent: list[Insight] = []
        self.succeed = succeed

    async def send(self, insight: Insight) -> AlertResult:
        self.sent.append(insight)
        return AlertResult(
            channel=self.type,
            severity=insight.severity,
            success=self.succeed,
            message="ok" if self.succeed else "fail",
        )


@pytest.mark.asyncio
async def test_dispatch_respects_severity_filter() -> None:
    critical_only = _CaptureChannel(severity_filter=["critical"])
    warning_only = _CaptureChannel(severity_filter=["warning"])
    manager = AlertManager(channels=[critical_only, warning_only])
    await manager.dispatch(
        Insight(title="t", severity="critical", summary="s")
    )
    assert len(critical_only.sent) == 1
    assert len(warning_only.sent) == 0


@pytest.mark.asyncio
async def test_rate_limit_blocks_duplicates() -> None:
    channel = _CaptureChannel(severity_filter=["warning"])
    manager = AlertManager(channels=[channel], max_alerts_per_hour=10, cooldown_seconds=600)
    insight = Insight(title="dup", severity="warning", summary="same")
    await manager.dispatch(insight)
    await manager.dispatch(insight)
    # second dispatch hits cooldown on same fingerprint
    assert len(channel.sent) == 1


@pytest.mark.asyncio
async def test_rate_limit_hourly_cap() -> None:
    channel = _CaptureChannel(severity_filter=["warning"])
    manager = AlertManager(channels=[channel], max_alerts_per_hour=2, cooldown_seconds=0)
    for i in range(5):
        await manager.dispatch(Insight(title=f"t{i}", severity="warning", summary="s"))
    assert len(channel.sent) == 2


@pytest.mark.asyncio
async def test_channel_exception_reported() -> None:
    class Boom(AlertChannel):
        type = "boom"

        async def send(self, insight: Insight) -> AlertResult:
            raise RuntimeError("kaboom")

    channel = Boom(options={}, severity_filter=["warning"])
    manager = AlertManager(channels=[channel])
    results = await manager.dispatch(Insight(title="t", severity="warning"))
    assert any(not r.success for r in results)


def test_rate_limit_state_allow() -> None:
    rl = RateLimitState(max_per_hour=2, cooldown_seconds=10)
    assert rl.allow("fp1") is True
    rl.record("fp1")
    assert rl.allow("fp1") is False  # cooldown
    assert rl.allow("fp2") is True


@pytest.mark.asyncio
async def test_aggregation_groups_burst_into_incident() -> None:
    channel = _CaptureChannel(severity_filter=["critical", "warning"])
    manager = AlertManager(
        channels=[channel],
        aggregation_window_seconds=0.05,
        aggregation_min_incidents=3,
    )
    for i in range(4):
        await manager.dispatch(
            Insight(title=f"anomaly {i}", severity="warning", summary=f"s{i}")
        )
    await manager.flush()
    assert len(channel.sent) == 1
    assert channel.sent[0].source == "incident"
    assert "4 anomalies" in channel.sent[0].title


@pytest.mark.asyncio
async def test_aggregation_sends_isolated_insights_individually() -> None:
    channel = _CaptureChannel(severity_filter=["warning"])
    manager = AlertManager(
        channels=[channel],
        aggregation_window_seconds=0.05,
        aggregation_min_incidents=3,
    )
    await manager.dispatch(
        Insight(title="alone", severity="warning", summary="s")
    )
    await manager.flush()
    assert len(channel.sent) == 1
    assert channel.sent[0].source != "incident"


@pytest.mark.asyncio
async def test_aggregation_incident_severity_is_max() -> None:
    channel = _CaptureChannel(severity_filter=["critical", "warning", "info"])
    manager = AlertManager(
        channels=[channel],
        aggregation_window_seconds=0.05,
        aggregation_min_incidents=3,
    )
    await manager.dispatch(Insight(title="a", severity="warning"))
    await manager.dispatch(Insight(title="b", severity="info"))
    await manager.dispatch(Insight(title="c", severity="critical"))
    await manager.flush()
    assert len(channel.sent) == 1
    assert channel.sent[0].severity == "critical"
