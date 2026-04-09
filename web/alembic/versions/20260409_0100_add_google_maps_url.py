"""Add google_maps_url to tenant_configs

Revision ID: 20260409_0100
Revises: 20260405_0400
Create Date: 2026-04-09 01:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = '20260409_0100'
down_revision = '20260405_0400'
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    if conn.dialect.name == 'postgresql':
        try:
            conn.execute(sa.text(
                "ALTER TABLE tenant_configs ADD COLUMN google_maps_url VARCHAR(512)"
            ))
        except Exception:
            pass
    elif conn.dialect.name == 'sqlite':
        try:
            conn.execute(sa.text(
                "ALTER TABLE tenant_configs ADD COLUMN google_maps_url VARCHAR(512)"
            ))
        except Exception:
            pass
    conn.commit()


def downgrade() -> None:
    pass
