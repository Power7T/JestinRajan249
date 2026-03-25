"""Add routine_model to system_config for optimized routine message generation

Revision ID: 20260326_0520
Revises: 20260325_0510
Create Date: 2026-03-26 05:20:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '20260326_0520'
down_revision = '20260325_0510'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add routine_model column to system_config
    # Uses google/gemini-2.5-flash by default (cheaper for routine messages)
    op.add_column('system_config', sa.Column(
        'routine_model',
        sa.String(length=100),
        nullable=False,
        server_default='google/gemini-2.5-flash'
    ))


def downgrade() -> None:
    op.drop_column('system_config', 'routine_model')
