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
        # Development/test convenience: persist the generated key so we don't lose encrypted DB data on restart
        key_file = ".dev_fernet_key"
        if os.path.exists(key_file):
            import logging
            logging.getLogger(__name__).warning("Using auto-generated Fernet key from %s. DO NOT do this in production.", key_file)
            with open(key_file, "rb") as f:
                return f.read().strip()
        
        new_key = Fernet.generate_key()
        import logging
        logging.getLogger(__name__).warning("No FIELD_ENCRYPTION_KEY found. Auto-generating one and saving to %s.", key_file)
        with open(key_file, "wb") as f:
            f.write(new_key)
        return new_key
        
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
