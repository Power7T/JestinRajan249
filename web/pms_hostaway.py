# © 2024 Jestin Rajan. All rights reserved.
"""
Hostaway PMS adapter.

Credentials: api_key stores "client_id|||client_secret"
Auth:        OAuth2 client_credentials → POST /v1/accessTokens
Docs:        https://api.hostaway.com/
"""

import logging
from datetime import datetime, date, timezone
from typing import Optional

import requests

from web.pms_base import PMSAdapter, PMSMessage, PMSReservation

log = logging.getLogger(__name__)

_BASE = "https://api.hostaway.com"


class HostawayAdapter(PMSAdapter):
    """
    Hostaway API adapter.
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
            f"{self._base}/v1/accessTokens",
            data={
                "grant_type":    "client_credentials",
                "client_id":     self._client_id,
                "client_secret": self._client_secret,
                "scope":         "general",
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
        return {
            "Authorization": f"Bearer {self._get_token()}",
            "Cache-control": "no-cache",
        }

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
            log.warning("Hostaway test_connection failed: %s", exc)
            return False

    def get_new_messages(self, since: datetime) -> list[PMSMessage]:
        since_ts = int(since.astimezone(timezone.utc).timestamp())
        messages: list[PMSMessage] = []
        offset = 0
        while True:
            resp = requests.get(
                f"{self._base}/v1/conversations",
                headers=self._headers(),
                params={
                    "createdOn[gte]": since_ts,
                    "limit": 50,
                    "offset": offset,
                },
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            convs = data.get("result") or []
            if not convs:
                break
            for conv in convs:
                conv_id = conv.get("id", "")
                res_id  = str(conv.get("reservationId") or conv.get("reservation_id", ""))
                guest   = conv.get("guestName") or "Guest"
                # Fetch messages in conversation
                msgs_resp = requests.get(
                    f"{self._base}/v1/conversations/{conv_id}/messages",
                    headers=self._headers(),
                    params={"limit": 50},
                    timeout=15,
                )
                if msgs_resp.status_code != 200:
                    continue
                for m in (msgs_resp.json().get("result") or []):
                    # Skip outbound (host → guest)
                    if m.get("isOutgoing") or m.get("is_outgoing"):
                        continue
                    created_ts = m.get("createdAt") or m.get("created_at") or 0
                    try:
                        msg_time = datetime.fromtimestamp(int(created_ts), tz=timezone.utc)
                    except Exception:
                        msg_time = since
                    if msg_time <= since:
                        continue
                    messages.append(PMSMessage(
                        message_id=str(m.get("id", conv_id)),
                        reservation_id=res_id,
                        guest_name=guest,
                        text=m.get("body") or m.get("message", ""),
                        received_at=msg_time,
                        channel=conv.get("channelName", "direct"),
                    ))
            if len(convs) < 50:
                break
            offset += 50
        return messages

    def send_message(self, reservation_id: str, text: str) -> bool:
        try:
            # Find conversation for the reservation
            resp = requests.get(
                f"{self._base}/v1/conversations",
                headers=self._headers(),
                params={"reservationId": reservation_id, "limit": 1},
                timeout=10,
            )
            resp.raise_for_status()
            convs = resp.json().get("result") or []
            if not convs:
                log.warning("Hostaway: no conversation found for reservation %s", reservation_id)
                return False
            conv_id = convs[0].get("id")
            send_resp = requests.post(
                f"{self._base}/v1/conversations/{conv_id}/messages",
                headers=self._headers(),
                json={"body": text},
                timeout=15,
            )
            send_resp.raise_for_status()
            return True
        except Exception as exc:
            log.error("Hostaway send_message failed for reservation %s: %s", reservation_id, exc)
            return False

    def get_reservations(self, from_date: date, to_date: date) -> list[PMSReservation]:
        results: list[PMSReservation] = []
        offset = 0
        while True:
            resp = requests.get(
                f"{self._base}/v1/reservations",
                headers=self._headers(),
                params={
                    "arrivalStartDate": from_date.isoformat(),
                    "arrivalEndDate":   to_date.isoformat(),
                    "limit":  50,
                    "offset": offset,
                },
                timeout=15,
            )
            resp.raise_for_status()
            rows = resp.json().get("result") or []
            if not rows:
                break
            for r in rows:
                results.append(PMSReservation(
                    reservation_id=str(r.get("id", "")),
                    confirmation_code=r.get("channelReservationId") or str(r.get("id", "")),
                    guest_name=r.get("guestName") or "Guest",
                    listing_name=r.get("listingName") or r.get("listing_name", ""),
                    checkin=_parse_date(r.get("arrivalDate")),
                    checkout=_parse_date(r.get("departureDate")),
                    guests_count=r.get("guestsCount") or 1,
                    status=r.get("status", "confirmed").lower(),
                ))
            if len(rows) < 50:
                break
            offset += 50
        return results


def _parse_date(val) -> Optional[date]:
    if not val:
        return None
    try:
        return datetime.fromisoformat(str(val)[:10]).date()
    except Exception:
        return None
