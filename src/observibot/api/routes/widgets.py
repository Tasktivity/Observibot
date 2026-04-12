"""Widget CRUD routes for the dashboard."""
from __future__ import annotations

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, status

from observibot.api.deps import get_current_user, get_store
from observibot.api.schemas import (
    BatchLayoutUpdate,
    WidgetCreate,
    WidgetResponse,
    WidgetUpdate,
)
from observibot.core.models import _new_id
from observibot.core.store import Store, dashboard_widgets

router = APIRouter(prefix="/api/widgets", tags=["widgets"])


def _utcnow_iso() -> str:
    from datetime import UTC, datetime
    return datetime.now(UTC).isoformat()


def _row_to_response(row) -> WidgetResponse:
    return WidgetResponse(
        id=row[0],
        user_id=row[1],
        widget_type=row[3],
        title=row[4] or "",
        config=row[5],
        layout=row[6],
        data_source=row[7],
        schema_version=row[8] or 1,
        pinned=bool(row[9]) if row[9] is not None else True,
        created_at=row[10],
        updated_at=row[11],
    )


@router.get("")
async def list_widgets(
    user: dict = Depends(get_current_user),
    store: Store = Depends(get_store),
) -> list[WidgetResponse]:
    async with store.engine.begin() as conn:
        result = await conn.execute(
            sa.select(dashboard_widgets)
            .where(dashboard_widgets.c.user_id == user["id"])
            .order_by(dashboard_widgets.c.created_at.desc())
        )
        rows = result.fetchall()
    return [_row_to_response(r) for r in rows]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_widget(
    widget: WidgetCreate,
    user: dict = Depends(get_current_user),
    store: Store = Depends(get_store),
) -> WidgetResponse:
    widget_id = _new_id()
    now = _utcnow_iso()
    async with store.engine.begin() as conn:
        await conn.execute(
            dashboard_widgets.insert().values(
                id=widget_id,
                user_id=user["id"],
                tenant_id=1,
                widget_type=widget.widget_type,
                title=widget.title,
                config=widget.config,
                layout=widget.layout,
                data_source=widget.data_source,
                schema_version=1,
                pinned=True,
                created_at=now,
                updated_at=now,
            )
        )
    return WidgetResponse(
        id=widget_id,
        user_id=user["id"],
        widget_type=widget.widget_type,
        title=widget.title,
        config=widget.config,
        layout=widget.layout,
        data_source=widget.data_source,
        created_at=now,
        updated_at=now,
    )


@router.patch("/layout")
async def batch_update_layout(
    batch: BatchLayoutUpdate,
    user: dict = Depends(get_current_user),
    store: Store = Depends(get_store),
) -> dict:
    async with store.engine.begin() as conn:
        for item in batch.items:
            await conn.execute(
                dashboard_widgets.update()
                .where(dashboard_widgets.c.id == item.id)
                .where(dashboard_widgets.c.user_id == user["id"])
                .values(
                    layout={"x": item.x, "y": item.y, "w": item.w, "h": item.h},
                    updated_at=_utcnow_iso(),
                )
            )
    return {"updated": len(batch.items)}


@router.patch("/{widget_id}")
async def update_widget(
    widget_id: str,
    update: WidgetUpdate,
    user: dict = Depends(get_current_user),
    store: Store = Depends(get_store),
) -> WidgetResponse:
    values: dict = {"updated_at": _utcnow_iso()}
    if update.title is not None:
        values["title"] = update.title
    if update.config is not None:
        values["config"] = update.config
    if update.layout is not None:
        values["layout"] = update.layout
    if update.data_source is not None:
        values["data_source"] = update.data_source
    if update.pinned is not None:
        values["pinned"] = update.pinned

    async with store.engine.begin() as conn:
        result = await conn.execute(
            dashboard_widgets.update()
            .where(dashboard_widgets.c.id == widget_id)
            .where(dashboard_widgets.c.user_id == user["id"])
            .values(**values)
        )
        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail="Widget not found")

        result = await conn.execute(
            sa.select(dashboard_widgets).where(dashboard_widgets.c.id == widget_id)
        )
        row = result.fetchone()

    return _row_to_response(row)


@router.delete("/{widget_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_widget(
    widget_id: str,
    user: dict = Depends(get_current_user),
    store: Store = Depends(get_store),
):
    async with store.engine.begin() as conn:
        result = await conn.execute(
            dashboard_widgets.delete()
            .where(dashboard_widgets.c.id == widget_id)
            .where(dashboard_widgets.c.user_id == user["id"])
        )
        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail="Widget not found")
