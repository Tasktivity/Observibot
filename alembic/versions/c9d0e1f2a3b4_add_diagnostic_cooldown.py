"""add diagnostic_cooldown table for store-backed cooldown cache

Revision ID: c9d0e1f2a3b4
Revises: b8c9d0e1f2a3
Create Date: 2026-04-17 16:00:00.000000

Phase 4.5 Step 4 Stage 5 — replace the in-memory
``MonitorLoop._diagnostic_cache`` dict with a persistent table so
restarted processes re-use prior diagnostic results and horizontal
workers share cooldown state.

Schema: ``anomaly_signature`` (primary key), ``cached_at`` ISO-8601
timestamp (drives both freshness check and retention eviction),
``evidence_json`` serialized ``list[DiagnosticEvidence]``. No dialect
guards needed — the table uses only standard column types that both
SQLite and Postgres accept. (SQLite picks this up via
``metadata.create_all`` at connect-time regardless, but shipping the
explicit migration ensures hosted Postgres deployments upgrade
cleanly.)
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c9d0e1f2a3b4"
down_revision: Union[str, Sequence[str], None] = "b8c9d0e1f2a3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "diagnostic_cooldown",
        sa.Column("anomaly_signature", sa.String(), nullable=False),
        sa.Column("cached_at", sa.String(), nullable=False),
        sa.Column("evidence_json", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("anomaly_signature"),
    )
    op.create_index(
        "idx_diag_cooldown_cached",
        "diagnostic_cooldown",
        ["cached_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "idx_diag_cooldown_cached", table_name="diagnostic_cooldown",
    )
    op.drop_table("diagnostic_cooldown")
