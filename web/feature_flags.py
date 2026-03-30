"""
Feature flag system for safe canary deployments.
Allows rolling out features to a percentage of tenants or specific tenants.
"""

from sqlalchemy.orm import Session
from web.models import FeatureFlag, FeatureFlagOverride
from datetime import datetime, timezone
import random

import logging

log = logging.getLogger(__name__)


def is_feature_enabled(
    db: Session,
    tenant_id: str,
    flag_name: str,
) -> bool:
    """
    Check if a feature is enabled for a specific tenant.

    Logic:
    1. Check tenant-specific override first
    2. Then check global flag + rollout percentage
    3. Default to False
    """
    # Check tenant-specific override
    override = db.query(FeatureFlagOverride).filter(
        FeatureFlagOverride.flag_name == flag_name,
        FeatureFlagOverride.tenant_id == tenant_id,
    ).first()

    if override:
        return override.enabled

    # Check global flag
    flag = db.query(FeatureFlag).filter(
        FeatureFlag.flag_name == flag_name
    ).first()

    if not flag or not flag.enabled:
        return False

    # Rollout percentage logic: deterministic based on tenant_id
    # This ensures the same tenant always gets the same result
    if flag.rollout_percentage == 0:
        return False
    if flag.rollout_percentage == 100:
        return True

    # Hash tenant_id to a percentage
    hash_val = hash(tenant_id) % 100
    return hash_val < flag.rollout_percentage


def set_feature_flag(
    db: Session,
    flag_name: str,
    enabled: bool,
    rollout_percentage: int = 0,
    description: str = "",
):
    """Create or update a global feature flag."""
    try:
        flag = db.query(FeatureFlag).filter(
            FeatureFlag.flag_name == flag_name
        ).first()

        if not flag:
            flag = FeatureFlag(
                flag_name=flag_name,
                enabled=enabled,
                rollout_percentage=rollout_percentage,
                description=description,
            )
            db.add(flag)
        else:
            flag.enabled = enabled
            flag.rollout_percentage = rollout_percentage
            flag.description = description
            flag.updated_at = datetime.now(timezone.utc)

        db.commit()
        log.info(f"[FEATURE_FLAG] Set {flag_name}: enabled={enabled}, rollout={rollout_percentage}%")
    except Exception as e:
        log.error(f"[FEATURE_FLAG] Error setting flag {flag_name}: {e}")
        db.rollback()


def set_tenant_override(
    db: Session,
    flag_name: str,
    tenant_id: str,
    enabled: bool,
):
    """Set a feature flag override for a specific tenant."""
    try:
        from uuid import uuid4
        override = db.query(FeatureFlagOverride).filter(
            FeatureFlagOverride.flag_name == flag_name,
            FeatureFlagOverride.tenant_id == tenant_id,
        ).first()

        if not override:
            override = FeatureFlagOverride(
                id=str(uuid4()),
                flag_name=flag_name,
                tenant_id=tenant_id,
                enabled=enabled,
            )
            db.add(override)
        else:
            override.enabled = enabled

        db.commit()
        log.info(f"[FEATURE_FLAG] Override {flag_name} for {tenant_id}: {enabled}")
    except Exception as e:
        log.error(f"[FEATURE_FLAG] Error setting override: {e}")
        db.rollback()
