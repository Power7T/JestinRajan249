# © 2024 Jestin Rajan. All rights reserved.
"""
Multi-tenant Email Worker.
Adapted from airbnb-host/scripts/email_watcher.py.
Each tenant runs this in its own background thread via worker_manager.py.
"""

import re
import time
import email
import json
import smtplib
import logging
from email.message import EmailMessage
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass
from typing import Optional

import imapclient
from bs4 import BeautifulSoup

from web.db import SessionLocal
from web.models import Draft, ProcessedEmail, ActivityLog, Reservation, AutomationRule, GuestTimelineEvent, IssueTicket
from web.classifier import (
    classify_message, classify_message_with_confidence, extract_context_sources,
    detect_vendor_type, make_draft_id, needs_escalation, build_property_context,
)
from web.crypto import decrypt
from web.workflow import (
    analyze_guest_sentiment,
    automation_rule_decision,
    build_conversation_memory,
    build_thread_key,
    compute_guest_history_score,
    compute_stay_stage,
    draft_policy_conflicts,
)

log = logging.getLogger(__name__)

_AIRBNB_SENDERS = ("noreply@airbnb.com", "@airbnb.com", "@messaging.airbnb.com")
_MSG_MIN_LEN    = 10
_MSG_MAX_LEN    = 3000
_MAX_BACKOFF    = 300

_NAME_RE = re.compile(r"([A-Z][a-zA-Z\-']+)\s+(?:sent you a message|has sent you a message|wrote)", re.IGNORECASE)
_BODY_RE = re.compile(r"(?:sent you a message|wrote:|says:|message from [^:]+:)\s*[\r\n]+(.*?)(?:\n\n|\Z)", re.DOTALL | re.IGNORECASE)


@dataclass
class EmailConfig:
    tenant_id:    str
    imap_host:    str
    imap_port:    int
    smtp_host:    str
    smtp_port:    int
    email_address: str
    email_password: str   # already decrypted
    property_context: str = ""   # injected into Claude system prompt
    escalation_email: str = ""   # host email for human-handoff alerts
    pet_policy: str = ""
    refund_policy: str = ""
    early_checkin_policy: str = ""
    early_checkin_fee: str = ""
    late_checkout_policy: str = ""
    late_checkout_fee: str = ""
    poll_interval: int = 30
    imap_timeout:  int = 20


def _connect(cfg: EmailConfig) -> imapclient.IMAPClient:
    c = imapclient.IMAPClient(cfg.imap_host, port=cfg.imap_port, ssl=True, timeout=cfg.imap_timeout)
    c.login(cfg.email_address, cfg.email_password)
    return c


def _fetch_airbnb_emails(c: imapclient.IMAPClient) -> list:
    c.select_folder("INBOX")
    uids = c.search(["UNSEEN"])
    if not uids:
        return []
    results = []
    for uid, data in c.fetch(uids, ["RFC822"]).items():
        raw = data[b"RFC822"]
        msg = email.message_from_bytes(raw)
        from_hdr = msg.get("From", "").lower()
        if any(s in from_hdr for s in _AIRBNB_SENDERS):
            results.append({"uid": str(uid), "msg": msg})
    return results


def _get_text(msg) -> str:
    plain, html = None, None
    for part in msg.walk():
        ct = part.get_content_type()
        if ct == "text/plain" and plain is None:
            plain = part.get_payload(decode=True).decode("utf-8", errors="replace")
        elif ct == "text/html" and html is None:
            raw_html = part.get_payload(decode=True).decode("utf-8", errors="replace")
            html = BeautifulSoup(raw_html, "html.parser").get_text(separator="\n")
    return plain or html or ""


def _parse_airbnb_email(msg) -> Optional[dict]:
    subject = msg.get("Subject", "")
    body    = _get_text(msg)
    m = _NAME_RE.search(subject) or _NAME_RE.search(body)
    guest_name = m.group(1) if m else "Guest"
    bm = _BODY_RE.search(body)
    if bm:
        guest_message = bm.group(1).strip()
    else:
        lines  = [l.strip() for l in body.splitlines() if l.strip()]
        marker = next((i for i, l in enumerate(lines) if "message" in l.lower()), -1)
        guest_message = "\n".join(lines[marker + 1: marker + 6]) if marker >= 0 else body[:_MSG_MAX_LEN].strip()

    if len(guest_message) < _MSG_MIN_LEN:
        return None
    if len(guest_message) > _MSG_MAX_LEN:
        guest_message = guest_message[:_MSG_MAX_LEN]

    reply_to = msg.get("Reply-To") or msg.get("From") or ""
    return {"guest_name": guest_name, "guest_message": guest_message, "reply_to": reply_to}


