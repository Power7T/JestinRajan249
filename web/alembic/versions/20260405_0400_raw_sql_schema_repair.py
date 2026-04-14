"""Schema repair - add missing columns directly.

Revision ID: 20260405_0400
Revises: 20260405_0300
Create Date: 2026-04-05 04:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = '20260405_0400'
down_revision = '20260405_0300'
branch_labels = None
depends_on = None


def _table_columns(conn, table_name: str) -> set[str]:
    inspector = sa.inspect(conn)
    if table_name not in inspector.get_table_names():
        return set()
    return {col["name"] for col in inspector.get_columns(table_name)}


def upgrade() -> None:
    """Add missing columns without relying on caught DDL errors."""
    conn = op.get_bind()
    false_default = sa.text("0") if conn.dialect.name == "sqlite" else sa.text("false")

    tenant_config_columns = _table_columns(conn, "tenant_configs")
    if "digest_enabled" not in tenant_config_columns:
        op.add_column(
            "tenant_configs",
            sa.Column("digest_enabled", sa.Boolean(), nullable=False, server_default=false_default),
        )

    tenant_columns = _table_columns(conn, "tenants")
    if "voice_forward_enabled" not in tenant_columns:
        op.add_column(
            "tenants",
            sa.Column("voice_forward_enabled", sa.Boolean(), nullable=False, server_default=false_default),
        )
        tenant_columns.add("voice_forward_enabled")

    if "voice_forward_number" not in tenant_columns:
        op.add_column(
            "tenants",
            sa.Column("voice_forward_number", sa.String(length=32), nullable=True),
        )


def downgrade() -> None:
    """No downgrade."""
    pass
