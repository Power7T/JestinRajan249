"""Add custom welcome message template to tenant_configs

Revision ID: 20260325_0510
Revises: 20260325_0400
Create Date: 2026-03-25 05:10:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = "20260325_0510"
down_revision = "20260325_0400"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tenant_configs",
        sa.Column("guest_welcome_template", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("tenant_configs", "guest_welcome_template")
