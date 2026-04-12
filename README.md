# Observibot

**Autonomous AI SRE agent for indie developers and small teams on PaaS stacks.**

Observibot connects to your database and infrastructure, autonomously discovers
your application architecture, continuously monitors everything that matters,
and lets you interrogate your system through an intelligent chat interface — so
you can focus on building your product instead of configuring dashboards.

## What It Does

- **Self-discovers** your database schema, service topology, and infrastructure
  layout — no manual configuration required
- **Builds a semantic model** of your specific application — not generic
  metrics, but what matters for *your* product
- **Continuously monitors** business data, platform health, and infrastructure
  performance using MAD-based anomaly detection
- **Surfaces insights** through a three-zone web dashboard:
  - **Dynamic Discovery Feed** — real-time, LLM-generated findings about your
    system with severity badges and confidence scores
  - **Static Dashboard** — persistent, user-curated widgets promoted from
    the Discovery Feed or Chat
  - **System Intelligence Chat** — ask questions in natural language across
    three domains (observability, application data, infrastructure)
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

The agentic chat uses a **two-call pipeline**: the LLM first plans which tools
to call (query your monitoring data, your production database, or your
infrastructure platform), executes the queries through a sandboxed SQL layer,
then interprets the results into a narrative answer with optional
visualizations. All production database queries go through a 5-layer security
sandbox (AST parsing, SELECT-only enforcement, table allowlisting, row limits,
and EXPLAIN cost gating).

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

**Phase 3 — Web Dashboard & Agentic Chat** (just completed)

| Phase | Name | Status |
|-------|------|--------|
| 0 | Foundation & Discovery | ✅ Complete |
| 1 | Monitor Loop & Alerting | ✅ Complete |
| 2 | Hardening & Deployment | ✅ Complete |
| 3 | Web Dashboard & Agentic Chat | ✅ Complete |
| 4 | Deep Application Intelligence | Next |
| 5 | Reporting & Analytics Maturity | Future |
| 6 | Generalization & Community | Future |

Each phase has explicit exit criteria. See
[docs/phases/ROADMAP.md](docs/phases/ROADMAP.md) for the full roadmap.

## Current Capabilities

- **Connectors:** Supabase (PostgreSQL), Railway, Generic PostgreSQL
- **Metrics:** 62 per collection cycle (row counts, connection stats, cache
  hit ratios, dead tuple ratios, long-running queries, and more)
- **Anomaly Detection:** MAD-based (Median Absolute Deviation) with
  configurable thresholds and sustained-interval escalation
- **Web Dashboard:** Three-zone layout with real-time insight polling,
  multi-domain agentic chat, and pinnable widgets
- **Alert Channels:** ntfy.sh (push notifications), Slack, generic webhook
- **LLM Providers:** Anthropic (Claude), OpenAI, Mock (for testing)
- **Security:** Read-only credentials, sqlglot SQL sandbox, sensitive column
  filtering, JWT auth, httpOnly cookies

## Documentation

- [QUICKSTART.md](docs/QUICKSTART.md) — 5-minute setup guide
- [CONFIGURATION.md](docs/CONFIGURATION.md) — All config options
- [DEPLOYMENT.md](docs/DEPLOYMENT.md) — Docker and production deployment
- [ROADMAP.md](docs/phases/ROADMAP.md) — Phased roadmap with exit criteria
- [BACKLOG.md](docs/BACKLOG.md) — Prioritized bugs and feature backlog
- [Architecture docs](docs/architecture/) — System design, connectors, data store

## Vision

Observibot is evolving from a single SRE agent into a platform for multiple
specialized agents that analyze the same system from different perspectives.
The SRE agent monitors performance and detects anomalies. Future agents —
security, cost optimization, compliance — will plug into the same ecosystem,
sharing connectors, data, and the web dashboard while bringing their own
domain expertise. A security agent, for example, could trace auth flows
across source code, database permissions, API routes, and infrastructure
config to find cross-layer vulnerabilities invisible from any single layer.

See the [architecture docs](docs/architecture/ARCHITECTURE.md) for details
on the agent ecosystem design.

## License

Apache 2.0
