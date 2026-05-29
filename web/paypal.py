# © 2024 Jestin Rajan. All rights reserved.
"""
PayPal Subscriptions API integration for recurring billing.

PayPal allows hosts to subscribe once and be auto-billed monthly.
Webhook events track subscription lifecycle (created, activated, cancelled, etc.)
"""

import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from typing import Optional

import httpx
from fastapi import HTTPException
from sqlalchemy.orm import Session

from web.models import TenantConfig, PlanConfig

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# PayPal API Configuration
# ---------------------------------------------------------------------------

PAYPAL_CLIENT_ID = os.getenv("PAYPAL_CLIENT_ID", "")
PAYPAL_CLIENT_SECRET = os.getenv("PAYPAL_CLIENT_SECRET", "")
PAYPAL_WEBHOOK_ID = os.getenv("PAYPAL_WEBHOOK_ID", "")

_ENVIRONMENT = os.getenv("ENVIRONMENT", "production").lower()
_ALLOW_INSECURE_DEFAULTS = _ENVIRONMENT in {"development", "dev", "test"}

# Use sandbox in development, production otherwise
if _ENVIRONMENT in {"development", "dev", "test"}:
    PAYPAL_BASE_URL = "https://api-m.sandbox.paypal.com"
else:
    PAYPAL_BASE_URL = "https://api-m.paypal.com"

# Map plan_key to PayPal Plan ID (created in PayPal dashboard or via API)
PAYPAL_PLAN_IDS: dict[str, str] = {
    "baileys":    os.getenv("PAYPAL_PLAN_ID_BAILEYS",    ""),
    "meta_cloud": os.getenv("PAYPAL_PLAN_ID_META_CLOUD", ""),
    "sms":        os.getenv("PAYPAL_PLAN_ID_SMS",        ""),
    "pro":        os.getenv("PAYPAL_PLAN_ID_PRO",        ""),
}

if not PAYPAL_CLIENT_ID or not PAYPAL_CLIENT_SECRET:
    log.warning("PayPal credentials not configured — PayPal subscription API will be unavailable")

if not PAYPAL_WEBHOOK_ID:
    log.warning("PayPal webhook ID not configured — webhook handling will be unavailable")


# ---------------------------------------------------------------------------
# PayPal OAuth2 Token Management
# ---------------------------------------------------------------------------

_access_token_cache = {"token": "", "expires_at": 0}


def _get_access_token() -> str:
    """
    Get a valid PayPal OAuth2 access token.
    Caches token until expiry to avoid repeated requests.

    Raises:
        HTTPException: If credentials not configured or authentication fails
    """
    if not PAYPAL_CLIENT_ID or not PAYPAL_CLIENT_SECRET:
        raise HTTPException(
            status_code=500,
            detail="PayPal credentials not configured. Set PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET in .env"
        )

    now = datetime.now(timezone.utc).timestamp()
    if _access_token_cache["token"] and now < _access_token_cache["expires_at"]:
        return _access_token_cache["token"]

    url = f"{PAYPAL_BASE_URL}/v1/oauth2/token"
    auth = (PAYPAL_CLIENT_ID, PAYPAL_CLIENT_SECRET)
    payload = {"grant_type": "client_credentials"}

    try:
        resp = httpx.post(url, auth=auth, data=payload, timeout=10.0)
        resp.raise_for_status()
        data = resp.json()
        token = data.get("access_token", "")
        expires_in = data.get("expires_in", 3600)

        _access_token_cache["token"] = token
        _access_token_cache["expires_at"] = now + expires_in - 60  # Refresh 60s before expiry

        return token
    except Exception as exc:
        log.error("Failed to get PayPal access token: %s", exc)
        raise HTTPException(status_code=500, detail="PayPal authentication failed")


# ---------------------------------------------------------------------------
# PayPal Subscription API
# ---------------------------------------------------------------------------

