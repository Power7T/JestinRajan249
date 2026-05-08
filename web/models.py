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
    google_maps_api_key_enc: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)  # Google Maps Places API key
    deepgram_api_key_enc: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    elevenlabs_api_key_enc: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    cloudflare_account_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    cloudflare_r2_access_key_enc: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    cloudflare_r2_secret_key_enc: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    cloudflare_r2_bucket: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    # Primary models (used 99% of time)
    primary_model: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, default="mistralai/mistral-large-2512")
    routine_model: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, default="google/gemini-2.5-flash")

    # Backup models (used if primary fails - 1% of time)
    primary_backup_model: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, default="anthropic/claude-3.5-sonnet")
    routine_backup_model: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, default="anthropic/claude-3.5-haiku")

    # Fallback models (emergency only)
    fallback_model: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, default="meta-llama/llama-3.3-70b-instruct")
    sentiment_model: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, default="openai/gpt-4o-mini")

    # Voice AI admin defaults and test harness settings
    voice_llm_model: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, default="openai/gpt-4o-mini")
    voice_llm_backup_model: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, default="anthropic/claude-3.5-haiku")
    voice_llm_emergency_model: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, default="meta-llama/llama-3.3-70b-instruct")
    voice_deepgram_model: Mapped[Optional[str]] = mapped_column(String(50), nullable=True, default="nova-2")
    voice_llm_max_tokens: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, default=300)
    voice_llm_temperature: Mapped[Optional[float]] = mapped_column(Float, nullable=True, default=0.7)
    voice_elevenlabs_model: Mapped[Optional[str]] = mapped_column(String(50), nullable=True, default="eleven_turbo_v2")
    voice_elevenlabs_stability: Mapped[Optional[float]] = mapped_column(Float, nullable=True, default=0.5)
    voice_elevenlabs_similarity: Mapped[Optional[float]] = mapped_column(Float, nullable=True, default=0.75)
    voice_elevenlabs_voice_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, default="EXAVITQu4vr4xnSDxMaL")
    # Google Cloud TTS
    google_tts_api_key_enc: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    voice_tts_provider: Mapped[Optional[str]] = mapped_column(String(20), nullable=True, default="google")
    voice_google_tts_voice: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, default="en-US-Neural2-F")
    voice_google_tts_language: Mapped[Optional[str]] = mapped_column(String(16), nullable=True, default="en-US")
    voice_google_tts_speaking_rate: Mapped[Optional[float]] = mapped_column(Float, nullable=True, default=1.0)
    # Phase 2: API budget tracking
    api_budget_monthly_usd: Mapped[Optional[float]] = mapped_column(Float, nullable=True, default=100.0)
    api_budget_alert_percent: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, default=80)
    # Security: admin IP whitelist (comma-separated CIDRs/IPs; empty = allow all)
    admin_ip_whitelist: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True, default=_now, onupdate=_now)

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
# VoicePricingConfig — Voice AI add-on pricing (editable in admin panel)
# ---------------------------------------------------------------------------

