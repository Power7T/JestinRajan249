"""Add per-tenant Twilio credentials for voice calling.

Revision ID: 20260330_1000
Revises: 20260330_0900
Create Date: 2026-03-30 10:00:00.000000
"""
from alembic import op

revision = '20260330_1000'
down_revision = '20260330_0900'
branch_labels = None
depends_on = None


def upgrade() -> None:
    cols = [
        "ALTER TABLE tenant_configs ADD COLUMN IF NOT EXISTS voice_twilio_account_sid VARCHAR(64)",
        "ALTER TABLE tenant_configs ADD COLUMN IF NOT EXISTS voice_twilio_auth_token_enc TEXT",
        "ALTER TABLE tenant_configs ADD COLUMN IF NOT EXISTS voice_twilio_from_number VARCHAR(32)",
    ]
    for col in cols:
        op.execute(col)


def downgrade() -> None:
    for col in ["voice_twilio_from_number", "voice_twilio_auth_token_enc", "voice_twilio_account_sid"]:
        op.execute(f"ALTER TABLE tenant_configs DROP COLUMN IF EXISTS {col}")
