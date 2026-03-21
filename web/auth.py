# © 2024 Jestin Rajan. All rights reserved.
"""
Auth helpers: password hashing (bcrypt) + JWT session tokens.
"""

import hashlib
import os
from datetime import datetime, timezone, timedelta
from typing import Optional

import bcrypt
from jose import JWTError, jwt
from fastapi import Request, HTTPException

from web.db import SessionLocal
from web.models import Tenant

_ENVIRONMENT = os.getenv("ENVIRONMENT", "production").lower()
_ALLOW_INSECURE_DEFAULTS = _ENVIRONMENT in {"development", "dev", "test"}


def _load_required_secret(name: str, placeholder: str) -> str:
    value = os.getenv(name, "").strip()
    if value and not value.startswith("change-me"):
        return value
    if _ALLOW_INSECURE_DEFAULTS:
        return value or placeholder
    raise RuntimeError(f"{name} must be set in non-development environments")


SECRET_KEY  = _load_required_secret("SECRET_KEY", "change-me-in-production-use-a-long-random-string")
ALGORITHM   = "HS256"
TOKEN_HOURS = int(os.getenv("SESSION_HOURS", "72"))

# ---------------------------------------------------------------------------
# Password
# ---------------------------------------------------------------------------

def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(), hashed.encode())

# ---------------------------------------------------------------------------
# JWT
# ---------------------------------------------------------------------------

def tenant_session_version(tenant: Tenant) -> str:
    """Version claim used to revoke sessions when credentials or active state change."""
    raw = f"{tenant.password_hash}:{int(bool(tenant.is_active))}"
    return hashlib.sha256(raw.encode()).hexdigest()


def create_token(tenant_id: str, version: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(hours=TOKEN_HOURS)
    return jwt.encode({"sub": tenant_id, "ver": version, "exp": expire}, SECRET_KEY, algorithm=ALGORITHM)


def _decode_token_payload(token: str) -> Optional[dict]:
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        return None


def decode_token(token: str) -> Optional[str]:
    """Returns tenant_id or None if token invalid/expired."""
    payload = _decode_token_payload(token)
    if not payload:
        return None
    return payload.get("sub")

# ---------------------------------------------------------------------------
# Request helper — reads token from cookie or Authorization header
# ---------------------------------------------------------------------------

def get_current_tenant_id(request: Request) -> str:
    token = request.cookies.get("session")
    if not token:
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            token = auth[7:]
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    payload = _decode_token_payload(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Session expired")
    tenant_id = payload.get("sub")
    if not tenant_id:
        raise HTTPException(status_code=401, detail="Session expired")

    db = SessionLocal()
    try:
        tenant = db.query(Tenant).filter_by(id=tenant_id).first()
        if not tenant or not tenant.is_active:
            raise HTTPException(status_code=401, detail="Session expired")
        if payload.get("ver") != tenant_session_version(tenant):
            raise HTTPException(status_code=401, detail="Session expired")
    finally:
        db.close()
    return tenant_id
