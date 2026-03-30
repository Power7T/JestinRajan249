"""Add voice_pricing_configs table for admin-editable voice pricing

Revision ID: 20260330_1400
Revises: 20260330_1230
Create Date: 2026-03-30 14:00:00.000000+00:00

Creates new table to store voice AI add-on pricing that can be edited
from the admin panel. Allows admins to adjust:
- monthly_price_usd (e.g., $39 → $49)
- overage_per_minute_usd (e.g., $0.049 → $0.059)
- surge_threshold and surge_multiplier
- minutes_included per tier

Default values from current VOICE_ADD_ON_PRICING dict in billing.py
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '20260330_1400'
down_revision: Union[str, None] = '20260330_1230'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'voice_pricing_configs',
        sa.Column('id', sa.Integer(), nullable=False, autoincrement=True),
        sa.Column('voice_tier', sa.String(32), nullable=False, unique=True, index=True),
        sa.Column('display_name', sa.String(128), nullable=False),
        sa.Column('monthly_price_usd', sa.Float(), nullable=False),
        sa.Column('minutes_included', sa.Integer(), nullable=True),
        sa.Column('overage_per_minute_usd', sa.Float(), nullable=False),
        sa.Column('surge_threshold', sa.Float(), nullable=False, server_default='0.5'),
        sa.Column('surge_multiplier', sa.Float(), nullable=False, server_default='1.15'),
        sa.Column('cost_basis_usd', sa.Float(), nullable=False),
        sa.Column('markup_ratio', sa.Float(), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )

    # Seed default voice pricing
    op.execute("""
        INSERT INTO voice_pricing_configs
        (voice_tier, display_name, monthly_price_usd, minutes_included, overage_per_minute_usd,
         surge_threshold, surge_multiplier, cost_basis_usd, markup_ratio, is_active, updated_at)
        VALUES
        ('light', 'Voice Light', 39.0, 100, 0.049, 0.5, 1.15, 1.80, 21.7, TRUE, CURRENT_TIMESTAMP),
        ('standard', 'Voice Standard', 79.0, 300, 0.049, 0.5, 1.15, 5.40, 14.6, TRUE, CURRENT_TIMESTAMP),
        ('professional', 'Voice Professional', 129.0, 750, 0.049, 0.5, 1.15, 13.50, 9.6, TRUE, CURRENT_TIMESTAMP),
        ('unlimited', 'Voice Unlimited', 199.0, NULL, 0.0, 0.5, 1.15, 36.0, 5.5, TRUE, CURRENT_TIMESTAMP)
    """)


def downgrade() -> None:
    op.drop_table('voice_pricing_configs')
