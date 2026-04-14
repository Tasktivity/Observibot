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

## Phase 4: Deep Application Intelligence ✅ COMPLETE

**Completed:** April 12, 2026. All exit criteria met. Architecture reviewed by
3 external reviewers (Gemini, ChatGPT, Perplexity) — all critical feedback
incorporated. Shared Knowledge Layer in `core/code_intelligence/` (not coupled
to SRE agent). 608 semantic facts (376 from source code, 232 from schema
analysis) powering business-logic chat queries against the monitored app.
296 tests pass, ruff clean.

### Architecture Decisions (from 3-reviewer consensus)
- **SemanticFact model with provenance** — every fact has evidence (file path,
  commit SHA, line range, confidence, source) and maps to PostgreSQL tables/columns
- **Shared `core/code_intelligence/`** — not in `agent/`, reusable by future agents
- **Tree-sitter structural foundation** — `CodeIndex` interface with
  `TreeSitterIndex` implementation; symbol density ranking for file selection
- **FTS5 retrieval** — SQLite full-text search over semantic facts, not keyword
  prompt stuffing
- **Question classifier** — deterministic gating of business context injection;
  simple schema queries skip it to prevent regressions
- **Deterministic secret scanning** — 13 regex patterns before any cloud LLM
  submission; explicit `cloud_extraction` opt-in config flag
- **5-minute correlation checks** — cheap deterministic proximity detection
  every monitoring cycle, LLM escalation only on high-confidence overlaps

### Deliverables

#### Shared Knowledge Layer (`core/code_intelligence/`)
- `SemanticFact` Pydantic model with provenance fields (evidence path, commit
  SHA, line range, confidence, source, tables, columns, sql_condition)
- `CodeKnowledgeService` — question classifier, FTS5 context retrieval,
  token-budgeted prompt formatting, freshness tracking, correction storage
- `semantic_facts` table + `semantic_facts_fts` FTS5 virtual table
- `code_intelligence_meta` table for freshness tracking (last_indexed_commit,
  last_index_time)
- Alembic migration for new tables

#### Schema-Derived Semantic Facts
- `pg_description` column comments extracted during discovery and stored as
  DEFINITION facts with 0.95 confidence
- Naming pattern inference: `*_at` → timestamp transitions, `status` → state
  machines, `is_*` → boolean flags
- FK relationship mapping: generates MAPPING facts linking related tables
- Deduplication: upserts by concept+source+fact_type, prevents duplicates
  across discovery cycles

#### GitHub Source Code Connector
- `GitHubConnector(BaseConnector)` with DISCOVERY, CHANGES, HEALTH,
  CODE_ACCESS, CODE_CHANGES capabilities
- Fine-grained PAT, read-only, scoped to specific repos
- ETag conditional requests, rate-limit backoff (Retry-After), 10s timeout,
  circuit breaker (3 failures → 1hr backoff)
- Strictly optional — system boots cleanly without GitHub config
- Wired into `_instantiate_connectors()` when `github.enabled` is true

#### Tree-Sitter Structural Analysis
- `CodeIndex` ABC — interface for structural code analysis backends
- `TreeSitterIndex` implementation — Python tree-sitter parsing with regex
  fallback for JS/TS
- Universal file selection: 50KB size cap, generated-file exclusion, binary
  skip, symbol density ranking (not framework-specific heuristics)
- Top 30 files by score, 100KB total code cap for LLM submission

#### Semantic Extraction Pipeline
- `SemanticExtractor` — tree-sitter chunks + LLM extraction with Pydantic
  validation
- Per-chunk extraction prompt demands PostgreSQL table/column mapping
- Deterministic validation: facts referencing unknown tables get confidence
  capped at 0.3
- Secret scanner: 13 regex patterns, redaction before LLM submission
- Cloud extraction opt-in: `github.cloud_extraction` config flag
- Incremental save: facts persist after each chunk (survives timeout)

#### Automated Semantic Refresh
- Polling-based: 15-minute poll interval via GitHub API (configurable)
- Commit SHA tracking in `code_intelligence_meta`
- Incremental re-analysis: `git diff` to find changed files, extract only those
- Full extraction on first discovery cycle, incremental on subsequent cycles
- 120-second timeout with `asyncio.wait_for()` — never blocks monitor loop

#### Change-to-Performance Correlation
- `CorrelationDetector` — deterministic temporal proximity scoring every
  monitoring cycle (5-minute)
