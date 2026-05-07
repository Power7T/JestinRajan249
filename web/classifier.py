# © 2024 Jestin Rajan. All rights reserved.
"""
Multi-tenant AI classifier + draft generator.
Adapted from airbnb-host/scripts/response_router.py — operates on per-tenant
config objects instead of env vars, writes results to PostgreSQL via the DB session.
"""

import re
import time
import pathlib
import logging
from datetime import datetime, timezone
from typing import Optional

# OpenRouter / model pricing table (USD per 1M tokens, input/output)
_MODEL_PRICE: dict[str, tuple[float, float]] = {
    "anthropic/claude-3.7-sonnet":          (3.00,  15.00),
    "anthropic/claude-3.5-sonnet":          (3.00,  15.00),
    "anthropic/claude-3-opus":              (15.00, 75.00),
    "anthropic/claude-3-haiku":             (0.25,   1.25),
    "meta-llama/llama-3.3-70b-instruct":    (0.12,   0.30),
    "meta-llama/llama-3.1-70b-instruct":    (0.12,   0.30),
    "mistralai/mistral-large":              (2.00,   6.00),
    "mistralai/mistral-7b-instruct":        (0.07,   0.07),
    "google/gemini-2.5-flash":              (0.075,  0.30),
    "google/gemini-flash-1.5":              (0.075,  0.30),
    "openai/gpt-4o":                        (5.00,  15.00),
    "openai/gpt-4o-mini":                   (0.15,   0.60),
}

