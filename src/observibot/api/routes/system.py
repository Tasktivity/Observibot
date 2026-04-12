"""System routes: health, status, cost."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from observibot import __version__
from observibot.api.deps import get_current_user, get_store
from observibot.api.schemas import CostResponse, HealthResponse, SystemStatusResponse
from observibot.core.store import Store

router = APIRouter(prefix="/api/system", tags=["system"])


@router.get("/health")
async def health() -> HealthResponse:
    return HealthResponse(status="ok", version=__version__)


@router.get("/status")
async def system_status(user: dict = Depends(get_current_user)) -> SystemStatusResponse:
    return SystemStatusResponse(status="ok", version=__version__)


@router.get("/cost")
async def cost(
    user: dict = Depends(get_current_user),
    store: Store = Depends(get_store),
) -> CostResponse:
    summary = await store.get_llm_usage_summary()
    return CostResponse(**summary)
