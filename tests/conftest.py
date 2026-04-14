"""Shared pytest fixtures."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from observibot.agent.llm_provider import MockProvider
from observibot.connectors.base import (
    BaseConnector,
    Capability,
    ConnectorCapabilities,
)
from observibot.core.models import (
    ChangeEvent,
    HealthStatus,
    MetricSnapshot,
    Relationship,
    ServiceInfo,
    SystemFragment,
    SystemModel,
    TableInfo,
)
from observibot.core.store import Store

EXAMPLE_TABLES = [
    TableInfo(
        name="users",
        schema="public",
        columns=[
            {"name": "id", "type": "uuid", "nullable": False, "default": None},
            {"name": "email", "type": "text", "nullable": False, "default": None},
            {"name": "created_at", "type": "timestamptz", "nullable": False, "default": "now()"},
        ],
        row_count=1000,
        primary_key=["id"],
    ),
    TableInfo(
        name="profiles",
        schema="public",
        columns=[
            {"name": "id", "type": "uuid", "nullable": False, "default": None},
            {"name": "user_id", "type": "uuid", "nullable": False, "default": None},
            {"name": "full_name", "type": "text", "nullable": True, "default": None},
        ],
        row_count=950,
        primary_key=["id"],
    ),
    TableInfo(
        name="tasks",
        schema="public",
        columns=[
            {"name": "id", "type": "uuid", "nullable": False, "default": None},
            {"name": "owner_id", "type": "uuid", "nullable": False, "default": None},
            {"name": "title", "type": "text", "nullable": False, "default": None},
            {"name": "status", "type": "text", "nullable": False, "default": "'open'"},
            {"name": "created_at", "type": "timestamptz", "nullable": False, "default": "now()"},
        ],
        row_count=25000,
        primary_key=["id"],
    ),
    TableInfo(
        name="assignments",
        schema="public",
        columns=[
            {"name": "task_id", "type": "uuid", "nullable": False, "default": None},
            {"name": "user_id", "type": "uuid", "nullable": False, "default": None},
            {"name": "role", "type": "text", "nullable": True, "default": None},
        ],
        row_count=30000,
        primary_key=["task_id", "user_id"],
    ),
    TableInfo(
        name="gator_jobs",
        schema="public",
        columns=[
            {"name": "id", "type": "uuid", "nullable": False, "default": None},
            {"name": "kind", "type": "text", "nullable": False, "default": None},
            {"name": "payload", "type": "jsonb", "nullable": True, "default": None},
            {"name": "status", "type": "text", "nullable": False, "default": "'queued'"},
        ],
        row_count=500,
        primary_key=["id"],
    ),
    TableInfo(
        name="payments",
        schema="public",
        columns=[
            {"name": "id", "type": "uuid", "nullable": False, "default": None},
            {"name": "user_id", "type": "uuid", "nullable": False, "default": None},
            {"name": "amount_cents", "type": "integer", "nullable": False, "default": None},
            {"name": "status", "type": "text", "nullable": False, "default": None},
        ],
        row_count=4200,
        primary_key=["id"],
    ),
]

EXAMPLE_RELATIONSHIPS = [
    Relationship(
        from_table="profiles",
        from_column="user_id",
        to_table="users",
        to_column="id",
        constraint_name="profiles_user_id_fkey",
    ),
    Relationship(
        from_table="tasks",
        from_column="owner_id",
        to_table="users",
        to_column="id",
        constraint_name="tasks_owner_id_fkey",
    ),
    Relationship(
        from_table="assignments",
        from_column="task_id",
        to_table="tasks",
        to_column="id",
        constraint_name="assignments_task_id_fkey",
    ),
    Relationship(
        from_table="assignments",
        from_column="user_id",
        to_table="users",
        to_column="id",
        constraint_name="assignments_user_id_fkey",
    ),
    Relationship(
        from_table="payments",
        from_column="user_id",
        to_table="users",
        to_column="id",
        constraint_name="payments_user_id_fkey",
    ),
]

EXAMPLE_SERVICES = [
    ServiceInfo(
        name="web",
        type="web",
        environment="production",
        status="SUCCESS",
        last_deploy_at=datetime(2026, 4, 1, 12, 0, tzinfo=UTC),
        last_deploy_id="dep-001",
    ),
    ServiceInfo(
        name="worker",
        type="worker",
        environment="production",
        status="SUCCESS",
        last_deploy_at=datetime(2026, 4, 1, 11, 0, tzinfo=UTC),
        last_deploy_id="dep-002",
    ),
]


class FakeSupabaseConnector(BaseConnector):
    """Non-network mock connector."""

    type = "supabase"

    def __init__(self, name: str = "mock-supabase") -> None:
        super().__init__(name=name, config={})
        self.metrics_calls = 0

    def get_capabilities(self) -> ConnectorCapabilities:
        return ConnectorCapabilities(
            capabilities=(
                Capability.DISCOVERY
                | Capability.METRICS
                | Capability.CHANGES
                | Capability.HEALTH
            ),
            requires_elevated_role=True,
        )

    async def connect(self) -> None:
        return None

    async def discover(self) -> SystemFragment:
        return SystemFragment(
            connector_name=self.name,
            connector_type=self.type,
            tables=[TableInfo.from_dict(t.to_dict()) for t in EXAMPLE_TABLES],
            relationships=[Relationship.from_dict(r.to_dict()) for r in EXAMPLE_RELATIONSHIPS],
        )

    async def collect_metrics(self) -> list[MetricSnapshot]:
        self.metrics_calls += 1
        now = datetime.now(UTC)
        metrics = []
        for table in EXAMPLE_TABLES:
            metrics.append(
                MetricSnapshot(
                    connector_name=self.name,
                    metric_name="table_row_count",
                    value=float(table.row_count or 0),
                    labels={"schema": table.schema, "table": table.name},
                    collected_at=now,
                )
            )
        metrics.append(
            MetricSnapshot(
                connector_name=self.name,
                metric_name="active_connections",
                value=5.0,
                labels={},
                collected_at=now,
            )
        )
        return metrics

    async def get_recent_changes(self, since: datetime) -> list[ChangeEvent]:
        return []

    async def health_check(self) -> HealthStatus:
        return HealthStatus(
            connector_name=self.name, healthy=True, latency_ms=1.0, message="ok"
        )

    def required_permissions(self) -> list[str]:
        return ["read pg_stat_*"]


class FakeRailwayConnector(BaseConnector):
    type = "railway"

    def __init__(self, name: str = "mock-railway") -> None:
        super().__init__(name=name, config={})

    def get_capabilities(self) -> ConnectorCapabilities:
        return ConnectorCapabilities(
            capabilities=(
                Capability.DISCOVERY
                | Capability.CHANGES
                | Capability.HEALTH
            ),
            has_rate_limits=True,
        )

    async def connect(self) -> None:
        return None

    async def discover(self) -> SystemFragment:
        return SystemFragment(
            connector_name=self.name,
            connector_type=self.type,
            services=[ServiceInfo.from_dict(s.to_dict()) for s in EXAMPLE_SERVICES],
        )

    async def collect_metrics(self) -> list[MetricSnapshot]:
        now = datetime.now(UTC)
        return [
            MetricSnapshot(
                connector_name=self.name,
                metric_name="service_count",
                value=float(len(EXAMPLE_SERVICES)),
                labels={},
                collected_at=now,
            )
        ]

    async def get_recent_changes(self, since: datetime) -> list[ChangeEvent]:
        return [
            ChangeEvent(
                connector_name=self.name,
                event_type="deploy",
                summary="web deploy SUCCESS",
                details={"service": "web"},
                occurred_at=datetime.now(UTC),
            )
        ]

    async def health_check(self) -> HealthStatus:
        return HealthStatus(
            connector_name=self.name, healthy=True, latency_ms=2.0, message="ok"
        )

    def required_permissions(self) -> list[str]:
        return ["read railway project"]


@pytest.fixture
def mock_supabase_connector() -> FakeSupabaseConnector:
    return FakeSupabaseConnector()


@pytest.fixture
def mock_railway_connector() -> FakeRailwayConnector:
    return FakeRailwayConnector()


@pytest.fixture
def mock_llm_provider() -> MockProvider:
    return MockProvider(model="mock-model")


@pytest.fixture
def sample_system_model(
    mock_supabase_connector: FakeSupabaseConnector,
    mock_railway_connector: FakeRailwayConnector,
) -> SystemModel:
    frag_a = SystemFragment(
        connector_name=mock_supabase_connector.name,
        connector_type=mock_supabase_connector.type,
        tables=[TableInfo.from_dict(t.to_dict()) for t in EXAMPLE_TABLES],
        relationships=[Relationship.from_dict(r.to_dict()) for r in EXAMPLE_RELATIONSHIPS],
    )
    frag_b = SystemFragment(
        connector_name=mock_railway_connector.name,
        connector_type=mock_railway_connector.type,
        services=[ServiceInfo.from_dict(s.to_dict()) for s in EXAMPLE_SERVICES],
    )
    from observibot.core.discovery import DiscoveryEngine

    engine = DiscoveryEngine(connectors=[])
    model = engine.merge_fragments([frag_a, frag_b])
    return model


@pytest.fixture
def tmp_config(tmp_path: Path) -> Path:
    cfg_text = """\
llm:
  provider: mock
  model: mock-model
  api_key: ${NONEXISTENT_KEY:-}
connectors: []
monitor:
  collection_interval_seconds: 60
store:
  type: sqlite
  path: %s
alerting:
  channels: []
""" % (tmp_path / "observibot.db")
    cfg_path = tmp_path / "observibot.yaml"
    cfg_path.write_text(cfg_text)
    return cfg_path


@pytest.fixture
async def tmp_store(tmp_path: Path):
    path = tmp_path / "store.db"
    async with Store(path) as store:
        yield store


def make_metric(
    name: str,
    value: float,
    offset_seconds: int = 0,
    labels: dict[str, str] | None = None,
    connector: str = "mock-db",
) -> MetricSnapshot:
    return MetricSnapshot(
        connector_name=connector,
        metric_name=name,
        value=value,
        labels=labels or {},
        collected_at=datetime.now(UTC) - timedelta(seconds=offset_seconds),
    )
