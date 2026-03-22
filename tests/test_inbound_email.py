"""Tests for forwarding-based inbound email ingestion."""

import os

from web.auth import hash_password
from web.crypto import encrypt
from web.models import Draft, Tenant, TenantConfig


def test_inbound_email_webhook_creates_draft(client, db, monkeypatch):
    os.environ["INBOUND_PARSE_WEBHOOK_SECRET"] = "test-inbound-secret"
    tenant = Tenant(
        email="forwarding@example.com",
        password_hash=hash_password("password123"),
        email_verified=True,
    )
    db.add(tenant)
    db.commit()

    cfg = TenantConfig(
        tenant_id=tenant.id,
        email_ingest_mode="forwarding",
        inbound_email_alias="forwarding-tenant",
        anthropic_api_key_enc=encrypt("test-anthropic-key"),
        property_names="Forwarding Test Property",
    )
    db.add(cfg)
    db.commit()

    monkeypatch.setattr("web.classifier.generate_draft", lambda *args, **kwargs: "Forwarded draft")

    resp = client.post(
        "/email/inbound",
        headers={"X-Inbound-Webhook-Secret": "test-inbound-secret"},
        data={
            "recipient": "forwarding-tenant@inbound.hostai.local",
            "sender": "noreply@airbnb.com",
            "subject": "Jane sent you a message",
            "text": "Jane sent you a message\n\nWhat is the WiFi password?",
            "message-id": "msg-123",
        },
    )

    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
    draft = db.query(Draft).filter_by(tenant_id=tenant.id, source="email").first()
    assert draft is not None
    assert draft.guest_name == "Jane"
    assert draft.draft == "Forwarded draft"
