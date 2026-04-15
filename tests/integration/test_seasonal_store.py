"""Tier 2 store-level tests for seasonal baselines.

These exercise the SQLAlchemy path against a real SQLite file, catching
dialect/dispatch bugs that the pure-Python Tier 1 tests can't surface.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import sqlalchemy as sa

from observibot.core.store import Store, seasonal_baselines

pytestmark = pytest.mark.asyncio


async def test_bulk_upsert_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "store.db"
    async with Store(path) as store:
        row = {
            "metric_name": "m",
            "connector_name": "c",
            "labels_key": "",
            "hour_of_week": 10,
            "samples_json": json.dumps([1.0, 2.0, 3.0]),
            "sample_count": 3,
            "weeks_observed": 1,
            "last_week": "2026-W15",
            "median": 2.0,
            "mad": 1.0,
        }
        assert await store.bulk_upsert_seasonal_baselines([row]) == 1

        # Second write to the same PK should UPDATE, not insert a duplicate.
        row2 = {
            **row,
            "samples_json": json.dumps([2.0, 3.0, 4.0]),
            "sample_count": 3,
            "weeks_observed": 2,
            "last_week": "2026-W16",
            "median": 3.0,
        }
        await store.bulk_upsert_seasonal_baselines([row2])

        async with store.engine.begin() as conn:
            count = (await conn.execute(
                sa.select(sa.func.count()).select_from(seasonal_baselines)
            )).scalar()
        assert count == 1

        got = await store.fetch_seasonal_buckets([("m", "c", "", 10)])
        ((_k, state),) = got.items()
        assert state["weeks_observed"] == 2
        assert state["samples"] == [2.0, 3.0, 4.0]
        assert state["last_week"] == "2026-W16"


async def test_fetch_seasonal_buckets_batch_size_200(tmp_path: Path) -> None:
    path = tmp_path / "store.db"
    async with Store(path) as store:
        rows = []
        for i in range(200):
            rows.append({
                "metric_name": f"m{i}",
                "connector_name": "c",
                "labels_key": "",
                "hour_of_week": i % 168,
                "samples_json": json.dumps([float(i)]),
                "sample_count": 1,
                "weeks_observed": 1,
                "last_week": "2026-W15",
                "median": float(i),
                "mad": 0.0,
            })
        await store.bulk_upsert_seasonal_baselines(rows)

        keys = [(r["metric_name"], "c", "", r["hour_of_week"]) for r in rows]
        fetched = await store.fetch_seasonal_buckets(keys)
        assert len(fetched) == 200


async def test_get_seasonal_baselines_for_hour_uses_index(tmp_path: Path) -> None:
    """EXPLAIN QUERY PLAN must show an index lookup, not a full table scan."""
    path = tmp_path / "store.db"
    async with Store(path) as store:
        rows = [
            {
                "metric_name": f"m{i}",
                "connector_name": "c",
                "labels_key": "",
                "hour_of_week": 42,
                "samples_json": json.dumps([1.0]),
                "sample_count": 1,
                "weeks_observed": 4,
                "last_week": "2026-W15",
                "median": 1.0,
                "mad": 0.0,
            }
            for i in range(100)
        ]
        await store.bulk_upsert_seasonal_baselines(rows)

        async with store.engine.begin() as conn:
            plan = (await conn.execute(sa.text(
                "EXPLAIN QUERY PLAN SELECT * FROM seasonal_baselines "
                "WHERE hour_of_week = 42 AND weeks_observed >= 4"
            ))).fetchall()
        plan_text = " | ".join(str(r) for r in plan).lower()
        assert "idx_seasonal_how" in plan_text, (
            f"Expected idx_seasonal_how in plan, got: {plan_text}"
        )
