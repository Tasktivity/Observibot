"""Tests for the events envelope store methods."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from observibot.core.store import Store

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def store(tmp_path: Path):
    path = tmp_path / "events_test.db"
    async with Store(path) as s:
        yield s


async def test_emit_event_returns_id(store: Store) -> None:
    eid = await store.emit_event(
        event_type="anomaly",
        source="monitor_loop",
        subject="db_conn_pool_util",
        ref_table="metric_snapshots",
        ref_id="snap001",
        severity="warning",
        summary="db_conn_pool_util exceeded threshold: 92.3",
    )
    assert isinstance(eid, str)
    assert len(eid) == 12


async def test_emit_event_with_run_id(store: Store) -> None:
    eid = await store.emit_event(
        event_type="metric_collection",
        source="monitor_loop",
        subject="collection_cycle",
        ref_table="monitor_runs",
        ref_id="run001",
        run_id="run001",
        summary="Collected 7 metrics from 1 connector",
    )
    events = await store.get_events()
    assert len(events) == 1
    assert events[0]["run_id"] == "run001"
    assert events[0]["id"] == eid


async def test_get_events_newest_first(store: Store) -> None:
    for i in range(3):
        await store.emit_event(
            event_type="anomaly",
            source="monitor_loop",
            subject=f"metric_{i}",
            ref_table="metric_snapshots",
            ref_id=f"snap{i:03d}",
        )
    events = await store.get_events()
    assert len(events) == 3
    # newest first — occurred_at should be descending
    assert events[0]["occurred_at"] >= events[1]["occurred_at"]
    assert events[1]["occurred_at"] >= events[2]["occurred_at"]


async def test_get_events_filter_by_type(store: Store) -> None:
    await store.emit_event(
        event_type="anomaly", source="monitor_loop",
        subject="cpu", ref_table="metric_snapshots", ref_id="s1",
    )
    await store.emit_event(
        event_type="insight", source="monitor_loop",
        subject="cpu", ref_table="insights", ref_id="i1",
    )
    anomalies = await store.get_events(event_type="anomaly")
    assert len(anomalies) == 1
    assert anomalies[0]["event_type"] == "anomaly"


async def test_get_events_filter_by_subject(store: Store) -> None:
    await store.emit_event(
        event_type="anomaly", source="monitor_loop",
        subject="cpu", ref_table="metric_snapshots", ref_id="s1",
    )
    await store.emit_event(
        event_type="anomaly", source="monitor_loop",
        subject="memory", ref_table="metric_snapshots", ref_id="s2",
    )
    cpu_events = await store.get_events(subject="cpu")
    assert len(cpu_events) == 1
    assert cpu_events[0]["subject"] == "cpu"


async def test_get_events_filter_by_agent(store: Store) -> None:
    await store.emit_event(
        event_type="anomaly", source="monitor_loop",
        subject="cpu", ref_table="metric_snapshots", ref_id="s1",
        agent="sre",
    )
    await store.emit_event(
        event_type="anomaly", source="monitor_loop",
        subject="cpu", ref_table="metric_snapshots", ref_id="s2",
        agent="security",
    )
    sre_events = await store.get_events(agent="sre")
    assert len(sre_events) == 1


async def test_get_events_filter_by_time(store: Store) -> None:
    await store.emit_event(
        event_type="anomaly", source="monitor_loop",
        subject="cpu", ref_table="metric_snapshots", ref_id="s1",
    )
    # Since is in the past — should find our event
    events = await store.get_events(since=datetime.now(UTC) - timedelta(hours=1))
    assert len(events) == 1
    # Since is in the future — should not find
    events = await store.get_events(since=datetime.now(UTC) + timedelta(hours=1))
    assert len(events) == 0


async def test_get_events_limit(store: Store) -> None:
    for i in range(10):
        await store.emit_event(
            event_type="anomaly", source="monitor_loop",
            subject="cpu", ref_table="metric_snapshots", ref_id=f"s{i}",
        )
    events = await store.get_events(limit=3)
    assert len(events) == 3


async def test_get_events_for_subject(store: Store) -> None:
    await store.emit_event(
        event_type="anomaly", source="monitor_loop",
        subject="cpu", ref_table="metric_snapshots", ref_id="s1",
    )
    await store.emit_event(
        event_type="insight", source="monitor_loop",
        subject="cpu", ref_table="insights", ref_id="i1",
    )
    await store.emit_event(
        event_type="anomaly", source="monitor_loop",
        subject="memory", ref_table="metric_snapshots", ref_id="s2",
    )
    events = await store.get_events_for_subject("cpu")
    assert len(events) == 2
    assert all(e["subject"] == "cpu" for e in events)


async def test_get_events_near_time(store: Store) -> None:
    await store.emit_event(
        event_type="anomaly", source="monitor_loop",
        subject="cpu", ref_table="metric_snapshots", ref_id="s1",
    )
    # Nearby (within 30 min window)
    events = await store.get_events_near_time(datetime.now(UTC))
    assert len(events) == 1
    # Far away (1 day in the past, 1 min window)
    events = await store.get_events_near_time(
        datetime.now(UTC) - timedelta(days=1), window_minutes=1,
    )
    assert len(events) == 0


async def test_search_events(store: Store) -> None:
    await store.emit_event(
        event_type="anomaly", source="monitor_loop",
        subject="signups", ref_table="metric_snapshots", ref_id="s1",
        summary="Signup rate dropped below threshold",
    )
    await store.emit_event(
        event_type="anomaly", source="monitor_loop",
        subject="cpu", ref_table="metric_snapshots", ref_id="s2",
        summary="CPU usage spiked above 95%",
    )
    results = await store.search_events("signup")
    assert len(results) == 1
    assert results[0]["subject"] == "signups"


async def test_search_events_no_results(store: Store) -> None:
    await store.emit_event(
        event_type="anomaly", source="monitor_loop",
        subject="cpu", ref_table="metric_snapshots", ref_id="s1",
        summary="CPU usage high",
    )
    results = await store.search_events("nonexistent_term_xyz")
    assert len(results) == 0


async def test_count_events_for_subject(store: Store) -> None:
    for i in range(5):
        await store.emit_event(
            event_type="anomaly", source="monitor_loop",
            subject="cpu", ref_table="metric_snapshots", ref_id=f"s{i}",
        )
    await store.emit_event(
        event_type="insight", source="monitor_loop",
        subject="cpu", ref_table="insights", ref_id="i1",
    )
    # All types
    assert await store.count_events_for_subject("cpu") == 6
    # Anomaly only
    assert await store.count_events_for_subject("cpu", event_type="anomaly") == 5
    # With since filter
    count = await store.count_events_for_subject(
        "cpu", since=datetime.now(UTC) - timedelta(hours=1),
    )
    assert count == 6


async def test_get_event_recurrence_summary(store: Store) -> None:
    for i in range(4):
        await store.emit_event(
            event_type="anomaly", source="monitor_loop",
            subject="db_conn_pool_util", ref_table="metric_snapshots",
            ref_id=f"s{i}", severity="warning",
            summary=f"Anomaly #{i}",
        )
    summary = await store.get_event_recurrence_summary(
        subject="db_conn_pool_util", event_type="anomaly", days=30,
    )
    assert summary is not None
    assert summary["count"] == 4
    assert summary["first_seen"] is not None
    assert summary["last_seen"] is not None
    assert isinstance(summary["common_hours"], list)


async def test_get_event_recurrence_summary_no_data(store: Store) -> None:
    summary = await store.get_event_recurrence_summary(
        subject="nonexistent", event_type="anomaly",
    )
    assert summary is None


async def test_event_default_agent(store: Store) -> None:
    await store.emit_event(
        event_type="anomaly", source="monitor_loop",
        subject="cpu", ref_table="metric_snapshots", ref_id="s1",
    )
    events = await store.get_events()
    assert events[0]["agent"] == "sre"


async def test_event_retention(store: Store) -> None:
    await store.emit_event(
        event_type="anomaly", source="monitor_loop",
        subject="cpu", ref_table="metric_snapshots", ref_id="s1",
    )
    result = await store.apply_retention(
        metrics_days=30, events_days=0, insights_days=30, max_snapshots=10,
    )
    assert result["observation_events"] == 1
    events = await store.get_events()
    assert len(events) == 0
