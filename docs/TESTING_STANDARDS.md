# Observibot — Testing Standards

**This document is mandatory reading for every CC implementation session.**
**No deliverable is complete until all three tiers pass.**

## Origin

This standard exists because a Railway GraphQL schema change broke metrics
collection silently. All 361 mock-based unit tests passed while the real
Railway API returned 400 errors on every single metrics call. The bug was
only discovered accidentally by reading server logs. Mock tests encode
assumptions — they cannot catch when the real world changes under you.

## Three-Tier Testing Requirement

### Tier 1: Unit Tests (pytest)

Standard pytest with mocks for fast feedback. Tests internal logic: parsing,
validation, entity extraction, transformation, error handling. Run after
every code change. **This is the baseline, not the finish line.**

Requirements:
- All existing tests must pass (zero regressions)
- Every new feature/fix must have dedicated tests
- Edge cases must be tested (null inputs, empty responses, malformed data)
- Error paths must be tested (what happens when X fails?)
- ruff must report zero lint errors
- Frontend must build clean (tsc --noEmit, npm run build)

### Tier 2: Integration Contract Tests (real API validation)

For every external API integration, a contract test makes ONE real call and
validates the response structure matches what the code expects.

Location: `tests/integration/`

Run command: `pytest tests/integration/ -v`

Rules:
- Use real credentials from environment variables
- Skip gracefully if credentials are missing (`@pytest.mark.skipif`)
- Validate field names, types, and structure — NOT specific values
- Must cover: Railway GraphQL, Supabase Metrics API, Supabase pg_stat,
  GitHub API (when enabled)
- When a new connector or external endpoint is added, a contract test
  must be added in the same deliverable

Contract tests catch:
- API schema changes (field renamed, type changed, field removed)
- Auth changes (new required headers, changed auth flow)
- Response format changes (JSON structure, Prometheus format)

### Tier 3: Live End-to-End Verification (MANDATORY)

CC must start the system, wait for monitoring cycles, and directly observe
the system doing what it is supposed to do. This is not optional. This is
not "nice to have." No deliverable is complete without this.

**CC must verify BOTH the backend AND the frontend.** Checking server logs
and running scripts is NOT sufficient. The dashboard at localhost:8080 must
be opened in a real browser and every UI feature must be visually verified.
"N/A — requires browser session" is NOT an acceptable result.

**Use the Chrome DevTools MCP tools for browser testing.** These are the
exact tools and sequence CC must use:

1. `Claude in Chrome:tabs_context_mcp` — get or create a tab group
2. `Claude in Chrome:tabs_create_mcp` — create a new tab
3. `Claude in Chrome:navigate(tabId, "http://localhost:8080")` — open dashboard
4. If login screen appears, STOP and ask the user to log in, then continue
5. `Claude in Chrome:computer(action="screenshot", tabId)` — take screenshot
   to verify the page loaded and see the layout
6. `Claude in Chrome:read_page(tabId)` — read the accessibility tree to verify
   elements exist (zones, buttons, inputs)
7. `Claude in Chrome:find(tabId, "chat input")` — find specific elements
8. `Claude in Chrome:computer(action="left_click", tabId, coordinate)` — click
   buttons (feedback pills, lifecycle actions, new conversation)
9. `Claude in Chrome:form_input(ref, tabId, value)` — type in chat input
10. `Claude in Chrome:read_console_messages(tabId, onlyErrors=true)` — check
    for JavaScript exceptions
11. `Claude in Chrome:read_network_requests(tabId, urlPattern="/api/")` — check
    for failed API calls

**Every browser verification step must include a screenshot as evidence.**
Use `computer(action="screenshot", save_to_disk=true)` and include the
path in the report.

**Steps CC must execute after every implementation:**

1. Start Observibot (`observibot run` or the dev startup command)
2. Wait for 1-2 complete monitoring cycles (5-10 minutes)
3. Check server logs for:
   - Zero errors/warnings related to the changes
   - Connector metrics collection succeeding (log lines with counts)
   - Events being emitted (if applicable)
   - Monitor runs completing with correct status

4. Test via the web dashboard (localhost:8080):
   - **THIS IS NOT OPTIONAL. DO NOT SKIP. DO NOT MARK AS N/A.**
   - Use Chrome DevTools MCP: `tabs_context_mcp` → `tabs_create_mcp` →
     `navigate(tabId, "http://localhost:8080")`
   - If login screen appears, ask the user to log in, then continue
   - Take a screenshot (`computer(action="screenshot")`) after login
   - Primary acceptance test: type a known-answer question in chat (e.g.
     a row count for a stable table) and verify the LLM-generated answer
     matches the expected value
   - Use `read_console_messages(tabId, onlyErrors=true)` for JS exceptions
   - Use `read_network_requests(tabId, urlPattern="/api/")` for failed calls
   - Verify all UI features changed in this deliverable work visually
   - **Attach screenshots as evidence in the report**
