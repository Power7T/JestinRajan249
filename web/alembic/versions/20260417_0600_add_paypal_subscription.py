"""Add PayPal subscription fields to tenant_configs

Revision ID: 20260417_0600
Revises: 20260415_0500
Create Date: 2026-04-17 06:00:00.000000+00:00
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "20260417_0600"
down_revision: Union[str, None] = "20260415_0500"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if "tenant_configs" not in inspector.get_table_names():
        return
    existing = {col["name"] for col in inspector.get_columns("tenant_configs")}
    if "paypal_subscription_id" not in existing:
        op.add_column("tenant_configs", sa.Column("paypal_subscription_id", sa.String(128), nullable=True))
    if "subscription_payment_method" not in existing:
        op.add_column("tenant_configs", sa.Column("subscription_payment_method", sa.String(16), server_default="stripe", nullable=False))


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if "tenant_configs" not in inspector.get_table_names():
        return
    existing = {col["name"] for col in inspector.get_columns("tenant_configs")}
    if "paypal_subscription_id" in existing:
        op.drop_column("tenant_configs", "paypal_subscription_id")
    if "subscription_payment_method" in existing:
        op.drop_column("tenant_configs", "subscription_payment_method")
