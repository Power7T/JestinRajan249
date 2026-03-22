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

import anthropic

from web.workflow import build_structured_policy_context

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
    r"\bwifi\b", r"\bwi-?fi\b", r"\bpassword\b", r"\bcheck.?in\b", r"\bcheck.?out\b",
    r"\barrive\b", r"\barrival\b", r"\beta\b", r"\bparking\b", r"\bdirections?\b",
    r"\baddress\b", r"\bcode\b", r"\baccess\b", r"\bkeypad\b", r"\bamenities?\b",
    r"\bpool\b", r"\bgym\b", r"\bquiet hours\b", r"\beach\b", r"\bhow do i\b",
    r"\bwhat time\b", r"\bwhere is\b", r"\bwhere do\b",
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
        parts.append(f"Location: {cfg.property_city}")
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
        # No signal at all — treat as complex but low confidence
        return "complex", 0.30, ["no matching patterns"]

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
    """Uses the OpenRouter sentiment model to do JSON structured sentiment analysis."""
    from web.workflow import analyze_guest_sentiment as fallback_analyze
    from web.db import SessionLocal
    from web.models import SystemConfig, ApiUsageLog
    import openai
    
    if not text.strip():
        return {"label": "neutral", "score": 0.0}
        
    db = SessionLocal()
    try:
        sys_conf = db.query(SystemConfig).first()
        if not sys_conf or not sys_conf.openrouter_api_key_enc or sys_conf.openrouter_api_key_enc == "********":
            return fallback_analyze(text)
            
        apiKey = sys_conf.openrouter_api_key_enc
        model = sys_conf.sentiment_model or "openai/gpt-4o-mini"
        
        client = openai.OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=apiKey,
        )
        
        prompt = (
            "Analyze the sentiment of the following guest message. "
            "Return ONLY valid JSON with exactly two keys: 'label' (string: 'positive', 'negative', or 'neutral') "
            "and 'score' (float between -1.0 for very negative and 1.0 for very positive). "
            f"Message: {text}"
        )
        
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0.0
        )
        
        usage = resp.usage
        if usage:
            cost = 0.0
            if "gpt-4o-mini" in model:
                cost = (usage.prompt_tokens / 1000000 * 0.15) + (usage.completion_tokens / 1000000 * 0.60)
            log_entry = ApiUsageLog(
                tenant_id=tenant_id,
                feature="sentiment_analysis",
                model=model,
                provider="openrouter",
                input_tokens=usage.prompt_tokens,
                output_tokens=usage.completion_tokens,
                cost_usd=cost
            )
            db.add(log_entry)
            db.commit()
            
        content = resp.choices[0].message.content
        result = json.loads(content)
        
        label = result.get("label", "neutral")
        score = float(result.get("score", 0.0))
        return {"label": label, "score": score}
        
    except Exception as exc:
        log.warning(f"LLM Sentiment fallback to regex due to error: {exc}")
        return fallback_analyze(text)
    finally:
        db.close()


