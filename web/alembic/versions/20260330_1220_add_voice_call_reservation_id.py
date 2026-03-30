"""Add reservation_id FK to VoiceCall for guest data integration.

Revision ID: 20260330_1220
Revises: 20260330_1210
Create Date: 2026-03-30 12:20:00.000000
"""
from alembic import op


revision = '20260330_1220'
down_revision = '20260330_1210'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add reservation_id FK to voice_calls
    op.execute(
        "ALTER TABLE voice_calls ADD COLUMN IF NOT EXISTS reservation_id "
        "INTEGER REFERENCES reservations(id) ON DELETE SET NULL"
    )
    # Index for faster lookups
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_voice_calls_reservation_id ON voice_calls(reservation_id)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_voice_calls_reservation_id")
    op.execute("ALTER TABLE voice_calls DROP COLUMN IF EXISTS reservation_id")
