"""Restore legacy tenant email columns for rollout compatibility.

Revision ID: 20260419_0800
Revises: 20260419_0700
Create Date: 2026-04-19 08:00:00.000000
"""
from __future__ import annotations

from typing import Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision: str = "20260419_0800"
down_revision: Union[str, None] = "20260419_0700"
branch_labels = None
depends_on = None

_TABLE = "tenant_configs"
_INDEX = "ix_tenant_configs_inbound_email_alias"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if _TABLE not in inspector.get_table_names():
        return

    existing_columns = {col["name"] for col in inspector.get_columns(_TABLE)}
    existing_indexes = {idx["name"] for idx in inspector.get_indexes(_TABLE)}
    legacy_columns = [
        ("imap_host", sa.Column("imap_host", sa.String(255), nullable=True)),
        ("imap_port", sa.Column("imap_port", sa.Integer(), nullable=False, server_default="993")),
        ("smtp_host", sa.Column("smtp_host", sa.String(255), nullable=True)),
        ("smtp_port", sa.Column("smtp_port", sa.Integer(), nullable=False, server_default="587")),
        ("email_address", sa.Column("email_address", sa.String(255), nullable=True)),
        ("email_password_enc", sa.Column("email_password_enc", sa.Text(), nullable=True)),
        ("email_ingest_mode", sa.Column("email_ingest_mode", sa.String(32), nullable=False, server_default="imap")),
        ("inbound_email_alias", sa.Column("inbound_email_alias", sa.String(64), nullable=True)),
        ("last_inbound_email_at", sa.Column("last_inbound_email_at", sa.DateTime(timezone=True), nullable=True)),
    ]

    for column_name, column in legacy_columns:
        if column_name not in existing_columns:
            op.add_column(_TABLE, column)

    if "inbound_email_alias" in {col["name"] for col in inspector.get_columns(_TABLE)} and _INDEX not in existing_indexes:
        op.create_index(_INDEX, _TABLE, ["inbound_email_alias"], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if _TABLE not in inspector.get_table_names():
        return

    existing_columns = {col["name"] for col in inspector.get_columns(_TABLE)}
    existing_indexes = {idx["name"] for idx in inspector.get_indexes(_TABLE)}

    if _INDEX in existing_indexes:
        op.drop_index(_INDEX, table_name=_TABLE)

    for column_name in (
        "last_inbound_email_at",
        "inbound_email_alias",
        "email_ingest_mode",
        "email_password_enc",
        "email_address",
        "smtp_port",
        "smtp_host",
        "imap_port",
        "imap_host",
    ):
        if column_name in existing_columns:
            op.drop_column(_TABLE, column_name)
