"""Add voice send-during-call options, post-call summary, and scheduled calls flag.

Revision ID: 20260330_0700
Revises: 20260330_0600
Create Date: 2026-03-30 07:00:00.000000
"""
from alembic import op

revision = '20260330_0700'
down_revision = '20260330_0600'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # voice_send_channel: 'disabled' | 'sms' | 'whatsapp'
    op.execute(
        "ALTER TABLE tenant_configs ADD COLUMN IF NOT EXISTS "
        "voice_send_channel VARCHAR(16) DEFAULT 'disabled'"
    )
    # send a call-end summary SMS/WhatsApp to the host
    op.execute(
        "ALTER TABLE tenant_configs ADD COLUMN IF NOT EXISTS "
        "voice_post_call_summary BOOLEAN DEFAULT FALSE"
    )
    # auto-call guests 24h before check-in
    op.execute(
        "ALTER TABLE tenant_configs ADD COLUMN IF NOT EXISTS "
        "voice_scheduled_calls_enabled BOOLEAN DEFAULT FALSE"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE tenant_configs DROP COLUMN IF EXISTS voice_scheduled_calls_enabled")
    op.execute("ALTER TABLE tenant_configs DROP COLUMN IF EXISTS voice_post_call_summary")
    op.execute("ALTER TABLE tenant_configs DROP COLUMN IF EXISTS voice_send_channel")
