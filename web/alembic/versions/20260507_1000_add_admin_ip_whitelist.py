"""Add admin_ip_whitelist to system_config

Revision ID: 20260507_1000
Revises: 20260507_0900
Create Date: 2026-05-07
"""
from typing import Union
import sqlalchemy as sa
from alembic import op

revision: str = "20260507_1000"
down_revision: Union[str, None] = "20260507_0900"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("system_config", sa.Column("admin_ip_whitelist", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("system_config", "admin_ip_whitelist")
