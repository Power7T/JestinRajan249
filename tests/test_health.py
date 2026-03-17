"""Tests for health/liveness endpoints."""
import pytest
from fastapi.testclient import TestClient

from web.app import app

client = TestClient(app, raise_server_exceptions=False)


def test_ping():
    """GET /ping should return 200 with {ok: true} without hitting the DB."""
    resp = client.get("/ping")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


def test_health_returns_json():
    """/health should return valid JSON with a 'status' key."""
    resp = client.get("/health")
    assert resp.status_code in (200, 503)
    data = resp.json()
    assert "status" in data
    assert "db" in data
    assert "redis" in data


def test_health_status_values():
    """Status must be one of the known values."""
    resp = client.get("/health")
    data = resp.json()
    assert data["status"] in ("ok", "degraded")
    assert data["db"] in ("ok", "error")
    assert data["redis"] in ("ok", "error", "disabled")


def test_metrics_json():
    """/metrics endpoint returns JSON with expected keys."""
    resp = client.get("/metrics")
    assert resp.status_code == 200
    data = resp.json()
    assert "db" in data
    assert "total_tenants" in data
    assert "pending_drafts" in data


def test_metrics_prometheus():
    """/metrics/prometheus returns Prometheus text format."""
    resp = client.get("/metrics/prometheus")
    assert resp.status_code == 200
    assert "text/plain" in resp.headers.get("content-type", "")
    body = resp.text
    # Should contain at least one metric
    assert "hostai_" in body


def test_unknown_route_returns_404():
    """Unknown paths should return 404."""
    resp = client.get("/this-does-not-exist-xyz")
    assert resp.status_code == 404


def test_login_page_loads():
    """GET /login should return the login HTML page."""
    resp = client.get("/login")
    assert resp.status_code == 200
    assert "text/html" in resp.headers.get("content-type", "")


def test_pricing_page_loads():
    """GET /pricing should be publicly accessible."""
    resp = client.get("/pricing")
    assert resp.status_code == 200
