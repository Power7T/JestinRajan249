"""workflow and ingest schema

Revision ID: 003_workflow_and_ingest_schema
Revises: 002_reservation_guest_context
Create Date: 2026-03-22
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "003_workflow_and_ingest_schema"
down_revision = "002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # -- team_members ---------------------------------------------
    op.create_table(
        "team_members",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.String(length=36), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("display_name", sa.String(length=128), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("phone", sa.String(length=32), nullable=True),
        sa.Column("role", sa.String(length=32), nullable=False, server_default=sa.text("'manager'")),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("permissions_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_id", "email", name="uq_team_member_tenant_email"),
    )
    op.create_index("ix_team_members_tenant_id", "team_members", ["tenant_id"])
    op.create_index("ix_team_members_role", "team_members", ["role"])
    op.create_index("ix_team_members_is_active", "team_members", ["is_active"])

    # -- automation_rules -----------------------------------------
    op.create_table(
        "automation_rules",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.String(length=36), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("trigger_kind", sa.String(length=32), nullable=False, server_default=sa.text("'inbound_message'")),
        sa.Column("scope_kind", sa.String(length=32), nullable=False, server_default=sa.text("'tenant'")),
        sa.Column("channel", sa.String(length=32), nullable=False, server_default=sa.text("'any'")),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("priority", sa.Integer(), nullable=False, server_default=sa.text("100")),
        sa.Column("confidence_threshold", sa.Float(), nullable=False, server_default=sa.text("0")),
        sa.Column("conditions_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("actions_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("last_triggered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_automation_rules_tenant_id", "automation_rules", ["tenant_id"])
    op.create_index("ix_automation_rules_trigger_kind", "automation_rules", ["trigger_kind"])
    op.create_index("ix_automation_rules_scope_kind", "automation_rules", ["scope_kind"])
    op.create_index("ix_automation_rules_channel", "automation_rules", ["channel"])
    op.create_index("ix_automation_rules_is_active", "automation_rules", ["is_active"])
    op.create_index("ix_automation_rules_priority", "automation_rules", ["priority"])

    # -- reservation_intake_batches ------------------------------
    op.create_table(
        "reservation_intake_batches",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.String(length=36), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("source_kind", sa.String(length=16), nullable=False, server_default=sa.text("'csv'")),
        sa.Column("source_name", sa.String(length=128), nullable=True),
        sa.Column("external_reference", sa.String(length=128), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default=sa.text("'queued'")),
        sa.Column("rows_total", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("rows_imported", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("rows_failed", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("details_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("pms_integration_id", sa.Integer(), sa.ForeignKey("pms_integrations.id"), nullable=True),
        sa.Column("created_by_member_id", sa.Integer(), sa.ForeignKey("team_members.id"), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_reservation_intake_batches_tenant_id", "reservation_intake_batches", ["tenant_id"])
    op.create_index("ix_reservation_intake_batches_source_kind", "reservation_intake_batches", ["source_kind"])
    op.create_index("ix_reservation_intake_batches_external_reference", "reservation_intake_batches", ["external_reference"])
    op.create_index("ix_reservation_intake_batches_status", "reservation_intake_batches", ["status"])
    op.create_index("ix_reservation_intake_batches_pms_integration_id", "reservation_intake_batches", ["pms_integration_id"])
    op.create_index("ix_reservation_intake_batches_created_by_member_id", "reservation_intake_batches", ["created_by_member_id"])

    # -- issue_tickets -------------------------------------------
    op.create_table(
        "issue_tickets",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.String(length=36), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("reservation_id", sa.Integer(), sa.ForeignKey("reservations.id"), nullable=True),
        sa.Column("created_by_member_id", sa.Integer(), sa.ForeignKey("team_members.id"), nullable=True),
        sa.Column("assigned_to_member_id", sa.Integer(), sa.ForeignKey("team_members.id"), nullable=True),
        sa.Column("vendor_id", sa.Integer(), sa.ForeignKey("vendors.id"), nullable=True),
        sa.Column("property_name", sa.String(length=256), nullable=True),
        sa.Column("unit_identifier", sa.String(length=64), nullable=True),
        sa.Column("guest_name", sa.String(length=128), nullable=True),
        sa.Column("guest_phone", sa.String(length=32), nullable=True),
        sa.Column("category", sa.String(length=32), nullable=False, server_default=sa.text("'general'")),
        sa.Column("priority", sa.String(length=16), nullable=False, server_default=sa.text("'medium'")),
        sa.Column("status", sa.String(length=32), nullable=False, server_default=sa.text("'open'")),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("resolution_notes", sa.Text(), nullable=True),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("payload_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_issue_tickets_tenant_id", "issue_tickets", ["tenant_id"])
    op.create_index("ix_issue_tickets_reservation_id", "issue_tickets", ["reservation_id"])
    op.create_index("ix_issue_tickets_created_by_member_id", "issue_tickets", ["created_by_member_id"])
    op.create_index("ix_issue_tickets_assigned_to_member_id", "issue_tickets", ["assigned_to_member_id"])
    op.create_index("ix_issue_tickets_vendor_id", "issue_tickets", ["vendor_id"])
    op.create_index("ix_issue_tickets_unit_identifier", "issue_tickets", ["unit_identifier"])
    op.create_index("ix_issue_tickets_guest_phone", "issue_tickets", ["guest_phone"])
    op.create_index("ix_issue_tickets_category", "issue_tickets", ["category"])
    op.create_index("ix_issue_tickets_priority", "issue_tickets", ["priority"])
    op.create_index("ix_issue_tickets_status", "issue_tickets", ["status"])
    op.create_index("ix_issue_tickets_resolved_at", "issue_tickets", ["resolved_at"])
    op.create_index("ix_issue_tickets_created_at", "issue_tickets", ["created_at"])

    # -- guest_timeline_events ----------------------------------
    op.create_table(
        "guest_timeline_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.String(length=36), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("reservation_id", sa.Integer(), sa.ForeignKey("reservations.id"), nullable=True),
        sa.Column("draft_id", sa.String(length=64), sa.ForeignKey("drafts.id"), nullable=True),
        sa.Column("issue_ticket_id", sa.Integer(), sa.ForeignKey("issue_tickets.id"), nullable=True),
        sa.Column("automation_rule_id", sa.Integer(), sa.ForeignKey("automation_rules.id"), nullable=True),
        sa.Column("intake_batch_id", sa.Integer(), sa.ForeignKey("reservation_intake_batches.id"), nullable=True),
        sa.Column("created_by_member_id", sa.Integer(), sa.ForeignKey("team_members.id"), nullable=True),
        sa.Column("guest_name", sa.String(length=128), nullable=True),
        sa.Column("guest_phone", sa.String(length=32), nullable=True),
        sa.Column("property_name", sa.String(length=256), nullable=True),
        sa.Column("unit_identifier", sa.String(length=64), nullable=True),
        sa.Column("channel", sa.String(length=32), nullable=False, server_default=sa.text("'system'")),
        sa.Column("direction", sa.String(length=16), nullable=False, server_default=sa.text("'internal'")),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("summary", sa.String(length=255), nullable=False),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("payload_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_guest_timeline_events_tenant_id", "guest_timeline_events", ["tenant_id"])
    op.create_index("ix_guest_timeline_events_reservation_id", "guest_timeline_events", ["reservation_id"])
    op.create_index("ix_guest_timeline_events_draft_id", "guest_timeline_events", ["draft_id"])
    op.create_index("ix_guest_timeline_events_issue_ticket_id", "guest_timeline_events", ["issue_ticket_id"])
    op.create_index("ix_guest_timeline_events_automation_rule_id", "guest_timeline_events", ["automation_rule_id"])
    op.create_index("ix_guest_timeline_events_intake_batch_id", "guest_timeline_events", ["intake_batch_id"])
    op.create_index("ix_guest_timeline_events_created_by_member_id", "guest_timeline_events", ["created_by_member_id"])
    op.create_index("ix_guest_timeline_events_guest_phone", "guest_timeline_events", ["guest_phone"])
    op.create_index("ix_guest_timeline_events_unit_identifier", "guest_timeline_events", ["unit_identifier"])
    op.create_index("ix_guest_timeline_events_channel", "guest_timeline_events", ["channel"])
    op.create_index("ix_guest_timeline_events_direction", "guest_timeline_events", ["direction"])
    op.create_index("ix_guest_timeline_events_event_type", "guest_timeline_events", ["event_type"])
    op.create_index("ix_guest_timeline_events_created_at", "guest_timeline_events", ["created_at"])

    # -- arrival_activations ------------------------------------
    op.create_table(
        "arrival_activations",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.String(length=36), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("reservation_id", sa.Integer(), sa.ForeignKey("reservations.id"), nullable=True),
        sa.Column("timeline_event_id", sa.Integer(), sa.ForeignKey("guest_timeline_events.id"), nullable=True),
        sa.Column("created_by_member_id", sa.Integer(), sa.ForeignKey("team_members.id"), nullable=True),
        sa.Column("property_name", sa.String(length=256), nullable=True),
        sa.Column("unit_identifier", sa.String(length=64), nullable=True),
        sa.Column("guest_name", sa.String(length=128), nullable=True),
        sa.Column("guest_phone", sa.String(length=32), nullable=True),
        sa.Column("activation_source", sa.String(length=32), nullable=False, server_default=sa.text("'manual'")),
        sa.Column("status", sa.String(length=32), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deactivated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("payload_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_arrival_activations_tenant_id", "arrival_activations", ["tenant_id"])
    op.create_index("ix_arrival_activations_reservation_id", "arrival_activations", ["reservation_id"])
    op.create_index("ix_arrival_activations_timeline_event_id", "arrival_activations", ["timeline_event_id"])
    op.create_index("ix_arrival_activations_created_by_member_id", "arrival_activations", ["created_by_member_id"])
    op.create_index("ix_arrival_activations_unit_identifier", "arrival_activations", ["unit_identifier"])
    op.create_index("ix_arrival_activations_guest_phone", "arrival_activations", ["guest_phone"])
    op.create_index("ix_arrival_activations_activation_source", "arrival_activations", ["activation_source"])
    op.create_index("ix_arrival_activations_status", "arrival_activations", ["status"])

    # -- tenant_kpi_snapshots -----------------------------------
    op.create_table(
        "tenant_kpi_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.String(length=36), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("property_name", sa.String(length=256), nullable=True),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("messages_total", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("drafts_total", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("auto_sent_total", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("approvals_total", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("escalations_total", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("open_issues_total", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("resolved_issues_total", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("avg_response_seconds", sa.Float(), nullable=True),
        sa.Column("automation_rate_pct", sa.Float(), nullable=True),
        sa.Column("edit_rate_pct", sa.Float(), nullable=True),
        sa.Column("saved_hours", sa.Float(), nullable=True),
        sa.Column("payload_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "tenant_id",
            "property_name",
            "period_start",
            "period_end",
            name="uq_tenant_kpi_snapshot_window",
        ),
    )
    op.create_index("ix_tenant_kpi_snapshots_tenant_id", "tenant_kpi_snapshots", ["tenant_id"])
    op.create_index("ix_tenant_kpi_snapshots_property_name", "tenant_kpi_snapshots", ["property_name"])
    op.create_index("ix_tenant_kpi_snapshots_period_start", "tenant_kpi_snapshots", ["period_start"])
    op.create_index("ix_tenant_kpi_snapshots_period_end", "tenant_kpi_snapshots", ["period_end"])

    # -- tenant_configs additions -------------------------------
    with op.batch_alter_table("tenant_configs") as batch_op:
        batch_op.add_column(sa.Column("email_ingest_mode", sa.String(length=32), nullable=True, server_default=sa.text("'imap'")))
        batch_op.add_column(sa.Column("inbound_email_alias", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("last_inbound_email_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.create_index("ix_tenant_configs_inbound_email_alias", ["inbound_email_alias"])

    # -- reservations additions ---------------------------------
    with op.batch_alter_table("reservations") as batch_op:
        batch_op.add_column(sa.Column("intake_batch_id", sa.Integer(), sa.ForeignKey("reservation_intake_batches.id"), nullable=True))
        batch_op.add_column(sa.Column("last_guest_message_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("last_host_reply_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.create_index("ix_reservations_intake_batch_id", ["intake_batch_id"])
        batch_op.create_index("ix_reservations_last_guest_message_at", ["last_guest_message_at"])
        batch_op.create_index("ix_reservations_last_host_reply_at", ["last_host_reply_at"])

    # -- drafts additions ---------------------------------------
    with op.batch_alter_table("drafts") as batch_op:
        batch_op.add_column(sa.Column("reservation_id", sa.Integer(), sa.ForeignKey("reservations.id"), nullable=True))
        batch_op.add_column(sa.Column("automation_rule_id", sa.Integer(), sa.ForeignKey("automation_rules.id"), nullable=True))
        batch_op.create_index("ix_drafts_reservation_id", ["reservation_id"])
        batch_op.create_index("ix_drafts_automation_rule_id", ["automation_rule_id"])


def downgrade() -> None:
    # -- drafts ------------------------------------------------
    with op.batch_alter_table("drafts") as batch_op:
        batch_op.drop_index("ix_drafts_automation_rule_id")
        batch_op.drop_index("ix_drafts_reservation_id")
        batch_op.drop_column("automation_rule_id")
        batch_op.drop_column("reservation_id")

    # -- reservations -----------------------------------------
    with op.batch_alter_table("reservations") as batch_op:
        batch_op.drop_index("ix_reservations_last_host_reply_at")
        batch_op.drop_index("ix_reservations_last_guest_message_at")
        batch_op.drop_index("ix_reservations_intake_batch_id")
        batch_op.drop_column("last_host_reply_at")
        batch_op.drop_column("last_guest_message_at")
        batch_op.drop_column("intake_batch_id")

    # -- tenant_configs ----------------------------------------
    with op.batch_alter_table("tenant_configs") as batch_op:
        batch_op.drop_index("ix_tenant_configs_inbound_email_alias")
        batch_op.drop_column("last_inbound_email_at")
        batch_op.drop_column("inbound_email_alias")
        batch_op.drop_column("email_ingest_mode")

    # -- tables (reverse dependency order) ---------------------
    op.drop_index("ix_tenant_kpi_snapshots_period_end", table_name="tenant_kpi_snapshots")
    op.drop_index("ix_tenant_kpi_snapshots_period_start", table_name="tenant_kpi_snapshots")
    op.drop_index("ix_tenant_kpi_snapshots_property_name", table_name="tenant_kpi_snapshots")
    op.drop_index("ix_tenant_kpi_snapshots_tenant_id", table_name="tenant_kpi_snapshots")
    op.drop_table("tenant_kpi_snapshots")

    op.drop_index("ix_arrival_activations_status", table_name="arrival_activations")
    op.drop_index("ix_arrival_activations_activation_source", table_name="arrival_activations")
    op.drop_index("ix_arrival_activations_guest_phone", table_name="arrival_activations")
    op.drop_index("ix_arrival_activations_unit_identifier", table_name="arrival_activations")
    op.drop_index("ix_arrival_activations_created_by_member_id", table_name="arrival_activations")
    op.drop_index("ix_arrival_activations_timeline_event_id", table_name="arrival_activations")
    op.drop_index("ix_arrival_activations_reservation_id", table_name="arrival_activations")
    op.drop_index("ix_arrival_activations_tenant_id", table_name="arrival_activations")
    op.drop_table("arrival_activations")

    op.drop_index("ix_guest_timeline_events_created_at", table_name="guest_timeline_events")
    op.drop_index("ix_guest_timeline_events_event_type", table_name="guest_timeline_events")
    op.drop_index("ix_guest_timeline_events_direction", table_name="guest_timeline_events")
    op.drop_index("ix_guest_timeline_events_channel", table_name="guest_timeline_events")
    op.drop_index("ix_guest_timeline_events_unit_identifier", table_name="guest_timeline_events")
    op.drop_index("ix_guest_timeline_events_guest_phone", table_name="guest_timeline_events")
    op.drop_index("ix_guest_timeline_events_created_by_member_id", table_name="guest_timeline_events")
    op.drop_index("ix_guest_timeline_events_intake_batch_id", table_name="guest_timeline_events")
    op.drop_index("ix_guest_timeline_events_automation_rule_id", table_name="guest_timeline_events")
    op.drop_index("ix_guest_timeline_events_issue_ticket_id", table_name="guest_timeline_events")
    op.drop_index("ix_guest_timeline_events_draft_id", table_name="guest_timeline_events")
    op.drop_index("ix_guest_timeline_events_reservation_id", table_name="guest_timeline_events")
    op.drop_index("ix_guest_timeline_events_tenant_id", table_name="guest_timeline_events")
    op.drop_table("guest_timeline_events")

    op.drop_index("ix_issue_tickets_created_at", table_name="issue_tickets")
    op.drop_index("ix_issue_tickets_resolved_at", table_name="issue_tickets")
    op.drop_index("ix_issue_tickets_status", table_name="issue_tickets")
    op.drop_index("ix_issue_tickets_priority", table_name="issue_tickets")
    op.drop_index("ix_issue_tickets_category", table_name="issue_tickets")
    op.drop_index("ix_issue_tickets_guest_phone", table_name="issue_tickets")
    op.drop_index("ix_issue_tickets_unit_identifier", table_name="issue_tickets")
    op.drop_index("ix_issue_tickets_vendor_id", table_name="issue_tickets")
    op.drop_index("ix_issue_tickets_assigned_to_member_id", table_name="issue_tickets")
    op.drop_index("ix_issue_tickets_created_by_member_id", table_name="issue_tickets")
    op.drop_index("ix_issue_tickets_reservation_id", table_name="issue_tickets")
    op.drop_index("ix_issue_tickets_tenant_id", table_name="issue_tickets")
    op.drop_table("issue_tickets")

    op.drop_index("ix_reservation_intake_batches_created_by_member_id", table_name="reservation_intake_batches")
    op.drop_index("ix_reservation_intake_batches_pms_integration_id", table_name="reservation_intake_batches")
    op.drop_index("ix_reservation_intake_batches_status", table_name="reservation_intake_batches")
    op.drop_index("ix_reservation_intake_batches_external_reference", table_name="reservation_intake_batches")
    op.drop_index("ix_reservation_intake_batches_source_kind", table_name="reservation_intake_batches")
    op.drop_index("ix_reservation_intake_batches_tenant_id", table_name="reservation_intake_batches")
    op.drop_table("reservation_intake_batches")

    op.drop_index("ix_automation_rules_priority", table_name="automation_rules")
    op.drop_index("ix_automation_rules_is_active", table_name="automation_rules")
    op.drop_index("ix_automation_rules_channel", table_name="automation_rules")
    op.drop_index("ix_automation_rules_scope_kind", table_name="automation_rules")
    op.drop_index("ix_automation_rules_trigger_kind", table_name="automation_rules")
    op.drop_index("ix_automation_rules_tenant_id", table_name="automation_rules")
    op.drop_table("automation_rules")

    op.drop_index("ix_team_members_is_active", table_name="team_members")
    op.drop_index("ix_team_members_role", table_name="team_members")
    op.drop_index("ix_team_members_tenant_id", table_name="team_members")
    op.drop_table("team_members")
