"""Add autonomous sending config to tenant_configs

Revision ID: 20260528_0100
Revises: 20260508_0500
Create Date: 2026-05-28
"""
from typing import Union
import sqlalchemy as sa
from alembic import op

revision: str = "20260528_0100"
down_revision: Union[str, None] = "20260508_0500"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Idempotent — safe to re-run if columns already exist from a partial deploy
    conn = op.get_bind()
    columns = {row[0] for row in conn.execute(
        sa.text("SELECT column_name FROM information_schema.columns WHERE table_name='tenant_configs'")
    )}

    if "auto_send_enabled" not in columns:
        op.add_column(
            "tenant_configs",
            sa.Column("auto_send_enabled", sa.Boolean(), nullable=False, server_default="false"),
        )

    if "auto_send_threshold" not in columns:
        op.add_column(
            "tenant_configs",
            sa.Column("auto_send_threshold", sa.Float(), nullable=False, server_default="0.85"),
        )


def downgrade() -> None:
    op.drop_column("tenant_configs", "auto_send_threshold")
    op.drop_column("tenant_configs", "auto_send_enabled")
