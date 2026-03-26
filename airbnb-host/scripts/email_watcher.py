# © 2024 Jestin Rajan. All rights reserved.
# Licensed under the Airbnb Host AI License Agreement.
# Unauthorized copying, distribution or use is prohibited.
"""
Email Watcher — Option 3
========================
IMAP daemon that monitors the host's inbox for Airbnb guest message
notification emails.

Supports:
  • Gmail (imap.gmail.com)
  • Outlook / Hotmail (outlook.office365.com)
  • Yahoo Mail (imap.mail.yahoo.com)
  • Any standard IMAP server

Flow:
  1. Poll inbox every EMAIL_POLL_SECONDS for unread Airbnb emails
  2. Parse guest name + message body from each email
  3. POST to response_router /classify → get AI draft + type
  4. Routine  → SMTP reply sent immediately
  5. Complex  → draft forwarded to WhatsApp bot → host receives approval prompt

Run:
  python email_watcher.py
  (or via start.sh)
"""

import os
import re
import time
import json
import email
import smtplib
import logging
import pathlib
import requests
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

import imapclient
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
IMAP_HOST      = os.environ["EMAIL_IMAP_HOST"]
IMAP_PORT      = int(os.getenv("EMAIL_IMAP_PORT", "993"))
SMTP_HOST      = os.environ["EMAIL_SMTP_HOST"]
SMTP_PORT      = int(os.getenv("EMAIL_SMTP_PORT", "587"))
EMAIL_ADDRESS  = os.environ["EMAIL_ADDRESS"]
EMAIL_PASSWORD = os.environ["EMAIL_PASSWORD"]
POLL_INTERVAL  = int(os.getenv("EMAIL_POLL_SECONDS", "30"))
IMAP_TIMEOUT   = int(os.getenv("EMAIL_IMAP_TIMEOUT", "20"))   # seconds before giving up on hang

ROUTER_URL      = f"http://127.0.0.1:{os.getenv('ROUTER_PORT', '7771')}"
WA_NOTIFY_URL   = f"http://127.0.0.1:{os.getenv('WA_BOT_PORT', '7772')}/notify-host"
INTERNAL_TOKEN  = os.getenv("INTERNAL_TOKEN", "")

SEEN_FILE       = pathlib.Path(__file__).parent / "seen_emails.json"
HB_EMAIL_FILE   = pathlib.Path(__file__).parent / "heartbeat_email.json"

_AIRBNB_SENDERS = ("noreply@airbnb.com", "@airbnb.com", "@messaging.airbnb.com")
_poll_count     = 0  # module-level counter for heartbeat

# Message length sanity bounds (characters)
_MSG_MIN_LEN = 10
_MSG_MAX_LEN = 3000

# ---------------------------------------------------------------------------
# Auth header
# ---------------------------------------------------------------------------

def _auth_headers() -> dict:
    if INTERNAL_TOKEN:
        return {"X-Internal-Token": INTERNAL_TOKEN}
    return {}

# ---------------------------------------------------------------------------
# Seen-email tracking
# ---------------------------------------------------------------------------

def _load_seen() -> set:
    if SEEN_FILE.exists():
        try:
            return set(json.loads(SEEN_FILE.read_text()))
        except (json.JSONDecodeError, OSError):
            log.warning("seen_emails.json unreadable — starting fresh")
    return set()


def _save_seen(seen: set):
    tmp = SEEN_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(sorted(seen)))
    tmp.replace(SEEN_FILE)   # atomic


def _write_heartbeat(last_email: str = ""):
    """Atomically write heartbeat after each successful poll cycle."""
    global _poll_count
    _poll_count += 1
    data = {
        "ts":         time.time(),
        "pid":        os.getpid(),
        "polls":      _poll_count,
        "last_email": last_email,
        "status":     "ok",
    }
    tmp = HB_EMAIL_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data))
    tmp.replace(HB_EMAIL_FILE)   # atomic on POSIX

# ---------------------------------------------------------------------------
# IMAP connection — with socket timeout to prevent hangs
# ---------------------------------------------------------------------------

def _connect() -> imapclient.IMAPClient:
    c = imapclient.IMAPClient(
        IMAP_HOST,
        port=IMAP_PORT,
        ssl=True,
        timeout=IMAP_TIMEOUT,
    )
    c.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
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

# ---------------------------------------------------------------------------
# Email body parsing
# ---------------------------------------------------------------------------
_NAME_RE = re.compile(
    r"([A-Z][a-zA-Z\-']+)\s+(?:sent you a message|has sent you a message|wrote)",
    re.IGNORECASE,
)
_BODY_RE = re.compile(
    r"(?:sent you a message|wrote:|says:|message from [^:]+:)\s*[\r\n]+(.*?)(?:\n\n|\Z)",
    re.DOTALL | re.IGNORECASE,
)


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


def parse_airbnb_email(msg) -> dict | None:
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
        if marker >= 0:
            guest_message = "\n".join(lines[marker + 1 : marker + 6])
        else:
            guest_message = body[:_MSG_MAX_LEN].strip()

    # Sanity-check the extracted message length
    if len(guest_message) < _MSG_MIN_LEN:
        log.warning("Parsed message too short (%d chars) — skipping", len(guest_message))
        return None
    if len(guest_message) > _MSG_MAX_LEN:
        guest_message = guest_message[:_MSG_MAX_LEN]

    reply_to = msg.get("Reply-To") or msg.get("From") or ""
    return {
        "guest_name":    guest_name,
        "guest_message": guest_message,
        "reply_to":      reply_to,
    }

