"""Add Draft.archived_at column and API usage index.

Revision ID: 20260328_0600
Revises: 20260326_0530
Create Date: 2026-03-28 06:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


def upgrade():
    # Add archived_at column to drafts table
    op.add_column(
        'drafts',
        sa.Column('archived_at', sa.DateTime(timezone=True), nullable=True)
    )

    # Create composite index on api_usage_logs for tenant_id + created_at
    op.create_index(
        'idx_api_usage_tenant_created',
        'api_usage_logs',
        ['tenant_id', 'created_at'],
        unique=False
    )


def downgrade():
    # Remove the index
    op.drop_index('idx_api_usage_tenant_created', table_name='api_usage_logs')

    # Remove archived_at column
    op.drop_column('drafts', 'archived_at')
