"""Add EscalationRule model for per-property escalation rules

Revision ID: 20260401_0800
Revises: 20260401_0700
Create Date: 2026-04-01 08:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = '20260401_0800'
down_revision = '20260401_0700'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Check if table already exists (in case of database corruption/reset)
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if 'escalation_rules' in inspector.get_table_names():
        return

    op.create_table(
        'escalation_rules',
        sa.Column('id', sa.String(36), nullable=False),
        sa.Column('property_id', sa.String(36), nullable=False),
        sa.Column('tenant_id', sa.String(36), nullable=False),
        sa.Column('name', sa.String(128), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('priority', sa.Integer(), nullable=False, server_default='100'),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('condition_type', sa.String(32), nullable=False),
        sa.Column('confidence_threshold', sa.Float(), nullable=True),
        sa.Column('keywords', sa.Text(), nullable=True),
        sa.Column('min_repeat_count', sa.Integer(), nullable=True),
        sa.Column('time_window_minutes', sa.Integer(), nullable=True),
        sa.Column('channels', sa.String(128), nullable=True),
        sa.Column('action', sa.String(32), nullable=False),
        sa.Column('escalation_priority', sa.String(16), nullable=False, server_default='high'),
        sa.Column('assign_to_team_member', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['property_id'], ['properties.id'], ),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ),
        sa.ForeignKeyConstraint(['assign_to_team_member'], ['team_members.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_escalation_rules_property_id'), 'escalation_rules', ['property_id'], unique=False)
    op.create_index(op.f('ix_escalation_rules_tenant_id'), 'escalation_rules', ['tenant_id'], unique=False)
    op.create_index(op.f('ix_escalation_rules_condition_type'), 'escalation_rules', ['condition_type'], unique=False)
    op.create_index(op.f('ix_escalation_rules_is_active'), 'escalation_rules', ['is_active'], unique=False)


def downgrade() -> None:
    op.drop_table('escalation_rules')
