# Observibot — Backlog & Deferred Items

Items are tagged: [P0] Must fix now, [P1] Should do next, [P2] Nice to have

## P0 — Active Bugs (Must Fix Before Expanding Scope)

### ~~User Count Returns 0 from Production DB~~ ✅ FIXED
Root cause was Supabase RLS, not table name collision. Added SELECT policy
for `observibot_reader` on production tables. "How many users?" now returns 47.

### ~~Layout Overflow — Zones Not Independently Scrollable~~ ✅ FIXED
Changed `min-h-screen` → `h-screen` in Layout.tsx. Body scrollHeight now
matches viewport height. All three zones scroll independently.

### ~~JavaScript Exceptions — "Unexpected end of JSON input"~~ ✅ FIXED
`client.ts` `request()` now handles empty/non-JSON success responses.

## P0 — Critical UX Issues

### ~~Dynamic Discovery Feed Insight Lifecycle~~ ✅ FIXED
Four actions implemented on each insight card: Acknowledge (removes from
active view, retained in backend), Pin (pinned to top with blue ring),
Promote to Dashboard (creates Static Dashboard widget), Investigate
(auto-submits context to System Intelligence Chat).
Remaining future work: auto-decay for stale insights and re-evaluation
against current metrics (requires storing triggering conditions).

### ~~Insight Deduplication in Discovery Feed~~ ✅ FIXED
Fingerprint now excludes LLM-generated title/summary, keeping only structural
fields (severity, source, related_tables, related_metrics).

### ~~Number Formatting Throughout~~ ✅ FIXED
KPI values, confidence scores formatted. Timestamps now show relative time
("5m ago"). Remaining minor polish deferred to Phase 5 (Metric Registry).

## P1 — Phase 4: Deep Application Intelligence

### GitHub Source Code Connector (Phase 4 — Primary Deliverable)
`BaseConnector` subclass using GitHub REST/GraphQL API. Investigate best
open-source tools for accurate semantic code understanding (architecture,
components, logic flow, connectors, data models). Multi-stage pipeline:
discover repo → identify high-signal files → LLM-powered semantic
extraction → store as business context. Auto-refresh on commit/merge
(webhook preferred, polling fallback). `SourceCodeConnector` ABC for
GitLab/Bitbucket (Phase 6). Security: filter secrets, exclude sensitive files.

### Runtime-to-Source Change Correlation (Phase 4)
Agent correlates source code changes with performance impacts. Uses Discovery
Feed history and Static Dashboard baselines to detect shifts. Automatically
investigates recent commits and surfaces both obvious and non-obvious
downstream observations (positive and negative).

### Enhanced Self-Discovery of Application Semantics (Phase 4)
Improvements that deepen the agent's understanding:
1. **Postgres column comments** — query `pg_description` during discovery;
   include comments in the schema catalog sent to the LLM. Zero user effort.
2. **Schema pattern inference** — enhance the semantic modeler prompt to
   extract business semantics from naming patterns (e.g., `completed_at`
   implies a state transition, FK chains imply workflows).
3. **Conversational correction** — when user says "that's wrong, onboarded
   means completed_onboarding_at is not null," store the correction in
   `business_context` and inject into future planning prompts.

## ~~P0 — Phase 4.5 Prerequisites~~ ✅ COMPLETE (April 13, 2026)

All 6 prerequisites implemented. 339 tests passing (43 new, zero regressions).

### ~~Monitor Runs Table~~ ✅
### ~~Insight Outcome/Feedback Tracking~~ ✅
### ~~Chat Session ID Support~~ ✅
### ~~Shared Prometheus Text Parser~~ ✅
### ~~Supabase Metrics API Scraping~~ ✅
### ~~Railway Resource Metrics~~ ✅

## P1 — Phase 4.5 Memory Implementation

### ~~Events Envelope (Tier 1)~~ ✅
Unified episodic timeline. 387 tests passing.

### ~~Session Memory (Tier 3)~~ ✅
Structured turns, multi-turn resolution, EXPLAIN gating, Agent Memory Inspector. 478 tests.

### Deterministic Experiential Retrieval
Seasonal MAD baselines (hour-of-week bucketing). Deterministic lookbacks
during anomaly analysis. Recurrence annotations on Discovery Feed.

### Synthesis Agent (Tier 2 — Advisory Mode)
Deterministic pre-clustering + LLM labeling → SynthesizedKnowledge records.
Advisory-only: patterns displayed but no behavior changes.

### Policy Layer + Alert Suppression
Separate suppression_policies table. Bayesian posterior updates. Strict
guardrails for auto-suppression.

