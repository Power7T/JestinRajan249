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
    # Add columns to tenant table using proper Alembic DDL
    op.add_column('tenant', sa.Column('voice_forward_enabled', sa.Boolean(), nullable=False, server_default='false'))
    op.add_column('tenant', sa.Column('voice_forward_number', sa.String(32), nullable=True))


def downgrade() -> None:
    op.drop_column('tenant', 'voice_forward_number')
    op.drop_column('tenant', 'voice_forward_enabled')
