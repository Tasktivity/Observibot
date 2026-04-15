"""Agent Memory Inspector API — read/edit the agent's knowledge stores.

Exposes four families of endpoints:

  * ``/api/knowledge/facts`` — CRUD over ``semantic_facts``
  * ``/api/knowledge/context`` — read-only business_context key/values
  * ``/api/knowledge/feedback-summary`` — aggregate recent insight feedback
  * ``/api/knowledge/stats`` — counts across all stores + code-intel freshness

Write operations (PATCH/DELETE on facts) are deliberately scoped small: we
don't expose bulk delete or import/export. Those are future work if the tab
turns out to be heavily used for corrections.
"""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, status

from observibot.api.deps import get_current_user, get_store
from observibot.api.schemas import (
    BusinessContextEntry,
    FactUpdateRequest,
    FeedbackSummaryResponse,
    KnowledgeStatsResponse,
    SemanticFactResponse,
)
from observibot.core.store import Store, insight_feedback, insights_table

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])


def _require_admin(user: dict) -> None:
    """Raise 403 unless the current user has the admin flag.

    Knowledge mutations rewrite what the agent believes — we gate them behind
    ``is_admin`` so a single compromised or curious account can't rewrite the
    entire 1k-fact corpus for every other user of the same deployment.
    """
    if not user.get("is_admin", False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required to modify agent knowledge",
        )


async def _emit_knowledge_event(
    store: Store,
    fact_id: str,
    user: dict,
    action: str,
) -> None:
    """Best-effort audit trail for knowledge mutations."""
    try:
        await store.emit_event(
            event_type="knowledge_edit",
            source="user",
            subject=f"fact:{fact_id}",
            ref_table="semantic_facts",
            ref_id=fact_id,
            summary=f"User {user.get('id', 'unknown')} {action} fact {fact_id}",
        )
    except Exception as exc:
        log.debug("Failed to emit knowledge_edit event: %s", exc)


# ---------- Semantic Facts ----------


@router.get("/facts")
async def list_facts(
    source: str | None = Query(default=None),
    fact_type: str | None = Query(default=None),
    active_only: bool = Query(default=True),
    search: str | None = Query(default=None),
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
    user: dict = Depends(get_current_user),
    store: Store = Depends(get_store),
) -> list[SemanticFactResponse]:
    """List semantic facts with optional filters and FTS search."""
    rows = await store.get_semantic_facts_filtered(
        source=source,
        fact_type=fact_type,
        active_only=active_only,
        search=search,
        limit=limit,
        offset=offset,
    )
    return [SemanticFactResponse(**row) for row in rows]


@router.patch("/facts/{fact_id}")
async def update_fact(
    fact_id: str,
    body: FactUpdateRequest,
    user: dict = Depends(get_current_user),
    store: Store = Depends(get_store),
) -> SemanticFactResponse:
    """Deactivate/reactivate or edit a fact's claim/confidence."""
    _require_admin(user)
    updated = await store.update_semantic_fact(
        fact_id,
        is_active=body.is_active,
        claim=body.claim,
        confidence=body.confidence,
    )
    if updated is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Fact {fact_id} not found",
        )
    if body.is_active is False:
        action = "deactivated"
    elif body.is_active is True:
        action = "reactivated"
    else:
        action = "edited"
    await _emit_knowledge_event(store, fact_id, user, action)
    return SemanticFactResponse(**updated)


@router.delete("/facts/{fact_id}")
async def delete_fact(
    fact_id: str,
    user: dict = Depends(get_current_user),
    store: Store = Depends(get_store),
) -> dict:
    """Permanently remove a fact. Prefer PATCH is_active=false for reversibility."""
    _require_admin(user)
    ok = await store.delete_semantic_fact(fact_id)
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Fact {fact_id} not found",
        )
    await _emit_knowledge_event(store, fact_id, user, "deleted")
    return {"id": fact_id, "deleted": True}


# ---------- Business Context ----------


