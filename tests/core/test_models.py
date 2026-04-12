from __future__ import annotations

from datetime import datetime, timezone

from observibot.core.models import (
    ChangeEvent,
    HealthStatus,
    Insight,
    MetricSnapshot,
    Relationship,
    ServiceInfo,
    SystemFragment,
    SystemModel,
    TableInfo,
)


def test_table_info_roundtrip() -> None:
    tbl = TableInfo(
        name="tasks",
        schema="public",
        columns=[{"name": "id", "type": "uuid"}],
        row_count=42,
        primary_key=["id"],
    )
    assert TableInfo.from_dict(tbl.to_dict()) == tbl


def test_system_model_roundtrip(sample_system_model: SystemModel) -> None:
    data = sample_system_model.to_dict()
    restored = SystemModel.from_dict(data)
    assert restored.fingerprint == sample_system_model.fingerprint
    assert len(restored.tables) == len(sample_system_model.tables)
    assert len(restored.relationships) == len(sample_system_model.relationships)
    assert len(restored.services) == len(sample_system_model.services)
    assert restored.compute_fingerprint() == sample_system_model.compute_fingerprint()


def test_system_model_fingerprint_stable(sample_system_model: SystemModel) -> None:
    fp1 = sample_system_model.compute_fingerprint()
    fp2 = sample_system_model.compute_fingerprint()
    assert fp1 == fp2
    assert len(fp1) == 64


def test_system_model_fingerprint_changes_with_schema(
    sample_system_model: SystemModel,
) -> None:
    baseline = sample_system_model.compute_fingerprint()
    sample_system_model.tables.append(
        TableInfo(name="extra", schema="public", columns=[{"name": "id"}])
    )
    new_fp = sample_system_model.compute_fingerprint()
    assert new_fp != baseline


def test_system_model_fingerprint_ignores_row_counts(
    sample_system_model: SystemModel,
) -> None:
    baseline = sample_system_model.compute_fingerprint()
    for tbl in sample_system_model.tables:
        tbl.row_count = (tbl.row_count or 0) + 100
    assert sample_system_model.compute_fingerprint() == baseline


def test_metric_snapshot_roundtrip() -> None:
    m = MetricSnapshot(
        connector_name="c",
        metric_name="x",
        value=1.23,
        labels={"table": "users"},
        collected_at=datetime(2026, 4, 10, 12, tzinfo=timezone.utc),
    )
    restored = MetricSnapshot.from_dict(m.to_dict())
    assert restored.value == 1.23
    assert restored.labels == {"table": "users"}
    assert restored.collected_at == m.collected_at


def test_change_event_roundtrip() -> None:
    e = ChangeEvent(
        connector_name="c", event_type="deploy", summary="s", details={"k": 1}
    )
    assert ChangeEvent.from_dict(e.to_dict()).details == {"k": 1}


def test_health_status_roundtrip() -> None:
    h = HealthStatus(connector_name="c", healthy=True, latency_ms=1.5, message="ok")
    assert HealthStatus.from_dict(h.to_dict()).healthy is True


def test_insight_fingerprint_stable() -> None:
    a = Insight(title="x", summary="y", severity="warning")
    b = Insight(title="x", summary="y", severity="warning")
    assert a.fingerprint == b.fingerprint


def test_insight_fingerprint_ignores_llm_text() -> None:
    """Same structural fields but different LLM-generated text → same fingerprint."""
    a = Insight(
        title="Abnormal spike detected",
        summary="User activity spiked after deploy",
        severity="warning",
        source="anomaly",
        related_tables=["users"],
        related_metrics=["user_count"],
    )
    b = Insight(
        title="Unusual user activity increase",
        summary="Significant uptick in user signups post-deployment",
        severity="warning",
        source="anomaly",
        related_tables=["users"],
        related_metrics=["user_count"],
    )
    assert a.fingerprint == b.fingerprint


def test_insight_fingerprint_differs_on_structural_change() -> None:
    """Different severity or tables → different fingerprint."""
    a = Insight(severity="warning", source="anomaly", related_tables=["users"])
    b = Insight(severity="critical", source="anomaly", related_tables=["users"])
    assert a.fingerprint != b.fingerprint


def test_insight_roundtrip() -> None:
    a = Insight(
        title="x",
        summary="y",
        severity="critical",
        recommended_actions=["do it"],
        related_tables=["public.t"],
    )
    restored = Insight.from_dict(a.to_dict())
    assert restored.fingerprint == a.fingerprint
    assert restored.recommended_actions == ["do it"]


def test_service_info_roundtrip() -> None:
    s = ServiceInfo(
        name="web",
        type="web",
        environment="prod",
        status="SUCCESS",
        last_deploy_at=datetime(2026, 4, 10, tzinfo=timezone.utc),
    )
    assert ServiceInfo.from_dict(s.to_dict()).last_deploy_at == s.last_deploy_at


def test_relationship_roundtrip() -> None:
    r = Relationship(
        from_table="a", from_column="id", to_table="b", to_column="a_id"
    )
    assert Relationship.from_dict(r.to_dict()) == r


def test_system_fragment_roundtrip() -> None:
    f = SystemFragment(connector_name="c", connector_type="supabase")
    f.tables.append(TableInfo(name="users"))
    data = f.to_dict()
    restored = SystemFragment.from_dict(data)
    assert restored.tables[0].name == "users"
