# Observibot — Implementation Roadmap

## Phase 0: Foundation & Discovery ✅ COMPLETE
- Project skeleton, pyproject.toml, venv, dependencies
- BaseConnector ABC and all data models
- Supabase connector: discover() and health_check()
- Railway connector: discover() via GraphQL
- Discovery engine: merge fragments → SystemModel → fingerprint
- SQLite store with system_snapshots table
- CLI: init, discover, health, show-model
- Config loader with YAML + env var resolution

## Phase 1: Monitor Loop & Alerting ✅ COMPLETE
- Metric collection (62 metrics/cycle, 5-min intervals)
- MAD-based anomaly detection (replaced z-score per reviewer consensus)
- Change detection (deploys, schema diffs via DeepDiff)
- LLM integration (Anthropic/OpenAI/Mock) with Pydantic validation
- Semantic modeler (auto-identifies app type from schema)
- Alert channels: ntfy.sh, Slack, generic webhook
- Alert aggregation with rate limiting and cooldown
- Drift detection via periodic re-discovery
- CLI: run, status, ask, cost, analyze, test-alert
- Circuit breaker for LLM failures (soft/hard escalation)
- Lockfile single-writer enforcement

## Phase 2: Hardening & Deployment ✅ COMPLETE
- Multi-stage Dockerfile + docker-compose (lite/production profiles)
- railway.toml for Railway deployment
- GitHub Actions CI (lint + test on Python 3.11/3.12 + Docker build)
- Health endpoint (FastAPI on :8080)
- User documentation: QUICKSTART.md, CONFIGURATION.md, DEPLOYMENT.md
- Git init + local commit (GitHub push pending user action)
- License: Apache-2.0 (consistent across LICENSE, pyproject.toml, README)

## Phase 3: Web Dashboard & Agentic Chat ✅ STRUCTURE COMPLETE, 🔧 FIXES IN PROGRESS
- SQLAlchemy 2.x + Alembic migration (dynamic SQLite/Postgres engine)
- FastAPI REST API (18 endpoints: auth, metrics, insights, widgets, discovery, chat, system)
- JWT auth in httpOnly cookies, bcrypt, first-run registration
- React + TypeScript + Vite + Tailwind frontend (three-zone layout)
- Zone 1: Discovery Feed with real-time polling, severity badges, Pin button
- Zone 2: Dashboard with 6 widget types (KPI, time series, bar, table, status, text summary)
- Zone 3: Agentic Chat with multi-domain tool calling:
  - Tool 1: query_observability (8 internal tables)
  - Tool 2: query_application (production DB, opt-in, sandboxed)
  - Tool 3: query_infrastructure (Railway services/deploys from snapshots)
- Two-call LLM pipeline: plan→execute→interpret (narrative answers)
- sqlglot SQL sandbox (5-layer: AST parse, SELECT-only, table allowlist, LIMIT, EXPLAIN cost gating)
- AppDatabasePool for production DB queries (separate read-only pool)
- Sensitive column filtering (excluded from LLM prompts + post-execution redaction)
- Schema catalog built dynamically from discovery SystemModel
- Vega-Lite chart generation via vega-embed
- 188 tests passing, ruff clean, npm builds

### Phase 3 Known Issues (Active)
- **P0: User count returns 0** — `query_application` tool executes `SELECT COUNT(*) FROM public.users` but returns 0; suspected table name collision between Observibot's internal `users` auth table and production `public.users`
- **P0: Layout overflow bug** — Discovery Feed with many insights pushes Dashboard/Chat off-screen; zone wrappers need `h-full` constraint (fix applied to source, needs frontend rebuild)
- **P1: Vega-Lite chart rendering** — version mismatch warnings (spec v5, lib v6.4.2); "Infinite extent" errors on infrastructure status charts
- **P1: Insight deduplication** — near-identical insights generated every 5 minutes (same anomaly restated)
- **P1: Number formatting** — raw decimals (0.9995 not 99.95%), ISO timestamps not relative
- **P1: Pinned widget data** — widgets pin with title but body may not render data
- **P2: JavaScript exceptions** — "Unexpected end of JSON input" from API calls returning non-JSON
- **P2: gridstack.js** — installed but not wired; CSS grid used instead

## Phase 4: Generalization & Community (Future)
- Additional connectors (Neon, Fly.io, Render, Vercel, PlanetScale)
- Generic Prometheus/OpenTelemetry connectors
- Plugin system via entry points
- Multi-project support
- Optional hosted tier / Railway template marketplace
- Business KPI promotion (collect key app health metrics into Observibot's store)
- Schema RAG for 500+ table schemas

## Tech Stack
Backend: Python 3.12, FastAPI, SQLAlchemy 2.x + Alembic, asyncpg, aiosqlite, httpx, APScheduler, Anthropic/OpenAI SDKs, sqlglot, pydantic, scipy, numpy, deepdiff, python-jose, bcrypt
Frontend: React 18 + TypeScript, Vite, Tailwind CSS, ECharts (echarts-for-react), Vega-Lite + vega-embed, gridstack.js (installed)
Infra: Docker (multi-stage), Railway templates, GitHub Actions CI
