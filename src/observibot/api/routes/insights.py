"""Insights routes."""
from __future__ import annotations

import logging

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Query

from observibot.api.deps import get_current_user, get_store
from observibot.api.schemas import InsightFeedbackRequest, InsightFeedbackResponse, InsightResponse
from observibot.core.store import Store, insight_feedback

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/insights", tags=["insights"])

@router.get("")
async def list_insights(
    limit: int = Query(default=20, le=100),
    user: dict = Depends(get_current_user),
    store: Store = Depends(get_store),
) -> list[InsightResponse]:
    insights = await store.get_recent_insights(limit=limit)
    return [
        InsightResponse(
            id=i.id,
            severity=i.severity,
            title=i.title,
            summary=i.summary,
            details=i.details,
            recommended_actions=i.recommended_actions,
            related_metrics=i.related_metrics,
            related_tables=i.related_tables,
            confidence=i.confidence,
            source=i.source,
            is_hypothesis=i.is_hypothesis,
            created_at=i.created_at.isoformat(),
            recurrence_context=i.recurrence_context,
            evidence=i.evidence,
        )
        for i in insights
    ]


@router.patch("/{insight_id}/ack")
async def acknowledge_insight(
    insight_id: str,
    user: dict = Depends(get_current_user),
    store: Store = Depends(get_store),
) -> dict:
    """Persist user acknowledgement of an insight.

    Until this lands, the frontend was hiding the card client-side while the
    backend returned 200 without storing anything — a lie to the user. We now
    record an ``acknowledged`` feedback row and emit an event so the
    acknowledgement survives page refresh and leaves an audit trail.

    Idempotent: a second ack for the same (user, insight) pair does not create
    a duplicate row.
    """
    if await store.get_insight_by_id(insight_id) is None:
        raise HTTPException(status_code=404, detail="Insight not found")

    user_id = user.get("id")

    async with store.engine.begin() as conn:
        result = await conn.execute(
            sa.select(insight_feedback.c.id)
            .where(insight_feedback.c.insight_id == insight_id)
            .where(insight_feedback.c.user_id == user_id)
            .where(insight_feedback.c.outcome == "acknowledged")
            .limit(1)
        )
        already_acked = result.fetchone() is not None

    if already_acked:
        return {"id": insight_id, "acknowledged": True, "idempotent": True}

    record = await store.record_insight_feedback(
        insight_id=insight_id,
        user_id=user_id,
        outcome="acknowledged",
    )

    try:
        await store.emit_event(
            event_type="feedback",
            source="user",
            subject=insight_id,
            ref_table="insight_feedback",
            ref_id=str(record["id"]),
            summary="Insight acknowledged by user",
        )
    except Exception as exc:
        log.debug("Failed to emit acknowledge event: %s", exc)

    return {"id": insight_id, "acknowledged": True, "idempotent": False}


@router.post("/{insight_id}/feedback")
async def submit_insight_feedback(
    insight_id: str,
    body: InsightFeedbackRequest,
    user: dict = Depends(get_current_user),
    store: Store = Depends(get_store),
) -> InsightFeedbackResponse:
    existing = await store.get_insight_by_id(insight_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Insight not found")
    record = await store.record_insight_feedback(
        insight_id=insight_id,
        user_id=user["id"],
        outcome=body.outcome,
        note=body.note,
    )

    try:
        await store.emit_event(
            event_type="feedback",
            source="user",
            subject=insight_id,
            ref_table="insight_feedback",
            ref_id=str(record["id"]),
            summary=f"User marked insight as {body.outcome}",
        )
    except Exception as exc:
        log.debug("Failed to emit feedback event: %s", exc)

    return InsightFeedbackResponse(**record)
