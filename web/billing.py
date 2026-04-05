# © 2024 Jestin Rajan. All rights reserved.
"""
Stripe billing integration.

Plans map directly to messaging channels a host can use:
  free        — web dashboard + email/iCal drafts only (no messaging channels)
  baileys     — local Baileys bot on host's own PC (WhatsApp via home IP)
  meta_cloud  — Meta WhatsApp Cloud API running on our server
  sms         — Twilio SMS running on our server
  pro         — all three channels (baileys + meta_cloud + sms)

Subscription enforcement:
  - subscription_status must be 'active' or 'trialing'
  - subscription_expires_at is informational (Stripe manages actual expiry via webhooks)
  - require_plan() decorator returns 402 if plan check fails
"""

import hashlib
import logging
import os
import secrets
from datetime import datetime, timezone
from typing import Callable

import stripe
from fastapi import HTTPException, Request
from sqlalchemy.orm import Session

from web.models import TenantConfig, PlanConfig, PLAN_FREE, PLAN_BAILEYS, PLAN_META_CLOUD, PLAN_SMS, PLAN_PRO

log = logging.getLogger(__name__)

stripe.api_key = os.getenv("STRIPE_SECRET_KEY", "")

# ---------------------------------------------------------------------------
# Stripe price IDs — set these in .env after creating products in Stripe dashboard
# ---------------------------------------------------------------------------
PRICE_IDS: dict[str, str] = {
    PLAN_BAILEYS:    os.getenv("STRIPE_PRICE_BAILEYS",    ""),
    PLAN_META_CLOUD: os.getenv("STRIPE_PRICE_META_CLOUD", ""),
    PLAN_SMS:        os.getenv("STRIPE_PRICE_SMS",        ""),
    PLAN_PRO:        os.getenv("STRIPE_PRICE_PRO",        ""),
}

# Plan metadata shown in UI
PLAN_INFO: dict[str, dict] = {
    PLAN_FREE: {
        "name":  "Free",
        "price": "$0/mo",
        "features": [
            "Web dashboard",
            "AI email drafts",
            "iCal calendar sync",
            "Unlimited properties",
        ],
        "channels": [],
    },
    PLAN_BAILEYS: {
        "name":  "Baileys (Local Bot)",
        "price": "$19/mo",
        "features": [
            "Everything in Free",
            "WhatsApp via your own PC",
            "Zero ban risk (your home IP)",
            "One-click bot installer",
            "Guest + vendor WhatsApp flows",
        ],
        "channels": ["baileys"],
    },
    PLAN_META_CLOUD: {
        "name":  "Meta Cloud API",
        "price": "$29/mo",
        "features": [
            "Everything in Free",
            "WhatsApp Business Cloud API",
            "No hardware needed",
            "Scales to 10,000+ msgs/day",
            "Official Meta support",
        ],
        "channels": ["meta_cloud"],
    },
    PLAN_SMS: {
        "name":  "SMS (Twilio)",
        "price": "$19/mo",
        "features": [
            "Everything in Free",
            "SMS via Twilio",
            "Reach guests without WhatsApp",
            "Host alerts via SMS",
            "Bring your own Twilio number",
        ],
        "channels": ["sms"],
    },
    PLAN_PRO: {
        "name":  "Pro (All Channels)",
        "price": "$49/mo",
        "features": [
            "Everything in all plans",
            "Baileys + Meta Cloud + SMS",
            "Priority support",
            "Best value",
        ],
        "channels": ["baileys", "meta_cloud", "sms"],
    },
}

