# © 2024 Jestin Rajan. All rights reserved.
"""
Database setup — SQLAlchemy sync engine.
Supports SQLite (dev) and PostgreSQL (prod) via DATABASE_URL env var.
"""

import logging
import os
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker, DeclarativeBase

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./airbnb_host.db")
log = logging.getLogger(__name__)

_ENVIRONMENT = os.getenv("ENVIRONMENT", "production").lower()
_ALLOW_SCHEMA_MUTATION_DEFAULT = _ENVIRONMENT in {"development", "dev", "test"}


def _env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


_AUTO_CREATE_TABLES = _env_flag("AUTO_CREATE_TABLES", _ALLOW_SCHEMA_MUTATION_DEFAULT)
# Keep additive schema repair enabled by default so missed deploy migrations do
# not leave the app hard-crashing on newer ORM columns.
_AUTO_MIGRATE = _env_flag("AUTO_MIGRATE", True)
if _ENVIRONMENT not in {"development", "dev", "test"} and not _AUTO_MIGRATE:
    log.warning("AUTO_MIGRATE=false ignored in production; additive schema repair stays enabled")
    _AUTO_MIGRATE = True

_is_sqlite = DATABASE_URL.startswith("sqlite")

# ---------------------------------------------------------------------------
# Engine — tuned for production load
# ---------------------------------------------------------------------------
if _is_sqlite:
    engine = create_engine(
        DATABASE_URL,
        connect_args={"check_same_thread": False},
        echo=False,
    )
else:
    engine = create_engine(
        DATABASE_URL,
        echo=False,
        # Connection pool — sized for 4 Uvicorn workers + background threads
        # Each worker can hold ~5 connections; 4 workers = 20 base; threads add ~10 more
        pool_size=20,           # keep 20 connections warm (was 10 — too low)
        max_overflow=30,        # allow up to 50 total under peak load (was 20)
        pool_timeout=10,        # fail fast: 10s wait then 503, not 30s hang (was 30)
        pool_recycle=1800,      # recycle connections every 30 min (avoids stale TCP)
        pool_pre_ping=True,     # verify connection alive before handing it out
    )

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """Create all tables (dev/test) and run lightweight migrations when enabled."""
    from web.models import (  # noqa: F401
        SystemConfig, APIUsageLog,
        Tenant, TenantConfig, Draft, FailedDraftLog, ProcessedEmail, CalendarState,
        Vendor, ActivityLog, Reservation, ReservationSyncLog,
        ReservationIntakeBatch, AutomationRule, TeamMember, GuestTimelineEvent,
        ArrivalActivation, IssueTicket, TenantKpiSnapshot,
        PMSIntegration, PMSProcessedMessage, PlanConfig, VoicePricingConfig,
        GuestContact, VoiceCall, VoiceKnowledgeGap,
        IdempotencyKey, TenantRateLimit, RateLimitCounter,
        FeatureFlag, FeatureFlagOverride,
        VoiceRoutingConfig, RoutingRule,
        AutomatedMessage, GuestFeedback,
        Property, PropertyConfig, EscalatedMessage, MessageLog,
        TeamMemberWorkload, EscalationRule,
    )
    if _AUTO_CREATE_TABLES:
        Base.metadata.create_all(bind=engine)
    else:
        log.info("AUTO_CREATE_TABLES disabled; skipping Base.metadata.create_all()")
    if _AUTO_MIGRATE:
        db_migrate()
    else:
        log.info("AUTO_MIGRATE disabled; skipping db_migrate()")


