"""Add Property, PropertyConfig, EscalatedMessage, MessageLog models for multi-property support

Revision ID: 20260401_0700
Revises: 20260401_0600
Create Date: 2026-04-01 07:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = '20260401_0700'
down_revision = '20260401_0600'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Check if tables already exist
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing_tables = inspector.get_table_names()

    # Only create if not already present
    if 'properties' in existing_tables:
        return

    # Create properties table
    op.create_table(
        'properties',
        sa.Column('id', sa.String(36), nullable=False),
        sa.Column('tenant_id', sa.String(36), nullable=False),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('property_type', sa.String(64), nullable=True),
        sa.Column('city', sa.String(128), nullable=True),
        sa.Column('ical_url', sa.Text(), nullable=True),
        sa.Column('status', sa.String(32), nullable=False, server_default='active'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_properties_tenant_id'), 'properties', ['tenant_id'], unique=False)
    op.create_index(op.f('ix_properties_name'), 'properties', ['name'], unique=False)
    op.create_index(op.f('ix_properties_status'), 'properties', ['status'], unique=False)

    # Create property_configs table
    op.create_table(
        'property_configs',
        sa.Column('id', sa.String(36), nullable=False),
        sa.Column('property_id', sa.String(36), nullable=False),
        sa.Column('tenant_id', sa.String(36), nullable=False),
        sa.Column('voice_enabled', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('voice_phone_number', sa.String(32), nullable=True),
        sa.Column('voice_twilio_account_sid', sa.String(64), nullable=True),
        sa.Column('voice_twilio_auth_token_enc', sa.Text(), nullable=True),
        sa.Column('voice_twilio_from_number', sa.String(32), nullable=True),
        sa.Column('voice_elevenlabs_voice_id', sa.String(64), nullable=True),
        sa.Column('voice_forward_enabled', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('voice_forward_number', sa.String(32), nullable=True),
        sa.Column('voice_routing_mode', sa.String(32), nullable=False, server_default='per_property'),
        sa.Column('shared_voice_number', sa.String(32), nullable=True),
        sa.Column('voice_routing_webhook_param', sa.String(128), nullable=True),
        sa.Column('amenities', sa.Text(), nullable=True),
        sa.Column('house_rules', sa.Text(), nullable=True),
        sa.Column('check_in_time', sa.String(32), nullable=True),
        sa.Column('check_out_time', sa.String(32), nullable=True),
        sa.Column('max_guests', sa.Integer(), nullable=True),
        sa.Column('faq', sa.Text(), nullable=True),
        sa.Column('food_menu', sa.Text(), nullable=True),
        sa.Column('nearby_restaurants', sa.Text(), nullable=True),
        sa.Column('parking_policy', sa.Text(), nullable=True),
        sa.Column('pet_policy', sa.Text(), nullable=True),
        sa.Column('wifi_password', sa.String(128), nullable=True),
        sa.Column('wifi_network_name', sa.String(128), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['property_id'], ['properties.id'], ),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('property_id')
    )
    op.create_index(op.f('ix_property_configs_property_id'), 'property_configs', ['property_id'], unique=False)
    op.create_index(op.f('ix_property_configs_tenant_id'), 'property_configs', ['tenant_id'], unique=False)
    op.create_index(op.f('ix_property_configs_voice_phone_number'), 'property_configs', ['voice_phone_number'], unique=True)

    # Create escalated_messages table
    op.create_table(
        'escalated_messages',
        sa.Column('id', sa.String(36), nullable=False),
        sa.Column('property_id', sa.String(36), nullable=False),
        sa.Column('tenant_id', sa.String(36), nullable=False),
        sa.Column('guest_id', sa.String(128), nullable=True),
        sa.Column('guest_name', sa.String(255), nullable=True),
        sa.Column('message_type', sa.String(32), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('reason', sa.String(64), nullable=False),
        sa.Column('priority', sa.String(16), nullable=False, server_default='medium'),
        sa.Column('confidence_score', sa.Float(), nullable=True),
        sa.Column('status', sa.String(32), nullable=False, server_default='pending'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('host_response', sa.Text(), nullable=True),
        sa.Column('assigned_to', sa.Integer(), nullable=True),
        sa.Column('source_message_id', sa.String(128), nullable=True),
        sa.ForeignKeyConstraint(['assigned_to'], ['team_members.id'], ),
        sa.ForeignKeyConstraint(['property_id'], ['properties.id'], ),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_escalated_messages_property_id'), 'escalated_messages', ['property_id'], unique=False)
    op.create_index(op.f('ix_escalated_messages_tenant_id'), 'escalated_messages', ['tenant_id'], unique=False)
    op.create_index(op.f('ix_escalated_messages_guest_id'), 'escalated_messages', ['guest_id'], unique=False)
    op.create_index(op.f('ix_escalated_messages_message_type'), 'escalated_messages', ['message_type'], unique=False)
    op.create_index(op.f('ix_escalated_messages_reason'), 'escalated_messages', ['reason'], unique=False)
    op.create_index(op.f('ix_escalated_messages_priority'), 'escalated_messages', ['priority'], unique=False)
    op.create_index(op.f('ix_escalated_messages_status'), 'escalated_messages', ['status'], unique=False)
    op.create_index(op.f('ix_escalated_messages_created_at'), 'escalated_messages', ['created_at'], unique=False)
    op.create_index(op.f('ix_escalated_messages_assigned_to'), 'escalated_messages', ['assigned_to'], unique=False)
    op.create_index(op.f('ix_escalated_messages_source_message_id'), 'escalated_messages', ['source_message_id'], unique=False)

    # Create message_logs table
    op.create_table(
        'message_logs',
        sa.Column('id', sa.String(36), nullable=False),
        sa.Column('property_id', sa.String(36), nullable=False),
        sa.Column('tenant_id', sa.String(36), nullable=False),
        sa.Column('guest_id', sa.String(128), nullable=True),
        sa.Column('guest_name', sa.String(255), nullable=True),
        sa.Column('direction', sa.String(16), nullable=False),
        sa.Column('channel', sa.String(32), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('ai_handled', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('ai_response', sa.Text(), nullable=True),
        sa.Column('confidence_score', sa.Float(), nullable=True),
        sa.Column('escalated', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('escalated_message_id', sa.String(36), nullable=True),
        sa.Column('status', sa.String(32), nullable=False, server_default='pending'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['escalated_message_id'], ['escalated_messages.id'], ),
        sa.ForeignKeyConstraint(['property_id'], ['properties.id'], ),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_message_logs_property_id'), 'message_logs', ['property_id'], unique=False)
    op.create_index(op.f('ix_message_logs_tenant_id'), 'message_logs', ['tenant_id'], unique=False)
    op.create_index(op.f('ix_message_logs_guest_id'), 'message_logs', ['guest_id'], unique=False)
    op.create_index(op.f('ix_message_logs_direction'), 'message_logs', ['direction'], unique=False)
    op.create_index(op.f('ix_message_logs_channel'), 'message_logs', ['channel'], unique=False)
    op.create_index(op.f('ix_message_logs_ai_handled'), 'message_logs', ['ai_handled'], unique=False)
    op.create_index(op.f('ix_message_logs_escalated'), 'message_logs', ['escalated'], unique=False)
    op.create_index(op.f('ix_message_logs_escalated_message_id'), 'message_logs', ['escalated_message_id'], unique=False)
    op.create_index(op.f('ix_message_logs_status'), 'message_logs', ['status'], unique=False)
    op.create_index(op.f('ix_message_logs_created_at'), 'message_logs', ['created_at'], unique=False)
    op.create_index(op.f('ix_message_logs_updated_at'), 'message_logs', ['updated_at'], unique=False)


def downgrade() -> None:
    op.drop_table('message_logs')
    op.drop_table('escalated_messages')
    op.drop_table('property_configs')
    op.drop_table('properties')
