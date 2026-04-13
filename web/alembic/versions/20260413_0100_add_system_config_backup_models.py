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
    # Add backup model columns to system_config (fallback_model and sentiment_model already exist)
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing_columns = [col['name'] for col in inspector.get_columns('system_config')]

    if 'primary_backup_model' not in existing_columns:
        op.add_column('system_config', sa.Column('primary_backup_model', sa.String(100), nullable=False, server_default='anthropic/claude-3.5-sonnet'))

    if 'routine_backup_model' not in existing_columns:
        op.add_column('system_config', sa.Column('routine_backup_model', sa.String(100), nullable=False, server_default='anthropic/claude-3.5-haiku'))


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing_columns = [col['name'] for col in inspector.get_columns('system_config')]

    if 'routine_backup_model' in existing_columns:
        op.drop_column('system_config', 'routine_backup_model')

    if 'primary_backup_model' in existing_columns:
        op.drop_column('system_config', 'primary_backup_model')
