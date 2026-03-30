"""
Call recording consent management.
Ensures GDPR/CCPA compliance by getting explicit consent before recording.
"""

from datetime import datetime, timezone
from sqlalchemy.orm import Session
from web.models import VoiceCall
from web import logger

log = logger.get_logger(__name__)


def get_consent_prompt(property_name: str = "our property") -> str:
    """
    Get the consent disclosure prompt.
    Must be played before recording starts.
    """
    return (
        f"Hello! Thank you for calling {property_name}. "
        "This call will be recorded for quality assurance and training purposes. "
        "Press 1 to continue, or press 2 to hang up."
    )


def handle_consent_response(
    db: Session,
    call_id: str,
    consent_digit: str,  # "1" = agreed, "2" = declined
) -> dict:
    """
    Handle guest's consent response.
    Returns: {"allowed": bool, "message": str}
    """
    voice_call = db.query(VoiceCall).filter(VoiceCall.id == call_id).first()
    if not voice_call:
        return {"allowed": False, "message": "Call not found"}

    if consent_digit == "1":
        # Guest consented to recording
        voice_call.recording_consent_given = True
        voice_call.recording_consent_at = datetime.now(timezone.utc)
        db.commit()
        log.info(f"[CONSENT] Guest consented to recording for call {call_id}")
        return {
            "allowed": True,
            "message": "Thank you! How can I help you today?",
        }
    else:
        # Guest declined recording
        voice_call.recording_consent_given = False
        voice_call.recording_consent_at = datetime.now(timezone.utc)
        voice_call.status = "declined"
        db.commit()
        log.info(f"[CONSENT] Guest declined recording for call {call_id}")
        return {
            "allowed": False,
            "message": "We understand. We are unable to process calls that are not recorded. Thank you for calling.",
        }


def should_record_call(voice_call: VoiceCall) -> bool:
    """
    Determine if a call should be recorded based on consent.
    """
    if voice_call.recording_consent_given is None:
        # Consent not yet obtained
        return False
    return voice_call.recording_consent_given is True
