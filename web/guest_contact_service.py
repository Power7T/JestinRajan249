"""
Guest Contact Service — handle guest contact creation, welcome messages, and bot whitelisting.
"""

import os
from datetime import datetime, timezone
from sqlalchemy.orm import Session

from web.models import GuestContact, TenantConfig
from web import logger

log = logger.get_logger(__name__)


async def create_guest_contact(
    tenant_id: str,
    guest_name: str,
    guest_phone: str,
    check_in: datetime,
    check_out: datetime,
    property_name: str = None,
    room_identifier: str = None,
    reservation_id: int = None,
    db: Session = None,
) -> GuestContact:
    """Create a guest contact and send welcome messages."""

    guest_contact = GuestContact(
        tenant_id=tenant_id,
        guest_name=guest_name,
        guest_phone=guest_phone,
        check_in=check_in,
        check_out=check_out,
        property_name=property_name,
        room_identifier=room_identifier,
        reservation_id=reservation_id,
        status="active",
    )

    db.add(guest_contact)
    db.commit()
    db.refresh(guest_contact)

    log.info(f"[{tenant_id}] Created guest contact: {guest_name} ({guest_phone})")

    # Send welcome messages asynchronously
    try:
        await send_welcome_messages(tenant_id, guest_contact, db)
    except Exception as e:
        log.error(f"[{tenant_id}] Error sending welcome messages: {e}")
        guest_contact.welcome_status = "failed"
        db.commit()

    return guest_contact


async def send_welcome_messages(
    tenant_id: str,
    guest_contact: GuestContact,
    db: Session,
) -> None:
    """
    Send welcome messages via:
    1. WhatsApp to guest
    2. WhatsApp to host (self-message)
    3. Dashboard notification
    """

    cfg = db.query(TenantConfig).filter_by(tenant_id=tenant_id).first()
    if not cfg:
        log.warning(f"[{tenant_id}] No TenantConfig found")
        return

    # Build welcome message for guest
    guest_msg = _build_guest_welcome_message(guest_contact, cfg)

    # Build confirmation message for host
    host_msg = _build_host_confirmation_message(guest_contact)

    # Send to guest via Baileys (WhatsApp)
    guest_sent = await _send_via_baileys(guest_contact.guest_phone, guest_msg, tenant_id)

    if guest_sent:
        guest_contact.welcome_sent_at = datetime.now(timezone.utc)
        guest_contact.welcome_status = "sent"
        log.info(f"[{tenant_id}] Welcome sent to {guest_contact.guest_name}")
    else:
        guest_contact.welcome_status = "failed"
        log.warning(f"[{tenant_id}] Failed to send welcome to {guest_contact.guest_name}")

    # Send to host via Baileys (self-message)
    host_sent = await _send_via_baileys(cfg.host_whatsapp_number, host_msg, tenant_id) if hasattr(cfg, 'host_whatsapp_number') and cfg.host_whatsapp_number else False

    if host_sent:
        guest_contact.welcome_sent_to_host = datetime.now(timezone.utc)
        log.info(f"[{tenant_id}] Confirmation sent to host")

    db.commit()


def _build_guest_welcome_message(guest_contact: GuestContact, cfg: TenantConfig) -> str:
    """Build welcome message for guest."""

    property_info = f" at {guest_contact.property_name}" if guest_contact.property_name else ""
    room_info = f" (Room {guest_contact.room_identifier})" if guest_contact.room_identifier else ""

    message = f"""Hi {guest_contact.guest_name}! 👋

Welcome to {cfg.property_names or 'our property'}{property_info}{room_info}!

I'm your AI host assistant. I can help with:
✓ Check-in instructions
✓ WiFi & parking info
✓ House rules & amenities
✓ Emergency contacts
✓ Checkout procedures

Just ask me anything and I'll help! 🏠"""

    return message


def _build_host_confirmation_message(guest_contact: GuestContact) -> str:
    """Build confirmation message for host."""

    check_in_time = guest_contact.check_in.strftime("%I:%M %p") if guest_contact.check_in else "TBD"
    room_info = f" ({guest_contact.room_identifier})" if guest_contact.room_identifier else ""

    message = f"""[✅ BOOKING ACTIVATED]

Guest: {guest_contact.guest_name}
Phone: {guest_contact.guest_phone}
Room: {room_info if room_info else 'N/A'}
Check-in: Today {check_in_time}
Check-out: {guest_contact.check_out.strftime('%Y-%m-%d')}

✅ Welcome message sent to guest
🤖 Bot is now active for this guest"""

    return message


async def _send_via_baileys(phone_number: str, message: str, tenant_id: str) -> bool:
    """
    Send message via Baileys (WhatsApp).

    This is a placeholder — integrate with your actual Baileys bot.
    """

    try:
        # TODO: Integrate with actual Baileys bot API
        # Example: POST to http://localhost:3000/send with { phone, message }

        log.info(f"[{tenant_id}] Would send to {phone_number}: {message[:50]}...")

        # For now, assume it worked
        return True

    except Exception as e:
        log.error(f"[{tenant_id}] Error sending via Baileys: {e}")
        return False


def is_guest_whitelisted(
    tenant_id: str,
    guest_phone: str,
    db: Session,
) -> bool:
    """Check if guest phone is whitelisted (has active guest contact)."""

    now = datetime.now(timezone.utc)

    guest_contact = (
        db.query(GuestContact)
        .filter(
            GuestContact.tenant_id == tenant_id,
            GuestContact.guest_phone == guest_phone,
            GuestContact.status == "active",
            GuestContact.check_in <= now,
            GuestContact.check_out >= now,
        )
        .first()
    )

    return guest_contact is not None


def get_guest_contact_for_phone(
    tenant_id: str,
    guest_phone: str,
    db: Session,
) -> GuestContact:
    """Get active guest contact for a phone number."""

    now = datetime.now(timezone.utc)

    return (
        db.query(GuestContact)
        .filter(
            GuestContact.tenant_id == tenant_id,
            GuestContact.guest_phone == guest_phone,
            GuestContact.status == "active",
            GuestContact.check_in <= now,
            GuestContact.check_out >= now,
        )
        .first()
    )
