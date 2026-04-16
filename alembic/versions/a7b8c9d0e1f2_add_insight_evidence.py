"""add insights.evidence column for unified EvidenceBundle persistence

Revision ID: a7b8c9d0e1f2
Revises: f6a7b8c9d0e1
Create Date: 2026-04-16 13:00:00.000000

Step 3.3 unifies recurrence/correlation/diagnostic evidence under a
single :class:`observibot.core.evidence.EvidenceBundle`, serialized into
this column as JSON. The older ``recurrence_context`` column is retained
for backwards compatibility with rows written before Step 3.3.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a7b8c9d0e1f2"
down_revision: Union[str, Sequence[str], None] = "f6a7b8c9d0e1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    insights_cols = {c["name"] for c in inspector.get_columns("insights")}
    if "evidence" not in insights_cols:
        op.add_column(
            "insights",
            sa.Column("evidence", sa.Text(), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    insights_cols = {c["name"] for c in inspector.get_columns("insights")}
    if "evidence" in insights_cols:
        op.drop_column("insights", "evidence")
