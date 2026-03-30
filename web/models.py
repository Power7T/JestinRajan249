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

# Subscription plans (old channel-based — kept for backward compat with Alembic)
PLAN_FREE       = "free"
PLAN_BAILEYS    = "baileys"  # kept for backward compat with billing.py (integration discontinued)
PLAN_META_CLOUD = "meta_cloud"
PLAN_SMS        = "sms"
PLAN_PRO        = "pro"   # all three channels

# New unit-based plans
PLAN_STARTER = "starter"
PLAN_GROWTH  = "growth"
PLAN_PRO_UNIT = "pro"  # Note: conflicts with PLAN_PRO above; will use PLAN_PRO for backward compat

# Workflow helpers
ROLE_OWNER       = "owner"
ROLE_MANAGER     = "manager"
ROLE_FRONT_DESK  = "front_desk"
ROLE_MAINTENANCE = "maintenance"
ROLE_CLEANER     = "cleaner"

INTAKE_SOURCE_CSV    = "csv"
INTAKE_SOURCE_PMS    = "pms"
INTAKE_SOURCE_MANUAL = "manual"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _uuid() -> str:
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# System Settings (Admin Panel)
# ---------------------------------------------------------------------------
class SystemConfig(Base):
    __tablename__ = "system_config"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    openrouter_api_key_enc: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    primary_model: Mapped[str] = mapped_column(String(100), default="anthropic/claude-3.5-sonnet")
    fallback_model: Mapped[str] = mapped_column(String(100), default="meta-llama/llama-3.1-70b-instruct")
    routine_model: Mapped[str] = mapped_column(String(100), default="google/gemini-2.5-flash")
    sentiment_model: Mapped[str] = mapped_column(String(100), default="openai/gpt-4o-mini")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)