5. Test via API endpoints (curl or browser with auth cookie):
   - Hit every new/changed endpoint
   - Verify response structure and values
6. Report findings with specific evidence:
   - Include exact log lines (copy-paste, not paraphrase)
   - Include HTTP response bodies for API tests
   - Include any warnings or unexpected behavior
   - If ANYTHING unexpected is found, flag it and fix it before
     reporting the deliverable as complete

**What "complete" means:**
- All Tier 1 tests pass
- All Tier 2 contract tests pass (or skip cleanly if no credentials)
- All Tier 3 live verification steps completed with evidence
- CC report includes the actual log lines and API responses observed
- Any discrepancy between expected and actual behavior is resolved

**What "complete" does NOT mean:**
- "339 tests pass" with no live verification
- "Tests pass" when tests only use mocked responses
- "Should work" based on code inspection alone
- Reporting success without showing evidence
- Marking browser/UI tests as "N/A" or "requires browser session"
- Verifying only the backend while ignoring the frontend entirely
- Running scripts that test API endpoints but never opening the dashboard

## Regression Testing

Every change must be tested against the full existing system, not just the
new code. Regression means:
- Run the full pytest suite (not just new tests)
- Verify the primary acceptance test (known-answer chat question returns
  the expected value via the LLM, not the deterministic fallback)
- Verify at least one feature from each prior phase still works:
  - Discovery Feed shows insights (Phase 3)
  - Feedback buttons work (Step 0)
  - Events are being emitted (Step 1)
  - Session persistence across messages (Step 0)

## CC Report Requirements

Every CC implementation report must include:

1. **Unit test results:** count passed/failed/skipped, new test names
2. **Contract test results:** which ran, which skipped, any failures
3. **Live verification evidence:**
   - Server log excerpts showing successful operation
   - API response samples from new/changed endpoints
   - Dashboard screenshots or descriptions of observed behavior
   - Any errors, warnings, or unexpected behavior encountered
4. **Regression confirmation:** primary acceptance test result, prior
   feature spot-checks

A report that says "all tests pass" without live verification evidence
is incomplete and must be sent back for proper verification.

## External Architecture Reviews

External AI reviews (Gemini, Claude Code, Codex, etc.) are mandatory before
major milestones (phase completions, pre-Step-3 type boundaries). They
consistently find issues that internal testing and CC implementation miss.

Rules for crafting review prompts:
- Give reviewers full repo access and specific file paths to read
- Ask explicit questions (yes/no format forces concrete answers)
- Tell them to read the actual code, not trust the summary
- Ask about downstream impacts and unintended consequences
- Bias toward "fix it now" — don't defer things that will bite later

Review findings that surfaced real bugs in this project:
- Railway GraphQL schema change (silent 400s for weeks)
- 204k-token prompt overflow (silently retried 3×)
- React error boundary missing (one crash blanks entire dashboard)
- SQL sandbox schema-qualified bypass (auth.users accessible)
- Freshness key mismatch (semantic facts never reached LLM)
- Baseline contamination (current batch in own evaluation)
- Alphabetical schema sort dropping high-value analytics views

## Lessons Learned (Updated Periodically)

1. **Token budgets are safety nets, not quality filters.** The 204k→4.9k
   overcorrection fixed the overflow but starved the LLM. The real fix was
   selection quality (relevance ranking, FTS preservation), not tighter caps.

2. **Verify claims against source code.** A one-key mismatch
   (last_extraction_at vs last_index_time) silenced semantic fact injection
   for the entire project. CC never caught it. Always trace end-to-end.

3. **Acceptance tests should not use hardcoded production values.** Data
   changes (47→48 users). Use behavioral assertions instead.

4. **CC will skip browser testing unless forced.** Every CC prompt must
   include explicit Chrome DevTools MCP tool names and the instruction to
   STOP and ask the user to log in. "N/A — requires browser" is not acceptable.

5. **Silent failures are the most dangerous bugs.** Errors caught with
   log.debug() and swallowed with .catch(() => {}) create invisible breakage.
   Always surface errors to the user.

## Full System Verification Plan

After major deliverables (step completions, hotfix passes), CC must execute
the comprehensive verification plan at `docs/VERIFICATION_PLAN.md`. This
covers ALL features across all phases — not just the most recent changes.
It includes 10 sections, ~50 individual checks, and a results template that
must be filled in with evidence. Any failure blocks further feature work.


## Tier 0: Generality Firewall (MANDATORY for pattern-based fixes)

