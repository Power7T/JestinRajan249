# © 2024 Jestin Rajan. All rights reserved.
"""
SQLAlchemy models for multi-tenant Airbnb Host Assistant.
"""

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (
    String, Text, Integer, Boolean, DateTime, ForeignKey, JSON, Float, Date, UniqueConstraint
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from web.db import Base

# Subscription plans
PLAN_FREE       = "free"
PLAN_BAILEYS    = "baileys"
PLAN_META_CLOUD = "meta_cloud"
PLAN_SMS        = "sms"
PLAN_PRO        = "pro"   # all three channels


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

    # Email verification
    email_verified:      Mapped[bool]           = mapped_column(Boolean, default=False)
    verification_token:  Mapped[Optional[str]]  = mapped_column(String(128), nullable=True, index=True)
    verification_sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    # Password reset
    reset_token:          Mapped[Optional[str]]      = mapped_column(String(128), nullable=True, index=True)
    reset_token_expires:  Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

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

    # Onboarding — property details (filled during wizard)
    property_type:       Mapped[Optional[str]] = mapped_column(String(64), nullable=True)   # apartment/villa/bnb/hotel
    property_city:       Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    check_in_time:       Mapped[Optional[str]] = mapped_column(String(32), nullable=True)   # e.g. "15:00"
    check_out_time:      Mapped[Optional[str]] = mapped_column(String(32), nullable=True)   # e.g. "11:00"
    max_guests:          Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    house_rules:         Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    amenities:           Mapped[Optional[str]] = mapped_column(Text, nullable=True)         # comma-separated
    food_menu:           Mapped[Optional[str]] = mapped_column(Text, nullable=True)         # plain text (extracted from PDF or pasted)
    nearby_restaurants:  Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    faq:                 Mapped[Optional[str]] = mapped_column(Text, nullable=True)         # free-form Q&A text
    custom_instructions: Mapped[Optional[str]] = mapped_column(Text, nullable=True)         # host's special instructions to the AI
    escalation_email:    Mapped[Optional[str]] = mapped_column(String(255), nullable=True)  # where to send human-handoff alerts

    # Onboarding progress
    onboarding_complete: Mapped[bool] = mapped_column(Boolean, default=False)
    onboarding_step:     Mapped[int]  = mapped_column(Integer, default=0)

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
    wa_mode:               Mapped[str]           = mapped_column(String(32), default="none")
    whatsapp_number:       Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    whatsapp_token_enc:    Mapped[Optional[str]] = mapped_column(Text, nullable=True)   # encrypted
    whatsapp_phone_id:     Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    whatsapp_verify_token: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)

    # SMS / Twilio
    sms_mode:              Mapped[str]           = mapped_column(String(32), default="none")
    twilio_account_sid:    Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    twilio_auth_token_enc: Mapped[Optional[str]] = mapped_column(Text, nullable=True)   # encrypted
    twilio_from_number:    Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    sms_notify_number:     Mapped[Optional[str]] = mapped_column(String(32), nullable=True)

    # Subscription (Stripe)
    subscription_plan:       Mapped[str]           = mapped_column(String(32), default="free")
    subscription_status:     Mapped[str]           = mapped_column(String(32), default="inactive")
    subscription_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    stripe_customer_id:      Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    stripe_subscription_id:  Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    # Baileys bot API token (hashed) — bot authenticates with this instead of user password
    bot_api_token_hash: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    bot_api_token_hint: Mapped[Optional[str]] = mapped_column(String(8), nullable=True)  # last 4 chars for display

    # Internal token (auto-generated) for service-to-service auth
    internal_token: Mapped[str] = mapped_column(String(64), default=lambda: str(uuid.uuid4()))

    tenant: Mapped["Tenant"] = relationship("Tenant", back_populates="config")


# ---------------------------------------------------------------------------
# BaileysOutbound — persistent fallback queue for Baileys outbound messages.
# Primary queue is Redis; rows here are written when Redis is unavailable
# and are also used as audit trail so no message is silently lost.
# ---------------------------------------------------------------------------

class BaileysOutbound(Base):
    __tablename__ = "baileys_outbound"

    id:          Mapped[int]           = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id:   Mapped[str]           = mapped_column(String(36), ForeignKey("tenants.id"), index=True)
    to_phone:    Mapped[str]           = mapped_column(String(32))
    text:        Mapped[str]           = mapped_column(Text)
    created_at:  Mapped[datetime]      = mapped_column(DateTime(timezone=True), default=_now)
    delivered:   Mapped[bool]          = mapped_column(Boolean, default=False, index=True)
    delivered_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


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


# ---------------------------------------------------------------------------
# Reservation — imported from Airbnb CSV (one row per booking per tenant)
# ---------------------------------------------------------------------------

class Reservation(Base):
    __tablename__ = "reservations"
    __table_args__ = (
        UniqueConstraint("tenant_id", "confirmation_code", name="uq_reservation_tenant_code"),
    )

    id:                Mapped[int]           = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id:         Mapped[str]           = mapped_column(String(36), ForeignKey("tenants.id"), index=True)
    confirmation_code: Mapped[str]           = mapped_column(String(64), index=True)
    guest_name:        Mapped[str]           = mapped_column(String(128))
    listing_name:      Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    checkin:           Mapped[Optional[datetime]] = mapped_column(Date, nullable=True, index=True)
    checkout:          Mapped[Optional[datetime]] = mapped_column(Date, nullable=True, index=True)
    nights:            Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    guests_count:      Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    payout_usd:        Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    status:            Mapped[str]           = mapped_column(String(32), default="confirmed", index=True)  # confirmed / cancelled / pending
    imported_at:       Mapped[datetime]      = mapped_column(DateTime(timezone=True), default=_now)

    # Proactive message state flags (prevent re-sending)
    pre_arrival_sent:   Mapped[bool] = mapped_column(Boolean, default=False)
    checkout_msg_sent:  Mapped[bool] = mapped_column(Boolean, default=False)
    review_reminder_sent: Mapped[bool] = mapped_column(Boolean, default=False)
    cleaner_brief_sent: Mapped[bool] = mapped_column(Boolean, default=False)


# ---------------------------------------------------------------------------
# ReservationSyncLog — tracks when each tenant last uploaded their CSV
# ---------------------------------------------------------------------------

class ReservationSyncLog(Base):
    __tablename__ = "reservation_sync_logs"

    id:          Mapped[int]      = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id:   Mapped[str]      = mapped_column(String(36), ForeignKey("tenants.id"), unique=True, index=True)
    last_synced: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    rows_imported: Mapped[int]    = mapped_column(Integer, default=0)
