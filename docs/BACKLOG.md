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
