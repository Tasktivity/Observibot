# Observibot — Comprehensive System Verification Plan

**This is a one-time deep verification of ALL features built across Phases 0-4
and Phase 4.5 Steps 0-1. Every capability must be directly observed working
against the live system. This plan must be executed by CC after the
stabilization pass is complete.**

**Origin:** Multiple production bugs were found by accident (Railway GraphQL
schema change, 204k prompt overflow) because testing only covered the happy
path of the most recent changes. This plan ensures nothing is silently broken.

## How To Use This Plan

CC must execute every test in order. For each test:
1. Perform the action described
2. Record the ACTUAL result (not "should work" — what DID happen)
3. If the result doesn't match expected: STOP and report the failure
4. Include evidence: log lines, API responses, database queries, screenshots

Estimate: 30-45 minutes of live testing time.

---

## Section 1: System Startup & Health

### 1.1 Clean Start
- Start Observibot (`observibot run` or equivalent dev command)
- **Expected:** No errors in startup log. All connectors initialize.
- **Check:** Log shows Supabase connected, Railway connected, GitHub connected
- **Check:** Web server starts on :8080

### 1.2 Health Endpoints
- `GET /api/system/health` → `{"status": "ok", "version": "..."}`
- `GET /api/system/status` (with auth cookie) → connectors listed, monitor_running=true
- `GET /api/system/code-intelligence-status` → status: "current" or "stale"

### 1.3 Authentication
- Load `localhost:8080` without auth → redirected to login
- Login with valid credentials → JWT cookie set, dashboard loads
- Invalid credentials → error message displayed

---

## Section 2: Monitor Loop (Phase 1)

### 2.1 Metric Collection Cycle
- Wait for one 5-minute collection cycle to complete
- **Check server logs for:**
  - `Collection cycle completed: N metrics from M connectors`
  - N should be significantly > 70 (Supabase pg_stat + Prometheus + Railway)
  - No errors or warnings during collection

### 2.2 Supabase pg_stat Metrics (Phase 1, original)
- Query database: `SELECT DISTINCT metric_name FROM metric_snapshots WHERE connector_name LIKE '%supabase%' ORDER BY metric_name`
- **Expected:** table_row_count, table_inserts, table_updates, table_deletes, dead_tuple_ratio, active_connections, blocked_queries, long_running_queries, cache_hit_ratio

### 2.3 Supabase Prometheus Metrics (Step 0)
- **Check server logs for:** `Supabase Metrics API: collected N metrics`
- N should be > 50 (they expose ~200, filtering reduces this)
- Query database: `SELECT DISTINCT metric_name FROM metric_snapshots WHERE connector_name LIKE '%supabase%' AND metric_name LIKE 'node_%' LIMIT 20`
- **Expected:** node_cpu_seconds_total, node_memory_*, node_disk_*, etc.
- **Verify no NaN/Inf:** `SELECT COUNT(*) FROM metric_snapshots WHERE value = 'NaN' OR value = 'Inf' OR value = '-Inf'` → should be 0
- **Verify counter deltas:** After 2+ cycles, check that counter-type metrics (node_cpu_seconds_total) show small delta values, NOT large cumulative values. Query: `SELECT metric_name, value, collected_at FROM metric_snapshots WHERE metric_name = 'node_cpu_seconds_total' ORDER BY collected_at DESC LIMIT 5`

### 2.4 Railway Resource Metrics (Step 0)
- **Check server logs for:** Railway GraphQL metrics OK or similar
- Query database: `SELECT DISTINCT metric_name FROM metric_snapshots WHERE connector_name LIKE '%railway%'`
- **Expected:** service_count PLUS service_cpu_usage, service_memory_usage_gb, service_disk_usage_gb, service_network_rx_gb, service_network_tx_gb
- **Verify per-service:** `SELECT metric_name, labels, value FROM metric_snapshots WHERE connector_name LIKE '%railway%' AND metric_name != 'service_count' ORDER BY collected_at DESC LIMIT 10`
- **Expected:** labels should contain `{"service": "<your-service-name>"}` etc.