def parse_structured_email(
    subject: str,
    from_header: str,
    reply_to: str,
    text_body: str,
    html_body: str = "",
) -> Optional[dict]:
    """
    Reuse the existing Airbnb parsing heuristics for webhook-delivered mail.
    """
    msg = EmailMessage()
    msg["Subject"] = subject or ""
    msg["From"] = from_header or ""
    if reply_to:
        msg["Reply-To"] = reply_to
    plain = (text_body or "").strip()
    html = (html_body or "").strip()
    if plain:
        msg.set_content(plain)
        if html:
            msg.add_alternative(html, subtype="html")
    elif html:
        msg.set_content(BeautifulSoup(html, "html.parser").get_text(separator="\n"))
        msg.add_alternative(html, subtype="html")
    else:
        msg.set_content("")
    return _parse_airbnb_email(msg)


_SMTP_TIMEOUT = 15  # seconds — prevents hanging on unresponsive SMTP servers

def _send_smtp_reply(cfg: EmailConfig, to: str, subject: str, body: str):
    subject = subject if subject.startswith("Re:") else f"Re: {subject}"
    mime = MIMEMultipart("alternative")
    mime["From"]    = cfg.email_address
    mime["To"]      = to
    mime["Subject"] = subject
    mime.attach(MIMEText(body, "plain"))
    with smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=_SMTP_TIMEOUT) as smtp:
        smtp.ehlo()
        smtp.starttls()
        smtp.login(cfg.email_address, cfg.email_password)
        smtp.sendmail(cfg.email_address, to, mime.as_string())


def _load_seen(tenant_id: str) -> set:
    db = SessionLocal()
    try:
        rows = db.query(ProcessedEmail.email_uid).filter_by(tenant_id=tenant_id).all()
        return {r[0] for r in rows}
    finally:
        db.close()


def _mark_seen(tenant_id: str, uids: set):
    if not uids:
        return
    db = SessionLocal()
    try:
        for uid in uids:
            db.add(ProcessedEmail(tenant_id=tenant_id, email_uid=uid))
        db.commit()
    finally:
        db.close()


def _record_timeline_event(
    tenant_id: str,
    reservation: Optional[Reservation],
    event_type: str,
    summary: str,
    *,
    direction: str = "internal",
    body: str = "",
    draft_id: Optional[str] = None,
    automation_rule_id: Optional[int] = None,
):
    db = SessionLocal()
    try:
        db.add(GuestTimelineEvent(
            tenant_id=tenant_id,
            reservation_id=reservation.id if reservation else None,
            draft_id=draft_id,
            automation_rule_id=automation_rule_id,
            guest_name=reservation.guest_name if reservation else None,
            guest_phone=reservation.guest_phone if reservation else None,
            property_name=reservation.listing_name if reservation else None,
            unit_identifier=reservation.unit_identifier if reservation else None,
            channel="email",
            direction=direction,
            event_type=event_type,
            summary=summary,
            body=body or None,
        ))
        db.commit()
    finally:
        db.close()


