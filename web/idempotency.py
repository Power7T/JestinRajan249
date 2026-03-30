"""
Idempotency key management for webhook handlers.
Prevents duplicate processing of webhook retries.
"""

import hashlib
from datetime import datetime, timezone, timedelta
from sqlalchemy.orm import Session
from web.models import IdempotencyKey

import logging

log = logging.getLogger(__name__)


def generate_key_id(tenant_id: str, idempotency_key: str) -> str:
    """Generate unique key_id from tenant_id + idempotency_key."""
    combined = f"{tenant_id}:{idempotency_key}"
    return hashlib.sha256(combined.encode()).hexdigest()


def check_idempotency(
    db: Session,
    tenant_id: str,
    idempotency_key: str,
    operation: str,
) -> dict:
    """
    Check if this request was already processed.

    Returns:
        {
            "is_duplicate": bool,
            "result": {...}  # If duplicate, the previous result
        }
    """
    if not idempotency_key:
        return {"is_duplicate": False, "result": None}

    key_id = generate_key_id(tenant_id, idempotency_key)

    existing = db.query(IdempotencyKey).filter(
        IdempotencyKey.key_id == key_id,
        IdempotencyKey.expires_at > datetime.now(timezone.utc)
    ).first()

    if existing:
        log.info(f"[IDEMPOTENCY] Duplicate detected: {operation} for {tenant_id}")
        return {
            "is_duplicate": True,
            "status": existing.result_status,
            "result": existing.result_data,
            "error": existing.result_error,
        }

    return {"is_duplicate": False, "result": None}


def store_idempotency_result(
    db: Session,
    tenant_id: str,
    idempotency_key: str,
    operation: str,
    status: str,  # success, error
    result_data: dict = None,
    error_msg: str = None,
    ttl_hours: int = 24,
):
    """Store the result of an idempotent operation for future deduplication."""
    if not idempotency_key:
        return

    key_id = generate_key_id(tenant_id, idempotency_key)

    try:
        idempotency_record = IdempotencyKey(
            key_id=key_id,
            tenant_id=tenant_id,
            idempotency_key=idempotency_key,
            operation=operation,
            result_status=status,
            result_data=result_data,
            result_error=error_msg,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=ttl_hours),
        )
        db.add(idempotency_record)
        db.commit()
        log.info(f"[IDEMPOTENCY] Stored result for {operation}")
    except Exception as e:
        log.error(f"[IDEMPOTENCY] Error storing result: {e}")
        db.rollback()
