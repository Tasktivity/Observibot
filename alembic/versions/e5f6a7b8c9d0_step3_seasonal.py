"""step 3: add seasonal_baselines, drop metric_baselines, add insights.recurrence_context

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-04-14 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e5f6a7b8c9d0"
down_revision: Union[str, Sequence[str], None] = "d4e5f6a7b8c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # 1. Drop the orphaned z-score-era table (never written to since Hardening).
    if "metric_baselines" in inspector.get_table_names():
        op.drop_table("metric_baselines")

    # 2. Add seasonal_baselines (MAD ring-buffer, hour-of-week keyed).
    op.create_table(
        "seasonal_baselines",
        sa.Column("tenant_id", sa.String(), nullable=False, server_default="default"),
        sa.Column("metric_name", sa.String(), nullable=False),
        sa.Column("connector_name", sa.String(), nullable=False),
        sa.Column("labels_key", sa.String(), nullable=False),
        sa.Column("hour_of_week", sa.Integer(), nullable=False),
        sa.Column("samples_json", sa.Text(), nullable=False),
        sa.Column("sample_count", sa.Integer(), nullable=False),
        sa.Column(
            "weeks_observed", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("last_week", sa.String(), nullable=True),
        sa.Column("median", sa.Float(), nullable=False),
        sa.Column("mad", sa.Float(), nullable=False),
        sa.Column("last_updated", sa.String(), nullable=False),
    )
    op.create_index(
        "idx_seasonal_unique",
        "seasonal_baselines",
        ["tenant_id", "metric_name", "connector_name", "labels_key", "hour_of_week"],
        unique=True,
    )
    op.create_index(
        "idx_seasonal_how",
        "seasonal_baselines",
        ["hour_of_week", "weeks_observed"],
    )

    # 3. Fix pre-existing bug: recurrence_context was never persisted on insights.
    insights_cols = {c["name"] for c in inspector.get_columns("insights")}
    if "recurrence_context" not in insights_cols:
        op.add_column(
            "insights", sa.Column("recurrence_context", sa.Text(), nullable=True)
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    insights_cols = {c["name"] for c in inspector.get_columns("insights")}
    if "recurrence_context" in insights_cols:
        op.drop_column("insights", "recurrence_context")

    op.drop_index("idx_seasonal_how", table_name="seasonal_baselines")
    op.drop_index("idx_seasonal_unique", table_name="seasonal_baselines")
    op.drop_table("seasonal_baselines")

    # Recreate metric_baselines for rollback parity (empty table).
    op.create_table(
        "metric_baselines",
        sa.Column("metric_name", sa.String(), nullable=False),
        sa.Column("connector_name", sa.String(), nullable=False),
        sa.Column("labels_key", sa.String(), nullable=False),
        sa.Column("sample_count", sa.Integer(), nullable=False),
        sa.Column("mean", sa.Float(), nullable=False),
        sa.Column("stddev", sa.Float(), nullable=False),
        sa.Column("last_updated", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint(
            "metric_name", "connector_name", "labels_key"
        ),
    )
