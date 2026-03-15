# © 2024 Jestin Rajan. All rights reserved.
"""
Abstract PMS adapter interface.
All PMS adapters (Guesty, Hostaway, Lodgify, Generic) implement PMSAdapter.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, date
from typing import Optional


@dataclass
class PMSMessage:
    """A guest message retrieved from a PMS inbox."""
    message_id:     str        # unique ID within the PMS — used for deduplication
    reservation_id: str        # PMS reservation identifier
    guest_name:     str
    text:           str
    received_at:    datetime
    channel:        str = "direct"  # airbnb / booking / direct / etc.


@dataclass
class PMSReservation:
    """A reservation retrieved from a PMS."""
    reservation_id:    str
    confirmation_code: str
    guest_name:        str
    listing_name:      str = ""
    checkin:           Optional[date] = None
    checkout:          Optional[date] = None
    guests_count:      int = 1
    status:            str = "confirmed"  # confirmed / cancelled / pending


class PMSAdapter(ABC):
    """Abstract base class — one concrete subclass per PMS type."""

    @abstractmethod
    def test_connection(self) -> bool:
        """Return True if credentials are valid and the API is reachable."""
        ...

    @abstractmethod
    def get_new_messages(self, since: datetime) -> list[PMSMessage]:
        """Return all guest inbox messages received after `since`."""
        ...

    @abstractmethod
    def send_message(self, reservation_id: str, text: str) -> bool:
        """Send a reply to a guest via the PMS. Return True on success."""
        ...

    @abstractmethod
    def get_reservations(self, from_date: date, to_date: date) -> list[PMSReservation]:
        """Return reservations with checkin date within [from_date, to_date]."""
        ...


def make_adapter(pms_type: str, api_key: str,
                 account_id: str = "", base_url: str = "") -> PMSAdapter:
    """Factory — returns the correct adapter for the given pms_type."""
    pms_type = pms_type.lower()
    if pms_type == "guesty":
        from web.pms_guesty import GuestyAdapter
        return GuestyAdapter(api_key, account_id, base_url)
    if pms_type == "hostaway":
        from web.pms_hostaway import HostawayAdapter
        return HostawayAdapter(api_key, account_id, base_url)
    if pms_type == "lodgify":
        from web.pms_lodgify import LodgifyAdapter
        return LodgifyAdapter(api_key, account_id, base_url)
    if pms_type == "generic":
        from web.pms_generic import GenericAdapter
        return GenericAdapter(api_key, account_id, base_url)
    raise ValueError(f"Unknown PMS type: {pms_type!r}")
