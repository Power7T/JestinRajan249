"""
Airbnb Host Response Router
===========================
FastAPI server that sits at the centre of the automated pipeline.

Endpoints:
  POST /classify     — classify a guest message (routine/complex) + generate AI draft
  POST /approve      — approve / edit / skip a pending draft
  GET  /pending      — list all pending drafts awaiting host approval
  GET  /health       — liveness check for start.sh / monitoring

Run:
  python response_router.py
  (or via start.sh)
"""

import os
import re
import json
import pathlib
import logging
import time
from datetime import datetime, timezone
from typing import Optional

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import anthropic
from filelock import FileLock
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Load SKILL.md system prompt (strips YAML frontmatter)
# ---------------------------------------------------------------------------
_SKILL_MD = pathlib.Path(__file__).parent.parent / "SKILL.md"
_raw = _SKILL_MD.read_text()
_parts = _raw.split("---", 2)
SYSTEM_PROMPT = _parts[2].strip() if len(_parts) >= 3 else _raw

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
ROUTER_PORT       = int(os.getenv("ROUTER_PORT", "7771"))
INTERNAL_TOKEN    = os.getenv("INTERNAL_TOKEN", "")   # shared secret for service-to-service auth
DRAFT_TTL_DAYS    = int(os.getenv("DRAFT_TTL_DAYS", "7"))

PENDING_FILE      = pathlib.Path(__file__).parent / "pending_drafts.json"
PENDING_LOCK      = FileLock(str(PENDING_FILE) + ".lock")

_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

# ---------------------------------------------------------------------------
# Auth helper
# ---------------------------------------------------------------------------

def _check_auth(request: Request):
    """Reject requests that don't carry the correct internal token."""
    if not INTERNAL_TOKEN:
        return   # token not configured — open (dev mode)
    token = request.headers.get("X-Internal-Token", "")
    if token != INTERNAL_TOKEN:
        raise HTTPException(status_code=401, detail="Unauthorized")

# ---------------------------------------------------------------------------
# Pending drafts — atomic reads/writes via filelock
# ---------------------------------------------------------------------------

def _load_pending() -> dict:
    with PENDING_LOCK:
        if PENDING_FILE.exists():
            try:
                return json.loads(PENDING_FILE.read_text())
            except (json.JSONDecodeError, OSError):
                log.warning("pending_drafts.json corrupted — starting fresh")
    return {}


def _save_pending(data: dict):
    # Prune entries older than DRAFT_TTL_DAYS
    cutoff = datetime.now(timezone.utc).timestamp() - DRAFT_TTL_DAYS * 86400
    pruned = {
        k: v for k, v in data.items()
        if _parse_ts(v.get("created_at", "")) > cutoff
    }
    with PENDING_LOCK:
        tmp = PENDING_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(pruned, indent=2))
        tmp.replace(PENDING_FILE)   # atomic on POSIX


def _parse_ts(ts: str) -> float:
    try:
        return datetime.fromisoformat(ts).timestamp()
    except Exception:
        return 0.0

# ---------------------------------------------------------------------------
# Message classification
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


def classify_message(text: str) -> str:
    lower = text.lower()
    if any(re.search(p, lower) for p in _COMPLEX):
        return "complex"
    if any(re.search(p, lower) for p in _ROUTINE):
        return "routine"
    return "complex"

# Vendor-type detection — which maintenance category does the message indicate?
_AC_PATTERNS        = [r"\bac\b", r"\bair.?con", r"\bhvac\b", r"\bcooling\b", r"\bheat(ing)?\b", r"\bfurnace\b", r"\bthermostat\b"]
_PLUMBING_PATTERNS  = [r"\bleak\b", r"\bwater\b", r"\bpipe\b", r"\btoilet\b", r"\bplumb"]


def detect_vendor_type(text: str) -> str | None:
    lower = text.lower()
    if any(re.search(p, lower) for p in _AC_PATTERNS):
        return "ac_technicians"
    return None

# ---------------------------------------------------------------------------
# AI draft generation — with retry and structured delimiters (anti-injection)
# ---------------------------------------------------------------------------
_MAX_RETRIES = 3
_RETRY_DELAYS = [2, 4, 8]


_SKILL_CMD_MAP = {
    "checkin":       "/checkin",
    "cleaner-brief": "/cleaner-brief",
    "reply":         "/reply",
    "complaint":     "/complaint",
}

# Calendar-triggered drafts get more tokens (check-in instructions are long)
_CALENDAR_SKILLS = {"checkin", "cleaner-brief"}


