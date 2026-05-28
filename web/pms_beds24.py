# © 2024 Jestin Rajan. All rights reserved.
"""
Beds24 PMS adapter (API v2).

Credentials: api_key stores the Beds24 refresh_token.
Auth:        POST https://api.beds24.com/v2/authentication/token
             → exchange refresh_token for a short-lived `token`
             → pass `token` header on every subsequent request.
Docs:        https://beds24.com/api2.html
"""

import logging
import time
from datetime import datetime, date, timezone
from typing import Optional

import requests

from web.pms_base import PMSAdapter, PMSMessage, PMSReservation

log = logging.getLogger(__name__)

_BASE = "https://api.beds24.com/v2"


class Beds24Adapter(PMSAdapter):
    """
    Beds24 API v2 adapter.
    api_key: Beds24 refresh_token (long-lived credential).
    """
    SUPPORTS_UPDATE_RESERVATION = True
    SUPPORTS_ADD_NOTE           = True

    def __init__(self, api_key: str, account_id: str = "", base_url: str = ""):
        self._refresh_token = api_key.strip()
        self._base = base_url.rstrip("/") if base_url else _BASE
        self._token: Optional[str] = None
        self._token_expires: Optional[datetime] = None

    # ── retry helper ─────────────────────────────────────────────────────

    def _req(self, method: str, url: str, **kwargs) -> requests.Response:
        """Make an HTTP request, retrying up to 2 times on network/5xx errors."""
        last_exc: Exception = RuntimeError("no attempts made")
        for attempt in range(3):
            try:
                resp = requests.request(method, url, **kwargs)
                if resp.status_code < 500:
                    return resp
                if attempt < 2:
                    time.sleep(1.5 ** attempt)
                    continue
                return resp
            except requests.exceptions.ConnectionError as exc:
                last_exc = exc
                if attempt < 2:
                    time.sleep(1.5 ** attempt)
            except requests.exceptions.Timeout as exc:
                last_exc = exc
                if attempt < 2:
                    time.sleep(1.5 ** attempt)
        raise last_exc

    # ── auth ──────────────────────────────────────────────────────────────

    def _get_token(self) -> str:
        now = datetime.now(timezone.utc)
        if self._token and self._token_expires and now < self._token_expires:
            return self._token
        resp = self._req(
            "POST",
            f"{self._base}/authentication/token",
            json={"refreshToken": self._refresh_token},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        self._token = data.get("token") or data.get("access_token", "")
        # Beds24 tokens last 24 hours; refresh 60s early
        expires_in = data.get("expiresIn") or data.get("expires_in") or 86400
        self._token_expires = datetime.fromtimestamp(
            now.timestamp() + int(expires_in) - 60, tz=timezone.utc
        )
        return self._token

    def _headers(self) -> dict:
        return {
            "token": self._get_token(),
            "Content-Type": "application/json",
        }

    # ── interface ─────────────────────────────────────────────────────────

    def test_connection(self) -> bool:
        try:
            resp = self._req(
                "GET",
                f"{self._base}/settings/account",
                headers=self._headers(),
                timeout=10,
            )
            return resp.status_code == 200
        except Exception as exc:
            log.warning("Beds24 test_connection failed: %s", exc)
            return False

    def get_new_messages(self, since: datetime) -> list[PMSMessage]:
        """
        Fetch bookings modified since `since`, then retrieve each booking's
        messages and filter those sent after `since`.
        """
        since_str = since.astimezone(timezone.utc).strftime("%Y-%m-%d")
        messages: list[PMSMessage] = []

        # Collect recently modified bookings
        bookings: list[dict] = []
        page = 0
        while True:
            resp = self._req(
                "GET",
                f"{self._base}/bookings",
                headers=self._headers(),
                params={
                    "modifiedFrom": since_str,
                    "pageSize": 50,
                    "pageNumber": page,
                },
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            batch = data if isinstance(data, list) else data.get("data", data.get("bookings", []))
            if not batch:
                break
            bookings.extend(batch)
            if len(batch) < 50:
                break
            page += 1

        # For each booking, fetch messages and filter by timestamp
        for booking in bookings:
            booking_id = str(booking.get("id", ""))
            if not booking_id:
                continue
            guest_first = booking.get("guestFirstName") or ""
            guest_last = booking.get("guestLastName") or ""
            guest_name = f"{guest_first} {guest_last}".strip() or booking.get("guestId", "Guest")
            listing = booking.get("propertyName") or booking.get("roomName") or ""

            try:
                msgs_resp = self._req(
                    "GET",
                    f"{self._base}/bookings/messages",
                    headers=self._headers(),
                    params={"bookingId": booking_id},
                    timeout=15,
                )
                if msgs_resp.status_code != 200:
                    continue
                msgs_data = msgs_resp.json()
                msg_list = msgs_data if isinstance(msgs_data, list) else msgs_data.get("data", msgs_data.get("messages", []))
            except Exception as exc:
                log.warning("Beds24 fetch messages for booking %s failed: %s", booking_id, exc)
                continue

            for m in msg_list:
                # Skip host-sent messages
                if m.get("isHost") or m.get("fromHost"):
                    continue
                ts_str = m.get("createdAt") or m.get("date") or m.get("timestamp", "")
                try:
                    msg_time = datetime.fromisoformat(str(ts_str).replace("Z", "+00:00"))
                except Exception:
                    continue
                if msg_time.tzinfo is None:
                    msg_time = msg_time.replace(tzinfo=timezone.utc)
                if msg_time <= since:
                    continue
                messages.append(PMSMessage(
                    message_id=str(m.get("id", f"b24_{booking_id}_{len(messages)}")),
                    reservation_id=booking_id,
                    guest_name=guest_name,
                    text=str(m.get("message") or m.get("text") or m.get("body", "")),
                    received_at=msg_time,
                    channel="direct",
                ))

        return messages

    def send_message(self, reservation_id: str, text: str) -> bool:
        try:
            resp = self._req(
                "POST",
                f"{self._base}/bookings/messages",
                headers=self._headers(),
                json={
                    "bookingId": reservation_id,
                    "message": text,
                    "isHost": True,
                },
                timeout=15,
            )
            resp.raise_for_status()
            return True
        except Exception as exc:
            log.error("Beds24 send_message failed for reservation %s: %s", reservation_id, exc)
            return False

    def get_reservations(self, from_date: date, to_date: date) -> list[PMSReservation]:
        results: list[PMSReservation] = []
        page = 0
        while True:
            resp = self._req(
                "GET",
                f"{self._base}/bookings",
                headers=self._headers(),
                params={
                    "arrivalFrom": from_date.isoformat(),
                    "arrivalTo": to_date.isoformat(),
                    "pageSize": 50,
                    "pageNumber": page,
                },
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            rows = data if isinstance(data, list) else data.get("data", data.get("bookings", []))
            if not rows:
                break
            for r in rows:
                guest_first = r.get("guestFirstName") or ""
                guest_last = r.get("guestLastName") or ""
                guest_name = f"{guest_first} {guest_last}".strip() or str(r.get("guestId", "Guest"))
                listing = r.get("propertyName") or r.get("roomName") or ""
                results.append(PMSReservation(
                    reservation_id=str(r.get("id", "")),
                    confirmation_code=str(r.get("id", "")),
                    guest_name=guest_name,
                    listing_name=listing,
                    checkin=_parse_date(r.get("arrival")),
                    checkout=_parse_date(r.get("departure")),
                    guests_count=int(r.get("numberOfGuests") or 1),
                    status=str(r.get("status", "confirmed")).lower(),
                ))
            if len(rows) < 50:
                break
            page += 1
        return results

    # ── Phase 3: Agentic actions ─────────────────────────────────────────

    def update_reservation(self, reservation_id: str, changes: dict) -> bool:
        if not reservation_id or not changes:
            return False
        payload: dict = {"id": reservation_id}
        if "checkout" in changes:
            payload["departure"] = str(changes["checkout"])
        if "checkin" in changes:
            payload["arrival"] = str(changes["checkin"])
        if "guests_count" in changes:
            payload["numAdult"] = int(changes["guests_count"])
        if "checkout_time" in changes:
            payload["departureTime"] = str(changes["checkout_time"])
        if "checkin_time" in changes:
            payload["arrivalTime"] = str(changes["checkin_time"])
        if len(payload) == 1:
            return False  # only id, no changes
        try:
            resp = requests.post(
                f"{self._base}/v2/bookings",
                headers=self._headers(),
                json=[payload],   # Beds24 expects an array for batch updates
                timeout=15,
            )
            resp.raise_for_status()
            return True
        except Exception as exc:
            log.error("Beds24 update_reservation failed for %s: %s", reservation_id, exc)
            return False

    def add_note(self, reservation_id: str, note: str) -> bool:
        if not reservation_id or not note:
            return False
        try:
            resp = requests.post(
                f"{self._base}/v2/bookings",
                headers=self._headers(),
                json=[{"id": reservation_id, "notes": note[:1000]}],
                timeout=15,
            )
            resp.raise_for_status()
            return True
        except Exception as exc:
            log.error("Beds24 add_note failed for %s: %s", reservation_id, exc)
            return False


def _parse_date(val) -> Optional[date]:
    if not val:
        return None
    try:
        return datetime.fromisoformat(str(val)[:10]).date()
    except Exception:
        return None
