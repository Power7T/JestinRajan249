"""Add Draft.archived_at column and API usage index.

Revision ID: 20260328_0600
Revises: 20260326_0530
Create Date: 2026-03-28 06:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '20260328_0600'
down_revision = '20260326_0530'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add archived_at column using raw SQL (PostgreSQL supports IF NOT EXISTS)
    op.execute(
        'ALTER TABLE drafts ADD COLUMN IF NOT EXISTS archived_at TIMESTAMP WITH TIME ZONE'
    )

    # Create composite index on api_usage_logs for tenant_id + created_at
    try:
        op.create_index(
            'idx_api_usage_tenant_created',
            'api_usage_logs',
            ['tenant_id', 'created_at'],
            unique=False
        )
    except Exception:
        # Index may already exist
        pass


def downgrade() -> None:
    # Remove archived_at column using raw SQL (PostgreSQL supports IF EXISTS)
    op.execute(
        'ALTER TABLE drafts DROP COLUMN IF EXISTS archived_at'
    )

    # Remove the index
    try:
        op.drop_index('idx_api_usage_tenant_created', table_name='api_usage_logs')
    except Exception:
        # Index may not exist
        pass
