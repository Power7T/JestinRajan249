"""Add signup fields to tenant table

Revision ID: 20260326_0530
Revises: 20260326_0520
Create Date: 2026-03-26 05:30:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '20260326_0530'
down_revision = '20260326_0520'
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    # Get existing columns in tenants table
    existing_columns = [col['name'] for col in inspector.get_columns('tenants')]

    # Add first_name column if it doesn't exist
    if 'first_name' not in existing_columns:
        op.add_column('tenants', sa.Column(
            'first_name',
            sa.String(length=100),
            nullable=True
        ))

    # Add last_name column if it doesn't exist
    if 'last_name' not in existing_columns:
        op.add_column('tenants', sa.Column(
            'last_name',
            sa.String(length=100),
            nullable=True
        ))

    # Add phone column if it doesn't exist
    if 'phone' not in existing_columns:
        op.add_column('tenants', sa.Column(
            'phone',
            sa.String(length=20),
            nullable=True
        ))

    # Add country column if it doesn't exist
    if 'country' not in existing_columns:
        op.add_column('tenants', sa.Column(
            'country',
            sa.String(length=2),
            nullable=True
        ))


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    # Get existing columns in tenants table
    existing_columns = [col['name'] for col in inspector.get_columns('tenants')]

    # Drop columns if they exist
    if 'country' in existing_columns:
        op.drop_column('tenants', 'country')

    if 'phone' in existing_columns:
        op.drop_column('tenants', 'phone')

    if 'last_name' in existing_columns:
        op.drop_column('tenants', 'last_name')

    if 'first_name' in existing_columns:
        op.drop_column('tenants', 'first_name')