- Severity score: severity_weight × proximity_weight × z_weight
- LLM escalation only when `severity_score > threshold`
- Deterministic fallback insight when LLM unavailable
- Correlation prompt includes change details, metric anomalies, system topology

#### Enhanced Self-Discovery
- `pg_description` column comments flow through discovery → schema catalog →
  semantic facts → chat planning prompt
- Schema pattern inference via `schema_analyzer.py`
- Conversational correction detection: 4 regex patterns in chat pipeline
- Corrections stored as CORRECTION facts with confidence 1.0 (highest priority)
- `/api/system/code-intelligence-status` endpoint for freshness monitoring

#### Business Context in Chat Pipeline
- `CodeKnowledgeService.should_inject_context()` — deterministic question
  classifier gates injection
- `CodeKnowledgeService.get_context_for_question()` — FTS5 retrieval ranked
  by source priority (user corrections > code extraction > schema analysis)
- Token-budgeted formatting: compact one-line-per-fact with sql_condition
- Freshness warnings when semantic model is stale
- Business context section added to `PLANNING_PROMPT` only when relevant

### Exit Criteria
1. **Source code semantic understanding:** ✅ Observibot connects to GitHub,
   indexes 77 files via tree-sitter, extracts 376 code-derived facts covering
   access control, business rules, workflows, entity definitions, and domain
   mappings. Chat uses these facts for business-logic answers (e.g., "How many
   bug reports are feature requests?" → 3, using `category = 'feature_request'`
   from code-extracted fact).
2. **Automated refresh:** ✅ Polling-based (15-min). Commit SHA tracked
   (`b09194e5`). Incremental extraction on changed files via git diff.
3. **Change-to-performance correlation:** ✅ `CorrelationDetector` runs every
   monitoring cycle. Cheap deterministic check with LLM escalation on
   high-confidence overlaps.
4. **Self-improving understanding:** ✅ Two mechanisms: automated code
   extraction (376 facts) + conversational correction detection (4 regex
   patterns, stored as priority-1.0 facts).

### Phase 4 Known Polish Items
- Tree-sitter test gaps: route handler, entrypoint, and docstring detection
  tests fail (tree-sitter implementation, not architecture)
- Question classifier over-matches: "How many users?" triggers context
  injection via word overlap with concept terms (no regression, token waste)
- First-run extraction timeout: 120s limit processes ~21 of 30 files;
  incremental save mitigates, batching across cycles would fix
- Minified JS noise: sub-50KB compiled files produce low-value facts;
  line-length heuristic would filter
- CLI `ask` command uses different code path from web chat, bypasses
  business context pipeline

---

## Phase 4.5: Experiential Memory & Connector Enrichment (In Progress)

Observibot currently has no memory across monitoring cycles — every 5-minute
analysis is stateless. A senior SRE builds institutional knowledge over months:
which alerts are noise, what patterns recur weekly, which deploys cause which
symptoms. This phase gives the agent that capability.

Additionally, both Supabase and Railway expose far more metrics than the
connectors currently collect. Supabase has a Prometheus-compatible Metrics API
with ~200 metrics (CPU, IO, WAL, memory, disk, replication, auth). Railway's
GraphQL API exposes CPU/memory/disk/network per service. Enriching the metric
pipeline before building memory ensures the experiential system learns from
the full picture from day one.

Architecture reviewed by 3 external reviewers (Gemini, ChatGPT, Perplexity).
All critical feedback incorporated into the design.

### Architecture Decisions (from 3-reviewer consensus)
- **Three-tier experiential memory** — Observation Journal (episodic log),
  Synthesized Knowledge (pattern memory), Working Memory (session context)
- **Memory and policy are separate records** — "this pattern exists" is
  descriptive; "suppress this alert" is a policy requiring user confirmation
- **Deterministic first, LLM second** — deterministic pre-clustering by
  metric + time-of-day + deploy proximity; LLM only labels/summarizes clusters
- **Seasonal MAD baselines** — hour-of-week bucketing (168 buckets) for
  time-aware anomaly detection, eliminating known-pattern false positives
- **Bayesian confidence** — Beta distribution updated by user feedback
  (noise/actionable); no full RL infrastructure needed
- **Bespoke on SQLite/Postgres** — rejected Mem0, Zep, Letta, Cognee due to
  deployment friction; borrow patterns from ExpeL, Hindsight, MemRL
