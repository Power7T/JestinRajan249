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

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Load SKILL.md system prompt (strips YAML frontmatter)
# ---------------------------------------------------------------------------
_SKILL_MD = pathlib.Path(__file__).parent.parent / "SKILL.md"
_raw      = _SKILL_MD.read_text()
_parts    = _raw.split("---", 2)
SYSTEM_PROMPT = _parts[2].strip() if len(_parts) >= 3 else _raw

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


def generate_draft(api_key: str, guest_name: str, message: str, msg_type: str, skill: str = None, property_context: str = "") -> str:
    """Call Claude API with tenant's own key and return draft text."""
    client     = anthropic.Anthropic(api_key=api_key)
    skill_cmd  = _SKILL_CMD_MAP.get(skill) or ("/reply" if msg_type == "routine" else "/complaint")
    max_tokens = 1024 if skill in _CALENDAR_SKILLS else 512

    # Build dynamic system prompt: base + per-tenant property context + multilingual rule
    system = SYSTEM_PROMPT
    if property_context:
        system = system + "\n\n" + property_context
    system = system + "\n\n" + _MULTILINGUAL_RULE

    user_content = (
        f"[Automated pipeline — use {skill_cmd} flow]\n\n"
        f"<guest_name>{guest_name}</guest_name>\n\n"
        f"<context>\n{message}\n</context>\n\n"
        "Return ONLY the output text ready to send or use. No headings, no meta-commentary. Just the content."
    )
    last_exc = None
    for attempt, delay in zip(range(1, _MAX_RETRIES + 1), _RETRY_DELAYS):
        try:
            resp = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user_content}],
            )
            if not resp.content or not resp.content[0].text:
                raise ValueError("Empty response from Claude API")
            return resp.content[0].text.strip()
        except Exception as exc:
            last_exc = exc
            log.warning("Claude API attempt %d failed: %s — retrying in %ds", attempt, exc, delay)
            time.sleep(delay)
    raise RuntimeError(f"Claude API failed after {_MAX_RETRIES} attempts: {last_exc}")


def make_draft_id(source: str) -> str:
    return f"{source}_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}"
