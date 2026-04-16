"""Step 3.4 diagnostic-activity observability endpoint.

Aggregates ``diagnostic_run`` / ``diagnostic_skipped`` /
``diagnostic_timeout`` events from the store so operators can answer
"did Observibot try to investigate this?" without reading logs.
O2 of Step 3.4 — the loop's own activity must itself be observable.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends

from observibot.api.deps import get_current_user, get_store
from observibot.core.store import Store

router = APIRouter(prefix="/api/diagnostics", tags=["diagnostics"])


_EVENT_TYPES = (
    "diagnostic_run",
    "diagnostic_skipped",
    "diagnostic_timeout",
)


@router.get("/recent")
async def recent_diagnostics(
    user: dict = Depends(get_current_user),
    store: Store = Depends(get_store),
) -> dict:
    """Return aggregate counts plus the 20 most-recent diagnostic events.

    Counts are derived from event summaries (no separate table) so the
    endpoint is purely read-side and cannot drift from the event stream.
    """
    del user  # auth gate only; not used for filtering

    since = datetime.now(UTC) - timedelta(hours=24)
    rows: list[dict] = []
    for ev_type in _EVENT_TYPES:
        rows.extend(
            await store.get_events(event_type=ev_type, since=since, limit=500)
        )
    rows.sort(key=lambda r: str(r.get("occurred_at") or ""), reverse=True)

    runs = sum(1 for r in rows if r["event_type"] == "diagnostic_run")
    skipped = sum(1 for r in rows if r["event_type"] == "diagnostic_skipped")
    timed_out = sum(1 for r in rows if r["event_type"] == "diagnostic_timeout")

    queries_issued = 0
    queries_succeeded = 0
    queries_rejected = 0
    for r in rows:
        if r["event_type"] != "diagnostic_run":
            continue
        summary = r.get("summary") or ""
        issued, succ, rej = _parse_run_summary(summary)
        queries_issued += issued
        queries_succeeded += succ
        queries_rejected += rej

    recent_runs = [
        {
            "run_id": r.get("run_id"),
            "occurred_at": r.get("occurred_at"),
            "event_type": r["event_type"],
            "summary": r.get("summary"),
        }
        for r in rows[:20]
    ]

    return {
        "last_24h": {
            "runs": runs,
            "skipped_cooldown": skipped,
            "timed_out": timed_out,
            "queries_issued": queries_issued,
            "queries_succeeded": queries_succeeded,
            "queries_rejected": queries_rejected,
        },
        "recent_runs": recent_runs,
    }


def _parse_run_summary(summary: str) -> tuple[int, int, int]:
    """Extract issued/succeeded/rejected counts from a diagnostic_run summary.

    Summary format produced by the monitor:
    ``"N diagnostic(s): X succeeded, Y rejected/errored"``. Missing or
    unparseable summaries simply contribute zeros — we never fail the
    endpoint on a stray format.
    """
    if not summary:
        return 0, 0, 0
    issued = succeeded = rejected = 0
    try:
        head, _, tail = summary.partition(" diagnostic(s):")
        issued = int(head.strip())
    except ValueError:
        return 0, 0, 0
    for piece in tail.split(","):
        parts = piece.strip().split()
        if len(parts) < 2:
            continue
        try:
            count = int(parts[0])
        except ValueError:
            continue
        label = parts[1].lower()
        if label.startswith("succeed"):
            succeeded = count
        elif label.startswith("reject"):
            rejected = count
    return issued, succeeded, rejected
