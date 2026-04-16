# Observibot — Implementation Roadmap

> **How this roadmap relates to the vision.** Observibot is building a
> platform: a layer that turns running production systems into live,
> structured models specialized agents can reason about. See
> [VISION.md](../VISION.md) for the full framing. This roadmap documents
> the path we're taking to build that platform — starting with the first
> agent (SRE) against a narrow set of connectors (GitHub, Supabase,
> Railway), then generalizing outward on both axes.
>
> **Two things to keep in mind when reading it:**
>
> 1. The early phases use a narrow stack (indie/PaaS) as a *validation
>    wedge*, not as a product scope. The architecture is built so
>    connector and agent surfaces can grow without rearchitecting.
>
> 2. "Phase 6" and later — the phases titled "Ecosystem Expansion" and
>    "Second Agent" — are not "future work we'll get to eventually."
>    They're the phases where the platform thesis actually plays out.
>    Everything before them is foundation.
>
> Each phase has explicit exit criteria that define "done." The
> deliverable lists document what was built; the exit criteria document
> what must be true.

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
- Zone 3: System Intelligence Chat with multi-domain tool calling
- Two-call LLM pipeline: plan→execute→interpret (narrative answers)
- sqlglot SQL sandbox (5-layer: AST parse, SELECT-only, table allowlist, LIMIT, EXPLAIN cost gating)
- AppDatabasePool for production DB queries (separate read-only pool)
- Sensitive column filtering (excluded from LLM prompts + post-execution redaction)
- Schema catalog built dynamically from discovery SystemModel
- Vega-Lite chart generation via vega-embed

### Exit Criteria
1. **Layout works as designed:** Three zones simultaneously visible, scroll
   independently.
2. **Chat produces correct answers for schema-answerable questions.**
3. **Dynamic Discovery Feed has a usable lifecycle** (Acknowledge, Pin,
   Promote, Investigate). Duplicates do not accumulate.
4. **Data is human-readable.** Numbers formatted, relative timestamps.
5. **Charts render without errors.**
6. **Pin-to-dashboard works end-to-end.**
7. **No silent failures.**
8. **Auth works.**

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
- **FTS5 retrieval** — SQLite full-text search over semantic facts
- **Question classifier** — deterministic gating of business context injection
- **Deterministic secret scanning** — 13 regex patterns before any cloud LLM
  submission
- **5-minute correlation checks** — cheap deterministic proximity detection
  every monitoring cycle, LLM escalation only on high-confidence overlaps

### Exit Criteria
1. **Source code semantic understanding** ✅
2. **Automated refresh** ✅
3. **Change-to-performance correlation** ✅
4. **Self-improving understanding** ✅ (code extraction + conversational
   correction)

---

## Phase 4.5: Experiential Memory & Foundational Work (In Progress)

Observibot currently has no memory across monitoring cycles — every 5-minute
analysis is stateless. A senior SRE builds institutional knowledge over
months: which alerts are noise, what patterns recur weekly, which deploys
cause which symptoms. This phase gives the agent that capability.

This phase also contains the **foundational work that unlocks Phase 5's
architecture view**. Multi-repo awareness and typed entity IDs on insights
are landing here rather than being retrofitted later, because changing the
shape of semantic facts and insights after they've accumulated is painful.

Architecture reviewed by 3 external reviewers (Gemini, ChatGPT, Perplexity).

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
- **Bespoke on SQLite/Postgres** — rejected Mem0, Zep, Letta, Cognee due to
  deployment friction; borrow patterns from ExpeL, Hindsight, MemRL
- **Shared Prometheus parser** — reusable utility for Supabase Metrics API +
  Railway Prometheus exporter + future generic connectors
- **Advisory-only before suppression** — synthesized patterns surface in UI
  as recommendations before any alert behavior changes

### Step Summaries

**Step 0 (Prerequisites) ✅ COMPLETE** — April 13, 2026. monitor_runs
anchoring, insight feedback buttons, chat session support, shared Prometheus
parser, Supabase/Railway metrics enrichment.

**Step 1 (Events Envelope) ✅ COMPLETE** — April 13, 2026. Unified episodic
timeline. Events table, FTS5 narrative search, 5 API endpoints, Discovery
Feed recurrence annotations.