# ---------------------------------------------------------------------------
# PlanConfig — subscription plan tiers (admin-editable)
# ---------------------------------------------------------------------------
class PlanConfig(Base):
    __tablename__ = "plan_configs"

    id:               Mapped[int]      = mapped_column(Integer, primary_key=True, autoincrement=True)
    plan_key:         Mapped[str]      = mapped_column(String(32), unique=True, index=True)  # "starter" / "growth" / "pro"
    display_name:     Mapped[str]      = mapped_column(String(128))  # "Starter" / "Growth" / "Pro"
    base_fee_usd:     Mapped[float]    = mapped_column(Float)  # 20.0
    per_unit_fee_usd: Mapped[float]    = mapped_column(Float)  # 10.0 / 9.0 / 8.0
    min_units:        Mapped[int]      = mapped_column(Integer)  # 1 / 6 / 11
    max_units:        Mapped[int]      = mapped_column(Integer)  # 5 / 10 / 50
    is_active:        Mapped[bool]     = mapped_column(Boolean, default=True)
    updated_at:       Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)

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

    # User profile
    first_name:   Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    last_name:    Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    phone:        Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    country:      Mapped[Optional[str]] = mapped_column(String(2), nullable=True)

    # Voice calling
    voice_enabled:     Mapped[bool]           = mapped_column(Boolean, default=False)
    voice_phone_number: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)

    # Email verification
    email_verified:      Mapped[bool]           = mapped_column(Boolean, default=False)
    verification_token:  Mapped[Optional[str]]  = mapped_column(String(128), nullable=True, index=True)
    verification_sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    # Password reset
    reset_token:          Mapped[Optional[str]]      = mapped_column(String(128), nullable=True, index=True)
    reset_token_expires:  Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    config:   Mapped[Optional["TenantConfig"]] = relationship("TenantConfig", back_populates="tenant", uselist=False)
    drafts:   Mapped[list["Draft"]]            = relationship("Draft", back_populates="tenant")
    reservations: Mapped[list["Reservation"]]  = relationship("Reservation", back_populates="tenant")
    vendors:  Mapped[list["Vendor"]]           = relationship("Vendor", back_populates="tenant")
    logs:     Mapped[list["ActivityLog"]]      = relationship("ActivityLog", back_populates="tenant")
    workflow_rules: Mapped[list["AutomationRule"]] = relationship("AutomationRule", back_populates="tenant")
    team_members:   Mapped[list["TeamMember"]]     = relationship("TeamMember", back_populates="tenant")
    timeline_events: Mapped[list["GuestTimelineEvent"]] = relationship("GuestTimelineEvent", back_populates="tenant")
    arrival_activations: Mapped[list["ArrivalActivation"]] = relationship("ArrivalActivation", back_populates="tenant")
    issue_tickets:  Mapped[list["IssueTicket"]]    = relationship("IssueTicket", back_populates="tenant")
    kpi_snapshots:  Mapped[list["TenantKpiSnapshot"]] = relationship("TenantKpiSnapshot", back_populates="tenant")
    intake_batches: Mapped[list["ReservationIntakeBatch"]] = relationship("ReservationIntakeBatch", back_populates="tenant")
    guest_contacts: Mapped[list["GuestContact"]]  = relationship("GuestContact", back_populates="tenant")
    voice_calls:    Mapped[list["VoiceCall"]]     = relationship("VoiceCall", back_populates="tenant")


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
    timezone:       Mapped[str]           = mapped_column(String(64), nullable=False, server_default="UTC")
    data_retention_days: Mapped[int]      = mapped_column(Integer, nullable=False, server_default="30")

    # Onboarding — property details (filled during wizard)
    property_type:       Mapped[Optional[str]] = mapped_column(String(64), nullable=True)   # apartment/villa/bnb/hotel
    property_city:       Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    check_in_time:       Mapped[Optional[str]] = mapped_column(String(32), nullable=True)   # e.g. "15:00"
    check_out_time:      Mapped[Optional[str]] = mapped_column(String(32), nullable=True)   # e.g. "11:00"
    max_guests:          Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    house_rules:         Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    pet_policy:          Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    refund_policy:       Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    early_checkin_policy: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    early_checkin_fee:   Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    late_checkout_policy: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    late_checkout_fee:   Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    parking_policy:      Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    smoking_policy:      Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    quiet_hours:         Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
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
    email_ingest_mode: Mapped[str]           = mapped_column(String(32), default="imap")
    inbound_email_alias: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    last_inbound_email_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

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

    # Voice AI — in-call send + post-call options
    voice_send_channel:           Mapped[str]           = mapped_column(String(16), default="disabled")  # disabled | sms | whatsapp
    voice_post_call_summary:      Mapped[bool]          = mapped_column(Boolean, default=False)
    voice_scheduled_calls_enabled: Mapped[bool]         = mapped_column(Boolean, default=False)
    # Voice AI — Twilio credentials (per-tenant)
    voice_twilio_account_sid:     Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    voice_twilio_auth_token_enc:  Mapped[Optional[str]] = mapped_column(Text, nullable=True)   # encrypted
    voice_twilio_from_number:     Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    # Voice AI — ElevenLabs voice selection
    voice_elevenlabs_voice_id:    Mapped[Optional[str]] = mapped_column(String(64), nullable=True, default="EXAVITQu4vr4xnSDxMaL")

    # Host notifications on guest messages
    notify_host_on_guest_msg: Mapped[bool]           = mapped_column(Boolean, default=False)
    host_notify_phone:        Mapped[Optional[str]]  = mapped_column(String(32), nullable=True)  # optional separate phone for notifications

    # AI usage limits (for free tier enforcement)
    ai_calls_today:      Mapped[int]      = mapped_column(Integer, default=0)
    ai_calls_today_date: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)  # date counter was last reset
    ai_calls_monthly:    Mapped[int]      = mapped_column(Integer, default=0)
    ai_calls_monthly_date: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)  # month counter was last reset

    # Subscription (Stripe) — unit-based billing
    subscription_plan:       Mapped[str]           = mapped_column(String(32), default="starter")
    subscription_status:     Mapped[str]           = mapped_column(String(32), default="requires_upgrade")
    subscription_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    stripe_customer_id:      Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    stripe_subscription_id:  Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    num_units:               Mapped[int]           = mapped_column(Integer, default=1)  # units this tenant manages

    # Onboarding step 3: extra services (comma-separated, stored separately for re-population)
    extra_services: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Guest welcome message template for custom welcome text
    guest_welcome_template: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

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
    draft:        Mapped[str]           = mapped_column(Text)
    final_text:   Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status:       Mapped[str]           = mapped_column(String(16), default="pending", index=True)
    created_at:   Mapped[datetime]      = mapped_column(DateTime(timezone=True), default=_now)
    approved_at:  Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    scheduled_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    reservation_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("reservations.id"), nullable=True, index=True)
    automation_rule_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("automation_rules.id"), nullable=True, index=True)
    parent_draft_id: Mapped[Optional[str]] = mapped_column(String(64), ForeignKey("drafts.id"), nullable=True, index=True)
    thread_key:      Mapped[Optional[str]] = mapped_column(String(128), nullable=True, index=True)
    guest_message_index: Mapped[int]       = mapped_column(Integer, default=1)
    property_name_snapshot: Mapped[Optional[str]] = mapped_column(String(256), nullable=True, index=True)
    unit_identifier_snapshot: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    confidence:      Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    auto_send_eligible: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    guest_history_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    guest_sentiment: Mapped[Optional[str]] = mapped_column(String(16), nullable=True, index=True)
    sentiment_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    stay_stage: Mapped[Optional[str]] = mapped_column(String(32), nullable=True, index=True)
    policy_conflicts_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    host_feedback_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    host_feedback_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    host_feedback_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    context_sources: Mapped[Optional[str]]   = mapped_column(Text, nullable=True)   # JSON list of source labels
    archived_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    tenant: Mapped["Tenant"] = relationship("Tenant", back_populates="drafts")
    reservation: Mapped[Optional["Reservation"]] = relationship("Reservation", back_populates="drafts")
    timeline_events: Mapped[list["GuestTimelineEvent"]] = relationship("GuestTimelineEvent", back_populates="draft")
    automation_rule: Mapped[Optional["AutomationRule"]] = relationship("AutomationRule", back_populates="drafts")
    parent_draft: Mapped[Optional["Draft"]] = relationship("Draft", remote_side=[id])


