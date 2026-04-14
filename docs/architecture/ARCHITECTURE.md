# Observibot — System Architecture

## Design Philosophy

Observibot is built on four principles:

1. **Read-only everywhere.** Observibot never writes to your production
   systems. It connects with read-only credentials and observes.

2. **LLM as reasoning engine, not as database.** The LLM interprets,
   correlates, and generates insights. All raw data stays in structured
   storage (SQLite/PostgreSQL).

3. **Connector-based extensibility.** Each external system is a connector
   module with a standard interface. Adding a new platform means writing
   one connector.

4. **Local-first data storage.** All collected data, insights, and
   configuration stays on your infrastructure. No telemetry, no cloud
   dependency.

---

## High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                     WEB DASHBOARD (:8080)                       │
│  ┌──────────────┐  ┌──────────────┐  ┌────────────────────┐     │
│  │   Dynamic    │  │    Static    │  │ System Intelligence│     │
│  │  Discovery   │  │  Dashboard   │  │       Chat         │     │
│  │    Feed      │  │              │  │  (3-domain agentic)│     │
│  └──────┬───────┘  └──────┬───────┘  └────────┬───────────┘     │
│         └─────────────────┼───────────────────┘                 │
│                           ▼                                     │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │              FastAPI REST API (18 endpoints)            │    │
│  │  Auth │ Metrics │ Insights │ Widgets │ Chat │ System    │    │
│  └─────────────────────────┬───────────────────────────────┘    │
├────────────────────────────┼────────────────────────────────────┤
│                    OBSERVIBOT CORE                              │
│  ┌───────────┐  ┌───────────┐  ┌───────────┐  ┌───────────┐     │
│  │ Supabase  │  │ Railway   │  │ Generic   │  │ Future    │     │
│  │ Connector │  │ Connector │  │ PG Conn.  │  │ Connectors│     │
│  └─────┬─────┘  └─────┬─────┘  └─────┬─────┘  └─────┬─────┘     │
│        └──────────────┼──────────────┼──────────────┘           │
│                       ▼                                         │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │                   DISCOVERY ENGINE                      │    │
│  │  Schema Crawler → Relationship Mapper → Topology Builder│    │
│  │  Output: SystemModel (JSON graph)                       │    │
│  └─────────────────────────┬───────────────────────────────┘    │
│                            ▼                                    │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │                   SEMANTIC MODELER                      │    │
│  │  LLM interprets raw SystemModel into business context   │    │
│  └─────────────────────────┬───────────────────────────────┘    │
│                            ▼                                    │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │                    MONITOR LOOP                         │    │
│  │  1. Collect metrics from all connectors (every 5m)      │    │
│  │  2. Store in local time-series store                    │    │
│  │  3. MAD-based anomaly detection                         │    │
│  │  4. LLM analysis of anomalies + recent changes          │    │
│  │  5. Route insights to alerting + Discovery Feed         │    │
│  │  6. Periodic re-discovery for drift detection (hourly)  │    │
│  └─────────────────────────┬───────────────────────────────┘    │
│                            ▼                                    │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │              AGENTIC CHAT PIPELINE                      │    │
│  │  Two-call LLM pipeline: plan → execute → interpret      │    │
│  │  Tools: observability │ application (sandboxed) │ infra │    │
│  │  sqlglot SQL sandbox │ Sensitive column filtering       │    │
│  └─────────────────────────┬───────────────────────────────┘    │
│                            ▼                                    │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │                   ALERTING / OUTPUT                     │    │
│  │  ntfy.sh push │ Slack webhook │ Generic webhook │ CLI   │    │
│  └─────────────────────────────────────────────────────────┘    │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │              DATA STORE (SQLite / PostgreSQL)           │    │
│  │  system_snapshots │ metric_snapshots │ change_events    │    │
│  │  insights │ alert_history │ business_context            │    │
│  │  llm_usage │ metric_baselines │ users │ widgets         │    │
│  └─────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────┘
```

---

## Component Details

### 1. Connectors

Each connector implements the `BaseConnector` interface with four methods:
- `discover() -> SystemFragment` — crawl schema/topology/config
- `collect_metrics() -> MetricSnapshot` — collect current metric values
- `get_recent_changes(since) -> list[ChangeEvent]` — deployments, migrations
- `health_check() -> HealthStatus` — connectivity and permission verification

Current connectors: Supabase (PostgreSQL), Railway (GraphQL), Generic PostgreSQL.
Future (Phase 4): GitHub source code connector. Future (Phase 6): Neon, Fly.io,
Render, Vercel, PlanetScale, Prometheus, OpenTelemetry.

See [CONNECTORS.md](CONNECTORS.md) for full details and permission requirements.

### 2. Discovery Engine

Orchestrates connectors to build a unified SystemModel containing tables,
relationships, services, metrics endpoints, and topology. Computes SHA256
fingerprint for drift detection. Runs on startup, periodically (hourly),
and on-demand.

### 3. Semantic Modeler

LLM interprets raw SystemModel into business context: app type, core entities,
critical metrics, cross-layer correlation rules. Auto-identifies application
type from schema patterns without user intervention.

### 4. Monitor Loop

Collection cycle (every 5m): pull metrics → store → anomaly detect → trigger
LLM if needed. Discovery cycle (every 1h): re-discover → diff fingerprint →
alert on drift. Analysis (every 30m or on anomaly): build context → LLM
analyze → generate insights → alert. Uses MAD-based anomaly detection with
configurable thresholds and sustained-interval escalation.

### 5. Agentic Chat Pipeline

Two-call LLM pipeline: the first call plans which tools to invoke (with
generated SQL or infrastructure queries), the second call interprets the
results into a narrative answer with optional widget configurations. Three
domain tools: query_observability (internal metrics), query_application
(production DB via sandboxed pool), query_infrastructure (Railway services
and deploys). All SQL goes through a 5-layer sqlglot sandbox.

### 6. Web Dashboard

Three-zone layout served by FastAPI at `:8080`:
- **Dynamic Discovery Feed** (Zone 1): Real-time, ephemeral insights from the
  monitoring loop with severity badges, confidence scores, and lifecycle
  actions (Acknowledge, Pin, Promote to Dashboard, Investigate).
- **Static Dashboard** (Zone 2): Persistent, user-curated widgets promoted
  from the Discovery Feed or Chat. Six widget types: KPI, time series,
  categorical bar, table, status, and text summary.
- **System Intelligence Chat** (Zone 3): Natural language interface for
  querying across all three domains with domain badges and inline widgets.

### 7. Alerting

Structured Insight objects with severity, evidence, correlation, and
recommended actions. Delivered via ntfy.sh (push notifications), Slack
webhook, or generic webhook. Alert aggregation prevents storms; rate
limiting and per-fingerprint cooldown prevent duplicates.

### 8. Data Store

SQLAlchemy 2.x with dynamic engine selection (SQLite for dev/demo, PostgreSQL
for production). Alembic migrations. Retention scheduling trims old data
automatically. Tables: system_snapshots, metric_snapshots, change_events,
insights, alert_history, business_context, llm_usage, metric_baselines,
users, widgets.

---

## Planned: Experiential Memory (Phase 4.5)

The agent currently has no memory across monitoring cycles — each 5-minute
analysis is stateless. Phase 4.5 introduces three-tier experiential memory:

- **Tier 1 (Observation Journal):** Append-only event log via a lightweight
  `events` envelope table referencing existing tables. Records what happened,
  when, and what the outcome was.
- **Tier 2 (Synthesized Knowledge):** Higher-order patterns distilled from
  accumulated observations via deterministic clustering + LLM interpretation.
  Patterns have machine-readable signatures, Bayesian confidence scores, and
  temporal metadata.
- **Tier 3 (Working Memory):** Server-side session context for multi-turn chat.
  Structured state + compressed turns, ~1k token budget.

Key architectural principles:
- Memory and policy are separate records (pattern ≠ suppression rule)
- Deterministic pre-processing before any LLM synthesis
- Seasonal MAD baselines (168 hour-of-week buckets) for time-aware anomaly detection
- Advisory-only mode before any alert behavior changes
- Bespoke on SQLite/Postgres — no external memory frameworks

See `docs/PHASE45_DECISIONS.md` for full architecture decisions.

---

## Technology Stack

| Component | Technology | Rationale |
|-----------|-----------|-----------|
| Language | Python 3.12 | Best LLM SDK ecosystem, async support |
| Web Framework | FastAPI | Async, OpenAPI, lightweight |
| Frontend | React 18 + TypeScript + Vite + Tailwind | Modern, fast builds, type safety |
| ORM | SQLAlchemy 2.x + Alembic | Dynamic SQLite/Postgres engine, migrations |
| LLM Client | anthropic / openai SDK | BYO API key, abstracted behind LLMProvider |
| Database | aiosqlite (dev) / asyncpg (prod) | SQLite for simplicity, Postgres for production |
| SQL Sandbox | sqlglot | AST-based query validation and rewriting |
| HTTP Client | httpx | Async, connection pooling |
| Scheduling | APScheduler | Lightweight, in-process |
| CLI | typer + rich | Clean CLI with formatting |
| Anomaly Detection | MAD (custom) | No scipy dependency, robust to outliers |
| Auth | python-jose + bcrypt | JWT in httpOnly cookies |
| Charts | Vega-Lite + vega-embed | Declarative, LLM-generatable specs |
| Config | YAML + env vars | Human-readable, secrets via environment |

---

## Security Model

1. **Read-only credentials only** — SELECT on information_schema, pg_stat_*,
   and application tables. Never writes to production.
2. **SQL sandbox** — All LLM-generated SQL goes through sqlglot AST parsing:
   SELECT-only enforcement, table allowlisting, row limits, EXPLAIN cost gating.
3. **Sensitive column filtering** — Columns matching patterns like `password`,
   `token`, `secret`, `api_key` are excluded from LLM prompts and redacted
   from query results.
4. **API keys in environment** — Never in config files, never logged.
5. **JWT auth** — httpOnly cookies, bcrypt password hashing, session management.
6. **Local-first** — All data stays on your infrastructure. No telemetry,
   no cloud dependency, no phone-home.

---

## Future: Agent Ecosystem

Observibot is designed to evolve beyond a single SRE agent into a platform
that hosts multiple specialized agents, each analyzing the same system from
a different perspective. For example:

- **SRE Agent** (current) — monitors performance, detects anomalies, correlates
  business metrics with infrastructure events
- **Security Agent** (future) — traces auth flows across code, database
  permissions, API routes, and infrastructure config to find cross-layer
  vulnerabilities that aren't visible from any single layer
- **Cost Agent** (future) — tracks resource utilization, identifies waste,
  and correlates spending with business value

### What's shared across agents
All agents consume the same foundational infrastructure:
- **Connectors** provide raw data (schema, topology, metrics, source code)
- **SystemModel** describes the monitored system in an agent-agnostic way
- **Store** holds metrics, insights, and business context accessible to all agents
- **Insight model** is generic (severity, title, summary, related entities, source)
- **Web dashboard** presents all agents' outputs in the same three-zone layout

### What's agent-specific
Each agent brings its own:
- **Analysis loop** with its own schedule and detection logic
- **Tool set** for the System Intelligence Chat (e.g., security tools differ
  from SRE tools)
- **Prompt templates** tuned to the agent's domain expertise
- **Severity taxonomy** appropriate to the domain (vulnerability vs. anomaly)

### Architecture principles for multi-agent support
These decisions apply now to keep the door open:
1. The Insight `source` field must identify which agent generated the finding
2. Connectors (including the GitHub source code connector) are shared
   infrastructure, not coupled to any single agent
3. Chat tool registration should be dynamic, not hard-coded in prompt strings
4. The Discovery Feed should support filtering by agent source
5. The `BaseAgent` ABC (to be designed) will define the contract: tools,
   analysis loop, prompts, and severity taxonomy
