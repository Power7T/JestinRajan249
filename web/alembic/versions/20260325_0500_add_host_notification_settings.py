"""Add host notification settings to tenant_configs

Revision ID: 20260325_0500
Revises: 20260325_0400
Create Date: 2026-03-25 05:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = "20260325_0500"
down_revision = "20260325_0400"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tenant_configs",
        sa.Column("notify_host_on_guest_msg", sa.Boolean(), nullable=False, server_default="0"),
    )
    op.add_column(
        "tenant_configs",
        sa.Column("host_notify_phone", sa.String(32), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("tenant_configs", "host_notify_phone")
    op.drop_column("tenant_configs", "notify_host_on_guest_msg")
