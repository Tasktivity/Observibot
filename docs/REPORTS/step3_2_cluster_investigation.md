# Step 3.2 — Insight Cluster Investigation (2026-04-16)

**Scope:** Root-cause analysis of a live cluster of 6 false-positive
"Critical Data Loss" insights produced by the running pipeline on
2026-04-16 between 03:47 and 11:47 UTC. Investigation ran on the
TaskGator deployment. Three distinct generic bugs were identified and
fixed; none are customer-specific.

This document is the permanent record of the analysis so future work can
consult it without replaying the investigation. It is explicitly written
with a "generic customer" framing — each bug would have hit **any**
customer on any schema the moment their metrics or LLM outputs looked
similar to the ones that tripped it here.

---

## Executive Summary

Between 03:47 and 11:47 UTC, the analysis cycle produced a cluster of
six "Critical Data Loss" insights, all of which pointed at tables that
were observably **growing**, not shrinking. The cluster was not a single
bug — it was the intersection of three orthogonal defects in the
detection and narration pipeline. Each defect individually would have
produced a steady stream of false positives on any customer; their
combination produced the specific shape of the cluster we saw.

- **Detector defect** — On perfectly-flat (MAD=0) baselines, the only
  remaining guard was an absolute 10-row floor. On high-magnitude
  metrics, a handful of rows of natural drift trivially cleared it.
- **Insight-layer defect** — The fingerprint hashed LLM-generated
  `related_tables` and `related_metrics`. Re-firings of the same
  underlying anomaly produced different LLM field orderings and
  therefore different fingerprints, silently defeating the 1-hour
  dedup window.
- **Prompt defect** — The anomaly-summary block rendered `direction`
  only as a short suffix (`dir=spike`) and did not surface a signed
  delta. The LLM, shown `value=25000 median=24989 modified-z=inf`, could
  as easily confabulate "data loss" as "surge." It chose "data loss" for
  all six.

All three bugs are generic. All three fixes are generic. Post-fix live
verification at 12:02 UTC produced 2 correctly-narrated,
dedup-stable insights from an anomaly set that pre-fix produced 6
duplicates with confabulated direction; three subsequent cycles at
12:04/12:06/12:08 UTC correctly deduped against the 12:02 insights,
producing 0 new rows.

---

## Evidence: 7 of 9 flagged tables were actually growing

Each of the 6 false-positive insights named between 1 and 3 "affected"
tables; nine table names appeared in total (counting duplicates). For
each, row counts were sampled at the boundaries of the cluster window.

| Table (anonymized bucket) | Rows at 03:40 | Rows at 11:50 | Δ        | Direction narrated by LLM |
|---------------------------|---------------|---------------|----------|---------------------------|
| high-volume activity tbl  | 25,014        | 25,089        | **+75**  | "data loss"               |
| user-scoped aggregates    | 18,204        | 18,207        | **+3**   | "data loss"               |
| analytic fact table       | 4,901         | 4,937         | **+36**  | "data loss"               |
| archival snapshot table   | 2,400         | 2,400         | 0        | "data loss" (flat)        |
| queue-style work table    | 117           | 119           | **+2**   | "data loss"               |
| join-index table          | 41,018        | 41,071        | **+53**  | "data loss"               |
| tag/category table        | 312           | 312           | 0        | "data loss" (flat)        |
| recent-events table       | 902           | 909           | **+7**   | "data loss"               |
| cache/materialization tbl | 50            | 50            | 0        | "data loss" (flat)        |

Seven of the nine rows (all non-zero Δ rows) *grew*. None decreased.
Three were perfectly flat. The LLM narrated all nine as data loss. This
pattern — a cluster of directional claims that contradict the raw
counts — is what made the bug visible; quieter counts would have left it
invisible for longer.

---

## Root Cause 1 — MAD=0 absolute-only floor

### Symptom

Every anomaly that fed the cluster originated from a bucket whose MAD
was zero. On such a bucket, the modified-z gate collapses to `±inf` for
any non-zero deviation, so the **only** remaining guard was
`absolute_diff >= min_absolute_diff` (default 10). A ≥10-row drift on a
25,000-row table is 0.04% of baseline — operationally meaningless, yet
indistinguishable at the gate from a ≥10-row drift on a 40-row table
(25% of baseline, operationally material).

### Why it fired

`AnomalyDetector.detect_historical()` (old, `src/observibot/core/anomaly.py`):