class VoicePricingConfig(Base):
    __tablename__ = "voice_pricing_configs"

    id:                     Mapped[int]      = mapped_column(Integer, primary_key=True, autoincrement=True)
    voice_tier:             Mapped[str]      = mapped_column(String(32), unique=True, index=True)  # "light" / "standard" / "professional" / "unlimited"
    display_name:           Mapped[str]      = mapped_column(String(128))  # "Voice Light" / "Voice Standard" / etc
    monthly_price_usd:      Mapped[float]    = mapped_column(Float)  # 39.0 / 79.0 / 129.0 / 199.0
    minutes_included:       Mapped[int]      = mapped_column(Integer, nullable=True)  # 100 / 300 / 750 / None (unlimited)
    overage_per_minute_usd: Mapped[float]    = mapped_column(Float)  # 0.049
    surge_threshold:        Mapped[float]    = mapped_column(Float, default=0.5)  # Apply surge if >50% over limit
    surge_multiplier:       Mapped[float]    = mapped_column(Float, default=1.15)  # 15% surcharge
    cost_basis_usd:         Mapped[float]    = mapped_column(Float)  # 1.80 / 5.40 / 13.50 / 36.0
    markup_ratio:           Mapped[float]    = mapped_column(Float)  # 21.7 / 14.6 / 9.6 / 5.5
    description:            Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # Feature list
    is_active:              Mapped[bool]     = mapped_column(Boolean, default=True)
    updated_at:             Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)

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
    voice_forward_enabled: Mapped[bool]       = mapped_column(Boolean, default=False)
    voice_forward_number: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)

    # Email verification
    email_verified:      Mapped[bool]           = mapped_column(Boolean, default=False)
    verification_token:  Mapped[Optional[str]]  = mapped_column(String(128), nullable=True, index=True)
    verification_sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    # Password reset
    reset_token:          Mapped[Optional[str]]      = mapped_column(String(128), nullable=True, index=True)
    reset_token_expires:  Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    config:   Mapped[Optional["TenantConfig"]] = relationship("TenantConfig", back_populates="tenant", uselist=False)
    properties: Mapped[list["Property"]]      = relationship("Property", foreign_keys="Property.tenant_id")
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
    voice_routing_config: Mapped[Optional["VoiceRoutingConfig"]] = relationship("VoiceRoutingConfig", back_populates="tenant", uselist=False)
    routing_rules:  Mapped[list["RoutingRule"]]   = relationship("RoutingRule", back_populates="tenant")
    upsell_offers:  Mapped[list["UpsellOffer"]]   = relationship("UpsellOffer", back_populates="tenant")
    sales_config:   Mapped[Optional["SalesConfig"]] = relationship("SalesConfig", back_populates="tenant", uselist=False)
    sales_conversations: Mapped[list["SalesConversation"]] = relationship("SalesConversation", back_populates="tenant")


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
    twilio_whatsapp_number: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)   # Twilio WA sender e.g. +14155238886
    nearby_restaurants:  Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    google_maps_url:     Mapped[Optional[str]] = mapped_column(String(512), nullable=True)  # Google Maps link for location context
    faq:                 Mapped[Optional[str]] = mapped_column(Text, nullable=True)         # free-form Q&A text
    custom_instructions: Mapped[Optional[str]] = mapped_column(Text, nullable=True)         # host's special instructions to the AI
    escalation_email:    Mapped[Optional[str]] = mapped_column(String(255), nullable=True)  # where to send human-handoff alerts

    # Onboarding progress
    onboarding_complete: Mapped[bool] = mapped_column(Boolean, default=False)
    onboarding_step:     Mapped[int]  = mapped_column(Integer, default=0)

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

    # Voice AI — LLM Models (in-call conversation)
    voice_llm_model:              Mapped[Optional[str]] = mapped_column(String(100), nullable=True, default="openai/gpt-4o-mini")
    voice_llm_backup_model:       Mapped[Optional[str]] = mapped_column(String(100), nullable=True, default="anthropic/claude-3.5-haiku")
    voice_llm_emergency_model:    Mapped[Optional[str]] = mapped_column(String(100), nullable=True, default="meta-llama/llama-3.3-70b-instruct")

    # Voice AI — Deepgram STT Model
    voice_deepgram_model:         Mapped[Optional[str]] = mapped_column(String(50), nullable=True, default="nova-2")

    # Voice AI — Response Configuration
    voice_llm_max_tokens:         Mapped[Optional[int]] = mapped_column(Integer, nullable=True, default=300)
    voice_llm_temperature:        Mapped[Optional[float]] = mapped_column(Float, nullable=True, default=0.7)

    # Voice AI — ElevenLabs TTS Settings
    voice_elevenlabs_stability:   Mapped[Optional[float]] = mapped_column(Float, nullable=True, default=0.5)
    voice_elevenlabs_similarity:  Mapped[Optional[float]] = mapped_column(Float, nullable=True, default=0.75)
    voice_elevenlabs_model:       Mapped[Optional[str]] = mapped_column(String(50), nullable=True, default="eleven_turbo_v2")

    # Voice AI — Google Cloud TTS voice (per-tenant override; falls back to system default)
    voice_google_tts_voice:       Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

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
    paypal_subscription_id:  Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    subscription_payment_method: Mapped[str]      = mapped_column(String(16), default="stripe")
    num_units:               Mapped[int]           = mapped_column(Integer, default=1)  # units this tenant manages

    # Onboarding step 3: extra services (comma-separated, stored separately for re-population)
    extra_services: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Guest welcome message template for custom welcome text
    guest_welcome_template: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Internal token (auto-generated) for service-to-service auth
    internal_token: Mapped[str] = mapped_column(String(64), default=lambda: str(uuid.uuid4()))

    # Digest / daily summary email
    digest_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false", default=False)
    bot_last_heartbeat: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    digest_email_address: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)  # separate digest email if different from account

    # Default AI response language
    default_response_language: Mapped[Optional[str]] = mapped_column(String(8), nullable=True, default="en")

    # Guest review request automation
    review_request_url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)    # Airbnb / Google review link
    review_request_enabled: Mapped[bool] = mapped_column(Boolean, default=False)

    # Guest satisfaction pulse (rate 1-5 after checkout)
    satisfaction_pulse_enabled: Mapped[bool] = mapped_column(Boolean, default=False)

    # Upsell offers engine
    upsell_enabled: Mapped[bool] = mapped_column(Boolean, default=False)

    # iCal sync health tracking
    ical_last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    ical_last_error_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    ical_last_success_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    tenant: Mapped["Tenant"] = relationship("Tenant", back_populates="config")