# ---------------------------------------------------------------------------
# FailedDraftLog — dead-letter table for automated drafts that failed to send
# ---------------------------------------------------------------------------

class FailedDraftLog(Base):
    __tablename__ = "failed_draft_logs"

    id:           Mapped[int]      = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id:    Mapped[str]      = mapped_column(String(36), ForeignKey("tenants.id"), index=True)
    draft_id:     Mapped[str]      = mapped_column(String(64), index=True)
    error_reason: Mapped[str]      = mapped_column(Text)
    created_at:   Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


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
    __table_args__ = (
        UniqueConstraint("tenant_id", "state_key", name="uq_calendar_state_tenant_key"),
    )

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
# ApiUsageLog — tracks LLM token usage and estimated cost
# ---------------------------------------------------------------------------

class ApiUsageLog(Base):
    __tablename__ = "api_usage_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("tenants.id"), index=True, nullable=True)
    model: Mapped[str] = mapped_column(String(100))
    provider: Mapped[str] = mapped_column(String(50)) 
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    feature: Mapped[str] = mapped_column(String(50))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    tenant: Mapped[Optional["Tenant"]] = relationship("Tenant")


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
    guest_phone:       Mapped[Optional[str]] = mapped_column(String(32), nullable=True, index=True)
    listing_name:      Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    unit_identifier:   Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    checkin:           Mapped[Optional[datetime]] = mapped_column(Date, nullable=True, index=True)
    checkout:          Mapped[Optional[datetime]] = mapped_column(Date, nullable=True, index=True)
    nights:            Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    guests_count:      Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    payout_usd:        Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    status:            Mapped[str]           = mapped_column(String(32), default="confirmed", index=True)  # confirmed / cancelled / pending
    imported_at:       Mapped[datetime]      = mapped_column(DateTime(timezone=True), default=_now)
    last_guest_message_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    last_host_reply_at:    Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    review_rating:     Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    review_text:       Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    review_submitted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    review_sentiment:  Mapped[Optional[str]] = mapped_column(String(16), nullable=True, index=True)
    review_sentiment_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    guest_feedback_positive: Mapped[int] = mapped_column(Integer, default=0)
    guest_feedback_negative: Mapped[int] = mapped_column(Integer, default=0)
    guest_satisfaction_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    repeat_guest_count: Mapped[int] = mapped_column(Integer, default=0)
    message_count: Mapped[int] = mapped_column(Integer, default=0)
    latest_guest_sentiment: Mapped[Optional[str]] = mapped_column(String(16), nullable=True, index=True)
    latest_guest_sentiment_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # Proactive message state flags (prevent re-sending)
    pre_arrival_sent:     Mapped[bool]          = mapped_column(Boolean, default=False)
    checkout_msg_sent:    Mapped[bool]          = mapped_column(Boolean, default=False)
    review_reminder_sent: Mapped[bool]          = mapped_column(Boolean, default=False)
    cleaner_brief_sent:   Mapped[bool]          = mapped_column(Boolean, default=False)
    intake_batch_id:      Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("reservation_intake_batches.id"), nullable=True, index=True)

    # Guest-facing check-in portal token (random URL-safe string, unique per reservation)
    checkin_token: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True, unique=True)
    checkin_token_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    tenant: Mapped["Tenant"] = relationship("Tenant", back_populates="reservations")
    intake_batch: Mapped[Optional["ReservationIntakeBatch"]] = relationship("ReservationIntakeBatch", back_populates="reservations")
    drafts: Mapped[list["Draft"]] = relationship("Draft", back_populates="reservation")
    timeline_events: Mapped[list["GuestTimelineEvent"]] = relationship("GuestTimelineEvent", back_populates="reservation")
    activations: Mapped[list["ArrivalActivation"]] = relationship("ArrivalActivation", back_populates="reservation")
    issue_tickets: Mapped[list["IssueTicket"]] = relationship("IssueTicket", back_populates="reservation")
    guest_contacts: Mapped[list["GuestContact"]] = relationship("GuestContact", back_populates="reservation")


