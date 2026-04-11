# Observibot

**Observibot — autonomous AI SRE agent for everyone on PaaS stacks.**

Observibot connects to your backend systems (databases, PaaS platforms, infrastructure), autonomously discovers your application architecture, and continuously monitors everything that matters — so you can focus on building your product instead of staring at dashboards.

## What It Does

- **Self-discovers** your database schema, service topology, and infrastructure layout
- **Builds a semantic model** of your specific application — not generic metrics, but what matters for *your* product
- **Continuously monitors** business data, platform health, and infrastructure performance
- **Correlates across layers** — links business anomalies to infrastructure causes
- **Proactively alerts** you with context-rich, actionable insights via Slack/email/webhook
- **Adapts automatically** when your system changes (new tables, new services, config changes)

## Who It's For

Anyone running production apps on PaaS stacks (Supabase + Railway, Neon + Fly.io, PlanetScale + Render, etc.) — solo developers, small teams, and larger orgs that want autonomous, context-aware monitoring without paying $50K+/year for enterprise observability tools.

## Architecture

Observibot is a Python application that runs as a long-lived process (locally, in Docker, or deployed alongside your stack). It uses an LLM (Claude, OpenAI, or local models) as its reasoning engine and connects to your systems via read-only credentials.

See [docs/architecture/](docs/architecture/) for full technical details.

## Quick Start

```bash
# Clone and install
git clone https://github.com/YOUR_USERNAME/Observibot.git
cd Observibot
pip install -e .

# Configure your connections
cp config/observibot.example.yaml config/observibot.yaml
# Edit config/observibot.yaml with your credentials

# Run discovery
observibot discover

# Start continuous monitoring
observibot run
```

## Project Status

**Phase 0 — Foundation & Discovery** (current)

See [docs/phases/](docs/phases/) for the full implementation roadmap.

## License

Apache 2.0