# ---------------------------------------------------------------------------
# Property — represents a single property managed by a tenant
# ---------------------------------------------------------------------------

class Property(Base):
    __tablename__ = "properties"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(36), ForeignKey("tenants.id"), index=True, nullable=False)

    # Property metadata
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)  # "Villa A", "Beachfront", etc.
    property_type: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)  # apartment/villa/bnb/hotel
    city: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    ical_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # PMS calendar integration

    # Status
    status: Mapped[str] = mapped_column(String(32), default="active", index=True)  # active/inactive
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    # Relationships
    config: Mapped[Optional["PropertyConfig"]] = relationship("PropertyConfig", back_populates="property", uselist=False)
    escalated_messages: Mapped[list["EscalatedMessage"]] = relationship("EscalatedMessage", back_populates="property")
    message_logs: Mapped[list["MessageLog"]] = relationship("MessageLog", back_populates="property")
    tenant: Mapped["Tenant"] = relationship("Tenant", overlaps="properties")


# ---------------------------------------------------------------------------
# PropertyConfig — per-property settings (voice AI, amenities, rules, etc.)
# ---------------------------------------------------------------------------

class PropertyConfig(Base):
    __tablename__ = "property_configs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    property_id: Mapped[str] = mapped_column(String(36), ForeignKey("properties.id"), unique=True, nullable=False, index=True)
    tenant_id: Mapped[str] = mapped_column(String(36), ForeignKey("tenants.id"), index=True, nullable=False)

    # ========== VOICE AI SETTINGS (Per Property) ==========
    voice_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    voice_phone_number: Mapped[Optional[str]] = mapped_column(String(32), nullable=True, unique=True)  # Unique per property

    # Twilio credentials (encrypted)
    voice_twilio_account_sid: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    voice_twilio_auth_token_enc: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # encrypted
    voice_twilio_from_number: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)

    # ElevenLabs voice selection
    voice_elevenlabs_voice_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, default="EXAVITQu4vr4xnSDxMaL")

    # Call forwarding
    voice_forward_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    voice_forward_number: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)  # Host's phone for this property

    # ========== VOICE ROUTING STRATEGY ==========
    voice_routing_mode: Mapped[str] = mapped_column(String(32), default="per_property")  # per_property | shared_routing | mixed
    shared_voice_number: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)  # For shared routing mode
    voice_routing_webhook_param: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)  # How to identify property (e.g., "property_key", "ivr_digit")

    # ========== GENERAL SETTINGS ==========
    amenities: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # comma-separated or newline-separated
    house_rules: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    check_in_time: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)  # e.g. "15:00"
    check_out_time: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)  # e.g. "11:00"
    max_guests: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    faq: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # free-form Q&A
    food_menu: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    nearby_restaurants: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    parking_policy: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    pet_policy: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    wifi_password: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    wifi_network_name: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)

    # ========== METADATA ==========
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)

    # Relationships
    property: Mapped["Property"] = relationship("Property", back_populates="config")
    tenant: Mapped["Tenant"] = relationship("Tenant")


# ---------------------------------------------------------------------------
# EscalatedMessage — messages needing host attention per property
# ---------------------------------------------------------------------------

class EscalatedMessage(Base):
    __tablename__ = "escalated_messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    property_id: Mapped[str] = mapped_column(String(36), ForeignKey("properties.id"), index=True, nullable=False)
    tenant_id: Mapped[str] = mapped_column(String(36), ForeignKey("tenants.id"), index=True, nullable=False)

    # Message details
    guest_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True, index=True)
    guest_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    message_type: Mapped[str] = mapped_column(String(32), index=True)  # email | whatsapp | voice | sms
    content: Mapped[Text] = mapped_column(Text)

    # Escalation details
    reason: Mapped[str] = mapped_column(String(64), index=True)  # ai_low_confidence | keyword_escalation | guest_request | voice_unclear | pattern_detected
    priority: Mapped[str] = mapped_column(String(16), default="medium", index=True)  # critical | high | medium | low
    confidence_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # 0.0-1.0

    # Status tracking
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)  # pending | in_progress | resolved | delegated
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    # Resolution
    host_response: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    assigned_to: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("team_members.id"), nullable=True, index=True)  # TeamMember ID

    # Source tracking
    source_message_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True, index=True)  # Reference to original message

    # Relationships
    property: Mapped["Property"] = relationship("Property", back_populates="escalated_messages")
    tenant: Mapped["Tenant"] = relationship("Tenant")
    assigned_to_member: Mapped[Optional["TeamMember"]] = relationship("TeamMember", foreign_keys=[assigned_to])


# ---------------------------------------------------------------------------
# MessageLog — log of all messages per property (for analytics/history)
# ---------------------------------------------------------------------------

