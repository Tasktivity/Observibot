# Observibot — Implementation Roadmap

Each phase has explicit exit criteria that define "done." The deliverable
lists document what was built; the exit criteria document what must be true.

---

## Phase 0: Foundation & Discovery ✅ COMPLETE

### Deliverables
- Project skeleton, pyproject.toml, venv, dependencies
- BaseConnector ABC and all data models
- Supabase connector: discover() and health_check()
- Railway connector: discover() via GraphQL
- Discovery engine: merge fragments → SystemModel → fingerprint
- SQLite store with system_snapshots table
- CLI: init, discover, health, show-model
- Config loader with YAML + env var resolution

### Exit Criteria
1. At least one database connector and one infrastructure connector discover successfully
2. `observibot discover` produces a SystemModel with tables, relationships, and services
3. `observibot health` reports per-connector status with actionable error messages
4. Config loader resolves `${ENV_VAR}` placeholders and reports missing credentials clearly

---

## Phase 1: Monitor Loop & Alerting ✅ COMPLETE

### Deliverables
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

### Exit Criteria
1. `observibot run` collects metrics on schedule without crashing or leaking resources over 24+ hours
2. MAD-based anomaly detection fires on genuine anomalies without false positive storms on steady-state data
3. Alert channels deliver notifications to at least one configured destination
4. Alert aggregation prevents notification floods — burst incidents roll up into a single alert
5. LLM circuit breaker degrades gracefully to deterministic alerts when the provider is unavailable
6. Drift detection identifies schema or topology changes between discovery cycles

---

## Phase 2: Hardening & Deployment ✅ COMPLETE

### Deliverables
- Multi-stage Dockerfile + docker-compose (lite/production profiles)
- railway.toml for Railway deployment
- GitHub Actions CI (lint + test on Python 3.11/3.12 + Docker build)
- Health endpoint (FastAPI on :8080)
- User documentation: QUICKSTART.md, CONFIGURATION.md, DEPLOYMENT.md
- Git init + local commit (GitHub push pending user action)
- License: Apache-2.0 (consistent across LICENSE, pyproject.toml, README)

### Exit Criteria
1. `docker compose up` starts a working instance from a fresh clone with only `.env` configuration
2. CI passes on every commit (lint + tests on Python 3.11 and 3.12 + Docker build)
3. QUICKSTART.md gets a new user from zero to running in under 10 minutes (assuming credentials in hand)
4. CONFIGURATION.md documents every config key; DEPLOYMENT.md covers at least one production target
5. `observibot init` creates a valid config and reports which environment variables are set/missing

---

## Phase 3: Web Dashboard & Agentic Chat ✅ COMPLETE

**Completed:** April 12, 2026. All exit criteria met (chart refinement
deferred to Phase 5 per plan — system needs historical data accumulation).
Backend agentic pipeline, three-zone layout with independent scrolling,
insight lifecycle actions (Acknowledge/Pin/Promote/Investigate), production
DB queries via RLS-aware pool, relative timestamps, zone naming convention
applied. 190+ tests pass, ruff clean, frontend builds.

### Zone Naming Convention
- **Zone 1 — Dynamic Discovery Feed:** Real-time, ephemeral insights generated
  by the autonomous monitoring loop. Content is transient and manageable via
  insight lifecycle actions.
- **Zone 2 — Static Dashboard:** Persistent widgets promoted from the
  Discovery Feed or Chat. Content is stable and user-curated.
- **Zone 3 — System Intelligence Chat:** Agentic conversational interface for
  querying across all three domains (observability, application, infrastructure).

### Deliverables
- SQLAlchemy 2.x + Alembic migration (dynamic SQLite/Postgres engine)
- FastAPI REST API (18 endpoints: auth, metrics, insights, widgets, discovery, chat, system)
- JWT auth in httpOnly cookies, bcrypt, first-run registration
- React + TypeScript + Vite + Tailwind frontend (three-zone layout)
- Zone 1: Dynamic Discovery Feed with real-time polling, severity badges
- Zone 2: Static Dashboard with 6 widget types (KPI, time series, bar, table, status, text summary)
- Zone 3: System Intelligence Chat with multi-domain tool calling:
  - Tool 1: query_observability (8 internal tables)
  - Tool 2: query_application (production DB, opt-in, sandboxed)
  - Tool 3: query_infrastructure (Railway services/deploys from snapshots)
