"""Regression checks for authenticated dashboard pages."""


def _signup(client, email: str = "page-health@example.com") -> None:
    page = client.get("/login")
    csrf = page.cookies.get("csrf_token", "") or client.cookies.get("csrf_token", "")
    response = client.post(
        "/signup",
        data={
            "email": email,
            "password": "securepassword1",
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


def test_guest_contacts_page_renders_for_authenticated_user(client):
    _signup(client, email="guest-contacts-page@example.com")

    response = client.get("/guest-contacts", follow_redirects=False)

    assert response.status_code == 200
    assert b"Guest Check-ins" in response.content


def test_voice_calls_page_renders_for_authenticated_user(client):
    _signup(client, email="voice-calls-page@example.com")

    response = client.get("/voice-calls", follow_redirects=False)

    assert response.status_code == 200
    assert b"Voice Calls" in response.content
