"""Tests for the Stripe billing integration (web.billing)."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from web.billing import (
    ACTIVE_STATUSES,
    PLAN_INFO,
    is_plan_active,
    tenant_has_channel,
    require_channel,
    generate_bot_token,
    verify_bot_token,
    _sync_subscription,
    _deactivate_subscription,
    _mark_past_due,
    PLAN_FREE,
    PLAN_BAILEYS,
    PLAN_META_CLOUD,
    PLAN_SMS,
    PLAN_PRO,
)
import pytest
from fastapi import HTTPException


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _cfg(**kwargs):
    defaults = dict(
        tenant_id="tenant-1",
        subscription_plan=PLAN_FREE,
        subscription_status="inactive",
        stripe_customer_id=None,
        stripe_subscription_id=None,
        subscription_expires_at=None,
        bot_api_token_hash=None,
        bot_api_token_hint=None,
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


# ---------------------------------------------------------------------------
# is_plan_active
# ---------------------------------------------------------------------------

class TestIsPlanActive:
    def test_active_status_is_active(self):
        cfg = _cfg(subscription_status="active")
        assert is_plan_active(cfg) is True

    def test_trialing_status_is_active(self):
        cfg = _cfg(subscription_status="trialing")
        assert is_plan_active(cfg) is True

    def test_inactive_is_not_active(self):
        cfg = _cfg(subscription_status="inactive")
        assert is_plan_active(cfg) is False

    def test_cancelled_is_not_active(self):
        cfg = _cfg(subscription_status="cancelled")
        assert is_plan_active(cfg) is False

    def test_past_due_is_not_active(self):
        cfg = _cfg(subscription_status="past_due")
        assert is_plan_active(cfg) is False


# ---------------------------------------------------------------------------
# tenant_has_channel
# ---------------------------------------------------------------------------

class TestTenantHasChannel:
    def test_free_plan_has_no_channels(self):
        cfg = _cfg(subscription_plan=PLAN_FREE, subscription_status="active")
        assert tenant_has_channel(cfg, "baileys") is False
        assert tenant_has_channel(cfg, "meta_cloud") is False
        assert tenant_has_channel(cfg, "sms") is False

    def test_baileys_plan_has_baileys_only(self):
        cfg = _cfg(subscription_plan=PLAN_BAILEYS, subscription_status="active")
        assert tenant_has_channel(cfg, "baileys") is True
        assert tenant_has_channel(cfg, "meta_cloud") is False
        assert tenant_has_channel(cfg, "sms") is False

    def test_meta_cloud_plan_has_meta_cloud_only(self):
        cfg = _cfg(subscription_plan=PLAN_META_CLOUD, subscription_status="active")
        assert tenant_has_channel(cfg, "meta_cloud") is True
        assert tenant_has_channel(cfg, "baileys") is False

    def test_sms_plan_has_sms_only(self):
        cfg = _cfg(subscription_plan=PLAN_SMS, subscription_status="active")
        assert tenant_has_channel(cfg, "sms") is True
        assert tenant_has_channel(cfg, "baileys") is False

    def test_pro_plan_has_all_channels(self):
        cfg = _cfg(subscription_plan=PLAN_PRO, subscription_status="active")
        assert tenant_has_channel(cfg, "baileys") is True
        assert tenant_has_channel(cfg, "meta_cloud") is True
        assert tenant_has_channel(cfg, "sms") is True

    def test_inactive_subscription_blocks_all_channels(self):
        cfg = _cfg(subscription_plan=PLAN_PRO, subscription_status="inactive")
        assert tenant_has_channel(cfg, "baileys") is False
        assert tenant_has_channel(cfg, "sms") is False


# ---------------------------------------------------------------------------
# require_channel
# ---------------------------------------------------------------------------

class TestRequireChannel:
    def test_raises_402_when_channel_not_available(self):
        cfg = _cfg(subscription_plan=PLAN_FREE, subscription_status="active")
        with pytest.raises(HTTPException) as exc_info:
            require_channel(cfg, "baileys")
        assert exc_info.value.status_code == 402
        assert "baileys" in exc_info.value.detail

    def test_does_not_raise_when_channel_available(self):
        cfg = _cfg(subscription_plan=PLAN_BAILEYS, subscription_status="active")
        require_channel(cfg, "baileys")  # should not raise

    def test_raises_402_when_subscription_inactive(self):
        cfg = _cfg(subscription_plan=PLAN_PRO, subscription_status="cancelled")
        with pytest.raises(HTTPException) as exc_info:
            require_channel(cfg, "sms")
        assert exc_info.value.status_code == 402


# ---------------------------------------------------------------------------
# Bot token generation and verification
# ---------------------------------------------------------------------------

class TestBotToken:
    def test_generate_returns_raw_token(self):
        cfg = _cfg()
        db = MagicMock()
        raw = generate_bot_token(cfg, db)
        assert len(raw) > 20
        assert cfg.bot_api_token_hash is not None
        assert cfg.bot_api_token_hint == raw[-4:]

    def test_verify_correct_token(self):
        cfg = _cfg()
        db = MagicMock()
        raw = generate_bot_token(cfg, db)
        assert verify_bot_token(raw, cfg) is True

    def test_verify_wrong_token(self):
        cfg = _cfg()
        db = MagicMock()
        generate_bot_token(cfg, db)
        assert verify_bot_token("wrong-token", cfg) is False

    def test_verify_empty_token(self):
        cfg = _cfg()
        assert verify_bot_token("", cfg) is False

    def test_verify_when_no_hash_stored(self):
        cfg = _cfg(bot_api_token_hash=None)
        assert verify_bot_token("any-token", cfg) is False


# ---------------------------------------------------------------------------
# Webhook subscription sync helpers
# ---------------------------------------------------------------------------

class TestSyncSubscription:
    def _make_db(self, cfg):
        db = MagicMock()
        db.query.return_value.filter_by.return_value.first.return_value = cfg
        return db

    def test_sync_activates_subscription_from_checkout_session(self):
        cfg = _cfg()
        db = self._make_db(cfg)
        obj = {
            "object": "checkout.session",
            "client_reference_id": "tenant-1",
            "customer": "cus_123",
            "metadata": {"plan": PLAN_BAILEYS},
            "subscription": "sub_abc",
        }
        _sync_subscription(obj, db, "checkout.session.completed")
        assert cfg.subscription_plan == PLAN_BAILEYS
        assert cfg.subscription_status == "active"
        assert cfg.stripe_customer_id == "cus_123"

    def test_sync_activates_subscription_from_subscription_object(self):
        cfg = _cfg()
        db = self._make_db(cfg)
        obj = {
            "object": "subscription",
            "id": "sub_xyz",
            "customer": "cus_456",
            "status": "active",
            "metadata": {"tenant_id": "tenant-1", "plan": PLAN_PRO},
            "current_period_end": 9999999999,
        }
        _sync_subscription(obj, db, "customer.subscription.updated")
        assert cfg.subscription_plan == PLAN_PRO
        assert cfg.subscription_status == "active"

    def test_deactivate_sets_cancelled(self):
        cfg = _cfg(subscription_status="active")
        db = self._make_db(cfg)
        obj = {"customer": "cus_123"}
        _deactivate_subscription(obj, db)
        assert cfg.subscription_status == "cancelled"

    def test_mark_past_due(self):
        cfg = _cfg(subscription_status="active")
        db = self._make_db(cfg)
        obj = {"customer": "cus_123"}
        _mark_past_due(obj, db)
        assert cfg.subscription_status == "past_due"


# ---------------------------------------------------------------------------
# Webhook endpoint — signature verification
# ---------------------------------------------------------------------------

class TestStripeWebhookEndpoint:
    def test_webhook_rejects_bad_signature(self, client):
        resp = client.post(
            "/billing/stripe-webhook",
            content=b'{"type":"test"}',
            headers={"stripe-signature": "bad-sig", "content-type": "application/json"},
        )
        assert resp.status_code == 400

    def test_webhook_endpoint_exists(self, client):
        """Endpoint must be reachable (not 404/405) — bad sig returns 400, not 404."""
        resp = client.post(
            "/billing/stripe-webhook",
            content=b"{}",
            headers={"stripe-signature": "t=1,v1=bad", "content-type": "application/json"},
        )
        assert resp.status_code != 404
        assert resp.status_code != 405


# ---------------------------------------------------------------------------
# Plan info completeness
# ---------------------------------------------------------------------------

class TestPlanInfo:
    def test_all_plans_have_required_keys(self):
        for plan, info in PLAN_INFO.items():
            assert "name" in info, f"Plan {plan} missing 'name'"
            assert "price" in info, f"Plan {plan} missing 'price'"
            assert "features" in info, f"Plan {plan} missing 'features'"
            assert "channels" in info, f"Plan {plan} missing 'channels'"
            assert isinstance(info["features"], list)
            assert isinstance(info["channels"], list)

    def test_active_statuses_are_expected(self):
        assert "active" in ACTIVE_STATUSES
        assert "trialing" in ACTIVE_STATUSES
        assert "inactive" not in ACTIVE_STATUSES
        assert "cancelled" not in ACTIVE_STATUSES
