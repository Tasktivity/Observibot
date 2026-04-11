# Observibot — Implementation Roadmap

## Phase 0: Foundation & Discovery (Days 1-5)
- Project skeleton, pyproject.toml, venv, dependencies
- BaseConnector ABC and all data models
- Supabase connector: discover() and health_check()
- Railway connector: discover() via GraphQL
- Discovery engine: merge fragments → SystemModel → fingerprint
- SQLite store with system_snapshots table
- CLI: init, discover, health, show-model
- Config loader with YAML + env var resolution

**Exit criteria:** `observibot discover` produces complete SystemModel from live backends.

## Phase 1: Monitor Loop & Alerting (Days 6-14)
- Metric collection for both connectors (every 5m)
- Statistical anomaly detection (z-score, rolling 24h baseline)
- Change detection (deploys, schema diffs)
- LLM integration (Claude API) with MockProvider for testing
- Semantic modeler with onboarding interview
- Slack webhook alerting with rate limiting
- Drift detection via periodic re-discovery
- CLI: run, status, ask, cost

**Exit criteria:** Continuous monitoring, anomaly detection, LLM insights, Slack alerts working.

## Phase 2: Hardening & Productization (Days 15-21)
- Dockerfile and docker-compose.yaml
- Multi-LLM support (Claude/OpenAI/Ollama)
- Metric retention and cleanup
- Error handling, retry logic, circuit breakers
- Documentation and CI (ruff, mypy, pytest)

**Exit criteria:** `docker-compose up` with `.env` → monitoring in 15 minutes.

## Phase 3: Web Dashboard & Chat (Days 22-35)
- FastAPI + HTMX web UI
- Three zones: Discovery Feed, Pinned Dashboards, Chat
- Text-to-SQL-to-chart via LLM
- Real-time updates via SSE

## Phase 4: Generalization & Community (Ongoing)
- Additional connectors (Neon, Fly.io, Render, Vercel, PlanetScale)
- Generic Prometheus/OpenTelemetry connectors
- Plugin system, multi-project support
- Optional hosted tier

## Dependencies (Phase 0-1)
anthropic, openai, httpx, asyncpg, aiosqlite, typer, rich,
pyyaml, apscheduler, scipy, python-dotenv.
Dev: pytest, pytest-asyncio, pytest-cov, ruff, mypy.
