"""Full parity build: proactive messaging, learning loop, agentic actions,
ops task dispatch, booking inquiries, vision PMS sessions.

Revision ID: 20260528_0200
Revises: 20260528_0100
Create Date: 2026-05-28
"""
from typing import Union
import sqlalchemy as sa
from alembic import op

revision: str = "20260528_0200"
down_revision: Union[str, None] = "20260528_0100"
branch_labels = None
depends_on = None


def _cols(conn, table: str) -> set:
    return {row[0] for row in conn.execute(
        sa.text("SELECT column_name FROM information_schema.columns WHERE table_name=:t"),
        {"t": table},
    )}


def _has_table(conn, table: str) -> bool:
    return bool(conn.execute(
        sa.text("SELECT to_regclass(:t)"), {"t": f"public.{table}"}
    ).scalar())


def upgrade() -> None:
    conn = op.get_bind()

    # ── tenant_configs new columns ────────────────────────────────────────
    tc = _cols(conn, "tenant_configs")
    add_tc = [
        ("proactive_enabled",           sa.Column("proactive_enabled", sa.Boolean(), nullable=False, server_default="false")),
        ("proactive_pre_arrival_h",     sa.Column("proactive_pre_arrival_h", sa.Integer(), nullable=False, server_default="24")),
        ("proactive_mid_stay_day",      sa.Column("proactive_mid_stay_day", sa.Integer(), nullable=False, server_default="2")),
        ("proactive_review_delay_days", sa.Column("proactive_review_delay_days", sa.Integer(), nullable=False, server_default="2")),
        ("proactive_triggers",          sa.Column("proactive_triggers", sa.Text(), nullable=False,
                                                   server_default="pre_arrival,checkin_day,mid_stay,checkout,review_request")),
        ("ai_learning_notes",           sa.Column("ai_learning_notes", sa.Text(), nullable=True)),
        ("ai_corrections_count",        sa.Column("ai_corrections_count", sa.Integer(), nullable=False, server_default="0")),
        ("action_auto_approve",         sa.Column("action_auto_approve", sa.Text(), nullable=False, server_default="")),
    ]
    for name, col in add_tc:
        if name not in tc:
            op.add_column("tenant_configs", col)

    # ── reservations new flags ────────────────────────────────────────────
    res = _cols(conn, "reservations")
    for name in ("checkin_day_msg_sent", "mid_stay_msg_sent"):
        if name not in res:
            op.add_column("reservations", sa.Column(name, sa.Boolean(), nullable=False, server_default="false"))

    # ── team_members new columns ──────────────────────────────────────────
    tm = _cols(conn, "team_members")
    if "task_types" not in tm:
        op.add_column("team_members", sa.Column("task_types", sa.Text(), nullable=False, server_default=""))
    if "notify_phone" not in tm:
        op.add_column("team_members", sa.Column("notify_phone", sa.String(length=32), nullable=True))
    if "notify_channel" not in tm:
        op.add_column("team_members", sa.Column("notify_channel", sa.String(length=16), nullable=False, server_default="sms"))

    # ── proactive_messages ────────────────────────────────────────────────
    if not _has_table(conn, "proactive_messages"):
        op.create_table(
            "proactive_messages",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("tenant_id", sa.String(length=36), sa.ForeignKey("tenants.id"), index=True),
            sa.Column("reservation_id", sa.Integer(), sa.ForeignKey("reservations.id"), nullable=True, index=True),
            sa.Column("trigger_type", sa.String(length=32), index=True),
            sa.Column("scheduled_at", sa.DateTime(timezone=True), index=True),
            sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("status", sa.String(length=16), server_default="pending", index=True),
            sa.Column("channel", sa.String(length=16), nullable=True),
            sa.Column("message_text", sa.Text(), nullable=True),
            sa.Column("draft_id", sa.Integer(), sa.ForeignKey("drafts.id"), nullable=True),
            sa.Column("error_reason", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.UniqueConstraint("reservation_id", "trigger_type", name="uq_proactive_res_trigger"),
        )

    # ── draft_corrections ─────────────────────────────────────────────────
    if not _has_table(conn, "draft_corrections"):
        op.create_table(
            "draft_corrections",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("tenant_id", sa.String(length=36), sa.ForeignKey("tenants.id"), index=True),
            sa.Column("draft_id", sa.Integer(), sa.ForeignKey("drafts.id"), nullable=True),
            sa.Column("original_text", sa.Text()),
            sa.Column("corrected_text", sa.Text()),
            sa.Column("guest_message", sa.Text(), nullable=True),
            sa.Column("msg_type", sa.String(length=32), nullable=True),
            sa.Column("sentiment", sa.String(length=16), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), index=True),
        )

    # ── guest_actions ─────────────────────────────────────────────────────
    if not _has_table(conn, "guest_actions"):
        op.create_table(
            "guest_actions",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("tenant_id", sa.String(length=36), sa.ForeignKey("tenants.id"), index=True),
            sa.Column("reservation_id", sa.Integer(), sa.ForeignKey("reservations.id"), nullable=True, index=True),
            sa.Column("draft_id", sa.Integer(), sa.ForeignKey("drafts.id"), nullable=True),
            sa.Column("action_type", sa.String(length=64), index=True),
            sa.Column("params_json", sa.JSON(), nullable=True),
            sa.Column("status", sa.String(length=16), server_default="pending", index=True),
            sa.Column("result_json", sa.JSON(), nullable=True),
            sa.Column("requires_approval", sa.Boolean(), server_default="true"),
            sa.Column("executed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), index=True),
        )

    # ── operations_tasks ──────────────────────────────────────────────────
    if not _has_table(conn, "operations_tasks"):
        op.create_table(
            "operations_tasks",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("tenant_id", sa.String(length=36), sa.ForeignKey("tenants.id"), index=True),
            sa.Column("reservation_id", sa.Integer(), sa.ForeignKey("reservations.id"), nullable=True, index=True),
            sa.Column("draft_id", sa.Integer(), sa.ForeignKey("drafts.id"), nullable=True),
            sa.Column("assigned_to", sa.Integer(), sa.ForeignKey("team_members.id"), nullable=True, index=True),
            sa.Column("task_type", sa.String(length=32), index=True),
            sa.Column("priority", sa.String(length=16), server_default="normal", index=True),
            sa.Column("title", sa.Text()),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("status", sa.String(length=16), server_default="open", index=True),
            sa.Column("sla_minutes", sa.Integer(), nullable=True),
            sa.Column("guest_phone", sa.String(length=32), nullable=True, index=True),
            sa.Column("guest_name", sa.String(length=128), nullable=True),
            sa.Column("guest_channel", sa.String(length=16), nullable=True),
            sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("resolution_note", sa.Text(), nullable=True),
            sa.Column("guest_notified", sa.Boolean(), server_default="false"),
            sa.Column("escalated", sa.Boolean(), server_default="false"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), index=True),
        )

    # ── booking_inquiries ─────────────────────────────────────────────────
    if not _has_table(conn, "booking_inquiries"):
        op.create_table(
            "booking_inquiries",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("tenant_id", sa.String(length=36), sa.ForeignKey("tenants.id"), index=True),
            sa.Column("channel", sa.String(length=16), index=True),
            sa.Column("contact_phone", sa.String(length=32), nullable=True, index=True),
            sa.Column("contact_email", sa.String(length=255), nullable=True),
            sa.Column("contact_name", sa.String(length=255), nullable=True),
            sa.Column("requested_dates_json", sa.JSON(), nullable=True),
            sa.Column("listing_id", sa.String(length=64), nullable=True),
            sa.Column("listing_name", sa.String(length=256), nullable=True),
            sa.Column("status", sa.String(length=16), server_default="open", index=True),
            sa.Column("quoted_price", sa.Float(), nullable=True),
            sa.Column("availability_checked", sa.Boolean(), server_default="false"),
            sa.Column("is_available", sa.Boolean(), nullable=True),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), index=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )

    # ── vision_pms_sessions ───────────────────────────────────────────────
    if not _has_table(conn, "vision_pms_sessions"):
        op.create_table(
            "vision_pms_sessions",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("integration_id", sa.Integer(), sa.ForeignKey("pms_integrations.id"), nullable=True, index=True),
            sa.Column("tenant_id", sa.String(length=36), sa.ForeignKey("tenants.id"), index=True),
            sa.Column("cookies_enc", sa.Text(), nullable=True),
            sa.Column("last_url", sa.String(length=512), nullable=True),
            sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("valid_until", sa.DateTime(timezone=True), nullable=True),
            sa.Column("awaiting_otp", sa.Boolean(), server_default="false"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )


def downgrade() -> None:
    for t in ("vision_pms_sessions", "booking_inquiries", "operations_tasks",
              "guest_actions", "draft_corrections", "proactive_messages"):
        op.execute(f"DROP TABLE IF EXISTS {t}")
    for c in ("task_types", "notify_phone", "notify_channel"):
        op.execute(f"ALTER TABLE team_members DROP COLUMN IF EXISTS {c}")
    for c in ("checkin_day_msg_sent", "mid_stay_msg_sent"):
        op.execute(f"ALTER TABLE reservations DROP COLUMN IF EXISTS {c}")
    for c in ("proactive_enabled", "proactive_pre_arrival_h", "proactive_mid_stay_day",
              "proactive_review_delay_days", "proactive_triggers", "ai_learning_notes",
              "ai_corrections_count", "action_auto_approve"):
        op.execute(f"ALTER TABLE tenant_configs DROP COLUMN IF EXISTS {c}")
