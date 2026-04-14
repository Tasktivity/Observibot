"""Events envelope routes — queryable observation timeline."""
from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query

from observibot.api.deps import get_current_user, get_store
from observibot.core.store import Store

router = APIRouter(prefix="/api/events", tags=["events"])


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


@router.get("")
async def list_events(
    event_type: str | None = Query(default=None),
    subject: str | None = Query(default=None),
    agent: str | None = Query(default=None),
    since: str | None = Query(default=None),
    until: str | None = Query(default=None),
    limit: int = Query(default=50, le=200),
    user: dict = Depends(get_current_user),
    store: Store = Depends(get_store),
) -> list[dict]:
    """List events with optional filters. Newest first."""
    return await store.get_events(
        event_type=event_type,
        subject=subject,
        agent=agent,
        since=_parse_iso(since),
        until=_parse_iso(until),
        limit=limit,
    )


@router.get("/subject/{subject}")
async def events_for_subject(
    subject: str,
    event_type: str | None = Query(default=None),
    limit: int = Query(default=20, le=100),
    user: dict = Depends(get_current_user),
    store: Store = Depends(get_store),
) -> list[dict]:
    """Get recent events for a specific metric/table/service."""
    return await store.get_events(
        subject=subject, event_type=event_type, limit=limit,
    )


@router.get("/subject/{subject}/recurrence")
async def subject_recurrence(
    subject: str,
    event_type: str = Query(default="anomaly"),
    days: int = Query(default=30, le=90),
    user: dict = Depends(get_current_user),
    store: Store = Depends(get_store),
) -> dict:
    """Recurrence stats: count, first_seen, last_seen, common_hours."""
    result = await store.get_event_recurrence_summary(
        subject=subject, event_type=event_type, days=days,
    )
    return result or {"count": 0, "first_seen": None, "last_seen": None, "common_hours": []}


@router.get("/search")
async def search_events_endpoint(
    q: str = Query(..., min_length=2),
    limit: int = Query(default=10, le=50),
    user: dict = Depends(get_current_user),
    store: Store = Depends(get_store),
) -> list[dict]:
    """Full-text search over event summaries."""
    return await store.search_events(query=q, limit=limit)


@router.get("/timeline")
async def event_timeline(
    since: str = Query(...),
    until: str | None = Query(default=None),
    user: dict = Depends(get_current_user),
    store: Store = Depends(get_store),
) -> list[dict]:
    """Raw timeline view — all events in a time window."""
    return await store.get_events(
        since=_parse_iso(since),
        until=_parse_iso(until),
        limit=200,
    )