# Voice AI Add-On Pricing (Premium Feature)
# Ensures voice calling is profitable with 9.6x+ markup on costs
VOICE_ADD_ON_PRICING: dict[str, dict] = {
    "light": {
        "name": "Voice Light",
        "price": "$39/month",
        "minutes_included": 100,
        "overage_per_minute": 0.049,  # $0.049/min for overages (170% markup on $0.018 cost)
        "features": [
            "100 AI voice calls/month",
            "Deepgram speech-to-text",
            "OpenAI GPT-4o responses",
            "ElevenLabs text-to-speech",
            "Call recording & storage",
            "Sentiment detection",
        ],
        "best_for": "Testing, small properties (1-3 units)",
        "cost_basis": 1.80,  # Actual API cost (100 mins × $0.018)
        "markup": 21.7,  # Revenue/cost ratio
    },
    "standard": {
        "name": "Voice Standard",
        "price": "$79/month",
        "minutes_included": 300,
        "overage_per_minute": 0.049,
        "surge_threshold": 0.5,  # Apply 15% surge if >50% over limit
        "surge_multiplier": 1.15,
        "features": [
            "300 AI voice calls/month",
            "Smart call routing (first-time vs repeat guests)",
            "Call analytics & insights",
            "Guest sentiment tracking",
            "Deepgram + OpenAI + ElevenLabs included",
            "Call recording & storage",
            "Priority call handling",
        ],
        "best_for": "Growing hosts (6-12 units)",
        "cost_basis": 5.40,
        "markup": 14.6,
    },
    "professional": {
        "name": "Voice Professional",
        "price": "$129/month",
        "minutes_included": 750,
        "overage_per_minute": 0.049,
        "surge_threshold": 0.5,
        "surge_multiplier": 1.15,
        "features": [
            "750 AI voice calls/month",
            "Everything in Standard PLUS:",
            "Advanced voice routing by guest history",
            "Real-time sentiment alerts",
            "Automatic escalation to humans",
            "Post-call summary via SMS",
            "Call quality analytics",
            "White-glove onboarding",
        ],
        "best_for": "Large hosts (15-25 units)",
        "cost_basis": 13.50,
        "markup": 9.6,
    },
    "unlimited": {
        "name": "Voice Unlimited",
        "price": "$199/month",
        "minutes_included": None,  # Unlimited
        "overage_per_minute": 0.0,  # No overages, everything included
        "features": [
            "UNLIMITED AI voice calls",
            "Everything in Professional PLUS:",
            "Dedicated support channel (Slack)",
            "Custom voice prompts & training",
            "Advanced analytics & reporting",
            "API access for integrations",
            "SLA: 99.9% uptime guarantee",
            "Priority engineering support",
        ],
        "best_for": "Enterprise hosts (25+ units)",
        "cost_basis": 36.0,  # Estimate for 2000 mins
        "markup": 5.5,
    },
}

_ENVIRONMENT = os.getenv("ENVIRONMENT", "production").lower()
_ALLOW_INSECURE_DEFAULTS = _ENVIRONMENT in {"development", "dev", "test"}

WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")
if not WEBHOOK_SECRET and not _ALLOW_INSECURE_DEFAULTS:
    raise RuntimeError(
        "STRIPE_WEBHOOK_SECRET must be set in production. "
        "Copy it from your Stripe dashboard under Webhooks → your endpoint → Signing secret. "
        "Without it every incoming Stripe webhook will be rejected with HTTP 400."
    )

# ---------------------------------------------------------------------------
# Subscription helpers
# ---------------------------------------------------------------------------

ACTIVE_STATUSES = {"active", "trialing"}


def is_plan_active(cfg: TenantConfig) -> bool:
    return cfg.subscription_status in ACTIVE_STATUSES


def tenant_has_channel(cfg: TenantConfig, channel: str) -> bool:
    """Return True if the tenant's active plan includes the given channel."""
    # WhatsApp (Meta Cloud) and SMS (Twilio) are now available on all plans.
    # We use literal strings here to avoid a circular dependency with web.models.
    if channel in ("meta_cloud", "sms"):
        return True

    if not is_plan_active(cfg):
        return False
    plan = cfg.subscription_plan or PLAN_FREE
    if plan == PLAN_PRO:
        return True
    return channel == plan


