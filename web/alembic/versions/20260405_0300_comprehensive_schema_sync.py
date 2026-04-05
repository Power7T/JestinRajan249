"""Comprehensive schema sync - ensure all tables and columns exist

This is the final safety migration that creates any missing tables from scratch
based on the current ORM models. Use as a last resort to synchronize schema.

Revision ID: 20260405_0300
Revises: 20260405_0200
Create Date: 2026-04-05 03:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = '20260405_0300'
down_revision = '20260405_0200'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create any missing tables and columns that ORM models expect."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing_tables = set(inspector.get_table_names())

    # Fix tenant_configs - add digest_enabled if missing
    if 'tenant_configs' in existing_tables:
        existing_cols = {col['name'] for col in inspector.get_columns('tenant_configs')}
        if 'digest_enabled' not in existing_cols:
            op.add_column('tenant_configs',
                sa.Column('digest_enabled', sa.Boolean, nullable=False, server_default='false'))

    # Fix tenants - add voice_forward columns if missing
    if 'tenants' in existing_tables:
        existing_cols = {col['name'] for col in inspector.get_columns('tenants')}
        if 'voice_forward_enabled' not in existing_cols:
            op.add_column('tenants',
                sa.Column('voice_forward_enabled', sa.Boolean(), nullable=False, server_default='false'))
        if 'voice_forward_number' not in existing_cols:
            op.add_column('tenants',
                sa.Column('voice_forward_number', sa.String(32), nullable=True))

    # Properties table (multi-property support)
    if 'properties' not in existing_tables:
        op.create_table(
            'properties',
            sa.Column('id', sa.String(36), primary_key=True),
            sa.Column('tenant_id', sa.String(36), sa.ForeignKey('tenants.id'), nullable=False, index=True),
            sa.Column('name', sa.String(255), nullable=False, index=True),
            sa.Column('property_type', sa.String(64), nullable=True),
            sa.Column('city', sa.String(128), nullable=True),
            sa.Column('ical_url', sa.Text, nullable=True),
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        )
    
    # Property configs
    if 'property_configs' not in existing_tables:
        op.create_table(
            'property_configs',
            sa.Column('id', sa.Integer, primary_key=True, autoincrement=True),
            sa.Column('property_id', sa.String(36), sa.ForeignKey('properties.id'), nullable=False, unique=True, index=True),
            sa.Column('wifi_password', sa.String(128), nullable=True),
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        )
    
    # Escalated messages (urgent messages needing host attention)
    if 'escalated_messages' not in existing_tables:
        op.create_table(
            'escalated_messages',
            sa.Column('id', sa.String(36), primary_key=True),
            sa.Column('tenant_id', sa.String(36), sa.ForeignKey('tenants.id'), nullable=False, index=True),
            sa.Column('property_id', sa.String(36), sa.ForeignKey('properties.id'), nullable=False),
            sa.Column('status', sa.String(32), nullable=False, default='open'),
            sa.Column('priority', sa.String(16), nullable=False, default='medium'),
            sa.Column('content', sa.Text, nullable=False),
            sa.Column('guest_name', sa.String(128), nullable=True),
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, index=True),
        )
    
    # Escalation rules (per-property escalation logic)
    if 'escalation_rules' not in existing_tables:
        op.create_table(
            'escalation_rules',
            sa.Column('id', sa.String(36), primary_key=True),
            sa.Column('tenant_id', sa.String(36), sa.ForeignKey('tenants.id'), nullable=False, index=True),
            sa.Column('property_id', sa.String(36), sa.ForeignKey('properties.id'), nullable=False),
            sa.Column('trigger_keyword', sa.String(255), nullable=False),
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        )
    
    # Automated messages (trigger-based messaging)
    if 'automated_messages' not in existing_tables:
        op.create_table(
            'automated_messages',
            sa.Column('id', sa.String(36), primary_key=True),
            sa.Column('tenant_id', sa.String(36), sa.ForeignKey('tenants.id'), nullable=False, index=True),
            sa.Column('property_name', sa.String(256), nullable=True),
            sa.Column('trigger', sa.String(32), nullable=False, index=True),
            sa.Column('channel', sa.String(32), nullable=False, server_default='whatsapp'),
            sa.Column('message_template', sa.Text, nullable=False),
            sa.Column('is_active', sa.Boolean, nullable=False, server_default='true'),
            sa.Column('send_hour', sa.Integer, nullable=False, server_default='9'),
            sa.Column('last_run_date', sa.String(16), nullable=True),
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        )
    
    # Guest feedback (post-stay ratings)
    if 'guest_feedback' not in existing_tables:
        op.create_table(
            'guest_feedback',
            sa.Column('id', sa.String(36), primary_key=True),
            sa.Column('tenant_id', sa.String(36), sa.ForeignKey('tenants.id'), nullable=False, index=True),
            sa.Column('reservation_id', sa.Integer, sa.ForeignKey('reservations.id'), nullable=True, index=True),
            sa.Column('feedback_token', sa.String(64), unique=True, nullable=False, index=True),
            sa.Column('guest_name', sa.String(128), nullable=False),
            sa.Column('guest_phone', sa.String(32), nullable=True),
            sa.Column('property_name', sa.String(256), nullable=True),
            sa.Column('rating', sa.Integer, nullable=True),
            sa.Column('comment', sa.Text, nullable=True),
            sa.Column('submitted_at', sa.DateTime(timezone=True), nullable=True),
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        )
    
    # Message logs (for audit trail)
    if 'message_logs' not in existing_tables:
        op.create_table(
            'message_logs',
            sa.Column('id', sa.String(36), primary_key=True),
            sa.Column('escalated_message_id', sa.String(36), sa.ForeignKey('escalated_messages.id'), nullable=False, index=True),
            sa.Column('sent_at', sa.DateTime(timezone=True), nullable=False),
        )
    
    # Team member workloads (for task assignment)
    if 'team_member_workloads' not in existing_tables:
        op.create_table(
            'team_member_workloads',
            sa.Column('id', sa.String(36), primary_key=True),
            sa.Column('team_member_id', sa.String(36), sa.ForeignKey('team_members.id'), nullable=False, unique=True),
            sa.Column('assigned_message_id', sa.String(36), sa.ForeignKey('escalated_messages.id'), nullable=True),
            sa.Column('status', sa.String(32), nullable=False, default='idle'),
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        )


def downgrade() -> None:
    """Drop tables (not recommended)."""
    pass
