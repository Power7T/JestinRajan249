# © 2024 Jestin Rajan. All rights reserved.
"""
Proactive Messaging — auto-send timed guest messages around the stay.

Triggers:
  pre_arrival     — N hours before check-in (default 24h)
  checkin_day     — morning of arrival
  mid_stay        — day N of stay (default day 2)
  checkout        — morning of departure
  review_request  — N days after checkout (default 2 days)

Scheduler runs every 30 minutes, finds reservations due for each trigger,
generates an AI message, and sends it via existing tenant channels.
"""
from __future__ import annotations

import logging
from datetime import datetime, date, time, timedelta, timezone
from typing import Optional

from sqlalchemy.orm import Session

from web.db import SessionLocal
from web.models import (
    ActivityLog,
    ProactiveMessage,
    Reservation,
    Tenant,
    TenantConfig,
)

log = logging.getLogger(__name__)

ALL_TRIGGERS = ("pre_arrival", "checkin_day", "mid_stay", "checkout", "review_request")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _ensure_aware(dt: datetime | date | None) -> Optional[datetime]:
    """Coerce a date/datetime to a tz-aware UTC datetime."""
    if dt is None:
        return None
    if isinstance(dt, datetime):
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    # date → midnight UTC
    return datetime.combine(dt, time.min).replace(tzinfo=timezone.utc)


def _checkin_dt(res: Reservation) -> Optional[datetime]:
    """Best-effort check-in datetime in UTC. Uses 15:00 local-ish if only date given."""
    if not res.checkin:
        return None
    base = _ensure_aware(res.checkin)
    if base:
        # Default check-in 15:00 — tenant timezone is honoured loosely
        return base.replace(hour=15, minute=0)
    return None


def _checkout_dt(res: Reservation) -> Optional[datetime]:
    if not res.checkout:
        return None
    base = _ensure_aware(res.checkout)
    if base:
        return base.replace(hour=11, minute=0)
    return None


def _guest_phone(res: Reservation) -> Optional[str]:
    return (res.guest_phone or "").strip() or None


def _resolve_channel(cfg: TenantConfig) -> Optional[str]:
    """Pick the best available channel for proactive sending."""
    if (cfg.wa_mode or "none") not in ("none", ""):
        return "whatsapp"
    if (cfg.sms_mode or "none") not in ("none", ""):
        return "sms"
    return None


def _flag_field(trigger: str) -> Optional[str]:
    """Map trigger → Reservation column that records 'already sent'."""
    return {
        "pre_arrival":    "pre_arrival_sent",
        "checkin_day":    "checkin_day_msg_sent",
        "mid_stay":       "mid_stay_msg_sent",
        "checkout":       "checkout_msg_sent",
        "review_request": "review_reminder_sent",
    }.get(trigger)


# ── AI message generation ─────────────────────────────────────────────────

_TEMPLATES = {
    "pre_arrival":
        "Generate a friendly pre-arrival message for {guest} arriving tomorrow. "
        "Include: warm welcome, mention check-in time {checkin_time}, ask if they need anything before arrival. "
        "Keep it under 3 short sentences. Use the host's customary tone.",
    "checkin_day":
        "Generate a check-in day message for {guest} arriving today. "
        "Include: excitement they're arriving, gentle check-in instructions, offer of help. "
        "Keep it under 3 short sentences.",
    "mid_stay":
        "Generate a friendly mid-stay check-in message for {guest} who is currently staying. "
        "Ask how everything is going and offer help if needed. Keep it under 2 short sentences. "
        "Do NOT mention any specific issues — be open-ended.",
    "checkout":
        "Generate a checkout reminder for {guest} departing today. "
        "Include: checkout time {checkout_time}, brief checkout instructions, thank them for staying. "
        "Keep it under 3 short sentences.",
    "review_request":
        "Generate a polite review request message for {guest} who stayed recently. "
        "Thank them, ask for an honest review, and (if mentioning a link) say where the review link is. "
        "Keep it under 3 short sentences.",
}


