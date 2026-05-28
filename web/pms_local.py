# © 2024 Jestin Rajan. All rights reserved.
"""
LocalPMSAdapter — fallback PMS adapter for hosts with no external PMS.

When a host has no Hostaway / Guesty / iCal-backed external PMS, all
agentic-action writes go directly to HostAI's own Reservation table.
This makes the system fully autonomous for every host regardless of
their tech stack.

Returned by _get_pms_adapter() when no PMSIntegration row exists for
the tenant. The execute_action() pipeline calls this adapter exactly
the same way it calls any other — no special-casing needed.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

log = logging.getLogger(__name__)


class LocalPMSAdapter:
    """
    Writes reservation changes directly to the HostAI database.

    Supported operations:
      update_reservation  — checkout_date, checkin_date, checkout_time,
                            checkin_time, guests_count
      add_note            — stored as a GuestAction result (already done by caller)

    Unsupported (returns False gracefully, caller handles):
      block_dates, send_message, get_new_messages, get_reservations
    """

    SUPPORTS_UPDATE_RESERVATION: bool = True
    SUPPORTS_ADD_NOTE:            bool = True
    SUPPORTS_BLOCK_DATES:         bool = False
    SUPPORTS_AVAILABILITY:        bool = False
    SUPPORTS_CREATE_BOOKING:      bool = False
    IS_LOCAL:                     bool = True   # lets callers identify the fallback

    def __init__(self, db: Session, tenant_id: str) -> None:
        self.db = db
        self.tenant_id = tenant_id

    # ── Required stubs (adapter interface) ────────────────────────────────

    def test_connection(self) -> bool:
        return True

    def get_new_messages(self, since: datetime) -> list:
        return []

    def send_message(self, reservation_id: str, text: str) -> bool:
        # Messages go via WhatsApp / SMS / Web Chat — not via a PMS
        return False

    def get_reservations(self, from_date: date, to_date: date) -> list:
        return []

    # ── Core write operations ──────────────────────────────────────────────

    def update_reservation(self, confirmation_code: str, changes: dict) -> bool:
        """
        Apply `changes` to the Reservation row identified by `confirmation_code`.

        Recognised keys
        ---------------
        checkout_date   str  YYYY-MM-DD   → res.checkout
        checkin_date    str  YYYY-MM-DD   → res.checkin
        checkout_time   str  HH:MM        → res.checkout_time
        checkin_time    str  HH:MM        → res.checkin_time
        guests_count    int               → res.guests_count
        """
        from web.models import Reservation

        res = (
            self.db.query(Reservation)
            .filter_by(tenant_id=self.tenant_id, confirmation_code=confirmation_code)
            .first()
        )
        if not res:
            log.warning("[LocalPMS] Reservation not found: %s / %s", self.tenant_id, confirmation_code)
            return False

        changed = False

        if "checkout_date" in changes and changes["checkout_date"]:
            try:
                new_date = date.fromisoformat(str(changes["checkout_date"]))
                res.checkout = new_date
                changed = True
            except ValueError as exc:
                log.warning("[LocalPMS] Bad checkout_date %s: %s", changes["checkout_date"], exc)

        if "checkin_date" in changes and changes["checkin_date"]:
            try:
                res.checkin = date.fromisoformat(str(changes["checkin_date"]))
                changed = True
            except ValueError as exc:
                log.warning("[LocalPMS] Bad checkin_date %s: %s", changes["checkin_date"], exc)

        if "checkout_time" in changes and changes["checkout_time"]:
            res.checkout_time = str(changes["checkout_time"])[:8]
            changed = True

        if "checkin_time" in changes and changes["checkin_time"]:
            res.checkin_time = str(changes["checkin_time"])[:8]
            changed = True

        if "guests_count" in changes:
            try:
                res.guests_count = max(1, int(changes["guests_count"]))
                changed = True
            except (TypeError, ValueError):
                pass

        # Recompute nights whenever dates change
        if res.checkin and res.checkout:
            try:
                ci = res.checkin if isinstance(res.checkin, date) else res.checkin.date()
                co = res.checkout if isinstance(res.checkout, date) else res.checkout.date()
                res.nights = max(0, (co - ci).days)
            except Exception:
                pass

        if changed:
            self.db.commit()
            log.info("[LocalPMS] Updated reservation %s: %s", confirmation_code, list(changes.keys()))

        return True  # always True for non-date/time fields even if no-op

    def add_note(self, reservation_id: str, note: str) -> bool:
        # Notes are persisted in GuestAction.result_json by the caller — nothing to do here
        return True

    def block_dates(self, listing_id: str, from_date: date, to_date: date, reason: str = "") -> bool:
        log.info("[LocalPMS] block_dates not supported without external PMS")
        return False
