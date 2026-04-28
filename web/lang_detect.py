# © 2024 Jestin Rajan. All rights reserved.
"""
Language detection for guest messages.
LLM-based via OpenRouter (same model as sentiment analysis), Redis-cached.
Falls back to heuristic regex on error.
"""

import hashlib
import json
import logging
import re

log = logging.getLogger(__name__)

_HEURISTIC_MAP = [
    ("es", ["gracias", "hola", "por favor", "agua", "baño", "habitación", "llave", "cómo", "dónde"]),
    ("fr", ["merci", "bonjour", "bonsoir", "s'il vous", "votre", "chambre", "clé", "arrivée"]),
    ("de", ["danke", "guten", "bitte", "schlüssel", "ankunft", "zimmer", "können"]),
    ("pt", ["obrigado", "obrigada", "olá", "por favor", "quarto", "chegada", "chave"]),
    ("it", ["grazie", "buongiorno", "buona", "per favore", "camera", "arrivo", "chiave"]),
    ("nl", ["dank", "bedankt", "goedemorgen", "alstublieft", "kamer", "sleutel"]),
    ("ar", ["شكرا", "مرحبا", "من فضلك", "الغرفة", "المفتاح"]),
    ("ja", ["あ", "い", "う", "え", "お"]),  # hiragana chars
    ("ko", ["가", "나", "다", "라", "마"]),  # hangul chars
    ("zh", ["中", "上", "下", "我", "你"]),  # CJK chars
    ("ru", ["при", "спа", "здр"]),  # Cyrillic
]


def _heuristic_detect(text: str) -> str:
    if not text:
        return "en"
    lower = text.lower()
    for code, markers in _HEURISTIC_MAP:
        if any(m in lower for m in markers):
            return code
    if any(0x4E00 <= ord(c) <= 0x9FFF for c in text):
        return "zh"
    if any(0x3040 <= ord(c) <= 0x309F for c in text):
        return "ja"
    if any(0xAC00 <= ord(c) <= 0xD7A3 for c in text):
        return "ko"
    if any(0x0400 <= ord(c) <= 0x04FF for c in text):
        return "ru"
    return "en"


def _cache_key(text: str) -> str:
    digest = hashlib.sha256(text[:200].encode()).hexdigest()[:16]
    return f"lang:{digest}"


def detect_language(text: str, tenant_id: str) -> str:
    """
    Detect ISO 639-1 language code for the given text.
    Uses LLM via OpenRouter for accuracy; Redis-cached per message hash.
    Falls back to heuristics on any error.
    """
    if not text or not text.strip():
        return "en"

    # Try Redis cache first
    try:
        from web.redis_client import get_redis
        redis = get_redis()
        if redis:
            cached = redis.get(_cache_key(text))
            if cached:
                return cached.decode() if isinstance(cached, bytes) else cached
    except Exception:
        pass

    # Try LLM detection
    try:
        from web.db import SessionLocal
        from web.models import SystemConfig
        from web.crypto import decrypt
        import openai

        with SessionLocal() as db:
            sys_conf = db.query(SystemConfig).first()
            if sys_conf and sys_conf.openrouter_api_key_enc and sys_conf.openrouter_api_key_enc != "********":
                client = openai.OpenAI(
                    base_url="https://openrouter.ai/api/v1",
                    api_key=decrypt(sys_conf.openrouter_api_key_enc),
                )
                # Strip PII before sending
                safe = re.sub(r'\+?\d(?:[\d\-\s\(\)]{8,})\d', '', text[:200])
                safe = re.sub(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,7}\b', '', safe)

                resp = client.chat.completions.create(
                    model=sys_conf.sentiment_model or "openai/gpt-4o-mini",
                    messages=[{
                        "role": "user",
                        "content": (
                            "Detect the language of this text and return ONLY valid JSON with one key: "
                            f"'language_code' (ISO 639-1 string, e.g. 'en', 'es', 'fr'). Text: {safe}"
                        )
                    }],
                    response_format={"type": "json_object"},
                    temperature=0.0,
                    max_tokens=16,
                )
                result = json.loads(resp.choices[0].message.content)
                code = str(result.get("language_code", "en")).lower()[:5].strip()
                if not re.match(r'^[a-z]{2}(-[A-Z]{2})?$', code):
                    code = "en"

                # Cache result for 1 hour
                try:
                    from web.redis_client import get_redis
                    redis = get_redis()
                    if redis:
                        redis.setex(_cache_key(text), 3600, code)
                except Exception:
                    pass

                return code
    except Exception as exc:
        log.debug("LLM language detection failed, using heuristic: %s", exc)

    return _heuristic_detect(text)