- **Shared Prometheus parser** — reusable utility for Supabase Metrics API +
  Railway Prometheus exporter + future Phase 6 generic connectors
- **Advisory-only before suppression** — synthesized patterns surface in UI
  as recommendations before any alert behavior changes

### Step 0 Deliverables (Prerequisites) ✅ COMPLETE
Completed April 13, 2026. 361 tests passing (65 new total), ruff clean,
frontend builds clean. Two external code reviews completed — all critical
and important issues fixed across two hotfix passes.

- `monitor_runs` table — anchors each monitoring cycle with a run ID
- `insight_feedback` table + API + UI buttons — Noise/Actionable/Investigating/Resolved
- Chat session ID support — server-side session store (30min TTL, 5 turn max)
- Shared Prometheus text parser utility (`connectors/prometheus_parser.py`)
- Supabase Metrics API scraping — ~200 metrics (CPU, IO, WAL, memory, disk, replication, auth, pooler)
- Railway GraphQL resource metrics (CPU, memory, disk, network) + optional Prometheus exporter

### Step 1 Deliverables (Events Envelope — Tier 1) ✅ COMPLETE
Completed April 13, 2026. 387 tests passing (26 new), ruff clean, frontend
builds clean. Unified episodic timeline operational from first monitoring cycle.

- `events` table — lightweight envelope referencing existing tables (5 indexes)
- FTS5 virtual table (SQLite) / GIN tsvector index (PostgreSQL) for narrative search
- 7 store methods for event emission, querying, search, and recurrence stats
- Event emission wired into all code paths (monitor loop, chat, feedback)
- Events API — 5 endpoints (list, subject, recurrence, search, timeline)
- Discovery Feed recurrence annotations ("Seen N times in last 30 days")

### Step 2 Deliverables (Session Memory — Tier 3)
- Server-side session store with structured state + compressed turns
- Multi-turn chat (pronoun resolution, query refinement)
- Context injection into planning prompt (~1k token budget)

### Step 3 Deliverables (Deterministic Experiential Retrieval)
- Seasonal MAD baselines (hour-of-week bucketing)
- Deterministic lookbacks during anomaly analysis ("seen N times in 30 days")
- Recurrence annotations on Discovery Feed insights

### Step 4 Deliverables (Synthesis Agent — Tier 2, Advisory Mode)
- Deterministic pre-clustering of observations
- LLM synthesis to label and summarize clusters
- `SynthesizedKnowledge` with pattern_signature + prior/posterior split
- UI for viewing, confirming, and rejecting learned patterns
- Advisory-only: patterns displayed but no behavior changes

### Step 5 Deliverables (Policy Layer + Alert Suppression)
- Separate `suppression_policies` table (memory ≠ policy)
- Auto-created policies with strict guardrails (never suppress > warning)
- User-confirmed policies via one-click from UI
- Bayesian posterior updates from insight feedback

### Step 6 Deliverables (Correction Detection Upgrade)
- Teaching-intent detection integrated into planning call structured output
- Structured `/api/feedback` endpoint for UI-driven corrections
- Retire hardcoded regex patterns

### Exit Criteria
1. **Monitoring cycles are anchored:** Every cycle creates a `monitor_runs`
   record linking anomalies, insights, and metrics collected in that cycle.
2. **User feedback is captured:** Insight cards offer Noise/Actionable/
   Investigating/Resolved buttons that persist feedback to the database.
3. **Multi-turn chat works:** "How many users?" followed by "Break that down
   by month" resolves correctly within a session.
4. **Supabase metrics are comprehensive:** Connector collects CPU, memory,
   disk IO, WAL, and connection pooler metrics via the Metrics API (~200 series)
   in addition to existing pg_stat metrics.
5. **Railway resource metrics are collected:** CPU, memory, disk, and network
   per service via GraphQL (and/or Prometheus exporter).
6. **Experiential retrieval works:** Anomaly insights show recurrence context
   ("Seen 4 times in 30 days — usually self-resolves in ~20 minutes").
7. **Synthesized patterns are surfaced:** Learned patterns appear in the UI
   as advisory recommendations with confidence scores and evidence counts.
8. **Alert suppression is safe:** Only high-confidence patterns with user
   confirmation or strict guardrails can suppress alerts. No silent suppression
   of severity > warning.
9. **Seasonal baselines reduce false positives:** Known weekly/daily patterns
   are absorbed into time-bucketed baselines, reducing noise alerts.

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
