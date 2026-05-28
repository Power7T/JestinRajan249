"""Add action_policies column to tenant_configs for host-configured outcome rules.

Revision ID: 20260528_0300
Revises: 20260528_0200
Create Date: 2026-05-28
"""
from typing import Union
import sqlalchemy as sa
from alembic import op

revision: str = "20260528_0300"
down_revision: Union[str, None] = "20260528_0200"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    cols = [c["name"] for c in inspector.get_columns("tenant_configs")]
    if "action_policies" not in cols:
        op.add_column(
            "tenant_configs",
            sa.Column("action_policies", sa.Text(), nullable=True),
        )


def downgrade() -> None:
    op.drop_column("tenant_configs", "action_policies")
