# Observibot

**A live model of your production system, kept continuously current, so
specialized agents can reason about it.**

Modern production systems span source code, databases, deployment
platforms, and cloud infrastructure. Understanding what a system *is* —
how it's structured, what the tables mean, how the services fit
together, how it changes over time — usually means a human stitching
information together across a dozen dashboards and a lot of code.

Observibot automates that stitching. It connects to each layer of your
system, builds a structured model of what it finds, and keeps that model
current as the system evolves. Specialized agents then reason about the
model through different lenses — monitoring, security, cost, and others
as the project grows.

The core product is the model and the layer that builds it. The agents
are consumers of it.

## What You Can Do With It Today

Observibot ships with its first agent: an autonomous SRE that monitors
your system for anomalies, correlates business metrics with
infrastructure events, and surfaces insights through a web dashboard
and chat interface.

- **Self-discovery.** Connects to your source code, database, and
  deployment platform, then builds a structured model of your
  application — schema, services, relationships, metrics, and
  change history — with no manual configuration.
- **Continuous monitoring.** Collects metrics across every connected
  system on a 5-minute cycle, detects anomalies with time-aware
  baselines, and correlates anomalies to recent changes.
- **Autonomous investigation.** When an anomaly fires, the agent
  generates diagnostic queries, runs them through a sandboxed SQL
  interface, and attaches the actual results as evidence on the
  insight — so you see confirmed findings, not speculation.
- **Agentic chat.** Ask questions about your system in natural
  language. The agent plans which tools to call, executes queries
  through a security sandbox, and synthesizes answers with optional
  visualizations. Multi-turn sessions track context naturally.
- **Learned knowledge.** Observibot extracts semantic facts from your
  source code — business logic, workflows, domain definitions — and
  uses them when answering questions. You can review, correct, or
  retract anything it has learned through an Agent Memory Inspector.
- **Alerting.** Routes insights to ntfy, Slack, or any webhook.

## Who It's For

**Today:** Indie developers and small teams running production apps on
PaaS stacks — Supabase + Railway, Neon + Fly, PlanetScale + Render, and
similar combinations. The SRE agent works well here because these
stacks are small enough to understand end-to-end, the target users are
underserved by enterprise tools, and they adopt new developer
infrastructure fast.

**Where we're going:** Any team running production software on any
major cloud, with source code on any major code host. Indie teams use
AWS and GCP too; small teams grow; mid-sized teams want more than
monitoring. The architecture is built so connector coverage and agent
capability can both expand without rearchitecting the core.

See [VISION.md](docs/VISION.md) for the full framing.

## How It's Built

Observibot has three layers, designed to be separable:

**Connectors.** Each external system — source code host, database,
deployment platform, cloud provider — has a connector implementing a
common interface. Connectors are shared infrastructure, usable by any
agent built on the platform. More connectors mean more of your
system is understood.

**System model.** A structured representation of your running system,
built from connector output and interpreted into meaning. Not raw
schema metadata but *semantics* — this table holds orders, this column
is sensitive, this metric is a counter, this service depends on that
one. Continuously updated as the system changes.

**Agents.** Specialized reasoners that consume the system model. Each
agent brings its own analysis logic, chat tools, and severity taxonomy,
but all share the same connectors, store, and dashboard. The first
agent is SRE. Others will follow.

## Principles

- **Read-only, always.** Observibot observes. It never writes to your
  production systems.
- **Local-first.** All collected data stays on your infrastructure.
  No telemetry, no phone-home.
- **Autonomous discovery.** No manual configuration files for business
  context. The platform learns through automated analysis and
  conversational corrections.
- **Semantic fidelity over raw coverage.** Better understanding beats
  more metrics.
- **Every platform, eventually.** Connectors are architected to
  generalize. We ship the ones that prove the pattern; the community
  will extend them.
- **Agents are first-class citizens in three modes.** Core agents
  (shipped), community agents (contributed), and private agents (built
  for a single team's use only) are all first-class consumers of the
  platform.

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

## Current State

**Connectors**

- Supabase (PostgreSQL pg_stat + Prometheus Metrics API)
- Railway (GraphQL topology + resource metrics)
- Generic PostgreSQL
- GitHub source code

**Agents**

- SRE agent (active): anomaly detection, change correlation, agentic
  chat, learned knowledge from source code and conversations.

**Security & trust**

- Read-only credentials required. Observibot has no write path to
  production.
- 5-layer SQL sandbox on every LLM-generated query: SELECT-only AST
  parsing, table allowlisting against the discovered schema, LIMIT
  enforcement, EXPLAIN cost gating, and statement_timeout.
- Sensitive columns (patterns like `password`, `token`, `secret`,
  `api_key`) are redacted from LLM prompts and from query results.
- API keys in environment variables only. Never in config files.
- JWT auth on the web UI. httpOnly cookies. bcrypt password hashing.

## Roadmap

Phase-by-phase roadmap with exit criteria:
[docs/phases/ROADMAP.md](docs/phases/ROADMAP.md). We're currently in
Phase 4.5, focused on experiential memory and diagnostic accuracy.

Open work items are tracked as [GitHub Issues](https://github.com/YOUR_USERNAME/Observibot/issues).

## Contributing

Observibot is open-core and designed to grow through community
contribution. The contribution surfaces are connectors and agents —
both are architected for extension.

- **[CONTRIBUTING.md](CONTRIBUTING.md)** — getting started as a
  contributor
- **[docs/contributing/CONNECTORS.md](docs/contributing/CONNECTORS.md)**
  — how connectors work, what they must honor, and when the core team
  is ready to accept community-contributed connectors
- **[docs/contributing/AGENTS.md](docs/contributing/AGENTS.md)** — how
  agents work, what they must honor, and the current status of the
  agent extension API

The connector and agent contribution guides are evolving. The core
team is shipping enough connectors and a second agent before opening
community contribution broadly — this is to make sure the patterns
are real before others build on them.

## Commercial Intent

Observibot is Apache 2.0, open-core. The full platform — every
connector, every agent, every API — lives in this repository. We
plan to offer managed hosting for teams who don't want to run it
themselves, which is how we intend to sustain long-term development.
The open-source tier is not a trial or a crippled version; it's the
same codebase we run in production.

## Documentation

- **[VISION.md](docs/VISION.md)** — the project's north star
- [QUICKSTART.md](docs/QUICKSTART.md) — 5-minute setup
- [CONFIGURATION.md](docs/CONFIGURATION.md) — all config options
- [DEPLOYMENT.md](docs/DEPLOYMENT.md) — Docker and production
- [ROADMAP.md](docs/phases/ROADMAP.md) — phased roadmap with exit
  criteria
- [Architecture](docs/architecture/) — system design, connectors,
  data store
- [TESTING_STANDARDS.md](docs/TESTING_STANDARDS.md) — three-tier
  testing requirements

## License

Apache 2.0
