"""Ensure all system_config columns exist (comprehensive schema fix)

Revision ID: 20260413_0200
Revises: 20260413_0100
Create Date: 2026-04-13 02:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = '20260413_0200'
down_revision = '20260413_0100'
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    # Get list of existing columns in system_config
    existing_columns = [col['name'] for col in inspector.get_columns('system_config')]

    # Add missing columns if they don't exist
    columns_to_add = {
        'primary_model': sa.String(100),
        'routine_model': sa.String(100),
        'updated_at': sa.DateTime(timezone=True),
    }

    for col_name, col_type in columns_to_add.items():
        if col_name not in existing_columns:
            if col_name == 'primary_model':
                op.add_column('system_config', sa.Column('primary_model', col_type, nullable=False, server_default='mistralai/mistral-large-2512'))
            elif col_name == 'routine_model':
                op.add_column('system_config', sa.Column('routine_model', col_type, nullable=False, server_default='google/gemini-2.5-flash'))
            elif col_name == 'updated_at':
                op.add_column('system_config', sa.Column('updated_at', col_type, nullable=False, server_default=sa.func.now()))


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing_columns = [col['name'] for col in inspector.get_columns('system_config')]

    # Only drop if they exist
    for col_name in ['updated_at', 'routine_model', 'primary_model']:
        if col_name in existing_columns:
            op.drop_column('system_config', col_name)
