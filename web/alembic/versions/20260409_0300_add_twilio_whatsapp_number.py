"""Add twilio_whatsapp_number to tenant_configs

Revision ID: 20260409_0300
Revises: 20260409_0200
Create Date: 2026-04-09 03:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = '20260409_0300'
down_revision = '20260409_0200'
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    try:
        conn.execute(sa.text(
            "ALTER TABLE tenant_configs ADD COLUMN twilio_whatsapp_number VARCHAR(32)"
        ))
    except Exception:
        pass
    # Let Alembic manage the migration transaction/version stamp.


def downgrade() -> None:
    pass
