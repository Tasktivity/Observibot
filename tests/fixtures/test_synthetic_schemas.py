"""Smoke tests for the Tier 0 synthetic schema fixtures.

These ensure each fixture builds a well-formed :class:`SystemModel`
round-trips through ``to_dict``/``from_dict``, and produces sensible
``Anomaly`` and ``MetricSnapshot`` objects for downstream tests to use.
"""
from __future__ import annotations

from observibot.core.anomaly import Anomaly
from observibot.core.models import MetricSnapshot, SystemModel
from tests.fixtures.synthetic_schemas import (
    ecommerce_anomaly,
    ecommerce_metrics,
    ecommerce_schema,
    event_stream_anomaly,
    event_stream_metrics,
    event_stream_schema,
    medical_anomaly,
    medical_metrics,
    medical_records_schema,
)


def _assert_model_roundtrip(model: SystemModel) -> None:
    restored = SystemModel.from_dict(model.to_dict())
    assert len(restored.tables) == len(model.tables)
    assert len(restored.relationships) == len(model.relationships)
    assert restored.fingerprint == model.fingerprint


def test_ecommerce_schema_is_well_formed() -> None:
    model = ecommerce_schema()
    names = {t.name for t in model.tables}
    assert {"orders", "line_items", "customers", "inventory"}.issubset(names)
    assert all(t.primary_key for t in model.tables if t.name != "line_items" or True)
    _assert_model_roundtrip(model)


def test_ecommerce_anomaly_and_metrics() -> None:
    a = ecommerce_anomaly()
    assert isinstance(a, Anomaly)
    assert a.metric_name == "order_count"
    assert a.direction == "spike"
    assert a.absolute_diff > 0
    metrics = ecommerce_metrics(n=50)
    assert len(metrics) == 50
    assert all(isinstance(m, MetricSnapshot) for m in metrics)
    assert metrics[0].value < metrics[-1].value  # ramp


def test_medical_schema_has_rls_policies() -> None:
    model = medical_records_schema()
    patients = next(t for t in model.tables if t.name == "patients")
    assert patients.rls_policies, "medical patients must carry RLS policies"
    encounters = next(t for t in model.tables if t.name == "encounters")
    assert encounters.rls_policies
    _assert_model_roundtrip(model)


def test_medical_anomaly_is_dip_direction() -> None:
    a = medical_anomaly()
    assert a.direction == "dip"
    assert a.value < a.median
    metrics = medical_metrics(n=20)
    assert all(m.value == 50.0 for m in metrics)  # flat baseline


def test_event_stream_schema_has_time_partitioned_fact() -> None:
    model = event_stream_schema()
    fact = next(t for t in model.tables if t.name == "events_2026_04")
    assert fact.row_count and fact.row_count > 1_000_000
    # Severity enum column + unit-suffixed numeric columns.
    col_names = {c["name"] for c in fact.columns}
    assert "severity" in col_names
    assert "duration_ms" in col_names
    assert "bytes_transferred" in col_names
    _assert_model_roundtrip(model)


def test_event_stream_anomaly_and_flat_metrics() -> None:
    a = event_stream_anomaly()
    assert a.metric_name == "event_count"
    # The flat-baseline metrics are specifically for the MAD=0 path.
    metrics = event_stream_metrics(n=30)
    values = {m.value for m in metrics}
    assert values == {100_000.0}
