"""Add call forwarding fields to voice calling

Revision ID: 20260401_0600
Revises: 20260330_1400
Create Date: 2026-04-01 06:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = '20260401_0600'
down_revision = '20260330_1400'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Check if tenants table exists before adding columns
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    if 'tenants' not in inspector.get_table_names():
        # Tenants table doesn't exist yet, skip this migration
        # It will be created by earlier migrations if database is rebuilt
        return

    # Check if columns already exist
    tenant_columns = {col['name'] for col in inspector.get_columns('tenants')}

    if 'voice_forward_enabled' not in tenant_columns:
        op.add_column('tenants', sa.Column('voice_forward_enabled', sa.Boolean(), nullable=False, server_default='false'))

    if 'voice_forward_number' not in tenant_columns:
        op.add_column('tenants', sa.Column('voice_forward_number', sa.String(32), nullable=True))


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    if 'tenants' not in inspector.get_table_names():
        return

    tenant_columns = {col['name'] for col in inspector.get_columns('tenants')}

    if 'voice_forward_number' in tenant_columns:
        op.drop_column('tenants', 'voice_forward_number')

    if 'voice_forward_enabled' in tenant_columns:
        op.drop_column('tenants', 'voice_forward_enabled')