- Two-call LLM pipeline: plan→execute→interpret (narrative answers)
- sqlglot SQL sandbox (5-layer: AST parse, SELECT-only, table allowlist, LIMIT, EXPLAIN cost gating)
- AppDatabasePool for production DB queries (separate read-only pool)
- Sensitive column filtering (excluded from LLM prompts + post-execution redaction)
- Schema catalog built dynamically from discovery SystemModel
- Vega-Lite chart generation via vega-embed

### Exit Criteria
1. **Layout works as designed:** Three zones (Dynamic Discovery Feed, Static
   Dashboard, System Intelligence Chat) are simultaneously visible and scroll
   independently. No zone pushes another off-screen regardless of content volume.
2. **Chat produces correct answers for schema-answerable questions:** "How many
   users are there?" returns the actual count. The agent routes to the correct
   domain and the narrative is factual. (Questions requiring business logic
   understanding are Phase 4 territory.)
3. **Dynamic Discovery Feed has a usable lifecycle:** Each insight card offers
   four actions:
   - **Acknowledge** — removes from active view; retained in backend so the
     agent detects recurrence of the same condition
   - **Pin** — keeps the insight pinned to the top of the Discovery Feed
   - **Promote to Dashboard** — moves the insight into a persistent Static
     Dashboard widget
   - **Investigate** — pre-populates the System Intelligence Chat with the
     insight context and initiates a deeper analysis conversation
   Duplicate insights from consecutive cycles do not accumulate.
4. **Data is human-readable:** Numbers formatted (percentages, relative
   timestamps, Title Case headers). No raw database output visible.
5. **Charts render without errors:** Vega-Lite specs produce visible charts
   with no console errors. (Chart refinement and advanced visualizations
   deferred to Phase 5 — the system needs time to accumulate historical data.)
6. **Pin-to-dashboard works end-to-end:** Promoting from either the Discovery
   Feed or Chat creates a widget that renders with data, not an empty shell.
7. **No silent failures:** API errors surface as user-visible messages. Browser
   console is clean during normal operation.
8. **Auth works:** Login, logout, session persistence, first-run registration.

### Phase 3 Issues (All Resolved)
- ~~P0: Layout overflow~~ ✅ FIXED — `h-screen` on outer container
- ~~P0: User count returns 0~~ ✅ FIXED — Supabase RLS policy
- ~~P1: Discovery Feed lifecycle~~ ✅ FIXED — Acknowledge/Pin/Promote/Investigate
- ~~P1: Semantic understanding limited~~ → Moved to Phase 4
- ~~P1: Insight deduplication~~ ✅ FIXED — fingerprint excludes LLM text
- ~~P1: Number formatting~~ ✅ FIXED — relative timestamps, formatted values
- ~~P1: Pinned widget data~~ → Moved to Phase 5
- ~~P2: JavaScript exceptions~~ ✅ FIXED
- P1: Vega-Lite chart rendering → Moved to Phase 5
- P2: gridstack.js → Moved to Phase 5

---

## Phase 4: Deep Application Intelligence (Future)

The agent understands schema structure but not application business logic. This
phase makes the agent genuinely autonomous by giving it access to the source
code and the ability to correlate code changes with runtime behavior.

### Deliverables

#### GitHub Source Code Connector
- `BaseConnector` subclass via GitHub REST/GraphQL API
- Fine-grained Personal Access Token, read-only, scoped to specific repos
- Multi-stage semantic extraction pipeline: discover repo structure → identify
  high-signal files (models, schemas, routes, README, migrations) → LLM-powered
  semantic summary → store as business context
- Security: filter secrets/credentials from source, sensitive file exclusion
- `SourceCodeConnector` ABC for future platform support (Phase 6)
- Investigate best open-source options or combination of tools for accurate
  semantic understanding of codebases (architecture, components, logic flow,
  connectors, data models)

#### Automated Semantic Refresh
- Webhook or polling-based refresh on commit/merge to main branch
- Incremental re-analysis (only changed files, not full repo scan)
- Change events emitted for source code changes (same model as deploy events)
- History of semantic changes maintained for trend analysis

#### Runtime-to-Source Correlation
- Correlate source code changes with downstream performance impacts
- Agent uses Discovery Feed history and Static Dashboard baselines to detect
  performance changes (both positive and negative) following code changes
- Automatic investigation: when performance shifts, the agent checks recent
  commits/merges and surfaces both obvious and non-obvious observations
- Understanding of the runtime stack (what runs where, how components connect)
  so the agent can reason about blast radius of changes