**This standard exists because Observibot is being validated against a
single live deployment (TaskGator) while being designed as a general
open-core product for any Postgres/Railway/Supabase application. Every
fix that looks generic can silently overfit to the patterns that
TaskGator happens to expose. Tier 0 is the guardrail that catches
overfitting BEFORE code is written.**

### When Tier 0 Applies

Tier 0 is mandatory whenever a change meets ANY of these conditions:

- Modifies anomaly detection, scoring, gating, or thresholds
- Modifies insight generation, fingerprinting, dedup, or recurrence logic
- Modifies prompts that the LLM uses to analyze customer data
- Adds or changes a schema-discovery heuristic (enum detection, column
  classification, table allowlist logic, label normalization)
- Adds or changes a data-quality safeguard (hallucination detector,
  redaction rules, fact validation)
- Is triggered by something observed in the live TaskGator deployment

If a change is purely infrastructural (a new connector protocol adapter,
a storage migration, a refactor that preserves behavior), Tier 0 is
optional but encouraged.

### The Three-Question Test (must be answered in writing in every CC prompt)

Before writing code, CC must answer each of these in the implementation
plan. If any answer is "no" or "unclear," the fix is not generic enough
and must be reconsidered — OR the work must be reclassified as a
customer-specific operational matter rather than an Observibot change.

1. **Portability:** Would this fix apply identically to a customer whose
   application has completely different tables, domain terminology,
   data patterns, and scale? If the fix depends on tables being named
   "users" or "orders" or on rows having a particular semantic, it is
   not portable.

2. **Identifier Hygiene:** Is the code free of any literal TaskGator
   strings — table names, column names, metric names, deployment IDs,
   domain-specific terminology — anywhere in source, tests, fixtures,
   or documentation? `grep -rE "(taskgator|task-gator|course|extraction)"`
   must return zero matches in the diff.

3. **Scale Invariance:** Are constants in the fix expressed as ratios,
   relative proportions, or pattern matches — not absolute values
   calibrated to TaskGator's current scale? A 2% threshold is scale-
   invariant. A "10 rows" threshold or "must have fewer than 200 tables"
   check is not.

### Synthetic Schema Fixtures (MANDATORY)

For any fix that passes the three-question test, CC must add at least
ONE test that exercises the fix against a synthetic schema *deliberately
unlike TaskGator*. This is NOT optional. A test that only uses TaskGator-
shaped data proves nothing about generality.

Synthetic fixtures live in `tests/fixtures/synthetic_schemas.py`. If the
file does not exist, create it. Each fixture is a small helper that
builds `SystemModel`, `MetricSnapshot`, `Anomaly`, or `SemanticFact`
objects representing a domain completely unrelated to TaskGator's
educational-content domain.

Maintain at least three reference domains in the fixtures module:

- **`ecommerce_schema()`** — orders, line_items, customers, inventory,
  shipments, returns. Typical patterns: high-cardinality orders table,
  soft-delete via archived_at, RLS on customer data, enum on
  order_status with values like pending/paid/shipped/refunded.

- **`medical_records_schema()`** — patients, encounters, diagnoses,
  prescriptions, providers. Typical patterns: strict RLS, hard
  foreign-key integrity, type enum with values like inpatient/
  outpatient/emergency, soft-delete via deleted_at with audit trail.

- **`event_stream_schema()`** — events, sessions, aggregates_hourly,
  aggregates_daily. Typical patterns: very high row counts
  (billions), numeric columns with units in the name (duration_ms,
  bytes_transferred), no soft-delete, time-partitioned tables,
  severity enum with values like debug/info/warn/error/fatal.

Every fixture should intentionally use values, sizes, and terminology
that have no overlap with TaskGator's. When a new pattern-based fix
is added, at least one of these synthetic domains must be in the test
suite for that fix.

### Test Structure for Pattern-Based Fixes

Every pattern-based fix must include:

1. A unit test using a TaskGator-shaped fixture (the pattern that
   surfaced the bug). This confirms the fix addresses the original
   case.

2. A unit test using at least one synthetic-domain fixture from the
   reference list above. This confirms the fix is not TaskGator-shaped.

3. A negative test: a scenario where the pattern does NOT match and
   the fix must not fire. This prevents the fix from becoming over-
   aggressive.

Example — for the MAD=0 relative-floor gate (Step 3.2):

- Positive/TaskGator: flat-history metric of 20,000 rows, grew by 11;
  must NOT fire (11/20000 < 2%).
- Positive/synthetic: flat-history e-commerce orders of 500,000, grew
  by 100; must NOT fire (100/500000 < 2%).
- Positive/synthetic: flat-history medical patients of 50, grew by 10;
  must fire (10/50 = 20% > 2%).
