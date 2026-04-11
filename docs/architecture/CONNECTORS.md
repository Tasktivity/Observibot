# Observibot — Connector Architecture

## BaseConnector Interface

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime

@dataclass
class TableInfo:
    schema_name: str
    table_name: str
    columns: list[dict]           # {name, type, nullable, default, is_pk}
    row_count_estimate: int
    size_bytes: int | None
    indexes: list[dict]
    constraints: list[dict]       # foreign keys, unique, check

@dataclass
class Relationship:
    from_table: str
    from_column: str
    to_table: str
    to_column: str
    relationship_type: str        # "foreign_key" or "inferred"
    confidence: float             # 1.0 for FK, 0.0-1.0 for inferred

@dataclass
class ServiceInfo:
    name: str
    platform: str                 # "railway", "fly", "render"
    status: str
    deploy_count: int
    last_deploy_at: datetime | None
    environment: dict             # env var NAMES only (never values)
    resources: dict               # CPU, memory allocations

@dataclass
class SystemFragment:
    connector_name: str
    tables: list[TableInfo] = field(default_factory=list)
    relationships: list[Relationship] = field(default_factory=list)
    services: list[ServiceInfo] = field(default_factory=list)
    raw_metadata: dict = field(default_factory=dict)

@dataclass
class MetricSnapshot:
    connector_name: str
    metrics: list[dict]           # {name, value, labels, collected_at}
    collected_at: datetime

@dataclass
class ChangeEvent:
    connector_name: str
    event_type: str               # "deploy", "migration", "config_change", "scale"
    description: str
    metadata: dict
    occurred_at: datetime

@dataclass
class HealthStatus:
    connector_name: str
    healthy: bool
    latency_ms: float
    permissions_ok: bool
    issues: list[str]

class BaseConnector(ABC):
    def __init__(self, name: str, config: dict):
        self.name = name
        self.config = config

    @abstractmethod
    async def discover(self) -> SystemFragment: ...
    @abstractmethod
    async def collect_metrics(self) -> MetricSnapshot: ...
    @abstractmethod
    async def get_recent_changes(self, since: datetime) -> list[ChangeEvent]: ...
    @abstractmethod
    async def health_check(self) -> HealthStatus: ...
    @abstractmethod
    def required_permissions(self) -> list[str]: ...
```

---

## Supabase Connector

**Connection:** Direct PostgreSQL on port `5432`. **Do NOT use the Supavisor
pooler port `6543`** — `pg_stat_activity` through Supavisor reports pooler
sessions, not your application sessions, which makes connection-count metrics
useless.

**Credentials:** `SUPABASE_DB_URL` (required), `SUPABASE_SERVICE_KEY` (optional), `SUPABASE_PROJECT_REF` (optional for Metrics API).

**Required permissions:**

```sql
-- Run once as the postgres superuser to create a dedicated role.
CREATE ROLE observibot LOGIN PASSWORD 'STRONG_PASSWORD';
GRANT pg_monitor TO observibot;            -- pg_stat_*, pg_stat_activity
GRANT pg_read_all_stats TO observibot;     -- redundant on PG 14+, harmless
GRANT USAGE ON SCHEMA public TO observibot;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO observibot;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT ON TABLES TO observibot;
```

A helper script lives at `scripts/setup_supabase_role.sh` that automates this.

**Foreign-key discovery fallback:** Managed Supabase often returns 0 rows from
`information_schema.table_constraints` for non-owner roles. The connector
automatically retries via `pg_constraint` (which `pg_monitor` can read) so FKs
are still discovered without superuser access.

**Discovery queries:**

| Query | Purpose |
|-------|---------|
| `information_schema.columns` (filtered, excludes internal schemas) | Tables and columns |
| `information_schema.table_constraints` + `key_column_usage` | Foreign key relationships |
| `pg_stat_user_tables` | Row estimates, table sizes |
| `pg_policies` | RLS policies (Supabase-specific) |
| `pg_indexes` | Index inspection |

**Metrics collected per cycle:**

| Metric | Source | Purpose |
|--------|--------|---------|
| `table_row_count` per table | `pg_stat_user_tables.n_live_tup` | Growth/shrinkage detection |
| `table_inserts` per table | `n_tup_ins` delta | Activity rate |
| `table_updates` per table | `n_tup_upd` delta | Mutation rate |
| `table_deletes` per table | `n_tup_del` delta | Data loss detection |
| `active_connections` | `pg_stat_activity` count | Pool pressure |
| `blocked_queries` | `pg_stat_activity` WHERE wait_event_type='Lock' | Lock contention |
| `long_running_queries` | `pg_stat_activity` WHERE duration > 30s | Performance |
| `dead_tuples_ratio` per table | `n_dead_tup / (n_live_tup + 1)` | Vacuum health |
| `cache_hit_ratio` | `pg_stat_database` | Memory pressure |

**Change detection:** Schema diff against previous discovery, `supabase_migrations.schema_migrations` if accessible, RLS policy diffs.

---

## Railway Connector

**Connection:** Railway GraphQL API (`https://backboard.railway.com/graphql/v2`).