# ---------------------------------------------------------------------------
# GuestContact — guest contact info added by host (for bot whitelisting)
# ---------------------------------------------------------------------------

class GuestContact(Base):
    __tablename__ = "guest_contacts"
    __table_args__ = (
        UniqueConstraint("tenant_id", "guest_phone", "check_in", name="uq_guest_contact"),
    )

    id:                Mapped[str]           = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id:         Mapped[str]           = mapped_column(String(36), ForeignKey("tenants.id"), index=True)
    reservation_id:    Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("reservations.id"), nullable=True, index=True)

    # Guest info
    guest_name:        Mapped[str]           = mapped_column(String(128))
    guest_phone:       Mapped[str]           = mapped_column(String(32), index=True)  # Whitelisted number

    # Property/Room details
    property_name:     Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    room_identifier:   Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    # Check-in/out (from iCal or manually entered)
    check_in:          Mapped[datetime]      = mapped_column(DateTime(timezone=True), index=True)
    check_out:         Mapped[datetime]      = mapped_column(DateTime(timezone=True), index=True)

    # Status tracking
    status:            Mapped[str]           = mapped_column(String(32), default="pending", index=True)  # pending, active, completed, cancelled

    # Welcome message tracking
    welcome_sent_at:   Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    welcome_sent_to_host: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    welcome_status:    Mapped[str]           = mapped_column(String(32), default="pending")  # pending, sent, failed, retry

    # Retry tracking
    welcome_retry_count: Mapped[int]         = mapped_column(Integer, default=0)
    last_retry_at:     Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    # Timestamps
    created_at:        Mapped[datetime]      = mapped_column(DateTime(timezone=True), default=_now)
    updated_at:        Mapped[datetime]      = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)

    reservation: Mapped[Optional["Reservation"]] = relationship("Reservation", back_populates="guest_contacts")
    tenant: Mapped["Tenant"] = relationship("Tenant", back_populates="guest_contacts")
    voice_calls: Mapped[list["VoiceCall"]] = relationship("VoiceCall", back_populates="guest_contact")


# ---------------------------------------------------------------------------
# PMSIntegration — one row per PMS connection per tenant
# ---------------------------------------------------------------------------

class PMSIntegration(Base):
    __tablename__ = "pms_integrations"

    id:             Mapped[int]           = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id:      Mapped[str]           = mapped_column(String(36), ForeignKey("tenants.id"), index=True)
    pms_type:       Mapped[str]           = mapped_column(String(32))          # guesty / hostaway / lodgify / generic
    api_key_enc:    Mapped[str]           = mapped_column(Text)                # AES-encrypted API key / credentials
    api_base_url:   Mapped[Optional[str]] = mapped_column(Text, nullable=True) # optional custom base URL
    account_id:     Mapped[Optional[str]] = mapped_column(Text, nullable=True) # extra config (JSON for generic, account ID for others)
    is_active:      Mapped[bool]          = mapped_column(Boolean, default=True)
    last_synced_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at:     Mapped[datetime]      = mapped_column(DateTime(timezone=True), default=_now)


# ---------------------------------------------------------------------------
# PMSProcessedMessage — deduplication: tracks which PMS message IDs were handled
# ---------------------------------------------------------------------------

