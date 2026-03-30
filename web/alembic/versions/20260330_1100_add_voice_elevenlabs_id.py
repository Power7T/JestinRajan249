"""Add per-tenant ElevenLabs voice ID selection.

Revision ID: 20260330_1100
Revises: 20260330_1000
Create Date: 2026-03-30 11:00:00.000000
"""
from alembic import op

revision = '20260330_1100'
down_revision = '20260330_1000'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE tenant_configs ADD COLUMN IF NOT EXISTS voice_elevenlabs_voice_id "
        "VARCHAR(64) DEFAULT 'EXAVITQu4vr4xnSDxMaL'"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE tenant_configs DROP COLUMN IF EXISTS voice_elevenlabs_voice_id")
