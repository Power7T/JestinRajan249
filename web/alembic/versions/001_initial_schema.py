"""Initial schema — creates all tables from scratch.

Revision ID: 001
Revises: None
Create Date: 2024-01-01 00:00:00

Run on a fresh database:   alembic upgrade head
Check status:              alembic current
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── tenants ───────────────────────────────────────────────────────────
    op.create_table(
        "tenants",
        sa.Column("id",                    sa.String(36),  primary_key=True),
        sa.Column("email",                 sa.String(255), nullable=False),
        sa.Column("password_hash",         sa.String(128), nullable=False),
        sa.Column("is_active",             sa.Boolean(),   nullable=False, server_default="true"),
        sa.Column("created_at",            sa.DateTime(timezone=True), nullable=False),
        sa.Column("email_verified",        sa.Boolean(),   nullable=False, server_default="false"),
        sa.Column("verification_token",    sa.String(128), nullable=True),
        sa.Column("verification_sent_at",  sa.DateTime(timezone=True), nullable=True),
        sa.Column("reset_token",           sa.String(128), nullable=True),
        sa.Column("reset_token_expires",   sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_tenants_email",              "tenants", ["email"],              unique=True)
    op.create_index("ix_tenants_verification_token", "tenants", ["verification_token"], unique=False)
    op.create_index("ix_tenants_reset_token",        "tenants", ["reset_token"],        unique=False)

    # ── tenant_configs ────────────────────────────────────────────────────
    op.create_table(
        "tenant_configs",
        sa.Column("id",                      sa.Integer(),    primary_key=True, autoincrement=True),
        sa.Column("tenant_id",               sa.String(36),   sa.ForeignKey("tenants.id"), nullable=False, unique=True),
        sa.Column("property_names",          sa.Text(),       nullable=True),
        sa.Column("ical_urls",               sa.Text(),       nullable=True),
        sa.Column("property_type",           sa.String(64),   nullable=True),
        sa.Column("property_city",           sa.String(128),  nullable=True),
        sa.Column("check_in_time",           sa.String(32),   nullable=True),
        sa.Column("check_out_time",          sa.String(32),   nullable=True),
        sa.Column("max_guests",              sa.Integer(),    nullable=True),
        sa.Column("house_rules",             sa.Text(),       nullable=True),
        sa.Column("amenities",               sa.Text(),       nullable=True),
        sa.Column("food_menu",               sa.Text(),       nullable=True),
        sa.Column("nearby_restaurants",      sa.Text(),       nullable=True),
        sa.Column("faq",                     sa.Text(),       nullable=True),
        sa.Column("custom_instructions",     sa.Text(),       nullable=True),
        sa.Column("escalation_email",        sa.String(255),  nullable=True),
        sa.Column("onboarding_complete",     sa.Boolean(),    nullable=False, server_default="false"),
        sa.Column("onboarding_step",         sa.Integer(),    nullable=False, server_default="0"),
        sa.Column("imap_host",               sa.String(255),  nullable=True),
        sa.Column("imap_port",               sa.Integer(),    nullable=False, server_default="993"),
        sa.Column("smtp_host",               sa.String(255),  nullable=True),
        sa.Column("smtp_port",               sa.Integer(),    nullable=False, server_default="587"),
        sa.Column("email_address",           sa.String(255),  nullable=True),
        sa.Column("email_password_enc",      sa.Text(),       nullable=True),
        sa.Column("anthropic_api_key_enc",   sa.Text(),       nullable=True),
        sa.Column("wa_mode",                 sa.String(32),   nullable=False, server_default="'none'"),
        sa.Column("whatsapp_number",         sa.String(32),   nullable=True),
        sa.Column("whatsapp_token_enc",      sa.Text(),       nullable=True),
        sa.Column("whatsapp_phone_id",       sa.String(64),   nullable=True),
        sa.Column("whatsapp_verify_token",   sa.String(128),  nullable=True),
        sa.Column("sms_mode",                sa.String(32),   nullable=False, server_default="'none'"),
        sa.Column("twilio_account_sid",      sa.String(64),   nullable=True),
        sa.Column("twilio_auth_token_enc",   sa.Text(),       nullable=True),
        sa.Column("twilio_from_number",      sa.String(32),   nullable=True),
        sa.Column("sms_notify_number",       sa.String(32),   nullable=True),
        sa.Column("subscription_plan",       sa.String(32),   nullable=False, server_default="'free'"),
        sa.Column("subscription_status",     sa.String(32),   nullable=False, server_default="'inactive'"),
        sa.Column("subscription_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("stripe_customer_id",      sa.String(64),   nullable=True),
        sa.Column("stripe_subscription_id",  sa.String(64),   nullable=True),
        sa.Column("bot_api_token_hash",      sa.String(128),  nullable=True),
        sa.Column("bot_api_token_hint",      sa.String(8),    nullable=True),
        sa.Column("internal_token",          sa.String(64),   nullable=False),
    )

    # ── baileys_outbound ──────────────────────────────────────────────────
    op.create_table(
        "baileys_outbound",
        sa.Column("id",           sa.Integer(),    primary_key=True, autoincrement=True),
        sa.Column("tenant_id",    sa.String(36),   sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("to_phone",     sa.String(32),   nullable=False),
        sa.Column("text",         sa.Text(),       nullable=False),
        sa.Column("created_at",   sa.DateTime(timezone=True), nullable=False),
        sa.Column("delivered",    sa.Boolean(),    nullable=False, server_default="false"),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_baileys_outbound_tenant_id", "baileys_outbound", ["tenant_id"])
    op.create_index("ix_baileys_outbound_delivered",  "baileys_outbound", ["delivered"])

    # ── drafts ────────────────────────────────────────────────────────────
    op.create_table(
        "drafts",
        sa.Column("id",           sa.String(64),   primary_key=True),
        sa.Column("tenant_id",    sa.String(36),   sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("source",       sa.String(32),   nullable=False),
        sa.Column("guest_name",   sa.String(128),  nullable=False),
        sa.Column("message",      sa.Text(),       nullable=False),
        sa.Column("reply_to",     sa.Text(),       nullable=True),
        sa.Column("msg_type",     sa.String(16),   nullable=False),
        sa.Column("vendor_type",  sa.String(32),   nullable=True),
        sa.Column("draft",        sa.Text(),       nullable=False),
        sa.Column("final_text",   sa.Text(),       nullable=True),
        sa.Column("status",       sa.String(16),   nullable=False, server_default="'pending'"),
        sa.Column("created_at",   sa.DateTime(timezone=True), nullable=False),
        sa.Column("approved_at",  sa.DateTime(timezone=True), nullable=True),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_drafts_tenant_id",    "drafts", ["tenant_id"])
    op.create_index("ix_drafts_status",       "drafts", ["status"])
    op.create_index("ix_drafts_scheduled_at", "drafts", ["scheduled_at"])

    # ── processed_emails ─────────────────────────────────────────────────
    op.create_table(
        "processed_emails",
        sa.Column("id",           sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("tenant_id",    sa.String(36), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("email_uid",    sa.String(64), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_processed_emails_tenant_id", "processed_emails", ["tenant_id"])

    # ── calendar_states ───────────────────────────────────────────────────
    op.create_table(
        "calendar_states",
        sa.Column("id",         sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("tenant_id",  sa.String(36), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("state_key",  sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_calendar_states_tenant_id", "calendar_states", ["tenant_id"])

    # ── vendors ───────────────────────────────────────────────────────────
    op.create_table(
        "vendors",
        sa.Column("id",        sa.Integer(),    primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.String(36),   sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("category",  sa.String(32),   nullable=False),
        sa.Column("name",      sa.String(128),  nullable=False),
        sa.Column("phone",     sa.String(32),   nullable=False),
        sa.Column("notes",     sa.Text(),       nullable=True),
    )
    op.create_index("ix_vendors_tenant_id", "vendors", ["tenant_id"])

    # ── activity_logs ─────────────────────────────────────────────────────
    op.create_table(
        "activity_logs",
        sa.Column("id",         sa.Integer(),    primary_key=True, autoincrement=True),
        sa.Column("tenant_id",  sa.String(36),   sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("event_type", sa.String(64),   nullable=False),
        sa.Column("message",    sa.Text(),       nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_activity_logs_tenant_id", "activity_logs", ["tenant_id"])

    # ── reservations ─────────────────────────────────────────────────────
    op.create_table(
        "reservations",
        sa.Column("id",                    sa.Integer(),    primary_key=True, autoincrement=True),
        sa.Column("tenant_id",             sa.String(36),   sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("confirmation_code",     sa.String(64),   nullable=False),
        sa.Column("guest_name",            sa.String(128),  nullable=False),
        sa.Column("listing_name",          sa.String(256),  nullable=True),
        sa.Column("checkin",               sa.Date(),       nullable=True),
        sa.Column("checkout",              sa.Date(),       nullable=True),
        sa.Column("nights",                sa.Integer(),    nullable=True),
        sa.Column("guests_count",          sa.Integer(),    nullable=True),
        sa.Column("payout_usd",            sa.Float(),      nullable=True),
        sa.Column("status",                sa.String(32),   nullable=False, server_default="'confirmed'"),
        sa.Column("imported_at",           sa.DateTime(timezone=True), nullable=False),
        sa.Column("pre_arrival_sent",      sa.Boolean(),    nullable=False, server_default="false"),
        sa.Column("checkout_msg_sent",     sa.Boolean(),    nullable=False, server_default="false"),
        sa.Column("review_reminder_sent",  sa.Boolean(),    nullable=False, server_default="false"),
        sa.Column("cleaner_brief_sent",    sa.Boolean(),    nullable=False, server_default="false"),
        sa.Column("checkin_token",         sa.String(64),   nullable=True),
        sa.UniqueConstraint("tenant_id", "confirmation_code", name="uq_reservation_tenant_code"),
    )
    op.create_index("ix_reservations_tenant_id",         "reservations", ["tenant_id"])
    op.create_index("ix_reservations_confirmation_code", "reservations", ["confirmation_code"])
    op.create_index("ix_reservations_checkin",           "reservations", ["checkin"])
    op.create_index("ix_reservations_checkout",          "reservations", ["checkout"])
    op.create_index("ix_reservations_status",            "reservations", ["status"])
    op.create_index("ix_reservations_checkin_token",     "reservations", ["checkin_token"], unique=True)

    # ── pms_integrations ─────────────────────────────────────────────────
    op.create_table(
        "pms_integrations",
        sa.Column("id",             sa.Integer(),    primary_key=True, autoincrement=True),
        sa.Column("tenant_id",      sa.String(36),   sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("pms_type",       sa.String(32),   nullable=False),
        sa.Column("api_key_enc",    sa.Text(),       nullable=False),
        sa.Column("api_base_url",   sa.Text(),       nullable=True),
        sa.Column("account_id",     sa.Text(),       nullable=True),
        sa.Column("is_active",      sa.Boolean(),    nullable=False, server_default="true"),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at",     sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_pms_integrations_tenant_id", "pms_integrations", ["tenant_id"])

    # ── pms_processed_messages ────────────────────────────────────────────
    op.create_table(
        "pms_processed_messages",
        sa.Column("id",                 sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("tenant_id",          sa.String(36), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("pms_integration_id", sa.Integer(),  sa.ForeignKey("pms_integrations.id"), nullable=False),
        sa.Column("pms_message_id",     sa.String(128), nullable=False),
        sa.Column("processed_at",       sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("pms_integration_id", "pms_message_id", name="uq_pms_msg"),
    )
    op.create_index("ix_pms_processed_messages_tenant_id",          "pms_processed_messages", ["tenant_id"])
    op.create_index("ix_pms_processed_messages_pms_integration_id", "pms_processed_messages", ["pms_integration_id"])

    # ── reservation_sync_logs ────────────────────────────────────────────
    op.create_table(
        "reservation_sync_logs",
        sa.Column("id",           sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("tenant_id",    sa.String(36), sa.ForeignKey("tenants.id"), nullable=False, unique=True),
        sa.Column("last_synced",  sa.DateTime(timezone=True), nullable=False),
        sa.Column("rows_imported", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index("ix_reservation_sync_logs_tenant_id", "reservation_sync_logs", ["tenant_id"], unique=True)


def downgrade() -> None:
    op.drop_table("reservation_sync_logs")
    op.drop_table("pms_processed_messages")
    op.drop_table("pms_integrations")
    op.drop_table("reservations")
    op.drop_table("activity_logs")
    op.drop_table("vendors")
    op.drop_table("calendar_states")
    op.drop_table("processed_emails")
    op.drop_table("drafts")
    op.drop_table("baileys_outbound")
    op.drop_table("tenant_configs")
    op.drop_table("tenants")
