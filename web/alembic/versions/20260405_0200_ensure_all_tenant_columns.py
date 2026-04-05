"""Ensure all tenant columns exist (idempotent schema repair)

This is a safety migration that ensures the tenants table has all required
columns from the ORM models. It runs after all other migrations and adds
any missing columns with the correct types and defaults.

Revision ID: 20260405_0200
Revises: 20260405_0100
Create Date: 2026-04-05 02:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = '20260405_0200'
down_revision = '20260405_0100'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add any missing columns to tenants table."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    
    if 'tenants' not in inspector.get_table_names():
        return
    
    existing = {col['name'] for col in inspector.get_columns('tenants')}
    
    # Define all columns that should exist in tenants
    required_columns = {
        'voice_forward_enabled': (sa.Boolean(), False, 'false'),
        'voice_forward_number': (sa.String(32), True, None),
    }
    
    # Add missing columns
    for col_name, (col_type, nullable, default) in required_columns.items():
        if col_name not in existing:
            if nullable:
                op.add_column('tenants', sa.Column(col_name, col_type, nullable=True))
            else:
                op.add_column('tenants', sa.Column(col_name, col_type, nullable=False, server_default=default))


def downgrade() -> None:
    """Remove added columns (not recommended for production)."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    
    if 'tenants' in inspector.get_table_names():
        existing = {col['name'] for col in inspector.get_columns('tenants')}
        for col_name in ['voice_forward_enabled', 'voice_forward_number']:
            if col_name in existing:
                op.drop_column('tenants', col_name)
