"""Add welcome_sent flag to ArrivalActivation

Revision ID: d2e3f4a5b6c7
Revises: c1a2d3e4f5b6
Create Date: 2026-03-22 16:00:00.000000+00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd2e3f4a5b6c7'
down_revision: Union[str, None] = 'c1a2d3e4f5b6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('arrival_activations', sa.Column('welcome_sent', sa.Boolean(), nullable=False, server_default='0'))
    op.create_index('ix_arrival_activations_welcome_sent', 'arrival_activations', ['welcome_sent'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_arrival_activations_welcome_sent', table_name='arrival_activations')
    op.drop_column('arrival_activations', 'welcome_sent')