def require_channel(cfg: TenantConfig, channel: str) -> None:
    """Raise HTTP 402 if tenant does not have access to the given channel."""
    if not tenant_has_channel(cfg, channel):
        raise HTTPException(
            status_code=402,
            detail=f"Your current plan does not include the '{channel}' channel. "
                   f"Upgrade at /billing"
        )


# ---------------------------------------------------------------------------
# Voice AI Add-On Pricing Helpers
# ---------------------------------------------------------------------------

def calculate_voice_overage(voice_tier: str, actual_minutes: int) -> dict:
    """
    Calculate voice call overage charges for a given tier and actual usage.

    Returns dict with:
      - minutes_included: How many minutes are free in this tier
      - actual_minutes: Actual usage
      - overage_minutes: Actual - included (max 0)
      - overage_cost: overage_minutes × overage_rate (with surge if applicable)
      - total_monthly_cost: Base price of tier (overage not included in base)
    """
    if voice_tier == "unlimited" or voice_tier not in VOICE_ADD_ON_PRICING:
        return {
            "tier": voice_tier,
            "minutes_included": None,
            "actual_minutes": actual_minutes,
            "overage_minutes": 0,
            "overage_cost": 0.0,
            "total_monthly_cost": VOICE_ADD_ON_PRICING.get("unlimited", {}).get("price", "$199"),
            "status": "unlimited"
        }

    tier_info = VOICE_ADD_ON_PRICING[voice_tier]
    included = tier_info["minutes_included"]
    overage_rate = tier_info["overage_per_minute"]

    overage_mins = max(0, actual_minutes - included)

    # Apply surge pricing if usage exceeds threshold
    overage_cost = 0.0
    surge_applied = False
    if overage_mins > 0 and "surge_threshold" in tier_info:
        usage_pct = actual_minutes / included
        if usage_pct > tier_info["surge_threshold"]:
            overage_cost = overage_mins * overage_rate * tier_info["surge_multiplier"]
            surge_applied = True
        else:
            overage_cost = overage_mins * overage_rate
    else:
        overage_cost = overage_mins * overage_rate

    return {
        "tier": voice_tier,
        "tier_name": tier_info["name"],
        "minutes_included": included,
        "actual_minutes": actual_minutes,
        "overage_minutes": overage_mins,
        "overage_rate": overage_rate,
        "surge_applied": surge_applied,
        "overage_cost": round(overage_cost, 2),
        "base_price": tier_info["price"],
        "total_monthly_cost": tier_info["price"],  # Plus overage_cost
    }


def require_voice_add_on(cfg: TenantConfig, voice_tier: str) -> None:
    """Raise HTTP 402 if tenant does not have voice add-on for the given tier."""
    if not cfg.voice_add_on_enabled:
        raise HTTPException(
            status_code=402,
            detail=f"Your plan does not include voice calling. "
                   f"Add voice to your account at /billing/voice"
        )
    if cfg.voice_tier and cfg.voice_tier != voice_tier:
        raise HTTPException(
            status_code=402,
            detail=f"Your current voice tier is '{cfg.voice_tier}'. "
                   f"This feature requires '{voice_tier}' or higher."
        )


# ---------------------------------------------------------------------------
# Stripe checkout
# ---------------------------------------------------------------------------

