from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import web.app as app_mod
import web.guest_contact_service as guest_contact_service
from web.models import Reservation, Tenant


def _signup(client, email: str) -> str:
    page = client.get("/login")
    csrf = page.cookies.get("csrf_token", "") or client.cookies.get("csrf_token", "")
    response = client.post(
        "/signup",
        data={
            "first_name": "Security",
            "last_name": "Tester",
            "email": email,
            "country": "US",
            "phone": "+15550006666",
            "password": "SecurePass123!",
            "csrf_token": csrf,
        },
        follow_redirects=False,
    )
    assert response.status_code in (302, 303)
    return client.cookies.get("csrf_token", "")


def test_guest_contact_api_rejects_missing_csrf_header(client):
    _signup(client, "guest-csrf-missing@example.com")

    response = client.post(
        "/api/guest-contacts/add",
        json={
            "guest_name": "Guest One",
            "guest_phone": "+15550007777",
            "property_name": "Sea View Loft",
            "room_identifier": "Unit 2",
            "check_in": datetime.now(timezone.utc).isoformat(),
            "check_out": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
        },
    )

    assert response.status_code == 403


def test_guest_contact_api_accepts_valid_csrf_header(client, monkeypatch):
    csrf = _signup(client, "guest-csrf-ok@example.com")

    async def fake_create_guest_contact(**kwargs):
        return SimpleNamespace(id="gc_test_123")

    monkeypatch.setattr(guest_contact_service, "create_guest_contact", fake_create_guest_contact)

    response = client.post(
        "/api/guest-contacts/add",
        headers={"X-CSRF-Token": csrf},
        json={
            "guest_name": "Guest Two",
            "guest_phone": "+15550008888",
            "property_name": "Sea View Loft",
            "room_identifier": "Unit 3",
            "check_in": datetime.now(timezone.utc).isoformat(),
            "check_out": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_checkin_links_store_hashed_token_but_accept_raw_url(client, db):
    csrf = _signup(client, "checkin-token@example.com")
    tenant = db.query(Tenant).filter_by(email="checkin-token@example.com").first()

    reservation = Reservation(
        tenant_id=tenant.id,
        confirmation_code="CONF1234",
        guest_name="Guest Three",
        guest_phone="+15550009999",
        listing_name="Sea View Loft",
        unit_identifier="Unit 7",
        checkin=date.today(),
        checkout=date.today() + timedelta(days=2),
        status="confirmed",
    )
    db.add(reservation)
    db.commit()
    db.refresh(reservation)

    response = client.post(
        f"/reservations/{reservation.id}/checkin-link",
        data={"csrf_token": csrf},
        follow_redirects=False,
    )

    assert response.status_code in (302, 303)
    raw_token = parse_qs(urlparse(response.headers["location"]).query)["checkin_link"][0]

    db.refresh(reservation)
    assert reservation.checkin_token
    assert reservation.checkin_token != raw_token
    assert len(reservation.checkin_token) == 64

    portal = client.get(f"/checkin/{raw_token}", follow_redirects=False)
    assert portal.status_code == 200


def test_voice_rating_requires_secret_when_enforced(client, monkeypatch):
    monkeypatch.setattr(app_mod, "_IS_DEV_ENV", False)
    monkeypatch.setenv("VOICE_RATING_WEBHOOK_SECRET", "test-voice-secret")

    response = client.post("/api/calls/rating", params={"phone": "+15550001111", "rating": 5})

    assert response.status_code == 403


def test_send_voice_requires_csrf_header_even_for_authenticated_hosts(client):
    _signup(client, "voice-send-csrf@example.com")

    response = client.post(
        "/api/calls/send-voice",
        params={"guest_phone": "+15551112222", "message": "Test outbound call"},
    )

    assert response.status_code == 403