### 2.5 Monitor Runs (Step 0)
- Query: `SELECT id, started_at, finished_at, status, metric_count, anomaly_count, insight_count, llm_used FROM monitor_runs ORDER BY started_at DESC LIMIT 5`
- **Expected:** Recent rows with status="completed", metric_count > 0
- **Verify no stale:** `SELECT COUNT(*) FROM monitor_runs WHERE status = 'running'` → should be 0 or 1 (current cycle only)
- **Verify llm_used:** If anomalies were detected, llm_used should be True

### 2.6 Anomaly Detection (Phase 1)
- Query: `SELECT metric_name, connector_name, mean, stddev, sample_count FROM metric_baselines ORDER BY sample_count DESC LIMIT 10`
- **Expected:** Baselines populated with sample_count > 0 for multiple metrics
- **Check logs:** Any "Detected N sustained anomalies" messages?

### 2.7 Change Detection (Phase 1)
- Query: `SELECT event_type, summary, occurred_at FROM change_events ORDER BY occurred_at DESC LIMIT 5`
- **Expected:** Deploy events from Railway with service names

### 2.8 Discovery & Drift (Phase 0-1)
- Query: `SELECT fingerprint, created_at FROM system_snapshots ORDER BY created_at DESC LIMIT 3`
- **Expected:** Snapshots with consistent fingerprints (unless schema changed)

---

## Section 3: Web Dashboard (Phase 3)

**ALL TESTS IN THIS SECTION REQUIRE A REAL BROWSER.**
Use Chrome DevTools MCP to open localhost:8080:
1. `Claude in Chrome:tabs_context_mcp` → `tabs_create_mcp` → `navigate(tabId, "http://localhost:8080")`
2. If login screen appears, STOP and ask the user to log in, then continue
3. Take screenshots with `computer(action="screenshot", tabId, save_to_disk=true)`
4. Check console: `read_console_messages(tabId, onlyErrors=true)`
5. Check network: `read_network_requests(tabId, urlPattern="/api/")`
Attach screenshots as evidence for every test in this section.

### 3.1 Three-Zone Layout
- Load `localhost:8080` after login
- **Expected:** Three zones visible simultaneously:
  - Zone 1 (Dynamic Discovery Feed) on the left
  - Zone 2 (Static Dashboard) in the center
  - Zone 3 (System Intelligence Chat) on the right
- **Check:** All three zones scroll independently
- **Check:** No zone pushes another off-screen
- **Check:** Browser console has zero JavaScript exceptions

### 3.2 Discovery Feed (Zone 1) — Insights
- **Expected:** Insight cards with severity badges (critical/warning/info)
- **Check:** Each card shows: title, summary, confidence, timestamp
- **Check:** Timestamps show relative time ("5m ago", not raw ISO)

### 3.3 Discovery Feed — Lifecycle Actions (Phase 3)
Test all four lifecycle actions on an insight card:
- **Acknowledge:** Click → card removed from active view
- **Pin:** Click → card pinned to top with visual indicator
- **Promote to Dashboard:** Click → widget appears in Zone 2
- **Investigate:** Click → context sent to Zone 3 chat

### 3.4 Discovery Feed — Feedback Buttons (Step 0)
- **Noise button:** Click → shows loading state → confirms with visual change
- **Actionable button:** Click → same pattern
- **Double-click test:** Click rapidly → only ONE request fires (button disabled during request)
- **Error test:** If possible, disconnect network briefly and click → error message visible
- **Verify in DB:** `SELECT insight_id, outcome, user_id, created_at FROM insight_feedback ORDER BY created_at DESC LIMIT 5`
- **user_id must NOT be NULL or empty string**

### 3.5 Discovery Feed — Recurrence Annotations (Step 1)
- If any anomaly has occurred multiple times on the same metric:
  - **Expected:** "Seen N times in last 30 days" annotation below the insight
  - **Check:** Common hours shown if applicable
- If system just started (no prior data):
  - **Expected:** No recurrence annotation (first occurrence)

### 3.6 Static Dashboard (Zone 2)
- **Check:** Any promoted widgets render with data (not empty shells)
- **Check:** Widget types render correctly (KPI, table, text summary)

### 3.7 Number Formatting
- KPI values formatted (commas, percentages)
- Confidence scores formatted (not raw 0.85000001)
- Timestamps show relative time

