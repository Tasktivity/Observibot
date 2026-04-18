"""add tsvector + GIN index on semantic_facts for Postgres FTS parity

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
Create Date: 2026-04-17 15:30:00.000000

Phase 4.5 Step 4 Stage 3 — Postgres FTS parity.

Before this migration, ``Store.search_semantic_facts`` used FTS5 with
BM25-like ranking on SQLite but fell back to naive ``ILIKE`` on two
columns under PostgreSQL. The two backends returned materially
different top-N sets for the same query, which violated the
local-first / managed-tier-identical principle from VISION.md.

This migration adds a Postgres-native ``search_tsv`` tsvector column
computed as ``to_tsvector('english', concept || ' ' || claim || ' ' ||
tables_json || ' ' || columns_json)`` and indexes it with GIN.
``search_semantic_facts`` on Postgres now queries via
``plainto_tsquery`` and orders by ``ts_rank_cd`` descending, producing
a rank-signal equivalent to SQLite's BM25 order. The SQLite FTS5
virtual table is unchanged.

Postgres 12+ is assumed (Supabase runs 15+; pyproject declares no
floor). ``GENERATED ALWAYS AS ... STORED`` is maintained by the
planner automatically on INSERT/UPDATE — no trigger and no write-path
change in Python.

Every dialect-specific statement in this migration is guarded on
``op.get_bind().dialect.name`` so SQLite-only deployments running the
migration against a clean DB do not error. The pre-existing
``a1b2c3d4e5f6_add_semantic_facts.py`` unconditionally runs a SQLite
FTS5 CREATE; that's a latent gap but not in scope here — we do not
repeat that mistake in this migration.
"""
from typing import Sequence, Union

import sqlalchemy as sa  # noqa: F401
from alembic import op

revision: str = "b8c9d0e1f2a3"
down_revision: Union[str, Sequence[str], None] = "a7b8c9d0e1f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect != "postgresql":
        # SQLite (and any future non-Postgres backend) keeps the
        # existing FTS5 virtual table created in
        # ``a1b2c3d4e5f6_add_semantic_facts.py``. Nothing to do.
        return

    # Postgres 12+ generated column: maintained automatically by the
    # planner on every INSERT/UPDATE, so no Python write-path change.
    # English config matches ``plainto_tsquery('english', ...)`` in
    # ``Store.search_semantic_facts``. COALESCE-wrapping each source
    # field protects against NULLs in tables_json / columns_json that
    # would otherwise yield a NULL tsvector and silently drop facts
    # from the index.
    op.execute("""
        ALTER TABLE semantic_facts
        ADD COLUMN search_tsv tsvector
        GENERATED ALWAYS AS (
            to_tsvector('english',
                coalesce(concept, '') || ' ' ||
                coalesce(claim, '') || ' ' ||
                coalesce(tables_json, '') || ' ' ||
                coalesce(columns_json, '')
            )
        ) STORED
    """)
    op.create_index(
        "idx_semantic_facts_search_tsv",
        "semantic_facts",
        ["search_tsv"],
        postgresql_using="gin",
    )


def downgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect != "postgresql":
        return
    op.drop_index(
        "idx_semantic_facts_search_tsv", table_name="semantic_facts",
    )
    op.execute("ALTER TABLE semantic_facts DROP COLUMN IF EXISTS search_tsv")
