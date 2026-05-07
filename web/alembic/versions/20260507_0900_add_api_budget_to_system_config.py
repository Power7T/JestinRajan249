"""Add api_budget fields to system_config

Revision ID: 20260507_0900
Revises: 20260419_0800
Create Date: 2026-05-07
"""
from typing import Union
import sqlalchemy as sa
from alembic import op

revision: str = "20260507_0900"
down_revision: Union[str, None] = "20260419_0800"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("system_config", sa.Column("api_budget_monthly_usd", sa.Float(), nullable=True, server_default="100.0"))
    op.add_column("system_config", sa.Column("api_budget_alert_percent", sa.Integer(), nullable=True, server_default="80"))


def downgrade() -> None:
    op.drop_column("system_config", "api_budget_alert_percent")
    op.drop_column("system_config", "api_budget_monthly_usd")
