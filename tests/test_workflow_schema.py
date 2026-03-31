"""Model-layer tests for the HostAI workflow schema."""

from sqlalchemy import create_engine, inspect, text

import web.db as db_mod
from web.db import Base


def test_workflow_tables_are_created(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'workflow-schema.db'}",
        connect_args={"check_same_thread": False},
    )

    Base.metadata.create_all(bind=engine)

    tables = set(inspect(engine).get_table_names())
    expected = {
        "automation_rules",
        "failed_draft_logs",
        "guest_contacts",
        "guest_timeline_events",
        "arrival_activations",
        "issue_tickets",
        "plan_configs",
        "team_members",
        "tenant_kpi_snapshots",
        "reservation_intake_batches",
        "voice_calls",
        "voice_knowledge_gaps",
    }
    assert expected.issubset(tables)


def test_db_migrate_backfills_workflow_columns(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'legacy-workflow.db'}",
        connect_args={"check_same_thread": False},
    )

    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE tenants (
                    id VARCHAR(36) PRIMARY KEY,
                    email VARCHAR(255) NOT NULL,
                    password_hash VARCHAR(128) NOT NULL
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE system_config (
                    id VARCHAR(36) PRIMARY KEY
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE tenant_configs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tenant_id VARCHAR(36) NOT NULL
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE drafts (
                    id VARCHAR(64) PRIMARY KEY,
                    tenant_id VARCHAR(36) NOT NULL
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE reservations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tenant_id VARCHAR(36) NOT NULL,
                    confirmation_code VARCHAR(64) NOT NULL
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE team_members (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tenant_id VARCHAR(36) NOT NULL,
                    display_name VARCHAR(128) NOT NULL
                )
                """
            )
        )

    monkeypatch.setattr(db_mod, "engine", engine)
    monkeypatch.setattr(db_mod, "_is_sqlite", True)

    db_mod.db_migrate()

    system_config_cols = {col["name"] for col in inspect(engine).get_columns("system_config")}
    tenant_cols = {col["name"] for col in inspect(engine).get_columns("tenants")}
    tenant_config_cols = {col["name"] for col in inspect(engine).get_columns("tenant_configs")}
    draft_cols = {col["name"] for col in inspect(engine).get_columns("drafts")}
    reservation_cols = {col["name"] for col in inspect(engine).get_columns("reservations")}
    team_member_cols = {col["name"] for col in inspect(engine).get_columns("team_members")}

    assert {"routine_model"} <= system_config_cols
    assert {"first_name", "last_name", "phone", "country", "voice_enabled", "voice_phone_number"} <= tenant_cols
    assert {
        "timezone",
        "data_retention_days",
        "email_ingest_mode",
        "inbound_email_alias",
        "last_inbound_email_at",
        "notify_host_on_guest_msg",
        "host_notify_phone",
        "guest_welcome_template",
        "voice_send_channel",
        "voice_post_call_summary",
        "voice_scheduled_calls_enabled",
    } <= tenant_config_cols
    assert {"reservation_id", "automation_rule_id"} <= draft_cols
    assert {"intake_batch_id", "last_guest_message_at", "last_host_reply_at", "checkin_token_expires_at"} <= reservation_cols
    assert {"password_hash", "invite_token", "invite_token_expires_at"} <= team_member_cols
