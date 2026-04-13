"""add semantic_facts and code_intelligence_meta tables

Revision ID: a1b2c3d4e5f6
Revises: 4f736e0d610c
Create Date: 2026-04-12 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = '4f736e0d610c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('semantic_facts',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('fact_type', sa.String(), nullable=False),
        sa.Column('concept', sa.String(), nullable=False),
        sa.Column('claim', sa.Text(), nullable=False),
        sa.Column('tables_json', sa.Text(), nullable=True),
        sa.Column('columns_json', sa.Text(), nullable=True),
        sa.Column('sql_condition', sa.Text(), nullable=True),
        sa.Column('evidence_path', sa.String(), nullable=True),
        sa.Column('evidence_lines', sa.String(), nullable=True),
        sa.Column('evidence_commit', sa.String(), nullable=True),
        sa.Column('source', sa.String(), nullable=False),
        sa.Column('confidence', sa.Float(), nullable=True),
        sa.Column('created_at', sa.String(), nullable=False),
        sa.Column('updated_at', sa.String(), nullable=False),
        sa.Column('valid_from_commit', sa.String(), nullable=True),
        sa.Column('valid_to_commit', sa.String(), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_semantic_facts_concept', 'semantic_facts', ['concept'])

    op.create_table('code_intelligence_meta',
        sa.Column('key', sa.String(), nullable=False),
        sa.Column('value', sa.Text(), nullable=False),
        sa.Column('updated_at', sa.String(), nullable=False),
        sa.PrimaryKeyConstraint('key'),
    )

    # FTS5 virtual table for SQLite only
    op.execute(
        "CREATE VIRTUAL TABLE IF NOT EXISTS semantic_facts_fts "
        "USING fts5(concept, claim, tables_json, columns_json, "
        "content=semantic_facts, content_rowid=rowid)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS semantic_facts_fts")
    op.drop_table('code_intelligence_meta')
    op.drop_index('ix_semantic_facts_concept', table_name='semantic_facts')
    op.drop_table('semantic_facts')
