"""Insights routes."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from observibot.api.deps import get_current_user, get_store
from observibot.api.schemas import InsightResponse
from observibot.core.store import Store

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
        )
        for i in insights
    ]


@router.patch("/{insight_id}/ack")
async def acknowledge_insight(
    insight_id: str,
    user: dict = Depends(get_current_user),
) -> dict:
    return {"id": insight_id, "acknowledged": True}
