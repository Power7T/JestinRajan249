# © 2024 Jestin Rajan. All rights reserved.
"""
Lodgify PMS adapter.

Credentials: api_key is the Lodgify API key (X-ApiKey header)
Docs:        https://api.lodgify.com/
"""

import logging
from datetime import datetime, date, timezone
from typing import Optional

import requests

from web.pms_base import PMSAdapter, PMSMessage, PMSReservation

log = logging.getLogger(__name__)

_BASE = "https://api.lodgify.com"


class LodgifyAdapter(PMSAdapter):
    """
    Lodgify API v2 adapter.
    api_key: plain Lodgify API key (used as X-ApiKey header)
    """

    def __init__(self, api_key: str, account_id: str = "", base_url: str = ""):
        self._api_key = api_key.strip()
        self._base    = base_url.rstrip("/") if base_url else _BASE

    def _headers(self) -> dict:
        return {
            "X-ApiKey": self._api_key,
            "Accept":   "application/json",
        }

    # ── interface ─────────────────────────────────────────────────────────

    def test_connection(self) -> bool:
        try:
            resp = requests.get(
                f"{self._base}/v2/user",
                headers=self._headers(),
                timeout=10,
            )
            return resp.status_code == 200
        except Exception as exc:
            log.warning("Lodgify test_connection failed: %s", exc)
            return False

    def get_new_messages(self, since: datetime) -> list[PMSMessage]:
        since_iso = since.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        messages: list[PMSMessage] = []
        page = 1
        while True:
            resp = requests.get(
                f"{self._base}/v2/conversations",
                headers=self._headers(),
                params={
                    "created_after": since_iso,
                    "page_size": 50,
                    "page": page,
                },
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            # Lodgify wraps in {"items": [...], "total": N}
            convs = data.get("items") or data if isinstance(data, list) else []
            if not convs:
                break
            for conv in convs:
                conv_id = str(conv.get("id", ""))
                res_id  = str(conv.get("booking_id") or conv.get("reservation_id", ""))
                guest   = conv.get("guest_name") or "Guest"
                channel = conv.get("source") or "direct"
                for m in conv.get("messages") or []:
                    # Skip outgoing messages
                    if m.get("is_sender_host") or m.get("sender_type") == "host":
                        continue
                    created_str = m.get("created_at") or m.get("date", "")
                    try:
                        msg_time = datetime.fromisoformat(
                            created_str.replace("Z", "+00:00")
                        )
                    except Exception:
                        msg_time = since
                    if msg_time <= since:
                        continue
                    messages.append(PMSMessage(
                        message_id=str(m.get("id", conv_id)),
                        reservation_id=res_id,
                        guest_name=guest,
                        text=m.get("content") or m.get("message", ""),
                        received_at=msg_time,
                        channel=channel,
                    ))
            total = data.get("total") or len(convs)
            if page * 50 >= total:
                break
            page += 1
        return messages

    def send_message(self, reservation_id: str, text: str) -> bool:
        try:
            # Find conversation by booking/reservation ID
            resp = requests.get(
                f"{self._base}/v2/conversations",
                headers=self._headers(),
                params={"booking_id": reservation_id, "page_size": 1},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            convs = data.get("items") or (data if isinstance(data, list) else [])
            if not convs:
                log.warning("Lodgify: no conversation for reservation %s", reservation_id)
                return False
            conv_id = convs[0].get("id")
            send_resp = requests.post(
                f"{self._base}/v2/conversations/{conv_id}/reply",
                headers=self._headers(),
                json={"message": text},
                timeout=15,
            )
            send_resp.raise_for_status()
            return True
        except Exception as exc:
            log.error("Lodgify send_message failed for reservation %s: %s", reservation_id, exc)
            return False

    def get_reservations(self, from_date: date, to_date: date) -> list[PMSReservation]:
        results: list[PMSReservation] = []
        page = 1
        while True:
            resp = requests.get(
                f"{self._base}/v2/booking",
                headers=self._headers(),
                params={
                    "date_from": from_date.isoformat(),
                    "date_to":   to_date.isoformat(),
                    "include_items": "true",
                    "page_size": 50,
                    "page": page,
                },
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            rows = data.get("items") or (data if isinstance(data, list) else [])
            if not rows:
                break
            for r in rows:
                results.append(PMSReservation(
                    reservation_id=str(r.get("id", "")),
                    confirmation_code=r.get("booking_reference") or str(r.get("id", "")),
                    guest_name=r.get("guest", {}).get("name") or r.get("guest_name", "Guest"),
                    listing_name=r.get("property_name") or "",
                    checkin=_parse_date(r.get("arrival")),
                    checkout=_parse_date(r.get("departure")),
                    guests_count=r.get("people_count") or 1,
                    status=r.get("status", "confirmed").lower(),
                ))
            total = data.get("total") or len(rows)
            if page * 50 >= total:
                break
            page += 1
        return results


def _parse_date(val) -> Optional[date]:
    if not val:
        return None
    try:
        return datetime.fromisoformat(str(val)[:10]).date()
    except Exception:
        return None
