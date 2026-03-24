"""Add timezone to tenant_configs

Revision ID: 20260324_0310
Revises: 20260324_0305
Create Date: 2026-03-24 03:10:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '20260324_0310'
down_revision = '20260324_0305'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Adding a column with a server_default ensures existing rows don't complain
    op.add_column('tenant_configs', sa.Column('timezone', sa.String(length=64), server_default='UTC', nullable=False))
    op.add_column('tenant_configs', sa.Column('data_retention_days', sa.Integer(), server_default='30', nullable=False))


def downgrade() -> None:
    op.drop_column('tenant_configs', 'timezone')
    op.drop_column('tenant_configs', 'data_retention_days')