class MessageLog(Base):
    __tablename__ = "message_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    property_id: Mapped[str] = mapped_column(String(36), ForeignKey("properties.id"), index=True, nullable=False)
    tenant_id: Mapped[str] = mapped_column(String(36), ForeignKey("tenants.id"), index=True, nullable=False)

    # Message identification
    guest_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True, index=True)
    guest_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    thread_key: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)  # conversation thread ID

    # Message details
    direction: Mapped[str] = mapped_column(String(16), index=True)  # inbound | outbound
    channel: Mapped[str] = mapped_column(String(32), index=True)  # email | whatsapp | voice | sms
    content: Mapped[Text] = mapped_column(Text)

    # AI processing
    ai_handled: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    ai_response: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    confidence_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # 0.0-1.0

    # Escalation tracking
    escalated: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    escalated_message_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("escalated_messages.id"), nullable=True, index=True)

    # Status
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)  # pending | ai_response | escalated | resolved
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)

    # Relationships
    property: Mapped["Property"] = relationship("Property", back_populates="message_logs")
    tenant: Mapped["Tenant"] = relationship("Tenant")


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
    detected_language: Mapped[Optional[str]] = mapped_column(String(8), nullable=True)

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

    # Upsell send-state flags (prevent duplicate sends per trigger point)
    upsell_booking_sent:    Mapped[bool] = mapped_column(Boolean, default=False)
    upsell_pre_arrival_sent: Mapped[bool] = mapped_column(Boolean, default=False)
    upsell_mid_stay_sent:   Mapped[bool] = mapped_column(Boolean, default=False)

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

    # Guest satisfaction pulse (1-5 rating sent after checkout)
    satisfaction_sent_at:   Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    satisfaction_score:     Mapped[Optional[int]]      = mapped_column(Integer, nullable=True)
    satisfaction_scored_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    # Detected language code (e.g. "es", "fr", "de") from first guest message
    language_code: Mapped[Optional[str]] = mapped_column(String(8), nullable=True)

    # CRM notes — host notes about this guest across stays (JSON list)
    crm_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

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

    # ========== DELEGATION & SKILLS ==========
    expertise_areas: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # Comma-separated: maintenance, billing, guest_relations, voice_support
    max_concurrent_tasks: Mapped[int] = mapped_column(Integer, default=10)  # Workload limit
    is_available_for_assignment: Mapped[bool] = mapped_column(Boolean, default=True, index=True)

    created_at:  Mapped[datetime]      = mapped_column(DateTime(timezone=True), default=_now)
    updated_at:  Mapped[datetime]      = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)

    tenant: Mapped["Tenant"] = relationship("Tenant", back_populates="team_members")
    created_batches: Mapped[list["ReservationIntakeBatch"]] = relationship("ReservationIntakeBatch", back_populates="created_by_member")
    assigned_issues: Mapped[list["IssueTicket"]] = relationship("IssueTicket", back_populates="assigned_to_member", foreign_keys="IssueTicket.assigned_to_member_id")
    created_issues: Mapped[list["IssueTicket"]] = relationship("IssueTicket", back_populates="created_by_member", foreign_keys="IssueTicket.created_by_member_id")
    created_timeline_events: Mapped[list["GuestTimelineEvent"]] = relationship("GuestTimelineEvent", back_populates="created_by_member")
    created_activations: Mapped[list["ArrivalActivation"]] = relationship("ArrivalActivation", back_populates="created_by_member")


# ---------------------------------------------------------------------------
# TeamMemberWorkload — track task assignments and workload for team members
# ---------------------------------------------------------------------------

class TeamMemberWorkload(Base):
    __tablename__ = "team_member_workloads"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    team_member_id: Mapped[int] = mapped_column(Integer, ForeignKey("team_members.id"), index=True, nullable=False)
    tenant_id: Mapped[str] = mapped_column(String(36), ForeignKey("tenants.id"), index=True, nullable=False)

    # Task reference
    escalated_message_id: Mapped[str] = mapped_column(String(36), ForeignKey("escalated_messages.id"), index=True, nullable=False)
    property_id: Mapped[str] = mapped_column(String(36), ForeignKey("properties.id"), index=True, nullable=False)

    # Status
    status: Mapped[str] = mapped_column(String(32), default="assigned", index=True)  # assigned | in_progress | completed | cancelled
    assigned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    # Notes & resolution
    internal_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    resolution_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Relationships
    team_member: Mapped["TeamMember"] = relationship("TeamMember")
    escalated_message: Mapped["EscalatedMessage"] = relationship("EscalatedMessage")
    property: Mapped["Property"] = relationship("Property")
    tenant: Mapped["Tenant"] = relationship("Tenant")


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
    reservation_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("reservations.id"), nullable=True, index=True)

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

    # Guest contact info
    guest_email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    guest_language: Mapped[str] = mapped_column(String(16), default="en")

    # Post-call feedback
    guest_rating: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # 1-5
    quality_score: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # 0-100

    # Callback scheduling
    callback_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    callback_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    # Recording consent (GDPR/CCPA compliance)
    recording_consent_given: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)  # None = not asked, True/False = asked
    recording_consent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    tenant: Mapped["Tenant"] = relationship("Tenant", back_populates="voice_calls")
    guest_contact: Mapped[Optional["GuestContact"]] = relationship("GuestContact", back_populates="voice_calls")
    reservation: Mapped[Optional["Reservation"]] = relationship("Reservation")
    knowledge_gaps: Mapped[list["VoiceKnowledgeGap"]] = relationship("VoiceKnowledgeGap", back_populates="call", cascade="all, delete-orphan")


