"""
Rate limiting for per-tenant API usage.
Prevents one tenant from DoS-ing the system or causing runaway API costs.
"""

from datetime import datetime, timezone, timedelta
from typing import Optional
from sqlalchemy.orm import Session
from web.models import TenantRateLimit, RateLimitCounter, Tenant
import logging

log = logging.getLogger(__name__)


def check_rate_limit(
    db: Session,
    tenant_id: str,
    metric: str,  # "voice_calls", "external_api", "daily_cost"
    cost_increment: float = 0,
) -> dict:
    """
    Check if tenant has exceeded rate limits.

    Returns:
        {
            "allowed": bool,
            "remaining": int,
            "limit": int,
            "resets_at": datetime,
            "reason": str  # if not allowed
        }
    """
    if not tenant_id:
        return {"allowed": True, "remaining": 999, "limit": 1000}

    # Get tenant's rate limit config
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if not tenant:
        return {"allowed": False, "reason": "Tenant not found"}

    # Get rate limit settings
    rate_limit_config = db.query(TenantRateLimit).filter(
        TenantRateLimit.tenant_id == tenant_id
    ).first()

    if not rate_limit_config:
        # Default limits
        rate_limit_config = TenantRateLimit(
            tenant_id=tenant_id,
            voice_calls_per_hour=100,
            external_api_calls_per_hour=500,
            max_daily_cost_usd=50,
        )
        db.add(rate_limit_config)
        db.commit()

    # Determine window and limit
    if metric == "voice_calls":
        limit = rate_limit_config.voice_calls_per_hour
        window_duration = timedelta(hours=1)
        window_key = f"{tenant_id}:{metric}:{datetime.now(timezone.utc).hour}"
    elif metric == "external_api":
        limit = rate_limit_config.external_api_calls_per_hour
        window_duration = timedelta(hours=1)
        window_key = f"{tenant_id}:{metric}:{datetime.now(timezone.utc).hour}"
    elif metric == "daily_cost":
        # For cost, we track cumulative cost in the day
        limit_usd = rate_limit_config.max_daily_cost_usd
        window_duration = timedelta(days=1)
        window_key = f"{tenant_id}:{metric}:{datetime.now(timezone.utc).date()}"
        # Check current daily cost
        start_of_day = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        counter = db.query(RateLimitCounter).filter(
            RateLimitCounter.tenant_id == tenant_id,
            RateLimitCounter.metric == "daily_cost",
            RateLimitCounter.window_start >= start_of_day,
        ).first()

        current_cost = (counter.count if counter else 0) + cost_increment
        if current_cost > limit_usd:
            return {
                "allowed": False,
                "remaining": max(0, limit_usd - current_cost),
                "limit": limit_usd,
                "resets_at": start_of_day + timedelta(days=1),
                "reason": f"Daily cost limit exceeded: ${current_cost:.2f} > ${limit_usd}",
            }

        return {
            "allowed": True,
            "remaining": max(0, limit_usd - current_cost),
            "limit": limit_usd,
            "resets_at": start_of_day + timedelta(days=1),
        }
    else:
        return {"allowed": True, "remaining": 999, "limit": 1000}

    # Get or create counter for this window
    counter = db.query(RateLimitCounter).filter(
        RateLimitCounter.counter_id == window_key
    ).first()

    now = datetime.now(timezone.utc)
    if not counter or (counter.expires_at and counter.expires_at < now):
        counter = RateLimitCounter(
            counter_id=window_key,
            tenant_id=tenant_id,
            metric=metric,
            count=0,
            window_start=now,
            expires_at=now + window_duration,
        )
        db.add(counter)
        db.commit()

    # Check limit
    if counter.count >= limit:
        return {
            "allowed": False,
            "remaining": 0,
            "limit": limit,
            "resets_at": counter.expires_at,
            "reason": f"{metric} rate limit exceeded: {counter.count}/{limit}",
        }

    return {
        "allowed": True,
        "remaining": limit - counter.count,
        "limit": limit,
        "resets_at": counter.expires_at,
    }


def increment_rate_limit(
    db: Session,
    tenant_id: str,
    metric: str,  # "voice_calls", "external_api", "daily_cost"
    amount: float = 1,  # For cost tracking, use USD amount
):
    """Increment rate limit counter."""
    if not tenant_id:
        return

    now = datetime.now(timezone.utc)

    if metric in ["voice_calls", "external_api"]:
        window_key = f"{tenant_id}:{metric}:{now.hour}"
        expires_at = now + timedelta(hours=1)
    elif metric == "daily_cost":
        window_key = f"{tenant_id}:{metric}:{now.date()}"
        expires_at = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    else:
        return

    try:
        counter = db.query(RateLimitCounter).filter(
            RateLimitCounter.counter_id == window_key
        ).first()

        if not counter or (counter.expires_at and counter.expires_at < now):
            counter = RateLimitCounter(
                counter_id=window_key,
                tenant_id=tenant_id,
                metric=metric,
                count=int(amount) if metric != "daily_cost" else 0,
                window_start=now,
                expires_at=expires_at,
            )
            if metric == "daily_cost":
                counter.count = int(amount * 100)  # Store as cents for precision
            db.add(counter)
        else:
            if metric == "daily_cost":
                counter.count += int(amount * 100)
            else:
                counter.count += int(amount)

        db.commit()
    except Exception as e:
        log.error(f"[RATE_LIMIT] Error incrementing counter: {e}")
        db.rollback()