**Credentials:**

| Variable | Where to get it |
|---|---|
| `RAILWAY_API_TOKEN` | Railway Dashboard → Account Settings → Tokens → Create. Use a token scoped to the specific project, not your account-wide token. |
| `RAILWAY_PROJECT_ID` | Railway Dashboard → Project → Settings → General → copy the Project ID UUID. |

**Capabilities:** `DISCOVERY | CHANGES | HEALTH`. **No `METRICS`** — Railway's
public GraphQL API does not expose CPU/memory/network metrics. The monitor
loop checks capabilities and skips `collect_metrics()` for Railway, so this
degrades cleanly.

**Discovery:** Project topology, services, environments, recent deployments via GraphQL.

**Change detection:** New deployments and deployment status changes via the
`deployments` GraphQL query.

**Rate limits:** Railway throttles the GraphQL endpoint. The connector uses
exponential backoff on `429 Too Many Requests`.

---

## Generic PostgreSQL Connector

Same as Supabase connector minus Supabase-specific features (no RLS, no
Metrics API, no migration table). Useful for self-hosted Postgres, Neon,
RDS, etc.

The same `pg_monitor` grant that the Supabase connector requires applies here.

---

## Connector Capabilities

Every connector declares a `ConnectorCapabilities` value via
`get_capabilities()`. The monitor loop filters operations by capability —
e.g. it never calls `collect_metrics()` on a Railway connector.

| Capability | Meaning |
|---|---|
| `DISCOVERY` | Can produce a `SystemFragment` (tables, services). |
| `METRICS` | Can produce `MetricSnapshot` instances. |
| `CHANGES` | Can produce `ChangeEvent` instances. |
| `HEALTH` | Supports `health_check()`. |
| `RESOURCE_METRICS` | Reports CPU/memory/network usage. |

| Connector | Capabilities | Elevated role | Rate limits |
|---|---|---|---|
| Supabase | DISCOVERY, METRICS, CHANGES, HEALTH | Yes (`pg_monitor`) | No |
| PostgreSQL | DISCOVERY, METRICS, CHANGES, HEALTH | Yes (`pg_monitor`) | No |
| Railway | DISCOVERY, CHANGES, HEALTH | No | Yes |

---

## Adding a New Connector

1. Create `src/observibot/connectors/your_platform.py` and subclass `BaseConnector`.
2. Implement the abstract methods:
   - `get_capabilities()` — declare what you support.
   - `connect()` — open pools/clients (idempotent).
   - `discover()` — return a `SystemFragment`.
   - `collect_metrics()` — return a list of `MetricSnapshot` (return `[]` if not supported).
   - `get_recent_changes(since)` — return a list of `ChangeEvent`.
   - `health_check()` — quick reachability probe.
   - `required_permissions()` — list of human-readable strings.
3. Register the connector type in `src/observibot/connectors/__init__.py`'s
   `get_connector` factory.
4. Add a config example to `config/observibot.example.yaml`.
5. Write tests in `tests/connectors/test_your_platform.py` — at minimum:
   credential validation, capability declaration, graceful failure when
   credentials are missing.
6. Document permissions and quirks in this file.
