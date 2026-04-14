"""add monitor_runs table

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-04-13 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b2c3d4e5f6a7'
down_revision: Union[str, Sequence[str], None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('monitor_runs',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('started_at', sa.String(), nullable=False),
        sa.Column('finished_at', sa.String(), nullable=True),
        sa.Column('system_snapshot_id', sa.String(), nullable=True),
        sa.Column('anomaly_count', sa.Integer(), nullable=True, server_default='0'),
        sa.Column('insight_count', sa.Integer(), nullable=True, server_default='0'),
        sa.Column('metric_count', sa.Integer(), nullable=True, server_default='0'),
        sa.Column('llm_used', sa.Boolean(), nullable=True, server_default='0'),
        sa.Column('llm_call_id', sa.String(), nullable=True),
        sa.Column('status', sa.String(), nullable=True, server_default='running'),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('idx_monitor_runs_time', 'monitor_runs', ['started_at'])


def downgrade() -> None:
    op.drop_index('idx_monitor_runs_time', table_name='monitor_runs')
    op.drop_table('monitor_runs')
