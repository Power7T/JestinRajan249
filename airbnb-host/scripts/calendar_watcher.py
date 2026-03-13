"""
Calendar Watcher — iCal Integration
=====================================
Polls Airbnb iCal feed(s) and auto-triggers proactive drafts:

  • Check-in instructions  — sent to host CHECKIN_NOTICE_HOURS before arrival
  • Cleaner brief          — sent to host on checkout day at CHECKOUT_BRIEF_HOUR

How to get your iCal URL:
  Airbnb → Calendar → Availability settings → Export Calendar → copy the .ics link

Set AIRBNB_ICAL_URL (one listing) or AIRBNB_ICAL_URLS (comma-separated, multiple).

Run:
  python calendar_watcher.py
  (or via start.sh)
"""

import os
import re
import time
import json
import logging
import pathlib
import urllib.request
from datetime import datetime, timezone, timedelta, date as date_type

import requests
from icalendar import Calendar
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
_raw_urls  = os.getenv("AIRBNB_ICAL_URLS") or os.getenv("AIRBNB_ICAL_URL") or ""
ICAL_URLS  = [u.strip() for u in _raw_urls.split(",") if u.strip()]

_raw_names     = os.getenv("PROPERTY_NAMES") or os.getenv("PROPERTY_NAME") or ""
PROPERTY_NAMES = [n.strip() for n in _raw_names.split(",") if n.strip()]

CHECKIN_NOTICE_HOURS  = int(os.getenv("CHECKIN_NOTICE_HOURS", "24"))
CHECKOUT_BRIEF_HOUR   = int(os.getenv("CHECKOUT_BRIEF_HOUR", "11"))    # local hour (0-23)
DEFAULT_CHECKIN_HOUR  = int(os.getenv("DEFAULT_CHECKIN_HOUR", "15"))   # assume 3 PM check-in
EXTENSION_OFFER_HOUR  = int(os.getenv("EXTENSION_OFFER_HOUR",          # 2h before checkout
                             str(max(0, int(os.getenv("CHECKOUT_BRIEF_HOUR", "11")) - 2))))
POLL_MINUTES          = int(os.getenv("CALENDAR_POLL_MINUTES", "30"))

ROUTER_URL     = f"http://127.0.0.1:{os.getenv('ROUTER_PORT', '7771')}"
WA_BOT_URL     = f"http://127.0.0.1:{os.getenv('WA_BOT_PORT', '7772')}"
WA_NOTIFY_URL  = f"{WA_BOT_URL}/notify-host"
INTERNAL_TOKEN = os.getenv("INTERNAL_TOKEN", "")

STATE_FILE = pathlib.Path(__file__).parent / "calendar_state.json"

# ---------------------------------------------------------------------------
# State  —  tracks which events have already triggered notifications
# { "notified_checkin": [uid, ...], "notified_cleaner": [uid, ...] }
# ---------------------------------------------------------------------------

def _load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            log.warning("calendar_state.json unreadable — starting fresh")
    return {
        "notified_checkin":       [],   # check-in instructions draft (24h before)
        "notified_cleaner":       [],   # cleaner brief draft (checkout day)
        "guest_arrival_notified": [],   # host notified guest arrived + asked for WA
        "extension_offered":      [],   # extension offer sent to guest
        "post_checkout_triggered":[],   # review request + cleaner contact triggered
    }


def _save_state(state: dict):
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2))
    tmp.replace(STATE_FILE)

# ---------------------------------------------------------------------------
# Auth header (shared secret with router + WhatsApp bot)
# ---------------------------------------------------------------------------

def _auth_headers() -> dict:
    return {"X-Internal-Token": INTERNAL_TOKEN} if INTERNAL_TOKEN else {}

# ---------------------------------------------------------------------------
# iCal fetch and parse
# ---------------------------------------------------------------------------