- Negative: metric with non-zero MAD; relative floor must not apply at
  all (preserves existing MAD-based behavior).

### CC Prompt Requirements for Pattern-Based Fixes

Every CC implementation prompt for a pattern-based fix must include,
near the top, a section titled exactly `## Generality Firewall` that:

- States "This change is pattern-based and subject to Tier 0."
- Answers the three-question test in writing, specifically for this fix.
- Names which synthetic fixtures the tests will exercise.
- Includes the forbidden-string grep as part of the completion checklist:
  `git diff | grep -iE "(taskgator|task-gator|<other-customer-strings>)"`
  must return zero matches.

A CC prompt that omits the Generality Firewall section is malformed and
must be rejected before it is sent to the implementer.

### Worked Examples (from recent Step 3.2 fixes)

**Fix A: MAD=0 relative floor** (src/observibot/core/anomaly.py)
- Three-question test: ✓ portable (any flat-baseline high-magnitude
  metric), ✓ no customer strings, ✓ scale-invariant (ratio, not count).
- Synthetic coverage: e-commerce order counts, medical patient counts.
- Verdict: PASS — this was the standard's first real test case.

**Fix B: Stable anomaly_signature fingerprint** (src/observibot/core/models.py)
- Three-question test: ✓ portable (LLM non-determinism is universal),
  ✓ no customer strings, ✓ scale-invariant (hash of structural fields).
- Synthetic coverage: build two Anomaly sets with identical structural
  fields but different LLM-authored related_tables arrays; assert
  identical signatures.
- Verdict: PASS.

**Fix C: Direction-aware anomaly prompt** (src/observibot/agent/prompts.py)
- Three-question test: ✓ portable (all metrics can move in either
  direction), ✓ no customer strings, ✓ scale-invariant (linguistic
  guidance, no numeric threshold).
- Synthetic coverage: summarize_anomalies(Anomaly with direction="spike")
  must include "INCREASE"; Anomaly with direction="dip" must include
  "DECREASE". Works regardless of metric_name or domain.
- Verdict: PASS.

**Counter-example — what would FAIL Tier 0:**

Hypothetical fix: "detect soft-delete by looking for a `deleted_at`
column and adding WHERE `deleted_at IS NULL` to queries."
- Three-question test:
  - Portable? Partially — assumes soft-delete is marked by a specific
    column name. A customer using `removed_on` or `is_deleted` or a
    separate audit table would not be covered.
  - Customer strings? None directly, but the column-name list is a
    TaskGator-shaped assumption.
  - Scale-invariant? Yes.
- The fix as stated is narrow. To pass Tier 0 it must either:
  (a) document the column-name set as an explicit configurable policy
      (not a hidden heuristic), AND test with synthetic domains that
      use different soft-delete conventions; OR
  (b) detect soft-delete via metadata signals (comments, triggers,
      constraints) rather than column names alone.
- The current implementation uses (a) with a defined pattern list.
  Acceptable, but the list must be reviewed whenever a new customer
  onboards with different conventions.

### Why This Standard Exists

During the Step 3 verification and Step 3.2 detour, three classes of
bug surfaced that would each have shipped broken for every customer
except TaskGator if not for direct observation:

1. **Discovery silently overfit to `status/_status` columns.** The
   fix landed pattern-based, but initial coverage was 1 of 7 enum-
   candidate columns in the live schema. Generic in intent, narrow in
   effect — only caught because live verification compared actual
   DEFINITION fact counts against candidate column counts.

2. **Anomaly detector's MAD=0 path used an absolute 10-row floor.**
   Correct for TaskGator's 100–1000 row tables. Catastrophically
   spammy for a customer with 40,000-row tables. Fix: relative floor.

3. **Insight fingerprint hashed LLM-authored fields.** Worked for
   TaskGator because the LLM's variability was small enough that
   occasional duplicates were tolerable; would become a flood for
   any customer whose traffic produces more anomalies. Fix: signature
   from triggering anomalies.

The common pattern: "looks generic" is not "is generic." Tier 0 makes
the test explicit so the verdict is rendered before code, not after.

### Tier 0 Completion Checklist (include in every pattern-based CC report)

- [ ] Three-question test answered in writing, all three "yes"
- [ ] Synthetic fixture for at least one non-TaskGator domain added
- [ ] Negative test added
- [ ] `git diff | grep -iE "(taskgator|task-gator)"` returns zero matches
- [ ] No absolute thresholds that only make sense at TaskGator's scale
- [ ] If a constant was added, rationale documented as scale-invariant
  (e.g., "2% relative floor: smallest ratio that suppresses the
  observed false-positive class while preserving real signals")
