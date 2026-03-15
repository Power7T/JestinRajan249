# © 2024 Jestin Rajan. All rights reserved.
"""
Generic REST PMS adapter.

Allows a host to connect any PMS that has an open REST API.
Configuration is stored as JSON in the `account_id` column:

{
  "inbox_url":       "https://api.mypms.com/messages",
  "send_url":        "https://api.mypms.com/messages/{reservation_id}",
  "reservations_url":"https://api.mypms.com/reservations",
  "auth_header":     "X-Api-Key",
  "message_id_path": "id",
  "reservation_id_path": "reservation_id",
  "guest_name_path": "guest.name",
  "text_path":       "body",
  "received_at_path":"created_at",
  "messages_list_path": "data"
}

The `api_key` field is used as the value of `auth_header`.
JSONPath-style dot notation is supported for nested fields (e.g. "guest.name").
"""

import json
import logging
from datetime import datetime, date, timezone
from typing import Any, Optional

import requests

from web.pms_base import PMSAdapter, PMSMessage, PMSReservation

log = logging.getLogger(__name__)


def _jget(obj: dict, path: str, default=None) -> Any:
    """Simple dot-notation accessor: _jget(d, 'guest.name') → d['guest']['name']."""
    for key in path.split("."):
        if not isinstance(obj, dict):
            return default
        obj = obj.get(key, default)
        if obj is None:
            return default
    return obj


class GenericAdapter(PMSAdapter):
    """
    Configurable REST adapter for any PMS with an open API.
    api_key:    the auth token / API key value
    account_id: JSON config string (see module docstring)
    base_url:   optional base URL override (prepended to relative URLs in config)
    """

    def __init__(self, api_key: str, account_id: str = "", base_url: str = ""):
        self._api_key = api_key.strip()
        self._base    = base_url.rstrip("/") if base_url else ""
        try:
            self._cfg: dict = json.loads(account_id) if account_id else {}
        except json.JSONDecodeError:
            self._cfg = {}

    def _headers(self) -> dict:
        auth_header = self._cfg.get("auth_header", "Authorization")
        val = self._api_key
        if auth_header.lower() == "authorization" and not val.lower().startswith("bearer "):
            val = f"Bearer {val}"
        return {auth_header: val, "Accept": "application/json"}

    def _url(self, key: str, **kwargs) -> str:
        url = self._cfg.get(key, "")
        if url and not url.startswith("http") and self._base:
            url = self._base + "/" + url.lstrip("/")
        for k, v in kwargs.items():
            url = url.replace("{" + k + "}", str(v))
        return url

    # ── interface ─────────────────────────────────────────────────────────

    def test_connection(self) -> bool:
        inbox_url = self._url("inbox_url")
        if not inbox_url:
            return False
        try:
            resp = requests.get(
                inbox_url,
                headers=self._headers(),
                params={"limit": 1, "page_size": 1},
                timeout=10,
            )
            return resp.status_code < 400
        except Exception as exc:
            log.warning("Generic PMS test_connection failed: %s", exc)
            return False

    def get_new_messages(self, since: datetime) -> list[PMSMessage]:
        inbox_url = self._url("inbox_url")
        if not inbox_url:
            return []
        since_iso = since.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        try:
            resp = requests.get(
                inbox_url,
                headers=self._headers(),
                params={"since": since_iso, "created_after": since_iso, "limit": 100},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            log.error("Generic PMS get_new_messages error: %s", exc)
            return []

        list_path = self._cfg.get("messages_list_path", "")
        items = _jget(data, list_path) if list_path else (
            data if isinstance(data, list) else data.get("data") or data.get("results") or []
        )
        if not isinstance(items, list):
            return []

        messages: list[PMSMessage] = []
        for item in items:
            raw_ts = _jget(item, self._cfg.get("received_at_path", "created_at"), "")
            try:
                msg_time = datetime.fromisoformat(str(raw_ts).replace("Z", "+00:00"))
            except Exception:
                msg_time = since
            if msg_time <= since:
                continue
            messages.append(PMSMessage(
                message_id=str(_jget(item, self._cfg.get("message_id_path", "id"), "")),
                reservation_id=str(_jget(item, self._cfg.get("reservation_id_path", "reservation_id"), "")),
                guest_name=str(_jget(item, self._cfg.get("guest_name_path", "guest_name"), "Guest")),
                text=str(_jget(item, self._cfg.get("text_path", "body"), "")),
                received_at=msg_time,
                channel=str(_jget(item, self._cfg.get("channel_path", "channel"), "direct")),
            ))
        return messages

    def send_message(self, reservation_id: str, text: str) -> bool:
        send_url = self._url("send_url", reservation_id=reservation_id)
        if not send_url:
            log.warning("Generic PMS: send_url not configured")
            return False
        try:
            send_body_key = self._cfg.get("send_body_key", "body")
            resp = requests.post(
                send_url,
                headers=self._headers(),
                json={send_body_key: text},
                timeout=15,
            )
            resp.raise_for_status()
            return True
        except Exception as exc:
            log.error("Generic PMS send_message failed for reservation %s: %s", reservation_id, exc)
            return False

    def get_reservations(self, from_date: date, to_date: date) -> list[PMSReservation]:
        res_url = self._url("reservations_url")
        if not res_url:
            return []
        try:
            resp = requests.get(
                res_url,
                headers=self._headers(),
                params={
                    "from": from_date.isoformat(),
                    "to":   to_date.isoformat(),
                    "date_from": from_date.isoformat(),
                    "date_to":   to_date.isoformat(),
                    "limit": 100,
                },
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            log.error("Generic PMS get_reservations error: %s", exc)
            return []

        list_path = self._cfg.get("reservations_list_path", "")
        items = _jget(data, list_path) if list_path else (
            data if isinstance(data, list) else data.get("data") or data.get("results") or []
        )
        if not isinstance(items, list):
            return []

        results: list[PMSReservation] = []
        for r in items:
            checkin  = _parse_date(_jget(r, self._cfg.get("checkin_path",  "check_in")))
            checkout = _parse_date(_jget(r, self._cfg.get("checkout_path", "check_out")))
            results.append(PMSReservation(
                reservation_id=str(_jget(r, self._cfg.get("res_id_path", "id"), "")),
                confirmation_code=str(_jget(r, self._cfg.get("confirmation_path", "confirmation_code"), "")),
                guest_name=str(_jget(r, self._cfg.get("guest_name_path", "guest_name"), "Guest")),
                listing_name=str(_jget(r, self._cfg.get("listing_name_path", "listing_name"), "")),
                checkin=checkin,
                checkout=checkout,
                guests_count=int(_jget(r, self._cfg.get("guests_count_path", "guests_count"), 1) or 1),
                status=str(_jget(r, self._cfg.get("status_path", "status"), "confirmed")).lower(),
            ))
        return results


def _parse_date(val) -> Optional[date]:
    if not val:
        return None
    try:
        return datetime.fromisoformat(str(val)[:10]).date()
    except Exception:
        return None