**Step 2 (Session Memory) ✅ COMPLETE** — April 14, 2026. EXPLAIN cost
gating, multi-turn resolution, Agent Memory Inspector tab with 6 API
endpoints, contract tests for Railway/Supabase.

**Step 3 (Deterministic Experiential Retrieval) ✅ COMPLETE** — Seasonal MAD
baselines with hour-of-week bucketing, recurrence annotations on insights.

**Step 3.1 (Insight Persistence Fix) ✅ COMPLETE** — Recurrence context now
persisted correctly on live pipeline insights.

**Step 3.2 (Generic Detector/Insight Bugs) ✅ COMPLETE** — MAD=0 relative
floor, stable anomaly_signature, direction-aware prompt.

**Step 3.3 (Shared Evidence Infrastructure) ✅ COMPLETE** — EvidenceBundle
dataclass, Insight.evidence field, DiagnosticsConfig stub, prompt_utils
extraction, synthetic schema fixtures (first Tier 0 compliance).

**Step 3.4 (Hypothesis-Test Loop)** — In progress. Autonomous diagnostic
SQL against the application DB with fail-closed EXPLAIN, cooldown cache,
diagnostic evidence rendered in the UI.

**Step 4 (Synthesis Agent — Advisory Mode)** — Deterministic pre-clustering
of observations, LLM synthesis to label clusters, SynthesizedKnowledge with
pattern_signature and Bayesian confidence, UI for viewing/confirming/
rejecting learned patterns.

**Step 5 (Policy Layer + Alert Suppression)** — Separate
suppression_policies table, user-confirmed policies via one-click from UI,
strict guardrails (never suppress > warning).

**Step 6 (Correction Detection Upgrade)** — Teaching-intent detection in
planning structured output, structured /api/feedback endpoint, retire
hardcoded regex patterns.

**Step 7 (Architecture View Foundations)** — Low-risk data additions that
unlock the Phase 5 architecture view without committing to its UI yet.
These land in Phase 4.5 rather than Phase 5 because retrofitting the
shape of stored data later is painful.

- **Multi-repo GitHub connector.** Allow a list of repos on a single
  `GithubConnector` config instance (not yet multi-platform — GitLab /
  Bitbucket are Phase 6). Connector emits fragments tagged with repo
  identity. `SemanticFact` gains a `repo` field. Existing single-repo
  deployments keep working unchanged; multi-repo is opt-in.
- **Typed entity IDs on insights.** Replace freeform
  `related_entities` strings with structured references of the form
  `{type: "table" | "service" | "file" | "function" | "repo", id: "..."}`.
  The insight pipeline (anomaly detector, analyzer, chat) populates
  these as it runs. One-release migration window for backwards
  compatibility with the string form.
- **Per-repo SystemFragment merging.** The discovery engine already
  merges fragments from multiple connectors; this extends the merge
  to keep repo identity distinct inside the resulting `SystemModel`.

These additions are worth doing now specifically because our live test
case already has two git repos, so we can exercise multi-repo end-to-end
before it's committed to a UI feature.

### Exit Criteria
1. **Monitoring cycles are anchored.**
2. **User feedback is captured.**
3. **Multi-turn chat works.**
4. **Supabase metrics are comprehensive.**
5. **Railway resource metrics are collected.**
6. **Experiential retrieval works.**
7. **Evidence-backed insights:** Critical anomalies produce insights with
   attached diagnostic evidence — actual query results, viewable in the UI.
8. **Synthesized patterns are surfaced.**
9. **Alert suppression is safe.**
10. **Seasonal baselines reduce false positives.**
11. **Multi-repo support works end-to-end:** A single deployment monitors
    two git repos; semantic facts, insights, and chat all correctly
    attribute code-derived context to the right repo.
12. **Insights carry typed entity IDs** resolvable to SystemModel
    nodes, positioning Phase 5 to build the architecture view on top.

---

## Phase 5: Reporting, Analytics Maturity & System Architecture View

By this phase the system has accumulated substantial historical data.
Phase 5 has two focus areas:

