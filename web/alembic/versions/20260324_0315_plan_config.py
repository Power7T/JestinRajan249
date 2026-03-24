"""Create PlanConfig table and add billing columns to tenant_configs

Revision ID: 20260324_0315
Revises: 20260324_0310
Create Date: 2026-03-24 03:15:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '20260324_0315'
down_revision = '20260324_0310'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create plan_configs table
    op.create_table(
        'plan_configs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('plan_key', sa.String(length=32), nullable=False),
        sa.Column('display_name', sa.String(length=128), nullable=False),
        sa.Column('base_fee_usd', sa.Float(), nullable=False),
        sa.Column('per_unit_fee_usd', sa.Float(), nullable=False),
        sa.Column('min_units', sa.Integer(), nullable=False),
        sa.Column('max_units', sa.Integer(), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='1'),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('plan_key'),
    )
    op.create_index(op.f('ix_plan_configs_plan_key'), 'plan_configs', ['plan_key'], unique=True)

    # Add billing columns to tenant_configs
    op.add_column('tenant_configs', sa.Column('num_units', sa.Integer(), server_default='1', nullable=False))
    op.add_column('tenant_configs', sa.Column('extra_services', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('tenant_configs', 'extra_services')
    op.drop_column('tenant_configs', 'num_units')
    op.drop_index(op.f('ix_plan_configs_plan_key'), table_name='plan_configs')
    op.drop_table('plan_configs')
