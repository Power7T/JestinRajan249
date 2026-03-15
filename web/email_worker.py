# © 2024 Jestin Rajan. All rights reserved.
"""
Multi-tenant Email Worker.
Adapted from airbnb-host/scripts/email_watcher.py.
Each tenant runs this in its own background thread via worker_manager.py.
"""

import re
import time
import email
import smtplib
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timezone
from dataclasses import dataclass
from typing import Optional

import imapclient
from bs4 import BeautifulSoup

from web.db import SessionLocal
from web.models import Draft, ProcessedEmail, ActivityLog
from web.classifier import classify_message, detect_vendor_type, generate_draft, make_draft_id, needs_escalation, build_property_context
from web.crypto import decrypt

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
    anthropic_api_key: str  # already decrypted
    property_context: str = ""   # injected into Claude system prompt
    escalation_email: str = ""   # host email for human-handoff alerts
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


def _send_smtp_reply(cfg: EmailConfig, to: str, subject: str, body: str):
    subject = subject if subject.startswith("Re:") else f"Re: {subject}"
    mime = MIMEMultipart("alternative")
    mime["From"]    = cfg.email_address
    mime["To"]      = to
    mime["Subject"] = subject
    mime.attach(MIMEText(body, "plain"))
    with smtplib.SMTP(cfg.smtp_host, cfg.smtp_port) as smtp:
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


def _save_draft(tenant_id: str, draft_id: str, parsed: dict, msg_type: str, vendor_type: Optional[str], draft_text: str):
    db = SessionLocal()
    try:
        draft = Draft(
            id=draft_id, tenant_id=tenant_id, source="email",
            guest_name=parsed["guest_name"], message=parsed["guest_message"],
            reply_to=parsed["reply_to"], msg_type=msg_type, vendor_type=vendor_type,
            draft=draft_text, status="pending", created_at=datetime.now(timezone.utc),
        )
        db.add(draft)
        db.add(ActivityLog(tenant_id=tenant_id, event_type="email_received",
                           message=f"Email from {parsed['guest_name']} — {msg_type}"))
        db.commit()
    finally:
        db.close()


def _mark_draft_approved(tenant_id: str, draft_id: str, final_text: str):
    db = SessionLocal()
    try:
        draft = db.query(Draft).filter_by(id=draft_id, tenant_id=tenant_id).first()
        if draft:
            draft.status     = "approved"
            draft.final_text = final_text
            draft.approved_at = datetime.now(timezone.utc)
            db.commit()
    finally:
        db.close()


def _process_message(cfg: EmailConfig, parsed: dict, subject: str):
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
        # Send alert to host
        if cfg.escalation_email:
            try:
                from web.mailer import send_escalation_alert
                send_escalation_alert(cfg.escalation_email, parsed["guest_name"], guest_msg)
            except Exception as exc:
                log.error("[%s] Escalation alert email failed: %s", cfg.tenant_id, exc)
        return

    msg_type    = classify_message(guest_msg)
    vendor_type = detect_vendor_type(guest_msg) if msg_type == "complex" else None
    try:
        draft_text = generate_draft(
            cfg.anthropic_api_key, parsed["guest_name"], guest_msg, msg_type,
            property_context=cfg.property_context,
        )
    except RuntimeError as exc:
        log.error("[%s] Draft generation failed: %s", cfg.tenant_id, exc)
        return

    draft_id = make_draft_id("email")
    _save_draft(cfg.tenant_id, draft_id, parsed, msg_type, vendor_type, draft_text)

    if msg_type == "routine":
        try:
            _send_smtp_reply(cfg, parsed["reply_to"], subject, draft_text)
            _mark_draft_approved(cfg.tenant_id, draft_id, draft_text)
            log.info("[%s] Routine reply auto-sent to %s", cfg.tenant_id, parsed["guest_name"])
        except Exception as exc:
            log.error("[%s] SMTP send failed: %s", cfg.tenant_id, exc)
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


def make_config_from_db(tenant_id: str) -> Optional[EmailConfig]:
    """Build EmailConfig from DB for a given tenant. Returns None if not configured."""
    db = SessionLocal()
    try:
        from web.models import TenantConfig
        cfg = db.query(TenantConfig).filter_by(tenant_id=tenant_id).first()
        if not cfg or not cfg.email_address or not cfg.imap_host:
            return None
        return EmailConfig(
            tenant_id=tenant_id,
            imap_host=cfg.imap_host,
            imap_port=cfg.imap_port,
            smtp_host=cfg.smtp_host or cfg.imap_host.replace("imap.", "smtp."),
            smtp_port=cfg.smtp_port,
            email_address=cfg.email_address,
            email_password=decrypt(cfg.email_password_enc or ""),
            anthropic_api_key=decrypt(cfg.anthropic_api_key_enc or ""),
            property_context=build_property_context(cfg),
            escalation_email=cfg.escalation_email or cfg.email_address or "",
        )
    finally:
        db.close()
