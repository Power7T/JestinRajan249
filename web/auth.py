# © 2024 Jestin Rajan. All rights reserved.
"""
Auth helpers: password hashing (bcrypt) + JWT session tokens.
"""

import os
from datetime import datetime, timezone, timedelta
from typing import Optional

import bcrypt
from jose import JWTError, jwt
from fastapi import Request, HTTPException

SECRET_KEY  = os.getenv("SECRET_KEY", "change-me-in-production-use-a-long-random-string")
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

def create_token(tenant_id: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(hours=TOKEN_HOURS)
    return jwt.encode({"sub": tenant_id, "exp": expire}, SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str) -> Optional[str]:
    """Returns tenant_id or None if token invalid/expired."""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload.get("sub")
    except JWTError:
        return None

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
    tenant_id = decode_token(token)
    if not tenant_id:
        raise HTTPException(status_code=401, detail="Session expired")
    return tenant_id
