"""Add voice routing tables

Revision ID: 20260404_1000
Revises: 20260330_1400
Create Date: 2026-04-04 10:00:00.000000+00:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "20260404_1000"
down_revision: Union[str, None] = "20260330_1400"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    if "voice_routing_configs" not in existing_tables:
        op.create_table(
            "voice_routing_configs",
            sa.Column("tenant_id", sa.String(length=36), nullable=False),
            sa.Column("default_route", sa.String(length=32), nullable=False, server_default="ai"),
            sa.Column("fallback_sms", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("dead_air_timeout", sa.Integer(), nullable=False, server_default="30"),
            sa.Column("queue_hold_music", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("host_routing_number", sa.String(length=32), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
            sa.PrimaryKeyConstraint("tenant_id"),
        )

    if "routing_rules" not in existing_tables:
        op.create_table(
            "routing_rules",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("tenant_id", sa.String(length=36), nullable=False),
            sa.Column("name", sa.String(length=128), nullable=False),
            sa.Column("priority", sa.Integer(), nullable=False, server_default="100"),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("condition_type", sa.String(length=32), nullable=False),
            sa.Column("condition_value", sa.String(length=128), nullable=False),
            sa.Column("action", sa.String(length=32), nullable=False),
            sa.Column("action_target", sa.String(length=128), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_routing_rules_tenant_id", "routing_rules", ["tenant_id"], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    if "routing_rules" in existing_tables:
        try:
            op.drop_index("ix_routing_rules_tenant_id", table_name="routing_rules")
        except Exception:
            pass
        op.drop_table("routing_rules")

    if "voice_routing_configs" in existing_tables:
        op.drop_table("voice_routing_configs")
