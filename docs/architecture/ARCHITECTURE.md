# Observibot — System Architecture

> See [VISION.md](../VISION.md) for the project's north star. This
> document covers how the system is structured to serve that vision.

## Design Principles

These are invariants. Every architectural decision is checked against
these. If a principle and a proposed feature conflict, the principle
wins.

1. **Autonomous discovery.** Never require manual configuration to
   understand a system. If the platform can infer something from
   source code, schema, metrics, or topology, it infers it. Users
   correct mistakes through conversation, not by editing YAML.

2. **Semantic fidelity over raw coverage.** A table isn't just
   columns; it's meaning. The platform's value to agents scales with
   the quality of its interpretation, not the volume of raw data it
   surfaces.

3. **Read-only, always.** Observibot observes. It never writes to
   production systems. This is non-negotiable and enables every other
   use case.

4. **Local-first.** All collected data stays on the operator's
   infrastructure. No telemetry, no phone-home. A fully-offline
   deployment behind a corporate firewall works exactly the same as
   a public-cloud deployment.

5. **Connector-based extensibility.** Each external system is a
   connector module with a standard interface. Adding a new platform
   means writing one connector. Connectors are shared infrastructure,
   usable by any agent built on the platform.

6. **Agents are first-class citizens in three modes.** Core agents
   (shipped with the platform), community agents (contributed
   upstream), and private agents (built and run by a single team) are
   all architectural constraints on the core API, not side-use-cases.

