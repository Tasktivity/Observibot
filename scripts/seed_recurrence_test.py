"""One-shot seed script: attach recurrence_context to a recent insight.

Used for Tier 3 browser regression of Step 3 recurrence badge rendering.
"""
from __future__ import annotations

import asyncio
import json

import sqlalchemy as sa

from observibot.core.store import Store, insights_table


async def seed() -> None:
    store = Store("data/observibot.db")
    await store.connect()
    try:
        insights = await store.get_recent_insights(limit=1)
        if not insights:
            print("No insights available to seed. Exiting.")
            return
        insight = insights[0]
        recurrence = {
            "count": 7,
            "first_seen": "2026-03-18T09:15:00+00:00",
            "last_seen": "2026-04-14T09:22:00+00:00",
            "common_hours": [9, 10, 14],
        }
        async with store.engine.begin() as conn:
            await conn.execute(
                sa.update(insights_table)
                .where(insights_table.c.id == insight.id)
                .values(recurrence_context=json.dumps(recurrence))
            )
        print(f"Seeded insight {insight.id} with recurrence_context count=7")
    finally:
        await store.close()


if __name__ == "__main__":
    asyncio.run(seed())