def _fetch_ical(url: str) -> bytes:
    req = urllib.request.Request(
        url, headers={"User-Agent": "Airbnb-Host-Assistant/1.0"}
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return resp.read()


_RESERVED_RE = re.compile(r"Reserved\s*[-–]\s*(.+)", re.IGNORECASE)


def _to_date(val) -> date_type:
    """Normalise iCal DTSTART/DTEND to a plain date object."""
    if isinstance(val, datetime):
        return val.date()
    return val   # already a date


def _parse_ical(raw: bytes) -> list[dict]:
    """Return list of booking dicts from raw iCal bytes."""
    cal      = Calendar.from_ical(raw)
    bookings = []
    for component in cal.walk():
        if component.name != "VEVENT":
            continue
        summary = str(component.get("SUMMARY", ""))
        m       = _RESERVED_RE.match(summary)
        if not m:
            continue   # skip blocked/unavailable dates
        guest_name = m.group(1).strip()
        uid        = str(component.get("UID", "unknown"))
        checkin    = _to_date(component.get("DTSTART").dt)
        checkout   = _to_date(component.get("DTEND").dt)
        bookings.append({
            "uid":        uid,
            "guest_name": guest_name,
            "checkin":    checkin,
            "checkout":   checkout,
            "nights":     (checkout - checkin).days,
        })
    return bookings

# ---------------------------------------------------------------------------
# Router + WhatsApp bot helpers
# ---------------------------------------------------------------------------

def _request_draft(skill: str, guest_name: str, context_message: str) -> tuple | None:
    """
    POST /classify to router with skill override.
    Returns (draft_id, draft) or None on failure.
    Calendar source is always treated as complex (host must approve).
    """
    try:
        resp = requests.post(
            f"{ROUTER_URL}/classify",
            json={
                "source":     "calendar",
                "guest_name": guest_name,
                "message":    context_message,
                "skill":      skill,
            },
            headers=_auth_headers(),
            timeout=40,
        )
        resp.raise_for_status()
        data = resp.json()
        return data["draft_id"], data["draft"]
    except Exception as exc:
        log.error("Router request failed for skill=%s: %s", skill, exc)
        return None


def _notify_host(draft_id: str, display_name: str, draft: str, label: str):
    try:
        requests.post(
            WA_NOTIFY_URL,
            json={
                "draft_id":   draft_id,
                "guest_name": display_name,
                "draft":      draft,
                "channel":    f"calendar:{label}",
            },
            headers=_auth_headers(),
            timeout=10,
        )
        log.info("Draft %s (%s) forwarded to host via WhatsApp", draft_id, label)
    except Exception as exc:
        log.warning(
            "WhatsApp bot unreachable: %s — draft %s is in router at %s/pending",
            exc, draft_id, ROUTER_URL,
        )

# ---------------------------------------------------------------------------
# Trigger: check-in instructions
# ---------------------------------------------------------------------------

def check_checkin(booking: dict, property_label: str, state: dict, now: datetime):
    state_uid = f"checkin:{booking['uid']}"
    if state_uid in state["notified_checkin"]:
        return

    # Build an assumed check-in datetime (date + DEFAULT_CHECKIN_HOUR in UTC)
    checkin_dt = datetime(
        booking["checkin"].year,
        booking["checkin"].month,
        booking["checkin"].day,
        DEFAULT_CHECKIN_HOUR,
        tzinfo=timezone.utc,
    )
    hours_until = (checkin_dt - now).total_seconds() / 3600

    if 0 < hours_until <= CHECKIN_NOTICE_HOURS:
        guest  = booking["guest_name"]
        nights = booking["nights"]
        context = (
            f"[CALENDAR TRIGGER — CHECK-IN INSTRUCTIONS]\n\n"
            f"Property: {property_label}\n"
            f"Guest: {guest}\n"
            f"Check-in: {booking['checkin'].strftime('%A, %B %d, %Y')} "
            f"(in approximately {int(hours_until)} hours)\n"
            f"Check-out: {booking['checkout'].strftime('%A, %B %d, %Y')}\n"
            f"Length of stay: {nights} night{'s' if nights != 1 else ''}\n\n"
            f"Please generate complete guest-ready check-in instructions for {property_label}. "
            f"The host will review and forward to the guest before arrival."
        )
        log.info("[%s] Check-in for %s in ~%.0fh — generating draft", property_label, guest, hours_until)
        result = _request_draft("checkin", guest, context)
        if result:
            draft_id, draft = result
            display = f"{guest} — check-in {booking['checkin'].strftime('%b %d')}"
            _notify_host(draft_id, display, draft, "checkin")
            state["notified_checkin"].append(state_uid)
            _save_state(state)

# ---------------------------------------------------------------------------
# Trigger: cleaner brief
# ---------------------------------------------------------------------------

def check_cleaner_brief(booking: dict, property_label: str, state: dict, now: datetime):
    state_uid = f"cleaner:{booking['uid']}"
    if state_uid in state["notified_cleaner"]:
        return

    today               = now.date()
    past_brief_hour     = now.hour >= CHECKOUT_BRIEF_HOUR
    is_checkout_day     = booking["checkout"] == today

    if is_checkout_day and past_brief_hour:
        guest  = booking["guest_name"]
        nights = booking["nights"]

        # Look for the next booking in the same property (caller passes all bookings)
        # — handled by the caller passing next_booking if known
        context = (
            f"[CALENDAR TRIGGER — CLEANER BRIEF]\n\n"
            f"Property: {property_label}\n"
            f"Guest checking out today: {guest}\n"
            f"Checkout date: {booking['checkout'].strftime('%A, %B %d, %Y')}\n"
            f"Length of stay: {nights} night{'s' if nights != 1 else ''}\n\n"
            f"Please generate a full cleaning checklist and turnover brief for {property_label}. "
            f"The host will review and forward to the cleaning crew."
        )
        log.info("[%s] Checkout day for %s — generating cleaner brief", property_label, guest)
        result = _request_draft("cleaner-brief", guest, context)
        if result:
            draft_id, draft = result
            display = f"{guest} checkout — cleaner brief"
            _notify_host(draft_id, display, draft, "cleaner")
            state["notified_cleaner"].append(state_uid)
            _save_state(state)

# ---------------------------------------------------------------------------
# Helper: call bot.js HTTP endpoints
# ---------------------------------------------------------------------------

def _call_bot(endpoint: str, payload: dict):
    try:
        r = requests.post(
            f"{WA_BOT_URL}/{endpoint}",
            json=payload,
            headers=_auth_headers(),
            timeout=10,
        )
        r.raise_for_status()
        log.info("Bot /%s called OK", endpoint)
    except Exception as exc:
        log.warning("Bot /%s call failed: %s", endpoint, exc)

# ---------------------------------------------------------------------------
# Trigger: host notified guest has arrived + asked for guest WhatsApp
# ---------------------------------------------------------------------------

def check_guest_arrival(booking: dict, property_label: str, state: dict, now: datetime):
    state_uid = f"arrival:{booking['uid']}"
    if state_uid in state.get("guest_arrival_notified", []):
        return
    today              = now.date()
    is_checkin_day     = booking["checkin"] == today
    past_checkin_hour  = now.hour >= DEFAULT_CHECKIN_HOUR
    if is_checkin_day and past_checkin_hour:
        log.info("[%s] Check-in day for %s — notifying host", property_label, booking["guest_name"])
        _call_bot("guest-checkin", {
            "booking_uid": booking["uid"],
            "guest_name":  booking["guest_name"],
            "property":    property_label,
            "checkin":     booking["checkin"].isoformat(),
            "checkout":    booking["checkout"].isoformat(),
        })
        state.setdefault("guest_arrival_notified", []).append(state_uid)
        _save_state(state)

# ---------------------------------------------------------------------------
# Trigger: extension offer sent to guest 2h before checkout
# ---------------------------------------------------------------------------

def check_extension_offer(booking: dict, property_label: str, state: dict, now: datetime):
    state_uid = f"extension:{booking['uid']}"
    if state_uid in state.get("extension_offered", []):
        return
    today              = now.date()
    is_checkout_day    = booking["checkout"] == today
    past_offer_hour    = now.hour >= EXTENSION_OFFER_HOUR
    before_checkout    = now.hour < CHECKOUT_BRIEF_HOUR
    if is_checkout_day and past_offer_hour and before_checkout:
        log.info("[%s] Offering extension to %s", property_label, booking["guest_name"])
        _call_bot("offer-extension", {
            "booking_uid": booking["uid"],
            "guest_name":  booking["guest_name"],
            "property":    property_label,
            "checkout":    booking["checkout"].isoformat(),
        })
        state.setdefault("extension_offered", []).append(state_uid)
        _save_state(state)

# ---------------------------------------------------------------------------
# Trigger: post-checkout — review request to guest + cleaner cascade
# ---------------------------------------------------------------------------

def check_post_checkout(booking: dict, property_label: str, state: dict, now: datetime):
    state_uid = f"post_checkout:{booking['uid']}"
    if state_uid in state.get("post_checkout_triggered", []):
        return
    today             = now.date()
    is_checkout_day   = booking["checkout"] == today
    past_checkout     = now.hour >= CHECKOUT_BRIEF_HOUR
    if is_checkout_day and past_checkout:
        log.info("[%s] Post-checkout flow for %s", property_label, booking["guest_name"])
        _call_bot("post-checkout", {
            "booking_uid": booking["uid"],
            "guest_name":  booking["guest_name"],
            "property":    property_label,
        })
        state.setdefault("post_checkout_triggered", []).append(state_uid)
        _save_state(state)

# ---------------------------------------------------------------------------
# Main poll loop
# ---------------------------------------------------------------------------
_MAX_BACKOFF = 3600   # 1 hour


def run():
    if not ICAL_URLS:
        log.error(
            "No iCal URL configured.\n"
            "  Set AIRBNB_ICAL_URL in .env\n"
            "  Find it: Airbnb app → Calendar → Availability settings → Export Calendar"
        )
        return

    log.info(
        "Calendar watcher started — %d listing(s), polling every %d min",
        len(ICAL_URLS), POLL_MINUTES,
    )
    fail_streak = 0

    while True:
        state = _load_state()
        now   = datetime.now(timezone.utc)
        try:
            for idx, url in enumerate(ICAL_URLS):
                label    = PROPERTY_NAMES[idx] if idx < len(PROPERTY_NAMES) else (
                    f"Property {idx + 1}" if len(ICAL_URLS) > 1 else "your property"
                )
                raw      = _fetch_ical(url)
                bookings = _parse_ical(raw)
                log.info("[%s] %d booking(s) in feed", label, len(bookings))
                for booking in bookings:
                    check_checkin(booking, label, state, now)
                    check_guest_arrival(booking, label, state, now)
                    check_extension_offer(booking, label, state, now)
                    check_cleaner_brief(booking, label, state, now)
                    check_post_checkout(booking, label, state, now)
            fail_streak = 0
        except Exception as exc:
            fail_streak += 1
            backoff = min(POLL_MINUTES * 60 * (2 ** (fail_streak - 1)), _MAX_BACKOFF)
            log.error(
                "Calendar watcher error (streak=%d): %s — backing off %ds",
                fail_streak, exc, backoff,
            )
            time.sleep(backoff)
            continue

        time.sleep(POLL_MINUTES * 60)


if __name__ == "__main__":
    run()