def _save_draft(
    tenant_id: str,
    draft_id: str,
    parsed: dict,
    msg_type: str,
    vendor_type: Optional[str],
    draft_text: str,
    reservation: Optional[Reservation] = None,
    automation_rule_id: Optional[int] = None,
    confidence: Optional[float] = None,
    context_sources: Optional[list] = None,
    parent_draft_id: Optional[str] = None,
    thread_key: Optional[str] = None,
    guest_message_index: int = 1,
    property_name_snapshot: Optional[str] = None,
    unit_identifier_snapshot: Optional[str] = None,
    auto_send_eligible: bool = False,
    guest_history_score: Optional[float] = None,
    guest_sentiment: Optional[str] = None,
    sentiment_score: Optional[float] = None,
    stay_stage: Optional[str] = None,
    policy_conflicts: Optional[list[str]] = None,
    db_override=None,
):
    db = db_override or SessionLocal()
    owns_session = db_override is None
    try:
        draft = Draft(
            id=draft_id, tenant_id=tenant_id, source="email",
            reservation_id=reservation.id if reservation else None,
            automation_rule_id=automation_rule_id,
            parent_draft_id=parent_draft_id,
            thread_key=thread_key,
            guest_message_index=guest_message_index,
            guest_name=parsed["guest_name"], message=parsed["guest_message"],
            reply_to=parsed["reply_to"], msg_type=msg_type, vendor_type=vendor_type,
            draft=draft_text, status="pending", created_at=datetime.now(timezone.utc),
            property_name_snapshot=property_name_snapshot,
            unit_identifier_snapshot=unit_identifier_snapshot,
            confidence=confidence,
            auto_send_eligible=auto_send_eligible,
            guest_history_score=guest_history_score,
            guest_sentiment=guest_sentiment,
            sentiment_score=sentiment_score,
            stay_stage=stay_stage,
            policy_conflicts_json=json.dumps(policy_conflicts) if policy_conflicts else None,
            context_sources=json.dumps(context_sources) if context_sources else None,
        )
        db.add(draft)
        db.add(ActivityLog(tenant_id=tenant_id, event_type="email_received",
                           message=f"Email from {parsed['guest_name']} — {msg_type}"))
        db.commit()
        if reservation:
            _record_timeline_event(
                tenant_id,
                reservation,
                "guest_message_received",
                f"Email from {parsed['guest_name']}",
                direction="inbound",
                body=parsed["guest_message"],
                draft_id=draft_id,
                automation_rule_id=automation_rule_id,
            )
    finally:
        if owns_session:
            db.close()


def _mark_draft_approved(tenant_id: str, draft_id: str, final_text: str):
    db = SessionLocal()
    try:
        draft = db.query(Draft).filter_by(id=draft_id, tenant_id=tenant_id).first()
        if draft:
            draft.status     = "approved"
            draft.final_text = final_text
            draft.approved_at = datetime.now(timezone.utc)
            if draft.reservation_id:
                reservation = db.query(Reservation).filter_by(id=draft.reservation_id, tenant_id=tenant_id).first()
                if reservation:
                    reservation.last_host_reply_at = draft.approved_at
            db.commit()
    finally:
        db.close()


def _lookup_reservation(tenant_id: str, guest_name: str) -> Optional[str]:
    """
    Try to match the guest name against a recent/upcoming reservation row.
    Returns a context string if found, else None.
    """
    from datetime import date as date_type
    today = datetime.now(timezone.utc).date()
    window_start = today - timedelta(days=7)
    window_end   = today + timedelta(days=90)
    db = SessionLocal()
    try:
        name_parts = guest_name.lower().split()
        rows = db.query(Reservation).filter(
            Reservation.tenant_id == tenant_id,
            Reservation.status == "confirmed",
            Reservation.checkin >= window_start,
            Reservation.checkin <= window_end,
        ).all()
        for r in rows:
            db_name_lower = r.guest_name.lower()
            if any(part in db_name_lower or db_name_lower in part for part in name_parts if len(part) > 2):
                lines = [f"Reservation: {r.confirmation_code}"]
                if r.listing_name:
                    lines.append(f"Property: {r.listing_name}")
                if r.unit_identifier:
                    lines.append(f"Room / unit / property #: {r.unit_identifier}")
                if r.checkin:
                    lines.append(f"Check-in: {r.checkin.strftime('%A, %B %d, %Y')}")
                if r.checkout:
                    lines.append(f"Check-out: {r.checkout.strftime('%A, %B %d, %Y')}")
                if r.nights:
                    lines.append(f"Nights: {r.nights}")
                if r.guests_count:
                    lines.append(f"Guests: {r.guests_count}")
                return "\n".join(lines)
    finally:
        db.close()
    return None


