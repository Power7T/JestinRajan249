"""
Phone number normalization utilities.
Ensures all phone numbers are stored and matched consistently in E.164 format.
"""

import re
from typing import Optional

try:
    import phonenumbers
    HAS_PHONENUMBERS = True
except ImportError:
    HAS_PHONENUMBERS = False


def normalize_phone(phone_str: str, default_country: str = "US") -> Optional[str]:
    """
    Normalize phone number to E.164 format: +14155551234

    Handles various formats:
    - 4155551234
    - 415-555-1234
    - (415) 555-1234
    - +1-415-555-1234
    - +14155551234

    Returns None if normalization fails (invalid number).
    """
    if not phone_str:
        return None

    phone_str = phone_str.strip()

    # Try phonenumbers library if available
    if HAS_PHONENUMBERS:
        try:
            parsed = phonenumbers.parse(phone_str, default_country)
            if phonenumbers.is_valid_number(parsed):
                return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
        except phonenumbers.NumberParseException:
            pass

    # Fallback: basic regex normalization
    # Remove all non-digit characters except leading +
    normalized = re.sub(r'[^\d+]', '', phone_str)

    # If no + prefix and doesn't start with country code, add +1 (US)
    if not normalized.startswith('+'):
        if len(normalized) == 10:  # US number without country code
            normalized = '+1' + normalized
        elif len(normalized) == 11 and normalized.startswith('1'):  # US with 1 prefix
            normalized = '+' + normalized
        elif len(normalized) > 11:  # Probably has country code
            normalized = '+' + normalized
        else:
            normalized = '+1' + normalized

    return normalized if len(normalized) >= 10 else None


def phones_match(phone1: Optional[str], phone2: Optional[str]) -> bool:
    """
    Check if two phone numbers match (after normalization).
    Returns False if either is None or normalization fails.
    """
    if not phone1 or not phone2:
        return False

    norm1 = normalize_phone(phone1)
    norm2 = normalize_phone(phone2)

    if not norm1 or not norm2:
        return False

    return norm1 == norm2
