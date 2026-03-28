"""Add signup fields to tenant table

Revision ID: 20260326_0530
Revises: 20260326_0520
Create Date: 2026-03-26 05:30:00.000000

"""
from alembic import op


# revision identifiers, used by Alembic.
revision = '20260326_0530'
down_revision = '20260326_0520'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add columns using raw SQL with IF NOT EXISTS
    op.execute(
        'ALTER TABLE tenants ADD COLUMN IF NOT EXISTS first_name VARCHAR(100)'
    )
    op.execute(
        'ALTER TABLE tenants ADD COLUMN IF NOT EXISTS last_name VARCHAR(100)'
    )
    op.execute(
        'ALTER TABLE tenants ADD COLUMN IF NOT EXISTS phone VARCHAR(20)'
    )
    op.execute(
        'ALTER TABLE tenants ADD COLUMN IF NOT EXISTS country VARCHAR(2)'
    )


def downgrade() -> None:
    # Drop columns using raw SQL with IF EXISTS
    op.execute(
        'ALTER TABLE tenants DROP COLUMN IF EXISTS country'
    )
    op.execute(
        'ALTER TABLE tenants DROP COLUMN IF EXISTS phone'
    )
    op.execute(
        'ALTER TABLE tenants DROP COLUMN IF EXISTS last_name'
    )
    op.execute(
        'ALTER TABLE tenants DROP COLUMN IF EXISTS first_name'
    )
