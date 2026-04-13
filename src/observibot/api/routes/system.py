"""System routes: health, status, cost, monitor intervals."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from observibot import __version__
from observibot.api.deps import get_current_user, get_monitor_loop, get_store
from observibot.api.schemas import (
    CodeIntelligenceStatusResponse,
    CostResponse,
    HealthResponse,
    MonitorIntervalsResponse,
    MonitorIntervalsUpdate,
    SystemStatusResponse,
)
from observibot.core.store import Store

router = APIRouter(prefix="/api/system", tags=["system"])


@router.get("/health")
async def health() -> HealthResponse:
    return HealthResponse(status="ok", version=__version__)


@router.get("/status")
async def system_status(user: dict = Depends(get_current_user)) -> SystemStatusResponse:
    return SystemStatusResponse(status="ok", version=__version__)


@router.get("/code-intelligence-status")
async def code_intelligence_status(
    user: dict = Depends(get_current_user),
    store: Store = Depends(get_store),
) -> CodeIntelligenceStatusResponse:
    from observibot.core.code_intelligence.service import CodeKnowledgeService
    service = CodeKnowledgeService(store)
    status = await service.get_freshness_status()
    return CodeIntelligenceStatusResponse(**status)


@router.get("/cost")
async def cost(
    user: dict = Depends(get_current_user),
    store: Store = Depends(get_store),
) -> CostResponse:
    summary = await store.get_llm_usage_summary()
    return CostResponse(**summary)


@router.get("/intervals")
async def get_intervals(
    user: dict = Depends(get_current_user),
) -> MonitorIntervalsResponse:
    monitor = get_monitor_loop()
    if monitor is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Monitor loop not running",
        )
    cfg = monitor.config.monitor
    return MonitorIntervalsResponse(
        collection_interval_seconds=cfg.collection_interval_seconds,
        analysis_interval_seconds=cfg.analysis_interval_seconds,
    )


@router.patch("/intervals")
async def update_intervals(
    body: MonitorIntervalsUpdate,
    user: dict = Depends(get_current_user),
) -> MonitorIntervalsResponse:
    monitor = get_monitor_loop()
    if monitor is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Monitor loop not running",
        )
    cfg = monitor.config.monitor
    new_collection = body.collection_interval_seconds or cfg.collection_interval_seconds
    new_analysis = body.analysis_interval_seconds or cfg.analysis_interval_seconds

    if new_collection < 30:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="collection_interval_seconds must be >= 30",
        )
    if new_analysis < 60:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="analysis_interval_seconds must be >= 60",
        )
    if new_analysis <= new_collection:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="analysis_interval must be greater than collection_interval",
        )

    if body.collection_interval_seconds is not None:
        monitor.reschedule("collect", body.collection_interval_seconds)
    if body.analysis_interval_seconds is not None:
        monitor.reschedule("analyze", body.analysis_interval_seconds)
    return MonitorIntervalsResponse(
        collection_interval_seconds=cfg.collection_interval_seconds,
        analysis_interval_seconds=cfg.analysis_interval_seconds,
    )