def generate_draft(api_key: str, guest_name: str, message: str, msg_type: str, skill: str = None, property_context: str = "", tenant_id: str = None) -> str:
    """Generate draft via OpenRouter if configured centrally, else default to Tenant's Anthropic Key."""
    from web.db import SessionLocal
    from web.models import SystemConfig, ApiUsageLog
    import openai

    skill_cmd  = _SKILL_CMD_MAP.get(skill) or ("/reply" if msg_type == "routine" else "/complaint")
    max_tokens = 1024 if skill in _CALENDAR_SKILLS else 512

    # Build dynamic system prompt: base + per-tenant property context + multilingual rule
    system = SYSTEM_PROMPT
    if property_context:
        system = system + "\n\n" + property_context
    system = system + "\n\n" + _MULTILINGUAL_RULE + "\n\n" + _GUEST_CONTEXT_RULE

    user_content = (
        f"[Automated pipeline — use {skill_cmd} flow]\n\n"
        f"<guest_name>{guest_name}</guest_name>\n\n"
        f"<context>\n{message}\n</context>\n\n"
        "Return ONLY the output text ready to send or use. No headings, no meta-commentary. Just the content."
    )

    with SessionLocal() as db:
        sys_conf = db.query(SystemConfig).first()

        # If OpenRouter is globally configured inside the local DB -> use hot-swapping router
        if sys_conf and sys_conf.openrouter_api_key_enc:
            client = openai.OpenAI(
                base_url="https://openrouter.ai/api/v1",
                api_key=sys_conf.openrouter_api_key_enc
            )

            last_exc = None
            for attempt, delay in zip(range(1, _MAX_RETRIES + 1), _RETRY_DELAYS):
                
                # Phase 3: Smart Routing
                if attempt == 1:
                    if msg_type == "routine":
                        model_to_use = sys_conf.fallback_model or "meta-llama/llama-3.1-70b-instruct"
                    elif msg_type == "escalation":
                        model_to_use = "anthropic/claude-3-opus"  # Max intelligence for critical
                    else:
                        model_to_use = sys_conf.primary_model or "anthropic/claude-3.5-sonnet"
                else:
                    # On failure, always fallback to the reliable cheap model
                    model_to_use = sys_conf.fallback_model or "meta-llama/llama-3.1-70b-instruct"
                
                try:
                    resp = client.chat.completions.create(
                        model=model_to_use,
                        max_tokens=max_tokens,
                        messages=[
                            {"role": "system", "content": system},
                            {"role": "user", "content": user_content}
                        ],
                    )
                    content = resp.choices[0].message.content
                    if not content:
                        raise ValueError(f"Empty content from {model_to_use}")
                    
                    # Log usage
                    log_entry = ApiUsageLog(
                        tenant_id=tenant_id,
                        model=model_to_use,
                        provider="openrouter",
                        input_tokens=resp.usage.prompt_tokens if hasattr(resp, 'usage') else 0,
                        output_tokens=resp.usage.completion_tokens if hasattr(resp, 'usage') else 0,
                        feature="generate_draft"
                    )
                    db.add(log_entry)
                    db.commit()

                    return content.strip()
                except Exception as exc:
                    last_exc = exc
                    log.warning("OpenRouter API attempt %d (model: %s) failed: %s — retrying in %ds", attempt, model_to_use, exc, delay)
                    time.sleep(delay)
            
            raise RuntimeError(f"OpenRouter API failed after {_MAX_RETRIES} attempts: {last_exc}")

        # Fallback to Tenant API Key (original behavior)
        client = anthropic.Anthropic(api_key=api_key)
        last_exc = None
        for attempt, delay in zip(range(1, _MAX_RETRIES + 1), _RETRY_DELAYS):
            try:
                resp = client.messages.create(
                    model="claude-3-5-sonnet-20241022",
                    max_tokens=max_tokens,
                    system=system,
                    messages=[{"role": "user", "content": user_content}],
                )
                if not resp.content or not resp.content[0].text:
                    raise ValueError("Empty response from Claude API")
                
                log_entry = ApiUsageLog(
                    tenant_id=tenant_id,
                    model="claude-3-5-sonnet-20241022",
                    provider="anthropic",
                    input_tokens=resp.usage.input_tokens if hasattr(resp, 'usage') else 0,
                    output_tokens=resp.usage.output_tokens if hasattr(resp, 'usage') else 0,
                    feature="generate_draft"
                )
                db.add(log_entry)
                db.commit()

                return resp.content[0].text.strip()
            except Exception as exc:
                last_exc = exc
                log.warning("Claude API attempt %d failed: %s — retrying in %ds", attempt, exc, delay)
                time.sleep(delay)
        raise RuntimeError(f"Claude API failed after {_MAX_RETRIES} attempts: {last_exc}")


def make_draft_id(source: str) -> str:
    return f"{source}_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}"
