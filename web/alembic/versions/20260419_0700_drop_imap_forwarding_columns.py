"""drop imap and email forwarding columns

Revision ID: 20260419_0700
Revises: 20260417_0600
Create Date: 2026-04-19
"""
from __future__ import annotations
from typing import Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision: str = "20260419_0700"
down_revision: Union[str, None] = "20260417_0600"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    existing = {col["name"] for col in inspector.get_columns("tenant_configs")}

    cols_to_drop = [
        "imap_host",
        "imap_port",
        "smtp_host",
        "smtp_port",
        "email_address",
        "email_password_enc",
        "email_ingest_mode",
        "inbound_email_alias",
        "last_inbound_email_at",
    ]
    for col in cols_to_drop:
        if col in existing:
            op.drop_column("tenant_configs", col)


def downgrade() -> None:
    op.add_column("tenant_configs", sa.Column("imap_host", sa.String(255), nullable=True))
    op.add_column("tenant_configs", sa.Column("imap_port", sa.Integer(), nullable=False, server_default="993"))
    op.add_column("tenant_configs", sa.Column("smtp_host", sa.String(255), nullable=True))
    op.add_column("tenant_configs", sa.Column("smtp_port", sa.Integer(), nullable=False, server_default="587"))
    op.add_column("tenant_configs", sa.Column("email_address", sa.String(255), nullable=True))
    op.add_column("tenant_configs", sa.Column("email_password_enc", sa.Text(), nullable=True))
    op.add_column("tenant_configs", sa.Column("email_ingest_mode", sa.String(32), nullable=False, server_default="imap"))
    op.add_column("tenant_configs", sa.Column("inbound_email_alias", sa.String(64), nullable=True))
    op.add_column("tenant_configs", sa.Column("last_inbound_email_at", sa.DateTime(timezone=True), nullable=True))
