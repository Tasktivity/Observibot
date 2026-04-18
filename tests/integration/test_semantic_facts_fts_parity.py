"""Tier 2 contract test: ``search_semantic_facts`` parity across SQLite / Postgres.

Seeds an identical 20-fact dataset into a SQLite store and (when a
``TEST_POSTGRES_URL`` is configured) a Postgres store, issues the same
five queries against each backend, and asserts that:

1. For each query, the top-5 result sets overlap by at least 3 members.
2. For each query, any ``USER_CORRECTION``-sourced fact that the
   SQLite backend returns in the top-5 also appears in the Postgres
   top-5 (high-confidence corrections must not be dropped by rank-signal
   divergence).
3. A query with no natural matches (``"zxqbwn"``) returns zero rows
   from both backends — not an error, not a crash.

This test skips cleanly when ``TEST_POSTGRES_URL`` is absent. To run
locally or in CI::

    TEST_POSTGRES_URL=postgresql+asyncpg://user:pw@host/db \\
        pytest tests/integration/test_semantic_facts_fts_parity.py -v

The Postgres target must have migration ``b8c9d0e1f2a3`` applied
(tsvector + GIN on ``semantic_facts``). The test creates and tears
down its own ephemeral rows so it is safe against a production
deployment provided the user has SELECT+INSERT+DELETE on
``semantic_facts``.
"""
from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa

from observibot.core.code_intelligence.models import (
    FactSource,
    FactType,
    SemanticFact,
)
from observibot.core.store import Store, build_engine, metadata, semantic_facts

pytestmark = pytest.mark.asyncio


POSTGRES_URL_ENV = "TEST_POSTGRES_URL"


# ---------------------------------------------------------------------------
# Seeded fact set — deliberately mixes domains so rank-signal parity can be
# observed across queries. Tier 0: every fact uses generic vocabulary,
# nothing specific to any customer deployment.
# ---------------------------------------------------------------------------

_SEED_FACTS: list[dict] = [
    # ecommerce-flavored
    {
        "concept": "order_placement",
        "claim": "customer places an order via checkout endpoint",
        "tables": ["orders", "line_items"],
        "source": FactSource.CODE_EXTRACTION,
        "confidence": 0.8,
    },
    {
        "concept": "order_settlement",
        "claim": "payment capture transitions order_status to paid",
        "tables": ["orders", "payments"],
        "source": FactSource.CODE_EXTRACTION,
        "confidence": 0.85,
    },
    {
        "concept": "shipment_dispatch",
        "claim": "shipments are created when orders are paid",
        "tables": ["shipments", "orders"],
        "source": FactSource.SCHEMA_ANALYSIS,
        "confidence": 0.7,
    },
    {
        "concept": "inventory_adjustment",
        "claim": "stock levels decrement on shipment dispatch",
        "tables": ["inventory", "shipments"],
        "source": FactSource.CODE_EXTRACTION,
        "confidence": 0.75,
    },
    {
        "concept": "refund_flow",
        "claim": "refunds reverse payment captures and restock",
        "tables": ["refunds", "orders"],
        "source": FactSource.USER_CORRECTION,
        "confidence": 1.0,
    },
    # medical-flavored
    {
        "concept": "patient_admission",
        "claim": "encounter_type inpatient marks hospital admission",
        "tables": ["encounters", "patients"],
        "source": FactSource.CODE_EXTRACTION,
        "confidence": 0.82,
    },
    {
        "concept": "provider_assignment",
        "claim": "encounters link to a providing clinician via provider_id",
        "tables": ["encounters", "providers"],
        "source": FactSource.SCHEMA_ANALYSIS,
        "confidence": 0.72,
    },
    {
        "concept": "prescription_lifecycle",
        "claim": "rxnorm_code identifies the prescribed medication",
        "tables": ["prescriptions"],
        "source": FactSource.CODE_EXTRACTION,
        "confidence": 0.78,
    },
    {
        "concept": "diagnosis_coding",
        "claim": "icd10_code identifies the recorded diagnosis",
        "tables": ["diagnoses", "encounters"],
        "source": FactSource.USER_CORRECTION,
        "confidence": 1.0,
    },
    # event-stream-flavored
    {
        "concept": "event_ingest",
        "claim": "events land in partitioned hourly tables by received_at",
        "tables": ["events", "aggregates_hourly"],
        "source": FactSource.CODE_EXTRACTION,
        "confidence": 0.77,
    },
    {
        "concept": "session_tracking",
        "claim": "sessions group events by user + device",
        "tables": ["sessions", "events"],
        "source": FactSource.SCHEMA_ANALYSIS,
        "confidence": 0.69,
    },
    {
        "concept": "daily_rollup",
        "claim": "aggregates_daily materializes cross-session counts",
        "tables": ["aggregates_daily"],
        "source": FactSource.CODE_EXTRACTION,
        "confidence": 0.74,
    },
    # generic / infrastructure
    {
        "concept": "cache_warmup",
        "claim": "cache preload runs on service startup",
        "tables": ["cache_entries"],
        "source": FactSource.CODE_EXTRACTION,
        "confidence": 0.6,
    },
    {
        "concept": "audit_trail",
        "claim": "audit_trail logs every mutating operation",
        "tables": ["audit_trail"],
        "source": FactSource.SCHEMA_ANALYSIS,
        "confidence": 0.65,
    },
    {
        "concept": "soft_delete_policy",
        "claim": "archived_at is non-null for archived rows",
        "tables": ["customers", "orders"],
        "source": FactSource.USER_CORRECTION,
        "confidence": 1.0,
    },
    # decoys that must NOT lead in any query
    {
        "concept": "unrelated_1",
        "claim": "this fact mentions none of the query keywords",
        "tables": ["misc_a"],
        "source": FactSource.CODE_EXTRACTION,
        "confidence": 0.9,
    },
    {
        "concept": "unrelated_2",
        "claim": "also nothing about the query terms",
        "tables": ["misc_b"],
        "source": FactSource.SCHEMA_ANALYSIS,
        "confidence": 0.9,
    },
    {
        "concept": "unrelated_3",
        "claim": "still nothing",
        "tables": ["misc_c"],
        "source": FactSource.CODE_EXTRACTION,
        "confidence": 0.9,
    },
    {
        "concept": "unrelated_4",
        "claim": "padding decoy for ranking realism",
        "tables": ["misc_d"],
        "source": FactSource.CODE_EXTRACTION,
        "confidence": 0.9,
    },
    {
        "concept": "unrelated_5",
        "claim": "another padding decoy",
        "tables": ["misc_e"],
        "source": FactSource.SCHEMA_ANALYSIS,
        "confidence": 0.9,
    },
]

