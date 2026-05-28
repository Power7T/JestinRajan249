"""Add guest web chat: chat_token on tenant_configs + web_chat_sessions table.

Revision ID: 20260528_0400
Revises: 20260528_0300
Create Date: 2026-05-28
"""
from typing import Union
import secrets
import sqlalchemy as sa
from alembic import op

revision: str = "20260528_0400"
down_revision: Union[str, None] = "20260528_0300"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    # 1. Add chat_token to tenant_configs (idempotent)
    tc_cols = [c["name"] for c in inspector.get_columns("tenant_configs")]
    if "chat_token" not in tc_cols:
        op.add_column(
            "tenant_configs",
            sa.Column("chat_token", sa.String(32), nullable=True),
        )
        # Backfill existing rows with unique tokens
        op.execute(
            """
            UPDATE tenant_configs
            SET chat_token = substr(md5(random()::text || tenant_id), 1, 16)
            WHERE chat_token IS NULL
            """
        )

    # 2. Create web_chat_sessions table (idempotent)
    existing_tables = inspector.get_table_names()
    if "web_chat_sessions" not in existing_tables:
        op.create_table(
            "web_chat_sessions",
            sa.Column("id",             sa.Integer(),     primary_key=True, autoincrement=True),
            sa.Column("tenant_id",      sa.String(36),    sa.ForeignKey("tenants.id"), nullable=False, index=True),
            sa.Column("session_token",  sa.String(64),    nullable=False, unique=True, index=True),
            sa.Column("reservation_id", sa.Integer(),     sa.ForeignKey("reservations.id"), nullable=True, index=True),
            sa.Column("messages_json",  sa.Text(),        nullable=True),
            sa.Column("created_at",     sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("last_active_at", sa.DateTime(timezone=True), nullable=True),
        )


def downgrade() -> None:
    op.drop_table("web_chat_sessions")
    op.drop_column("tenant_configs", "chat_token")