def create_subscription(
    tenant_id: str,
    plan_key: str,
    num_units: int = 1,
    return_url: str = "",
    cancel_url: str = "",
    db: Session | None = None,
) -> str:
    """
    Create a PayPal subscription and return the approval URL.

    Args:
        tenant_id: The tenant creating the subscription
        plan_key: Plan key (starter, growth, pro)
        num_units: Number of units to bill for
        return_url: URL to redirect to after PayPal approval
        cancel_url: URL to redirect to if user cancels
        db: Database session

    Returns:
        Approval URL to redirect user to PayPal checkout

    Raises:
        HTTPException: If plan not found, invalid config, or API fails
    """
    if not db:
        from web.db import SessionLocal
        db = SessionLocal()
        owns_db = True
    else:
        owns_db = False

    try:
        # Validate plan exists and is active
        plan = db.query(PlanConfig).filter_by(plan_key=plan_key, is_active=True).first()
        if not plan:
            raise HTTPException(status_code=400, detail=f"Plan '{plan_key}' not found or inactive")

        if not (plan.min_units <= num_units <= plan.max_units):
            raise HTTPException(
                status_code=400,
                detail=f"Plan '{plan.display_name}' requires {plan.min_units}-{plan.max_units} units",
            )

        # Get PayPal plan ID
        paypal_plan_id = PAYPAL_PLAN_IDS.get(plan_key, "")
        if not paypal_plan_id:
            raise HTTPException(
                status_code=500,
                detail=f"PayPal plan ID not configured for '{plan_key}'",
            )

        token = _get_access_token()

        # Calculate amount in USD (Stripe uses cents, PayPal uses decimal)
        amount = f"{(plan.base_fee_usd + plan.per_unit_fee_usd * num_units):.2f}"

        # Create subscription request
        url = f"{PAYPAL_BASE_URL}/v1/billing/subscriptions"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

        payload = {
            "plan_id": paypal_plan_id,
            "subscriber": {
                "name": {
                    "given_name": "HostAI User",
                    "surname": tenant_id,
                },
                "email_address": tenant_id,
            },
            "application_context": {
                "brand_name": "HostAI",
                "locale": "en-US",
                "user_action": "SUBSCRIBE_NOW",
                "return_url": return_url,
                "cancel_url": cancel_url,
            },
            "custom_id": tenant_id,  # Store tenant_id for webhook lookup
        }

        resp = httpx.post(url, headers=headers, json=payload, timeout=10.0)
        resp.raise_for_status()

        data = resp.json()
        subscription_id = data.get("id", "")
        links = data.get("links", [])

        # Find approval URL
        approval_url = ""
        for link in links:
            if link.get("rel") == "approve":
                approval_url = link.get("href", "")
                break

        if not approval_url:
            log.error("PayPal did not return approval URL: %s", data)
            raise HTTPException(status_code=500, detail="PayPal subscription creation failed")

        log.info(
            "PayPal subscription created: tenant=%s plan=%s id=%s",
            tenant_id,
            plan_key,
            subscription_id,
        )

        return approval_url

    finally:
        if owns_db:
            db.close()


def get_subscription(subscription_id: str) -> dict:
    """
    Fetch subscription details from PayPal.

    Args:
        subscription_id: PayPal subscription ID

    Returns:
        Subscription object from PayPal API
    """
    token = _get_access_token()

    url = f"{PAYPAL_BASE_URL}/v1/billing/subscriptions/{subscription_id}"
    headers = {"Authorization": f"Bearer {token}"}

    try:
        resp = httpx.get(url, headers=headers, timeout=10.0)
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        log.error("Failed to fetch PayPal subscription %s: %s", subscription_id, exc)
        raise HTTPException(status_code=500, detail="Failed to fetch subscription status")


