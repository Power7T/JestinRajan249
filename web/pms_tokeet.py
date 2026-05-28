# © 2024 Jestin Rajan. All rights reserved.
"""
Tokeet PMS adapter.

Auth:   Bearer token (stored as api_key)
Base:   https://api.tokeet.com/v1
Docs:   https://api.tokeet.com/
"""

import logging
import time
from datetime import datetime, date, timezone
from typing import Optional

import requests

from web.pms_base import PMSAdapter, PMSMessage, PMSReservation

log = logging.getLogger(__name__)

_BASE = "https://api.tokeet.com/v1"


class TokeetAdapter(PMSAdapter):
    """
    Tokeet API adapter.
    api_key: Bearer token for Tokeet API authentication.
    """
    SUPPORTS_UPDATE_RESERVATION = True
    SUPPORTS_ADD_NOTE           = True

    def __init__(self, api_key: str, account_id: str = "", base_url: str = ""):
        self._api_key = api_key.strip()
        self._base = base_url.rstrip("/") if base_url else _BASE

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

    # ── headers ──────────────────────────────────────────────────────────

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

    # ── interface ─────────────────────────────────────────────────────────

    def test_connection(self) -> bool:
        try:
            resp = self._req(
                "GET",
                f"{self._base}/app/info",
                headers=self._headers(),
                timeout=10,
            )
            return resp.status_code == 200
        except Exception as exc:
            log.warning("Tokeet test_connection failed: %s", exc)
            return False

    def get_new_messages(self, since: datetime) -> list[PMSMessage]:
        """
        List message threads, find those with recent activity,
        then fetch messages in each thread and filter guest-sent ones.
        """
        since_ts_ms = int(since.timestamp() * 1000)
        messages: list[PMSMessage] = []

        # Fetch threads
        threads: list[dict] = []
        skip = 0
        while True:
            try:
                resp = self._req(
                    "GET",
                    f"{self._base}/communications/threads",
                    headers=self._headers(),
                    params={"limit": 50, "skip": skip},
                    timeout=15,
                )
                resp.raise_for_status()
                data = resp.json()
                batch = data if isinstance(data, list) else data.get("data", data.get("results", []))
                if not batch:
                    break
                threads.extend(batch)
                if len(batch) < 50:
                    break
                skip += 50
            except Exception as exc:
                log.warning("Tokeet fetch threads failed (skip=%d): %s", skip, exc)
                break

        for thread in threads:
            thread_id = thread.get("_id") or thread.get("id", "")
            if not thread_id:
                continue
            # Quick check: skip threads with no recent activity
            last_msg_ts = thread.get("last_message_at") or thread.get("lastMessageAt") or 0
            try:
                last_ts_ms = int(last_msg_ts)
            except (ValueError, TypeError):
                last_ts_ms = 0
            if last_ts_ms and last_ts_ms < since_ts_ms:
                continue

            res_id = str(thread.get("inquiry_id") or thread.get("reservationId") or thread.get("_id", ""))
            renter_name = thread.get("renter_name") or thread.get("guestName") or "Guest"

            try:
                msgs_resp = self._req(
                    "GET",
                    f"{self._base}/communications/threads/{thread_id}/messages",
                    headers=self._headers(),
                    params={"limit": 50},
                    timeout=15,
                )
                if msgs_resp.status_code != 200:
                    continue
                msg_data = msgs_resp.json()
                msg_list = msg_data if isinstance(msg_data, list) else msg_data.get("data", msg_data.get("results", []))
            except Exception as exc:
                log.warning("Tokeet fetch messages for thread %s failed: %s", thread_id, exc)
                continue

            for m in msg_list:
                # Only guest-sent messages (from_renter=True means guest sent it)
                if not m.get("from_renter"):
                    continue
                ts_raw = m.get("timestamp") or m.get("created_at") or 0
                try:
                    # Tokeet timestamps are unix milliseconds
                    ts_ms = int(ts_raw)
                    msg_time = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
                except Exception:
                    continue
                if msg_time <= since:
                    continue
                messages.append(PMSMessage(
                    message_id=str(m.get("_id") or m.get("id", f"tokeet_{thread_id}_{len(messages)}")),
                    reservation_id=res_id,
                    guest_name=renter_name,
                    text=str(m.get("message") or m.get("body") or m.get("text", "")),
                    received_at=msg_time,
                    channel="direct",
                ))

        return messages

    def send_message(self, reservation_id: str, text: str) -> bool:
        """
        Find the thread matching `reservation_id` and post a reply.
        Falls back to posting to the first thread matching inquiry_id.
        """
        try:
            # Try to find thread by reservation/inquiry ID
            resp = self._req(
                "GET",
                f"{self._base}/communications/threads",
                headers=self._headers(),
                params={"inquiry_id": reservation_id, "limit": 1},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            threads = data if isinstance(data, list) else data.get("data", data.get("results", []))

            if not threads:
                # Try by reservation_id field directly
                resp2 = self._req(
                    "GET",
                    f"{self._base}/communications/threads",
                    headers=self._headers(),
                    params={"reservationId": reservation_id, "limit": 1},
                    timeout=10,
                )
                resp2.raise_for_status()
                d2 = resp2.json()
                threads = d2 if isinstance(d2, list) else d2.get("data", d2.get("results", []))

            if not threads:
                log.warning("Tokeet: no thread found for reservation %s", reservation_id)
                return False

            thread_id = threads[0].get("_id") or threads[0].get("id")
            send_resp = self._req(
                "POST",
                f"{self._base}/communications/threads/{thread_id}/messages",
                headers=self._headers(),
                json={"message": text},
                timeout=15,
            )
            send_resp.raise_for_status()
            return True
        except Exception as exc:
            log.error("Tokeet send_message failed for reservation %s: %s", reservation_id, exc)
            return False

    def get_reservations(self, from_date: date, to_date: date) -> list[PMSReservation]:
        start_ts = int(datetime(from_date.year, from_date.month, from_date.day,
                                tzinfo=timezone.utc).timestamp())
        end_ts = int(datetime(to_date.year, to_date.month, to_date.day,
                              tzinfo=timezone.utc).timestamp())
        results: list[PMSReservation] = []
        skip = 0
        while True:
            resp = self._req(
                "GET",
                f"{self._base}/reservations",
                headers=self._headers(),
                params={
                    "start": start_ts,
                    "end": end_ts,
                    "limit": 50,
                    "skip": skip,
                },
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            rows = data if isinstance(data, list) else data.get("data", data.get("results", []))
            if not rows:
                break
            for r in rows:
                checkin_ts = r.get("checkin") or r.get("check_in") or r.get("arrival")
                checkout_ts = r.get("checkout") or r.get("check_out") or r.get("departure")
                results.append(PMSReservation(
                    reservation_id=str(r.get("_id") or r.get("id", "")),
                    confirmation_code=str(r.get("confirmation_code") or r.get("_id") or r.get("id", "")),
                    guest_name=str(r.get("renter_name") or r.get("guest_name") or "Guest"),
                    listing_name=str(r.get("rental_name") or r.get("listing_name") or ""),
                    checkin=_parse_ts(checkin_ts),
                    checkout=_parse_ts(checkout_ts),
                    guests_count=int(r.get("adults") or r.get("guests_count") or 1),
                    status=str(r.get("status", "confirmed")).lower(),
                ))
            if len(rows) < 50:
                break
            skip += 50
        return results

    # ── Phase 3: Agentic actions ─────────────────────────────────────────

    def update_reservation(self, reservation_id: str, changes: dict) -> bool:
        if not reservation_id or not changes:
            return False
        payload: dict = {}
        if "checkout" in changes:
            payload["checkout"] = str(changes["checkout"])
        if "checkin" in changes:
            payload["checkin"] = str(changes["checkin"])
        if "guests_count" in changes:
            payload["adults"] = int(changes["guests_count"])
        if not payload:
            return False
        try:
            resp = requests.put(
                f"{self._base}/v1/reservations/{reservation_id}",
                headers=self._headers(),
                json=payload,
                timeout=15,
            )
            resp.raise_for_status()
            return True
        except Exception as exc:
            log.error("Tokeet update_reservation failed for %s: %s", reservation_id, exc)
            return False

    def add_note(self, reservation_id: str, note: str) -> bool:
        if not reservation_id or not note:
            return False
        try:
            resp = requests.post(
                f"{self._base}/v1/reservations/{reservation_id}/notes",
                headers=self._headers(),
                json={"content": note[:1000]},
                timeout=15,
            )
            resp.raise_for_status()
            return True
        except Exception as exc:
            log.error("Tokeet add_note failed for %s: %s", reservation_id, exc)
            return False


def _parse_ts(val) -> Optional[date]:
    """Parse a unix timestamp (int/float) or ISO date string to a date."""
    if not val:
        return None
    try:
        ts = int(val)
        return datetime.fromtimestamp(ts, tz=timezone.utc).date()
    except (ValueError, TypeError):
        pass
    try:
        return datetime.fromisoformat(str(val)[:10]).date()
    except Exception:
        return None