def _generate_message(trigger: str, res: Reservation, cfg: TenantConfig) -> Optional[str]:
    """Call the AI to write the proactive message. Falls back to a sensible template."""
    guest = (res.guest_name or "there").split()[0]
    checkin_time = cfg.check_in_time or "3 PM"
    checkout_time = cfg.check_out_time or "11 AM"
    property_name = res.listing_name or (cfg.property_names or "").split(",")[0].strip() or "our place"

    instruction = _TEMPLATES.get(trigger, "")
    instruction = instruction.format(
        guest=guest,
        checkin_time=checkin_time,
        checkout_time=checkout_time,
    )

    # Build the AI prompt
    try:
        from web.classifier import generate_draft
        property_context = (
            f"Property: {property_name}\n"
            f"Check-in: {checkin_time}\n"
            f"Check-out: {checkout_time}\n"
            + (f"House rules: {cfg.house_rules[:300]}\n" if cfg.house_rules else "")
            + (f"Custom instructions: {cfg.custom_instructions[:300]}\n" if cfg.custom_instructions else "")
        )
        text = generate_draft(
            guest_name=guest,
            message=instruction,
            msg_type="routine",
            property_context=property_context,
            tenant_id=res.tenant_id,
            skill=None,
            history=None,
        )
        if text and text.strip():
            return text.strip()
    except Exception as exc:
        log.warning("[PROACTIVE] AI generation failed for %s: %s — using template fallback", trigger, exc)

    # Fallback templates (used if AI is unavailable / over budget)
    fallback = {
        "pre_arrival":    f"Hi {guest}! We're looking forward to having you tomorrow. Check-in is from {checkin_time}. Let us know if you need anything before you arrive!",
        "checkin_day":    f"Hi {guest}! Today's the day. Check-in is from {checkin_time} — text us anytime if you need help finding the place.",
        "mid_stay":       f"Hi {guest}! Hope you're enjoying your stay so far. Anything you need from us?",
        "checkout":       f"Hi {guest}! Just a reminder that checkout is at {checkout_time} today. Thanks so much for staying with us — safe travels!",
        "review_request": f"Hi {guest}! Hope you had a great stay. If you have a moment, we'd love a review — it really helps us.",
    }
    return fallback.get(trigger)


# ── Scheduling pass: insert pending ProactiveMessage rows ─────────────────

def _schedule_pending_messages(db: Session, cfg: TenantConfig, tenant_id: str,
                                 now: datetime) -> int:
    """Find reservations that need a proactive message and insert them as pending."""
    if not cfg.proactive_enabled:
        return 0

    enabled = {t.strip() for t in (cfg.proactive_triggers or "").split(",") if t.strip()}
    if not enabled:
        return 0

    pre_h    = int(cfg.proactive_pre_arrival_h or 24)
    mid_day  = int(cfg.proactive_mid_stay_day or 2)
    review_d = int(cfg.proactive_review_delay_days or 2)

    inserted = 0
    # Window: reservations checking in within the next 7 days,
    #         checking out within the last 7 days
    window_start = (now - timedelta(days=14)).date()
    window_end   = (now + timedelta(days=7)).date()

    reservations = (
        db.query(Reservation)
        .filter(
            Reservation.tenant_id == tenant_id,
            Reservation.status == "confirmed",
            Reservation.checkin >= window_start,
            Reservation.checkin <= window_end,
        )
        .all()
    )

    for res in reservations:
        if not _guest_phone(res):
            continue
        checkin_dt  = _checkin_dt(res)
        checkout_dt = _checkout_dt(res)
        if not checkin_dt or not checkout_dt:
            continue

        per_trigger_schedule = {
            "pre_arrival":    checkin_dt - timedelta(hours=pre_h),
            "checkin_day":    checkin_dt.replace(hour=9, minute=0),
            "mid_stay":       checkin_dt + timedelta(days=mid_day),
            "checkout":       checkout_dt.replace(hour=9, minute=0),
            "review_request": checkout_dt + timedelta(days=review_d),
        }

        for trigger in ALL_TRIGGERS:
            if trigger not in enabled:
                continue
            scheduled_at = per_trigger_schedule[trigger]
            # Skip events more than 24h in the past (already missed window)
            if (now - scheduled_at).total_seconds() > 86400:
                continue
            # Skip events too far in the future (will be picked up later)
            if (scheduled_at - now).total_seconds() > 86400 * 7:
                continue
            # Dedupe (UniqueConstraint enforces, but we check first)
            exists = (
                db.query(ProactiveMessage)
                .filter_by(reservation_id=res.id, trigger_type=trigger)
                .first()
            )
            if exists:
                continue
            # Also respect the Reservation flag (covers messages sent before this system)
            flag = _flag_field(trigger)
            if flag and getattr(res, flag, False):
                continue
            db.add(ProactiveMessage(
                tenant_id=tenant_id,
                reservation_id=res.id,
                trigger_type=trigger,
                scheduled_at=scheduled_at,
                status="pending",
            ))
            inserted += 1
    if inserted:
        db.commit()
    return inserted