### Correction Detection Upgrade
Teaching-intent detection via planning call structured output. Retire
hardcoded regex patterns.

## Deferred — Slotted to Future Phases

### Vega-Lite Chart Issues (Phase 5)
- Version mismatch: spec uses v5, vega-embed is v6.4.2 (warnings)
- "Infinite extent" errors on infrastructure status charts
- Status data should use a status widget, not a bar chart

### Pinned Widget Data Flow (Phase 5)
Pin-to-dashboard creates a widget card with title but body may render empty.
Widgets should store BOTH a query_binding (for refresh) and a data_snapshot
(for immediate render). Currently only stores partial config.

### Business KPI Promotion (Phase 5)
Core health questions collected as first-class metrics into Observibot's
store. Answers from Domain 1 (fast, cached) instead of Domain 2 (live query).

### Metric Registry (Phase 5)
Display names, units, thresholds, healthy ranges, aggregation behavior,
synonyms. Enables formatted output and contextual LLM answers.

### gridstack.js Drag-and-Drop (Phase 5)
Dashboard uses CSS grid. gridstack.js installed but not wired.

### Schema RAG for Large Schemas (Phase 5)
For 500+ tables, need semantic search over table descriptions.

### Additional Connectors (Phase 6)
Neon, Fly.io, Render, Vercel, PlanetScale. In-tree until community emerges.

### Multi-Project Support (Phase 6)
Isolated discovery, metrics, and business context per project.

### Agent Abstraction Layer (Phase 6)
`BaseAgent` ABC defining the contract for specialized agents: tool set,
analysis loop, prompts, severity taxonomy. Agent registry for concurrent
execution. Discovery Feed agent filtering. Dynamic chat tool registration.
See `docs/architecture/ARCHITECTURE.md` "Future: Agent Ecosystem" section.

### GitLab/Bitbucket Source Code Connectors (Phase 6)
Via SourceCodeConnector ABC established in Phase 4.

### SSE for Discovery Feed (Unslotted)
Polling works. EventSource + httpOnly cookies unreliable. Keep polling for v1.

### SSE for Chat Streaming (Unslotted)
Chat responses arrive as single POST. Future: stream LLM tokens via SSE.

### Token Revocation / Blocklist (Unslotted)
JWTs valid until expiry even after user deletion.

### Timezone Handling in Charts (Unslotted)
LLM specs should use user's local timezone, not UTC.

### Widget Schema Versioning (Unslotted)
schema_version column exists, needs enforcement logic.

### Observability of Observibot Itself (Unslotted)
/metrics endpoint (Prometheus format) for daemon and web services.

### Cross-Widget Communication (Unslotted)
Global time filter coordinating across independent widget components.

### Interactive Onboarding Interview (Unslotted)
Semantic modeler auto-accepts; future: interactive walkthrough.

### LLM Query Optimization Feedback Loop (Unslotted)
Feed EXPLAIN cost back to LLM for query rewriting when rejected.


## P1 — Phase 4.5 Step 3 Follow-ups (discovered during live verification)

### Broaden enum DEFINITION fact coverage
Step 3 accuracy sprint extended the enum-sampling heuristic in
postgresql.py beyond `status/_status` to include `state`, `type`, `kind`,
`role`, `severity`, `level`, `tier`, `phase`, `mode`, `category`.
Live verification showed only 1 of 7 enum-candidate columns in the
TaskGator schema produced a DEFINITION fact. Investigate why the
extension landed in code but did not produce facts for `severity`,
`role`, `state`, `kind`, etc. Likely causes: the sampling query is
short-circuiting on column filters it shouldn't, or the schema_analyzer
path that emits the DEFINITION fact is still gated by the old narrow
heuristic.

### Hallucination detector: accept derived percentages
`_find_unsupported_numbers()` in chat_agent.py flags any number in a
narrative that is not literally present in the tool result rows. This
produces false positives for derived percentages (e.g. "37% of total"
when the data contains 18 and 49 separately). Extend the detector: if
the narrative cites X% and the tool results contain two numbers whose
ratio is approximately X%, accept. Likewise for simple averages, deltas,
and sums of visible columns. Keep the detector conservative enough to
still catch pure hallucinations ("117 jobs" when no 117 appears anywhere).

### Investigate 03:47 metric-count spike and cold-start gate
During the 2026-04-16 insight cluster investigation, evidence showed
metric_count jumping from 84 to 195 in one cycle (discovery added ~111
new metrics). First anomaly fires happened the same cycle. Verify that
the `min_samples=12` gate in AnomalyDetector is actually being respected
for newly-discovered metrics, or whether there is a path where a bucket
with insufficient history can fire.

