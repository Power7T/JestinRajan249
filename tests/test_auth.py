"""Tests for authentication routes (signup / login / logout)."""
import pytest
from fastapi.testclient import TestClient

from web.app import app
from web.models import Tenant, TenantConfig

client = TestClient(app, raise_server_exceptions=False)


def _get_csrf(client_: TestClient) -> str:
    """Retrieve a CSRF token by visiting the login page."""
    resp = client_.get("/login")
    assert resp.status_code == 200
    token = resp.cookies.get("csrf_token", "")
    assert token, "CSRF cookie not set"
    return token


class TestSignup:
    def test_signup_redirects_on_success(self):
        resp = client.get("/login")
        csrf = resp.cookies.get("csrf_token", "")
        signup_resp = client.post(
            "/signup",
            data={"email": "newuser@example.com", "password": "securepassword1", "csrf_token": csrf},
            follow_redirects=False,
        )
        # Should redirect to /onboarding on success
        assert signup_resp.status_code in (302, 303)
        assert "/onboarding" in signup_resp.headers.get("location", "")

    def test_signup_duplicate_email(self):
        resp = client.get("/login")
        csrf = resp.cookies.get("csrf_token", "")
        # First signup
        client.post(
            "/signup",
            data={"email": "dup@example.com", "password": "securepassword1", "csrf_token": csrf},
            follow_redirects=False,
        )
        # Second signup with same email
        resp2 = client.get("/login")
        csrf2 = resp2.cookies.get("csrf_token", "")
        dup_resp = client.post(
            "/signup",
            data={"email": "dup@example.com", "password": "anotherpassword", "csrf_token": csrf2},
            follow_redirects=False,
        )
        # Should show error (200 with error message on login page)
        assert dup_resp.status_code == 200
        assert b"already registered" in dup_resp.content

    def test_signup_weak_password(self):
        resp = client.get("/login")
        csrf = resp.cookies.get("csrf_token", "")
        weak_resp = client.post(
            "/signup",
            data={"email": "weak@example.com", "password": "short", "csrf_token": csrf},
            follow_redirects=False,
        )
        assert weak_resp.status_code == 200
        assert b"8+" in weak_resp.content or b"characters" in weak_resp.content


class TestLogin:
    def test_login_invalid_credentials(self):
        resp = client.get("/login")
        csrf = resp.cookies.get("csrf_token", "")
        login_resp = client.post(
            "/login",
            data={"email": "nobody@example.com", "password": "wrongpass", "csrf_token": csrf},
            follow_redirects=False,
        )
        assert login_resp.status_code == 200
        assert b"Invalid" in login_resp.content

    def test_login_missing_csrf(self):
        login_resp = client.post(
            "/login",
            data={"email": "test@example.com", "password": "password123"},
            follow_redirects=False,
        )
        # Missing CSRF should return 403
        assert login_resp.status_code == 403


class TestLogout:
    def test_logout_redirects_to_login(self):
        resp = client.get("/logout", follow_redirects=False)
        assert resp.status_code in (302, 303)
        assert "/login" in resp.headers.get("location", "")

    def test_logout_clears_session_cookie(self):
        resp = client.get("/logout", follow_redirects=False)
        # After logout, 'session' cookie should be cleared (max-age=0 or deleted)
        set_cookie = resp.headers.get("set-cookie", "")
        assert "session" in set_cookie


class TestProtectedRoutes:
    def test_dashboard_requires_auth(self):
        resp = client.get("/dashboard", follow_redirects=False)
        assert resp.status_code in (302, 303)
        assert "/login" in resp.headers.get("location", "")

    def test_settings_requires_auth(self):
        resp = client.get("/settings", follow_redirects=False)
        assert resp.status_code in (302, 303)

    def test_activity_requires_auth(self):
        resp = client.get("/activity", follow_redirects=False)
        assert resp.status_code in (302, 303)
