# © 2024 Jestin Rajan. All rights reserved.
"""
Multi-tenant Calendar Worker.
Adapted from airbnb-host/scripts/calendar_watcher.py.
Polls iCal feeds and fires proactive drafts stored in PostgreSQL.
"""

import re
import time
import logging
import urllib.request
from datetime import datetime, timezone, date as date_type
from zoneinfo import ZoneInfo
from dataclasses import dataclass, field
from typing import Optional

from icalendar import Calendar

from web.db import SessionLocal
from web.models import Draft, CalendarState, ActivityLog
from web.classifier import generate_draft, make_draft_id, build_property_context
from web.crypto import decrypt

log = logging.getLogger(__name__)

_RESERVED_RE = re.compile(r"Reserved\s*[-–]\s*(.+)", re.IGNORECASE)
_MAX_BACKOFF  = 3600
_PRE_ARRIVAL_DAYS      = 7
_CHECKIN_NOTICE_HOURS  = 24
_CHECKOUT_BRIEF_HOUR   = 11
_DEFAULT_CHECKIN_HOUR  = 15
_EXTENSION_OFFER_HOUR  = 9
_POLL_MINUTES          = 30


@dataclass
class CalendarConfig:
    tenant_id:         str
    ical_urls:         list[str]
    property_names:    list[str]
    property_context:  str = ""   # injected into Claude system prompt
    timezone:          str = "UTC"
    poll_minutes:      int = _POLL_MINUTES


# ---------------------------------------------------------------------------
# iCal helpers
# ---------------------------------------------------------------------------

