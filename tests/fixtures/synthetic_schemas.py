"""Synthetic schema fixtures for Tier 0 (Generality Firewall) tests.

These fixtures exist so that every pattern-based fix in Observibot has at
least one test exercising it against a schema *deliberately unlike*
TaskGator's educational-content domain. See
``docs/TESTING_STANDARDS.md`` ("Tier 0: Generality Firewall") for the
standard these fixtures implement.

Three reference domains are provided, each with a ``*_schema()`` builder,
a ``*_anomaly()`` helper, and a ``*_metrics()`` helper:

- **ecommerce** — orders, line_items, customers, inventory, shipments,
  returns. Enum on ``order_status``. Soft-delete via ``archived_at``. RLS
  on customer-scoped tables.
- **medical_records** — patients, encounters, diagnoses, prescriptions,
  providers. Enum on ``encounter_type``. Soft-delete via ``deleted_at``
  with audit trail. Strict RLS.
- **event_stream** — events, sessions, aggregates_hourly,
  aggregates_daily. Severity enum. Unit-suffixed numeric columns. No
  soft-delete. Time-partitioned.

None of the terminology here overlaps with TaskGator's domain. This is
validated by the forbidden-string grep in the Tier 0 checklist.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from observibot.core.anomaly import Anomaly
from observibot.core.models import (
    MetricSnapshot,
    Relationship,
    SystemModel,
    TableInfo,
)


def _column(
    name: str,
    data_type: str,
    *,
    nullable: bool = True,
    default: str | None = None,
) -> dict[str, object]:
    col: dict[str, object] = {"name": name, "type": data_type, "nullable": nullable}
    if default is not None:
        col["default"] = default
    return col


# ---------------------------------------------------------------------------
# E-commerce domain
# ---------------------------------------------------------------------------


def ecommerce_schema() -> SystemModel:
    """Return a SystemModel shaped like a typical B2C e-commerce backend."""
    tables = [
        TableInfo(
            name="customers",
            schema="public",
            columns=[
                _column("id", "uuid", nullable=False),
                _column("email", "text", nullable=False),
                _column("billing_country", "text"),
                _column("archived_at", "timestamp with time zone"),
                _column("created_at", "timestamp with time zone", nullable=False),
            ],
            row_count=48_000,
            primary_key=["id"],
            rls_policies=[
                {"name": "customer_self_read", "cmd": "SELECT"},
            ],
        ),
        TableInfo(
            name="orders",
            schema="public",
            columns=[
                _column("id", "uuid", nullable=False),
                _column("customer_id", "uuid", nullable=False),
                _column("order_status", "text", nullable=False),
                _column("subtotal_cents", "integer"),
                _column("archived_at", "timestamp with time zone"),
                _column("placed_at", "timestamp with time zone", nullable=False),
            ],
            row_count=512_000,
            primary_key=["id"],
            rls_policies=[
                {"name": "order_customer_read", "cmd": "SELECT"},
            ],
        ),
        TableInfo(
            name="line_items",
            schema="public",
            columns=[
                _column("id", "uuid", nullable=False),
                _column("order_id", "uuid", nullable=False),
                _column("sku", "text", nullable=False),
                _column("quantity", "integer", nullable=False),
                _column("unit_price_cents", "integer"),
            ],
            row_count=2_400_000,
            primary_key=["id"],
        ),
        TableInfo(
            name="inventory",
            schema="public",
            columns=[
                _column("sku", "text", nullable=False),
                _column("warehouse", "text", nullable=False),
                _column("on_hand", "integer", nullable=False, default="0"),
                _column("safety_stock", "integer", default="0"),
            ],
            row_count=12_500,
            primary_key=["sku", "warehouse"],
        ),
        TableInfo(
            name="shipments",
            schema="public",
            columns=[
                _column("id", "uuid", nullable=False),
                _column("order_id", "uuid", nullable=False),
                _column("carrier", "text"),
                _column("tracking_number", "text"),
                _column("shipped_at", "timestamp with time zone"),
                _column("archived_at", "timestamp with time zone"),
            ],
            row_count=498_000,
            primary_key=["id"],
        ),
        TableInfo(
            name="returns",
            schema="public",
            columns=[
                _column("id", "uuid", nullable=False),
                _column("order_id", "uuid", nullable=False),
                _column("reason_code", "text"),
                _column("refund_cents", "integer"),
                _column("archived_at", "timestamp with time zone"),
            ],
            row_count=18_200,
            primary_key=["id"],
        ),
    ]
    relationships = [
        Relationship("orders", "customer_id", "customers", "id"),
        Relationship("line_items", "order_id", "orders", "id"),
        Relationship("shipments", "order_id", "orders", "id"),
        Relationship("returns", "order_id", "orders", "id"),
    ]
    model = SystemModel(tables=tables, relationships=relationships)
    model.compute_fingerprint()
    return model


def ecommerce_anomaly(
    metric: str = "order_count",
    direction: str = "spike",
    *,
    value: float = 1200.0,
    median: float = 900.0,
    severity: str = "warning",
    labels: dict[str, str] | None = None,
) -> Anomaly:
    return Anomaly(
        metric_name=metric,
        connector_name="ecommerce_pg",
        labels=labels or {"table": "orders"},
        value=value,
        median=median,
        mad=25.0,
        modified_z=8.4 if direction == "spike" else -8.4,
        absolute_diff=abs(value - median),
        severity=severity,
        direction=direction,
        consecutive_count=3,
        detected_at=datetime.now(UTC),
        sample_count=48,
        baseline_source="rolling",
    )


def ecommerce_metrics(n: int = 100) -> list[MetricSnapshot]:
    """Return ``n`` synthetic order_count snapshots ramping from 800 to 1100."""
    now = datetime.now(UTC)
    step = max(n - 1, 1)
    return [
        MetricSnapshot(
            connector_name="ecommerce_pg",
            metric_name="order_count",
            value=800.0 + (300.0 * i / step),
            labels={"table": "orders"},
            collected_at=now - timedelta(minutes=5 * (n - i)),
        )
        for i in range(n)
    ]


# ---------------------------------------------------------------------------
# Medical records domain
# ---------------------------------------------------------------------------


def medical_records_schema() -> SystemModel:
    """Return a SystemModel shaped like a multi-tenant EHR backend."""
    tables = [
        TableInfo(
            name="patients",
            schema="public",
            columns=[
                _column("id", "uuid", nullable=False),
                _column("mrn", "text", nullable=False),
                _column("date_of_birth", "date"),
                _column("provider_org_id", "uuid", nullable=False),
                _column("deleted_at", "timestamp with time zone"),
                _column("created_at", "timestamp with time zone", nullable=False),
            ],
            row_count=92_000,
            primary_key=["id"],
            rls_policies=[
                {"name": "patient_org_read", "cmd": "SELECT"},
                {"name": "patient_org_write", "cmd": "UPDATE"},
            ],
        ),
        TableInfo(
            name="encounters",
            schema="public",
            columns=[
                _column("id", "uuid", nullable=False),
                _column("patient_id", "uuid", nullable=False),
                _column("provider_id", "uuid", nullable=False),
                _column("encounter_type", "text", nullable=False),
                _column("scheduled_at", "timestamp with time zone"),
                _column("completed_at", "timestamp with time zone"),
                _column("deleted_at", "timestamp with time zone"),
            ],
            row_count=340_000,
            primary_key=["id"],
            rls_policies=[
                {"name": "encounter_org_read", "cmd": "SELECT"},
            ],
        ),
        TableInfo(
            name="diagnoses",
            schema="public",
            columns=[
                _column("id", "uuid", nullable=False),
                _column("encounter_id", "uuid", nullable=False),
                _column("icd10_code", "text", nullable=False),
                _column("deleted_at", "timestamp with time zone"),
            ],
            row_count=620_000,
            primary_key=["id"],
        ),
        TableInfo(
            name="prescriptions",
            schema="public",
            columns=[
                _column("id", "uuid", nullable=False),
                _column("encounter_id", "uuid", nullable=False),
                _column("rxnorm_code", "text", nullable=False),
                _column("dose_mg", "numeric"),
                _column("deleted_at", "timestamp with time zone"),
            ],
            row_count=480_000,
            primary_key=["id"],
        ),
        TableInfo(
            name="providers",
            schema="public",
            columns=[
                _column("id", "uuid", nullable=False),
                _column("npi", "text", nullable=False),
                _column("specialty", "text"),
                _column("provider_org_id", "uuid", nullable=False),
            ],
            row_count=2_100,
            primary_key=["id"],
        ),
        TableInfo(
            name="audit_trail",
            schema="public",
            columns=[
                _column("id", "uuid", nullable=False),
                _column("actor_id", "uuid"),
                _column("entity_table", "text", nullable=False),
                _column("entity_id", "uuid", nullable=False),
                _column("action", "text", nullable=False),
                _column("occurred_at", "timestamp with time zone", nullable=False),
            ],
            row_count=1_800_000,
            primary_key=["id"],
        ),
    ]
    relationships = [
        Relationship("encounters", "patient_id", "patients", "id"),
        Relationship("encounters", "provider_id", "providers", "id"),
        Relationship("diagnoses", "encounter_id", "encounters", "id"),
        Relationship("prescriptions", "encounter_id", "encounters", "id"),
    ]
    model = SystemModel(tables=tables, relationships=relationships)
    model.compute_fingerprint()
    return model


def medical_anomaly(
    metric: str = "encounter_count",
    direction: str = "dip",
    *,
    value: float = 40.0,
    median: float = 120.0,
    severity: str = "critical",
    labels: dict[str, str] | None = None,
) -> Anomaly:
    return Anomaly(
        metric_name=metric,
        connector_name="ehr_pg",
        labels=labels or {"table": "encounters", "org_id": "org-42"},
        value=value,
        median=median,
        mad=6.0,
        modified_z=-9.0 if direction == "dip" else 9.0,
        absolute_diff=abs(value - median),
        severity=severity,
        direction=direction,
        consecutive_count=4,
        detected_at=datetime.now(UTC),
        sample_count=72,
        baseline_source="seasonal",
    )


def medical_metrics(n: int = 100) -> list[MetricSnapshot]:
    """Return ``n`` snapshots with a steady 50/hour encounter rate."""
    now = datetime.now(UTC)
    return [
        MetricSnapshot(
            connector_name="ehr_pg",
            metric_name="encounter_count",
            value=50.0,
            labels={"table": "encounters"},
            collected_at=now - timedelta(minutes=5 * (n - i)),
        )
        for i in range(n)
    ]


# ---------------------------------------------------------------------------
# Event stream domain
# ---------------------------------------------------------------------------


def event_stream_schema() -> SystemModel:
    """Return a SystemModel shaped like a high-volume event-stream backend."""
    tables = [
        TableInfo(
            name="events_2026_04",
            schema="analytics",
            columns=[
                _column("event_id", "uuid", nullable=False),
                _column("session_id", "uuid", nullable=False),
                _column("event_name", "text", nullable=False),
                _column("severity", "text", nullable=False),
                _column("duration_ms", "bigint"),
                _column("bytes_transferred", "bigint"),
                _column("emitted_at", "timestamp with time zone", nullable=False),
            ],
            row_count=1_250_000_000,
            primary_key=["event_id"],
        ),
        TableInfo(
            name="sessions",
            schema="analytics",
            columns=[
                _column("session_id", "uuid", nullable=False),
                _column("device_kind", "text"),
                _column("started_at", "timestamp with time zone", nullable=False),
                _column("ended_at", "timestamp with time zone"),
            ],
            row_count=84_000_000,
            primary_key=["session_id"],
        ),
        TableInfo(
            name="aggregates_hourly",
            schema="analytics",
            columns=[
                _column("bucket_hour", "timestamp with time zone", nullable=False),
                _column("event_name", "text", nullable=False),
                _column("event_count", "bigint", nullable=False, default="0"),
                _column("p95_duration_ms", "numeric"),
                _column("total_bytes_transferred", "bigint"),
            ],
            row_count=720_000,
            primary_key=["bucket_hour", "event_name"],
        ),
        TableInfo(
            name="aggregates_daily",
            schema="analytics",
            columns=[
                _column("bucket_day", "date", nullable=False),
                _column("event_name", "text", nullable=False),
                _column("event_count", "bigint", nullable=False, default="0"),
                _column("p95_duration_ms", "numeric"),
                _column("total_bytes_transferred", "bigint"),
            ],
            row_count=30_000,
            primary_key=["bucket_day", "event_name"],
        ),
    ]
    relationships = [
        Relationship("events_2026_04", "session_id", "sessions", "session_id"),
    ]
    model = SystemModel(tables=tables, relationships=relationships)
    model.compute_fingerprint()
    return model


def event_stream_anomaly(
    metric: str = "event_count",
    direction: str = "spike",
    *,
    value: float = 180_000.0,
    median: float = 90_000.0,
    severity: str = "warning",
    labels: dict[str, str] | None = None,
) -> Anomaly:
    return Anomaly(
        metric_name=metric,
        connector_name="events_clickhouse",
        labels=labels or {"table": "aggregates_hourly", "severity": "error"},
        value=value,
        median=median,
        mad=4_500.0,
        modified_z=13.5 if direction == "spike" else -13.5,
        absolute_diff=abs(value - median),
        severity=severity,
        direction=direction,
        consecutive_count=3,
        detected_at=datetime.now(UTC),
        sample_count=120,
        baseline_source="rolling",
    )


def event_stream_metrics(n: int = 100) -> list[MetricSnapshot]:
    """Return ``n`` snapshots with a perfectly flat 100k event_count baseline.

    Chosen to exercise the MAD=0 path the Step 3.2 relative-floor gate
    was built for: a very large, perfectly flat metric where any absolute
    floor is meaningless.
    """
    now = datetime.now(UTC)
    return [
        MetricSnapshot(
            connector_name="events_clickhouse",
            metric_name="event_count",
            value=100_000.0,
            labels={"table": "aggregates_hourly"},
            collected_at=now - timedelta(minutes=5 * (n - i)),
        )
        for i in range(n)
    ]