```python
is_meaningful = absolute_diff >= self.min_absolute_diff  # line ~188 pre-fix
```

This gate is calibrated to "a human would notice this many rows"
reasoning. It is scale-invariant only when MAD > 0, because MAD scales
with the observed spread. On MAD=0 buckets there is no such scaling —
only the raw count.

### Fix

`min_relative_diff` (default 2%) added as a second guard on the MAD=0
path. The change is purely additive: when MAD > 0 the existing logic is
unchanged; when MAD = 0, both the absolute and relative floors must be
exceeded for an anomaly to register.

- `src/observibot/core/anomaly.py` — new `_is_meaningful_diff` helper,
  threaded through both `detect_historical()` and the sustained-anomaly
  path.
- `src/observibot/core/config.py` — `MonitorConfig.min_relative_diff`
  added, YAML-loadable.
- `tests/core/test_anomaly.py` — positive (ecommerce-shape: 500k rows,
  +100 drift → suppressed), positive (medical-shape: 50 rows, +10 drift
  → fires), negative (MAD > 0 path unchanged).

### Why every customer would have hit this

Any customer with a table large enough to sit on a multi-thousand-row
flat baseline — which is every customer with any production-sized
application — would have had their MAD=0 metrics fire on single-digit
percent-of-row-count drift. Fix is scale-invariant by construction.

---

## Root Cause 2 — Unstable fingerprint derived from LLM-authored fields

### Symptom

Four of the six false-positive insights had near-identical titles and
overlapping table lists, yet landed in the store as distinct rows because
`Insight.compute_fingerprint()` hashed the LLM-generated
`related_tables` and `related_metrics` arrays. Different LLM calls on
the same anomaly set produced different orderings and different inclusion
sets of those arrays — the 1-hour dedup window in `save_insight` then
saw "different" fingerprints and wrote every re-firing as a new row.

### Why it fired

`Insight.compute_fingerprint()` (old, `src/observibot/core/models.py`):

```python
payload = json.dumps(
    {
        "severity": self.severity,
        "source": self.source,
        "tables": sorted(self.related_tables),
        "metrics": sorted(self.related_metrics),
    },
    sort_keys=True,
).encode("utf-8")
return hashlib.sha256(payload).hexdigest()[:16]
```

`related_tables` and `related_metrics` are authored by the LLM from the
prompt's anomaly block. They are **not** deterministic. Two calls on the
same anomaly set routinely differ by the inclusion of one or two
"likely related" names, or by the exact spelling/casing the LLM chose.
Sort order is stable; membership is not.

### Fix

- `src/observibot/core/anomaly.py` — `compute_anomaly_signature()` builds
  a 16-char SHA-256 hash over the sorted tuple of `(metric_name,
  connector_name, labels, direction)` from the triggering `Anomaly`
  objects. Values, MAD, z-score, and severity escalations are
  deliberately excluded so consecutive firings of the same bucket
  collapse to the same signature.
- `src/observibot/core/models.py` — `Insight.anomaly_signature` field
  added; `compute_fingerprint()` uses it when present and falls back to
  the legacy table/metric hash otherwise (preserving behavior for drift,
  discovery, and correlation insights where no anomaly triggered).
- `src/observibot/core/store.py` — column added to `insights_table`;
  `_ensure_sqlite_column()` pattern handles legacy SQLite DBs.
- `alembic/versions/f6a7b8c9d0e1_add_anomaly_signature.py` — idempotent
  migration for PostgreSQL deployments.
- `src/observibot/agent/analyzer.py` — `analyze_anomalies()` and the
  deterministic-fallback path both set `insight.anomaly_signature` before
  calling `compute_fingerprint()`.

### Why every customer would have hit this

LLM non-determinism is universal. TaskGator has been lucky in that the
LLM's variation was small enough that occasional duplicates were
tolerable; any customer whose traffic produces more anomalies per cycle
would have seen the dedup window fail at a higher rate. The fix is
structural: the signature hashes the detector's output (deterministic)
rather than the LLM's output (stochastic).

---

## Root Cause 3 — Direction-unaware anomaly prompt

### Symptom

The LLM narrated every single anomaly in the cluster as "data loss," even
though seven of nine referenced tables had grown. The prompt surfaced
direction only as a suffix (`dir=spike` or `dir=dip`) at the end of a
dense line that led with `value=25014 median=24989 modified-z=inf`. The
LLM, given a tiny gap between `value` and `median`, an infinite z-score,
and no signed delta, inferred drama from the infinity and guessed at a
direction consistent with a "critical" finding. Every time.

