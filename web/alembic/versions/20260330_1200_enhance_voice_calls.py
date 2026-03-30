"""Enhance VoiceCall with email, rating, callback, language fields.

Revision ID: 20260330_1200
Revises: 20260330_1100
Create Date: 2026-03-30 12:00:00.000000
"""
from alembic import op


revision = '20260330_1200'
down_revision = '20260330_1100'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add guest email, rating, callback fields
    op.execute(
        "ALTER TABLE voice_calls ADD COLUMN IF NOT EXISTS guest_email "
        "VARCHAR(255)"
    )
    op.execute(
        "ALTER TABLE voice_calls ADD COLUMN IF NOT EXISTS guest_rating "
        "INTEGER"
    )
    op.execute(
        "ALTER TABLE voice_calls ADD COLUMN IF NOT EXISTS guest_language "
        "VARCHAR(16) DEFAULT 'en'"
    )
    op.execute(
        "ALTER TABLE voice_calls ADD COLUMN IF NOT EXISTS callback_requested "
        "BOOLEAN DEFAULT false"
    )
    op.execute(
        "ALTER TABLE voice_calls ADD COLUMN IF NOT EXISTS callback_at "
        "TIMESTAMP WITH TIME ZONE"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE voice_calls DROP COLUMN IF EXISTS guest_email")
    op.execute("ALTER TABLE voice_calls DROP COLUMN IF EXISTS guest_rating")
    op.execute("ALTER TABLE voice_calls DROP COLUMN IF EXISTS guest_language")
    op.execute("ALTER TABLE voice_calls DROP COLUMN IF EXISTS callback_requested")
    op.execute("ALTER TABLE voice_calls DROP COLUMN IF EXISTS callback_at")
