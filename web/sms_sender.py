# © 2024 Jestin Rajan. All rights reserved.
"""
Twilio SMS — outbound message sender + inbound webhook parser.
Used when sms_mode == 'twilio'.
"""

import logging
import urllib.parse
import urllib.request
import urllib.error
import base64

log = logging.getLogger(__name__)

_TWILIO_API = "https://api.twilio.com/2010-04-01"


def send_sms(account_sid: str, auth_token: str, from_number: str, to_number: str, text: str) -> bool:
    """
    Send an SMS via Twilio REST API.
    Returns True on success, False on failure.
    """
    url = f"{_TWILIO_API}/Accounts/{account_sid}/Messages.json"
    payload = urllib.parse.urlencode({
        "From": from_number,
        "To":   to_number,
        "Body": text,
    }).encode()

    creds = base64.b64encode(f"{account_sid}:{auth_token}".encode()).decode()
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
        log.error("Twilio SMS HTTP %s: %s", e.code, body)
        return False
    except Exception as exc:
        log.error("Twilio SMS send failed: %s", exc)
        return False


def parse_twilio_inbound(form_data: dict) -> dict | None:
    """
    Parse a Twilio inbound SMS webhook (application/x-www-form-urlencoded).
    form_data is the already-parsed dict from FastAPI Form fields.
    Returns {'from': str, 'text': str} or None if invalid.
    """
    from_number = form_data.get("From", "").strip()
    body        = form_data.get("Body", "").strip()
    if not from_number or not body:
        return None
    return {"from": from_number, "text": body}