# ---------------------------------------------------------------------------
# VoiceKnowledgeGap — questions the AI couldn't answer; host can fill in
# ---------------------------------------------------------------------------

class VoiceKnowledgeGap(Base):
    __tablename__ = "voice_knowledge_gaps"

    id:          Mapped[str]           = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id:   Mapped[str]           = mapped_column(String(36), ForeignKey("tenants.id"), index=True)
    call_id:     Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("voice_calls.id"), nullable=True, index=True)
    issue_ticket_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("issue_tickets.id"), nullable=True)

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

    alerted_at:   Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    escalated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at:   Mapped[datetime]           = mapped_column(DateTime(timezone=True), default=_now, index=True)
    resolved_at:  Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    call:   Mapped[Optional["VoiceCall"]] = relationship("VoiceCall", back_populates="knowledge_gaps")
    tenant: Mapped["Tenant"]              = relationship("Tenant")
    issue_ticket: Mapped[Optional["IssueTicket"]] = relationship("IssueTicket")


# ---------------------------------------------------------------------------
# IdempotencyKey — prevent duplicate webhook processing
# ---------------------------------------------------------------------------

class IdempotencyKey(Base):
    __tablename__ = "idempotency_keys"

    key_id: Mapped[str]           = mapped_column(String(128), primary_key=True)
    tenant_id: Mapped[str]        = mapped_column(String(36), ForeignKey("tenants.id"), index=True)
    idempotency_key: Mapped[str]  = mapped_column(String(128), unique=True, index=True)
    operation: Mapped[str]        = mapped_column(String(128), index=True)  # voice.incoming_call, etc.

    result_status: Mapped[str]    = mapped_column(String(32), default="pending")  # pending, success, error
    result_data: Mapped[Optional[dict]]   = mapped_column(JSON, nullable=True)
    result_error: Mapped[Optional[str]]   = mapped_column(String(512), nullable=True)

    created_at: Mapped[datetime]  = mapped_column(DateTime(timezone=True), default=_now, index=True)
    expires_at: Mapped[datetime]  = mapped_column(DateTime(timezone=True), index=True)


# ---------------------------------------------------------------------------
# TenantRateLimit — per-tenant rate limit configuration
# ---------------------------------------------------------------------------

class TenantRateLimit(Base):
    __tablename__ = "tenant_rate_limits"

    tenant_id: Mapped[str]        = mapped_column(String(36), ForeignKey("tenants.id"), primary_key=True)
    voice_calls_per_hour: Mapped[int]      = mapped_column(Integer, default=100)
    external_api_calls_per_hour: Mapped[int] = mapped_column(Integer, default=500)
    max_daily_cost_usd: Mapped[int]        = mapped_column(Integer, default=50)

    created_at: Mapped[datetime]  = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime]  = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


# ---------------------------------------------------------------------------
# RateLimitCounter — track usage within current window
# ---------------------------------------------------------------------------

class RateLimitCounter(Base):
    __tablename__ = "rate_limit_counters"

    counter_id: Mapped[str]       = mapped_column(String(128), primary_key=True)
    tenant_id: Mapped[str]        = mapped_column(String(36), ForeignKey("tenants.id"), index=True)
    metric: Mapped[str]           = mapped_column(String(32), index=True)  # voice_calls, external_api, daily_cost
    count: Mapped[int]            = mapped_column(Integer, default=0)

    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime]  = mapped_column(DateTime(timezone=True), index=True)


# ---------------------------------------------------------------------------
# APIUsageLog — track API usage and costs per tenant
# ---------------------------------------------------------------------------