# ---------------------------------------------------------------------------
# SMTP reply
# ---------------------------------------------------------------------------

def send_email_reply(to: str, original_subject: str, body: str):
    subject = original_subject if original_subject.startswith("Re:") else f"Re: {original_subject}"
    mime = MIMEMultipart("alternative")
    mime["From"]    = EMAIL_ADDRESS
    mime["To"]      = to
    mime["Subject"] = subject
    mime.attach(MIMEText(body, "plain"))

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as smtp:
        smtp.ehlo()
        smtp.starttls()
        smtp.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
        smtp.sendmail(EMAIL_ADDRESS, to, mime.as_string())
    log.info("Email reply sent to %s", to)

# ---------------------------------------------------------------------------
# Router requests with exponential-backoff retry
# ---------------------------------------------------------------------------
_RETRY_DELAYS = [2, 4, 8]


def _post_with_retry(url: str, payload: dict, timeout: int = 30) -> dict:
    last_exc = None
    for attempt, delay in enumerate(zip(range(len(_RETRY_DELAYS)), _RETRY_DELAYS), 1):
        try:
            r = requests.post(url, json=payload, headers=_auth_headers(), timeout=timeout)
            r.raise_for_status()
            return r.json()
        except Exception as exc:
            last_exc = exc
            _, wait = delay
            log.warning("Request to %s attempt %d failed: %s — retrying in %ds", url, attempt, exc, wait)
            time.sleep(wait)
    raise RuntimeError(f"All retries exhausted for {url}: {last_exc}")

# ---------------------------------------------------------------------------
# Notify host via WhatsApp for complex drafts
# ---------------------------------------------------------------------------

def notify_host_whatsapp(draft_id: str, guest_name: str, draft: str):
    try:
        _post_with_retry(
            WA_NOTIFY_URL,
            {"draft_id": draft_id, "guest_name": guest_name, "draft": draft, "channel": "email"},
            timeout=5,
        )
        log.info("Complex draft %s forwarded to WhatsApp bot", draft_id)
    except Exception as exc:
        log.warning("Could not reach WhatsApp bot: %s", exc)
        log.warning("Draft %s is stored in router — approve at %s/pending", draft_id, ROUTER_URL)

# ---------------------------------------------------------------------------
# Process a single parsed email
# ---------------------------------------------------------------------------

def process_message(parsed: dict, original_subject: str):
    try:
        resp = _post_with_retry(
            f"{ROUTER_URL}/classify",
            {
                "source":     "email",
                "guest_name": parsed["guest_name"],
                "message":    parsed["guest_message"],
                "reply_to":   parsed["reply_to"],
            },
        )
    except Exception as exc:
        log.error("Router classify failed after retries: %s — message not processed", exc)
        return

    draft_id = resp["draft_id"]
    draft    = resp["draft"]
    msg_type = resp["msg_type"]

    if msg_type == "routine":
        try:
            send_email_reply(parsed["reply_to"], original_subject, draft)
        except Exception as exc:
            log.error("SMTP send failed: %s", exc)
            return
        try:
            _post_with_retry(f"{ROUTER_URL}/approve",
                             {"draft_id": draft_id, "action": "approve"}, timeout=5)
        except Exception:
            pass  # non-critical; draft already sent
    else:
        notify_host_whatsapp(draft_id, parsed["guest_name"], draft)

# ---------------------------------------------------------------------------
# Main poll loop
# ---------------------------------------------------------------------------
_MAX_BACKOFF = 300   # cap retry sleep at 5 minutes


def run():
    seen           = _load_seen()
    fail_streak    = 0
    log.info("Email watcher started — %s @ %s, polling every %ss",
             EMAIL_ADDRESS, IMAP_HOST, POLL_INTERVAL)
    while True:
        try:
            c        = _connect()
            messages = _fetch_airbnb_emails(c)
            new_seen = set()
            for item in messages:
                uid = item["uid"]
                if uid in seen:
                    continue
                parsed = parse_airbnb_email(item["msg"])
                if parsed:
                    subject = item["msg"].get("Subject", "Airbnb message")
                    log.info("New Airbnb message from %s", parsed["guest_name"])
                    process_message(parsed, subject)
                new_seen.add(uid)
            seen |= new_seen
            if new_seen:
                _save_seen(seen)
            c.logout()
            fail_streak = 0   # reset on success
            # Write heartbeat after successful poll
            last_email = item["msg"].get("Subject", "") if messages else ""
            _write_heartbeat(last_email=last_email)
        except KeyboardInterrupt:
            log.info("Email watcher stopped.")
            break
        except Exception as exc:
            fail_streak += 1
            backoff = min(POLL_INTERVAL * (2 ** (fail_streak - 1)), _MAX_BACKOFF)
            log.error("Email watcher error (streak=%d): %s — backing off %ds", fail_streak, exc, backoff)
            time.sleep(backoff)
            continue
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    run()