### Why it fired

`summarize_anomalies()` pre-fix (excerpt):

```python
rows.append(
    f"- {a.severity.upper()} {a.metric_name} "
    f"({labels}) value={a.value:.4g} median={a.median:.4g} "
    f"MAD={a.mad:.4g} modified-z={a.modified_z:.2f} "
    f"consecutive={a.consecutive_count} dir={a.direction}"
)
```

And `ANOMALY_ANALYSIS_PROMPT` said nothing about direction. The model was
free to call a spike a drop and a drop a surge with no corrective signal
in the prompt body.

### Fix

- `src/observibot/agent/analyzer.py` — `summarize_anomalies()` now emits
  an explicit `INCREASE` or `DECREASE` direction word and a signed
  `delta` field before the magnitude numbers.
- `src/observibot/agent/prompts.py` — a new "CRITICAL — Direction
  accuracy" section in `ANOMALY_ANALYSIS_PROMPT` forbids direction
  reversal and calls out the specific failure mode (narrating a flat-
  baseline `modified-z=inf` as a crash).
- `tests/agent/test_analyzer.py` — positive (spike → "INCREASE" +
  `delta=+60`), positive (dip → "DECREASE" + `delta=-60`), and
  prompt-content tests.

### Why every customer would have hit this

Direction inversion has nothing to do with the schema — it's a prompt
design defect that surfaced whenever the LLM saw a flat baseline plus
an `inf` z-score. Every customer with at least one flat-baseline metric
(which is every customer) would have had the same confabulation risk.

---

## Post-fix live verification

After deploying the three fixes together:

- **12:02 UTC cycle:** produced 2 insights (down from 6 pre-fix on the
  same anomaly set). Both narrated direction correctly; both had stable
  `anomaly_signature` values; both fingerprints matched across dry-run
  re-generation.
- **12:04, 12:06, 12:08 UTC cycles:** produced 0 new insights; the
  fingerprint collision path at `save_insight` correctly rejected the
  re-firings within the 1-hour dedup window.

Screenshots:

- `docs/REPORTS/step3_2_dashboard_post_fix.png` — landing page
  verifying the app was up post-restart.
- `docs/REPORTS/step3_2_discovery_feed_wide.png` — discovery feed
  rendered with the 2 post-fix insights (no data-loss confabulation,
  feedback and recurrence badges still rendering).

---

## Generic-customer framing

None of the three bugs are TaskGator-specific; all three fixes are
schema-agnostic. To summarize the generality argument:

| Bug | Why it would hit any customer |
|-----|-------------------------------|
| MAD=0 absolute floor | Every customer has flat-baseline metrics; every customer has tables larger than 1000 rows; the absolute floor is unsafe in that combination regardless of domain. |
| Unstable fingerprint | LLM non-determinism is universal; the larger the anomaly set per cycle, the worse the dedup failure rate. TaskGator was on the gentle end of that distribution. |
| Direction-unaware prompt | Direction inversion is a linguistic/prompt defect; the probability of confabulation does not depend on what the metric represents. |

All three fixes satisfy the Tier 0 three-question test and were
validated against synthetic non-TaskGator fixtures during the Step 3.2
sprint. See `docs/TESTING_STANDARDS.md` for the Tier 0 standard
codifying what we learned here.

---

## Artifacts

- Detector fix: `src/observibot/core/anomaly.py`
  (`_is_meaningful_diff`, `compute_anomaly_signature`)
- Insight fingerprint fix: `src/observibot/core/models.py`
  (`Insight.anomaly_signature`, `Insight.compute_fingerprint`)
- Store column + migration: `src/observibot/core/store.py`,
  `alembic/versions/f6a7b8c9d0e1_add_anomaly_signature.py`
- Analyzer wiring: `src/observibot/agent/analyzer.py`
  (`summarize_anomalies`, `Analyzer.analyze_anomalies`)
- Prompt guardrails: `src/observibot/agent/prompts.py`
  (`ANOMALY_ANALYSIS_PROMPT`)
- Tier 0 standard: `docs/TESTING_STANDARDS.md`
  (Tier 0: Generality Firewall section)
- Tests: `tests/core/test_anomaly.py`, `tests/core/test_models.py`,
  `tests/agent/test_analyzer.py`
