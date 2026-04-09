# © 2024 Jestin Rajan. All rights reserved.
"""
Twilio SMS — outbound message sender + inbound webhook parser.
Used when sms_mode == 'twilio'.
"""

import logging
import time
import urllib.parse
import urllib.request
import urllib.error
import base64

log = logging.getLogger(__name__)

_TWILIO_API = "https://api.twilio.com/2010-04-01"
_RETRYABLE_HTTP_CODES = {429, 500, 502, 503, 504}


def send_sms(account_sid: str, auth_token: str, from_number: str, to_number: str,
             text: str, max_retries: int = 3) -> bool:
    """
    Send an SMS via Twilio REST API.
    Retries up to max_retries times on transient errors with exponential backoff.
    Returns True on success, False on permanent failure.
    """
    url = f"{_TWILIO_API}/Accounts/{account_sid}/Messages.json"
    payload = urllib.parse.urlencode({
        "From": from_number,
        "To":   to_number,
        "Body": text,
    }).encode()
    creds = base64.b64encode(f"{account_sid}:{auth_token}".encode()).decode()

    for attempt in range(max_retries):
        req = urllib.request.Request(
            url,
            data=payload,
            headers={
                "Authorization": f"Basic {creds}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                log.info("Twilio SMS sent to %s: %s", to_number, resp.status)
                return True
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace")[:300]
            if e.code in _RETRYABLE_HTTP_CODES and attempt < max_retries - 1:
                wait = 2 ** attempt
                log.warning("Twilio SMS HTTP %s (attempt %d/%d), retrying in %ds: %s",
                            e.code, attempt + 1, max_retries, wait, body)
                time.sleep(wait)
                continue
            log.error("Twilio SMS HTTP %s (permanent): %s", e.code, body)
            return False
        except Exception as exc:
            if attempt < max_retries - 1:
                wait = 2 ** attempt
                log.warning("Twilio SMS error (attempt %d/%d), retrying in %ds: %s",
                            attempt + 1, max_retries, wait, exc)
                time.sleep(wait)
                continue
            log.error("Twilio SMS failed after %d attempts: %s", max_retries, exc)
            return False
    return False


def send_whatsapp_twilio(account_sid: str, auth_token: str, from_number: str,
                         to_number: str, text: str, max_retries: int = 3) -> bool:
    """
    Send a WhatsApp message via Twilio (same API as SMS, prefixed with 'whatsapp:').
    from_number: your Twilio WhatsApp sender (e.g. '+14155238886' for sandbox).
    to_number: guest phone in E.164 format ('+' prefix expected).
    """
    # Ensure whatsapp: prefix
    wa_from = from_number if from_number.startswith("whatsapp:") else f"whatsapp:{from_number}"
    to_clean = to_number.replace(" ", "").replace("-", "")
    wa_to = to_clean if to_clean.startswith("whatsapp:") else f"whatsapp:{to_clean}"
    return send_sms(account_sid, auth_token, wa_from, wa_to, text, max_retries)


def parse_twilio_inbound(form_data: dict) -> dict | None:
    """
    Parse a Twilio inbound SMS or WhatsApp webhook (application/x-www-form-urlencoded).
    form_data is the already-parsed dict from FastAPI Form fields.
    Returns {'from': str, 'text': str, 'is_whatsapp': bool} or None if invalid.
    """
    from_number = form_data.get("From", "").strip()
    body        = form_data.get("Body", "").strip()
    if not from_number or not body:
        return None
    is_whatsapp = from_number.startswith("whatsapp:")
    # Strip whatsapp: prefix so phone is in plain E.164 format
    clean_from = from_number.replace("whatsapp:", "")
    return {"from": clean_from, "text": body, "is_whatsapp": is_whatsapp}
