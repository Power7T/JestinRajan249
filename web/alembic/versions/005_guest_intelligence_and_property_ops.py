"""guest intelligence and property ops

Revision ID: 005_guest_intelligence_and_property_ops
Revises: 004_draft_confidence
Create Date: 2026-03-22
"""
from alembic import op
import sqlalchemy as sa


revision = "005_guest_intelligence_and_property_ops"
down_revision = "004_draft_confidence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("tenant_configs") as batch_op:
        batch_op.add_column(sa.Column("pet_policy", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("refund_policy", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("early_checkin_policy", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("early_checkin_fee", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("late_checkout_policy", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("late_checkout_fee", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("parking_policy", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("smoking_policy", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("quiet_hours", sa.String(length=128), nullable=True))

    with op.batch_alter_table("team_members") as batch_op:
        batch_op.add_column(sa.Column("property_scope", sa.Text(), nullable=True))

    with op.batch_alter_table("reservations") as batch_op:
        batch_op.add_column(sa.Column("review_rating", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("review_text", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("review_submitted_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("review_sentiment", sa.String(length=16), nullable=True))
        batch_op.add_column(sa.Column("review_sentiment_score", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("guest_feedback_positive", sa.Integer(), nullable=False, server_default=sa.text("0")))
        batch_op.add_column(sa.Column("guest_feedback_negative", sa.Integer(), nullable=False, server_default=sa.text("0")))
        batch_op.add_column(sa.Column("guest_satisfaction_score", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("repeat_guest_count", sa.Integer(), nullable=False, server_default=sa.text("0")))
        batch_op.add_column(sa.Column("message_count", sa.Integer(), nullable=False, server_default=sa.text("0")))
        batch_op.add_column(sa.Column("latest_guest_sentiment", sa.String(length=16), nullable=True))
        batch_op.add_column(sa.Column("latest_guest_sentiment_score", sa.Float(), nullable=True))
        batch_op.create_index("ix_reservations_review_submitted_at", ["review_submitted_at"])
        batch_op.create_index("ix_reservations_review_sentiment", ["review_sentiment"])
        batch_op.create_index("ix_reservations_latest_guest_sentiment", ["latest_guest_sentiment"])

    with op.batch_alter_table("drafts") as batch_op:
        batch_op.add_column(sa.Column("parent_draft_id", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("thread_key", sa.String(length=128), nullable=True))
        batch_op.add_column(sa.Column("guest_message_index", sa.Integer(), nullable=False, server_default=sa.text("1")))
        batch_op.add_column(sa.Column("property_name_snapshot", sa.String(length=256), nullable=True))
        batch_op.add_column(sa.Column("unit_identifier_snapshot", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("auto_send_eligible", sa.Boolean(), nullable=False, server_default=sa.text("false")))
        batch_op.add_column(sa.Column("guest_history_score", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("guest_sentiment", sa.String(length=16), nullable=True))
        batch_op.add_column(sa.Column("sentiment_score", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("stay_stage", sa.String(length=32), nullable=True))
        batch_op.add_column(sa.Column("policy_conflicts_json", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("host_feedback_score", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("host_feedback_note", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("host_feedback_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.create_foreign_key("fk_drafts_parent_draft_id", "drafts", ["parent_draft_id"], ["id"])
        batch_op.create_index("ix_drafts_parent_draft_id", ["parent_draft_id"])
        batch_op.create_index("ix_drafts_thread_key", ["thread_key"])
        batch_op.create_index("ix_drafts_property_name_snapshot", ["property_name_snapshot"])
        batch_op.create_index("ix_drafts_unit_identifier_snapshot", ["unit_identifier_snapshot"])
        batch_op.create_index("ix_drafts_auto_send_eligible", ["auto_send_eligible"])
        batch_op.create_index("ix_drafts_guest_sentiment", ["guest_sentiment"])
        batch_op.create_index("ix_drafts_stay_stage", ["stay_stage"])


def downgrade() -> None:
    with op.batch_alter_table("drafts") as batch_op:
        batch_op.drop_index("ix_drafts_stay_stage")
        batch_op.drop_index("ix_drafts_guest_sentiment")
        batch_op.drop_index("ix_drafts_auto_send_eligible")
        batch_op.drop_index("ix_drafts_unit_identifier_snapshot")
        batch_op.drop_index("ix_drafts_property_name_snapshot")
        batch_op.drop_index("ix_drafts_thread_key")
        batch_op.drop_index("ix_drafts_parent_draft_id")
        batch_op.drop_constraint("fk_drafts_parent_draft_id", type_="foreignkey")
        batch_op.drop_column("host_feedback_at")
        batch_op.drop_column("host_feedback_note")
        batch_op.drop_column("host_feedback_score")
        batch_op.drop_column("policy_conflicts_json")
        batch_op.drop_column("stay_stage")
        batch_op.drop_column("sentiment_score")
        batch_op.drop_column("guest_sentiment")
        batch_op.drop_column("guest_history_score")
        batch_op.drop_column("auto_send_eligible")
        batch_op.drop_column("unit_identifier_snapshot")
        batch_op.drop_column("property_name_snapshot")
        batch_op.drop_column("guest_message_index")
        batch_op.drop_column("thread_key")
        batch_op.drop_column("parent_draft_id")

    with op.batch_alter_table("reservations") as batch_op:
        batch_op.drop_index("ix_reservations_latest_guest_sentiment")
        batch_op.drop_index("ix_reservations_review_sentiment")
        batch_op.drop_index("ix_reservations_review_submitted_at")
        batch_op.drop_column("latest_guest_sentiment_score")
        batch_op.drop_column("latest_guest_sentiment")
        batch_op.drop_column("message_count")
        batch_op.drop_column("repeat_guest_count")
        batch_op.drop_column("guest_satisfaction_score")
        batch_op.drop_column("guest_feedback_negative")
        batch_op.drop_column("guest_feedback_positive")
        batch_op.drop_column("review_sentiment_score")
        batch_op.drop_column("review_sentiment")
        batch_op.drop_column("review_submitted_at")
        batch_op.drop_column("review_text")
        batch_op.drop_column("review_rating")

    with op.batch_alter_table("team_members") as batch_op:
        batch_op.drop_column("property_scope")

    with op.batch_alter_table("tenant_configs") as batch_op:
        batch_op.drop_column("quiet_hours")
        batch_op.drop_column("smoking_policy")
        batch_op.drop_column("parking_policy")
        batch_op.drop_column("late_checkout_fee")
        batch_op.drop_column("late_checkout_policy")
        batch_op.drop_column("early_checkin_fee")
        batch_op.drop_column("early_checkin_policy")
        batch_op.drop_column("refund_policy")
        batch_op.drop_column("pet_policy")
