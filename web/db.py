# © 2024 Jestin Rajan. All rights reserved.
"""
Database setup — SQLAlchemy sync engine.
Supports SQLite (dev) and PostgreSQL (prod) via DATABASE_URL env var.
"""

import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./airbnb_host.db")

# SQLite needs check_same_thread=False for multi-threaded use
connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(DATABASE_URL, connect_args=connect_args, echo=False)
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