---

## Section 4: System Intelligence Chat (Zone 3)

**ALL TESTS IN THIS SECTION REQUIRE A REAL BROWSER** for the UI tests
(session indicator, "New conversation" button, visual layout). Use Chrome
DevTools MCP: navigate to localhost:8080, type in the chat input using
`find(tabId, "chat input")` then `form_input(ref, tabId, value)` or
`computer(action="type", tabId, text)`. Take screenshots after each query.
Script-based verification can supplement but NOT replace browser testing.

### 4.1 Primary Acceptance Test
- Ask a known-answer question against a stable application table (e.g.
  a row count for an entity whose count rarely changes)
- **Expected:** LLM-generated narrative returns the correct value via the
  application domain (NOT the deterministic fallback)
- **Expected:** Narrative answer, not "Found N results"
- **Expected:** Domain badge shows "application"
- **Check server logs:** No 400 errors, no retry storms, no "prompt too long"

### 4.2 Multi-Domain Routing
- Ask: "What alerts fired recently?" → Domain: observability
- Ask: "When was the last deploy?" → Domain: infrastructure
- Ask a question requiring an application table → Domain: application

### 4.3 Business Context (Phase 4)
- Ask a question whose answer depends on a SemanticFact stored in the
  knowledge layer (one your `semantic_facts` table actually contains)
- **Expected:** The generated SQL uses the column/condition from the
  matching SemanticFact rather than a naive aggregate
- **Expected:** Returns the correct count, not a generic answer
- **Check server logs:** Business context injection logged

### 4.4 Session Persistence (Step 0)
- Ask the primary acceptance question
- Follow up with a refinement (e.g. "Break that down by signup month")
- **Expected:** Understands "that" refers to the prior subject and
  generates a GROUP BY query
- Follow up again with a filter ("What about just the last 3 months?")
- **Expected:** Adds the date filter to the prior query
- **Check:** Turn counter visible in UI

### 4.5 Session Expiry
- Click "New conversation" button
- **Expected:** Confirmation prompt or clear visual reset
- **Expected:** Message history clears
- Send a new message → should work as standalone (no prior context)

### 4.6 Chat Error Handling
- Ask a nonsensical question → should get graceful response, not crash
- **Check:** No JavaScript exceptions in browser console
- **Check:** No unhandled errors in server logs

### 4.7 Prompt Size (Stabilization)
- After any chat query, check server logs for prompt size logging
- **Expected:** Planning prompt logged at DEBUG level with section breakdown
- **Expected:** Total should be < 30k tokens (WARNING threshold)
- If > 30k tokens, report which section is largest

---

## Section 5: Code Intelligence (Phase 4)

### 5.1 GitHub Connector
- `GET /api/system/code-intelligence-status`
- **Expected:** status="current", last_indexed_commit has a SHA, last_index_time recent
- **Check server logs:** GitHub polling occurring every 15 minutes

### 5.2 Semantic Facts
- Query: `SELECT COUNT(*) FROM semantic_facts WHERE is_active = 1`
- **Expected:** 600+ active facts
- Query: `SELECT source, COUNT(*) FROM semantic_facts WHERE is_active = 1 GROUP BY source`
- **Expected:** code_extraction: ~376, schema_analysis: ~232, maybe some corrections
- Query: `SELECT concept, claim, confidence FROM semantic_facts WHERE source = 'user_correction'`
- **Expected:** Any corrections the user has provided via chat

### 5.3 Correction Detection
- In chat, type: "actually, active user means last_login_at > now() - interval '30 days'"
- **Expected:** Stored as a CORRECTION fact with confidence 1.0
- Verify: `SELECT * FROM semantic_facts WHERE source = 'user_correction' ORDER BY created_at DESC LIMIT 1`

---

## Section 6: Events System (Step 1)

### 6.1 Events Being Emitted
- After 1-2 monitoring cycles, query: `SELECT event_type, COUNT(*) FROM events GROUP BY event_type`
- **Expected:** metric_collection, anomaly (if any detected), insight (if any generated), possibly deploy, drift
- **Check:** Each event has non-null subject, source, ref_table, ref_id

