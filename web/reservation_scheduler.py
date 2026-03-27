# © 2024 Jestin Rajan. All rights reserved.
"""
Reservation Scheduler — proactive message generation from imported Airbnb CSV data.

Runs as a background thread (started by worker_manager alongside email/calendar workers).
Checks every 30 minutes for reservations that need proactive drafts:

  - PRE-ARRIVAL (7 days before check-in):
    Warm message to guest with property info, check-in instructions.

  - CHECK-OUT THANK-YOU (check-out day):
    Thank-you message + subtle review request.

  - REVIEW REMINDER (14 days after check-out):
    Follow-up nudge to host to leave a review for the guest.

  - CLEANER BRIEF (check-out day, morning):
    Full cleaning checklist and turnover brief for the cleaner.

Each draft fires at most once per reservation (tracked via boolean flags on the row).
"""

import logging
import time
import threading
from datetime import datetime, timezone, timedelta

log = logging.getLogger(__name__)

_POLL_MINUTES         = 30
_PRE_ARRIVAL_DAYS     = 7
_REVIEW_REMIND_DAYS   = 14
_MAX_BACKOFF          = 3600


def _run_scheduler(tenant_id: str, property_context: str,
                   stop_flag: threading.Event):
    """Main scheduler loop for one tenant."""
    fail_streak = 0
    log.info("[%s] Reservation scheduler started", tenant_id)

    while not stop_flag.is_set():
        try:
            _process_tenant(tenant_id, property_context)
            fail_streak = 0
        except Exception as exc:
            fail_streak += 1
            backoff = min(_POLL_MINUTES * 60 * (2 ** (fail_streak - 1)), _MAX_BACKOFF)
            log.error("[%s] Scheduler error (streak=%d): %s — backoff %ds",
                      tenant_id, fail_streak, exc, backoff)
            stop_flag.wait(backoff)
            continue
        stop_flag.wait(_POLL_MINUTES * 60)

    log.info("[%s] Reservation scheduler stopped", tenant_id)


def _process_tenant(tenant_id: str, property_context: str):
    from web.db import SessionLocal
    from web.models import Reservation, Draft, ActivityLog
    from web.classifier import generate_draft, make_draft_id

    db  = SessionLocal()
    now = datetime.now(timezone.utc)
    today = now.date()

    try:
        # Only look at confirmed reservations within a relevant window
        window_start = today - timedelta(days=_REVIEW_REMIND_DAYS + 2)
        window_end   = today + timedelta(days=_PRE_ARRIVAL_DAYS + 2)

        reservations = db.query(Reservation).filter(
            Reservation.tenant_id == tenant_id,
            Reservation.status == "confirmed",
            Reservation.checkin >= window_start,
            Reservation.checkin <= window_end,
        ).all()

        for r in reservations:
            if not r.checkin or not r.checkout:
                continue
            _maybe_pre_arrival(r, today, property_context, db, tenant_id)
            _maybe_cleaner_brief(r, today, property_context, db, tenant_id)
            _maybe_checkout_message(r, today, property_context, db, tenant_id)

        # Review reminders need checkout in the review window
        review_window_start = today - timedelta(days=_REVIEW_REMIND_DAYS + 1)
        review_window_end   = today - timedelta(days=_REVIEW_REMIND_DAYS - 1)
        review_rows = db.query(Reservation).filter(
            Reservation.tenant_id == tenant_id,
            Reservation.status == "confirmed",
            Reservation.checkout >= review_window_start,
            Reservation.checkout <= review_window_end,
            Reservation.review_reminder_sent == False,  # noqa: E712
        ).all()
        for r in review_rows:
            _maybe_review_reminder(r, today, property_context, db, tenant_id)

    finally:
        db.close()


def _save_draft(db, tenant_id: str, draft_id: str, guest_name: str,
                context: str, draft_text: str, label: str):
    from web.models import Draft, ActivityLog
    db.add(Draft(
        id=draft_id, tenant_id=tenant_id, source="calendar",
        guest_name=guest_name, message=context, reply_to=None,
        msg_type="complex", vendor_type=None, draft=draft_text,
        status="pending",
    ))
    db.add(ActivityLog(
        tenant_id=tenant_id, event_type=f"reservation_{label}",
        message=f"Proactive draft: {guest_name} ({label})",
    ))
    db.commit()


def _maybe_pre_arrival(r, today, property_context: str, db, tenant_id: str):
    if r.pre_arrival_sent:
        return
    days_until = (r.checkin - today).days
    # Allow ±1 day window to tolerate UTC vs host-local timezone differences
    if not (_PRE_ARRIVAL_DAYS - 1 <= days_until <= _PRE_ARRIVAL_DAYS + 1):
        return

    from web.classifier import generate_draft, make_draft_id
    prop = r.listing_name or "the property"
    context = (
        f"[PROACTIVE — PRE-ARRIVAL MESSAGE]\n"
        f"Guest: {r.guest_name}\n"
        f"Confirmation: {r.confirmation_code}\n"
        f"Property: {prop}\n"
        f"{'Room / unit / property #: ' + r.unit_identifier + chr(10) if r.unit_identifier else ''}"
        f"Check-in: {r.checkin.strftime('%A, %B %d, %Y')} ({_PRE_ARRIVAL_DAYS} days away)\n"
        f"Check-out: {r.checkout.strftime('%A, %B %d, %Y')}\n"
        f"Nights: {r.nights or 'N/A'} | Guests: {r.guests_count or 'N/A'}\n\n"
        f"Generate a warm pre-arrival welcome message for {r.guest_name}. "
        f"Include check-in time, how to access the property, WiFi details if available, "
        f"and express excitement about their visit."
    )
    try:
        draft = generate_draft(r.guest_name, context, "complex",
                               skill="reply", property_context=property_context)
        _save_draft(db, tenant_id, make_draft_id("reservation"), r.guest_name,
                    context, draft, "pre_arrival")
        r.pre_arrival_sent = True
        db.commit()
        log.info("[%s] Pre-arrival draft created for %s", tenant_id, r.guest_name)
    except Exception as exc:
        log.error("[%s] Pre-arrival draft failed for %s: %s", tenant_id, r.guest_name, exc)


