# Observibot — Backlog & Deferred Items

Items are tagged: [P0] Must do, [P1] Should do, [P2] Nice to have

## Phase 3 Deferred

### [P1] Production DB Querying — Enable and Wire
AppDatabasePool (core/app_db.py) and query_application tool
(chat_agent.py) are built but not yet wired into the config
loader or CLI startup. Need: config flag parsing for
chat.enable_app_queries, DSN from connector config, pool
initialization in monitor.py startup, and deps.set_app_db()
call. The tool, sandbox, and schema catalog are ready.

### [P1] LLM Query Optimization Feedback Loop
When EXPLAIN rejects a query as too expensive, feed the cost back
to the LLM and ask it to rewrite with filters/indexes. Currently
we just reject and show an error message.

### [P1] SSE for Discovery Feed
Discovery Feed currently polls /api/insights every 5 seconds.
EventSource doesn't reliably carry httpOnly cookies across all
browsers. Deferred in favor of polling which is simpler and
equally effective for the current update cadence (5s). SSE can
be revisited with a token-in-URL scheme or dedicated SSE auth.

### [P2] SSE for Chat Streaming
Chat responses arrive as a single POST response. Future: stream
LLM tokens via SSE for progressive rendering (narrative appears
word-by-word, then visualization renders at the end).

### [P2] gridstack.js Drag-and-Drop
Dashboard uses CSS grid. gridstack.js is installed but not yet
wired into the Dashboard component for drag-and-drop reordering
and resizing. The layout batch update API endpoint is ready.
Deferred because integrating gridstack with React 19 portals
requires careful lifecycle management.

### [P2] Schema RAG for Large Monitored Apps
For 500+ table schemas, the full DDL won't fit in LLM context.
Need semantic search over table descriptions to inject only the
5-10 most relevant tables into each prompt.

### [P2] Token Revocation / Blocklist
JWTs remain valid until expiry even after user deletion. Add a
minimal token blocklist (in-memory or DB-backed with TTL).

### [P2] Multi-Tenant Data Isolation
If monitoring a multi-tenant SaaS, LLM-generated queries could
cross tenant boundaries. AST parser should enforce WHERE tenant_id
clauses when tenant_id is configured.

### [P2] Timezone Handling in Charts
LLM-generated Vega-Lite specs should use user's local timezone,
not UTC. Requires passing timezone from frontend to backend.

### [P2] Widget Schema Versioning
Add explicit schema_version to widget definitions so saved
dashboards don't break on upgrade. Already has column in DB,
needs enforcement logic.

### [P2] Observability of Observibot Itself
Expose /metrics endpoint (Prometheus format) for the daemon and
web services. Users should see when Observibot is falling behind
or experiencing LLM failures.

### [P2] Cross-Widget Communication
Global time filter that all dashboard widgets respond to. Requires
event bus or context provider that coordinates filter state across
independently rendered widget components.

### [P2] Interactive Onboarding Interview
Current semantic modeler auto-accepts LLM suggestions. Future:
interactive walkthrough where the user confirms/corrects the LLM's
understanding of their application architecture.

### [P2] Additional Connectors
Neon, Fly.io, Render, Vercel, PlanetScale. Keep in-tree until
community emerges.