class APIUsageLog(Base):
    __tablename__ = "api_usage_logs"

    id: Mapped[str]               = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str]        = mapped_column(String(36), ForeignKey("tenants.id"), index=True)
    call_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("voice_calls.id"), nullable=True, index=True)

    service: Mapped[str]          = mapped_column(String(32), index=True)  # deepgram, openai, elevenlabs, twilio
    operation: Mapped[str]        = mapped_column(String(64))  # transcribe, generate_response, synthesize, etc.

    # Usage metrics
    input_tokens: Mapped[Optional[int]]   = mapped_column(Integer, nullable=True)  # For LLM
    output_tokens: Mapped[Optional[int]]  = mapped_column(Integer, nullable=True)  # For LLM
    duration_seconds: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # For speech/TTS
    characters: Mapped[Optional[int]]     = mapped_column(Integer, nullable=True)  # For TTS

    # Cost
    cost_usd: Mapped[float]       = mapped_column(Float, default=0.0)  # Actual cost in USD

    status: Mapped[str]           = mapped_column(String(16), default="success")  # success, error
    error_message: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)

    created_at: Mapped[datetime]  = mapped_column(DateTime(timezone=True), default=_now, index=True)

    voice_call: Mapped[Optional["VoiceCall"]] = relationship("VoiceCall")
    tenant: Mapped["Tenant"] = relationship("Tenant")


# ---------------------------------------------------------------------------
# FeatureFlag — feature flags for safe canary deployments
# ---------------------------------------------------------------------------

class FeatureFlag(Base):
    __tablename__ = "feature_flags"

    flag_name: Mapped[str]        = mapped_column(String(128), primary_key=True)
    enabled: Mapped[bool]         = mapped_column(Boolean, default=False)
    rollout_percentage: Mapped[int] = mapped_column(Integer, default=0)  # 0-100

    description: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime]  = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime]  = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class FeatureFlagOverride(Base):
    __tablename__ = "feature_flag_overrides"

    id: Mapped[str]               = mapped_column(String(128), primary_key=True)
    flag_name: Mapped[str]        = mapped_column(String(128), index=True)
    tenant_id: Mapped[str]        = mapped_column(String(36), index=True)
    enabled: Mapped[bool]         = mapped_column(Boolean)
    created_at: Mapped[datetime]  = mapped_column(DateTime(timezone=True), default=_now)

# ---------------------------------------------------------------------------
# VoiceRoutingConfig — configures call routing behavior per tenant
# ---------------------------------------------------------------------------

class VoiceRoutingConfig(Base):
    __tablename__ = "voice_routing_configs"

    tenant_id: Mapped[str] = mapped_column(String(36), ForeignKey("tenants.id"), primary_key=True)
    default_route: Mapped[str] = mapped_column(String(32), default="ai")  # ai, voicemail, host
    fallback_sms: Mapped[bool] = mapped_column(Boolean, default=True)
    dead_air_timeout: Mapped[int] = mapped_column(Integer, default=30)
    queue_hold_music: Mapped[bool] = mapped_column(Boolean, default=False)
    host_routing_number: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)

    tenant: Mapped["Tenant"] = relationship("Tenant", back_populates="voice_routing_config")

# ---------------------------------------------------------------------------
# RoutingRule — specific condition-based routes (e.g., negative sentiment -> escalate)
# ---------------------------------------------------------------------------

class RoutingRule(Base):
    __tablename__ = "routing_rules"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(36), ForeignKey("tenants.id"), index=True)
    name: Mapped[str] = mapped_column(String(128))
    priority: Mapped[int] = mapped_column(Integer, default=100)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    # Condition
    condition_type: Mapped[str] = mapped_column(String(32))  # sentiment, vip, repeat_caller, time_of_day
    condition_value: Mapped[str] = mapped_column(String(128))

    # Action
    action: Mapped[str] = mapped_column(String(32))  # escalate, direct_to_voicemail
    action_target: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)

    tenant: Mapped["Tenant"] = relationship("Tenant", back_populates="routing_rules")


# ---------------------------------------------------------------------------
# AutomatedMessage — host-configured automated message rules (pre/post stay, etc.)
# ---------------------------------------------------------------------------

class AutomatedMessage(Base):
    __tablename__ = "automated_messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(36), ForeignKey("tenants.id"), nullable=False, index=True)
    property_name: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)  # None = all properties

    # Trigger: pre_arrival | post_checkout | mid_stay | custom
    trigger: Mapped[str] = mapped_column(String(32), nullable=False, index=True)

    # Channel: whatsapp | sms | email
    channel: Mapped[str] = mapped_column(String(32), nullable=False, server_default="whatsapp", default="whatsapp")

    # Message body (supports Jinja-like placeholders like {{guest_name}})
    message_template: Mapped[str] = mapped_column(Text, nullable=False)

    # Controls
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true", default=True)
    send_hour: Mapped[int] = mapped_column(Integer, nullable=False, server_default="9", default=9)  # 24h UTC
    last_run_date: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)  # "YYYY-MM-DD" dedup key

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    tenant: Mapped["Tenant"] = relationship("Tenant")