### 6.2 Events API Endpoints
- `GET /api/events` → returns recent events, newest first
- `GET /api/events?event_type=metric_collection` → filtered to collection summaries
- `GET /api/events/subject/table_row_count` → events for that metric
- `GET /api/events/search?q=connection` → full-text search results
- `GET /api/events/subject/table_row_count/recurrence` → count, first/last seen
- **All endpoints require authentication**

### 6.3 Event-to-Run Linkage
- Query: `SELECT e.event_type, e.run_id, m.status FROM events e LEFT JOIN monitor_runs m ON e.run_id = m.id WHERE e.run_id IS NOT NULL LIMIT 5`
- **Expected:** Events from monitor loop have valid run_id linking to a completed monitor_run

### 6.4 Chat Investigation Events
- Ask a question in chat, then query: `SELECT * FROM events WHERE event_type = 'investigation' ORDER BY occurred_at DESC LIMIT 1`
- **Expected:** Event with source="chat", summary contains the question

### 6.5 Feedback Events
- Click a feedback button on an insight, then query: `SELECT * FROM events WHERE event_type = 'feedback' ORDER BY occurred_at DESC LIMIT 1`
- **Expected:** Event with source="user", summary contains the outcome

---

## Section 7: Alerting (Phase 1)

### 7.1 Alert Configuration
- **Check config:** at least one alert channel (ntfy / Slack / webhook)
  is configured with a valid topic/URL
- Query: `SELECT channel, severity, status, sent_at FROM alert_history ORDER BY sent_at DESC LIMIT 5`
- **Expected:** Alert records if any insights were generated

### 7.2 LLM Cost Tracking
- `GET /api/system/cost` → returns calls, total_tokens, cost_usd
- **Expected:** Non-zero values if LLM has been used

---

## Section 8: Data Retention & Housekeeping

### 8.1 Retention Running
- **Check server logs** for "Retention cleanup:" messages
- **Expected:** Periodic cleanup of old data

### 8.2 New Tables Included
- Verify retention covers: monitor_runs, insight_feedback, events
- Query old data counts if system has been running > retention period

### 8.3 Stale Run Cleanup
- Query: `SELECT COUNT(*) FROM monitor_runs WHERE status = 'running' AND started_at < datetime('now', '-1 hour')`
- **Expected:** 0 (stale runs should have been cleaned up on startup)

---

## Section 9: Contract Tests (External API Validation)

### 9.1 Railway GraphQL Contract
- Execute the SERVICE_METRICS_QUERY against Railway's actual API
- **Expected:** Response contains list of dicts with "measurement" and "values" keys
- **Expected:** measurement values include CPU_USAGE, MEMORY_USAGE_GB, etc.
- **Expected:** values entries have "ts" and "value" keys

### 9.2 Supabase Metrics API Contract
- Scrape `https://{ref}.supabase.co/customer/v1/privileged/metrics`
- **Expected:** Prometheus text format, parseable
- **Expected:** Contains node_cpu_seconds_total, node_memory_* metrics
- **Expected:** At least 50 metric families returned

### 9.3 Railway Project Query Contract
- Execute PROJECT_QUERY against Railway's actual API
- **Expected:** Response has project.services.edges and project.environments.edges

---

## Section 10: Browser Console & Frontend Quality

**REQUIRES CHROME DEVTOOLS MCP. NOT OPTIONAL.**
Use `read_console_messages(tabId, onlyErrors=true)` and
`read_network_requests(tabId, urlPattern="/api/")` after navigating
through all views and clicking all interactive elements.

### 10.1 Zero JavaScript Exceptions
- Open browser DevTools → Console tab
- Navigate through all dashboard views
- Click various buttons (lifecycle, feedback, chat)
- **Expected:** Zero errors. Warnings acceptable if third-party library.
- **Evidence required:** Screenshot of clean console or list of any errors found

### 10.2 Network Tab
- Monitor network requests during normal usage
- **Expected:** No failed requests (red entries) during normal operation
- **Expected:** API responses return 200, not 500/400/422

---

## Results Template

CC must fill in this template and include it in the report:

