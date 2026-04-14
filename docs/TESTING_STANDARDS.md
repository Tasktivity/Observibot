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

## Full System Verification Plan

After major deliverables (step completions, hotfix passes), CC must execute
the comprehensive verification plan at `docs/VERIFICATION_PLAN.md`. This
covers ALL features across all phases — not just the most recent changes.
It includes 10 sections, ~50 individual checks, and a results template that
must be filled in with evidence. Any failure blocks further feature work.
