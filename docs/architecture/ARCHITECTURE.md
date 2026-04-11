# Observibot — System Architecture

## Design Philosophy

Observibot is built on three principles:

1. **Read-only everywhere.** Observibot never writes to your production systems. It connects with read-only credentials and observes.

2. **LLM as reasoning engine, not as database.** The LLM interprets, correlates, and generates insights. All raw data stays in structured storage (SQLite/PostgreSQL).

3. **Connector-based extensibility.** Each external system is a connector module with a standard interface. Adding a new platform means writing one connector.

---

## High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        OBSERVIBOT CORE                          │
│  ┌───────────┐  ┌───────────┐  ┌───────────┐  ┌───────────┐   │
│  │ Supabase  │  │ Railway   │  │ Generic   │  │ Future    │   │
│  │ Connector │  │ Connector │  │ PG Conn.  │  │ Connectors│   │
│  └─────┬─────┘  └─────┬─────┘  └─────┬─────┘  └─────┬─────┘   │
│        └──────────────┼──────────────┼──────────────┘           │
│                       ▼                                         │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │                   DISCOVERY ENGINE                       │   │
│  │  Schema Crawler → Relationship Mapper → Topology Builder │   │
│  │  Output: SystemModel (JSON graph)                        │   │
│  └─────────────────────────┬───────────────────────────────┘   │
│                            ▼                                    │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │                   SEMANTIC MODELER                       │   │
│  │  LLM interprets raw SystemModel into business context    │   │
│  │  Onboarding interview for user confirmation/correction   │   │
│  └─────────────────────────┬───────────────────────────────┘   │
│                            ▼                                    │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │                    MONITOR LOOP                          │   │
│  │  1. Collect metrics from all connectors (every 5m)       │   │
│  │  2. Store in local time-series store                     │   │
│  │  3. Statistical anomaly detection (z-score, IQR)         │   │
│  │  4. LLM analysis of anomalies + recent changes           │   │
│  │  5. Route insights to alerting                           │   │
│  │  6. Periodic re-discovery for drift detection (hourly)   │   │
│  └─────────────────────────┬───────────────────────────────┘   │
│                            ▼                                    │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │                   ALERTING / OUTPUT                       │   │
│  │  Slack webhook │ Email (SMTP) │ Generic webhook │ CLI    │   │
│  └─────────────────────────────────────────────────────────┘   │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │                   LOCAL DATA STORE (SQLite)              │   │
│  │  system_snapshots │ metric_snapshots │ change_events     │   │
│  │  insights │ alert_history │ business_context             │   │
│  └─────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

---

## Component Details

### 1. Connectors

Each connector implements the `BaseConnector` interface with four methods:
- `discover() -> SystemFragment` — crawl schema/topology/config
- `collect_metrics() -> MetricSnapshot` — collect current metric values
- `get_recent_changes(since) -> list[ChangeEvent]` — deployments, migrations, config changes
- `health_check() -> HealthStatus` — connectivity and permission verification

Phase 1 connectors: Supabase (schema + pg_stat metrics), Railway (GraphQL API), Generic PostgreSQL.
Future: Neon, Fly.io, Render, Vercel, PlanetScale, generic Prometheus, generic OpenTelemetry.

### 2. Discovery Engine

Orchestrates connectors to build a unified SystemModel containing tables, relationships,
services, metrics endpoints, and topology. Computes SHA256 fingerprint for drift detection.
Runs initially, periodically (hourly), and on-demand.

### 3. Semantic Modeler

LLM interprets raw SystemModel into business context: app type, core entities,
critical metrics, cross-layer correlation rules. Conducts interactive CLI onboarding
interview to confirm and refine understanding.

### 4. Monitor Loop

Collection cycle (every 5m): pull metrics → store → anomaly detect → trigger LLM if needed.
Discovery cycle (every 1h): re-discover → diff fingerprint → alert on drift.
Analysis (every 30m or on anomaly): build context → LLM analyze → generate insights → alert.

### 5. Alerting

Structured Insight objects with severity, evidence, correlation, suggested actions.
Delivered via Slack webhook (Phase 1), generic webhook, email (future).
Rate limiting and deduplication prevent alert storms.

### 6. Local Data Store

SQLite (v1), upgradeable to PostgreSQL. Tables: system_snapshots, metric_snapshots,
change_events, insights, alert_history, business_context, llm_usage, metric_baselines.

---

## Technology Stack

| Component | Technology | Rationale |
|-----------|-----------|-----------|
| Language | Python 3.11+ | Best LLM SDK ecosystem, async support |
| LLM Client | anthropic / openai SDK | BYO API key, abstracted behind LLMProvider |
| Database | aiosqlite (v1) / asyncpg (v2) | Minimal abstraction, full SQL control |
| HTTP Client | httpx | Async, connection pooling |
| Scheduling | APScheduler | Lightweight, in-process |
| CLI | typer + rich | Clean CLI with formatting |
| Config | YAML + env vars | Human-readable, secrets via environment |
| Anomaly Detection | scipy.stats | Z-score, IQR |

---

## Security Model

1. Read-only database credentials only (SELECT on information_schema, pg_stat_*, app tables)
2. API keys in environment variables, never in config files
3. LLM API keys user-provided, no phone-home
4. All data stays local, no telemetry or cloud dependency
5. Connector permissions explicitly declared and auditable