def _lookup_reservation_row(tenant_id: str, guest_name: str) -> Optional[Reservation]:
    today = datetime.now(timezone.utc).date()
    window_start = today - timedelta(days=7)
    window_end = today + timedelta(days=90)
    db = SessionLocal()
    try:
        name_parts = guest_name.lower().split()
        rows = db.query(Reservation).filter(
            Reservation.tenant_id == tenant_id,
            Reservation.status == "confirmed",
            Reservation.checkin >= window_start,
            Reservation.checkin <= window_end,
        ).all()
        for res in rows:
            db_name_lower = res.guest_name.lower()
            if any(part in db_name_lower or db_name_lower in part for part in name_parts if len(part) > 2):
                return res
    finally:
        db.close()
    return None


def _timeline_memory(tenant_id: str, reservation: Optional[Reservation]) -> str:
    if not reservation:
        return ""
    db = SessionLocal()
    try:
        events = (
            db.query(GuestTimelineEvent)
            .filter_by(tenant_id=tenant_id, reservation_id=reservation.id)
            .order_by(GuestTimelineEvent.created_at.desc())
            .limit(8)
            .all()
        )
        return build_conversation_memory(reversed(events), limit=8)
    finally:
        db.close()


def _reservation_context_text(reservation: Reservation, cfg: EmailConfig) -> str:
    lines = [f"Reservation: {reservation.confirmation_code}"]
    if reservation.guest_phone:
        lines.append(f"Guest phone: {reservation.guest_phone}")
    if reservation.listing_name:
        lines.append(f"Property: {reservation.listing_name}")
    if reservation.unit_identifier:
        lines.append(f"Room / unit / property #: {reservation.unit_identifier}")
    if reservation.checkin:
        lines.append(f"Check-in: {reservation.checkin.strftime('%A, %B %d, %Y')}")
    if reservation.checkout:
        lines.append(f"Check-out: {reservation.checkout.strftime('%A, %B %d, %Y')}")
    if reservation.nights:
        lines.append(f"Nights: {reservation.nights}")
    if reservation.guests_count:
        lines.append(f"Guests: {reservation.guests_count}")

    today = datetime.now(timezone.utc).date()
    stay_stage = compute_stay_stage(reservation, today=today)
    if stay_stage:
        lines.append(f"Stay stage: {stay_stage.replace('_', ' ')}")
    if reservation.checkin and reservation.checkout and reservation.nights and reservation.checkin <= today <= reservation.checkout:
        day_of_stay = (today - reservation.checkin).days + 1
        lines.append(f"Guest is on day {day_of_stay} of {reservation.nights} nights.")

    if cfg.early_checkin_policy:
        early_line = f"Early check-in: {cfg.early_checkin_policy}"
        if cfg.early_checkin_fee:
            early_line += f" (fee: {cfg.early_checkin_fee})"
        lines.append(early_line)
    if cfg.late_checkout_policy:
        late_line = f"Late checkout: {cfg.late_checkout_policy}"
        if cfg.late_checkout_fee:
            late_line += f" (fee: {cfg.late_checkout_fee})"
        lines.append(late_line)
    if cfg.pet_policy:
        lines.append(f"Pet policy: {cfg.pet_policy}")
    if cfg.refund_policy:
        lines.append(f"Refund policy: {cfg.refund_policy}")
    return "\n".join(lines)


def _recent_reservation_drafts(tenant_id: str, reservation: Optional[Reservation]) -> list[Draft]:
    if not reservation:
        return []
    db = SessionLocal()
    try:
        return (
            db.query(Draft)
            .filter(Draft.tenant_id == tenant_id, Draft.reservation_id == reservation.id)
            .order_by(Draft.created_at.desc())
            .limit(12)
            .all()
        )
    finally:
        db.close()


def _thread_metadata(
    tenant_id: str,
    reservation: Optional[Reservation],
    parsed: dict,
    *,
    channel: str,
) -> tuple[str, Optional[str], int]:
    thread_key = build_thread_key(
        tenant_id,
        reservation_id=reservation.id if reservation else None,
        reply_to=parsed.get("reply_to", ""),
        guest_name=parsed.get("guest_name", ""),
        channel=channel,
    )
    db = SessionLocal()
    try:
        parent = (
            db.query(Draft)
            .filter(Draft.tenant_id == tenant_id, Draft.thread_key == thread_key)
            .order_by(Draft.created_at.desc())
            .first()
        )
        return thread_key, (parent.id if parent else None), ((parent.guest_message_index + 1) if parent else 1)
    finally:
        db.close()


