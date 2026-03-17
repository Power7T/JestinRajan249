"""Tests for draft-related API endpoints."""
import pytest
from fastapi.testclient import TestClient

from web.app import app

client = TestClient(app, raise_server_exceptions=False)


class TestDraftAPI:
    def test_api_drafts_requires_auth(self):
        """GET /api/drafts should return 401 without a session."""
        resp = client.get("/api/drafts")
        assert resp.status_code == 401

    def test_api_workers_requires_auth(self):
        """GET /api/workers should return 401 without a session."""
        resp = client.get("/api/workers")
        assert resp.status_code == 401

    def test_approve_draft_requires_auth(self):
        """POST /drafts/{id}/approve should redirect to login without session."""
        resp = client.post(
            "/drafts/nonexistent/approve",
            data={"csrf_token": "fake"},
            follow_redirects=False,
        )
        assert resp.status_code in (302, 303, 403)

    def test_skip_draft_requires_auth(self):
        """POST /drafts/{id}/skip should redirect to login without session."""
        resp = client.post(
            "/drafts/nonexistent/skip",
            data={"csrf_token": "fake"},
            follow_redirects=False,
        )
        assert resp.status_code in (302, 303, 403)

    def test_bulk_approve_requires_auth(self):
        """POST /drafts/bulk-approve should require authentication."""
        resp = client.post(
            "/drafts/bulk-approve",
            data={"csrf_token": "fake"},
            follow_redirects=False,
        )
        assert resp.status_code in (302, 303, 401, 403)

    def test_bulk_skip_requires_auth(self):
        """POST /drafts/bulk-skip should require authentication."""
        resp = client.post(
            "/drafts/bulk-skip",
            data={"csrf_token": "fake"},
            follow_redirects=False,
        )
        assert resp.status_code in (302, 303, 401, 403)


class TestCheckinPortal:
    def test_checkin_invalid_token(self):
        """GET /checkin/{bad_token} should return 404."""
        resp = client.get("/checkin/totally-invalid-token-xyz")
        assert resp.status_code == 404

    def test_checkin_missing_token(self):
        """GET /checkin/ path segment missing should 404."""
        resp = client.get("/checkin/")
        assert resp.status_code == 404
