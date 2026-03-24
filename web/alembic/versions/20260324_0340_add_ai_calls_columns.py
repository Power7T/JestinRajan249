"""Add AI usage tracking columns to tenant_configs

Revision ID: 20260324_0340
Revises: 20260324_0330
Create Date: 2026-03-24 03:40:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = "20260324_0340"
down_revision = "20260324_0330"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tenant_configs",
        sa.Column("ai_calls_today", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "tenant_configs",
        sa.Column("ai_calls_today_date", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "tenant_configs",
        sa.Column("ai_calls_monthly", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "tenant_configs",
        sa.Column("ai_calls_monthly_date", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("tenant_configs", "ai_calls_monthly_date")
    op.drop_column("tenant_configs", "ai_calls_monthly")
    op.drop_column("tenant_configs", "ai_calls_today_date")
    op.drop_column("tenant_configs", "ai_calls_today")
