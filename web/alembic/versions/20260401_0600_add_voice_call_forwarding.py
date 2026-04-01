"""Add call forwarding fields to voice calling

Revision ID: 20260401_0600
Revises: 20260330_1400
Create Date: 2026-04-01 06:00:00.000000
"""
from alembic import op

revision = '20260401_0600'
down_revision = '20260330_1400'
branch_labels = None
depends_on = None


def upgrade() -> None:
    cols = [
        "ALTER TABLE tenant ADD COLUMN IF NOT EXISTS voice_forward_enabled BOOLEAN DEFAULT FALSE",
        "ALTER TABLE tenant ADD COLUMN IF NOT EXISTS voice_forward_number VARCHAR(32)",
    ]
    for col in cols:
        op.execute(col)


def downgrade() -> None:
    for col in ["voice_forward_number", "voice_forward_enabled"]:
        op.execute(f"ALTER TABLE tenant DROP COLUMN IF EXISTS {col}")
