"""Add SaaS features: quick_replies, upsell_offers, guest satisfaction, language,
CRM notes, review request, satisfaction pulse, upsell toggle on TenantConfig.

Revision ID: 20260410_0100
Revises: 20260409_0300
Create Date: 2026-04-10 01:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = '20260410_0100'
down_revision = '20260409_0300'
branch_labels = None
depends_on = None

def _table_names(conn) -> set[str]:
    return set(sa.inspect(conn).get_table_names())


def _column_names(conn, table_name: str) -> set[str]:
    inspector = sa.inspect(conn)
    if table_name not in inspector.get_table_names():
        return set()
    return {col["name"] for col in inspector.get_columns(table_name)}


def _index_names(conn, table_name: str) -> set[str]:
    inspector = sa.inspect(conn)
    if table_name not in inspector.get_table_names():
        return set()
    return {index["name"] for index in inspector.get_indexes(table_name)}


def upgrade() -> None:
    conn = op.get_bind()
    tables = _table_names(conn)
    tenant_columns = _column_names(conn, "tenant_configs")
    guest_contact_columns = _column_names(conn, "guest_contacts")

    # ── TenantConfig new columns ──────────────────────────────────────────
    tenant_columns_to_add = {
        "digest_email_address": sa.Column("digest_email_address", sa.String(length=255), nullable=True),
        "review_request_url": sa.Column("review_request_url", sa.String(length=512), nullable=True),
        "review_request_enabled": sa.Column(
            "review_request_enabled",
            sa.Boolean(),
            nullable=True,
            server_default=sa.text("false"),
        ),
        "satisfaction_pulse_enabled": sa.Column(
            "satisfaction_pulse_enabled",
            sa.Boolean(),
            nullable=True,
            server_default=sa.text("false"),
        ),
        "upsell_enabled": sa.Column(
            "upsell_enabled",
            sa.Boolean(),
            nullable=True,
            server_default=sa.text("false"),
        ),
    }
    if "tenant_configs" in tables:
        for column_name, column in tenant_columns_to_add.items():
            if column_name not in tenant_columns:
                op.add_column("tenant_configs", column)
                tenant_columns.add(column_name)

    # ── GuestContact new columns ──────────────────────────────────────────
    guest_contact_columns_to_add = {
        "satisfaction_sent_at": sa.Column("satisfaction_sent_at", sa.DateTime(timezone=True), nullable=True),
        "satisfaction_score": sa.Column("satisfaction_score", sa.Integer(), nullable=True),
        "satisfaction_scored_at": sa.Column("satisfaction_scored_at", sa.DateTime(timezone=True), nullable=True),
        "language_code": sa.Column("language_code", sa.String(length=8), nullable=True),
        "crm_notes": sa.Column("crm_notes", sa.Text(), nullable=True),
    }
    if "guest_contacts" in tables:
        for column_name, column in guest_contact_columns_to_add.items():
            if column_name not in guest_contact_columns:
                op.add_column("guest_contacts", column)
                guest_contact_columns.add(column_name)

    # ── QuickReply table ──────────────────────────────────────────────────
    if "quick_replies" not in tables:
        op.create_table(
            "quick_replies",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("tenant_id", sa.String(length=36), nullable=False),
            sa.Column("label", sa.String(length=128), nullable=False),
            sa.Column("message_template", sa.Text(), nullable=False),
            sa.Column("sort_order", sa.Integer(), nullable=True, server_default="0"),
            sa.Column("is_active", sa.Boolean(), nullable=True, server_default=sa.text("true")),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=True, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        )
        tables.add("quick_replies")
    if "quick_replies" in tables:
        quick_reply_indexes = _index_names(conn, "quick_replies")
        if "ix_quick_replies_tenant_id" not in quick_reply_indexes:
            op.create_index("ix_quick_replies_tenant_id", "quick_replies", ["tenant_id"], unique=False)

    # ── UpsellOffer table ─────────────────────────────────────────────────
    if "upsell_offers" not in tables:
        op.create_table(
            "upsell_offers",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("tenant_id", sa.String(length=36), nullable=False),
            sa.Column("offer_type", sa.String(length=32), nullable=False),
            sa.Column("title", sa.String(length=128), nullable=False),
            sa.Column("price_str", sa.String(length=32), nullable=False),
            sa.Column("trigger_keywords", sa.Text(), nullable=True),
            sa.Column("message_template", sa.Text(), nullable=False),
            sa.Column("is_active", sa.Boolean(), nullable=True, server_default=sa.text("true")),
            sa.Column("accepted_count", sa.Integer(), nullable=True, server_default="0"),
            sa.Column("total_revenue", sa.Float(), nullable=True, server_default="0.0"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=True, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        )
        tables.add("upsell_offers")
    if "upsell_offers" in tables:
        upsell_indexes = _index_names(conn, "upsell_offers")
        if "ix_upsell_offers_tenant_id" not in upsell_indexes:
            op.create_index("ix_upsell_offers_tenant_id", "upsell_offers", ["tenant_id"], unique=False)

    # Let Alembic manage the migration transaction/version stamp.


def downgrade() -> None:
    pass
