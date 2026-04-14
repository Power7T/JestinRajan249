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
    inspector = sa.inspect(conn)
    if "tenant_configs" not in inspector.get_table_names():
        return

    existing_columns = {col["name"] for col in inspector.get_columns("tenant_configs")}
    if "google_maps_url" not in existing_columns:
        op.add_column(
            "tenant_configs",
            sa.Column("google_maps_url", sa.String(length=512), nullable=True),
        )


def downgrade() -> None:
    pass