1. **Analytics polish** — charts, reporting, business KPI caching, metric
   registry. The work originally scoped for this phase.

2. **System Architecture View** — a new top-level dashboard tab that
   renders the entire monitored system end-to-end as an interactive,
   zoomable diagram. This is new in the current roadmap revision and
   becomes the visual anchor the Phase 7 agent overlays will extend.

### 5A — Analytics Maturity

**Deliverables:**
- Chart and visualization refinement (time series, categorical, heatmaps)
- Advanced reporting (trend analysis, comparative periods, SLA tracking)
- Business KPI promotion (key app health metrics collected as first-class
  metrics)
- Metric registry (display names, units, thresholds, healthy ranges,
  synonyms)
- Schema RAG for 500+ table schemas
- gridstack.js drag-and-drop dashboard layout

**Exit Criteria:**
1. Charts are informative, with context (thresholds, healthy ranges, trend
   indicators) that makes them actionable without explanation.
2. Business KPIs answer from Observibot's own store, not live production
   DB queries.
3. Dashboard is customizable (drag-and-drop widgets).

### 5B — System Architecture View (Static)

A new top-level tab in the dashboard that renders the entire monitored
system as a single interactive diagram. Scope for Phase 5 is
**static-only**: the diagram shows structure, not live state. Agent-
driven overlays (live status, blast radius) are Phase 7 and will extend
what Phase 5 ships here.

**What the view shows:**
- **Source code structure** for each connected repo — files, modules,
  major functions, entry points, workers, route handlers — clustered
  by repo.
- **Backend components** — databases, services, deployment platforms —
  discovered through existing connectors.
- **Cross-layer connections** — source code that reads from or writes to
  specific database tables, API routes that invoke specific services,
  workers that subscribe to specific queues. Backend-to-backend edges
  (table foreign keys, service dependencies) render separately from
  code-to-backend edges so both kinds of structure are visible.
- **Multi-repo layout** — each repo rendered as its own collapsible
  cluster with cross-repo code references called out explicitly. Our
  live test case has two repos, so this is exercised end-to-end from
  day one.

**What the view does NOT show in Phase 5:**
- Live monitoring state (green/yellow/red nodes) — Phase 7.
- Blast-radius highlighting — Phase 7.
- Agent-specific overlays — Phase 7.

**Non-negotiable UX requirements** (carrying these into the research
brief to avoid settling for "good enough"):
- Sharp, clean visual language. No dated-looking defaults.
- Fast and responsive even for large graphs. Pan and zoom must be
  smooth. Click-to-expand clusters must be instant.
- Intuitive interactions. A developer seeing this for the first time
  should understand what they're looking at within seconds.