def _calc_model_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Return USD cost for a model call given token counts."""
    # Exact match first, then prefix match
    price = _MODEL_PRICE.get(model)
    if price is None:
        for k, v in _MODEL_PRICE.items():
            if model.startswith(k) or k in model:
                price = v
                break
    if price is None:
        # Unknown model — default to cheap estimate
        price = (1.00, 3.00)
    return (input_tokens / 1_000_000) * price[0] + (output_tokens / 1_000_000) * price[1]

import anthropic

from web.workflow import build_structured_policy_context
from web.crypto import decrypt

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Load SKILL.md system prompt (strips YAML frontmatter)
# ---------------------------------------------------------------------------
_DEFAULT_SYSTEM_PROMPT = """
You are HostAI, an assistant for property hosts.
Reply clearly, warmly, and practically. Use the host-provided property context,
FAQ, house rules, and reservation details as the source of truth. Never invent
room numbers, access codes, fees, or refunds. If required context is missing,
ask one concise clarifying question.
""".strip()


def _load_system_prompt() -> str:
    candidate_paths = [
        pathlib.Path(__file__).parent.parent / "SKILL.md",
        pathlib.Path(__file__).parent.parent / "airbnb-host" / "SKILL.md",
    ]
    for candidate in candidate_paths:
        if not candidate.exists():
            continue
        raw = candidate.read_text(encoding="utf-8")
        parts = raw.split("---", 2)
        return parts[2].strip() if len(parts) >= 3 else raw
    log.warning("No SKILL.md found; using built-in fallback system prompt")
    return _DEFAULT_SYSTEM_PROMPT


SYSTEM_PROMPT = _load_system_prompt()

# ---------------------------------------------------------------------------
# Classification patterns (same as response_router.py)
# ---------------------------------------------------------------------------
_ROUTINE = [
    # Property info
    r"\bwifi\b", r"\bwi-?fi\b", r"\bpassword\b", r"\bcheck.?in\b", r"\bcheck.?out\b",
    r"\barrive\b", r"\barrival\b", r"\beta\b", r"\bparking\b", r"\bdirections?\b",
    r"\baddress\b", r"\bcode\b", r"\baccess\b", r"\bkeypad\b", r"\bamenities?\b",
    r"\bpool\b", r"\bgym\b", r"\bquiet hours\b", r"\beach\b", r"\bhow do i\b",
    r"\bwhat time\b", r"\bwhere is\b", r"\bwhere do\b",
    # Greetings & acknowledgements
    r"^\s*hi+\b", r"^\s*hey+\b", r"^\s*hello+\b", r"\bgood\s+(morning|afternoon|evening|night)\b",
    r"^\s*ok(ay)?\s*$", r"^\s*ok(ay)?\b", r"^\s*sure\b", r"^\s*sounds\s+good\b",
    r"^\s*got\s+it\b", r"^\s*noted\b", r"^\s*understood\b", r"^\s*perfect\b",
    r"^\s*great\b", r"^\s*awesome\b", r"^\s*thanks?\b", r"\bthank\s+you\b",
    r"\bmany\s+thanks\b", r"\bthanks\s+a\s+lot\b", r"\bmuch\s+appreciated\b",
    r"^\s*no\s+problem\b", r"^\s*np\b", r"^\s*alright\b", r"^\s*cool\b",
    # Common guest requests
    r"\bcan\s+(you|i|we)\b", r"\bis\s+there\b", r"\bdo\s+you\s+have\b",
    r"\bhair\s*dryer\b", r"\btowel\b", r"\bpillow\b", r"\bblanket\b", r"\bsoap\b",
    r"\bshampoo\b", r"\btoilet\s+paper\b", r"\bdishes\b", r"\bcoffee\b", r"\bkitchen\b",
    r"\blaundry\b", r"\bwasher\b", r"\bdryer\b", r"\biron\b", r"\btelevision\b", r"\btv\b",
    r"\bremote\b", r"\bnetflix\b", r"\brestaurant\b", r"\brecommend\b", r"\bnearby\b",
    r"\bhow\s+far\b", r"\bhow\s+long\b", r"\bwhat\s+is\b", r"\bcan\s+we\b",
    r"\bearly\s+check.?in\b", r"\blate\s+check.?out\b", r"\bextend\b",
    r"\bextra\b", r"\bmore\b", r"\bneed\b", r"\brequest\b",
    # Location & surroundings
    r"\bhow\s+to\s+get\s+(there|to)\b", r"\bget\s+to\s+your\b", r"\bget\s+there\b",
    r"\bgoogle\s+maps?\b", r"\blocation\b", r"\bmap\b", r"\bcoordinates?\b",
    r"\bwhats?\s+around\b", r"\bwhat'?s\s+nearby\b", r"\bsurrounding\b",
    r"\bdistance\s+(to|from)\b", r"\bwalk(ing)?\s+(distance|to|from)\b",
    r"\bnear\s+(the\s+)?property\b", r"\bclose\s+(by|to)\b", r"\bneighbourhood\b", r"\bneighborhood\b",
    r"\bsights?\b", r"\battraction\b", r"\btourist\b", r"\bexplore\b",
    r"\bsupermarket\b", r"\bgrocery\b", r"\bpharmacy\b", r"\bhospital\b.*\bnear\b",
    r"\bgas\s+station\b", r"\bpetrol\b", r"\bbus\s+stop\b", r"\bmetro\b", r"\bsubway\b",
    r"\btaxi\b", r"\buber\b", r"\blyft\b", r"\bairport\b", r"\btrain\s+station\b",
]
_COMPLEX = [
    r"\brefund\b", r"\bcomplaint\b", r"\bbroken\b", r"\bdirty\b", r"\bdisappoint\b",
    r"\bnot working\b", r"\bdamage\b", r"\bmissing\b", r"\bnot as described\b",
    r"\bmisled\b", r"\bairbnb support\b", r"\bescalat\b", r"\bunacceptable\b",
    r"\bawful\b", r"\bterrible\b", r"\bhorrible\b", r"\bfraud\b", r"\bscam\b",
    r"\bbug\b", r"\bpest\b", r"\bmold\b", r"\bleak\b",
]
_AC_PATTERNS         = [r"\bac\b", r"\bair.?con", r"\bhvac\b", r"\bcooling\b", r"\bheat(ing)?\b", r"\bfurnace\b", r"\bthermostat\b"]
_PLUMBING_PATTERNS   = [r"\bleak\b", r"\bpipe\b", r"\btoilet\b", r"\bplumb", r"\bdrain\b", r"\bflood(ing)?\b", r"\bwater\s+(damage|leak|drip)"]
_ELECTRICAL_PATTERNS = [r"\belectr", r"\bpower\s+out", r"\boutlet\b", r"\btripped?\b", r"\bcircuit\b", r"\bfuse\b", r"\bblackout\b", r"\bno\s+power\b"]
_LOCKSMITH_PATTERNS  = [r"\blocked\s+out\b", r"\bcan.?t\s+get\s+in\b", r"\bkey\s+broke", r"\bdoor\s+won.?t\s+open", r"\bsmartlock\b", r"\bkeypad\s+not\s+work"]

_SKILL_CMD_MAP     = {"checkin": "/checkin", "cleaner-brief": "/cleaner-brief", "reply": "/reply", "complaint": "/complaint"}
_CALENDAR_SKILLS   = {"checkin", "cleaner-brief"}
_MAX_RETRIES       = 3
_RETRY_DELAYS      = [2, 4, 8]

# Patterns that trigger human escalation regardless of normal classification
_ESCALATION = [
    r"\bsue\b", r"\blawyer\b", r"\blegal\b", r"\bpolice\b", r"\brefund\s+now\b",
    r"\bthis\s+is\s+unacceptable\b", r"\bi('m| am)\s+furious\b", r"\bi('m| am)\s+disgusted\b",
    r"\bmedical\s+(emergency|attention|help)\b", r"\bambulance\b", r"\bhospital\b",
    r"\bfire\b.*\balarm\b", r"\bgas\s+leak\b", r"\bflood(ing)?\b", r"\bemergency\b",
    r"\bcall\s+the\s+cops\b", r"\breporting\s+you\b", r"\bchargeback\b",
]

_MULTILINGUAL_RULE = """
LANGUAGE RULE: Detect the language the guest is writing in. Always reply in the SAME language as the guest's message. If the guest writes in French, reply in French. If the guest writes in Spanish, reply in Spanish. If the guest writes in Arabic, reply in Arabic. The property information above is in English — translate your response for the guest automatically. The menu, house rules, and FAQ content should be translated on-the-fly as needed.
"""

_LANGUAGE_NAMES = {
    "en": "English", "es": "Spanish", "fr": "French", "de": "German",
    "pt": "Portuguese", "it": "Italian", "nl": "Dutch", "ar": "Arabic",
    "ja": "Japanese", "ko": "Korean", "zh": "Mandarin Chinese", "ru": "Russian",
}

_UPSELL_SYSTEM_PROMPT = """
You are a warm, concise hospitality assistant. Generate a short, personalized offer message for a guest.
Be friendly and specific to their stay context. Keep it to 2–4 sentences.
End with a clear yes/no question or action prompt. Include the price if provided.
Detect and match the guest's likely language from any prior context clues.
Do NOT hard-sell or repeat the same line twice. Do NOT mention competitors.
"""

_SALES_SYSTEM_PROMPT = """
You are {persona_name}'s knowledgeable and friendly booking assistant for a short-term rental property.
Your job is to answer pre-booking questions, quote pricing, check availability, and guide potential guests toward making a booking.

