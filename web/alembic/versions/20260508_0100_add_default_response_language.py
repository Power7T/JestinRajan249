"""Add default_response_language to tenant_configs

Revision ID: 20260508_0100
Revises: 20260507_1100
Create Date: 2026-05-08
"""
import sqlalchemy as sa
from alembic import op

revision: str = "20260508_0100"
down_revision: str = "20260507_1100"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tenant_configs",
        sa.Column("default_response_language", sa.String(8), nullable=True, server_default="en"),
    )


def downgrade() -> None:
    op.drop_column("tenant_configs", "default_response_language")