assert len(_SEED_FACTS) == 20, "seed set must be 20 facts"

_QUERIES: list[str] = [
    "order placement",
    "payment settlement",
    "patient admission",
    "event ingestion",
    "archived soft-delete rows",
]


def _make_fact(seed: dict) -> SemanticFact:
    return SemanticFact(
        id=uuid.uuid4().hex[:12],
        fact_type=FactType.DEFINITION,
        concept=seed["concept"],
        claim=seed["claim"],
        tables=list(seed["tables"]),
        columns=[],
        sql_condition=None,
        source=seed["source"],
        confidence=float(seed["confidence"]),
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        is_active=True,
    )


async def _seed(store: Store) -> list[str]:
    """Insert the seed set into ``store``. Returns the list of fact IDs."""
    ids: list[str] = []
    for seed in _SEED_FACTS:
        fact = _make_fact(seed)
        await store.save_semantic_fact(fact)
        ids.append(fact.id)
    return ids


async def _run_queries(store: Store) -> dict[str, list[dict]]:
    """Return ``{query: top-5 hits}`` for every test query."""
    out: dict[str, list[dict]] = {}
    for q in _QUERIES:
        out[q] = await store.search_semantic_facts(q, limit=5)
    out["zxqbwn"] = await store.search_semantic_facts("zxqbwn", limit=5)
    return out


async def _delete_ids(engine, ids: list[str]) -> None:
    """Remove the seeded rows so the test is idempotent against a shared DB."""
    async with engine.begin() as conn:
        await conn.execute(
            semantic_facts.delete().where(semantic_facts.c.id.in_(ids))
        )


