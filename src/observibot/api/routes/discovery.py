"""Discovery feed routes including SSE."""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from observibot.api.deps import get_current_user, get_store
from observibot.api.schemas import InsightResponse
from observibot.core.store import Store

router = APIRouter(prefix="/api/discovery", tags=["discovery"])


@router.get("/feed")
async def discovery_feed(
    request: Request,
    user: dict = Depends(get_current_user),
    store: Store = Depends(get_store),
):
    """SSE stream of recent insights."""
    async def event_generator():
        seen_ids: set[str] = set()
        insights = await store.get_recent_insights(limit=50)
        for i in insights:
            seen_ids.add(i.id)
            data = InsightResponse(
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
            yield f"data: {data.model_dump_json()}\n\n"

        while True:
            if await request.is_disconnected():
                break
            await asyncio.sleep(5)
            new_insights = await store.get_recent_insights(limit=10)
            for i in new_insights:
                if i.id not in seen_ids:
                    seen_ids.add(i.id)
                    data = InsightResponse(
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
                    yield f"data: {data.model_dump_json()}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.get("/summary")
async def discovery_summary(
    user: dict = Depends(get_current_user),
    store: Store = Depends(get_store),
) -> dict:
    """Return a summary of the monitored system for bootstrap display."""
    model = await store.get_latest_system_snapshot()
    ctx = await store.get_all_business_context()
    tables = len(model.tables) if model else 0
    relationships = len(model.relationships) if model else 0
    services = len(model.services) if model else 0
    return {
        "tables": tables,
        "relationships": relationships,
        "services": services,
        "app_description": ctx.get("summary", ""),
        "app_type": ctx.get("app_type", ""),
    }


@router.get("/model")
async def discovery_model(
    user: dict = Depends(get_current_user),
    store: Store = Depends(get_store),
) -> dict:
    """Return the latest SystemModel summary."""
    model = await store.get_latest_system_snapshot()
    if model is None:
        return {"tables": 0, "relationships": 0, "services": 0, "fingerprint": None}
    return {
        "tables": len(model.tables),
        "relationships": len(model.relationships),
        "services": len(model.services),
        "fingerprint": model.fingerprint,
        "created_at": model.created_at.isoformat(),
    }
