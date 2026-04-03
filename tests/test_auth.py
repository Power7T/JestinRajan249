"""Tests for authentication routes (signup / login / logout)."""

from web.auth import hash_password, verify_password


def test_verify_password_returns_false_for_invalid_hash():
    assert verify_password("whatever", "not-a-bcrypt-hash") is False


def test_verify_password_accepts_valid_hash():
    hashed = hash_password("securepassword1")
    assert verify_password("securepassword1", hashed) is True


class TestSignup:
    def test_signup_redirects_on_success(self, client):
        resp = client.get("/login")
        csrf = resp.cookies.get("csrf_token", "")
        signup_resp = client.post(
            "/signup",
            data={
                "first_name": "New",
                "last_name": "User",
                "email": "newuser@example.com",
                "country": "US",
                "phone": "+15550001111",
                "password": "SecurePass123!",
                "csrf_token": csrf,
            },
            follow_redirects=False,
        )
        assert signup_resp.status_code in (302, 303)
        assert "/onboarding" in signup_resp.headers.get("location", "")

    def test_signup_duplicate_email(self, client):
        resp = client.get("/login")
        csrf = resp.cookies.get("csrf_token", "")
        client.post(
            "/signup",
            data={
                "first_name": "Dup",
                "last_name": "User",
                "email": "dup@example.com",
                "country": "US",
                "phone": "+15550002222",
                "password": "SecurePass123!",
                "csrf_token": csrf,
            },
            follow_redirects=False,
        )
        resp2 = client.get("/login")
        csrf2 = resp2.cookies.get("csrf_token", "")
        dup_resp = client.post(
            "/signup",
            data={
                "first_name": "Dup",
                "last_name": "User",
                "email": "dup@example.com",
                "country": "US",
                "phone": "+15550002222",
                "password": "SecurePass123!",
                "csrf_token": csrf2,
            },
            follow_redirects=False,
        )
        assert dup_resp.status_code == 200
        assert b"already registered" in dup_resp.content

    def test_signup_weak_password(self, client):
        resp = client.get("/login")
        csrf = resp.cookies.get("csrf_token", "")
        weak_resp = client.post(
            "/signup",
            data={
                "first_name": "Weak",
                "last_name": "User",
                "email": "weak@example.com",
                "country": "US",
                "phone": "+15550003333",
                "password": "short",
                "csrf_token": csrf,
            },
            follow_redirects=False,
        )
        assert weak_resp.status_code == 200
        assert b"12 characters" in weak_resp.content or b"uppercase letter" in weak_resp.content


class TestLogin:
    def test_login_invalid_credentials(self, client):
        resp = client.get("/login")
        csrf = resp.cookies.get("csrf_token", "")
        login_resp = client.post(
            "/login",
            data={"email": "nobody@example.com", "password": "wrongpass", "csrf_token": csrf},
            follow_redirects=False,
        )
        assert login_resp.status_code == 200
        assert b"Invalid" in login_resp.content

    def test_login_missing_csrf(self, client):
        login_resp = client.post(
            "/login",
            data={"email": "test@example.com", "password": "password123"},
            follow_redirects=False,
        )
        assert login_resp.status_code == 403


class TestLogout:
    def test_logout_redirects_to_login(self, client):
        page = client.get("/logout", follow_redirects=False)
        csrf = page.cookies.get("csrf_token", "") or client.cookies.get("csrf_token", "")
        resp = client.post("/logout", data={"csrf_token": csrf}, follow_redirects=False)
        assert resp.status_code in (302, 303)
        assert "/login" in resp.headers.get("location", "")

    def test_logout_clears_session_cookie(self, client):
        page = client.get("/logout", follow_redirects=False)
        csrf = page.cookies.get("csrf_token", "") or client.cookies.get("csrf_token", "")
        resp = client.post("/logout", data={"csrf_token": csrf}, follow_redirects=False)
        set_cookie = resp.headers.get("set-cookie", "")
        assert "session" in set_cookie


class TestProtectedRoutes:
    def test_dashboard_requires_auth(self, client):
        resp = client.get("/dashboard", follow_redirects=False)
        assert resp.status_code in (302, 303)
        assert "/login" in resp.headers.get("location", "")

    def test_settings_requires_auth(self, client):
        resp = client.get("/settings", follow_redirects=False)
        assert resp.status_code in (302, 303)

    def test_activity_requires_auth(self, client):
        resp = client.get("/activity", follow_redirects=False)
        assert resp.status_code in (302, 303)
