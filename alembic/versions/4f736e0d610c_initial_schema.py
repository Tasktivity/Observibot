"""initial schema

Revision ID: 4f736e0d610c
Revises:
Create Date: 2026-04-11 20:09:21.695275

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '4f736e0d610c'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('users',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('email', sa.String(), nullable=False),
        sa.Column('password_hash', sa.String(), nullable=False),
        sa.Column('is_admin', sa.Boolean(), nullable=True),
        sa.Column('tenant_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.String(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('email'),
    )
    op.create_table('dashboard_widgets',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('user_id', sa.String(), nullable=True),
        sa.Column('tenant_id', sa.Integer(), nullable=True),
        sa.Column('widget_type', sa.String(), nullable=False),
        sa.Column('title', sa.String(), nullable=True),
        sa.Column('config', sa.JSON(), nullable=True),
        sa.Column('layout', sa.JSON(), nullable=True),
        sa.Column('data_source', sa.JSON(), nullable=True),
        sa.Column('schema_version', sa.Integer(), nullable=True),
        sa.Column('pinned', sa.Boolean(), nullable=True),
        sa.Column('created_at', sa.String(), nullable=True),
        sa.Column('updated_at', sa.String(), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_table('query_cache',
        sa.Column('hash', sa.String(), nullable=False),
        sa.Column('sql_text', sa.String(), nullable=False),
        sa.Column('result_json', sa.JSON(), nullable=True),
        sa.Column('row_count', sa.Integer(), nullable=True),
        sa.Column('execution_ms', sa.Float(), nullable=True),
        sa.Column('created_at', sa.String(), nullable=True),
        sa.Column('expires_at', sa.String(), nullable=True),
        sa.PrimaryKeyConstraint('hash'),
    )


def downgrade() -> None:
    op.drop_table('query_cache')
    op.drop_table('dashboard_widgets')
    op.drop_table('users')
