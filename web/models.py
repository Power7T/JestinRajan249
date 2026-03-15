# © 2024 Jestin Rajan. All rights reserved.
"""
SQLAlchemy models for multi-tenant Airbnb Host Assistant.
"""

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (
    String, Text, Integer, Boolean, DateTime, ForeignKey, JSON
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from web.db import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _uuid() -> str:
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# Tenant — one row per registered host
# ---------------------------------------------------------------------------

class Tenant(Base):
    __tablename__ = "tenants"

    id:           Mapped[str]      = mapped_column(String(36), primary_key=True, default=_uuid)
    email:        Mapped[str]      = mapped_column(String(255), unique=True, index=True, nullable=False)
    password_hash: Mapped[str]     = mapped_column(String(128), nullable=False)
    is_active:    Mapped[bool]     = mapped_column(Boolean, default=True)
    created_at:   Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    config:   Mapped[Optional["TenantConfig"]] = relationship("TenantConfig", back_populates="tenant", uselist=False)
    drafts:   Mapped[list["Draft"]]            = relationship("Draft", back_populates="tenant")
    vendors:  Mapped[list["Vendor"]]           = relationship("Vendor", back_populates="tenant")
    logs:     Mapped[list["ActivityLog"]]      = relationship("ActivityLog", back_populates="tenant")


# ---------------------------------------------------------------------------
# TenantConfig — settings per tenant (email creds, iCal, properties)
# ---------------------------------------------------------------------------

class TenantConfig(Base):
    __tablename__ = "tenant_configs"

    id:         Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id:  Mapped[str] = mapped_column(String(36), ForeignKey("tenants.id"), unique=True, nullable=False)

    # Property info
    property_names: Mapped[Optional[str]] = mapped_column(Text, nullable=True)   # comma-separated
    ical_urls:      Mapped[Optional[str]] = mapped_column(Text, nullable=True)    # comma-separated

    # Email / IMAP / SMTP (password stored AES-encrypted)
    imap_host:         Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    imap_port:         Mapped[int]           = mapped_column(Integer, default=993)
    smtp_host:         Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    smtp_port:         Mapped[int]           = mapped_column(Integer, default=587)
    email_address:     Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    email_password_enc: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # encrypted

    # Claude API key (AES-encrypted)
    anthropic_api_key_enc: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # WhatsApp (optional)
    wa_mode:             Mapped[str]           = mapped_column(String(32), default="none")
    whatsapp_number:     Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    whatsapp_token_enc:  Mapped[Optional[str]] = mapped_column(Text, nullable=True)   # encrypted
    whatsapp_phone_id:   Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    # Internal token (auto-generated) for service-to-service auth
    internal_token: Mapped[str] = mapped_column(String(64), default=lambda: str(uuid.uuid4()))

    tenant: Mapped["Tenant"] = relationship("Tenant", back_populates="config")


# ---------------------------------------------------------------------------
# Draft — AI-generated draft awaiting host approval
# ---------------------------------------------------------------------------

class Draft(Base):
    __tablename__ = "drafts"

    id:          Mapped[str]           = mapped_column(String(64), primary_key=True)
    tenant_id:   Mapped[str]           = mapped_column(String(36), ForeignKey("tenants.id"), index=True)
    source:      Mapped[str]           = mapped_column(String(32))           # email / calendar / whatsapp
    guest_name:  Mapped[str]           = mapped_column(String(128))
    message:     Mapped[str]           = mapped_column(Text)
    reply_to:    Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    msg_type:    Mapped[str]           = mapped_column(String(16))            # routine / complex
    vendor_type: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    draft:       Mapped[str]           = mapped_column(Text)
    final_text:  Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status:      Mapped[str]           = mapped_column(String(16), default="pending", index=True)
    created_at:  Mapped[datetime]      = mapped_column(DateTime(timezone=True), default=_now)
    approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    tenant: Mapped["Tenant"] = relationship("Tenant", back_populates="drafts")


# ---------------------------------------------------------------------------
# ProcessedEmail — tracks IMAP UIDs already handled per tenant
# ---------------------------------------------------------------------------

class ProcessedEmail(Base):
    __tablename__ = "processed_emails"

    id:           Mapped[int]      = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id:    Mapped[str]      = mapped_column(String(36), ForeignKey("tenants.id"), index=True)
    email_uid:    Mapped[str]      = mapped_column(String(64))
    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


# ---------------------------------------------------------------------------
# CalendarState — tracks which calendar trigger keys have fired per tenant
# ---------------------------------------------------------------------------

class CalendarState(Base):
    __tablename__ = "calendar_states"

    id:         Mapped[int]      = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id:  Mapped[str]      = mapped_column(String(36), ForeignKey("tenants.id"), index=True)
    state_key:  Mapped[str]      = mapped_column(String(128))    # e.g. "checkin:uid123"
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


# ---------------------------------------------------------------------------
# Vendor — contacts per tenant per category
# ---------------------------------------------------------------------------

class Vendor(Base):
    __tablename__ = "vendors"

    id:        Mapped[int]           = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str]           = mapped_column(String(36), ForeignKey("tenants.id"), index=True)
    category:  Mapped[str]           = mapped_column(String(32))   # cleaners / ac_technicians / etc.
    name:      Mapped[str]           = mapped_column(String(128))
    phone:     Mapped[str]           = mapped_column(String(32))
    notes:     Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    tenant: Mapped["Tenant"] = relationship("Tenant", back_populates="vendors")


# ---------------------------------------------------------------------------
# ActivityLog — audit trail of what the system did
# ---------------------------------------------------------------------------

class ActivityLog(Base):
    __tablename__ = "activity_logs"

    id:         Mapped[int]      = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id:  Mapped[str]      = mapped_column(String(36), ForeignKey("tenants.id"), index=True)
    event_type: Mapped[str]      = mapped_column(String(64))    # email_received / draft_approved / etc.
    message:    Mapped[str]      = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    tenant: Mapped["Tenant"] = relationship("Tenant", back_populates="logs")
