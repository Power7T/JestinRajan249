"""
API timeout handling and fallback strategies.
Prevents slow external API calls from blocking Twilio calls.
"""

import asyncio
import logging
from typing import Callable, Optional, Any

log = logging.getLogger(__name__)


async def call_with_timeout(
    coro,
    timeout_seconds: float,
    operation_name: str = "operation",
    fallback_result: Optional[Any] = None,
):
    """
    Call an async coroutine with a timeout.

    If timeout occurs, returns fallback_result and logs error.
    """
    try:
        return await asyncio.wait_for(coro, timeout=timeout_seconds)
    except asyncio.TimeoutError:
        log.error(
            f"[TIMEOUT] {operation_name} exceeded {timeout_seconds}s timeout. "
            f"Using fallback result."
        )
        return fallback_result
    except Exception as e:
        log.error(f"[TIMEOUT] {operation_name} failed: {e}")
        return fallback_result


# Fallback responses for common operations
FALLBACKS = {
    "transcribe": {
        "text": "[audio unclear]",
        "confidence": 0.0,
    },
    "generate_response": {
        "voice": "Sorry, I'm having trouble understanding right now. Could you please repeat that?",
        "send": None,
        "unknown": False,
        "unanswered_question": None,
    },
    "synthesize_speech": {
        "text": "I'm sorry, I'm having technical difficulties. Please try again later.",
        "uri": None,
    },
}


class TimeoutConfig:
    """Configurable timeouts for different operations."""

    # Voice operations
    DEEPGRAM_TRANSCRIBE = 8.0  # Deepgram should respond within 8s
    OPENAI_GENERATE = 6.0  # OpenAI should respond within 6s
    ELEVENLABS_SYNTHESIZE = 5.0  # ElevenLabs TTS should respond within 5s
    TWILIO_GATHER = 30.0  # Twilio gather timeout (Twilio controls this)

    # Database operations
    DB_QUERY = 3.0  # Database queries should respond within 3s
    DB_WRITE = 5.0  # Database writes should respond within 5s

    # External webhooks
    WEBHOOK_CALL = 10.0  # Webhook calls should respond within 10s


def get_timeout_for_operation(operation: str) -> float:
    """Get the timeout for a specific operation."""
    timeouts = {
        "transcribe": TimeoutConfig.DEEPGRAM_TRANSCRIBE,
        "generate_response": TimeoutConfig.OPENAI_GENERATE,
        "synthesize_speech": TimeoutConfig.ELEVENLABS_SYNTHESIZE,
        "db_query": TimeoutConfig.DB_QUERY,
        "db_write": TimeoutConfig.DB_WRITE,
        "webhook": TimeoutConfig.WEBHOOK_CALL,
    }
    return timeouts.get(operation, 10.0)  # Default 10s