class PMSProcessedMessage(Base):
    __tablename__ = "pms_processed_messages"
    __table_args__ = (
        UniqueConstraint("pms_integration_id", "pms_message_id", name="uq_pms_msg"),
    )

    id:                  Mapped[int]      = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id:           Mapped[str]      = mapped_column(String(36), ForeignKey("tenants.id"), index=True)
    pms_integration_id:  Mapped[int]      = mapped_column(Integer, ForeignKey("pms_integrations.id"), index=True)
    pms_message_id:      Mapped[str]      = mapped_column(String(128))
    processed_at:        Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


# ---------------------------------------------------------------------------
# ReservationSyncLog — tracks when each tenant last uploaded their CSV
# ---------------------------------------------------------------------------

class ReservationSyncLog(Base):
    __tablename__ = "reservation_sync_logs"

    id:          Mapped[int]      = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id:   Mapped[str]      = mapped_column(String(36), ForeignKey("tenants.id"), unique=True, index=True)
    last_synced: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    rows_imported: Mapped[int]    = mapped_column(Integer, default=0)


# ---------------------------------------------------------------------------
# ReservationIntakeBatch — tracks CSV / PMS / manual reservation ingestion
# ---------------------------------------------------------------------------

class ReservationIntakeBatch(Base):
    __tablename__ = "reservation_intake_batches"

    id:                Mapped[int]           = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id:         Mapped[str]           = mapped_column(String(36), ForeignKey("tenants.id"), index=True)
    source_kind:       Mapped[str]           = mapped_column(String(16), default=INTAKE_SOURCE_CSV, index=True)
    source_name:       Mapped[Optional[str]]  = mapped_column(String(128), nullable=True)
    external_reference: Mapped[Optional[str]] = mapped_column(String(128), nullable=True, index=True)
    status:            Mapped[str]           = mapped_column(String(32), default="queued", index=True)
    rows_total:        Mapped[int]           = mapped_column(Integer, default=0)
    rows_imported:     Mapped[int]           = mapped_column(Integer, default=0)
    rows_failed:       Mapped[int]           = mapped_column(Integer, default=0)
    notes:             Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    details_json:      Mapped[dict]          = mapped_column(JSON, default=dict)
    pms_integration_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("pms_integrations.id"), nullable=True, index=True)
    created_by_member_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("team_members.id"), nullable=True, index=True)
    started_at:        Mapped[datetime]      = mapped_column(DateTime(timezone=True), default=_now)
    completed_at:      Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at:        Mapped[datetime]      = mapped_column(DateTime(timezone=True), default=_now)
    updated_at:        Mapped[datetime]      = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)

    tenant: Mapped["Tenant"] = relationship("Tenant", back_populates="intake_batches")
    pms_integration: Mapped[Optional["PMSIntegration"]] = relationship("PMSIntegration")
    created_by_member: Mapped[Optional["TeamMember"]] = relationship("TeamMember", back_populates="created_batches")
    reservations: Mapped[list["Reservation"]] = relationship("Reservation", back_populates="intake_batch")


# ---------------------------------------------------------------------------
# AutomationRule — host-specific automation logic and routing
# ---------------------------------------------------------------------------

class AutomationRule(Base):
    __tablename__ = "automation_rules"

    id:          Mapped[int]           = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id:   Mapped[str]           = mapped_column(String(36), ForeignKey("tenants.id"), index=True)
    name:        Mapped[str]           = mapped_column(String(128))
    description: Mapped[Optional[str]]  = mapped_column(Text, nullable=True)
    trigger_kind: Mapped[str]          = mapped_column(String(32), default="inbound_message", index=True)
    scope_kind:  Mapped[str]           = mapped_column(String(32), default="tenant", index=True)
    channel:     Mapped[str]           = mapped_column(String(32), default="any", index=True)
    is_active:   Mapped[bool]          = mapped_column(Boolean, default=True, index=True)
    priority:    Mapped[int]           = mapped_column(Integer, default=100, index=True)
    confidence_threshold: Mapped[float] = mapped_column(Float, default=0.0)
    conditions_json: Mapped[dict]       = mapped_column(JSON, default=dict)
    actions_json:    Mapped[dict]       = mapped_column(JSON, default=dict)
    last_triggered_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at:      Mapped[datetime]   = mapped_column(DateTime(timezone=True), default=_now)
    updated_at:      Mapped[datetime]   = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)

    tenant: Mapped["Tenant"] = relationship("Tenant", back_populates="workflow_rules")
    drafts: Mapped[list["Draft"]] = relationship("Draft", back_populates="automation_rule")
    timeline_events: Mapped[list["GuestTimelineEvent"]] = relationship("GuestTimelineEvent", back_populates="automation_rule")