7. **Ground truth is versioned and attributable.** Every semantic
   claim the platform makes can be traced (where did this come
   from?), corrected (this is wrong, here's the right answer), and
   versioned (what did the platform believe when this insight fired?).

8. **LLMs are reasoning engines, not databases.** LLMs interpret,
   correlate, and generate insights. All raw data stays in structured
   storage (SQLite / PostgreSQL) and all LLM output is validated
   against schemas before persistence.

---

## The System Model

The system model is the product's core. Every other component either
contributes to building it (connectors, discovery engine, semantic
modeler) or consumes it (agents, dashboard, chat). It represents a
running production system in a form agents can reason about.

What the model contains:

- **Structural metadata** — tables and their columns, relationships,
  services and their dependencies, metrics endpoints, deployment
  topology.
- **Semantic interpretation** — what a table holds ("orders"), how a
  column is used ("sensitive", "soft-delete flag", "enum with values
  X/Y/Z"), what a metric measures ("counter of completed requests",
  "gauge of live connections").
- **Runtime state** — current metric values, recent changes, time-
  aware baselines, anomalies detected.
- **Provenance** — where every claim came from (schema introspection,
  source code extraction, user correction), when it was last
  verified, and how confident we are in it.

The model is continuously updated as the system it describes evolves —
new tables appear, schemas migrate, services deploy, metrics shift.
Drift detection compares fingerprints across discovery cycles and
surfaces structural change as first-class events.

Agents consume the model through a stable interface. They don't query
the store directly and they don't call connectors directly. This
separation is what allows the connector layer and the agent layer to
evolve independently.

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
│  │ Supabase  │  │ Railway   │  │ GitHub    │  │  Future   │     │
│  │ Connector │  │ Connector │  │ Connector │  │ Connectors│     │
│  └─────┬─────┘  └─────┬─────┘  └─────┬─────┘  └─────┬─────┘     │
│        └──────────────┼──────────────┼──────────────┘           │
│                       ▼                                         │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │                   DISCOVERY ENGINE                      │    │
│  │  Schema Crawler → Relationship Mapper → Topology Builder│    │
│  │  Output: SystemModel (structured graph)                 │    │
│  └─────────────────────────┬───────────────────────────────┘    │
│                            ▼                                    │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │                   SEMANTIC MODELER                      │    │
│  │  Interprets raw SystemModel into meaning agents can use │    │
│  └─────────────────────────┬───────────────────────────────┘    │
│                            ▼                                    │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │                    AGENT LOOP(s)                        │    │
│  │  Per-agent: collect → detect → analyze → emit insights  │    │
│  │  Current: SRE agent (monitoring + diagnostics)          │    │
│  │  Planned: Security threat-modeling agent (Phase 7)      │    │
│  └─────────────────────────┬───────────────────────────────┘    │
│                            ▼                                    │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │              AGENTIC CHAT PIPELINE                      │    │
│  │  Two-call LLM: plan → execute → interpret               │    │
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
│  │  insights │ events │ semantic_facts │ widgets           │    │
│  │  llm_usage │ seasonal_baselines │ users │ sessions      │    │
│  └─────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────┘
```

---

## Component Details

### 1. Connectors

Each connector implements the `BaseConnector` interface with four
methods:
- `discover() -> SystemFragment` — crawl schema / topology / config
- `collect_metrics() -> MetricSnapshot` — collect current metric values
- `get_recent_changes(since) -> list[ChangeEvent]` — deploys, migrations
- `health_check() -> HealthStatus` — connectivity and permission verification

**Current connectors:** Supabase (PostgreSQL pg_stat + Prometheus
Metrics API), Railway (GraphQL topology + resource metrics), Generic
PostgreSQL, GitHub (source code).

**Planned connectors** are tracked in the roadmap's Phase 6 section
(connector ecosystem expansion) rather than listed here — the list
changes frequently enough that the roadmap is the canonical source.

See [CONNECTORS.md](CONNECTORS.md) for the full interface spec,
database role requirements, and platform-specific setup instructions.

### 2. Discovery Engine

Orchestrates connectors to build a unified SystemModel: tables,
relationships, services, metrics endpoints, topology. Computes a
SHA256 fingerprint of the structural portion for drift detection.
Runs on startup, periodically (hourly), and on-demand via
`observibot discover`.

### 3. Semantic Modeler

Interprets the raw SystemModel into meaning agents can use. App-type
identification from schema patterns, soft-delete detection, enum
value sampling, sensitive-column classification, relationship
semantics. Some interpretation is deterministic (pattern matching);
some is LLM-assisted (classification, naming). All semantic claims
are stored with provenance so they can be traced and corrected.

### 4. Agent Loop

Each agent runs a collect → detect → analyze → emit cycle on its own
schedule. The currently-shipped SRE agent:

1. Collects metrics from all connectors (every 5m)
2. Stores snapshots in the local time-series store
3. MAD-based anomaly detection with seasonal (hour-of-week) baselines
4. LLM analysis of anomalies + recent changes with structured
   diagnostic evidence
5. Routes insights to alerting channels and the Discovery Feed
6. Periodic re-discovery for drift detection

Future agents will register their own loops against the same
SystemModel. The `BaseAgent` ABC that formalizes this contract lands
in Phase 7 — see the roadmap.

### 5. Agentic Chat Pipeline

Two-call LLM pipeline: the first call plans which tools to invoke
(with generated SQL or infrastructure queries), the second call
interprets the results into a narrative answer with optional widget
configurations. Three domain tools: `query_observability` (internal
metrics and events), `query_application` (production DB via
sandboxed pool), `query_infrastructure` (platform services and
deploys). All SQL goes through a 5-layer sqlglot sandbox.

Chat is currently global (one chat tool surface). Phase 7 introduces
per-agent chat tool registration so security, cost, and other agents
expose their own tools into the same chat interface.

### 6. Web Dashboard

Three-zone layout served by FastAPI at `:8080`:

- **Dynamic Discovery Feed** (Zone 1) — real-time, ephemeral insights
  from all active agents with severity badges, confidence scores, and
  lifecycle actions. Future: agent-source filtering.
- **Static Dashboard** (Zone 2) — persistent, user-curated widgets
  promoted from the Discovery Feed or Chat.
- **System Intelligence Chat** (Zone 3) — natural-language interface
  across observability, application, and infrastructure domains.

### 7. Alerting

Structured Insight objects with severity, evidence, correlation, and
recommended actions. Delivered via ntfy.sh push notifications, Slack
webhook, or generic webhook. Alert aggregation prevents storms; rate
limiting and per-fingerprint cooldown prevent duplicates. Insight
fingerprints are derived from triggering anomaly signatures (stable
across LLM text variance).

### 8. Data Store

SQLAlchemy 2.x with dynamic engine selection (SQLite for dev/demo,
PostgreSQL for production). Alembic migrations. Retention scheduling
trims old data automatically. Core tables: `system_snapshots`,
`metric_snapshots`, `change_events`, `insights`, `events`,
`semantic_facts`, `seasonal_baselines`, `llm_usage`, `users`,
`widgets`, `sessions`, `alert_history`.

---

## Experiential Memory (Phase 4.5, In Progress)

A senior SRE builds institutional knowledge over months: which alerts
are noise, what patterns recur weekly, which deploys cause which
symptoms. Phase 4.5 gives the platform that capability — not as an
add-on but as a first-class layer that every agent can consume.

Three tiers:

- **Tier 1 (Observation Journal)** ✅ shipped — append-only event log
  via a lightweight `events` envelope table. Records what happened,
  when, and what the outcome was.
- **Tier 2 (Synthesized Knowledge)** 🟡 in progress — higher-order
  patterns distilled from accumulated observations via deterministic
  clustering plus LLM interpretation. Patterns carry machine-readable
  signatures, Bayesian confidence scores, and temporal metadata.
- **Tier 3 (Working Memory)** ✅ shipped — server-side session context
  for multi-turn chat.

Key architectural principles for memory:
- Memory and policy are separate records — a pattern is descriptive;
  a suppression rule is a policy requiring user confirmation.
- Deterministic pre-processing before any LLM synthesis.
- Seasonal MAD baselines (168 hour-of-week buckets) for time-aware
  anomaly detection.
- Advisory-only before any alert behavior changes — patterns surface
  as UI recommendations before they can modify alerting.
- Bespoke on SQLite/Postgres. No external memory frameworks.

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
| Anomaly Detection | MAD (custom) | Robust to outliers, no heavy dependency |
| Auth | python-jose + bcrypt | JWT in httpOnly cookies |
| Charts | Vega-Lite + vega-embed | Declarative, LLM-generatable specs |
| Config | YAML + env vars | Human-readable, secrets via environment |

---

## Security Model

1. **Read-only credentials only** — SELECT on information_schema,
   pg_stat_*, and application tables. The platform has no write path
   to production systems.
2. **SQL sandbox** — All LLM-generated SQL goes through sqlglot AST
   parsing: SELECT-only enforcement, table allowlisting, row limits,
   EXPLAIN cost gating, statement_timeout.
3. **Sensitive column filtering** — Columns matching patterns like
   `password`, `token`, `secret`, `api_key` are excluded from LLM
   prompts and redacted from query results.
4. **API keys in environment** — Never in config files, never logged.
5. **JWT auth** — httpOnly cookies, bcrypt password hashing.
6. **Local-first** — All data stays on the operator's infrastructure.
   No telemetry, no phone-home.

---

## The Agent Ecosystem

Observibot is a platform for specialized agents, not a single agent.
This is a present-tense architectural commitment, not a future
aspiration — every design decision we make is constrained by it.

### Agent categories

**Core agents** — shipped with the platform, maintained by the core
team. Active: the SRE agent (monitoring, anomaly detection,
diagnostic evidence, agentic chat). Planned: a security threat-
modeling agent in Phase 7.

**Community agents** — contributed upstream by external developers
and bundled into the platform after review. The contribution path is
intentionally not open yet; see the roadmap and
`docs/contributing/AGENTS.md` for the timeline.

**Private agents** — built by a single team and run only in their own
deployment. Never shared, never reviewed, not part of the public
repo. Use cases include encoding team-specific compliance rules,
proprietary operational knowledge, or domain-specific analysis the
team doesn't want to open-source.

All three categories must be first-class consumers of the platform.
They consume the same SystemModel, use the same store, emit the
same Insight shape, and register tools into the same chat interface.

### What's shared across agents

- **Connectors** — shared infrastructure. Agents never call connectors
  directly; they read from the SystemModel and the store.
- **SystemModel** — agent-agnostic representation of the monitored
  system. An agent that only looks at database schema and an agent
  that only looks at deployment topology both read the same model.
- **Store** — metrics, insights, events, semantic facts, baselines.
  Agents read what they need; writes go through typed interfaces.
- **Insight shape** — `severity`, `title`, `summary`, `related
  entities`, `source`, `evidence`. The `source` field identifies
  which agent produced the insight. The `evidence` field carries
  structured backing data specific to the insight type.
- **Web dashboard** — all agents' output flows through the same
  three-zone layout with source filtering (future).

### What each agent brings

- **Analysis loop** — collection/detection logic and schedule.
- **Chat tool set** — domain-specific tools registered into the
  System Intelligence Chat interface.
- **Prompt templates** — tuned to the agent's domain expertise.
- **Severity taxonomy** — appropriate to the domain. SRE uses
  info/warning/critical. Security will use CVSS-style scoring. Cost
  will use quantitative impact.

### Architectural constraints that protect the ecosystem

These decisions apply now, across every phase, to keep the three-mode
ecosystem possible:

1. `Insight.source` identifies which agent generated the finding —
   no agent produces insights anonymously.
2. Connectors are shared infrastructure, never coupled to a specific
   agent. An agent cannot declare "this connector belongs to me."
3. Chat tool registration is dynamic, not hard-coded. When Phase 7
   lands, agents register their own tools through a stable API.
4. The Discovery Feed supports filtering by agent source.
5. The `BaseAgent` ABC (to be designed in Phase 7) defines the
   contract: tools, analysis loop, prompts, severity taxonomy,
   lifecycle. External agents implement this contract without
   modifying core code.
6. The agent API is narrow and read-mostly. Agents consume the
   SystemModel, consume metrics, emit insights, register chat
   tools — that's it. Deep coupling to core internals is rejected.

### What's deliberately deferred

- **Agent distribution model** (process boundary vs plugin class vs
  config-only). This decision is deferred until we've built the
  second core agent. Building the second agent will surface the
  real constraints; deciding before then would be guessing.
- **Agent registry / marketplace infrastructure.** Candidate Phase 8+
  territory. Premature before the three-mode ecosystem is working
  with even a handful of real agents.
- **Cross-agent coordination protocols.** Agents are currently
  independent. If two agents emit conflicting insights about the
  same subject, the UI surfaces both. Coordination (one agent
  suppressing another) is not on the roadmap.