# ---------------------------------------------------------------------------
# GuestFeedback — post-stay star rating + comment submitted via token link
# ---------------------------------------------------------------------------

class GuestFeedback(Base):
    __tablename__ = "guest_feedback"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(36), ForeignKey("tenants.id"), nullable=False, index=True)
    reservation_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("reservations.id"), nullable=True, index=True)

    # Public token used in the feedback URL  (e.g. /feedback/{token})
    feedback_token: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)

    # Guest info (copied at send time for privacy)
    guest_name: Mapped[str] = mapped_column(String(128), nullable=False)
    guest_phone: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    property_name: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)

    # Submitted values (null until the guest actually submits)
    rating: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # 1-5
    comment: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    submitted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    tenant: Mapped["Tenant"] = relationship("Tenant")
    reservation: Mapped[Optional["Reservation"]] = relationship("Reservation")


# ---------------------------------------------------------------------------
# EscalationRule — rules for escalating messages per property
# ---------------------------------------------------------------------------

class EscalationRule(Base):
    __tablename__ = "escalation_rules"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    property_id: Mapped[str] = mapped_column(String(36), ForeignKey("properties.id"), index=True, nullable=False)
    tenant_id: Mapped[str] = mapped_column(String(36), ForeignKey("tenants.id"), index=True, nullable=False)

    # Rule metadata
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    priority: Mapped[int] = mapped_column(Integer, default=100)  # Higher = applied first
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)

    # ========== CONDITION ==========
    condition_type: Mapped[str] = mapped_column(String(32), index=True)  # confidence_below | keyword_detected | voice_unclear | repeat_question | time_based | pattern_detected

    # Condition parameters (JSON for flexibility)
    confidence_threshold: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # For confidence_below: escalate if confidence < this value
    keywords: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # Comma-separated keywords for keyword_detected (e.g., "emergency,police,help")
    min_repeat_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # For pattern_detected: escalate if same issue appears N times
    time_window_minutes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # For time_based: check within last N minutes
    channels: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)  # Apply to specific channels: email,whatsapp,sms,voice (comma-separated, or empty for all)

    # ========== ACTION ==========
    action: Mapped[str] = mapped_column(String(32))  # escalate | notify_host | assign_team_member
    escalation_priority: Mapped[str] = mapped_column(String(16), default="high")  # critical | high | medium | low
    assign_to_team_member: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("team_members.id"), nullable=True)  # Optionally auto-assign

    # ========== METADATA ==========
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)

    # Relationships
    property: Mapped["Property"] = relationship("Property")
    tenant: Mapped["Tenant"] = relationship("Tenant")
    team_member: Mapped[Optional["TeamMember"]] = relationship("TeamMember", foreign_keys=[assign_to_team_member])



# ---------------------------------------------------------------------------
# QuickReply — host-defined canned responses for fast one-tap sending
# ---------------------------------------------------------------------------

class QuickReply(Base):
    __tablename__ = "quick_replies"

    id:               Mapped[int]           = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id:        Mapped[str]           = mapped_column(String(36), ForeignKey("tenants.id"), index=True)
    label:            Mapped[str]           = mapped_column(String(128))          # e.g. "WiFi Password"
    message_template: Mapped[str]           = mapped_column(Text)                 # e.g. "Network: MyWifi | Password: abc123"
    sort_order:       Mapped[int]           = mapped_column(Integer, default=0)
    is_active:        Mapped[bool]          = mapped_column(Boolean, default=True)
    created_at:       Mapped[datetime]      = mapped_column(DateTime(timezone=True), default=_now)

    tenant: Mapped["Tenant"] = relationship("Tenant")


# ---------------------------------------------------------------------------
# UpsellOffer — host-configured upsell opportunities (early check-in, etc.)
# ---------------------------------------------------------------------------

class UpsellOffer(Base):
    """Unified upsell offer — supports both keyword-triggered and time-triggered modes."""
    __tablename__ = "upsell_offers"

    id:               Mapped[int]           = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id:        Mapped[str]           = mapped_column(String(36), ForeignKey("tenants.id"), index=True)
    # Original fields (keyword-triggered mode)
    offer_type:       Mapped[Optional[str]] = mapped_column(String(32), nullable=True, index=True)
    title:            Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    price_str:        Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    trigger_keywords: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    message_template: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    accepted_count:   Mapped[int]           = mapped_column(Integer, default=0)
    total_revenue:    Mapped[float]         = mapped_column(Float, default=0.0)
    # Extended fields (time-triggered mode)
    name:             Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    description:      Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    price_usd:        Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    trigger_point:    Mapped[Optional[str]] = mapped_column(String(32), nullable=True, index=True)
    trigger_days_before: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    sort_order:       Mapped[int]           = mapped_column(Integer, default=100)
    is_active:        Mapped[bool]          = mapped_column(Boolean, default=True, index=True)
    created_at:       Mapped[datetime]      = mapped_column(DateTime(timezone=True), default=_now)
    updated_at:       Mapped[datetime]      = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)

    tenant: Mapped["Tenant"] = relationship("Tenant", back_populates="upsell_offers")
    sends:  Mapped[list["UpsellSend"]] = relationship("UpsellSend", back_populates="offer")