# ---------------------------------------------------------------------------
# TeamMember — staff/ops users per tenant
# ---------------------------------------------------------------------------

class TeamMember(Base):
    __tablename__ = "team_members"
    __table_args__ = (
        UniqueConstraint("tenant_id", "email", name="uq_team_member_tenant_email"),
    )

    id:          Mapped[int]           = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id:   Mapped[str]           = mapped_column(String(36), ForeignKey("tenants.id"), index=True)
    display_name: Mapped[str]          = mapped_column(String(128))
    email:       Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    phone:       Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    role:        Mapped[str]           = mapped_column(String(32), default=ROLE_MANAGER, index=True)
    is_active:   Mapped[bool]          = mapped_column(Boolean, default=True, index=True)
    property_scope: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    permissions_json: Mapped[dict]     = mapped_column(JSON, default=dict)
    last_login_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    password_hash: Mapped[Optional[str]]      = mapped_column(String(128), nullable=True)  # bcrypt; null until invite accepted
    invite_token:  Mapped[Optional[str]]      = mapped_column(String(64), nullable=True, index=True)
    invite_token_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at:  Mapped[datetime]      = mapped_column(DateTime(timezone=True), default=_now)
    updated_at:  Mapped[datetime]      = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)

    tenant: Mapped["Tenant"] = relationship("Tenant", back_populates="team_members")
    created_batches: Mapped[list["ReservationIntakeBatch"]] = relationship("ReservationIntakeBatch", back_populates="created_by_member")
    assigned_issues: Mapped[list["IssueTicket"]] = relationship("IssueTicket", back_populates="assigned_to_member", foreign_keys="IssueTicket.assigned_to_member_id")
    created_issues: Mapped[list["IssueTicket"]] = relationship("IssueTicket", back_populates="created_by_member", foreign_keys="IssueTicket.created_by_member_id")
    created_timeline_events: Mapped[list["GuestTimelineEvent"]] = relationship("GuestTimelineEvent", back_populates="created_by_member")
    created_activations: Mapped[list["ArrivalActivation"]] = relationship("ArrivalActivation", back_populates="created_by_member")


# ---------------------------------------------------------------------------
# GuestTimelineEvent — unified activity feed for a stay / guest
# ---------------------------------------------------------------------------

class GuestTimelineEvent(Base):
    __tablename__ = "guest_timeline_events"

    id:          Mapped[int]           = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id:   Mapped[str]           = mapped_column(String(36), ForeignKey("tenants.id"), index=True)
    reservation_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("reservations.id"), nullable=True, index=True)
    draft_id:    Mapped[Optional[str]]  = mapped_column(String(64), ForeignKey("drafts.id"), nullable=True, index=True)
    issue_ticket_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("issue_tickets.id"), nullable=True, index=True)
    automation_rule_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("automation_rules.id"), nullable=True, index=True)
    intake_batch_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("reservation_intake_batches.id"), nullable=True, index=True)
    created_by_member_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("team_members.id"), nullable=True, index=True)
    guest_name:  Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    guest_phone: Mapped[Optional[str]] = mapped_column(String(32), nullable=True, index=True)
    property_name: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    unit_identifier: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    channel:     Mapped[str]           = mapped_column(String(32), default="system", index=True)
    direction:   Mapped[str]           = mapped_column(String(16), default="internal", index=True)
    event_type:  Mapped[str]           = mapped_column(String(64), index=True)
    summary:     Mapped[str]           = mapped_column(String(255))
    body:        Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    payload_json: Mapped[dict]         = mapped_column(JSON, default=dict)
    created_at:  Mapped[datetime]      = mapped_column(DateTime(timezone=True), default=_now, index=True)

    tenant: Mapped["Tenant"] = relationship("Tenant", back_populates="timeline_events")
    reservation: Mapped[Optional["Reservation"]] = relationship("Reservation", back_populates="timeline_events")
    draft: Mapped[Optional["Draft"]] = relationship("Draft", back_populates="timeline_events")
    issue_ticket: Mapped[Optional["IssueTicket"]] = relationship("IssueTicket", back_populates="timeline_events")
    automation_rule: Mapped[Optional["AutomationRule"]] = relationship("AutomationRule", back_populates="timeline_events")
    intake_batch: Mapped[Optional["ReservationIntakeBatch"]] = relationship("ReservationIntakeBatch")
    created_by_member: Mapped[Optional["TeamMember"]] = relationship("TeamMember", back_populates="created_timeline_events")


