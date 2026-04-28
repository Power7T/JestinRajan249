"""Add language fields, upsell tables, and sales AI tables

Revision ID: 20260428_1000
Revises: 20260404_1000
Create Date: 2026-04-28 10:00:00.000000+00:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260428_1000"
down_revision: Union[str, None] = "20260404_1000"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    # ── Phase 1: Language fields ─────────────────────────────────────────────
    existing_draft_cols = {c["name"] for c in inspector.get_columns("drafts")}
    if "detected_language" not in existing_draft_cols:
        op.add_column("drafts", sa.Column("detected_language", sa.String(8), nullable=True))

    existing_cfg_cols = {c["name"] for c in inspector.get_columns("tenant_configs")}
    if "preferred_reply_language" not in existing_cfg_cols:
        op.add_column("tenant_configs", sa.Column("preferred_reply_language", sa.String(8), nullable=True))
    if "secondary_reply_language" not in existing_cfg_cols:
        op.add_column("tenant_configs", sa.Column("secondary_reply_language", sa.String(8), nullable=True))

    # ── Phase 2: Upsell flags on reservations ───────────────────────────────
    existing_res_cols = {c["name"] for c in inspector.get_columns("reservations")}
    if "upsell_booking_sent" not in existing_res_cols:
        op.add_column("reservations", sa.Column("upsell_booking_sent", sa.Boolean(), nullable=False, server_default=sa.false()))
    if "upsell_pre_arrival_sent" not in existing_res_cols:
        op.add_column("reservations", sa.Column("upsell_pre_arrival_sent", sa.Boolean(), nullable=False, server_default=sa.false()))
    if "upsell_mid_stay_sent" not in existing_res_cols:
        op.add_column("reservations", sa.Column("upsell_mid_stay_sent", sa.Boolean(), nullable=False, server_default=sa.false()))

    # ── Phase 2: Upsell tables ───────────────────────────────────────────────
    # upsell_offers may already exist (created by 20260410_0100_add_saas_features.py)
    # — add only the new time-trigger columns if missing
    if "upsell_offers" not in existing_tables:
        op.create_table(
            "upsell_offers",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id"), nullable=False, index=True),
            sa.Column("offer_type", sa.String(32), nullable=True),
            sa.Column("title", sa.String(128), nullable=True),
            sa.Column("price_str", sa.String(32), nullable=True),
            sa.Column("trigger_keywords", sa.Text(), nullable=True),
            sa.Column("message_template", sa.Text(), nullable=True),
            sa.Column("accepted_count", sa.Integer(), nullable=True, server_default="0"),
            sa.Column("total_revenue", sa.Float(), nullable=True, server_default="0.0"),
            sa.Column("name", sa.String(128), nullable=True),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("price_usd", sa.Float(), nullable=True),
            sa.Column("trigger_point", sa.String(32), nullable=True),
            sa.Column("trigger_days_before", sa.Integer(), nullable=True),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("sort_order", sa.Integer(), nullable=False, server_default="100"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        )
    else:
        # Table already exists — add the new columns only if missing
        existing_offer_cols = {c["name"] for c in inspector.get_columns("upsell_offers")}
        new_offer_cols = {
            "name":               sa.Column("name", sa.String(128), nullable=True),
            "description":        sa.Column("description", sa.Text(), nullable=True),
            "price_usd":          sa.Column("price_usd", sa.Float(), nullable=True),
            "trigger_point":      sa.Column("trigger_point", sa.String(32), nullable=True),
            "trigger_days_before": sa.Column("trigger_days_before", sa.Integer(), nullable=True),
            "sort_order":         sa.Column("sort_order", sa.Integer(), nullable=True, server_default="100"),
        }
        for col_name, col_def in new_offer_cols.items():
            if col_name not in existing_offer_cols:
                op.add_column("upsell_offers", col_def)

    if "upsell_sends" not in existing_tables:
        op.create_table(
            "upsell_sends",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id"), nullable=False, index=True),
            sa.Column("offer_id", sa.Integer(), sa.ForeignKey("upsell_offers.id"), nullable=False, index=True),
            sa.Column("reservation_id", sa.Integer(), sa.ForeignKey("reservations.id"), nullable=False, index=True),
            sa.Column("draft_id", sa.String(64), nullable=True, index=True),
            sa.Column("channel", sa.String(32), nullable=False),
            sa.Column("status", sa.String(16), nullable=False, server_default="sent"),
            sa.Column("guest_response_text", sa.Text(), nullable=True),
            sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.UniqueConstraint("offer_id", "reservation_id", name="uq_upsell_send"),
        )

    # ── Phase 3: Sales AI tables ─────────────────────────────────────────────
    if "sales_configs" not in existing_tables:
        op.create_table(
            "sales_configs",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id"), nullable=False, unique=True, index=True),
            sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("ai_persona_name", sa.String(128), nullable=False, server_default="Your Host"),
            sa.Column("pricing_note", sa.Text(), nullable=True),
            sa.Column("booking_link", sa.Text(), nullable=True),
            sa.Column("max_conv_turns", sa.Integer(), nullable=False, server_default="10"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        )

    if "sales_conversations" not in existing_tables:
        op.create_table(
            "sales_conversations",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id"), nullable=False, index=True),
            sa.Column("channel", sa.String(16), nullable=False),
            sa.Column("lead_phone", sa.String(32), nullable=True, index=True),
            sa.Column("lead_email", sa.String(255), nullable=True),
            sa.Column("lead_name", sa.String(128), nullable=True),
            sa.Column("status", sa.String(24), nullable=False, server_default="open"),
            sa.Column("detected_language", sa.String(8), nullable=False, server_default="en"),
            sa.Column("turn_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("booking_sent_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        )

    if "sales_messages" not in existing_tables:
        op.create_table(
            "sales_messages",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("conversation_id", sa.String(36), sa.ForeignKey("sales_conversations.id"), nullable=False, index=True),
            sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id"), nullable=False, index=True),
            sa.Column("direction", sa.String(8), nullable=False),
            sa.Column("body", sa.Text(), nullable=False),
            sa.Column("channel", sa.String(16), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        )


def downgrade() -> None:
    op.drop_table("sales_messages")
    op.drop_table("sales_conversations")
    op.drop_table("sales_configs")
    op.drop_table("upsell_sends")
    op.drop_table("upsell_offers")
    op.drop_column("reservations", "upsell_mid_stay_sent")
    op.drop_column("reservations", "upsell_pre_arrival_sent")
    op.drop_column("reservations", "upsell_booking_sent")
    op.drop_column("tenant_configs", "secondary_reply_language")
    op.drop_column("tenant_configs", "preferred_reply_language")
    op.drop_column("drafts", "detected_language")