\`\`\`
SECTION 1: System Startup & Health
  1.1 Clean Start: [PASS/FAIL] — [evidence]
  1.2 Health Endpoints: [PASS/FAIL] — [response bodies]
  1.3 Authentication: [PASS/FAIL]

SECTION 2: Monitor Loop
  2.1 Collection Cycle: [PASS/FAIL] — [metric count from logs]
  2.2 Supabase pg_stat: [PASS/FAIL] — [metric names found]
  2.3 Supabase Prometheus: [PASS/FAIL] — [count, sample names]
  2.4 Railway Resources: [PASS/FAIL] — [metric names, labels]
  2.5 Monitor Runs: [PASS/FAIL] — [sample row from DB]
  2.6 Anomaly Detection: [PASS/FAIL] — [baseline sample]
  2.7 Change Detection: [PASS/FAIL] — [recent events]
  2.8 Discovery & Drift: [PASS/FAIL] — [snapshot fingerprints]

SECTION 3: Dashboard (BROWSER REQUIRED — N/A IS NOT ACCEPTABLE)
  3.1 Three-Zone Layout: [PASS/FAIL] — [screenshot or description]
  3.2 Discovery Feed Insights: [PASS/FAIL] — [screenshot]
  3.3 Lifecycle Actions: [PASS/FAIL] — [each action tested IN BROWSER]
  3.4 Feedback Buttons: [PASS/FAIL] — [DB record with user_id + visual confirmation]
  3.5 Recurrence Annotations: [PASS/FAIL/NOT YET VISIBLE]
  3.6 Static Dashboard: [PASS/FAIL]
  3.7 Number Formatting: [PASS/FAIL]

SECTION 4: Chat (BROWSER REQUIRED for 4.4, 4.5, 4.6)
  4.1 Primary (known-answer chat query): [PASS/FAIL] — [answer text, verified in BROWSER]
  4.2 Multi-Domain: [PASS/FAIL] — [domains hit]
  4.3 Business Context: [PASS/FAIL] — [SQL used]
  4.4 Session Persistence: [PASS/FAIL] — [tested multi-turn IN BROWSER]
  4.5 Session Expiry: [PASS/FAIL] — [tested IN BROWSER]
  4.6 Error Handling: [PASS/FAIL] — [tested IN BROWSER]
  4.7 Prompt Size: [PASS/FAIL] — [token count from logs]

SECTION 5: Code Intelligence
  5.1 GitHub Connector: [PASS/FAIL] — [status response]
  5.2 Semantic Facts: [PASS/FAIL] — [counts by source]
  5.3 Correction Detection: [PASS/FAIL]

SECTION 6: Events
  6.1 Events Emitted: [PASS/FAIL] — [counts by type]
  6.2 Events API: [PASS/FAIL] — [each endpoint tested]
  6.3 Run Linkage: [PASS/FAIL] — [sample linked event]
  6.4 Chat Events: [PASS/FAIL]
  6.5 Feedback Events: [PASS/FAIL]

SECTION 7: Alerting
  7.1 Alert Config: [PASS/FAIL]
  7.2 LLM Cost: [PASS/FAIL] — [cost response]

SECTION 8: Retention
  8.1 Running: [PASS/FAIL]
  8.2 New Tables: [PASS/FAIL]
  8.3 Stale Cleanup: [PASS/FAIL]

SECTION 9: Contract Tests
  9.1 Railway GraphQL: [PASS/FAIL] — [response shape]
  9.2 Supabase Metrics API: [PASS/FAIL] — [metric count]
  9.3 Railway Project Query: [PASS/FAIL]

SECTION 10: Browser (BROWSER REQUIRED — N/A IS NOT ACCEPTABLE)
  10.1 Console Errors: [PASS/FAIL] — [screenshot of DevTools console]
  10.2 Network Failures: [PASS/FAIL] — [screenshot of Network tab]

TOTAL: [X/Y PASS] — [list any failures]
\`\`\`

---

## When To Run This Plan

- **Mandatory:** After the current stabilization pass, before Step 2 begins
- **Recommended:** After every step completion (Step 2, 3, 4, etc.)
- **Recommended:** After every hotfix pass
- **On demand:** Whenever the system seems unstable or a user reports an issue

Any test that fails indicates a regression that must be fixed before
proceeding with new feature work.