# ---------------------------------------------------------------------------
# ArrivalActivation — explicit check-in / bot activation records
# ---------------------------------------------------------------------------

class ArrivalActivation(Base):
    __tablename__ = "arrival_activations"

    id:          Mapped[int]           = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id:   Mapped[str]           = mapped_column(String(36), ForeignKey("tenants.id"), index=True)
    reservation_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("reservations.id"), nullable=True, index=True)
    timeline_event_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("guest_timeline_events.id"), nullable=True, index=True)
    created_by_member_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("team_members.id"), nullable=True, index=True)
    property_name: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    unit_identifier: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    guest_name:  Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    guest_phone: Mapped[Optional[str]] = mapped_column(String(32), nullable=True, index=True)
    activation_source: Mapped[str]     = mapped_column(String(32), default=INTAKE_SOURCE_MANUAL, index=True)
    status:      Mapped[str]           = mapped_column(String(32), default="pending", index=True)
    notes:       Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    activated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    deactivated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at:  Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    payload_json: Mapped[dict]         = mapped_column(JSON, default=dict)
    created_at:  Mapped[datetime]      = mapped_column(DateTime(timezone=True), default=_now)
    updated_at:  Mapped[datetime]      = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)

    tenant: Mapped["Tenant"] = relationship("Tenant", back_populates="arrival_activations")
    reservation: Mapped[Optional["Reservation"]] = relationship("Reservation", back_populates="activations")
    timeline_event: Mapped[Optional["GuestTimelineEvent"]] = relationship("GuestTimelineEvent")
    created_by_member: Mapped[Optional["TeamMember"]] = relationship("TeamMember", back_populates="created_activations")


# ---------------------------------------------------------------------------
# IssueTicket — exception / maintenance / ops tracking
# ---------------------------------------------------------------------------

class IssueTicket(Base):
    __tablename__ = "issue_tickets"

    id:          Mapped[int]           = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id:   Mapped[str]           = mapped_column(String(36), ForeignKey("tenants.id"), index=True)
    reservation_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("reservations.id"), nullable=True, index=True)
    created_by_member_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("team_members.id"), nullable=True, index=True)
    assigned_to_member_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("team_members.id"), nullable=True, index=True)
    vendor_id:   Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("vendors.id"), nullable=True, index=True)
    property_name: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    unit_identifier: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    guest_name:  Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    guest_phone: Mapped[Optional[str]] = mapped_column(String(32), nullable=True, index=True)
    category:    Mapped[str]           = mapped_column(String(32), default="general", index=True)
    priority:    Mapped[str]           = mapped_column(String(16), default="medium", index=True)
    status:      Mapped[str]           = mapped_column(String(32), default="open", index=True)
    title:       Mapped[str]           = mapped_column(String(255))
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    resolution_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    due_at:      Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    payload_json: Mapped[dict]         = mapped_column(JSON, default=dict)
    created_at:  Mapped[datetime]      = mapped_column(DateTime(timezone=True), default=_now, index=True)
    updated_at:  Mapped[datetime]      = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)

    tenant: Mapped["Tenant"] = relationship("Tenant", back_populates="issue_tickets")
    reservation: Mapped[Optional["Reservation"]] = relationship("Reservation", back_populates="issue_tickets")
    timeline_events: Mapped[list["GuestTimelineEvent"]] = relationship("GuestTimelineEvent", back_populates="issue_ticket")
    created_by_member: Mapped[Optional["TeamMember"]] = relationship("TeamMember", back_populates="created_issues", foreign_keys=[created_by_member_id])
    assigned_to_member: Mapped[Optional["TeamMember"]] = relationship("TeamMember", back_populates="assigned_issues", foreign_keys=[assigned_to_member_id])
    vendor: Mapped[Optional["Vendor"]] = relationship("Vendor")


# ---------------------------------------------------------------------------
# TenantKpiSnapshot — periodic KPI summary for dashboards / admin views
# ---------------------------------------------------------------------------