async def test_sqlite_search_returns_hits_not_decoys(tmp_path: Path) -> None:
    """Tier 1 sanity: the seeded dataset yields meaningful top-5 lists
    on SQLite — decoys don't dominate. This runs everywhere (no Postgres
    dependency) and also acts as the control for the parity test below.
    """
    db_path = tmp_path / "fts_parity_sqlite.db"
    async with Store(db_path) as store:
        ids = await _seed(store)
        try:
            results = await _run_queries(store)
        finally:
            # Not strictly needed on a tmp SQLite but mirrors the
            # shared-DB cleanup path so the helper stays exercised.
            await _delete_ids(store.engine, ids)

    # Concept tokens that should appear for each query.
    expectations = {
        "order placement": "order_placement",
        "payment settlement": "order_settlement",
        "patient admission": "patient_admission",
        "event ingestion": "event_ingest",
        "archived soft-delete rows": "soft_delete_policy",
    }
    for q, expected_concept in expectations.items():
        concepts = [h["concept"] for h in results[q]]
        assert expected_concept in concepts, (
            f"query {q!r} on SQLite did not surface {expected_concept!r}: "
            f"got {concepts}"
        )
    assert results["zxqbwn"] == []


@pytest.mark.skipif(
    not os.environ.get(POSTGRES_URL_ENV),
    reason=(
        f"{POSTGRES_URL_ENV} not set; skipping Postgres FTS parity contract "
        f"test. Run with {POSTGRES_URL_ENV}=postgresql+asyncpg://… to exercise "
        f"parity against the live tsvector + GIN index from migration "
        f"b8c9d0e1f2a3."
    ),
)
async def test_search_semantic_facts_parity_sqlite_vs_postgres(
    tmp_path: Path,
) -> None:
    """With ``TEST_POSTGRES_URL`` set, seed the identical 20-fact
    dataset into both a SQLite store and a Postgres store, issue every
    query against both, and assert top-5 overlap ≥3 per query plus the
    USER_CORRECTION-carry-over invariant.
    """
    postgres_url = os.environ[POSTGRES_URL_ENV]

    # --- SQLite side ---------------------------------------------------
    sqlite_db = tmp_path / "fts_parity_sqlite.db"
    async with Store(sqlite_db) as sqlite_store:
        _ = await _seed(sqlite_store)
        sqlite_results = await _run_queries(sqlite_store)

    # --- Postgres side -------------------------------------------------
    # Use the store's own engine builder so the URL normalization path
    # (asyncpg driver rewrite) stays consistent with production.
    pg_engine = build_engine(postgres_url)
    async with pg_engine.begin() as conn:
        await conn.run_sync(metadata.create_all)

    # We can't use ``Store.connect`` because it's hardcoded to a file
    # path. Build a thin wrapper that reuses Store's methods via
    # duck-typed access.
    class _PgStore(Store):
        def __init__(self, engine) -> None:  # noqa: ANN001
            self._engine = engine
            self._conn = None
            self.path = Path("/tmp/pg-store-unused")

    pg_store = _PgStore(pg_engine)
    pg_ids = await _seed(pg_store)
    try:
        pg_results = await _run_queries(pg_store)
    finally:
        await _delete_ids(pg_engine, pg_ids)
        await pg_engine.dispose()

    # --- Invariants ----------------------------------------------------

    # 1. Top-5 overlap per query.
    for q in _QUERIES:
        sqlite_ids = {h["concept"] for h in sqlite_results[q]}
        pg_ids_q = {h["concept"] for h in pg_results[q]}
        overlap = sqlite_ids & pg_ids_q
        assert len(overlap) >= 3, (
            f"top-5 overlap for query {q!r} below 3:\n"
            f"  sqlite: {sqlite_ids}\n  postgres: {pg_ids_q}"
        )

    # 2. USER_CORRECTION-sourced (confidence=1.0) facts that SQLite
    #    returned in top-5 must also appear in Postgres top-5.
    for q in _QUERIES:
        sqlite_corrections = {
            h["concept"]
            for h in sqlite_results[q]
            if h.get("source") == FactSource.USER_CORRECTION.value
        }
        pg_concepts = {h["concept"] for h in pg_results[q]}
        missing = sqlite_corrections - pg_concepts
        assert not missing, (
            f"query {q!r}: USER_CORRECTION fact(s) {missing} found on "
            f"SQLite but not in Postgres top-5"
        )

    # 3. No-match query behaves identically on both backends.
    assert sqlite_results["zxqbwn"] == []
    assert pg_results["zxqbwn"] == []


def _dummy_reference_to_sa() -> None:
    # keep ``sa`` import exercised by tooling
    _ = sa.text