@router.get("/context")
async def list_business_context(
    user: dict = Depends(get_current_user),
    store: Store = Depends(get_store),
) -> list[BusinessContextEntry]:
    """Dump all business context key/value pairs (read-only)."""
    ctx = await store.get_all_business_context()
    entries: list[BusinessContextEntry] = []
    for k, v in ctx.items():
        if isinstance(v, str):
            display = v
        else:
            try:
                display = json.dumps(v, indent=2, default=str)
            except Exception:
                display = str(v)
        entries.append(BusinessContextEntry(key=k, value=display))
    return entries


# ---------- Feedback Summary ----------


@router.get("/feedback-summary")
async def feedback_summary(
    days: int = Query(default=30, le=90),
    limit: int = Query(default=20, le=100),
    user: dict = Depends(get_current_user),
    store: Store = Depends(get_store),
) -> FeedbackSummaryResponse:
    """Aggregate recent feedback + list most-recent entries.

    ``days`` bounds both the aggregate counts and the recent list. A small
    join against ``insights`` surfaces titles so the UI doesn't have to make
    a second round-trip per entry.
    """
    import sqlalchemy as sa

    cutoff = datetime.now(UTC) - timedelta(days=days)

    async with store.engine.begin() as conn:
        # Aggregate by outcome within the window
        agg_result = await conn.execute(
            sa.select(
                insight_feedback.c.outcome,
                sa.func.count().label("n"),
            )
            .where(insight_feedback.c.created_at >= cutoff.isoformat())
            .group_by(insight_feedback.c.outcome)
        )
        by_outcome = {row[0]: row[1] for row in agg_result.fetchall()}

        # Recent list with insight titles
        recent_result = await conn.execute(
            sa.select(
                insight_feedback.c.id,
                insight_feedback.c.insight_id,
                insight_feedback.c.outcome,
                insight_feedback.c.note,
                insight_feedback.c.created_at,
                insights_table.c.title,
            )
            .select_from(
                insight_feedback.outerjoin(
                    insights_table,
                    insight_feedback.c.insight_id == insights_table.c.id,
                )
            )
            .where(insight_feedback.c.created_at >= cutoff.isoformat())
            .order_by(insight_feedback.c.created_at.desc())
            .limit(limit)
        )
        recent_rows = [
            {
                "id": r[0],
                "insight_id": r[1],
                "outcome": r[2],
                "note": r[3],
                "created_at": r[4],
                "insight_title": r[5] or "(insight deleted)",
            }
            for r in recent_result.fetchall()
        ]

    total = sum(by_outcome.values())
    return FeedbackSummaryResponse(
        total=total,
        since_days=days,
        by_outcome=by_outcome,
        recent=recent_rows,
    )


# ---------- Knowledge Stats ----------


@router.get("/stats")
async def knowledge_stats(
    user: dict = Depends(get_current_user),
    store: Store = Depends(get_store),
) -> KnowledgeStatsResponse:
    """One-shot overview for the Agent Memory dashboard header."""
    stats = await store.get_knowledge_stats()
    # Code intelligence freshness — lives in a separate service, small call.
    try:
        from observibot.core.code_intelligence.service import CodeKnowledgeService
        svc = CodeKnowledgeService(store)
        freshness = await svc.get_freshness_status()
    except Exception as exc:
        log.debug("Code intelligence freshness lookup failed: %s", exc)
        freshness = {
            "status": "unavailable",
            "last_indexed_commit": None,
            "last_index_time": None,
        }

    return KnowledgeStatsResponse(
        total_facts=stats["total_facts"],
        active_facts=stats["active_facts"],
        inactive_facts=stats["inactive_facts"],
        facts_by_source=stats["facts_by_source"],
        facts_by_type=stats["facts_by_type"],
        total_feedback=stats["total_feedback"],
        feedback_by_outcome=stats["feedback_by_outcome"],
        total_events=stats["total_events"],
        code_intelligence_status=freshness.get("status", "unavailable"),
        last_indexed_commit=freshness.get("last_indexed_commit"),
        last_index_time=freshness.get("last_index_time"),
    )
