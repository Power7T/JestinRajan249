from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from web.workflow import (
    automation_rule_decision,
    build_activation_checklist,
    build_conversation_memory,
    build_guest_timeline,
    derive_dashboard_kpis,
    should_auto_send,
    surface_exception_queue,
)


def _ns(**kwargs):
    return SimpleNamespace(**kwargs)


def test_derive_dashboard_kpis_counts_and_rates():
    now = datetime(2026, 3, 22, 12, 0, tzinfo=timezone.utc)
    drafts = [
        _ns(status="pending", msg_type="routine", created_at=now - timedelta(minutes=15)),
        _ns(status="pending", msg_type="complex", created_at=now - timedelta(hours=2)),
        _ns(status="approved", msg_type="routine", created_at=now - timedelta(hours=1)),
        _ns(status="skipped", msg_type="escalation", created_at=now - timedelta(minutes=5)),
    ]
    reservations = [
        _ns(status="confirmed", checkin=datetime(2026, 3, 24).date(), checkout=datetime(2026, 3, 27).date(),
            guest_phone="+1 555 111 2222", unit_identifier="12A"),
        _ns(status="confirmed", checkin=datetime(2026, 3, 24).date(), checkout=datetime(2026, 3, 27).date(),
            guest_phone="", unit_identifier=""),
    ]

    kpis = derive_dashboard_kpis(drafts, reservations, now=now)

    assert kpis["drafts"]["total"] == 4
    assert kpis["drafts"]["pending"] == 2
    assert kpis["drafts"]["approved"] == 1
    assert kpis["drafts"]["skipped"] == 1
    assert kpis["drafts"]["overdue_pending"] == 1
    assert kpis["drafts"]["approval_rate"] == 25.0
    assert kpis["reservations"]["total"] == 2
    assert kpis["reservations"]["context_complete"] == 1
    assert kpis["reservations"]["missing_guest_phone"] == 1
    assert kpis["reservations"]["missing_unit_identifier"] == 1
    assert kpis["ops"]["needs_attention"] == 6


def test_build_activation_checklist_flags_ready_items():
    config = _ns(
        property_names="Sea View Villa",
        house_rules="No smoking",
        faq="Wi-Fi in the living room",
        custom_instructions="Call me for exceptions",
        email_ingest_mode="forwarding",
        anthropic_api_key_enc="encrypted-key",
        ical_urls="https://example.com/listing.ics",
        escalation_email="ops@example.com",
        wa_mode="meta",
        sms_mode="none",
    )
    reservations = [
        _ns(guest_phone="+1 555 111 2222", unit_identifier="12A"),
        _ns(guest_phone="", unit_identifier=""),
    ]

    items = build_activation_checklist(
        config,
        reservations=reservations,
        inbound_email_address="host-abc@inbound.hostai.local",
        inbound_webhook_url="https://app.example.com/email/inbound",
    )
    by_key = {item["key"]: item for item in items}

    assert by_key["property_profile"]["complete"] is True
    assert by_key["house_context"]["complete"] is True
    assert by_key["calendar"]["complete"] is True
    assert by_key["guest_context"]["complete"] is True
    assert by_key["email_intake"]["complete"] is True
    assert by_key["claude"]["complete"] is True
    assert by_key["escalation"]["complete"] is True
    assert by_key["channels"]["complete"] is True


def test_build_conversation_memory_orders_and_formats_events():
    events = [
        {"created_at": "2026-03-22T10:05:00Z", "actor": "host", "channel": "dashboard", "message": "Check-in is at 3 PM", "reservation_code": "ABC123"},
        _ns(created_at=datetime(2026, 3, 22, 10, 0, tzinfo=timezone.utc), actor="guest", channel="sms", message="We arrived early", room="12A"),
        _ns(created_at=datetime(2026, 3, 22, 10, 10, tzinfo=timezone.utc), actor="guest", channel="sms", message="Can we leave bags?", room="12A"),
    ]

    memory = build_conversation_memory(events, limit=2)

    assert "Check-in is at 3 PM" in memory
    assert "Can we leave bags?" in memory
    assert "We arrived early" not in memory
    assert "booking=ABC123" in memory
    assert "room=12A" in memory
    assert memory.index("Check-in is at 3 PM") < memory.index("Can we leave bags?")


def test_build_guest_timeline_uses_summary_and_body_for_event_rows():
    events = [
        _ns(
            created_at=datetime(2026, 3, 22, 11, 0, tzinfo=timezone.utc),
            channel="sms",
            direction="incoming",
            event_type="guest_message_received",
            summary="Guest asked for parking instructions",
            body="They are arriving in 20 minutes.",
            unit_identifier="Room 204",
        )
    ]

    timeline = build_guest_timeline(events)

    assert timeline[0]["message"] == "Guest asked for parking instructions"
    assert timeline[0]["status"] == "They are arriving in 20 minutes."
    assert timeline[0]["room"] == "Room 204"


def test_automation_rule_decision_blocks_low_confidence_and_allows_match():
    rule = {
        "enabled": True,
        "channels": ["email"],
        "msg_types": ["routine"],
        "min_confidence": 0.8,
        "properties": ["Beach View Villa"],
    }
    low_confidence_draft = {
        "status": "pending",
        "source": "email",
        "msg_type": "routine",
        "confidence": 0.72,
        "listing_name": "Beach View Villa",
    }
    matching_draft = {
        "status": "pending",
        "source": "email",
        "msg_type": "routine",
        "confidence": 0.93,
        "listing_name": "Beach View Villa",
    }

    blocked = automation_rule_decision(rule, low_confidence_draft)
    allowed = automation_rule_decision(rule, matching_draft)

    assert blocked["should_send"] is False
    assert "confidence" in blocked["reason"]
    assert allowed["should_send"] is True
    assert should_auto_send(rule, matching_draft) is True


def test_surface_exception_queue_prioritizes_failures_and_gaps():
    now = datetime(2026, 3, 22, 12, 0, tzinfo=timezone.utc)
    drafts = [
        _ns(id="d1", status="failed", msg_type="routine", guest_name="Maya", created_at=now - timedelta(minutes=5), reply_to="maya@example.com"),
        _ns(id="d2", status="pending", msg_type="complex", guest_name="Noah", created_at=now - timedelta(hours=2), reply_to=""),
        _ns(id="d3", status="pending", msg_type="routine", guest_name="Zoe", created_at=now - timedelta(hours=2), reply_to="zoe@example.com"),
    ]
    reservations = [
        _ns(confirmation_code="ABC123", status="confirmed", guest_name="Isha", checkin=datetime(2026, 3, 24).date(),
            checkout=datetime(2026, 3, 27).date(), guest_phone="", unit_identifier=""),
    ]

    issues = surface_exception_queue(drafts, reservations, now=now, stale_minutes=60)
    kinds = [item["kind"] for item in issues]

    assert kinds[0] == "failed_send"
    assert "escalation" in kinds
    assert "stale_pending" in kinds
    assert "missing_reply_route" in kinds
    assert "missing_guest_phone" in kinds
    assert "missing_unit_mapping" in kinds
