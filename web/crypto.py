# © 2024 Jestin Rajan. All rights reserved.
"""
AES-256 (Fernet) encryption for sensitive fields stored in DB.
Set FIELD_ENCRYPTION_KEY env var to a base64-encoded 32-byte key.
Generate one with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
"""

import os
from cryptography.fernet import Fernet

_KEY = os.getenv("FIELD_ENCRYPTION_KEY", "")

if _KEY:
    _fernet = Fernet(_KEY.encode() if isinstance(_KEY, str) else _KEY)
else:
    # Dev mode — generate a temporary key (data won't survive restarts without env var)
    _fernet = Fernet(Fernet.generate_key())


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
