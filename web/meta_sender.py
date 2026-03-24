# © 2024 Jestin Rajan. All rights reserved.
"""
Meta WhatsApp Cloud API — outbound message sender.
Used when wa_mode == 'meta_cloud' or plan == 'pro'.
"""

import json
import hmac
import hashlib
import logging
import time
import urllib.request
import urllib.error

log = logging.getLogger(__name__)

_GRAPH_API = "https://graph.facebook.com/v18.0"
_RETRYABLE_HTTP_CODES = {429, 500, 502, 503, 504}  # transient errors worth retrying


def send_whatsapp(phone_id: str, token: str, to_phone: str, text: str,
                  max_retries: int = 3) -> bool:
    """
    Send a text message via Meta Cloud API.
    to_phone must be in E.164 format without '+', e.g. '14155550001'.
    Retries up to max_retries times on transient errors with exponential backoff.
    Returns True on success, False on permanent failure.
    """
    to = to_phone.replace("+", "").replace(" ", "").replace("-", "")
    payload = json.dumps({
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": str(text)},
    }).encode()

    for attempt in range(max_retries):
        req = urllib.request.Request(
            f"{_GRAPH_API}/{phone_id}/messages",
            data=payload,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                log.info("Meta WA sent to %s: %s", to, resp.status)
                return True
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace")[:300]
            if e.code in _RETRYABLE_HTTP_CODES and attempt < max_retries - 1:
                wait = 2 ** attempt  # 1s, 2s, 4s
                log.warning("Meta WA HTTP %s (attempt %d/%d), retrying in %ds: %s",
                            e.code, attempt + 1, max_retries, wait, body)
                time.sleep(wait)
                continue
            log.error("Meta WA HTTP %s (permanent): %s", e.code, body)
            return False
        except Exception as exc:
            if attempt < max_retries - 1:
                wait = 2 ** attempt
                log.warning("Meta WA send error (attempt %d/%d), retrying in %ds: %s",
                            attempt + 1, max_retries, wait, exc)
                time.sleep(wait)
                continue
            log.error("Meta WA send failed after %d attempts: %s", max_retries, exc)
            return False
    return False


def verify_webhook(verify_token_stored: str, mode: str, token: str, challenge: str) -> str | None:
    """
    Validate a Meta webhook verification GET request.
    Returns the challenge string if valid, None otherwise.
    """
    if mode == "subscribe" and token == verify_token_stored:
        return challenge
    return None


def verify_request_signature(payload: bytes, signature_header: str, app_secret: str) -> bool:
    """
    Validate the Meta X-Hub-Signature-256 header for webhook POST bodies.
    """
    if not app_secret or not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode(), payload, hashlib.sha256).hexdigest()
    supplied = signature_header.split("=", 1)[1]
    return hmac.compare_digest(expected, supplied)


def extract_inbound(body: dict) -> list[dict]:
    """
    Parse a Meta webhook POST body and return a list of
    {'from': phone_str, 'text': str} dicts (one per inbound text message).
    """
    results = []
    try:
        for entry in body.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                for msg in value.get("messages", []):
                    if msg.get("type") == "text":
                        results.append({
                            "from": msg["from"],
                            "text": msg["text"]["body"],
                        })
    except Exception as exc:
        log.warning("Failed to parse Meta webhook body: %s", exc)
    return results
