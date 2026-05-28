# © 2024 Jestin Rajan. All rights reserved.
"""
Direct Booking / Inquiry Handling — handle prospective guests before they're confirmed.

Flow:
  1. Unknown phone number messages or calls
  2. detect_booking_intent() → has the guest expressed interest in booking?
  3. extract_dates_and_guests() → parse "next weekend, 4 people"
  4. PMS check_availability() if listing known
  5. Send a quote / availability response
  6. If guest confirms, create_booking() in PMS

The BookingInquiry record tracks the funnel for the host.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, date, timedelta, timezone
from typing import Optional

from sqlalchemy.orm import Session

from web.models import BookingInquiry, PMSIntegration, TenantConfig

log = logging.getLogger(__name__)


_BOOKING_KEYWORDS = (
    "book", "booking", "reserve", "reservation", "stay", "available",
    "availability", "rate", "price", "cost", "per night", "vacancy",
    "openings", "free", "any rooms", "any units",
)


def _likely_booking_text(text: str) -> bool:
    t = (text or "").lower()
    return any(kw in t for kw in _BOOKING_KEYWORDS)


def detect_booking_intent(text: str) -> Optional[dict]:
    """
    LLM-based extraction of booking intent + dates + guest count.
    Returns {"checkin", "checkout", "guests", "confidence"} or None.
    """
    if not text or not _likely_booking_text(text):
        # Cheap heuristic gate — saves LLM cost on obviously-not-booking messages
        return None
    prompt = f"""A prospective guest sent this message about staying at a property.

Message: "{text[:500]}"

Extract:
- Is this a booking inquiry?
- The check-in date (if mentioned)
- The check-out date (if mentioned)
- Number of guests (if mentioned)

Respond with ONLY JSON (no markdown):
{{
  "is_inquiry": true|false,
  "checkin": "YYYY-MM-DD or null",
  "checkout": "YYYY-MM-DD or null",
  "guests": 0,
  "confidence": 0.0-1.0
}}"""
    try:
        import openai
        from web.db import SessionLocal
        from web.system_config_store import load_system_config
        from web.crypto import decrypt
        with SessionLocal() as db:
            sys_conf = load_system_config(db)
            if not sys_conf or not sys_conf.openrouter_api_key_enc:
                return None
            key = decrypt(sys_conf.openrouter_api_key_enc) or sys_conf.openrouter_api_key_enc
        client = openai.OpenAI(base_url="https://openrouter.ai/api/v1", api_key=key)
        resp = client.chat.completions.create(
            model="google/gemini-2.5-flash",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=200,
            temperature=0,
        )
        raw = (resp.choices[0].message.content or "").strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        data = json.loads(raw.strip())
        if not data.get("is_inquiry"):
            return None
        return {
            "checkin":    data.get("checkin"),
            "checkout":   data.get("checkout"),
            "guests":     int(data.get("guests") or 0),
            "confidence": float(data.get("confidence") or 0.0),
        }
    except Exception as exc:
        log.warning("[INQUIRY] detect_booking_intent failed: %s", exc)
        return None


def _get_pms_adapter(db: Session, tenant_id: str):
    integration = db.query(PMSIntegration).filter_by(
        tenant_id=tenant_id, is_active=True
    ).first()
    if not integration:
        return None, None
    try:
        from web.crypto import decrypt
        from web.pms_base import make_adapter
        adapter = make_adapter(
            integration.pms_type,
            decrypt(integration.api_key_enc),
            integration.account_id or "",
            integration.api_base_url or "",
        )
        return adapter, integration
    except Exception as exc:
        log.error("[INQUIRY] Could not load PMS adapter: %s", exc)
        return None, integration


def create_or_update_inquiry(db: Session, tenant_id: str, channel: str,
                              contact_phone: Optional[str], contact_email: Optional[str],
                              contact_name: Optional[str], parsed: dict) -> BookingInquiry:
    """Find existing open inquiry for this contact (24h window), else create."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    existing = None
    if contact_phone:
        existing = (
            db.query(BookingInquiry)
            .filter(
                BookingInquiry.tenant_id == tenant_id,
                BookingInquiry.contact_phone == contact_phone,
                BookingInquiry.status == "open",
                BookingInquiry.created_at >= cutoff,
            )
            .order_by(BookingInquiry.created_at.desc())
            .first()
        )

    if existing:
        # Update with any newly extracted info
        if parsed.get("checkin") or parsed.get("checkout") or parsed.get("guests"):
            req = existing.requested_dates_json or {}
            req["checkin"]  = parsed.get("checkin")  or req.get("checkin")
            req["checkout"] = parsed.get("checkout") or req.get("checkout")
            req["guests"]   = parsed.get("guests")   or req.get("guests")
            existing.requested_dates_json = req
            existing.updated_at = datetime.now(timezone.utc)
        return existing

    inquiry = BookingInquiry(
        tenant_id=tenant_id,
        channel=channel,
        contact_phone=contact_phone,
        contact_email=contact_email,
        contact_name=contact_name,
        requested_dates_json={
            "checkin":  parsed.get("checkin"),
            "checkout": parsed.get("checkout"),
            "guests":   parsed.get("guests"),
        },
        status="open",
    )
    db.add(inquiry)
    db.commit()
    db.refresh(inquiry)
    return inquiry


