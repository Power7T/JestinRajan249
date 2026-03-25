"""
Guest Contact Service — handle guest contact creation, welcome messages, and bot whitelisting.
Integrates with Baileys, Meta API, and Twilio for real message sending.
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
    1. WhatsApp (Baileys or Meta) to guest
    2. WhatsApp (Baileys or Meta) to host
    3. SMS (Twilio) if available
    """

    cfg = db.query(TenantConfig).filter_by(tenant_id=tenant_id).first()
    if not cfg:
        log.warning(f"[{tenant_id}] No TenantConfig found")
        return

    # Build welcome messages
    guest_msg = _build_guest_welcome_message(guest_contact, cfg)
    host_msg = _build_host_confirmation_message(guest_contact)

    guest_sent = False
    host_sent = False

    # Send to guest via available channel
    if cfg.wa_mode == "baileys" and cfg.whatsapp_number:
        # Send via Baileys
        guest_sent = await _send_via_baileys(guest_contact.guest_phone, guest_msg, tenant_id, db)
    elif cfg.wa_mode == "meta_cloud":
        # Send via Meta API
        guest_sent = await _send_via_meta(guest_contact.guest_phone, guest_msg, tenant_id, cfg)

    if guest_sent:
        guest_contact.welcome_sent_at = datetime.now(timezone.utc)
        guest_contact.welcome_status = "sent"
        log.info(f"[{tenant_id}] Welcome sent to {guest_contact.guest_name}")
    else:
        guest_contact.welcome_status = "failed"
        log.warning(f"[{tenant_id}] Failed to send welcome to {guest_contact.guest_name}")

    # Send to host via available channel
    if cfg.whatsapp_number:
        if cfg.wa_mode == "baileys":
            host_sent = await _send_via_baileys(cfg.whatsapp_number, host_msg, tenant_id, db)
        elif cfg.wa_mode == "meta_cloud":
            host_sent = await _send_via_meta(cfg.whatsapp_number, host_msg, tenant_id, cfg)

    if host_sent:
        guest_contact.welcome_sent_to_host = datetime.now(timezone.utc)
        log.info(f"[{tenant_id}] Confirmation sent to host")

    # Optionally send SMS to host
    if cfg.sms_notify_number and cfg.sms_mode == "twilio":
        try:
            from web.sms_sender import send_sms
            sms_msg = f"[{guest_contact.guest_name}] {guest_contact.guest_phone} checking in. Room: {guest_contact.room_identifier or 'N/A'}"
            send_sms(
                cfg.twilio_account_sid,
                cfg.twilio_auth_token_enc,  # Will be decrypted internally
                cfg.twilio_from_number,
                cfg.sms_notify_number,
                sms_msg,
            )
            log.info(f"[{tenant_id}] SMS notification sent to host")
        except Exception as e:
            log.warning(f"[{tenant_id}] Failed to send SMS: {e}")

    db.commit()


def _build_guest_welcome_message(guest_contact: GuestContact, cfg: TenantConfig) -> str:
    """Build welcome message for guest."""

    # Use custom template if available
    if cfg.guest_welcome_template:
        try:
            message = cfg.guest_welcome_template.format(
                guest_name=guest_contact.guest_name,
                property_name=guest_contact.property_name or cfg.property_names or 'our property',
                room=guest_contact.room_identifier or 'your room',
            )
            return message
        except KeyError:
            # If template has invalid placeholders, fall back to default
            pass

    # Default welcome message
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


async def _send_via_baileys(phone_number: str, message: str, tenant_id: str, db: Session) -> bool:
    """Send message via Baileys (WhatsApp) using queue system."""

    try:
        from web.app import _queue_baileys_outbound
        _queue_baileys_outbound(tenant_id, phone_number, message, db)
        log.info(f"[{tenant_id}] Queued Baileys message to {phone_number}")
        return True
    except Exception as e:
        log.error(f"[{tenant_id}] Error queuing Baileys message: {e}")
        return False


async def _send_via_meta(phone_number: str, message: str, tenant_id: str, cfg: TenantConfig) -> bool:
    """Send message via Meta Cloud API (WhatsApp Business)."""

    try:
        from web.meta_sender import send_whatsapp
        from web.crypto import decrypt

        # Decrypt phone ID and token
        phone_id = cfg.whatsapp_phone_id
        token = decrypt(cfg.whatsapp_token_enc) if cfg.whatsapp_token_enc else None

        if not phone_id or not token:
            log.warning(f"[{tenant_id}] Meta API not configured")
            return False

        # Send via Meta API (format: +1234567890 or 1234567890)
        success = send_whatsapp(phone_id, token, phone_number, message)

        if success:
            log.info(f"[{tenant_id}] Meta message sent to {phone_number}")
        else:
            log.warning(f"[{tenant_id}] Meta message failed for {phone_number}")

        return success

    except Exception as e:
        log.error(f"[{tenant_id}] Error sending via Meta: {e}")
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