def _fetch_ical(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "Airbnb-Host-Assistant/1.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return resp.read()


def _to_date(val) -> date_type:
    if isinstance(val, datetime):
        return val.date()
    return val


def _parse_ical(raw: bytes) -> list[dict]:
    cal = Calendar.from_ical(raw)
    bookings = []
    for component in cal.walk():
        if component.name != "VEVENT":
            continue
        summary = str(component.get("SUMMARY", ""))
        m = _RESERVED_RE.match(summary)
        if not m:
            continue
        guest_name = m.group(1).strip()
        uid        = str(component.get("UID", "unknown"))
        checkin    = _to_date(component.get("DTSTART").dt)
        checkout   = _to_date(component.get("DTEND").dt)
        bookings.append({"uid": uid, "guest_name": guest_name, "checkin": checkin,
                         "checkout": checkout, "nights": (checkout - checkin).days})
    return bookings


# ---------------------------------------------------------------------------
# State helpers (DB-backed)
# ---------------------------------------------------------------------------

def _state_fired(tenant_id: str, key: str) -> bool:
    db = SessionLocal()
    try:
        return db.query(CalendarState).filter_by(tenant_id=tenant_id, state_key=key).first() is not None
    finally:
        db.close()


def _fire_state(tenant_id: str, key: str):
    db = SessionLocal()
    try:
        if not db.query(CalendarState).filter_by(tenant_id=tenant_id, state_key=key).first():
            db.add(CalendarState(tenant_id=tenant_id, state_key=key))
            db.commit()
    finally:
        db.close()


def _save_draft(tenant_id: str, draft_id: str, guest_name: str, context: str, draft_text: str, label: str):
    db = SessionLocal()
    try:
        db.add(Draft(id=draft_id, tenant_id=tenant_id, source="calendar",
                     guest_name=guest_name, message=context, reply_to=None,
                     msg_type="complex", vendor_type=None, draft=draft_text,
                     status="pending"))
        db.add(ActivityLog(tenant_id=tenant_id, event_type=f"calendar_{label}",
                           message=f"Calendar draft for {guest_name} ({label})"))
        db.commit()
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Trigger helpers
# ---------------------------------------------------------------------------

def _request_draft(cfg: CalendarConfig, skill: str, guest: str, context: str) -> Optional[tuple]:
    try:
        draft = generate_draft(
            guest, context, "complex", skill=skill,
            property_context=cfg.property_context,
        )
        return make_draft_id("calendar"), draft
    except Exception as exc:
        log.error("[%s] Draft generation failed for skill=%s: %s", cfg.tenant_id, skill, exc)
        return None


def check_booking_confirmed(cfg: CalendarConfig, booking: dict, label: str):
    key = f"confirmed:{booking['uid']}"
    if _state_fired(cfg.tenant_id, key):
        return
    guest  = booking["guest_name"]
    nights = booking["nights"]
    ci_str = booking["checkin"].strftime("%A, %B %d, %Y")
    co_str = booking["checkout"].strftime("%A, %B %d, %Y")
    draft_text = (
        f"New booking confirmed!\n\nProperty: {label}\nGuest: {guest}\n"
        f"Check-in: {ci_str}\nCheckout: {co_str}\nNights: {nights}"
    )
    draft_id = make_draft_id("calendar")
    _save_draft(cfg.tenant_id, draft_id, f"{guest} — new booking",
                "New booking confirmation", draft_text, "booking")
    _fire_state(cfg.tenant_id, key)
    log.info("[%s] New booking for %s noted", cfg.tenant_id, guest)


def check_pre_arrival(cfg: CalendarConfig, booking: dict, label: str, now: datetime):
    key = f"pre_arrival:{booking['uid']}"
    if _state_fired(cfg.tenant_id, key):
        return
    checkin_dt = datetime(booking["checkin"].year, booking["checkin"].month,
                          booking["checkin"].day, _DEFAULT_CHECKIN_HOUR, tzinfo=ZoneInfo(cfg.timezone))
    hours_until = (checkin_dt - now).total_seconds() / 3600
    window_lo, window_hi = _PRE_ARRIVAL_DAYS * 24, (_PRE_ARRIVAL_DAYS + 1) * 24
    if window_lo < hours_until <= window_hi:
        guest   = booking["guest_name"]
        context = (
            f"[CALENDAR TRIGGER — PRE-ARRIVAL MESSAGE]\nProperty: {label}\nGuest: {guest}\n"
            f"Check-in: {booking['checkin'].strftime('%A, %B %d, %Y')} (in {_PRE_ARRIVAL_DAYS} days)\n"
            f"Please generate a warm pre-arrival message to send to {guest} on Airbnb."
        )
        result = _request_draft(cfg, "reply", guest, context)
        if result:
            draft_id, draft = result
            _save_draft(cfg.tenant_id, draft_id, f"{guest} — pre-arrival", context, draft, "pre-arrival")
            _fire_state(cfg.tenant_id, key)


def check_checkin(cfg: CalendarConfig, booking: dict, label: str, now: datetime):
    key = f"checkin:{booking['uid']}"
    if _state_fired(cfg.tenant_id, key):
        return
    checkin_dt = datetime(booking["checkin"].year, booking["checkin"].month,
                          booking["checkin"].day, _DEFAULT_CHECKIN_HOUR, tzinfo=ZoneInfo(cfg.timezone))
    hours_until = (checkin_dt - now).total_seconds() / 3600
    if 0 < hours_until <= _CHECKIN_NOTICE_HOURS:
        guest   = booking["guest_name"]
        context = (
            f"[CALENDAR TRIGGER — CHECK-IN INSTRUCTIONS]\nProperty: {label}\nGuest: {guest}\n"
            f"Check-in in ~{int(hours_until)}h\nNights: {booking['nights']}\n"
            f"Please generate complete guest-ready check-in instructions for {label}."
        )
        result = _request_draft(cfg, "checkin", guest, context)
        if result:
            draft_id, draft = result
            _save_draft(cfg.tenant_id, draft_id, f"{guest} — check-in", context, draft, "checkin")
            _fire_state(cfg.tenant_id, key)


def check_cleaner_brief(cfg: CalendarConfig, booking: dict, label: str, now: datetime):
    key = f"cleaner:{booking['uid']}"
    if _state_fired(cfg.tenant_id, key):
        return
    if booking["checkout"] == now.date() and now.hour >= _CHECKOUT_BRIEF_HOUR:
        guest   = booking["guest_name"]
        context = (
            f"[CALENDAR TRIGGER — CLEANER BRIEF]\nProperty: {label}\n"
            f"Guest checking out today: {guest}\n"
            f"Please generate a full cleaning checklist and turnover brief."
        )
        result = _request_draft(cfg, "cleaner-brief", guest, context)
        if result:
            draft_id, draft = result
            _save_draft(cfg.tenant_id, draft_id, f"{guest} checkout — cleaner brief", context, draft, "cleaner")
            _fire_state(cfg.tenant_id, key)


def check_extension_offer(cfg: CalendarConfig, booking: dict, label: str, now: datetime):
    """
    Fire a late-checkout / extension offer draft 2 hours before checkout.
    Trigger window: checkout day, between _EXTENSION_OFFER_HOUR and _EXTENSION_OFFER_HOUR+2.
    Only fires once per booking (state key: extension:<uid>).
    """
    key = f"extension:{booking['uid']}"
    if _state_fired(cfg.tenant_id, key):
        return
    if booking["checkout"] != now.date():
        return
    if not (_EXTENSION_OFFER_HOUR <= now.hour < _EXTENSION_OFFER_HOUR + 2):
        return
    guest   = booking["guest_name"]
    co_str  = booking["checkout"].strftime("%A, %B %d, %Y")
    context = (
        f"[CALENDAR TRIGGER — LATE CHECKOUT / EXTENSION OFFER]\nProperty: {label}\nGuest: {guest}\n"
        f"Checkout date: {co_str}\n"
        f"Please generate a friendly message asking if the guest would like to extend their stay "
        f"or arrange a late checkout, and if so to let us know so we can check availability."
    )
    result = _request_draft(cfg, "reply", guest, context)
    if result:
        draft_id, draft = result
        _save_draft(cfg.tenant_id, draft_id, f"{guest} — extension offer", context, draft, "extension")
        _fire_state(cfg.tenant_id, key)


# ---------------------------------------------------------------------------
# Main poll loop
# ---------------------------------------------------------------------------

def run_for_tenant(cfg: CalendarConfig, stop_flag: "threading.Event"):
    """Poll loop for one tenant. Runs until stop_flag is set."""
    if not cfg.ical_urls:
        log.info("[%s] No iCal URLs — calendar watcher idle", cfg.tenant_id)
        return

    fail_streak = 0
    log.info("[%s] Calendar watcher started — %d listing(s)", cfg.tenant_id, len(cfg.ical_urls))

    while not stop_flag.is_set():
        now = datetime.now(ZoneInfo(cfg.timezone))
        try:
            for idx, url in enumerate(cfg.ical_urls):
                label    = cfg.property_names[idx] if idx < len(cfg.property_names) else f"Property {idx + 1}"
                raw      = _fetch_ical(url)
                bookings = _parse_ical(raw)
                for booking in bookings:
                    check_booking_confirmed(cfg, booking, label)
                    check_pre_arrival(cfg, booking, label, now)
                    check_checkin(cfg, booking, label, now)
                    check_cleaner_brief(cfg, booking, label, now)
                    check_extension_offer(cfg, booking, label, now)
            fail_streak = 0
        except Exception as exc:
            fail_streak += 1
            backoff = min(cfg.poll_minutes * 60 * (2 ** (fail_streak - 1)), _MAX_BACKOFF)
            log.error("[%s] Calendar error (streak=%d): %s — backoff %ds",
                      cfg.tenant_id, fail_streak, exc, backoff)
            stop_flag.wait(backoff)
            continue
        stop_flag.wait(cfg.poll_minutes * 60)

    log.info("[%s] Calendar watcher stopped.", cfg.tenant_id)


def make_config_from_db(tenant_id: str) -> Optional[CalendarConfig]:
    db = SessionLocal()
    try:
        from web.models import TenantConfig
        cfg = db.query(TenantConfig).filter_by(tenant_id=tenant_id).first()
        if not cfg or not cfg.ical_urls:
            return None
        urls  = [u.strip() for u in cfg.ical_urls.split(",") if u.strip()]
        names = [n.strip() for n in (cfg.property_names or "").split(",") if n.strip()]
        return CalendarConfig(
            tenant_id=tenant_id,
            ical_urls=urls,
            property_names=names,
            property_context=build_property_context(cfg),
            timezone=getattr(cfg, "timezone", "UTC"),
        )
    finally:
        db.close()