def check_availability_and_quote(db: Session, inquiry: BookingInquiry,
                                  cfg: TenantConfig) -> tuple[Optional[bool], Optional[str]]:
    """
    Use the PMS adapter (if configured) to check availability for the inquiry's dates.
    Returns (is_available, response_text).
    """
    req = inquiry.requested_dates_json or {}
    checkin_str  = req.get("checkin")
    checkout_str = req.get("checkout")

    if not checkin_str or not checkout_str:
        # We don't have enough info yet — ask for it
        return (None, "Hi! Thanks for reaching out — happy to help. What dates are you looking at, and how many guests?")

    try:
        ci = date.fromisoformat(checkin_str)
        co = date.fromisoformat(checkout_str)
    except Exception:
        return (None, "Could you confirm the check-in and check-out dates you'd like?")

    if ci < date.today():
        return (False, "Those dates are in the past — could you give me the dates you'd like to stay?")

    adapter, integration = _get_pms_adapter(db, inquiry.tenant_id)
    if not adapter or not adapter.SUPPORTS_AVAILABILITY:
        # No PMS-side check possible — escalate to host (don't auto-promise)
        inquiry.availability_checked = False
        db.commit()
        return (None, "Thanks! Let me check with the host on those dates and get back to you shortly.")

    # Pick a listing — for simplicity, take the first listing the PMS has
    listing_id = inquiry.listing_id or ""
    if not listing_id:
        # Try to discover from reservations
        try:
            reservations = adapter.get_reservations(date.today(), date.today() + timedelta(days=90))
            if reservations:
                listing_id = reservations[0].reservation_id  # adapter-specific
        except Exception:
            pass

    available: Optional[bool] = None
    try:
        available = adapter.get_availability(listing_id, ci, co) if listing_id else None
    except Exception as exc:
        log.warning("[INQUIRY] availability check failed: %s", exc)
        available = None

    inquiry.availability_checked = True
    inquiry.is_available = available
    db.commit()

    nights = max(1, (co - ci).days)
    if available is True:
        text = (
            f"Great news — we're available from {ci.strftime('%b %d')} to {co.strftime('%b %d')} "
            f"({nights} night{'s' if nights > 1 else ''}). "
            f"Reply YES to confirm and we'll get the booking set up."
        )
        inquiry.status = "quoted"
        db.commit()
        return (True, text)
    elif available is False:
        return (False, f"Unfortunately we're booked from {ci.strftime('%b %d')} to {co.strftime('%b %d')}. Would different dates work?")
    else:
        return (None, "Let me check those dates with the host and I'll get right back to you.")


def handle_inquiry_message(db: Session, tenant_id: str, source: str, reply_to: str,
                            text: str, cfg: TenantConfig) -> bool:
    """
    Top-level: an unknown number sent us a message. Decide if this is an inquiry,
    create the row, send a reply. Returns True if we handled it.
    """
    parsed = detect_booking_intent(text)
    if not parsed:
        return False

    inquiry = create_or_update_inquiry(
        db, tenant_id, source, contact_phone=reply_to,
        contact_email=None, contact_name=None, parsed=parsed,
    )

    available, response = check_availability_and_quote(db, inquiry, cfg)

    if response and reply_to:
        try:
            from web.app import _send_voice_message
            _send_voice_message(cfg, reply_to, response, source)
        except Exception as exc:
            log.error("[INQUIRY] reply send failed: %s", exc)
    return True