def generate_draft(guest_name: str, message: str, msg_type: str, skill: str = None) -> str:
    if skill and skill in _SKILL_CMD_MAP:
        skill_cmd = _SKILL_CMD_MAP[skill]
    elif msg_type == "routine":
        skill_cmd = "/reply"
    else:
        skill_cmd = "/complaint"

    max_tokens = 1024 if skill in _CALENDAR_SKILLS else 512

    # Wrap guest content in XML-style delimiters to prevent prompt injection
    user_content = (
        f"[Automated pipeline — use {skill_cmd} flow]\n\n"
        f"<guest_name>{guest_name}</guest_name>\n\n"
        f"<context>\n{message}\n</context>\n\n"
        "Return ONLY the output text ready to send or use. No headings, no meta-commentary, "
        "no 'Here is a draft:' preamble. Just the content itself."
    )
    last_exc = None
    for attempt, delay in enumerate(zip(range(_MAX_RETRIES), _RETRY_DELAYS), 1):
        try:
            response = _client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=max_tokens,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_content}],
            )
            if not response.content or not response.content[0].text:
                raise ValueError("Empty response from Claude API")
            return response.content[0].text.strip()
        except Exception as exc:
            last_exc = exc
            _, wait = delay
            log.warning("Claude API attempt %d failed: %s — retrying in %ds", attempt, exc, wait)
            time.sleep(wait)
    raise RuntimeError(f"Claude API failed after {_MAX_RETRIES} attempts: {last_exc}")

# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------
app = FastAPI(title="Airbnb Host Response Router", version="1.1.0")


class ClassifyRequest(BaseModel):
    source: str           # "email", "whatsapp", or "calendar"
    guest_name: str
    message: str
    reply_to: Optional[str] = None
    skill: Optional[str] = None   # override: "checkin", "cleaner-brief", "reply", "complaint"


class ClassifyResponse(BaseModel):
    draft_id: str
    msg_type: str
    draft: str
    vendor_type: Optional[str] = None   # "ac_technicians" if maintenance issue detected


class ApproveRequest(BaseModel):
    draft_id: str
    action: str           # "approve", "edit", or "skip"
    edited_text: Optional[str] = None


@app.get("/health")
def health():
    return {"status": "ok", "version": "1.1.0"}


@app.post("/classify", response_model=ClassifyResponse)
def classify(req: ClassifyRequest, request: Request):
    _check_auth(request)

    # Sanity-check message length
    if len(req.message.strip()) < 5:
        raise HTTPException(status_code=422, detail="Message too short to classify")
    if len(req.message) > 4000:
        req = req.model_copy(update={"message": req.message[:4000]})

    # Calendar triggers always go to host for approval (never auto-send)
    msg_type    = "complex" if req.source == "calendar" else classify_message(req.message)
    vendor_type = detect_vendor_type(req.message) if msg_type == "complex" else None
    try:
        draft = generate_draft(req.guest_name, req.message, msg_type, skill=req.skill)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))

    draft_id = f"{req.source}_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}"
    pending  = _load_pending()
    pending[draft_id] = {
        "source":      req.source,
        "guest_name":  req.guest_name,
        "message":     req.message,
        "reply_to":    req.reply_to,
        "msg_type":    msg_type,
        "vendor_type": vendor_type,
        "draft":       draft,
        "status":      "pending",
        "created_at":  datetime.now(timezone.utc).isoformat(),
    }
    _save_pending(pending)
    log.info("Classified [%s] vendor=%s from %s (%s) → %s",
             msg_type, vendor_type, req.guest_name, req.source, draft_id)
    return ClassifyResponse(draft_id=draft_id, msg_type=msg_type, draft=draft, vendor_type=vendor_type)


@app.post("/approve")
def approve(req: ApproveRequest, request: Request):
    _check_auth(request)
    pending = _load_pending()
    if req.draft_id not in pending:
        raise HTTPException(status_code=404, detail="Draft not found")

    entry = pending[req.draft_id]

    if req.action == "skip":
        entry["status"] = "skipped"
        _save_pending(pending)
        log.info("Draft %s skipped", req.draft_id)
        return {"status": "skipped"}

    final_text = (
        req.edited_text
        if req.action == "edit" and req.edited_text
        else entry["draft"]
    )
    entry["status"]     = "approved"
    entry["final_text"] = final_text
    entry["approved_at"] = datetime.now(timezone.utc).isoformat()
    _save_pending(pending)
    log.info("Draft %s approved (action=%s)", req.draft_id, req.action)
    return {"status": "approved", "final_text": final_text}


@app.get("/pending")
def list_pending(request: Request):
    _check_auth(request)
    return _load_pending()


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=ROUTER_PORT)
