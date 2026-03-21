"""Tests for guest phone + room context enrichment flows."""

from datetime import datetime, timezone, timedelta

from web.app import _handle_inbound_sms
from web.auth import hash_password
from web.crypto import encrypt
from web.models import Draft, Reservation, Tenant, TenantConfig


def test_inbound_sms_uses_guest_phone_context(monkeypatch, db):
    tenant = Tenant(
        email="host@example.com",
        password_hash=hash_password("password123"),
        email_verified=True,
    )
    db.add(tenant)
    db.commit()

    cfg = TenantConfig(
        tenant_id=tenant.id,
        anthropic_api_key_enc=encrypt("test-anthropic-key"),
        property_names="Beach House",
    )
    reservation = Reservation(
        tenant_id=tenant.id,
        confirmation_code="ABC123",
        guest_name="Jane Guest",
        guest_phone="+1 (555) 123-0000",
        listing_name="Beach House",
        unit_identifier="Room 204",
        checkin=(datetime.now(timezone.utc) + timedelta(days=1)).date(),
        checkout=(datetime.now(timezone.utc) + timedelta(days=4)).date(),
        status="confirmed",
    )
    db.add(cfg)
    db.add(reservation)
    db.commit()

    monkeypatch.setattr("web.classifier.generate_draft", lambda *args, **kwargs: "Draft reply")

    _handle_inbound_sms(tenant.id, "+1 555 123 0000", "What is the WiFi password?", db)

    draft = db.query(Draft).filter_by(tenant_id=tenant.id, source="sms").first()
    assert draft is not None
    assert draft.guest_name == "Jane Guest"
    assert draft.reply_to == "+1 555 123 0000"
    assert draft.draft == "Draft reply"