def _process_message(cfg: EmailConfig, parsed: dict, subject: str, db_override=None):
    guest_msg = parsed["guest_message"]

    # Human handoff: escalation check before anything else
    if needs_escalation(guest_msg):
        draft_id = make_draft_id("email")
        escalation_note = (
            f"[ESCALATION ALERT] This message requires immediate human attention.\n\n"
            f"Guest: {parsed['guest_name']}\n"
            f"Message:\n{guest_msg}"
        )
        _save_draft(cfg.tenant_id, draft_id, parsed, "complex", None, escalation_note)
        log.warning("[%s] Escalation triggered for guest %s", cfg.tenant_id, parsed["guest_name"])
        # Send alert to host with retry queue
        if cfg.escalation_email:
            for attempt, backoff in enumerate((2, 5, 0), start=1):
                try:
                    from web.mailer import send_escalation_alert
                    send_escalation_alert(cfg.escalation_email, parsed["guest_name"], guest_msg)
                    log.info("[%s] Escalation alert sent to %s", cfg.tenant_id, cfg.escalation_email)
                    break
                except Exception as exc:
                    if attempt == 3:
                        log.error("[%s] Escalation alert email failed after 3 attempts: %s", cfg.tenant_id, exc)
                    else:
                        log.warning("[%s] Escalation alert email failed (attempt %d): %s. Retrying in %ds...", 
                                    cfg.tenant_id, attempt, exc, backoff)
                        import time
                        time.sleep(backoff)
        return

    msg_type, confidence, matched_patterns = classify_message_with_confidence(guest_msg)
    vendor_type = detect_vendor_type(guest_msg) if msg_type == "complex" else None
    
    from web import classifier as classifier_mod
    sentiment = classifier_mod.analyze_sentiment_and_intent_llm(cfg.tenant_id, guest_msg)

    reservation = _lookup_reservation_row(cfg.tenant_id, parsed["guest_name"])
    full_context = cfg.property_context
    if reservation:
        full_context = (
            full_context
            + "\n\n<reservation>\n"
            + _reservation_context_text(reservation, cfg)
            + "\n</reservation>"
        ).strip()
        log.info("[%s] Reservation match found for %s", cfg.tenant_id, parsed["guest_name"])
    memory_context = _timeline_memory(cfg.tenant_id, reservation)
    if memory_context:
        full_context = (full_context + "\n\n<recent_guest_history>\n" + memory_context + "\n</recent_guest_history>").strip()

    try:
        from web import classifier as classifier_mod
        draft_text = classifier_mod.generate_draft(
            parsed["guest_name"], guest_msg, msg_type,
            property_context=full_context,
        )
    except RuntimeError as exc:
        log.error("[%s] Draft generation failed: %s — saving as escalation draft", cfg.tenant_id, exc)
        fallback_id = make_draft_id("email")
        fallback_text = (
            f"[AI GENERATION FAILED — MANUAL REPLY REQUIRED]\n\n"
            f"Guest: {parsed['guest_name']}\n"
            f"Original message:\n{guest_msg}\n\n"
            f"Error: {exc}"
        )
        _save_draft(cfg.tenant_id, fallback_id, parsed, "complex", None, fallback_text)
        return

    draft_id = make_draft_id("email")
    thread_key, parent_draft_id, guest_message_index = _thread_metadata(
        cfg.tenant_id,
        reservation,
        parsed,
        channel="email",
    )
    recent_drafts = _recent_reservation_drafts(cfg.tenant_id, reservation)
    guest_history_score = compute_guest_history_score(reservation, recent_drafts)
    stay_stage = compute_stay_stage(reservation)
    policy_conflicts = draft_policy_conflicts(guest_msg, draft_text, cfg)
    auto_send_eligible = (
        msg_type == "routine"
        and confidence >= 0.7
        and sentiment["label"] != "negative"
        and not policy_conflicts
        and guest_history_score >= 0.4
    )
    automation_rule_id = None
    if reservation:
        db = SessionLocal()
        try:
            rules = (
                db.query(AutomationRule)
                .filter_by(tenant_id=cfg.tenant_id, is_active=True)
                .order_by(AutomationRule.priority.asc(), AutomationRule.created_at.asc())
                .all()
            )
            draft_view = {
                "status": "pending",
                "source": "email",
                "channel": "email",
                "msg_type": msg_type,
                "message": parsed["guest_message"],
                "draft": draft_text,
                "listing_name": reservation.listing_name or "",
                "property_name": reservation.listing_name or "",
                "reply_to": parsed["reply_to"],
                "confidence": confidence,
                "guest_history_score": guest_history_score,
                "guest_sentiment": sentiment["label"],
                "sentiment_score": sentiment["score"],
                "stay_stage": stay_stage,
                "policy_conflicts": policy_conflicts,
            }
            for rule in rules:
                conditions = rule.conditions_json or {}
                decision = automation_rule_decision(
                    {
                        "enabled": rule.is_active,
                        "status": "active" if rule.is_active else "disabled",
                        "channels": [rule.channel] if rule.channel != "any" else [],
                        "msg_types": conditions.get("msg_types") or [],
                        "min_confidence": rule.confidence_threshold,
                        "properties": conditions.get("properties") or [],
                        "allow_complex": conditions.get("allow_complex", False),
                        "allow_negative_sentiment": conditions.get("allow_negative_sentiment", False),
                        "min_guest_history_score": conditions.get("min_guest_history_score"),
                        "stay_stages": conditions.get("stay_stages") or [],
                        "requires_approval": (rule.actions_json or {}).get("mode") == "review",
                    },
                    draft_view,
                )
                if decision["should_send"]:
                    automation_rule_id = rule.id
                    auto_send_eligible = True
                    break
        finally:
            db.close()

    from web.models import TenantConfig
    _ctx_sources = matched_patterns[:]
    _db = SessionLocal()
    try:
        _tenant_cfg = _db.query(TenantConfig).filter_by(tenant_id=cfg.tenant_id).first()
        if _tenant_cfg:
            _ctx_sources += extract_context_sources(_tenant_cfg)
    finally:
        _db.close()

    _save_draft(
        cfg.tenant_id,
        draft_id,
        parsed,
        msg_type,
        vendor_type,
        draft_text,
        reservation,
        automation_rule_id,
        confidence=confidence,
        context_sources=_ctx_sources,
        parent_draft_id=parent_draft_id,
        thread_key=thread_key,
        guest_message_index=guest_message_index,
        property_name_snapshot=reservation.listing_name if reservation else None,
        unit_identifier_snapshot=reservation.unit_identifier if reservation else None,
        auto_send_eligible=auto_send_eligible,
        guest_history_score=guest_history_score,
        guest_sentiment=sentiment["label"],
        sentiment_score=sentiment["score"],
        stay_stage=stay_stage,
        policy_conflicts=policy_conflicts,
        db_override=db_override,
    )

    if reservation:
        db = SessionLocal()
        try:
            live_reservation = db.query(Reservation).filter_by(id=reservation.id, tenant_id=cfg.tenant_id).first()
            if live_reservation:
                live_reservation.last_guest_message_at = datetime.now(timezone.utc)
                live_reservation.message_count = (live_reservation.message_count or 0) + 1
                live_reservation.latest_guest_sentiment = sentiment["label"]
                live_reservation.latest_guest_sentiment_score = sentiment["score"]
                db.commit()
        finally:
            db.close()

    if auto_send_eligible and cfg.smtp_host and cfg.email_address and cfg.email_password:
        try:
            _send_smtp_reply(cfg, parsed["reply_to"], subject, draft_text)
            _mark_draft_approved(cfg.tenant_id, draft_id, draft_text)
            if reservation:
                _record_timeline_event(
                    cfg.tenant_id,
                    reservation,
                    "draft_approved",
                    f"Routine email auto-sent to {parsed['guest_name']}",
                    direction="outbound",
                    body=draft_text,
                    draft_id=draft_id,
                    automation_rule_id=automation_rule_id,
                )
            log.info("[%s] Routine reply auto-sent to %s", cfg.tenant_id, parsed["guest_name"])
        except Exception as exc:
            log.error("[%s] SMTP send failed: %s", cfg.tenant_id, exc)
    elif msg_type == "routine":
        if policy_conflicts:
            log.info("[%s] Routine draft %s saved — policy conflict requires review", cfg.tenant_id, draft_id)
        elif sentiment["label"] == "negative":
            log.info("[%s] Routine draft %s saved — negative guest tone requires review", cfg.tenant_id, draft_id)
        else:
            log.info("[%s] Routine draft %s saved — auto-send threshold not met", cfg.tenant_id, draft_id)
    else:
        log.info("[%s] Complex draft %s saved — awaiting host approval on dashboard", cfg.tenant_id, draft_id)


