# © 2024 Jestin Rajan. All rights reserved.
"""
AES-256 (Fernet) encryption for sensitive fields stored in DB.
Set FIELD_ENCRYPTION_KEY env var to a base64-encoded 32-byte key.
Generate one with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
"""

import os
from cryptography.fernet import Fernet

_ENVIRONMENT = os.getenv("ENVIRONMENT", "production").lower()
_ALLOW_INSECURE_DEFAULTS = _ENVIRONMENT in {"development", "dev", "test"}


def _load_required_key() -> bytes:
    key = os.getenv("FIELD_ENCRYPTION_KEY", "").strip()
    if key:
        return key.encode()
    if _ALLOW_INSECURE_DEFAULTS:
        # Development/test convenience only; production must provide a stable key.
        return Fernet.generate_key()
    raise RuntimeError(
        "FIELD_ENCRYPTION_KEY is required in production. "
        "Set it to a base64-encoded 32-byte Fernet key."
    )

_fernet = Fernet(_load_required_key())


def encrypt(plaintext: str) -> str:
    """Encrypt a string; returns a URL-safe base64 token."""
    if not plaintext:
        return ""
    return _fernet.encrypt(plaintext.encode()).decode()


def decrypt(token: str) -> str:
    """Decrypt a token back to plaintext. Returns '' on failure."""
    if not token:
        return ""
    try:
        return _fernet.decrypt(token.encode()).decode()
    except Exception:
        return ""
