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
    inspector = sa.inspect(conn)
    if "system_config" not in inspector.get_table_names():
        return

    existing_columns = {col["name"] for col in inspector.get_columns("system_config")}
    if "google_maps_api_key_enc" not in existing_columns:
        op.add_column(
            "system_config",
            sa.Column("google_maps_api_key_enc", sa.String(length=255), nullable=True),
        )


def downgrade() -> None:
    pass
