# Observibot — Backlog & Deferred Items

Items are tagged: [P0] Must fix now, [P1] Should do next, [P2] Nice to have

## P0 — Active Bugs (Must Fix Before Expanding Scope)

### User Count Returns 0 from Production DB
`query_application` tool generates correct SQL (`SELECT COUNT(*) FROM public.users`)
and routes to Domain 2 (green "application" badge appears), but returns 0 instead
of the actual count (46 users). Suspected cause: table name collision between
Observibot's internal auth `users` table in the SQLAlchemy store and the production
`public.users` table. The SQL may be executing against the wrong database engine.
**Investigation needed:** verify which engine `_exec_application` uses, ensure it
uses `AppDatabasePool.execute_sandboxed()` and NOT `store.engine`.

### Layout Overflow — Zones Not Independently Scrollable
When Discovery Feed has many insights (40+), the entire page becomes a tall scrollable
document, pushing Dashboard and Chat zones off-screen. Fix applied to `App.tsx`
(added `h-full` to zone wrappers) but frontend needs rebuild.
**CSS fix:** Each zone column must have `overflow-y-auto` within a height-constrained
container so each zone scrolls independently.

### JavaScript Exceptions — "Unexpected end of JSON input"
API endpoints occasionally return non-JSON responses (possibly empty bodies or
truncated responses), causing `SyntaxError` in the frontend JSON parser. No error
shown to user — silent failures. Need error boundary + graceful fallback.

## P0 — Critical UX Issues

### Insight Deduplication in Discovery Feed
Near-identical insights generated every 5 minutes ("Abnormal User Activity Spike
Following Worker Deployment" appears 10+ times with minor wording variations).
The insight fingerprinting/dedup system needs tuning — either increase fingerprint
similarity threshold or aggregate consecutive similar insights.

### Number Formatting Throughout
- Ratios display as raw decimals (0.9995 not 99.95%)
- Timestamps display as ISO strings not relative ("2m ago")
- Column headers are snake_case database names not human-readable
- `frontend/src/utils/format.ts` exists but may not be applied everywhere

## P1 — Should Do Next

### Vega-Lite Chart Issues
- Version mismatch: spec uses v5, vega-embed is v6.4.2 (warnings)
- "Infinite extent" errors on infrastructure status charts (bar chart for categorical status data doesn't render)
- Status data should use a status widget, not a bar chart

### Pinned Widget Data Flow
Pin-to-dashboard creates a widget card with title but body may render empty.
Per architecture decisions: widgets should store BOTH a query_binding (for refresh)
and a data_snapshot (for immediate render). Currently only stores partial config.

### Business KPI Promotion
Core health questions (user count, sync failure rate, extraction latency) should be
collected as first-class metrics into Observibot's own store, so they answer from
Domain 1 (fast, cached) instead of requiring Domain 2 (live production query).
See AGENTIC_DECISIONS.md Decision 8.

### Metric Registry
Build a registry for observability metrics: display name, unit, thresholds, healthy
ranges, aggregation behavior, synonyms. This fixes formatting and enables the LLM
to provide context ("99.95% — excellent, anything above 99% is healthy").

## P2 — Deferred

### gridstack.js Drag-and-Drop
Dashboard uses CSS grid. gridstack.js installed but not wired. Deferred because
accurate live data is more valuable than draggable blank widgets.

### SSE for Discovery Feed
Polling works. EventSource + httpOnly cookies unreliable. Keep polling for v1.

### SSE for Chat Streaming
Chat responses arrive as single POST. Future: stream LLM tokens via SSE.

### Schema RAG for Large Schemas
For 500+ tables, need semantic search over table descriptions.

### Token Revocation / Blocklist
JWTs valid until expiry even after user deletion.

### Multi-Tenant Data Isolation
RLS or tenant-scoped views for multi-tenant production databases.

### Timezone Handling in Charts
LLM specs should use user's local timezone, not UTC.

### Widget Schema Versioning
schema_version column exists, needs enforcement logic.

### Observability of Observibot Itself
/metrics endpoint (Prometheus format) for daemon and web services.

### Cross-Widget Communication
Global time filter coordinating across independent widget components.

### Interactive Onboarding Interview
Semantic modeler auto-accepts; future: interactive walkthrough.

### LLM Query Optimization Feedback Loop
Feed EXPLAIN cost back to LLM for query rewriting when rejected.

### Additional Connectors
Neon, Fly.io, Render, Vercel, PlanetScale. In-tree until community emerges.