# ── Sending pass: send any pending messages whose time has come ───────────

def _send_due_messages(db: Session, cfg: TenantConfig, tenant_id: str,
                       now: datetime) -> int:
    """Send any pending ProactiveMessage rows whose scheduled_at has passed."""
    from web.app import _send_voice_message  # reuse existing sender

    channel = _resolve_channel(cfg)
    if not channel:
        return 0

    due = (
        db.query(ProactiveMessage)
        .filter(
            ProactiveMessage.tenant_id == tenant_id,
            ProactiveMessage.status == "pending",
            ProactiveMessage.scheduled_at <= now,
        )
        .limit(50)
        .all()
    )

    sent_count = 0
    for pm in due:
        try:
            res = db.query(Reservation).filter_by(id=pm.reservation_id).first() if pm.reservation_id else None
            if not res:
                pm.status = "skipped"
                pm.error_reason = "reservation not found"
                continue

            phone = _guest_phone(res)
            if not phone:
                pm.status = "skipped"
                pm.error_reason = "guest phone missing"
                continue

            # Final stale-window guard: skip if more than 24h past
            if (now - pm.scheduled_at).total_seconds() > 86400:
                pm.status = "skipped"
                pm.error_reason = "stale (more than 24h past scheduled time)"
                continue

            text = _generate_message(pm.trigger_type, res, cfg)
            if not text:
                pm.status = "failed"
                pm.error_reason = "AI generation produced empty text"
                continue

            ok = _send_voice_message(cfg, phone, text, channel)
            if ok:
                pm.status = "sent"
                pm.sent_at = now
                pm.channel = channel
                pm.message_text = text
                sent_count += 1
                # Update Reservation flag so old code paths stay consistent
                flag = _flag_field(pm.trigger_type)
                if flag:
                    setattr(res, flag, True)
                db.add(ActivityLog(
                    tenant_id=tenant_id,
                    event_type="proactive_sent",
                    message=f"Proactive {pm.trigger_type} sent to {res.guest_name}: {text[:80]}",
                ))
            else:
                pm.status = "failed"
                pm.error_reason = "send_voice_message returned False"
        except Exception as exc:
            log.error("[PROACTIVE] Send error for ProactiveMessage %s: %s", pm.id, exc)
            pm.status = "failed"
            pm.error_reason = str(exc)[:240]
    if due:
        db.commit()
    return sent_count


# ── Top-level scheduler job ───────────────────────────────────────────────

def proactive_messaging_job() -> None:
    """
    Top-level entrypoint registered with APScheduler.
    Runs every 30 minutes: schedule new pending messages, send any due ones.
    """
    now = _now()
    total_scheduled = 0
    total_sent = 0
    try:
        with SessionLocal() as db:
            # Find all tenants with proactive enabled
            tenants = (
                db.query(TenantConfig)
                .filter(TenantConfig.proactive_enabled.is_(True))
                .all()
            )
            for cfg in tenants:
                try:
                    total_scheduled += _schedule_pending_messages(db, cfg, cfg.tenant_id, now)
                    total_sent      += _send_due_messages(db, cfg, cfg.tenant_id, now)
                except Exception as exc:
                    log.error("[PROACTIVE] Tenant %s failed: %s", cfg.tenant_id, exc, exc_info=True)
                    db.rollback()
            if total_scheduled or total_sent:
                log.info("[PROACTIVE] job complete: scheduled=%d sent=%d", total_scheduled, total_sent)
    except Exception as exc:
        log.error("[PROACTIVE] job error: %s", exc, exc_info=True)
