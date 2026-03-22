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
_AUTO_MIGRATE = _env_flag("AUTO_MIGRATE", _ALLOW_SCHEMA_MUTATION_DEFAULT)

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
        # Connection pool tuned for a typical 2-4 worker Uvicorn deployment
        pool_size=10,           # keep 10 connections warm
        max_overflow=20,        # allow up to 30 total under peak load
        pool_timeout=30,        # give up after 30s waiting for a connection
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
        Tenant, TenantConfig, Draft, ProcessedEmail, CalendarState,
        Vendor, ActivityLog, BaileysOutbound, Reservation, ReservationSyncLog,
        ReservationIntakeBatch, AutomationRule, TeamMember, GuestTimelineEvent,
        ArrivalActivation, IssueTicket, TenantKpiSnapshot,
        PMSIntegration, PMSProcessedMessage,
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
        ("tenant_configs", "whatsapp_verify_token",   "VARCHAR(128)", "NULL"),
        ("tenant_configs", "sms_mode",                "VARCHAR(32)",  "'none'"),
        ("tenant_configs", "twilio_account_sid",      "VARCHAR(64)",  "NULL"),
        ("tenant_configs", "twilio_auth_token_enc",   "TEXT",         "NULL"),
        ("tenant_configs", "twilio_from_number",      "VARCHAR(32)",  "NULL"),
        ("tenant_configs", "sms_notify_number",       "VARCHAR(32)",  "NULL"),
        ("tenant_configs", "email_ingest_mode",       "VARCHAR(32)",  "'imap'"),
        ("tenant_configs", "inbound_email_alias",     "VARCHAR(64)",  "NULL"),
        ("tenant_configs", "last_inbound_email_at",   datetime_type,  "NULL"),
        ("tenant_configs", "subscription_plan",       "VARCHAR(32)",  "'free'"),
        ("tenant_configs", "subscription_status",     "VARCHAR(32)",  "'inactive'"),
        ("tenant_configs", "subscription_expires_at", datetime_type,  "NULL"),
        ("tenant_configs", "stripe_customer_id",      "VARCHAR(64)",  "NULL"),
        ("tenant_configs", "stripe_subscription_id",  "VARCHAR(64)",  "NULL"),
        ("tenant_configs", "bot_api_token_hash",      "VARCHAR(128)", "NULL"),
        ("tenant_configs", "bot_api_token_hint",      "VARCHAR(8)",   "NULL"),
        # Email verification + password reset (on tenants table)
        ("tenants", "email_verified",        "BOOLEAN",      false_default),
        ("tenants", "verification_token",    "VARCHAR(128)", "NULL"),
        ("tenants", "verification_sent_at",  datetime_type,  "NULL"),
        ("tenants", "reset_token",           "VARCHAR(128)", "NULL"),
        ("tenants", "reset_token_expires",   datetime_type,  "NULL"),
        # Reservation guest context mapping
        ("reservations", "guest_phone",      "VARCHAR(32)",  "NULL"),
        ("reservations", "unit_identifier",  "VARCHAR(64)",  "NULL"),
        ("reservations", "intake_batch_id",  "INTEGER",      "NULL"),
        ("reservations", "last_guest_message_at", datetime_type, "NULL"),
        ("reservations", "last_host_reply_at",    datetime_type, "NULL"),
        # Draft workflow links
        ("drafts", "reservation_id",         "INTEGER",      "NULL"),
        ("drafts", "automation_rule_id",     "INTEGER",      "NULL"),
    ]
    inspector = inspect(engine)
    with engine.connect() as conn:
        for table, col, col_type, default in new_columns:
            existing_columns = {c["name"] for c in inspector.get_columns(table)}
            if col in existing_columns:
                continue
            try:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {col_type} DEFAULT {default}"))
                conn.commit()
                log.info("Added missing column %s.%s for %s", table, col, dialect)
                inspector = inspect(engine)
            except Exception as exc:
                conn.rollback()
                log.warning("Failed to add missing column %s.%s on %s: %s", table, col, dialect, exc)