def create_checkout_session(tenant_id: str, plan_key: str, num_units: int = 1,
                            success_url: str = "", cancel_url: str = "",
                            customer_id: str | None = None, db: Session | None = None) -> str:
    """Create a Stripe Checkout Session with dynamic pricing based on units."""
    if not db:
        from web.db import SessionLocal
        db = SessionLocal()
        owns_db = True
    else:
        owns_db = False

    try:
        from web.models import PlanConfig

        plan = db.query(PlanConfig).filter_by(plan_key=plan_key, is_active=True).first()
        if not plan:
            raise HTTPException(status_code=400, detail=f"Plan '{plan_key}' not found or inactive")

        if not (plan.min_units <= num_units <= plan.max_units):
            raise HTTPException(status_code=400,
                detail=f"Plan '{plan.display_name}' requires {plan.min_units}-{plan.max_units} units")

        # Calculate total price in cents
        total_cents = int((plan.base_fee_usd + plan.per_unit_fee_usd * num_units) * 100)

        params: dict = {
            "mode":               "subscription",
            "line_items": [{
                "price_data": {
                    "currency": "usd",
                    "unit_amount": total_cents,
                    "product_data": {
                        "name": f"HostAI {plan.display_name} ({num_units} unit{'s' if num_units > 1 else ''})"
                    },
                    "recurring": {"interval": "month"},
                },
                "quantity": 1,
            }],
            "success_url":        success_url,
            "cancel_url":         cancel_url,
            "client_reference_id": tenant_id,
            "metadata":           {"tenant_id": tenant_id, "plan": plan_key, "num_units": num_units},
            "subscription_data":  {"metadata": {"tenant_id": tenant_id, "plan": plan_key, "num_units": num_units}},
        }
        if customer_id:
            params["customer"] = customer_id
        else:
            params["customer_creation"] = "always"

        session = stripe.checkout.Session.create(**params)
        return session.url
    finally:
        if owns_db:
            db.close()


def create_portal_session(customer_id: str, return_url: str) -> str:
    """Create a Stripe Customer Portal session (manage billing, cancel, etc.)."""
    session = stripe.billing_portal.Session.create(
        customer=customer_id,
        return_url=return_url,
    )
    return session.url


# ---------------------------------------------------------------------------
# Stripe webhook handler
# ---------------------------------------------------------------------------

def handle_stripe_webhook(payload: bytes, sig_header: str, db: Session) -> dict:
    """
    Verify and process a Stripe webhook event.
    Updates subscription_status / plan in DB based on event type.
    Returns {"status": "ok"} or raises HTTPException.
    """
    try:
        event = stripe.Webhook.construct_event(payload, sig_header, WEBHOOK_SECRET)
    except stripe.error.SignatureVerificationError:
        raise HTTPException(status_code=400, detail="Invalid Stripe signature")
    except Exception as exc:
        # Catch all other Stripe errors without leaking the API key in the traceback
        log.error("Stripe webhook processing error: %s", type(exc).__name__)
        raise HTTPException(status_code=400, detail="Webhook processing error")

    ev_type  = event["type"]
    event_id = event.get("id", "")

    # Idempotency: Stripe can redeliver the same event multiple times.
    # PRIMARY: DB-backed dedup (survives Redis restart). SECONDARY: Redis for speed.
    if event_id:
        from web.models import SystemConfig
        try:
            dedup_key = f"stripe_evt_{event_id}"
            existing = db.query(SystemConfig).filter_by(key=dedup_key).first()
            if existing:
                log.info("Stripe duplicate event skipped (DB dedup): %s %s", ev_type, event_id)
                return {"status": "ok", "duplicate": True}
            # Mark as processed in DB — this is atomic with the subscription update below
            db.add(SystemConfig(key=dedup_key, value="1"))
            # Don't commit yet — commit atomically with the subscription update below
        except Exception as exc:
            log.warning("DB idempotency check failed: %s — checking Redis fallback", exc)
            db.rollback()
            # Fallback to Redis
            from web.redis_client import get_redis
            r = get_redis()
            if r is not None:
                try:
                    if not r.set(f"stripe:evt:{event_id}", "1", ex=172800, nx=True):
                        log.info("Stripe duplicate event skipped (Redis dedup): %s %s", ev_type, event_id)
                        return {"status": "ok", "duplicate": True}
                except Exception as redis_exc:
                    log.warning("Redis idempotency check also failed: %s — processing anyway", redis_exc)

    log.info("Stripe event: %s %s", ev_type, event_id)

    if ev_type in (
        "checkout.session.completed",
        "customer.subscription.created",
        "customer.subscription.updated",
    ):
        _sync_subscription(event["data"]["object"], db, ev_type)

    elif ev_type in (
        "customer.subscription.deleted",
        "customer.subscription.paused",
    ):
        _deactivate_subscription(event["data"]["object"], db)

    elif ev_type == "invoice.payment_failed":
        _mark_past_due(event["data"]["object"], db)

    return {"status": "ok"}


