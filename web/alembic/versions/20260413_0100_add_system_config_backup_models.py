"""Add backup model columns to system_config

Revision ID: 20260413_0100
Revises: 20260413_0000
Create Date: 2026-04-13 01:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = '20260413_0100'
down_revision = '20260413_0000'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add backup model columns to system_config
    op.add_column('system_config', sa.Column('primary_backup_model', sa.String(100), nullable=False, server_default='anthropic/claude-3.5-sonnet'))
    op.add_column('system_config', sa.Column('routine_backup_model', sa.String(100), nullable=False, server_default='anthropic/claude-3.5-haiku'))
    op.add_column('system_config', sa.Column('fallback_model', sa.String(100), nullable=False, server_default='meta-llama/llama-3.3-70b-instruct'))
    op.add_column('system_config', sa.Column('sentiment_model', sa.String(100), nullable=False, server_default='openai/gpt-4o-mini'))


def downgrade() -> None:
    op.drop_column('system_config', 'sentiment_model')
    op.drop_column('system_config', 'fallback_model')
    op.drop_column('system_config', 'routine_backup_model')
    op.drop_column('system_config', 'primary_backup_model')