Rules:
- Never invent prices, fees, or availability. Use only the context provided.
- If a date range is requested, reference the availability data to confirm or decline clearly.
- Always close with a call-to-action pointing to the booking link (if provided) or asking the guest to confirm interest.
- Be warm, brief, and helpful without being pushy.
- Detect the guest's language and reply in the same language.
- Do NOT mention or compare with other platforms or competitors.
"""

_GUEST_CONTEXT_RULE = """
GUEST CONTEXT RULES:
- Treat host-provided FAQ, house rules, food menu, nearby recommendations, and custom instructions as the operating manual for this property.
- Guests may ask about WiFi, check-in, check-out, parking, directions, amenities, food, local recommendations, extra towels, housekeeping, maintenance issues, late arrival, early check-in, late checkout, or help finding their room/unit.
- If reservation context includes a room / unit / property number, use it naturally when it improves the answer.
- Never reveal other guests' data or invent a room number. If a message depends on a room and no room is mapped, ask for a short confirmation.
- If the host has mapped a phone number to a reservation, assume that reservation context belongs to the guest using that phone unless the guest clearly says otherwise.
- When a guest raises a problem, acknowledge it, use the mapped stay context if available, and keep the reply specific to their room/unit and booking dates.
"""


def build_property_context(cfg) -> str:
    """Build a property context block from a TenantConfig (or similar duck-typed object)."""
    if not cfg:
        return ""
    parts = []
    if getattr(cfg, "property_names", None):
        parts.append(f"Property name: {cfg.property_names}")
    if getattr(cfg, "property_type", None):
        parts.append(f"Property type: {cfg.property_type}")
    if getattr(cfg, "property_city", None):
        loc = cfg.property_city
        if getattr(cfg, "google_maps_url", None):
            loc += f" — Google Maps: {cfg.google_maps_url}"
        parts.append(f"Location: {loc}")
    if getattr(cfg, "check_in_time", None) or getattr(cfg, "check_out_time", None):
        ci = getattr(cfg, "check_in_time", None) or "flexible"
        co = getattr(cfg, "check_out_time", None) or "flexible"
        parts.append(f"Check-in: {ci}  |  Check-out: {co}")
    if getattr(cfg, "max_guests", None):
        parts.append(f"Max guests: {cfg.max_guests}")
    if getattr(cfg, "amenities", None):
        parts.append(f"Amenities: {cfg.amenities}")
    if getattr(cfg, "house_rules", None):
        parts.append(f"House rules:\n{cfg.house_rules}")
    policy_context = build_structured_policy_context(cfg)
    if policy_context:
        parts.append(policy_context)
    if getattr(cfg, "food_menu", None):
        parts.append(f"Food menu / restaurant:\n{cfg.food_menu}")
    if getattr(cfg, "nearby_restaurants", None):
        parts.append(f"Nearby restaurant recommendations:\n{cfg.nearby_restaurants}")
    if getattr(cfg, "faq", None):
        parts.append(f"FAQ / common questions:\n{cfg.faq}")
    if getattr(cfg, "custom_instructions", None):
        parts.append(f"Special host instructions:\n{cfg.custom_instructions}")
    if not parts:
        return ""
    return "<property_context>\n" + "\n\n".join(parts) + "\n</property_context>"


def needs_escalation(text: str) -> bool:
    """Return True if the guest message contains patterns requiring immediate human attention."""
    lower = text.lower()
    return any(re.search(p, lower) for p in _ESCALATION)


def classify_message(text: str) -> str:
    lower = text.lower()
    if any(re.search(p, lower) for p in _COMPLEX):
        return "complex"
    if any(re.search(p, lower) for p in _ROUTINE):
        return "routine"
    return "complex"


def classify_message_with_confidence(text: str) -> tuple[str, float, list[str]]:
    """
    Classify a guest message and return (msg_type, confidence, matched_patterns).

    confidence is 0.0–1.0:
      - 1.0 = escalation (override, always human)
      - >0.5 = clearly routine (multiple pattern hits)
      - 0.5 = boundary / single hit
      - <0.5 = complex / ambiguous (fewer hits or conflict)

    matched_patterns is a list of human-readable labels for the "why" tooltip.
    """
    lower = text.lower()
    sources: list[str] = []

    if needs_escalation(text):
        return "escalation", 1.0, ["escalation trigger"]

    complex_hits  = [p for p in _COMPLEX  if re.search(p, lower)]
    routine_hits  = [p for p in _ROUTINE  if re.search(p, lower)]

    for p in complex_hits:
        sources.append(f"complex: {p.strip(chr(92) + 'b').strip('()?')}")
    for p in routine_hits:
        sources.append(f"routine: {p.strip(chr(92) + 'b').strip('()?')}")

    total = len(complex_hits) + len(routine_hits)

    if complex_hits and not routine_hits:
        # Pure complex signal
        conf = min(0.45 + 0.05 * len(complex_hits), 0.49)
        return "complex", round(conf, 2), sources

    if routine_hits and not complex_hits:
        # Pure routine signal
        conf = min(0.55 + 0.05 * len(routine_hits), 0.95)
        return "routine", round(conf, 2), sources

    if not total:
        # No keyword signal — default to routine so the bot replies.
        # Only _COMPLEX keywords escalate to host review; general chat should get a response.
        return "routine", 0.55, ["no complaint keywords — general chat"]

    # Mixed signals — whichever dominates
    if len(routine_hits) > len(complex_hits):
        ratio = len(routine_hits) / total
        return "routine", round(0.5 + 0.4 * (ratio - 0.5), 2), sources
    else:
        ratio = len(complex_hits) / total
        return "complex", round(0.5 - 0.4 * (ratio - 0.5), 2), sources


def extract_context_sources(cfg) -> list[str]:
    """Return a list of context fields that are populated for this tenant config."""
    fields = [
        ("property_names",       "Property name"),
        ("property_type",        "Property type"),
        ("property_city",        "Location"),
        ("check_in_time",        "Check-in time"),
        ("check_out_time",       "Check-out time"),
        ("house_rules",          "House rules"),
        ("pet_policy",           "Pet policy"),
        ("refund_policy",        "Refund policy"),
        ("early_checkin_policy", "Early check-in policy"),
        ("late_checkout_policy", "Late checkout policy"),
        ("parking_policy",       "Parking policy"),
        ("smoking_policy",       "Smoking policy"),
        ("quiet_hours",          "Quiet hours"),
        ("faq",                  "FAQ"),
        ("amenities",            "Amenities"),
        ("food_menu",            "Food menu"),
        ("nearby_restaurants",   "Nearby restaurants"),
        ("custom_instructions",  "Custom instructions"),
    ]
    return [label for attr, label in fields if getattr(cfg, attr, None)]


def detect_vendor_type(text: str) -> Optional[str]:
    lower = text.lower()
    for patterns, name in [
        (_AC_PATTERNS, "ac_technicians"),
        (_PLUMBING_PATTERNS, "plumbers"),
        (_ELECTRICAL_PATTERNS, "electricians"),
        (_LOCKSMITH_PATTERNS, "locksmiths"),
    ]:
        if any(re.search(p, lower) for p in patterns):
            return name
    return None


import json

def analyze_sentiment_and_intent_llm(tenant_id: str, text: str) -> dict:
    """Uses the OpenRouter sentiment model to do JSON structured sentiment analysis.
    Retries with fallback model before falling back to regex-based analysis."""
    from web.workflow import analyze_guest_sentiment as fallback_analyze
    from web.crypto import decrypt
    from web.db import SessionLocal
    from web.models import APIUsageLog
    from web.system_config_store import load_system_config
    from web.rate_limiter import check_rate_limit
    import openai

    if not text.strip():
        return {"label": "neutral", "score": 0.0}

    db = SessionLocal()
    try:
        sys_conf = load_system_config(db)
        if not sys_conf or not sys_conf.openrouter_api_key_enc or sys_conf.openrouter_api_key_enc == "********":
            return fallback_analyze(text)

        # Check daily cost rate limit before making the call (estimate ~200 input, 50 output tokens)
        estimated_cost = _calc_model_cost("openai/gpt-4o-mini", 200, 50)
        daily_cost_check = check_rate_limit(db, tenant_id, "daily_cost", cost_increment=estimated_cost)
        if not daily_cost_check["allowed"]:
            from web.cost_tracker import log_rate_limit_blocked
            log_rate_limit_blocked(db, tenant_id, daily_cost_check["reason"])
            return fallback_analyze(text)

        # Decrypt the API key (was previously using raw encrypted value!)
        client = openai.OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=(decrypt(sys_conf.openrouter_api_key_enc) or sys_conf.openrouter_api_key_enc),
            default_headers={
                "HTTP-Referer": "https://hostai.app",
                "X-OpenRouter-Title": "HostAI",
            },
        )

        # Strip basic PII shapes (phone numbers and emails)
        import re
        safe_text = text
        safe_text = re.sub(r'\+?\d(?:[\d\-\s\(\)]{8,})\d', '[PHONE REDACTED]', safe_text)
        safe_text = re.sub(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,7}\b', '[EMAIL REDACTED]', safe_text)

        prompt = (
            "Analyze the sentiment of the following guest message. "
            "Return ONLY valid JSON with exactly two keys: 'label' (string: 'positive', 'negative', or 'neutral') "
            "and 'score' (float between -1.0 for very negative and 1.0 for very positive). "
            f"Message: {safe_text}"
        )

        # Try sentiment_model first, then fallback_model, before falling back to regex
        models_to_try = [
            sys_conf.sentiment_model or "openai/gpt-4o-mini",
            sys_conf.fallback_model or "meta-llama/llama-3.3-70b-instruct",
        ]

        last_exc = None
        for model in models_to_try:
            try:
                resp = client.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": prompt}],
                    response_format={"type": "json_object"},
                    temperature=0.0
                )

                usage = resp.usage
                if usage:
                    log_entry = APIUsageLog(
                        tenant_id=tenant_id,
                        service="openrouter",
                        operation=f"sentiment_analysis:{model}",
                        input_tokens=usage.prompt_tokens,
                        output_tokens=usage.completion_tokens,
                        cost_usd=_calc_model_cost(model, usage.prompt_tokens, usage.completion_tokens),
                    )
                    db.add(log_entry)
                    db.commit()

                content = resp.choices[0].message.content
                result = json.loads(content)

                label = result.get("label", "neutral")
                score = float(result.get("score", 0.0))
                return {"label": label, "score": score}
            except Exception as exc:
                last_exc = exc
                log.warning(f"Sentiment model {model} failed: {exc}. Trying next...")

        # All LLM models failed, fall back to regex-based analysis
        log.warning(f"All LLM sentiment models failed. Falling back to regex-based analysis. Last error: {last_exc}")
        return fallback_analyze(text)
    except Exception as exc:
        log.warning(f"Sentiment analysis error (using regex fallback): {exc}")
        return fallback_analyze(text)
    finally:
        db.close()


def generate_draft(guest_name: str, message: str, msg_type: str, skill: Optional[str] = None, property_context: str = "", tenant_id: Optional[str] = None, history: Optional[list] = None) -> str:
    """Generate draft via OpenRouter configured centrally by administrator."""
    from web.db import SessionLocal
    from web.models import APIUsageLog, TenantConfig
    from web.system_config_store import load_system_config
    from datetime import timedelta
    import openai

    skill_cmd  = _SKILL_CMD_MAP.get(skill) or ("/reply" if msg_type == "routine" else "/complaint")
    max_tokens = 1024 if skill in _CALENDAR_SKILLS else 512

    # Build dynamic system prompt: base + per-tenant property context + language rule
    system = SYSTEM_PROMPT
    if property_context:
        system = system + "\n\n" + property_context

    # Resolve language rule: check TenantConfig overrides
    lang_rule = _MULTILINGUAL_RULE
    if tenant_id:
        try:
            from web.db import SessionLocal
            from web.models import TenantConfig as _TC
            with SessionLocal() as _db:
                _cfg = _db.query(_TC).filter_by(tenant_id=tenant_id).first()
                if _cfg:
                    pref = getattr(_cfg, "preferred_reply_language", None)
                    sec  = getattr(_cfg, "secondary_reply_language", None)
                    lang_override = None
                    if pref and pref in _LANGUAGE_NAMES:
                        lang_override = pref
                        lang_rule = f"LANGUAGE RULE: Always reply in {_LANGUAGE_NAMES[pref]} regardless of the guest's language."
                    elif sec and sec in _LANGUAGE_NAMES:
                        lang_rule = (
                            "LANGUAGE RULE: Detect guest language and reply in that language. "
                            f"Also append a translation in {_LANGUAGE_NAMES[sec]} below a '---' divider."
                        )
                    # check default_response_language as fallback
                    if not lang_override and hasattr(_cfg, 'default_response_language') and _cfg.default_response_language:
                        lang_override = _cfg.default_response_language
                        if lang_override in _LANGUAGE_NAMES and lang_override != "en":
                            lang_rule = f"LANGUAGE RULE: Always reply in {_LANGUAGE_NAMES[lang_override]} regardless of the guest's language."
        except Exception:
            pass

    system = system + "\n\n" + lang_rule + "\n\n" + _GUEST_CONTEXT_RULE

    safe_guest_name = "Guest" if guest_name else ""
    
    # Strip basic PII shapes (phone numbers and emails)
    import re
    safe_message = message
    safe_message = re.sub(r'\+?\d(?:[\d\-\s\(\)]{8,})\d', '[PHONE REDACTED]', safe_message)
    safe_message = re.sub(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,7}\b', '[EMAIL REDACTED]', safe_message)

    user_content = (
        f"[Automated pipeline — use {skill_cmd} flow]\n\n"
        f"<guest_name>{safe_guest_name}</guest_name>\n\n"
        f"<context>\n{safe_message}\n</context>\n\n"
        "Return ONLY the output text ready to send or use. No headings, no meta-commentary. Just the content."
    )

    with SessionLocal() as db:
        sys_conf = load_system_config(db)

        # Check free tier usage limits
        if tenant_id:
            cfg = db.query(TenantConfig).filter_by(tenant_id=tenant_id).first()
            if cfg and cfg.subscription_plan == "free":
                now = datetime.now(timezone.utc)

                # Reset daily counter if needed
                if cfg.ai_calls_today_date is None or (now - cfg.ai_calls_today_date).days >= 1:
                    cfg.ai_calls_today = 0
                    cfg.ai_calls_today_date = now

                # Reset monthly counter if needed
                if cfg.ai_calls_monthly_date is None or (now.year, now.month) != (cfg.ai_calls_monthly_date.year, cfg.ai_calls_monthly_date.month):
                    cfg.ai_calls_monthly = 0
                    cfg.ai_calls_monthly_date = now

                # Check limits: 10/day, 50/month for free tier
                if cfg.ai_calls_today >= 10:
                    raise RuntimeError("Free tier daily AI call limit (10) reached. Upgrade to unlock unlimited drafts.")
                if cfg.ai_calls_monthly >= 50:
                    raise RuntimeError("Free tier monthly AI call limit (50) reached. Upgrade to unlock unlimited drafts.")

        # Check daily cost rate limit before making OpenRouter call
        if tenant_id:
            from web.rate_limiter import check_rate_limit
            from web.cost_tracker import log_rate_limit_blocked
            estimated_cost = _calc_model_cost("anthropic/claude-3.7-sonnet", 800, 300)
            daily_cost_check = check_rate_limit(db, tenant_id, "daily_cost", cost_increment=estimated_cost)
            if not daily_cost_check["allowed"]:
                log_rate_limit_blocked(db, tenant_id, daily_cost_check["reason"])
                return "[Message couldn't be processed due to service limits. Please try again later.]"

        # OpenRouter is globally configured by administrator
        if sys_conf and sys_conf.openrouter_api_key_enc:
            client = openai.OpenAI(
                base_url="https://openrouter.ai/api/v1",
                api_key=(decrypt(sys_conf.openrouter_api_key_enc) or sys_conf.openrouter_api_key_enc),
                default_headers={
                    "HTTP-Referer": "https://hostai.app",
                    "X-OpenRouter-Title": "HostAI",
                },
            )

            last_exc = None
            for attempt, delay in zip(range(1, _MAX_RETRIES + 1), _RETRY_DELAYS):

                # Phase 3: Smart Routing
                if attempt == 1:
                    if msg_type == "routine":
                        model_to_use = sys_conf.routine_model or "google/gemini-2.5-flash"
                    elif msg_type == "escalation":
                        model_to_use = "anthropic/claude-3-opus"  # Max intelligence for critical
                    else:
                        model_to_use = sys_conf.primary_model or "anthropic/claude-3.7-sonnet"
                else:
                    # On failure, fallback to Llama (reliable fallback)
                    model_to_use = sys_conf.fallback_model or "meta-llama/llama-3.3-70b-instruct"

                try:
                    _messages = [{"role": "system", "content": system}]
                    if history:
                        _messages.extend(history[-20:])  # keep last 20 turns max
                    _messages.append({"role": "user", "content": user_content})
                    resp = client.chat.completions.create(
                        model=model_to_use,
                        max_tokens=max_tokens,
                        messages=_messages,
                    )
                    content = resp.choices[0].message.content
                    if not content:
                        raise ValueError(f"Empty content from {model_to_use}")
                    
                    # Post-process: attempt to re-inject real name if 'Guest' was used
                    if guest_name:
                        content = content.replace("Guest", guest_name)

                    # Log usage with real cost
                    _in_tok  = resp.usage.prompt_tokens     if (hasattr(resp, 'usage') and resp.usage) else 0
                    _out_tok = resp.usage.completion_tokens if (hasattr(resp, 'usage') and resp.usage) else 0
                    log_entry = APIUsageLog(
                        tenant_id=tenant_id,
                        service="openrouter",
                        operation=f"generate_draft:{model_to_use}",
                        input_tokens=_in_tok,
                        output_tokens=_out_tok,
                        cost_usd=_calc_model_cost(model_to_use, _in_tok, _out_tok),
                    )
                    db.add(log_entry)

                    # Increment free tier usage counters
                    if tenant_id:
                        cfg = db.query(TenantConfig).filter_by(tenant_id=tenant_id).first()
                        if cfg and cfg.subscription_plan == "free":
                            cfg.ai_calls_today += 1
                            cfg.ai_calls_monthly += 1
                            db.add(cfg)

                    db.commit()

                    return content.strip()
                except Exception as exc:
                    last_exc = exc
                    log.warning("OpenRouter API attempt %d (model: %s) failed: %s — retrying in %ds", attempt, model_to_use, exc, delay)
                    time.sleep(delay)

            raise RuntimeError(f"OpenRouter API failed after {_MAX_RETRIES} attempts: {last_exc}")

        # Phase 5: BYOK — try tenant's own Anthropic key if global key not set
        if tenant_id:
            cfg = db.query(TenantConfig).filter_by(tenant_id=tenant_id).first()
            if cfg and cfg.anthropic_api_key_enc:
                _api_key = decrypt(cfg.anthropic_api_key_enc)
                if _api_key:
                    _client = anthropic.Anthropic(api_key=_api_key)
                    _resp = _client.messages.create(
                        model="claude-3-5-sonnet-20241022",
                        max_tokens=max_tokens,
                        system=system,
                        messages=[{"role": "user", "content": user_content}]
                    )
                    return _resp.content[0].text.strip()
        raise RuntimeError("HostAI reply engine is not configured. Ask your admin to add an OpenRouter key, or add your own Anthropic key in Settings.")


def make_draft_id(source: str) -> str:
    return f"{source}_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}"


def generate_upsell_pitch(reservation, offer, cfg, tenant_id: Optional[str] = None) -> str:
    """Generate a personalized upsell offer message for a guest."""
    from web.db import SessionLocal
    from web.models import SystemConfig, APIUsageLog
    import openai

    price_str = f"${offer.price_usd:.0f}" if offer.price_usd else "ask about pricing"
    checkin_str = str(reservation.checkin) if reservation.checkin else "upcoming"
    system = _UPSELL_SYSTEM_PROMPT

    user_content = (
        f"Guest name: {reservation.guest_name}\n"
        f"Check-in: {checkin_str}\n"
        f"Offer: {offer.name}\n"
        f"Offer description: {offer.description or ''}\n"
        f"Price: {price_str}\n\n"
        "Generate a short, warm upsell message to send this guest."
    )

    with SessionLocal() as db:
        sys_conf = db.query(SystemConfig).first()
        if not sys_conf or not sys_conf.openrouter_api_key_enc:
            return f"Hi {reservation.guest_name}! Would you like to add {offer.name} to your stay? {price_str}. Let us know!"

        client = openai.OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=decrypt(sys_conf.openrouter_api_key_enc),
        )
        try:
            resp = client.chat.completions.create(
                model=sys_conf.routine_model or "google/gemini-2.5-flash",
                max_tokens=200,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user_content},
                ],
            )
            content = resp.choices[0].message.content or ""
            db.add(APIUsageLog(
                tenant_id=tenant_id,
                model=sys_conf.routine_model,
                provider="openrouter",
                input_tokens=resp.usage.prompt_tokens if resp.usage else 0,
                output_tokens=resp.usage.completion_tokens if resp.usage else 0,
                feature="upsell_pitch",
            ))
            db.commit()
            return content.strip()
        except Exception as exc:
            log.warning("Upsell pitch generation failed: %s", exc)
            return f"Hi {reservation.guest_name}! Would you like to add {offer.name} to your stay? {price_str}. Let us know!"


def generate_sales_reply(
    persona_name: str,
    lead_name: Optional[str],
    message: str,
    conversation_history: list[dict],
    property_context: str,
    availability_context: str,
    pricing_note: Optional[str],
    booking_link: Optional[str],
    tenant_id: Optional[str] = None,
) -> str:
    """Generate a sales AI reply for a pre-booking inquiry."""
    from web.db import SessionLocal
    from web.models import SystemConfig, APIUsageLog
    import openai

    system = _SALES_SYSTEM_PROMPT.format(persona_name=persona_name)
    if property_context:
        system += "\n\n" + property_context
    if pricing_note:
        system += f"\n\nPRICING: {pricing_note}"
    if availability_context:
        system += f"\n\nAVAILABILITY:\n{availability_context}"
    if booking_link:
        system += f"\n\nBOOKING LINK: {booking_link}"

    messages: list[dict] = [{"role": "system", "content": system}]
    for turn in conversation_history[-10:]:
        messages.append({"role": turn["role"], "content": turn["content"]})
    messages.append({"role": "user", "content": message})

    with SessionLocal() as db:
        sys_conf = db.query(SystemConfig).first()
        if not sys_conf or not sys_conf.openrouter_api_key_enc:
            return "Thanks for your interest! Please use our booking link to check availability and complete your reservation."

        client = openai.OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=decrypt(sys_conf.openrouter_api_key_enc),
        )
        try:
            resp = client.chat.completions.create(
                model=sys_conf.primary_model or "anthropic/claude-3.5-sonnet",
                max_tokens=400,
                messages=messages,
            )
            content = resp.choices[0].message.content or ""
            db.add(APIUsageLog(
                tenant_id=tenant_id,
                model=sys_conf.primary_model,
                provider="openrouter",
                input_tokens=resp.usage.prompt_tokens if resp.usage else 0,
                output_tokens=resp.usage.completion_tokens if resp.usage else 0,
                feature="sales_reply",
            ))
            db.commit()
            return content.strip()
        except Exception as exc:
            log.warning("Sales reply generation failed: %s", exc)
            return "Thanks for your interest! Please use our booking link to check availability and complete your reservation."
