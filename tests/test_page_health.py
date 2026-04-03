"""Regression checks for authenticated dashboard pages."""

from sqlalchemy.exc import OperationalError

from web.models import VoiceCall, VoiceKnowledgeGap


def _signup(client, email: str = "page-health@example.com") -> None:
    page = client.get("/login")
    csrf = page.cookies.get("csrf_token", "") or client.cookies.get("csrf_token", "")
    response = client.post(
        "/signup",
        data={
            "first_name": "Page",
            "last_name": "Health",
            "email": email,
            "country": "US",
            "phone": "+15550004444",
            "password": "SecurePass123!",
            "csrf_token": csrf,
        },
        follow_redirects=False,
    )
    assert response.status_code in (302, 303)


def test_settings_page_renders_for_authenticated_user(client):
    _signup(client, email="settings-page@example.com")

    response = client.get("/settings", follow_redirects=False)

    assert response.status_code == 200
    assert b"Settings" in response.content
    assert b"Twilio Webhook URL (for incoming calls)" not in response.content


def test_guest_contacts_page_renders_for_authenticated_user(client):
    _signup(client, email="guest-contacts-page@example.com")

    response = client.get("/guest-contacts", follow_redirects=False)

    assert response.status_code == 200
    assert b"Guest Check-ins" in response.content


def test_voice_ai_page_renders_for_authenticated_user(client):
    _signup(client, email="voice-ai-page@example.com")

    response = client.get("/voice-calls", follow_redirects=False)

    assert response.status_code == 200
    assert b"Voice AI" in response.content
    assert b"Voice AI Panel" in response.content
    assert b"Voice AI Settings" in response.content
    assert b"Filter Calls" in response.content


def test_voice_ai_settings_tab_renders_for_authenticated_user(client):
    _signup(client, email="voice-ai-settings-page@example.com")

    response = client.get("/voice-calls?tab=settings", follow_redirects=False)

    assert response.status_code == 200
    assert b"Voice AI Settings" in response.content
    assert b'action="/voice-calls/settings"' in response.content


def test_voice_calls_page_preserves_filter_state(client):
    _signup(client, email="voice-calls-filters@example.com")

    response = client.get("/voice-calls?status=completed&sentiment=negative", follow_redirects=False)

    assert response.status_code == 200
    assert b'<option value="completed" selected>' in response.content
    assert b'<option value="negative" selected>' in response.content


def test_voice_calls_page_fails_soft_when_voice_schema_is_unavailable(client, db, monkeypatch):
    _signup(client, email="voice-calls-schema@example.com")

    real_query = db.query

    def flaky_query(*entities, **kwargs):
        if any(entity in (VoiceCall, VoiceKnowledgeGap) for entity in entities):
            raise OperationalError("SELECT 1", {}, Exception("missing voice tables"))
        return real_query(*entities, **kwargs)

    monkeypatch.setattr(db, "query", flaky_query)

    response = client.get("/voice-calls", follow_redirects=False)

    assert response.status_code == 200
    assert b"Voice call data could not be loaded" in response.content
