"""Add iCal sync health tracking to tenant_configs

Revision ID: 20260508_0400
Revises: 20260508_0300
Create Date: 2026-05-08
"""
from typing import Union
import sqlalchemy as sa
from alembic import op

revision: str = "20260508_0400"
down_revision: Union[str, None] = "20260508_0300"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tenant_configs", sa.Column("ical_last_error", sa.Text(), nullable=True))
    op.add_column("tenant_configs", sa.Column("ical_last_error_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("tenant_configs", sa.Column("ical_last_success_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("tenant_configs", "ical_last_success_at")
    op.drop_column("tenant_configs", "ical_last_error_at")
    op.drop_column("tenant_configs", "ical_last_error")
