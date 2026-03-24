# © 2024 Jestin Rajan. All rights reserved.
"""
Auth helpers: password hashing (bcrypt) + JWT session tokens.
"""

import hashlib
import logging
import os
from datetime import datetime, timezone, timedelta
from typing import Optional

import bcrypt
import jwt
from jwt.exceptions import PyJWTError as JWTError
from fastapi import Request, HTTPException

from web.db import SessionLocal
from web.models import Tenant, TeamMember

log = logging.getLogger(__name__)

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
    try:
        return bcrypt.checkpw(plain.encode(), hashed.encode())
    except ValueError:
        log.warning("Invalid password hash encountered during login verification")
        return False

# ---------------------------------------------------------------------------
# JWT
# ---------------------------------------------------------------------------

def tenant_session_version(tenant: Tenant) -> str:
    """Version claim used to revoke sessions when credentials or active state change."""
    raw = f"{tenant.password_hash}:{int(bool(tenant.is_active))}"
    return hashlib.sha256(raw.encode()).hexdigest()


def member_session_version(member: TeamMember) -> str:
    """Version claim for team member token revocation."""
    raw = f"{member.password_hash}:{int(bool(member.is_active))}"
    return hashlib.sha256(raw.encode()).hexdigest()


def create_token(tenant_id: str, version: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(hours=TOKEN_HOURS)
    return jwt.encode({"sub": tenant_id, "ver": version, "exp": expire}, SECRET_KEY, algorithm=ALGORITHM)


def create_member_token(member_id: int, tenant_id: str, role: str, version: str) -> str:
    """Create JWT for team member login. Includes member_id in payload."""
    expire = datetime.now(timezone.utc) + timedelta(hours=TOKEN_HOURS)
    return jwt.encode({
        "sub": tenant_id,
        "mid": member_id,
        "role": role,
        "ver": version,
        "exp": expire
    }, SECRET_KEY, algorithm=ALGORITHM)


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

    provider = getattr(request.app.state, "auth_db_session_provider", None)
    db = provider() if callable(provider) else SessionLocal()
    owns_session = not callable(provider)
    try:
        tenant = db.query(Tenant).filter_by(id=tenant_id).first()
        if not tenant or not tenant.is_active:
            raise HTTPException(status_code=401, detail="Session expired")
        if payload.get("ver") != tenant_session_version(tenant):
            raise HTTPException(status_code=401, detail="Session expired")
    finally:
        if owns_session:
            db.close()

    # Enrich Sentry scope with tenant context so errors are tagged by tenant
    try:
        import sentry_sdk
        sentry_sdk.set_user({"id": tenant_id})
        sentry_sdk.set_tag("tenant_id", tenant_id)
    except Exception:
        pass

    return tenant_id


def get_current_member(request: Request, db) -> tuple[str, int, str]:
    """
    Returns (tenant_id, member_id, role) for a team member token.
    Raises 401 if token is invalid, missing member_id claim, or session is revoked.
    """
    token = request.cookies.get("session")
    if not token:
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            token = auth[7:]
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")

    payload = _decode_token_payload(token)
    if not payload or "mid" not in payload:  # Tenant token has no "mid" — not a member token
        raise HTTPException(status_code=401, detail="Session expired")

    tenant_id = payload.get("sub")
    member_id = payload.get("mid")
    role = payload.get("role", "manager")

    if not tenant_id or not member_id:
        raise HTTPException(status_code=401, detail="Session expired")

    # Validate member exists, is active, and version matches
    member = db.query(TeamMember).filter_by(id=member_id, tenant_id=tenant_id).first()
    if not member or not member.is_active:
        raise HTTPException(status_code=401, detail="Session expired")

    if payload.get("ver") != member_session_version(member):
        raise HTTPException(status_code=401, detail="Session expired")

    return tenant_id, member_id, role