- Accessible at the keyboard level, not just mouse.
- Works on the same color palette and font stack as the rest of the
  dashboard (no iframe'd third-party widget that looks alien).

**Research deliverables — complete before implementation begins:**

1. **Graph rendering library evaluation.** Build a small prototype
   with the top 2–3 candidates from the research list, loading a
   representative TaskGator-sized graph (~150 nodes, ~300 edges across
   2 repos plus backend components). Candidates to evaluate include,
   at minimum:
   - Cytoscape.js (mature, Apache 2.0, good clustering)
   - React Flow (reactflow.dev) — modern, React-native
   - Sigma.js (WebGL, designed for large graphs)
   - D3 + dagre or D3 + ELK layout engine
   - Any library surfaced by the research prompt runs that looks
     credible

   Decision criteria: render performance at target graph size, UX
   quality on first touch, license compatibility (Apache 2.0 or
   permissive equivalent), active maintenance, integration effort
   with React 18, ability to customize aesthetics to match the
   existing dashboard. Document the evaluation, the choice, and the
   reasons the others were rejected in `docs/PHASE5_DECISIONS.md`.

2. **Graph storage pattern evaluation.** Initial implementation uses
   a composed view over the existing SystemModel and SemanticFact
   stores — no new graph database dependency — because volume
   doesn't require it yet. But the architecture must not paint us
   into a corner: when a future customer has 10,000+ nodes, we need
   a clean migration path to a real graph store (Neo4j, Memgraph,
   SQLite with recursive CTE + graph schema, or Postgres with
   Apache AGE). Research deliverables:
   - A documented interface (`SystemGraphQuery` ABC or similar) that
     all graph consumers use. The composed-view implementation is
     one backing. A real graph-DB backing can slot in without UI
     changes.
   - A decision memo in `docs/PHASE5_DECISIONS.md` describing
     candidate graph stores, when we'd switch, and rough cost of
     the migration. This doesn't commit us to a choice — it just
     makes sure we know the choice is there.

3. **Cross-layer edge extraction research.** Today, `SemanticFact`
   entries that map code to tables are the foundation. We also need
   stable ways to infer:
   - Code-to-service edges (a function calls a service via HTTP, a
     worker reads a queue)
   - Service-to-service edges (inter-service RPC, message bus)
   - File-to-file dependencies within a repo (imports, calls)
   The research deliverable is a list of which edge types we can
   extract reliably from Phase 4 data alone, which need additional
   inference, and which need new sources of truth (e.g., OpenAPI
   specs, Kubernetes ServiceMonitors). Documented in
   `docs/PHASE5_DECISIONS.md`.

4. **Prior-art survey.** Read and summarize how comparable tools
   handle this — Backstage's Software Catalog, Port, Kubernetes
   topology viewers (Kiali, Weave Scope), OpenTelemetry's service
   maps. What works about their approaches, what doesn't, what
   should we borrow, what should we avoid.

**Implementation deliverables (after research closes):**
- `SystemGraphQuery` interface + composed-view implementation
- Graph extraction: nodes and typed edges from SystemModel,
  SemanticFact, code intelligence index, and connector snapshots
- `/api/graph/*` REST surface (returns nodes, edges, optional filters
  by repo / service / subgraph)
- New top-level tab: "System Architecture" (tentative name — product
  voice may land on something sharper)
- Frontend graph component using the library chosen by research
  deliverable #1
- Repo clustering, collapse/expand, zoom, pan, search, node focus
- Legend explaining node types and edge types
- Empty / partial state handling (e.g., no code extracted yet, one
  connector unavailable)

**Exit Criteria:**
1. **Architecture view tab is live** and accessible to authenticated
   users alongside the existing three zones.
2. **Multi-repo rendering works.** The TaskGator test deployment's
   two repos render as two clusters with cross-repo references
   visible.
3. **Cross-layer edges are visible.** Code-to-table edges from
   SemanticFacts render correctly on at least one representative
   flow. Service-to-service edges render where discoverable.
4. **Large-graph performance is acceptable.** Pan / zoom / expand
   interactions on a 500-node graph feel smooth on a mid-range
   laptop.
5. **Research decisions are documented** in
   `docs/PHASE5_DECISIONS.md` — library choice, graph storage
   pattern, edge extraction strategy, prior-art lessons.
6. **Graph API is backing-store agnostic.** The
   `SystemGraphQuery` interface has at least two backing-store
   implementations (the composed-view production one plus a test
   fixture) to prove the abstraction is real.

---

## Phase 6: Ecosystem Expansion — Connectors

This is the first of the two phases where the platform thesis plays out.
Up to this point, Observibot has been validated against GitHub + Supabase +
Railway, which is enough to prove the SRE agent works but not enough to
serve the broader audience the platform is built for. Phase 6 broadens the
connector surface so the platform starts being genuinely useful to teams
beyond the PaaS-only wedge.

**The core team will ship enough connectors to prove the pattern is real
before community contribution opens broadly.** The goal is to reach a
point where writing a new connector is a well-defined exercise a
contributor can complete in a week, not a research project.

### Deliverables

**Database connectors (priority order, subject to revision based on
research):**
- Generic Postgres already exists; expand to cover the common managed
  variants: Neon, Amazon RDS Postgres, Google Cloud SQL Postgres
- MySQL family: Generic MySQL, PlanetScale (Vitess), Amazon RDS MySQL
- Click-through targets based on usage data: DynamoDB, MongoDB, Redis

**Deployment / infrastructure connectors:**
- Fly.io, Render, Vercel (nearest neighbors to current Railway coverage)
- AWS (EC2, ECS, Lambda, CloudWatch metrics)
- GCP (Cloud Run, GCE, GKE, Cloud Monitoring)
- Azure (App Service, AKS, Azure Monitor)

**Source-code connectors:**
- GitLab (self-hosted and SaaS)
- Bitbucket

**Generic observability connectors:**
- Prometheus scrape
- OpenTelemetry (metrics + traces)

**Platform:**
- `SourceCodeConnector` ABC (generalizes GitHub connector)
- `CloudConnector` ABC (generalizes Railway connector to cover cloud providers)
- Multi-project support with isolated discovery, metrics, and business
  context per project
- Plugin system via Python entry points — connectors can be installed as
  third-party packages without modifying core code
- Each new connector's output flows into the System Architecture View
  (Phase 5B) automatically — no per-connector graph integration work

**Community contribution infrastructure:**
- `docs/contributing/CONNECTORS.md` promoted from stub to full guide with
  test harness, reference implementation walkthrough, and PR review rubric
- Connector certification process (what core-team review looks like)
- Connector manifest / capability declaration
- Community registry or index for third-party connectors

### Exit Criteria
1. **Connector breadth:** At least five database connectors, three
   deployment platforms, and two source-code hosts are supported in core.
2. **One major cloud:** AWS, GCP, or Azure is supported end-to-end
   (discovery, metrics, changes).
3. **Plugin system:** A third-party connector can be installed from a
   separate package without modifying core code, and it appears in the
   Discovery Feed like any core connector.
4. **Multi-project:** A single Observibot instance monitors two distinct
   applications with no data crossing between them.
5. **Contribution readiness:** A new developer can build and submit a
   connector PR following only the contribution docs, without consulting
   the core team.

---

## Phase 7: Ecosystem Expansion — Agents & Architecture View Overlays

The second platform-thesis phase. Observibot demonstrates that the system
model is genuinely useful for reasoners beyond the SRE agent, and that
the architecture view (shipped static in Phase 5) becomes a genuinely
powerful tool when agents project live state and analysis results onto
it.

### Deliverables

**Second core agent — Security Threat Modeling:**
- Traces auth flows across source code, database permissions, API routes,
  and infrastructure config to find cross-layer vulnerabilities
- Models threat surfaces: what could a compromised service reach? which
  RLS policies gate which data? what secrets are exposed where?
- Continuous threat posture tracking as the system evolves
- Severity taxonomy appropriate to security (vulnerability, not anomaly)
- Its own chat tools registered into System Intelligence Chat

**Agent platform:**
- `BaseAgent` ABC — the contract every agent implements
- Agent lifecycle: registration, analysis loop scheduling, tool
  registration, configuration
- Discovery Feed agent source filtering — see insights from all agents
  or one specific agent
- Dynamic chat tool registration — agents register their own tools
  instead of tools being hard-coded
- Agent-scoped severity taxonomies

**Architecture View Overlays (extends the Phase 5B static view):**
- **Live state overlay.** SRE agent paints node states onto the graph
  (healthy / warning / critical) based on the current Discovery Feed.
  A failing worker, a degraded service, a saturated database all
  render as colored nodes on the architecture diagram.
- **Click-through navigation.** Every insight in the Discovery Feed
  has a "Show on map" action that switches to the architecture view
  with the relevant node(s) highlighted. Uses the typed entity IDs
  shipped in Phase 4.5 Step 7.
- **Blast-radius highlighting.** When an agent identifies a
  concerning node, it can compute a reachable subgraph (what does
  this touch?) and return it as a visual overlay. Primary use: the
  security agent highlights a vulnerable code path and the
  downstream data, services, and tables it can reach. The SRE
  agent can do the same — a failing database's blast radius is every
  service reading from it.
- **Agent-specific overlay modes.** Users toggle between overlay
  modes via a dropdown: SRE (performance state), Security (threat
  exposure), and future agents. Each agent registers an overlay
  renderer that colors or annotates nodes and edges according to
  its own taxonomy.

**Community/private agent infrastructure:**
- `docs/contributing/AGENTS.md` promoted from stub to full guide
- Private agent installation path (load agent from local package without
  modifying core)
- Agent manifest / capability declaration
- Architecture decision on agent distribution model (separate process vs
  plugin class vs config-only) — deferred to this phase based on
  learnings from Phase 6
- Overlay registration API stable enough that community agents can
  contribute overlays without core changes

### Exit Criteria
1. **Second agent working:** The security threat modeling agent runs
   alongside the SRE agent, contributes insights to the same Discovery
   Feed with agent-source filtering, and registers its own chat tools.
2. **Three-mode support:** Core agents, community-contributed agents,
   and private agents can all run in the same deployment.
3. **Agent API stability:** The `BaseAgent` contract is versioned and
   documented. Existing agents do not need to change when new core
   capabilities are added.
4. **Live architecture overlay works.** An SRE-detected failing
   component renders as red on the architecture view within one
   monitoring cycle.
5. **Click-through from insight to architecture works.** Every insight
   with typed entity IDs has a working "Show on map" action.
6. **Blast-radius visualization works.** The security agent produces
   at least one insight with a computed reachable subgraph that
   renders correctly as a highlighted overlay.
7. **Contribution readiness:** A new developer can build and submit
   an agent PR (including an overlay) following only the contribution
   docs.

---

## Phase 8+: Beyond

The phases past this point are intentionally not defined in detail. At
current pace the platform thesis plays out over 12-24 months. What comes
after depends heavily on what we learn from the first community
contributions, the second agent, and real-world usage at scale.

### Candidate directions

- Cost agent (resource utilization, waste identification, spend
  correlation with business value)
- Compliance agent (audit trail generation, regulatory posture, evidence
  collection)
- On-call agent (incident response, runbook execution, handoff summaries)
- Hosted tier infrastructure (multi-tenant deployment, isolation,
  billing)
- Agent registry / marketplace
- Additional reasoning primitives beyond LLM calls (traditional ML for
  pattern detection, time-series forecasting, causal inference)

### Known architectural decision points deferred to this phase

These are decisions we've explicitly flagged but deliberately not made
yet, because making them before the relevant use cases exist would be
guessing. They're tracked here so they don't get lost.

- **Graph storage migration.** The System Architecture View ships in
  Phase 5 using a composed view over existing stores. When graph scale
  or query complexity outgrows that (10,000+ nodes, complex reachability
  queries, multi-hop path traversals at interactive speed), we migrate
  the `SystemGraphQuery` backing to a real graph store. The Phase 5
  research deliverable documented candidates (Neo4j, Memgraph, Postgres
  with Apache AGE, SQLite with graph-shaped schema + recursive CTEs)
  and rough migration cost. The decision to migrate — and to which
  backing — belongs in this phase.
- **Agent distribution model.** Plugin classes loaded at runtime vs
  separate processes with a stable API vs config-only agents. The
  Phase 7 decision was provisional; revisiting based on what real
  community and private agents need.
- **Multi-tenant data isolation.** Up through Phase 7, an Observibot
  deployment monitors one logical set of systems (even if multi-
  project from Phase 6). A hosted tier where many independent
  customers share infrastructure requires real isolation — at the
  DB row level, at the LLM context level, at the Discovery Feed
  level. Hard-to-retrofit, so likely becomes a clean architectural
  work stream when a hosted tier is committed to.

The principle that holds: whatever ships next must strengthen the
connector layer, the system model, or the agent ecosystem. Features
that serve only the first wedge or lock us into narrow scope are
off-mission.

---

## Tech Stack

**Backend:** Python 3.12, FastAPI, SQLAlchemy 2.x + Alembic, asyncpg,
aiosqlite, httpx, APScheduler, Anthropic/OpenAI SDKs, sqlglot, pydantic,
scipy, numpy, deepdiff, python-jose, bcrypt

**Frontend:** React 18 + TypeScript, Vite, Tailwind CSS, ECharts
(echarts-for-react), Vega-Lite + vega-embed, gridstack.js. Graph
rendering library TBD in Phase 5 research — candidates include
Cytoscape.js, React Flow, Sigma.js, and D3 + ELK/dagre.

**Infrastructure:** Docker (multi-stage), Railway templates, GitHub
Actions CI
