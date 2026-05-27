# © 2024 Jestin Rajan. All rights reserved.
"""
Smoobu PMS adapter.

Auth:   Api-Key header
Base:   https://login.smoobu.com/api
Docs:   https://docs.smoobu.com/
"""

import logging
import time
from datetime import datetime, date, timezone
from typing import Optional

import requests

from web.pms_base import PMSAdapter, PMSMessage, PMSReservation

log = logging.getLogger(__name__)

_BASE = "https://login.smoobu.com/api"


class SmoobuAdapter(PMSAdapter):
    """
    Smoobu API adapter.
    api_key: plain Smoobu API key (sent as Api-Key header).
    """

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

    # ── auth headers ──────────────────────────────────────────────────────

    def _headers(self) -> dict:
        return {
            "Api-Key": self._api_key,
            "Content-Type": "application/json",
        }

    # ── interface ─────────────────────────────────────────────────────────

    def test_connection(self) -> bool:
        try:
            resp = self._req(
                "GET",
                f"{self._base}/me",
                headers=self._headers(),
                timeout=10,
            )
            return resp.status_code == 200
        except Exception as exc:
            log.warning("Smoobu test_connection failed: %s", exc)
            return False

    def get_new_messages(self, since: datetime) -> list[PMSMessage]:
        """
        Fetch recent reservations, then for each one retrieve messages
        and filter to those sent after `since`.
        """
        since_str = since.astimezone(timezone.utc).strftime("%Y-%m-%d")
        # Use a wide window for to_date (90 days forward) to catch active reservations
        from datetime import timedelta
        to_str = (since.date() + timedelta(days=90)).isoformat()

        messages: list[PMSMessage] = []
        reservations = self._fetch_reservations_raw(since_str, to_str)

        for res in reservations:
            res_id = str(res.get("id", ""))
            if not res_id:
                continue
            first = res.get("firstname") or ""
            last = res.get("lastname") or ""
            guest_name = f"{first} {last}".strip() or "Guest"

            try:
                msgs_resp = self._req(
                    "GET",
                    f"{self._base}/messages",
                    headers=self._headers(),
                    params={"reservationId": res_id},
                    timeout=15,
                )
                if msgs_resp.status_code != 200:
                    continue
                msg_data = msgs_resp.json()
                msg_list = msg_data.get("messages", []) if isinstance(msg_data, dict) else msg_data
            except Exception as exc:
                log.warning("Smoobu fetch messages for reservation %s failed: %s", res_id, exc)
                continue

            for m in msg_list:
                # Skip host-sent messages
                if m.get("fromHost"):
                    continue
                ts_str = m.get("createdAt") or m.get("created_at") or m.get("date", "")
                try:
                    msg_time = datetime.fromisoformat(str(ts_str).replace("Z", "+00:00"))
                except Exception:
                    continue
                if msg_time.tzinfo is None:
                    msg_time = msg_time.replace(tzinfo=timezone.utc)
                if msg_time <= since:
                    continue
                messages.append(PMSMessage(
                    message_id=str(m.get("id", f"smoobu_{res_id}_{len(messages)}")),
                    reservation_id=res_id,
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
                f"{self._base}/messages",
                headers=self._headers(),
                json={
                    "reservationId": reservation_id,
                    "message": text,
                },
                timeout=15,
            )
            resp.raise_for_status()
            return True
        except Exception as exc:
            log.error("Smoobu send_message failed for reservation %s: %s", reservation_id, exc)
            return False

    def _fetch_reservations_raw(self, from_str: str, to_str: str) -> list[dict]:
        """Fetch raw reservation dicts with pagination."""
        results: list[dict] = []
        page = 1
        while True:
            resp = self._req(
                "GET",
                f"{self._base}/reservations",
                headers=self._headers(),
                params={
                    "from": from_str,
                    "to": to_str,
                    "pageSize": 100,
                    "page": page,
                },
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            batch = data.get("reservations", []) if isinstance(data, dict) else data
            if not batch:
                break
            results.extend(batch)
            total = data.get("total_count", 0) if isinstance(data, dict) else 0
            if total and page * 100 >= total:
                break
            if len(batch) < 100:
                break
            page += 1
        return results

    def get_reservations(self, from_date: date, to_date: date) -> list[PMSReservation]:
        results: list[PMSReservation] = []
        rows = self._fetch_reservations_raw(from_date.isoformat(), to_date.isoformat())
        for r in rows:
            first = r.get("firstname") or ""
            last = r.get("lastname") or ""
            guest_name = f"{first} {last}".strip() or "Guest"
            results.append(PMSReservation(
                reservation_id=str(r.get("id", "")),
                confirmation_code=str(r.get("id", "")),
                guest_name=guest_name,
                listing_name=str(r.get("apartment", {}).get("name") or r.get("property_name", "") if isinstance(r.get("apartment"), dict) else ""),
                checkin=_parse_date(r.get("checkin") or r.get("arrival")),
                checkout=_parse_date(r.get("checkout") or r.get("departure")),
                guests_count=int(r.get("adults") or r.get("persons") or 1),
                status=str(r.get("type") or r.get("status") or "confirmed").lower(),
            ))
        return results


def _parse_date(val) -> Optional[date]:
    if not val:
        return None
    try:
        return datetime.fromisoformat(str(val)[:10]).date()
    except Exception:
        return None
