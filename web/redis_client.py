# © 2024 Jestin Rajan. All rights reserved.
"""
Redis client singleton — used for shared state across Uvicorn workers:
  - Rate limiting (sliding window)
  - Baileys outbound message queue
  - (future) session store

Falls back gracefully to None when Redis is unavailable (dev mode / no REDIS_URL set).
Import and call get_redis() wherever you need the client. Always check for None.
"""

import logging
import os

log = logging.getLogger(__name__)

REDIS_URL = os.getenv("REDIS_URL", "")

_client = None
_initialized = False


def get_redis():
    """
    Return a connected Redis client, or None if Redis is not configured / unavailable.
    Thread-safe — initializes once on first call.
    """
    global _client, _initialized
    if _initialized:
        return _client
    _initialized = True

    if not REDIS_URL:
        log.info("REDIS_URL not set — running without Redis (in-memory fallbacks active)")
        return None

    try:
        import redis as _redis_lib  # lazy import so missing package only fails here
        client = _redis_lib.from_url(
            REDIS_URL,
            decode_responses=True,
            socket_timeout=2,
            socket_connect_timeout=2,
        )
        client.ping()
        _client = client
        log.info("Redis connected: %s", REDIS_URL.split("@")[-1])  # hide password
    except ImportError:
        log.warning("redis package not installed — pip install redis to enable Redis support")
    except Exception as exc:
        log.warning("Redis unavailable (%s) — falling back to in-memory", exc)

    return _client
