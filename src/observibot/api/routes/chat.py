"""Chat routes — placeholder for sub-task 7."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from observibot.api.deps import get_current_user
from observibot.api.schemas import ChatRequest, ChatResponse

router = APIRouter(prefix="/api/chat", tags=["chat"])


@router.post("/query")
async def chat_query(
    req: ChatRequest,
    user: dict = Depends(get_current_user),
) -> ChatResponse:
    return ChatResponse(answer="Chat functionality will be implemented in sub-task 7.")