def _maybe_cleaner_brief(r, today, property_context: str, db, tenant_id: str):
    if r.cleaner_brief_sent:
        return
    if r.checkout != today:
        return

    from web.classifier import generate_draft, make_draft_id
    prop = r.listing_name or "the property"
    context = (
        f"[PROACTIVE — CLEANER TURNOVER BRIEF]\n"
        f"Property: {prop}\n"
        f"{'Room / unit / property #: ' + r.unit_identifier + chr(10) if r.unit_identifier else ''}"
        f"Guest checking out today: {r.guest_name}\n"
        f"Confirmation: {r.confirmation_code}\n"
        f"Nights stayed: {r.nights or 'N/A'} | Guests: {r.guests_count or 'N/A'}\n\n"
        f"Generate a complete cleaning and turnover checklist for the cleaner. "
        f"Cover all rooms, linen change, restocking, damage check, and preparing for the next guest."
    )
    try:
        draft = generate_draft("Cleaner", context, "complex",
                               skill="cleaner-brief", property_context=property_context)
        _save_draft(db, tenant_id, make_draft_id("reservation"), f"{r.guest_name} checkout",
                    context, draft, "cleaner_brief")
        r.cleaner_brief_sent = True
        db.commit()
        log.info("[%s] Cleaner brief draft created for checkout: %s", tenant_id, r.guest_name)
    except Exception as exc:
        log.error("[%s] Cleaner brief failed for %s: %s", tenant_id, r.guest_name, exc)


def _maybe_checkout_message(r, today, property_context: str, db, tenant_id: str):
    if r.checkout_msg_sent:
        return
    if r.checkout != today:
        return

    from web.classifier import generate_draft, make_draft_id
    prop = r.listing_name or "the property"
    context = (
        f"[PROACTIVE — CHECKOUT THANK-YOU]\n"
        f"Guest: {r.guest_name}\n"
        f"Confirmation: {r.confirmation_code}\n"
        f"Property: {prop}\n"
        f"{'Room / unit / property #: ' + r.unit_identifier + chr(10) if r.unit_identifier else ''}"
        f"Check-out today: {r.checkout.strftime('%A, %B %d, %Y')}\n"
        f"Stayed: {r.nights or 'N/A'} nights\n\n"
        f"Generate a warm, genuine thank-you message to send to {r.guest_name} on their checkout day. "
        f"Thank them for staying, wish them safe travels, and gently mention that a 5-star review would mean the world."
    )
    try:
        draft = generate_draft(r.guest_name, context, "complex",
                               skill="reply", property_context=property_context)
        _save_draft(db, tenant_id, make_draft_id("reservation"), r.guest_name,
                    context, draft, "checkout_thanks")
        r.checkout_msg_sent = True
        db.commit()
        log.info("[%s] Checkout thank-you draft created for %s", tenant_id, r.guest_name)
    except Exception as exc:
        log.error("[%s] Checkout message failed for %s: %s", tenant_id, r.guest_name, exc)


def _maybe_review_reminder(r, today, property_context: str, db, tenant_id: str):
    from web.classifier import generate_draft, make_draft_id
    prop = r.listing_name or "the property"
    context = (
        f"[PROACTIVE — REVIEW REMINDER TO HOST]\n"
        f"Guest: {r.guest_name} checked out {_REVIEW_REMIND_DAYS} days ago from {prop}.\n"
        f"{'Room / unit / property #: ' + r.unit_identifier + chr(10) if r.unit_identifier else ''}"
        f"Confirmation: {r.confirmation_code}\n\n"
        f"Write a short, friendly reminder to the HOST (not the guest) reminding them to "
        f"leave a review for {r.guest_name} on Airbnb before the review window closes. "
        f"Keep it casual, under 3 sentences."
    )
    try:
        draft = generate_draft(r.guest_name, context, "complex",
                               skill="reply", property_context=property_context)
        _save_draft(db, tenant_id, make_draft_id("reservation"), r.guest_name,
                    context, draft, "review_reminder")
        r.review_reminder_sent = True
        db.commit()
        log.info("[%s] Review reminder draft created for %s", tenant_id, r.guest_name)
    except Exception as exc:
        log.error("[%s] Review reminder failed for %s: %s", tenant_id, r.guest_name, exc)


def make_config_from_db(tenant_id: str):
    """Return property_context for the tenant, or None if not ready."""
    from web.db import SessionLocal
    from web.models import TenantConfig, Reservation
    from web.classifier import build_property_context
    db = SessionLocal()
    try:
        cfg = db.query(TenantConfig).filter_by(tenant_id=tenant_id).first()
        if not cfg:
            return None
        # Only run if tenant has reservations imported
        has_reservations = db.query(Reservation).filter_by(tenant_id=tenant_id).first() is not None
        if not has_reservations:
            return None
        return build_property_context(cfg)
    finally:
        db.close()
