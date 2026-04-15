# Observibot

**Autonomous AI SRE agent for indie developers and small teams on PaaS stacks.**

Observibot connects to your database and infrastructure, autonomously discovers
your application architecture, continuously monitors everything that matters,
and lets you interrogate your system through an intelligent chat interface — so
you can focus on building your product instead of configuring dashboards.

## What It Does

- **Self-discovers** your database schema, service topology, and infrastructure
  layout — no manual configuration required
- **Analyzes your source code** via GitHub to extract business logic, workflows,
  and domain-specific facts — then uses that knowledge when answering questions
- **Continuously monitors** business data, platform health, and infrastructure
  performance using MAD-based anomaly detection
- **Surfaces insights** through a three-zone web dashboard:
  - **Dynamic Discovery Feed** — real-time, LLM-generated findings with
    severity badges, confidence scores, and recurrence annotations
  - **Static Dashboard** — persistent, user-curated widgets promoted from
    the Discovery Feed or Chat
  - **System Intelligence Chat** — multi-turn conversational interface across
    three domains (observability, application data, infrastructure) with
    session memory and reference resolution
- **Agent Memory Inspector** — view, edit, deactivate, or delete what the
  agent has learned from your codebase. Changes take effect on the next query.
- **Correlates across layers** — links business anomalies to infrastructure
  causes using a two-call agentic LLM pipeline
- **Proactively alerts** via Slack, ntfy push notifications, or webhooks
- **Adapts automatically** when your system changes (new tables, new services,
  schema migrations, deploys)

## Who It's For

Anyone running production apps on PaaS stacks — Supabase + Railway, Neon +
Fly.io, PlanetScale + Render, and similar combinations. Solo developers, small
teams, and larger orgs that want autonomous, context-aware monitoring without
enterprise pricing or configuration overhead.

## How It Works

Observibot is a Python application that runs as a long-lived daemon. It uses
an LLM (Claude, GPT, or local models via Ollama) as its reasoning engine and
connects to your systems via read-only credentials. The web dashboard runs on
the same process at `localhost:8080`.

**Agentic Chat Pipeline:** The LLM first plans which tools to call (query
monitoring data, production database, or infrastructure platform), executes
queries through a 5-layer security sandbox, then interprets results into a
narrative answer with optional visualizations. Multi-turn sessions track
entities across exchanges so follow-up questions resolve naturally.

**5-Layer SQL Sandbox:** All production database queries go through: (1)
SELECT-only enforcement via AST parsing, (2) table allowlisting against
discovered schema, (3) LIMIT injection/enforcement, (4) EXPLAIN cost gating
to reject expensive queries before execution, and (5) statement_timeout as
the last line of defense. Schema-qualified names are validated to prevent
cross-schema access.

**Code Intelligence:** Observibot analyzes your GitHub repository to extract
semantic facts — business logic definitions, workflows, entity relationships,
and domain-specific rules. These facts are automatically injected into LLM
prompts so the agent understands your application's domain, not just its schema.

See [docs/architecture/](docs/architecture/) for full technical details.

## Quick Start

```bash
# Clone and install
git clone https://github.com/YOUR_USERNAME/Observibot.git
cd Observibot
pip install -e .

# Configure your connections
cp config/observibot.example.yaml config/observibot.yaml
# Edit with your credentials (see docs/QUICKSTART.md)

# Verify connectivity
observibot health

# Start monitoring + web dashboard
observibot run
# Dashboard at http://localhost:8080
```

See [docs/QUICKSTART.md](docs/QUICKSTART.md) for the full setup guide.

## Project Status

**Phase 4.5 — Experiential Memory** (in progress)

| Phase | Name | Status |
|-------|------|--------|
| 0 | Foundation & Discovery | ✅ Complete |
| 1 | Monitor Loop & Alerting | ✅ Complete |
| 2 | Hardening & Deployment | ✅ Complete |
| 3 | Web Dashboard & Agentic Chat | ✅ Complete |
| 4 | Deep Application Intelligence | ✅ Complete |
| 4.5 | Experiential Memory | 🟡 In Progress |
| 5 | Reporting & Analytics Maturity | Future |
| 6 | Generalization & Community | Future |

Phase 4.5 adds experiential memory — the ability to learn from accumulated
observations like a seasoned SRE. Steps completed: monitor run tracking,
insight feedback, events envelope, session memory with multi-turn resolution,
Agent Memory Inspector tab, and comprehensive pipeline quality improvements.
518 tests passing.

Each phase has explicit exit criteria. See
[docs/phases/ROADMAP.md](docs/phases/ROADMAP.md) for the full roadmap.

## Current Capabilities

- **Connectors:** Supabase (PostgreSQL pg_stat + Prometheus metrics), Railway
  (GraphQL topology + resource metrics), Generic PostgreSQL
- **Metrics:** 187+ per collection cycle (row counts, connection stats, cache
  hit ratios, CPU, memory, network, deploy events, and more)
- **Anomaly Detection:** MAD-based (Median Absolute Deviation) with sustained-
  interval escalation and configurable thresholds
- **Code Intelligence:** GitHub source code analysis extracts 1,000+ semantic
  facts (business logic, workflows, domain definitions) that enhance LLM context
- **Web Dashboard:** Three-zone layout with real-time insight polling, multi-turn
  agentic chat with session memory, and an Agent Memory Inspector for reviewing
  and editing learned knowledge
- **Events System:** Unified observation journal tracking anomalies, insights,
  deploys, drift, investigations, and feedback with full-text search
- **SQL Sandbox:** 5-layer security for LLM-generated queries (SELECT-only,
  table allowlist, LIMIT injection, EXPLAIN cost gating, statement_timeout)
- **Alert Channels:** ntfy.sh (push notifications), Slack, generic webhook
- **LLM Providers:** Anthropic (Claude), OpenAI, Mock (for testing)
- **Security:** Read-only credentials, schema-qualified table validation,
  sensitive column filtering, admin-gated knowledge mutations, JWT auth

## Documentation

- [QUICKSTART.md](docs/QUICKSTART.md) — 5-minute setup guide
- [CONFIGURATION.md](docs/CONFIGURATION.md) — All config options
- [DEPLOYMENT.md](docs/DEPLOYMENT.md) — Docker and production deployment
- [ROADMAP.md](docs/phases/ROADMAP.md) — Phased roadmap with exit criteria
- [BACKLOG.md](docs/BACKLOG.md) — Prioritized bugs and feature backlog
- [Architecture docs](docs/architecture/) — System design, connectors, data store
- [Testing Standards](docs/TESTING_STANDARDS.md) — Three-tier testing requirements

## Vision

Observibot is evolving from a single SRE agent into a platform for multiple
specialized agents that analyze the same system from different perspectives.
The SRE agent monitors performance and detects anomalies. Future agents —
security, cost optimization, compliance — will plug into the same ecosystem,
sharing connectors, data, and the web dashboard while bringing their own
domain expertise.

See the [architecture docs](docs/architecture/ARCHITECTURE.md) for details
on the agent ecosystem design.

## License

Apache 2.0