# ---------------------------------------------------------------------------
# UpsellSend — tracks which offers were sent to which reservation and outcome
# ---------------------------------------------------------------------------

class UpsellSend(Base):
    __tablename__ = "upsell_sends"
    __table_args__ = (
        UniqueConstraint("offer_id", "reservation_id", name="uq_upsell_send"),
    )

    id:              Mapped[int]           = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id:       Mapped[str]           = mapped_column(String(36), ForeignKey("tenants.id"), index=True)
    offer_id:        Mapped[int]           = mapped_column(Integer, ForeignKey("upsell_offers.id"), index=True)
    reservation_id:  Mapped[int]           = mapped_column(Integer, ForeignKey("reservations.id"), index=True)
    draft_id:        Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    channel:         Mapped[str]           = mapped_column(String(32))
    status:          Mapped[str]           = mapped_column(String(16), default="sent", index=True)  # sent | accepted | declined
    guest_response_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    accepted_at:     Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    sent_at:         Mapped[datetime]      = mapped_column(DateTime(timezone=True), default=_now, index=True)

    offer:       Mapped["UpsellOffer"]  = relationship("UpsellOffer", back_populates="sends")
    reservation: Mapped["Reservation"]  = relationship("Reservation")


# ---------------------------------------------------------------------------
# SalesConfig — per-tenant Sales AI agent settings
# ---------------------------------------------------------------------------

class SalesConfig(Base):
    __tablename__ = "sales_configs"

    id:               Mapped[int]           = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id:        Mapped[str]           = mapped_column(String(36), ForeignKey("tenants.id"), unique=True, index=True)
    is_enabled:       Mapped[bool]          = mapped_column(Boolean, default=False)
    ai_persona_name:  Mapped[str]           = mapped_column(String(128), default="Your Host")
    pricing_note:     Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    booking_link:     Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    max_conv_turns:   Mapped[int]           = mapped_column(Integer, default=10)
    created_at:       Mapped[datetime]      = mapped_column(DateTime(timezone=True), default=_now)
    updated_at:       Mapped[datetime]      = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)

    tenant: Mapped["Tenant"] = relationship("Tenant", back_populates="sales_config")


# ---------------------------------------------------------------------------
# SalesConversation — pre-booking inquiry thread (kept separate from post-booking drafts)
# ---------------------------------------------------------------------------

class SalesConversation(Base):
    __tablename__ = "sales_conversations"

    id:               Mapped[str]           = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id:        Mapped[str]           = mapped_column(String(36), ForeignKey("tenants.id"), index=True)
    channel:          Mapped[str]           = mapped_column(String(16))
    lead_phone:       Mapped[Optional[str]] = mapped_column(String(32), nullable=True, index=True)
    lead_email:       Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    lead_name:        Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    status:           Mapped[str]           = mapped_column(String(24), default="open", index=True)  # open | closed_booked | closed_dropped | closed_maxturns
    detected_language: Mapped[str]          = mapped_column(String(8), default="en")
    turn_count:       Mapped[int]           = mapped_column(Integer, default=0)
    booking_sent_at:  Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_at:        Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at:       Mapped[datetime]      = mapped_column(DateTime(timezone=True), default=_now, index=True)
    updated_at:       Mapped[datetime]      = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)

    tenant:   Mapped["Tenant"]             = relationship("Tenant", back_populates="sales_conversations")
    messages: Mapped[list["SalesMessage"]] = relationship("SalesMessage", back_populates="conversation", order_by="SalesMessage.created_at")


# ---------------------------------------------------------------------------
# SalesMessage — individual turn in a sales conversation
# ---------------------------------------------------------------------------

class SalesMessage(Base):
    __tablename__ = "sales_messages"

    id:              Mapped[int]      = mapped_column(Integer, primary_key=True, autoincrement=True)
    conversation_id: Mapped[str]      = mapped_column(String(36), ForeignKey("sales_conversations.id"), index=True)
    tenant_id:       Mapped[str]      = mapped_column(String(36), ForeignKey("tenants.id"), index=True)
    direction:       Mapped[str]      = mapped_column(String(8))   # inbound | outbound
    body:            Mapped[str]      = mapped_column(Text)
    channel:         Mapped[str]      = mapped_column(String(16))
    created_at:      Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)

    conversation: Mapped["SalesConversation"] = relationship("SalesConversation", back_populates="messages")
