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

ROUTER_URL     = f"http://127.0.0.1:{os.getenv('ROUTER_PORT', '7771')}"
WA_NOTIFY_URL  = f"http://127.0.0.1:{os.getenv('WA_BOT_PORT', '7772')}/notify-host"

SEEN_FILE      = pathlib.Path(__file__).parent / "seen_emails.json"

# Airbnb sends notifications from these domains
_AIRBNB_SENDERS = ("noreply@airbnb.com", "@airbnb.com", "@messaging.airbnb.com")

# ---------------------------------------------------------------------------
# Seen-email tracking (avoid processing the same UID twice)
# ---------------------------------------------------------------------------

def _load_seen() -> set:
    if SEEN_FILE.exists():
        return set(json.loads(SEEN_FILE.read_text()))
    return set()


def _save_seen(seen: set):
    SEEN_FILE.write_text(json.dumps(sorted(seen)))

# ---------------------------------------------------------------------------
# IMAP connection
# ---------------------------------------------------------------------------

def _connect() -> imapclient.IMAPClient:
    c = imapclient.IMAPClient(IMAP_HOST, port=IMAP_PORT, ssl=True)
    c.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
    return c


def _fetch_airbnb_emails(c: imapclient.IMAPClient) -> list:
    """Return list of {uid, msg} dicts for unread Airbnb notification emails."""
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
    """Extract readable text from email (prefer plain text, fall back to HTML)."""
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
    """
    Returns {guest_name, guest_message, reply_to} extracted from an Airbnb
    notification email, or None if the content can't be parsed.
    """
    subject = msg.get("Subject", "")
    body    = _get_text(msg)

    # Guest name: try subject first, then body
    m = _NAME_RE.search(subject) or _NAME_RE.search(body)
    guest_name = m.group(1) if m else "Guest"

    # Guest message body
    bm = _BODY_RE.search(body)
    if bm:
        guest_message = bm.group(1).strip()
    else:
        # Fallback: grab a few lines after any "message" keyword
        lines  = [l.strip() for l in body.splitlines() if l.strip()]
        marker = next((i for i, l in enumerate(lines) if "message" in l.lower()), -1)
        if marker >= 0:
            guest_message = "\n".join(lines[marker + 1 : marker + 6])
        else:
            guest_message = body[:500].strip()

    if not guest_message:
        return None

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
# Notify host via WhatsApp for complex drafts
# ---------------------------------------------------------------------------

def notify_host_whatsapp(draft_id: str, guest_name: str, draft: str):
    try:
        requests.post(
            WA_NOTIFY_URL,
            json={"draft_id": draft_id, "guest_name": guest_name, "draft": draft, "channel": "email"},
            timeout=5,
        )
        log.info("Complex draft %s forwarded to WhatsApp bot for host approval", draft_id)
    except Exception as exc:
        log.warning("Could not reach WhatsApp bot: %s", exc)
        log.warning("Pending draft %s stored in router — approve at http://127.0.0.1:%s/pending",
                    draft_id, os.getenv("ROUTER_PORT", "7771"))

# ---------------------------------------------------------------------------
# Process a single parsed email
# ---------------------------------------------------------------------------

def process_message(parsed: dict, original_subject: str):
    try:
        resp = requests.post(
            f"{ROUTER_URL}/classify",
            json={
                "source":     "email",
                "guest_name": parsed["guest_name"],
                "message":    parsed["guest_message"],
                "reply_to":   parsed["reply_to"],
            },
            timeout=30,
        ).json()
    except Exception as exc:
        log.error("Router classify failed: %s", exc)
        return

    draft_id = resp["draft_id"]
    draft    = resp["draft"]
    msg_type = resp["msg_type"]

    if msg_type == "routine":
        send_email_reply(parsed["reply_to"], original_subject, draft)
        # Record as approved in router
        try:
            requests.post(
                f"{ROUTER_URL}/approve",
                json={"draft_id": draft_id, "action": "approve"},
                timeout=5,
            )
        except Exception:
            pass
    else:
        notify_host_whatsapp(draft_id, parsed["guest_name"], draft)

# ---------------------------------------------------------------------------
# Main poll loop
# ---------------------------------------------------------------------------

def run():
    seen = _load_seen()
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
        except KeyboardInterrupt:
            log.info("Email watcher stopped.")
            break
        except Exception as exc:
            log.error("Email watcher error: %s", exc)
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    run()
