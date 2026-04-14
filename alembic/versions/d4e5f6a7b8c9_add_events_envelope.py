"""add events envelope table

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-04-13 14:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd4e5f6a7b8c9'
down_revision: Union[str, Sequence[str], None] = 'c3d4e5f6a7b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('events',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('event_type', sa.String(), nullable=False),
        sa.Column('occurred_at', sa.String(), nullable=False),
        sa.Column('severity', sa.String(), nullable=True),
        sa.Column('source', sa.String(), nullable=False),
        sa.Column('agent', sa.String(), nullable=False, server_default='sre'),
        sa.Column('subject', sa.String(), nullable=False),
        sa.Column('summary', sa.Text(), nullable=True),
        sa.Column('ref_table', sa.String(), nullable=False),
        sa.Column('ref_id', sa.String(), nullable=False),
        sa.Column('run_id', sa.String(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('idx_events_type_time', 'events', ['event_type', sa.text('occurred_at DESC')])
    op.create_index('idx_events_subject_time', 'events', ['subject', sa.text('occurred_at DESC')])
    op.create_index('idx_events_agent_time', 'events', ['agent', sa.text('occurred_at DESC')])
    op.create_index('idx_events_run', 'events', ['run_id'])
    op.create_index('idx_events_ref', 'events', ['ref_table', 'ref_id'])

    dialect = op.get_bind().dialect.name
    if dialect == 'sqlite':
        op.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS events_fts "
            "USING fts5(summary, content=events, content_rowid=rowid)"
        )
    else:
        # PostgreSQL: add generated tsvector column + GIN index
        op.execute(
            "ALTER TABLE events ADD COLUMN summary_tsv tsvector "
            "GENERATED ALWAYS AS (to_tsvector('english', coalesce(summary, ''))) STORED"
        )
        op.create_index('idx_events_fts', 'events', ['summary_tsv'], postgresql_using='gin')


def downgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == 'sqlite':
        op.execute("DROP TABLE IF EXISTS events_fts")
    else:
        op.drop_index('idx_events_fts', table_name='events')
        op.execute("ALTER TABLE events DROP COLUMN IF EXISTS summary_tsv")

    op.drop_index('idx_events_ref', table_name='events')
    op.drop_index('idx_events_run', table_name='events')
    op.drop_index('idx_events_agent_time', table_name='events')
    op.drop_index('idx_events_subject_time', table_name='events')
    op.drop_index('idx_events_type_time', table_name='events')
    op.drop_table('events')
