"""Add team member delegation and workload tracking

Revision ID: 20260401_0900
Revises: 20260401_0800
Create Date: 2026-04-01 09:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = '20260401_0900'
down_revision = '20260401_0800'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add skill-related fields to team_members
    op.add_column('team_members', sa.Column('expertise_areas', sa.Text(), nullable=True))
    op.add_column('team_members', sa.Column('max_concurrent_tasks', sa.Integer(), nullable=False, server_default='10'))
    op.add_column('team_members', sa.Column('is_available_for_assignment', sa.Boolean(), nullable=False, server_default='true'))
    op.create_index(op.f('ix_team_members_is_available_for_assignment'), 'team_members', ['is_available_for_assignment'], unique=False)

    # Create team_member_workloads table
    op.create_table(
        'team_member_workloads',
        sa.Column('id', sa.String(36), nullable=False),
        sa.Column('team_member_id', sa.Integer(), nullable=False),
        sa.Column('tenant_id', sa.String(36), nullable=False),
        sa.Column('escalated_message_id', sa.String(36), nullable=False),
        sa.Column('property_id', sa.String(36), nullable=False),
        sa.Column('status', sa.String(32), nullable=False, server_default='assigned'),
        sa.Column('assigned_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('internal_notes', sa.Text(), nullable=True),
        sa.Column('resolution_notes', sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(['escalated_message_id'], ['escalated_messages.id'], ),
        sa.ForeignKeyConstraint(['property_id'], ['properties.id'], ),
        sa.ForeignKeyConstraint(['team_member_id'], ['team_members.id'], ),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_team_member_workloads_team_member_id'), 'team_member_workloads', ['team_member_id'], unique=False)
    op.create_index(op.f('ix_team_member_workloads_tenant_id'), 'team_member_workloads', ['tenant_id'], unique=False)
    op.create_index(op.f('ix_team_member_workloads_escalated_message_id'), 'team_member_workloads', ['escalated_message_id'], unique=False)
    op.create_index(op.f('ix_team_member_workloads_property_id'), 'team_member_workloads', ['property_id'], unique=False)
    op.create_index(op.f('ix_team_member_workloads_status'), 'team_member_workloads', ['status'], unique=False)
    op.create_index(op.f('ix_team_member_workloads_assigned_at'), 'team_member_workloads', ['assigned_at'], unique=False)


def downgrade() -> None:
    op.drop_table('team_member_workloads')
    op.drop_index(op.f('ix_team_members_is_available_for_assignment'), table_name='team_members')
    op.drop_column('team_members', 'is_available_for_assignment')
    op.drop_column('team_members', 'max_concurrent_tasks')
    op.drop_column('team_members', 'expertise_areas')