def cancel_subscription(subscription_id: str, reason: str = "User cancelled") -> None:
    """
    Cancel a PayPal subscription.

    Args:
        subscription_id: PayPal subscription ID
        reason: Cancellation reason for PayPal

    Raises:
        HTTPException: If cancellation fails
    """
    token = _get_access_token()

    url = f"{PAYPAL_BASE_URL}/v1/billing/subscriptions/{subscription_id}/cancel"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    payload = {"reason": reason}

    try:
        resp = httpx.post(url, headers=headers, json=payload, timeout=10.0)
        resp.raise_for_status()
        log.info("PayPal subscription cancelled: %s", subscription_id)
    except Exception as exc:
        log.error("Failed to cancel PayPal subscription %s: %s", subscription_id, exc)
        raise HTTPException(status_code=500, detail="Failed to cancel subscription")


# ---------------------------------------------------------------------------
# PayPal Webhook Handling
# ---------------------------------------------------------------------------

def _verify_webhook_signature(payload_bytes: bytes, headers: dict) -> bool:
    """
    Verify PayPal webhook signature using their verification endpoint.

    Args:
        payload_bytes: Raw webhook body
        headers: HTTP headers from webhook request

    Returns:
        True if signature is valid, False otherwise
    """
    if not PAYPAL_WEBHOOK_ID:
        log.warning("PayPal webhook ID not configured; skipping signature verification")
        return False

    transmission_id = headers.get("paypal-transmission-id", "")
    transmission_time = headers.get("paypal-transmission-time", "")
    cert_url = headers.get("paypal-cert-url", "")
    auth_algo = headers.get("paypal-auth-algo", "")
    signature = headers.get("paypal-transmission-sig", "")

    if not all([transmission_id, transmission_time, cert_url, auth_algo, signature]):
        log.warning("PayPal webhook missing required headers")
        return False

    token = _get_access_token()

    url = f"{PAYPAL_BASE_URL}/v1/notifications/verify-webhook-signature"
    headers_req = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    payload = {
        "transmission_id": transmission_id,
        "transmission_time": transmission_time,
        "cert_url": cert_url,
        "auth_algo": auth_algo,
        "transmission_sig": signature,
        "webhook_id": PAYPAL_WEBHOOK_ID,
        "webhook_event": json.loads(payload_bytes),
    }

    try:
        resp = httpx.post(url, headers=headers_req, json=payload, timeout=10.0)
        resp.raise_for_status()
        data = resp.json()
        is_valid = data.get("verification_status") == "SUCCESS"
        if is_valid:
            log.debug("PayPal webhook signature verified")
        else:
            log.warning("PayPal webhook signature verification failed")
        return is_valid
    except Exception as exc:
        log.error("PayPal signature verification request failed: %s", exc)
        return False


def handle_paypal_webhook(payload_bytes: bytes, headers: dict, db: Session) -> dict:
    """
    Handle incoming PayPal webhook events.

    Supported events:
    - BILLING.SUBSCRIPTION.CREATED
    - BILLING.SUBSCRIPTION.ACTIVATED
    - BILLING.SUBSCRIPTION.CANCELLED
    - BILLING.SUBSCRIPTION.SUSPENDED
    - BILLING.SUBSCRIPTION.PAYMENT.FAILED
    - PAYMENT.SALE.COMPLETED

    Args:
        payload_bytes: Raw webhook body
        headers: HTTP headers from webhook request
        db: Database session

    Returns:
        Response dict with status
    """
    # Verify signature
    if not _verify_webhook_signature(payload_bytes, headers):
        log.warning("PayPal webhook signature verification failed; rejecting")
        raise HTTPException(status_code=401, detail="Signature verification failed")

    try:
        event = json.loads(payload_bytes)
    except json.JSONDecodeError:
        log.warning("Invalid JSON in PayPal webhook payload")
        raise HTTPException(status_code=400, detail="Invalid JSON")

    event_id = event.get("id", "")
    event_type = event.get("event_type", "")
    resource = event.get("resource", {})

    log.info("PayPal webhook: %s %s", event_type, event_id)

    # Idempotency check (similar to Stripe)
    if event_id:
        from web.models import SystemConfig

        try:
            dedup_key = f"paypal_evt_{event_id}"
            existing = db.query(SystemConfig).filter_by(key=dedup_key).first()
            if existing:
                log.info("PayPal duplicate event skipped (DB dedup): %s %s", event_type, event_id)
                return {"status": "ok", "duplicate": True}
            db.add(SystemConfig(key=dedup_key, value="1"))
        except Exception as exc:
            log.warning("DB idempotency check failed: %s", exc)
            db.rollback()

    # Handle event types
    if event_type == "BILLING.SUBSCRIPTION.CREATED":
        _handle_subscription_created(resource, db)

    elif event_type == "BILLING.SUBSCRIPTION.ACTIVATED":
        _handle_subscription_activated(resource, db)

    elif event_type in (
        "BILLING.SUBSCRIPTION.CANCELLED",
        "BILLING.SUBSCRIPTION.SUSPENDED",
    ):
        _handle_subscription_cancelled(resource, db)

    elif event_type == "PAYMENT.SALE.COMPLETED":
        _handle_payment_completed(resource, db)

    db.commit()
    return {"status": "ok"}


