"""Add voice calling support with VoiceCall table and Tenant voice fields.

Revision ID: 20260330_0600
Revises: 20260328_0610
Create Date: 2026-03-30 06:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '20260330_0600'
down_revision = '20260328_0610'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add voice fields to tenants table
    op.execute(
        'ALTER TABLE tenants ADD COLUMN IF NOT EXISTS voice_enabled BOOLEAN DEFAULT FALSE'
    )
    op.execute(
        'ALTER TABLE tenants ADD COLUMN IF NOT EXISTS voice_phone_number VARCHAR(32)'
    )

    # Create voice_calls table
    op.execute(
        """CREATE TABLE IF NOT EXISTS voice_calls (
            id VARCHAR(36) PRIMARY KEY,
            tenant_id VARCHAR(36) NOT NULL,
            guest_contact_id VARCHAR(36),
            twilio_call_id VARCHAR(64) NOT NULL UNIQUE,
            twilio_phone_number VARCHAR(32) NOT NULL,
            guest_phone_number VARCHAR(32) NOT NULL,
            call_type VARCHAR(16) NOT NULL,
            status VARCHAR(32) DEFAULT 'ringing',
            guest_messages JSON DEFAULT '[]',
            ai_responses JSON DEFAULT '[]',
            full_transcript TEXT,
            confidence_avg FLOAT,
            sentiment VARCHAR(16),
            duration_seconds INTEGER,
            recording_url VARCHAR(512),
            created_at TIMESTAMP WITH TIME ZONE NOT NULL,
            started_at TIMESTAMP WITH TIME ZONE,
            ended_at TIMESTAMP WITH TIME ZONE,
            FOREIGN KEY (tenant_id) REFERENCES tenants(id),
            FOREIGN KEY (guest_contact_id) REFERENCES guest_contacts(id)
        )"""
    )

    # Create indexes
    op.execute(
        'CREATE INDEX IF NOT EXISTS ix_voice_calls_tenant_id ON voice_calls(tenant_id)'
    )
    op.execute(
        'CREATE INDEX IF NOT EXISTS ix_voice_calls_guest_phone ON voice_calls(guest_phone_number)'
    )
    op.execute(
        'CREATE INDEX IF NOT EXISTS ix_voice_calls_created_at ON voice_calls(created_at)'
    )


def downgrade() -> None:
    op.execute('DROP TABLE IF EXISTS voice_calls')
    op.execute('ALTER TABLE tenants DROP COLUMN IF EXISTS voice_phone_number')
    op.execute('ALTER TABLE tenants DROP COLUMN IF EXISTS voice_enabled')