def run_for_tenant(cfg: EmailConfig, stop_flag: "threading.Event"):
    """Main poll loop for one tenant. Runs until stop_flag is set."""
    import threading
    seen       = _load_seen(cfg.tenant_id)
    fail_streak = 0
    log.info("[%s] Email watcher started — %s @ %s", cfg.tenant_id, cfg.email_address, cfg.imap_host)

    while not stop_flag.is_set():
        try:
            c        = _connect(cfg)
            messages = _fetch_airbnb_emails(c)
            new_seen = set()
            for item in messages:
                uid = item["uid"]
                if uid in seen:
                    continue
                parsed = _parse_airbnb_email(item["msg"])
                if parsed:
                    subject = item["msg"].get("Subject", "Airbnb message")
                    _process_message(cfg, parsed, subject)
                new_seen.add(uid)
            seen |= new_seen
            _mark_seen(cfg.tenant_id, new_seen)
            c.logout()
            fail_streak = 0
        except Exception as exc:
            fail_streak += 1
            backoff = min(cfg.poll_interval * (2 ** (fail_streak - 1)), _MAX_BACKOFF)
            log.error("[%s] Email watcher error (streak=%d): %s — backoff %ds",
                      cfg.tenant_id, fail_streak, exc, backoff)
            stop_flag.wait(backoff)
            continue
        stop_flag.wait(cfg.poll_interval)

    log.info("[%s] Email watcher stopped.", cfg.tenant_id)


