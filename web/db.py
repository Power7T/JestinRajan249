# © 2024 Jestin Rajan. All rights reserved.
"""
Database setup — SQLAlchemy sync engine.
Supports SQLite (dev) and PostgreSQL (prod) via DATABASE_URL env var.
"""

import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./airbnb_host.db")

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
    """Create all tables. Called once at startup."""
    from web.models import Tenant, TenantConfig, Draft, ProcessedEmail, CalendarState, Vendor, ActivityLog  # noqa: F401
    Base.metadata.create_all(bind=engine)
    db_migrate()


def db_migrate():
    """Add new columns to existing tables without dropping data (safe on live DBs)."""
    new_columns = [
        # (table, column, sql_type, default_sql)
        ("tenant_configs", "whatsapp_verify_token",   "VARCHAR(128)", "NULL"),
        ("tenant_configs", "sms_mode",                "VARCHAR(32)",  "'none'"),
        ("tenant_configs", "twilio_account_sid",      "VARCHAR(64)",  "NULL"),
        ("tenant_configs", "twilio_auth_token_enc",   "TEXT",         "NULL"),
        ("tenant_configs", "twilio_from_number",      "VARCHAR(32)",  "NULL"),
        ("tenant_configs", "sms_notify_number",       "VARCHAR(32)",  "NULL"),
        ("tenant_configs", "subscription_plan",       "VARCHAR(32)",  "'free'"),
        ("tenant_configs", "subscription_status",     "VARCHAR(32)",  "'inactive'"),
        ("tenant_configs", "subscription_expires_at", "DATETIME",     "NULL"),
        ("tenant_configs", "stripe_customer_id",      "VARCHAR(64)",  "NULL"),
        ("tenant_configs", "stripe_subscription_id",  "VARCHAR(64)",  "NULL"),
        ("tenant_configs", "bot_api_token_hash",      "VARCHAR(128)", "NULL"),
        ("tenant_configs", "bot_api_token_hint",      "VARCHAR(8)",   "NULL"),
        # Email verification + password reset (on tenants table)
        ("tenants", "email_verified",        "BOOLEAN",      "0"),
        ("tenants", "verification_token",    "VARCHAR(128)", "NULL"),
        ("tenants", "verification_sent_at",  "DATETIME",     "NULL"),
        ("tenants", "reset_token",           "VARCHAR(128)", "NULL"),
        ("tenants", "reset_token_expires",   "DATETIME",     "NULL"),
    ]
    with engine.connect() as conn:
        for table, col, col_type, default in new_columns:
            try:
                conn.execute(
                    __import__("sqlalchemy").text(
                        f"ALTER TABLE {table} ADD COLUMN {col} {col_type} DEFAULT {default}"
                    )
                )
                conn.commit()
            except Exception:
                # Column already exists — safe to ignore
                conn.rollback()
