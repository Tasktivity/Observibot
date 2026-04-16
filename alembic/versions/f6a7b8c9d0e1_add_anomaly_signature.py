"""add insights.anomaly_signature for stable dedup fingerprint

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-04-16 11:45:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f6a7b8c9d0e1"
down_revision: Union[str, Sequence[str], None] = "e5f6a7b8c9d0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    insights_cols = {c["name"] for c in inspector.get_columns("insights")}
    if "anomaly_signature" not in insights_cols:
        op.add_column(
            "insights",
            sa.Column("anomaly_signature", sa.String(), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    insights_cols = {c["name"] for c in inspector.get_columns("insights")}
    if "anomaly_signature" in insights_cols:
        op.drop_column("insights", "anomaly_signature")
