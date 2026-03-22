"""Add num_units and messaging_channel to TenantConfig

Revision ID: c1a2d3e4f5b6
Revises: b99205d2bc7b
Create Date: 2026-03-22 15:00:00.000000+00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c1a2d3e4f5b6'
down_revision: Union[str, None] = 'b99205d2bc7b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('tenant_configs', sa.Column('num_units', sa.Integer(), nullable=False, server_default='1'))
    op.add_column('tenant_configs', sa.Column('messaging_channel', sa.String(32), nullable=True))


def downgrade() -> None:
    op.drop_column('tenant_configs', 'messaging_channel')
    op.drop_column('tenant_configs', 'num_units')
