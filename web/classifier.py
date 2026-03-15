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


def generate_draft(api_key: str, guest_name: str, message: str, msg_type: str, skill: str = None) -> str:
    """Call Claude API with tenant's own key and return draft text."""
    client     = anthropic.Anthropic(api_key=api_key)
    skill_cmd  = _SKILL_CMD_MAP.get(skill) or ("/reply" if msg_type == "routine" else "/complaint")
    max_tokens = 1024 if skill in _CALENDAR_SKILLS else 512
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
                system=SYSTEM_PROMPT,
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
