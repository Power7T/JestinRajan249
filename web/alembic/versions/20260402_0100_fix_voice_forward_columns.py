"""Fix: Add missing voice columns to tenants table

This migration ensures all voice-related columns exist on the tenants table:
- voice_enabled (may have been skipped in earlier migration due to bugs)
- voice_phone_number (may have been skipped in earlier migration due to bugs)
- voice_forward_enabled (was skipped due to table name bug in 20260401_0600)
- voice_forward_number (was skipped due to table name bug in 20260401_0600)

Revision ID: 20260402_0100
Revises: 20260401_0900
Create Date: 2026-04-02 01:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = '20260402_0100'
down_revision = '20260401_0900'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Verify tenants table exists
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    if 'tenants' not in inspector.get_table_names():
        return

    # Check which columns are missing
    existing_columns = {col['name'] for col in inspector.get_columns('tenants')}

    # Add voice_enabled if missing
    if 'voice_enabled' not in existing_columns:
        op.add_column('tenants',
            sa.Column('voice_enabled', sa.Boolean(), nullable=False, server_default='false')
        )

    # Add voice_phone_number if missing
    if 'voice_phone_number' not in existing_columns:
        op.add_column('tenants',
            sa.Column('voice_phone_number', sa.String(32), nullable=True)
        )

    # Add voice_forward_enabled if missing
    if 'voice_forward_enabled' not in existing_columns:
        op.add_column('tenants',
            sa.Column('voice_forward_enabled', sa.Boolean(), nullable=False, server_default='false')
        )

    # Add voice_forward_number if missing
    if 'voice_forward_number' not in existing_columns:
        op.add_column('tenants',
            sa.Column('voice_forward_number', sa.String(32), nullable=True)
        )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    if 'tenants' not in inspector.get_table_names():
        return

    existing_columns = {col['name'] for col in inspector.get_columns('tenants')}

    # Only drop columns if they exist
    # Note: This downgrade only removes the columns we added in this migration
    # voice_enabled and voice_phone_number may have been added by earlier migrations
    if 'voice_forward_number' in existing_columns:
        op.drop_column('tenants', 'voice_forward_number')

    if 'voice_forward_enabled' in existing_columns:
        op.drop_column('tenants', 'voice_forward_enabled')
