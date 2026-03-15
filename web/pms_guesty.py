# © 2024 Jestin Rajan. All rights reserved.
"""
Guesty PMS adapter.

Credentials: api_key stores "client_id|||client_secret"
Auth:        OAuth2 client_credentials → POST /oauth2/token
Docs:        https://open-api.guesty.com/
"""

import logging
from datetime import datetime, date, timezone
from typing import Optional

import requests

from web.pms_base import PMSAdapter, PMSMessage, PMSReservation

log = logging.getLogger(__name__)

_BASE = "https://open-api.guesty.com"
_TOKEN_URL = f"{_BASE}/oauth2/token"
_CONV_URL  = f"{_BASE}/v1/conversations"
_RES_URL   = f"{_BASE}/v1/reservations"


class GuestyAdapter(PMSAdapter):
    """
    Guesty Open API adapter.
    api_key format: "<client_id>|||<client_secret>"
    """

    def __init__(self, api_key: str, account_id: str = "", base_url: str = ""):
        parts = api_key.split("|||", 1)
        self._client_id     = parts[0].strip()
        self._client_secret = parts[1].strip() if len(parts) > 1 else ""
        self._base = base_url.rstrip("/") if base_url else _BASE
        self._token: Optional[str] = None
        self._token_expires: Optional[datetime] = None

    # ── auth ──────────────────────────────────────────────────────────────

    def _get_token(self) -> str:
        now = datetime.now(timezone.utc)
        if self._token and self._token_expires and now < self._token_expires:
            return self._token
        resp = requests.post(
            f"{self._base}/oauth2/token",
            json={
                "grant_type":    "client_credentials",
                "client_id":     self._client_id,
                "client_secret": self._client_secret,
                "audience":      "https://open-api.guesty.com",
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        self._token = data["access_token"]
        expires_in = data.get("expires_in", 3600)
        self._token_expires = datetime.fromtimestamp(
            now.timestamp() + expires_in - 60, tz=timezone.utc
        )
        return self._token

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._get_token()}"}

    # ── interface ─────────────────────────────────────────────────────────

    def test_connection(self) -> bool:
        try:
            self._get_token()
            resp = requests.get(
                f"{self._base}/v1/reservations",
                headers=self._headers(),
                params={"limit": 1},
                timeout=10,
            )
            return resp.status_code == 200
        except Exception as exc:
            log.warning("Guesty test_connection failed: %s", exc)
            return False

    def get_new_messages(self, since: datetime) -> list[PMSMessage]:
        since_iso = since.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        messages: list[PMSMessage] = []
        skip = 0
        while True:
            resp = requests.get(
                f"{self._base}/v1/conversations",
                headers=self._headers(),
                params={"createdAt[gte]": since_iso, "limit": 50, "skip": skip},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            conversations = data.get("results") or data.get("data") or []
            if not conversations:
                break
            for conv in conversations:
                conv_id = conv.get("_id") or conv.get("id", "")
                res_id  = conv.get("reservationId") or conv.get("reservation_id", "")
                # Fetch individual messages in the conversation
                msgs_resp = requests.get(
                    f"{self._base}/v1/conversations/{conv_id}/messages",
                    headers=self._headers(),
                    params={"limit": 50},
                    timeout=15,
                )
                if msgs_resp.status_code != 200:
                    continue
                for m in (msgs_resp.json().get("results") or []):
                    # Skip messages sent by the host (direction == "out")
                    if m.get("direction") == "out" or m.get("type") == "host_to_guest":
                        continue
                    msg_time_str = m.get("createdAt") or m.get("created_at", "")
                    try:
                        msg_time = datetime.fromisoformat(
                            msg_time_str.replace("Z", "+00:00")
                        )
                    except Exception:
                        msg_time = since
                    if msg_time <= since:
                        continue
                    guest = (conv.get("guestName") or
                             conv.get("guest", {}).get("fullName", "Guest"))
                    messages.append(PMSMessage(
                        message_id=m.get("_id") or m.get("id", conv_id),
                        reservation_id=res_id,
                        guest_name=guest,
                        text=m.get("body") or m.get("text", ""),
                        received_at=msg_time,
                        channel=conv.get("source", "direct"),
                    ))
            if len(conversations) < 50:
                break
            skip += 50
        return messages

    def send_message(self, reservation_id: str, text: str) -> bool:
        # Find the conversation for this reservation
        try:
            resp = requests.get(
                f"{self._base}/v1/conversations",
                headers=self._headers(),
                params={"reservationId": reservation_id, "limit": 1},
                timeout=10,
            )
            resp.raise_for_status()
            convs = resp.json().get("results") or []
            if not convs:
                log.warning("Guesty: no conversation found for reservation %s", reservation_id)
                return False
            conv_id = convs[0].get("_id") or convs[0].get("id")
            send_resp = requests.post(
                f"{self._base}/v1/conversations/{conv_id}/messages",
                headers=self._headers(),
                json={"body": text, "type": "host_to_guest"},
                timeout=15,
            )
            send_resp.raise_for_status()
            return True
        except Exception as exc:
            log.error("Guesty send_message failed for reservation %s: %s", reservation_id, exc)
            return False

    def get_reservations(self, from_date: date, to_date: date) -> list[PMSReservation]:
        results: list[PMSReservation] = []
        skip = 0
        while True:
            resp = requests.get(
                f"{self._base}/v1/reservations",
                headers=self._headers(),
                params={
                    "checkIn[gte]":  from_date.isoformat(),
                    "checkIn[lte]":  to_date.isoformat(),
                    "limit": 50,
                    "skip": skip,
                },
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            rows = data.get("results") or data.get("data") or []
            if not rows:
                break
            for r in rows:
                checkin  = _parse_date(r.get("checkIn") or r.get("check_in"))
                checkout = _parse_date(r.get("checkOut") or r.get("check_out"))
                results.append(PMSReservation(
                    reservation_id=r.get("_id") or r.get("id", ""),
                    confirmation_code=r.get("confirmationCode") or r.get("_id", ""),
                    guest_name=(r.get("guest", {}) or {}).get("fullName") or "Guest",
                    listing_name=(r.get("listing", {}) or {}).get("title", ""),
                    checkin=checkin,
                    checkout=checkout,
                    guests_count=r.get("guestsCount") or r.get("guests_count") or 1,
                    status=r.get("status", "confirmed").lower(),
                ))
            if len(rows) < 50:
                break
            skip += 50
        return results


def _parse_date(val) -> Optional[date]:
    if not val:
        return None
    try:
        return datetime.fromisoformat(str(val)[:10]).date()
    except Exception:
        return None
