"""Metrics routes."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Query

from observibot.api.deps import get_current_user, get_store
from observibot.api.schemas import MetricResponse
from observibot.core.store import Store

router = APIRouter(prefix="/api/metrics", tags=["metrics"])


@router.get("/recent")
async def recent_metrics(
    limit: int = Query(default=100, le=1000),
    user: dict = Depends(get_current_user),
    store: Store = Depends(get_store),
) -> list[MetricResponse]:
    metrics = await store.get_metrics(limit=limit)
    return [
        MetricResponse(
            id=m.id,
            connector_name=m.connector_name,
            metric_name=m.metric_name,
            value=m.value,
            labels=m.labels,
            collected_at=m.collected_at.isoformat(),
        )
        for m in metrics
    ]


@router.get("/{name}/history")
async def metric_history(
    name: str,
    hours: int = Query(default=24, le=168),
    user: dict = Depends(get_current_user),
    store: Store = Depends(get_store),
) -> list[MetricResponse]:
    since = datetime.now(UTC) - timedelta(hours=hours)
    metrics = await store.get_metrics(metric_name=name, since=since)
    return [
        MetricResponse(
            id=m.id,
            connector_name=m.connector_name,
            metric_name=m.metric_name,
            value=m.value,
            labels=m.labels,
            collected_at=m.collected_at.isoformat(),
        )
        for m in metrics
    ]
