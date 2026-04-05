"""Add automated_messages and guest_feedback tables

Revision ID: 20260403_0200
Revises: 20260402_0100
Create Date: 2026-04-03 02:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = '20260403_0200'
down_revision = '20260402_0100'
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = inspector.get_table_names()

    # Create automated_messages table
    if 'automated_messages' not in tables:
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

    # Create guest_feedback table
    if 'guest_feedback' not in tables:
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

    # Add digest_enabled to tenant_configs if missing
    if 'tenant_configs' in tables:
        existing = {c['name'] for c in inspector.get_columns('tenant_configs')}
        if 'digest_enabled' not in existing:
            op.add_column('tenant_configs', sa.Column('digest_enabled', sa.Boolean, nullable=False, server_default='false'))


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = inspector.get_table_names()
    if 'guest_feedback' in tables:
        op.drop_table('guest_feedback')
    if 'automated_messages' in tables:
        op.drop_table('automated_messages')
    if 'tenant_configs' in tables:
        existing = {c['name'] for c in inspector.get_columns('tenant_configs')}
        if 'digest_enabled' in existing:
            op.drop_column('tenant_configs', 'digest_enabled')