class TenantKpiSnapshot(Base):
    __tablename__ = "tenant_kpi_snapshots"
    __table_args__ = (
        UniqueConstraint("tenant_id", "property_name", "period_start", "period_end", name="uq_tenant_kpi_snapshot_window"),
    )

    id:          Mapped[int]           = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id:   Mapped[str]           = mapped_column(String(36), ForeignKey("tenants.id"), index=True)
    property_name: Mapped[Optional[str]] = mapped_column(String(256), nullable=True, index=True)
    period_start: Mapped[datetime]     = mapped_column(DateTime(timezone=True), index=True)
    period_end:   Mapped[datetime]     = mapped_column(DateTime(timezone=True), index=True)
    messages_total: Mapped[int]        = mapped_column(Integer, default=0)
    drafts_total:   Mapped[int]        = mapped_column(Integer, default=0)
    auto_sent_total: Mapped[int]        = mapped_column(Integer, default=0)
    approvals_total: Mapped[int]       = mapped_column(Integer, default=0)
    escalations_total: Mapped[int]     = mapped_column(Integer, default=0)
    open_issues_total: Mapped[int]     = mapped_column(Integer, default=0)
    resolved_issues_total: Mapped[int] = mapped_column(Integer, default=0)
    avg_response_seconds: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    automation_rate_pct: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    edit_rate_pct:      Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    saved_hours:        Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    payload_json:       Mapped[dict]   = mapped_column(JSON, default=dict)
    created_at:         Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    tenant: Mapped["Tenant"] = relationship("Tenant", back_populates="kpi_snapshots")


# ---------------------------------------------------------------------------
# VoiceCall — incoming and outbound voice calls via Twilio
# ---------------------------------------------------------------------------

class VoiceCall(Base):
    __tablename__ = "voice_calls"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(36), ForeignKey("tenants.id"), index=True)
    guest_contact_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("guest_contacts.id"), nullable=True, index=True)

    # Twilio info
    twilio_call_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    twilio_phone_number: Mapped[str] = mapped_column(String(32))
    guest_phone_number: Mapped[str] = mapped_column(String(32), index=True)

    # Call details
    call_type: Mapped[str] = mapped_column(String(16))  # incoming / outbound
    status: Mapped[str] = mapped_column(String(32), default="ringing", index=True)  # ringing / answered / completed / failed

    # Conversation history (JSON arrays)
    guest_messages: Mapped[list] = mapped_column(JSON, default=list)
    ai_responses: Mapped[list] = mapped_column(JSON, default=list)

    # Analytics
    full_transcript: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    confidence_avg: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    sentiment: Mapped[Optional[str]] = mapped_column(String(16), nullable=True, index=True)
    duration_seconds: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    recording_url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    tenant: Mapped["Tenant"] = relationship("Tenant", back_populates="voice_calls")
    guest_contact: Mapped[Optional["GuestContact"]] = relationship("GuestContact", back_populates="voice_calls")
    knowledge_gaps: Mapped[list["VoiceKnowledgeGap"]] = relationship("VoiceKnowledgeGap", back_populates="call", cascade="all, delete-orphan")


# ---------------------------------------------------------------------------
# VoiceKnowledgeGap — questions the AI couldn't answer; host can fill in
# ---------------------------------------------------------------------------

class VoiceKnowledgeGap(Base):
    __tablename__ = "voice_knowledge_gaps"

    id:          Mapped[str]           = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id:   Mapped[str]           = mapped_column(String(36), ForeignKey("tenants.id"), index=True)
    call_id:     Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("voice_calls.id"), nullable=True, index=True)

    # Guest who asked (copied from voice_call at creation time for easy access)
    guest_phone: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    guest_name:  Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    guest_room:  Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    question:    Mapped[str]           = mapped_column(Text)
    host_answer: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    saved_to:    Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    resolved:    Mapped[bool]          = mapped_column(Boolean, default=False, index=True)

    # Reply back to guest
    reply_sent:    Mapped[bool]               = mapped_column(Boolean, default=False)
    reply_sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    reply_channel: Mapped[Optional[str]]      = mapped_column(String(16), nullable=True)  # sms | whatsapp

    alerted_at:  Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at:  Mapped[datetime]           = mapped_column(DateTime(timezone=True), default=_now, index=True)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    call:   Mapped[Optional["VoiceCall"]] = relationship("VoiceCall", back_populates="knowledge_gaps")
    tenant: Mapped["Tenant"]              = relationship("Tenant")