def db_migrate():
    """Add new columns to existing tables without dropping data (safe on live DBs)."""
    dialect = engine.dialect.name
    datetime_type = "DATETIME" if _is_sqlite else "TIMESTAMP WITH TIME ZONE"
    false_default = "0" if _is_sqlite else "FALSE"
    new_columns = [
        # (table, column, sql_type, default_sql)
        ("system_config", "openrouter_api_key_enc",    "VARCHAR(255)", "NULL"),
        ("system_config", "deepgram_api_key_enc",      "VARCHAR(255)", "NULL"),
        ("system_config", "elevenlabs_api_key_enc",       "VARCHAR(255)", "NULL"),
        ("system_config", "cloudflare_account_id",        "VARCHAR(255)", "NULL"),
        ("system_config", "cloudflare_r2_access_key_enc", "VARCHAR(255)", "NULL"),
        ("system_config", "cloudflare_r2_secret_key_enc", "VARCHAR(255)", "NULL"),
        ("system_config", "cloudflare_r2_bucket",         "VARCHAR(255)", "NULL"),
        ("system_config", "primary_model",             "VARCHAR(100)", "'anthropic/claude-3.7-sonnet'"),
        ("system_config", "primary_backup_model",      "VARCHAR(100)", "'anthropic/claude-3.5-sonnet'"),
        ("system_config", "fallback_model",            "VARCHAR(100)", "'meta-llama/llama-3.3-70b-instruct'"),
        ("system_config", "routine_model",             "VARCHAR(100)", "'google/gemini-2.5-flash'"),
        ("system_config", "routine_backup_model",      "VARCHAR(100)", "'anthropic/claude-3.5-haiku'"),
        ("system_config", "sentiment_model",           "VARCHAR(100)", "'openai/gpt-4o-mini'"),
        ("system_config", "voice_llm_model",           "VARCHAR(100)", "'openai/gpt-4o-mini'"),
        ("system_config", "voice_llm_backup_model",    "VARCHAR(100)", "'anthropic/claude-3.5-haiku'"),
        ("system_config", "voice_llm_emergency_model", "VARCHAR(100)", "'meta-llama/llama-3.3-70b-instruct'"),
        ("system_config", "voice_deepgram_model",      "VARCHAR(50)",  "'nova-2'"),
        ("system_config", "voice_llm_max_tokens",      "INTEGER",      "300"),
        ("system_config", "voice_llm_temperature",     "FLOAT",        "0.7"),
        ("system_config", "voice_elevenlabs_model",    "VARCHAR(50)",  "'eleven_turbo_v2'"),
        ("system_config", "voice_elevenlabs_stability","FLOAT",        "0.5"),
        ("system_config", "voice_elevenlabs_similarity","FLOAT",       "0.75"),
        ("system_config", "voice_elevenlabs_voice_id",      "VARCHAR(64)",  "'EXAVITQu4vr4xnSDxMaL'"),
        ("system_config", "google_maps_api_key_enc",        "VARCHAR(255)", "NULL"),
        ("system_config", "google_tts_api_key_enc",         "VARCHAR(255)", "NULL"),
        ("system_config", "voice_tts_provider",             "VARCHAR(20)",  "'google'"),
        ("system_config", "voice_google_tts_voice",         "VARCHAR(64)",  "'en-US-Neural2-F'"),
        ("system_config", "voice_google_tts_language",      "VARCHAR(16)",  "'en-US'"),
        ("system_config", "voice_google_tts_speaking_rate", "FLOAT",        "1.0"),
        ("system_config", "updated_at",                datetime_type,  "NULL"),
        ("tenant_configs", "timezone",                "VARCHAR(64)",  "'UTC'"),
        ("tenant_configs", "data_retention_days",     "INTEGER",      "30"),
        ("tenant_configs", "whatsapp_verify_token",   "VARCHAR(128)", "NULL"),
        ("tenant_configs", "sms_mode",                "VARCHAR(32)",  "'none'"),
        ("tenant_configs", "twilio_account_sid",      "VARCHAR(64)",  "NULL"),
        ("tenant_configs", "twilio_auth_token_enc",   "TEXT",         "NULL"),
        ("tenant_configs", "twilio_from_number",      "VARCHAR(32)",  "NULL"),
        ("tenant_configs", "sms_notify_number",       "VARCHAR(32)",  "NULL"),
        ("tenant_configs", "voice_send_channel",      "VARCHAR(16)",  "'disabled'"),
        ("tenant_configs", "voice_post_call_summary", "BOOLEAN",      false_default),
        ("tenant_configs", "voice_scheduled_calls_enabled", "BOOLEAN", false_default),
        ("tenant_configs", "voice_twilio_account_sid", "VARCHAR(64)", "NULL"),
        ("tenant_configs", "voice_twilio_auth_token_enc", "TEXT",     "NULL"),
        ("tenant_configs", "voice_twilio_from_number", "VARCHAR(32)", "NULL"),
        ("tenant_configs", "voice_elevenlabs_voice_id", "VARCHAR(64)", "'EXAVITQu4vr4xnSDxMaL'"),
        ("tenant_configs", "voice_llm_model",          "VARCHAR(100)", "'openai/gpt-4o-mini'"),
        ("tenant_configs", "voice_llm_backup_model",   "VARCHAR(100)", "'anthropic/claude-3.5-haiku'"),
        ("tenant_configs", "voice_llm_emergency_model","VARCHAR(100)", "'meta-llama/llama-3.3-70b-instruct'"),
        ("tenant_configs", "voice_deepgram_model",     "VARCHAR(50)",  "'nova-2'"),
        ("tenant_configs", "voice_llm_max_tokens",     "INTEGER",      "300"),
        ("tenant_configs", "voice_llm_temperature",    "FLOAT",        "0.7"),
        ("tenant_configs", "voice_elevenlabs_stability","FLOAT",       "0.5"),
        ("tenant_configs", "voice_elevenlabs_similarity","FLOAT",      "0.75"),
        ("tenant_configs", "voice_elevenlabs_model",   "VARCHAR(50)",  "'eleven_turbo_v2'"),
        ("tenant_configs", "voice_google_tts_voice",  "VARCHAR(64)",  "NULL"),
        ("tenant_configs", "notify_host_on_guest_msg","BOOLEAN",      false_default),
        ("tenant_configs", "host_notify_phone",       "VARCHAR(32)",  "NULL"),
        ("tenant_configs", "ai_calls_today",          "INTEGER",      "0"),
        ("tenant_configs", "ai_calls_today_date",     datetime_type,  "NULL"),
        ("tenant_configs", "ai_calls_monthly",        "INTEGER",      "0"),
        ("tenant_configs", "ai_calls_monthly_date",   datetime_type,  "NULL"),
        ("tenant_configs", "pet_policy",              "TEXT",         "NULL"),
        ("tenant_configs", "refund_policy",           "TEXT",         "NULL"),
        ("tenant_configs", "early_checkin_policy",    "TEXT",         "NULL"),
        ("tenant_configs", "early_checkin_fee",       "VARCHAR(64)",  "NULL"),
        ("tenant_configs", "late_checkout_policy",    "TEXT",         "NULL"),
        ("tenant_configs", "late_checkout_fee",       "VARCHAR(64)",  "NULL"),
        ("tenant_configs", "parking_policy",          "TEXT",         "NULL"),
        ("tenant_configs", "smoking_policy",          "TEXT",         "NULL"),
        ("tenant_configs", "quiet_hours",             "VARCHAR(128)", "NULL"),
        ("tenant_configs", "subscription_plan",       "VARCHAR(32)",  "'starter'"),
        ("tenant_configs", "subscription_status",     "VARCHAR(32)",  "'requires_upgrade'"),
        ("tenant_configs", "subscription_expires_at", datetime_type,  "NULL"),
        ("tenant_configs", "stripe_customer_id",      "VARCHAR(64)",  "NULL"),
        ("tenant_configs", "stripe_subscription_id",  "VARCHAR(64)",  "NULL"),
        ("tenant_configs", "extra_services",          "TEXT",         "NULL"),
        ("tenant_configs", "digest_enabled",          "BOOLEAN",      false_default),
        ("tenant_configs", "bot_last_heartbeat",      datetime_type,  "NULL"),
        ("tenant_configs", "guest_welcome_template",  "TEXT",         "NULL"),
        ("tenant_configs", "internal_token",          "VARCHAR(64)",  "NULL"),
        # Tenant profile + voice calling
        ("tenants", "first_name",           "VARCHAR(100)", "NULL"),
        ("tenants", "last_name",            "VARCHAR(100)", "NULL"),
        ("tenants", "phone",                "VARCHAR(20)",  "NULL"),
        ("tenants", "country",              "VARCHAR(2)",   "NULL"),
        ("tenants", "voice_enabled",        "BOOLEAN",      false_default),
        ("tenants", "voice_phone_number",   "VARCHAR(32)",  "NULL"),
        # Email verification + password reset (on tenants table)
        ("tenants", "email_verified",        "BOOLEAN",      false_default),
        ("tenants", "verification_token",    "VARCHAR(128)", "NULL"),
        ("tenants", "verification_sent_at",  datetime_type,  "NULL"),
        ("tenants", "reset_token",           "VARCHAR(128)", "NULL"),
        ("tenants", "reset_token_expires",   datetime_type,  "NULL"),
        ("team_members", "password_hash",    "VARCHAR(128)", "NULL"),
        ("team_members", "invite_token",     "VARCHAR(64)",  "NULL"),
        ("team_members", "invite_token_expires_at", datetime_type, "NULL"),
        # Reservation guest context mapping
        ("reservations", "guest_phone",      "VARCHAR(32)",  "NULL"),
        ("reservations", "unit_identifier",  "VARCHAR(64)",  "NULL"),
        ("reservations", "intake_batch_id",  "INTEGER",      "NULL"),
        ("reservations", "last_guest_message_at", datetime_type, "NULL"),
        ("reservations", "last_host_reply_at",    datetime_type, "NULL"),
        ("reservations", "review_rating",         "FLOAT",        "NULL"),
        ("reservations", "review_text",           "TEXT",         "NULL"),
        ("reservations", "review_submitted_at",   datetime_type,  "NULL"),
        ("reservations", "review_sentiment",      "VARCHAR(16)",  "NULL"),
        ("reservations", "review_sentiment_score","FLOAT",        "NULL"),
        ("reservations", "guest_feedback_positive","INTEGER",     "0"),
        ("reservations", "guest_feedback_negative","INTEGER",     "0"),
        ("reservations", "guest_satisfaction_score","FLOAT",      "NULL"),
        ("reservations", "repeat_guest_count",    "INTEGER",      "0"),
        ("reservations", "message_count",         "INTEGER",      "0"),
        ("reservations", "latest_guest_sentiment","VARCHAR(16)",  "NULL"),
        ("reservations", "latest_guest_sentiment_score","FLOAT",  "NULL"),
        ("reservations", "checkin_token_expires_at", datetime_type, "NULL"),
        # Voice calling (older DBs may have partial schema)
        ("voice_calls", "guest_contact_id",       "VARCHAR(36)",  "NULL"),
        ("voice_calls", "reservation_id",         "INTEGER",      "NULL"),
        ("voice_calls", "twilio_call_id",         "VARCHAR(64)",  "NULL"),
        ("voice_calls", "twilio_phone_number",    "VARCHAR(32)",  "NULL"),
        ("voice_calls", "guest_phone_number",     "VARCHAR(32)",  "NULL"),
        ("voice_calls", "call_type",              "VARCHAR(16)",  "'incoming'"),
        ("voice_calls", "status",                 "VARCHAR(32)",  "'ringing'"),
        ("voice_calls", "guest_messages",         "JSON",         "'[]'"),
        ("voice_calls", "ai_responses",           "JSON",         "'[]'"),
        ("voice_calls", "created_at",             datetime_type,  "NULL"),
        ("voice_calls", "full_transcript",        "TEXT",         "NULL"),
        ("voice_calls", "confidence_avg",         "FLOAT",        "NULL"),
        ("voice_calls", "sentiment",              "VARCHAR(16)",  "NULL"),
        ("voice_calls", "duration_seconds",       "INTEGER",      "NULL"),
        ("voice_calls", "recording_url",          "VARCHAR(512)", "NULL"),
        ("voice_calls", "guest_email",            "VARCHAR(255)", "NULL"),
        ("voice_calls", "guest_language",         "VARCHAR(16)",  "'en'"),
        ("voice_calls", "guest_rating",           "INTEGER",      "NULL"),
        ("voice_calls", "callback_requested",     "BOOLEAN",      false_default),
        ("voice_calls", "callback_at",            datetime_type,  "NULL"),
        ("voice_calls", "recording_consent_given","BOOLEAN",      "NULL"),
        ("voice_calls", "recording_consent_at",   datetime_type,  "NULL"),
        ("voice_calls", "started_at",             datetime_type,  "NULL"),
        ("voice_calls", "ended_at",               datetime_type,  "NULL"),
        ("voice_knowledge_gaps", "call_id",       "VARCHAR(36)",  "NULL"),
        ("voice_knowledge_gaps", "issue_ticket_id","INTEGER",     "NULL"),
        ("voice_knowledge_gaps", "guest_phone",   "VARCHAR(32)",  "NULL"),
        ("voice_knowledge_gaps", "guest_name",    "VARCHAR(128)", "NULL"),
        ("voice_knowledge_gaps", "guest_room",    "VARCHAR(64)",  "NULL"),
        ("voice_knowledge_gaps", "host_answer",   "TEXT",         "NULL"),
        ("voice_knowledge_gaps", "saved_to",      "VARCHAR(32)",  "NULL"),
        ("voice_knowledge_gaps", "created_at",    datetime_type,  "NULL"),
        ("voice_knowledge_gaps", "resolved",      "BOOLEAN",      false_default),
        ("voice_knowledge_gaps", "reply_sent",    "BOOLEAN",      false_default),
        ("voice_knowledge_gaps", "reply_sent_at", datetime_type,  "NULL"),
        ("voice_knowledge_gaps", "reply_channel", "VARCHAR(16)",  "NULL"),
        ("voice_knowledge_gaps", "resolved_at",   datetime_type,  "NULL"),
        ("voice_knowledge_gaps", "alerted_at",    datetime_type,  "NULL"),
        ("team_members", "property_scope",        "TEXT",         "NULL"),
        # Draft workflow links
        ("drafts", "reservation_id",         "INTEGER",      "NULL"),
        ("drafts", "automation_rule_id",     "INTEGER",      "NULL"),
        ("drafts", "parent_draft_id",        "VARCHAR(64)",  "NULL"),
        ("drafts", "thread_key",             "VARCHAR(128)", "NULL"),
        ("drafts", "guest_message_index",    "INTEGER",      "1"),
        ("drafts", "property_name_snapshot", "VARCHAR(256)", "NULL"),
        ("drafts", "unit_identifier_snapshot","VARCHAR(64)", "NULL"),
        ("drafts", "confidence",            "FLOAT",        "NULL"),
        ("drafts", "auto_send_eligible",     "BOOLEAN",      false_default),
        ("drafts", "guest_history_score",    "FLOAT",        "NULL"),
        ("drafts", "guest_sentiment",        "VARCHAR(16)",  "NULL"),
        ("drafts", "sentiment_score",        "FLOAT",        "NULL"),
        ("drafts", "stay_stage",             "VARCHAR(32)",  "NULL"),
        ("drafts", "policy_conflicts_json",  "TEXT",         "NULL"),
        ("drafts", "host_feedback_score",    "FLOAT",        "NULL"),
        ("drafts", "host_feedback_note",     "TEXT",         "NULL"),
        ("drafts", "host_feedback_at",       datetime_type,  "NULL"),
        ("drafts", "context_sources",        "TEXT",         "NULL"),
        ("drafts", "archived_at",            datetime_type,  "NULL"),
        # digest_enabled on tenant_configs (migration 20260403_0200)
        ("tenant_configs", "digest_enabled",  "BOOLEAN",      false_default),
        # Voice forwarding on tenants (migration 20260401_0600)
        ("tenants", "voice_forward_enabled",  "BOOLEAN",      false_default),
        ("tenants", "voice_forward_number",   "VARCHAR(32)",  "NULL"),
        # Team member delegation (migration 20260401_0900)
        ("team_members", "expertise_areas",           "TEXT",    "NULL"),
        ("team_members", "max_concurrent_tasks",      "INTEGER", "10"),
        ("team_members", "is_available_for_assignment","BOOLEAN", false_default),
        # Building/multi-unit property support
        ("tenant_configs", "building_total_units",    "INTEGER",      "NULL"),
        ("tenant_configs", "building_name",           "VARCHAR(255)", "NULL"),
    ]
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    with engine.connect() as conn:
        for table, col, col_type, default in new_columns:
            if table not in existing_tables:
                continue
            existing_columns = {c["name"] for c in inspector.get_columns(table)}
            if col in existing_columns:
                continue
            try:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {col_type} DEFAULT {default}"))
                conn.commit()
                log.info("Added missing column %s.%s for %s", table, col, dialect)
                inspector = inspect(engine)
                existing_tables = set(inspector.get_table_names())
            except Exception as exc:
                conn.rollback()
                log.warning("Failed to add missing column %s.%s on %s: %s", table, col, dialect, exc)

    # Ensure new tables exist (safe fallback if Alembic migrations haven't run)
    # These tables were added post-initial-schema and may not exist on older production DBs.
    tables_to_ensure = [
        "automated_messages",
        "guest_feedback",
        "voice_routing_configs",
        "routing_rules",
        "escalation_rules",
        "team_member_workloads",
        "properties",
        "property_configs",
        "escalated_messages",
        "message_logs",
    ]
    inspector2 = inspect(engine)
    existing_tables2 = set(inspector2.get_table_names())
    missing_tables = [t for t in tables_to_ensure if t not in existing_tables2]
    if missing_tables:
        try:
            from web.models import Base as _Base  # noqa: F811
            _Base.metadata.create_all(bind=engine)
            log.info("Created missing tables: %s", missing_tables)
        except Exception as exc:
            log.warning("Failed to create missing tables %s: %s", missing_tables, exc)

    # Seed default PlanConfig rows
    db = SessionLocal()
    try:
        from web.models import PlanConfig
        plans = [
            {"plan_key": "starter", "display_name": "Starter", "base_fee_usd": 20.0, "per_unit_fee_usd": 10.0, "min_units": 1, "max_units": 5},
            {"plan_key": "growth", "display_name": "Growth", "base_fee_usd": 20.0, "per_unit_fee_usd": 9.0, "min_units": 6, "max_units": 10},
            {"plan_key": "pro", "display_name": "Pro", "base_fee_usd": 20.0, "per_unit_fee_usd": 8.0, "min_units": 11, "max_units": 50},
        ]
        for plan_data in plans:
            existing = db.query(PlanConfig).filter_by(plan_key=plan_data["plan_key"]).first()
            if not existing:
                db.add(PlanConfig(**plan_data))
                log.info("Seeded plan: %s", plan_data["plan_key"])
        db.commit()
    except Exception as exc:
        db.rollback()
        log.warning("Failed to seed PlanConfig: %s", exc)
    finally:
        db.close()
