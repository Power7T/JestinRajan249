"""Add google_maps_api_key_enc to system_config

Revision ID: 20260409_0200
Revises: 20260409_0100
Create Date: 2026-04-09 02:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = '20260409_0200'
down_revision = '20260409_0100'
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    for dialect in ('postgresql', 'sqlite'):
        if conn.dialect.name == dialect:
            try:
                conn.execute(sa.text(
                    "ALTER TABLE system_config ADD COLUMN google_maps_api_key_enc VARCHAR(255)"
                ))
            except Exception:
                pass
    conn.commit()


def downgrade() -> None:
    pass