def _get_cfg_by_customer(customer_id: str, db: Session) -> TenantConfig | None:
    return db.query(TenantConfig).filter_by(stripe_customer_id=customer_id).first()


def _get_cfg_by_tenant(tenant_id: str, db: Session) -> TenantConfig | None:
    return db.query(TenantConfig).filter_by(tenant_id=tenant_id).first()


def _sync_subscription(obj: dict, db: Session, ev_type: str):
    """Activate or update a subscription from a Stripe event object."""
    # obj may be a Subscription or a CheckoutSession — normalise
    if obj.get("object") == "checkout.session":
        tenant_id   = obj.get("client_reference_id") or obj.get("metadata", {}).get("tenant_id")
        customer_id = obj.get("customer")
        plan        = obj.get("metadata", {}).get("plan", PLAN_FREE)
        sub_id      = obj.get("subscription")
        status      = "active"
        expires_at  = None
    else:
        # Subscription object
        customer_id = obj.get("customer")
        plan        = obj.get("metadata", {}).get("plan", PLAN_FREE)
        sub_id      = obj.get("id")
        status      = obj.get("status", "active")
        tenant_id   = obj.get("metadata", {}).get("tenant_id")
        period_end  = obj.get("current_period_end")
        expires_at  = datetime.fromtimestamp(period_end, tz=timezone.utc) if period_end else None

    cfg = None
    if tenant_id:
        cfg = _get_cfg_by_tenant(tenant_id, db)
    if not cfg and customer_id:
        cfg = _get_cfg_by_customer(customer_id, db)
    if not cfg:
        log.warning("Stripe event: no tenant found for customer=%s tenant=%s", customer_id, tenant_id)
        return

    cfg.stripe_customer_id     = customer_id or cfg.stripe_customer_id
    cfg.stripe_subscription_id = sub_id      or cfg.stripe_subscription_id
    cfg.subscription_plan      = plan
    cfg.subscription_status    = status if status in ACTIVE_STATUSES else "active"
    if expires_at:
        cfg.subscription_expires_at = expires_at
    db.commit()
    log.info("Subscription activated: tenant=%s plan=%s", cfg.tenant_id, plan)


def _deactivate_subscription(obj: dict, db: Session):
    customer_id = obj.get("customer")
    cfg = _get_cfg_by_customer(customer_id, db)
    if not cfg:
        return
    cfg.subscription_status = "cancelled"
    db.commit()
    log.info("Subscription cancelled: tenant=%s", cfg.tenant_id)


def _mark_past_due(obj: dict, db: Session):
    customer_id = obj.get("customer")
    cfg = _get_cfg_by_customer(customer_id, db)
    if not cfg:
        return
    cfg.subscription_status = "past_due"
    db.commit()
    log.info("Subscription past_due: tenant=%s", cfg.tenant_id)


# ---------------------------------------------------------------------------
# Bot API token (for Baileys bot running on host's PC)
# ---------------------------------------------------------------------------

def generate_bot_token(cfg: TenantConfig, db: Session) -> str:
    """
    Generate a new opaque bot API token for the tenant, store its hash in DB.
    Returns the raw token (shown once — not stored in plaintext).
    """
    raw = secrets.token_urlsafe(32)
    cfg.bot_api_token_hash = hashlib.sha256(raw.encode()).hexdigest()
    cfg.bot_api_token_hint = raw[-4:]   # last 4 chars for display
    db.commit()
    return raw


def verify_bot_token(raw_token: str, cfg: TenantConfig) -> bool:
    """Verify a raw bot API token against the stored hash."""
    if not cfg.bot_api_token_hash or not raw_token:
        return False
    import hmac
    return hmac.compare_digest(hashlib.sha256(raw_token.encode()).hexdigest(), cfg.bot_api_token_hash)