def _handle_subscription_created(resource: dict, db: Session) -> None:
    """Handle subscription created event."""
    subscription_id = resource.get("id", "")
    custom_id = resource.get("custom_id", "")  # Our tenant_id

    if custom_id:
        cfg = db.query(TenantConfig).filter_by(tenant_id=custom_id).first()
        if cfg:
            cfg.paypal_subscription_id = subscription_id
            log.info("PayPal subscription created: tenant=%s id=%s", custom_id, subscription_id)


def _handle_subscription_activated(resource: dict, db: Session) -> None:
    """Handle subscription activated event."""
    subscription_id = resource.get("id", "")
    custom_id = resource.get("custom_id", "")
    status = resource.get("status", "ACTIVE")

    if not custom_id:
        # Fallback: search by subscription ID
        cfg = db.query(TenantConfig).filter_by(paypal_subscription_id=subscription_id).first()
    else:
        cfg = db.query(TenantConfig).filter_by(tenant_id=custom_id).first()

    if cfg:
        cfg.paypal_subscription_id = subscription_id
        cfg.subscription_payment_method = "paypal"
        cfg.subscription_status = "active"

        # Extract plan from billing plan details if available
        billing_plan_id = resource.get("plan_id", "")
        if billing_plan_id:
            for plan_key, plan_id in PAYPAL_PLAN_IDS.items():
                if plan_id == billing_plan_id:
                    cfg.subscription_plan = plan_key
                    break

        db.commit()
        log.info(
            "PayPal subscription activated: tenant=%s id=%s status=%s",
            custom_id,
            subscription_id,
            status,
        )


def _handle_subscription_cancelled(resource: dict, db: Session) -> None:
    """Handle subscription cancelled or suspended event."""
    subscription_id = resource.get("id", "")
    custom_id = resource.get("custom_id", "")

    if not custom_id:
        cfg = db.query(TenantConfig).filter_by(paypal_subscription_id=subscription_id).first()
    else:
        cfg = db.query(TenantConfig).filter_by(tenant_id=custom_id).first()

    if cfg:
        cfg.subscription_status = "cancelled"
        db.commit()
        log.info("PayPal subscription cancelled: tenant=%s id=%s", custom_id, subscription_id)


def _handle_payment_completed(resource: dict, db: Session) -> None:
    """Handle payment completed event."""
    sale_id = resource.get("id", "")
    subscription_id = resource.get("billing_agreement_id", "")

    if subscription_id:
        cfg = db.query(TenantConfig).filter_by(paypal_subscription_id=subscription_id).first()
        if cfg:
            # Confirm subscription is still active
            if cfg.subscription_status != "active":
                cfg.subscription_status = "active"
                db.commit()
            log.info("PayPal payment completed: subscription=%s sale=%s", subscription_id, sale_id)