def _email_config_from_record(cfg) -> Optional[EmailConfig]:
    if not cfg:
        return None
    smtp_host = cfg.smtp_host or (cfg.imap_host.replace("imap.", "smtp.") if cfg.imap_host else "")
    return EmailConfig(
        tenant_id=cfg.tenant_id,
        imap_host=cfg.imap_host or "",
        imap_port=cfg.imap_port,
        smtp_host=smtp_host,
        smtp_port=cfg.smtp_port,
        email_address=cfg.email_address or "",
        email_password=decrypt(cfg.email_password_enc or ""),
        property_context=build_property_context(cfg),
        escalation_email=cfg.escalation_email or cfg.email_address or "",
        pet_policy=cfg.pet_policy or "",
        refund_policy=cfg.refund_policy or "",
        early_checkin_policy=cfg.early_checkin_policy or "",
        early_checkin_fee=cfg.early_checkin_fee or "",
        late_checkout_policy=cfg.late_checkout_policy or "",
        late_checkout_fee=cfg.late_checkout_fee or "",
    )


def make_config_from_db(tenant_id: str, require_imap: bool = True) -> Optional[EmailConfig]:
    """Build EmailConfig from DB for a given tenant."""
    db = SessionLocal()
    try:
        from web.models import TenantConfig
        cfg = db.query(TenantConfig).filter_by(tenant_id=tenant_id).first()
        if not cfg:
            return None
        if require_imap and (cfg.email_ingest_mode == "forwarding" or not cfg.email_address or not cfg.imap_host):
            return None
        return _email_config_from_record(cfg)
    finally:
        db.close()


def process_parsed_email(tenant_id: str, parsed: dict, subject: str) -> bool:
    """
    Process a normalized inbound email payload without requiring IMAP polling.
    """
    cfg = make_config_from_db(tenant_id, require_imap=False)
    if not cfg:
        return False
    _process_message(cfg, parsed, subject)
    return True


def process_parsed_email_with_config(cfg_record, parsed: dict, subject: str, db_session=None) -> bool:
    cfg = _email_config_from_record(cfg_record)
    if not cfg:
        return False
    _process_message(cfg, parsed, subject, db_override=db_session)
    return True