#### Enhanced Self-Discovery
- Postgres column comments from `pg_description` injected into schema catalog
- Schema pattern inference (naming patterns → business semantics)
- Conversational corrections stored as business context, persisted across sessions

### Exit Criteria
1. **Source code semantic understanding:** Observibot connects to a GitHub repo,
   identifies high-signal files, and produces an accurate semantic model of the
   application: its components, logic flow, connectors, architecture, and data
   models. The System Intelligence Chat uses this context to answer questions
   that require business logic understanding (e.g., "what does onboarded mean?").
2. **Automated refresh:** Semantic model updates automatically on commit or merge
   (webhook preferred, polling fallback). The agent's understanding stays current
   without manual intervention.
3. **Change-to-performance correlation:** When the agent detects a performance
   change (positive or negative) via the monitoring loop, it automatically
   investigates recent source code changes and surfaces observations about
   potential causal relationships. The agent understands the runtime stack well
   enough to reason about non-obvious downstream impacts.
4. **Self-improving understanding:** The agent's knowledge of the application
   improves through at least two mechanisms: automated source code analysis and
   user-guided conversational correction. Corrections persist across sessions.

---

## Phase 5: Reporting & Analytics Maturity (Future)

By this phase the system has accumulated substantial historical data. This phase
focuses on making the output polished and the analytics genuinely useful.

### Deliverables
- Chart and visualization refinement (time series, categorical, heatmaps)
- Advanced reporting (trend analysis, comparative periods, SLA tracking)
- Business KPI promotion (key app health metrics collected as first-class metrics)
- Metric registry (display names, units, thresholds, healthy ranges, synonyms)
- Schema RAG for 500+ table schemas
- gridstack.js drag-and-drop dashboard layout

### Exit Criteria
1. **Charts are informative:** Time series, bar, and status charts render
   correctly with historical data. Charts include context (thresholds, healthy
   ranges, trend indicators) that make them actionable without explanation.
2. **Business KPIs answer from cache:** Core health questions (user count, error
   rates, latency) answer from Observibot's own store (fast, cached) rather than
   requiring live production DB queries.
3. **Dashboard is customizable:** Users can arrange, resize, and organize widgets
   via drag-and-drop.

---

## Phase 6: Generalization & Community (Future)

This is where Observibot becomes a product for the target audience of indie
developers, not just a tool for a single stack. All prior phases must maintain
flexibility for this transition — implementation decisions should not lock in
platform-specific assumptions.

### Deliverables
- Additional database connectors (Neon, Fly.io, Render, Vercel, PlanetScale)
- Generic Prometheus/OpenTelemetry connectors
- GitLab and Bitbucket source code connectors (via SourceCodeConnector ABC)
- Multi-project support with isolated discovery, metrics, and business context
- Plugin system via entry points
- Optional hosted tier / Railway template marketplace
- Agent abstraction layer: `BaseAgent` ABC defining the contract for
  specialized agents (tool set, analysis loop, prompts, severity taxonomy)
- Agent registry allowing multiple agents to run concurrently against the
  same system (e.g., SRE + Security + Cost agents sharing connectors and store)
- Discovery Feed agent filtering (view insights from all agents or one specific agent)
- Dynamic chat tool registration (agents register their own tools into the
  System Intelligence Chat rather than hard-coding tool definitions)

### Exit Criteria
1. **At least one additional database connector** beyond Supabase/PostgreSQL,
   proving the connector abstraction generalizes.
2. **At least one additional source code platform** beyond GitHub, proving the
   SourceCodeConnector ABC works.
3. **Multi-project support:** A single instance monitors multiple applications
   with isolated data and business context per project.
4. **Plugin system:** Third-party connectors can be installed without modifying
   core code.
5. **Agent abstraction:** At least one additional agent (e.g., Security Agent)
   runs alongside the SRE agent, contributing insights to the same Discovery
   Feed with agent-source filtering, and registering its own chat tools.

---

## Tech Stack
Backend: Python 3.12, FastAPI, SQLAlchemy 2.x + Alembic, asyncpg, aiosqlite, httpx, APScheduler, Anthropic/OpenAI SDKs, sqlglot, pydantic, scipy, numpy, deepdiff, python-jose, bcrypt
Frontend: React 18 + TypeScript, Vite, Tailwind CSS, ECharts (echarts-for-react), Vega-Lite + vega-embed, gridstack.js (installed)
Infra: Docker (multi-stage), Railway templates, GitHub Actions CI
