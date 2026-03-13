"""
Airbnb Host Response Router
===========================
FastAPI server that sits at the centre of the automated pipeline.

Endpoints:
  POST /classify     — classify a guest message (routine/complex) + generate AI draft
  POST /approve      — approve / edit / skip a pending draft
  GET  /pending      — list all pending drafts awaiting host approval

Run:
  python response_router.py
  (or via start.sh)
"""

import os
import re
import json
import pathlib
import logging
from datetime import datetime
from typing import Optional

import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import anthropic
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
PENDING_FILE      = pathlib.Path(__file__).parent / "pending_drafts.json"

_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

# ---------------------------------------------------------------------------
# Pending drafts — persisted to disk so restarts don't lose pending approvals
# ---------------------------------------------------------------------------

def _load_pending() -> dict:
    if PENDING_FILE.exists():
        return json.loads(PENDING_FILE.read_text())
    return {}


def _save_pending(data: dict):
    PENDING_FILE.write_text(json.dumps(data, indent=2))

# ---------------------------------------------------------------------------
# Message classification
# Keyword scan first; fall back to "complex" when ambiguous (safer default).
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
    """Returns 'routine' or 'complex'."""
    lower = text.lower()
    if any(re.search(p, lower) for p in _COMPLEX):
        return "complex"
    if any(re.search(p, lower) for p in _ROUTINE):
        return "routine"
    return "complex"   # safe default

# ---------------------------------------------------------------------------
# AI draft generation via Claude
# ---------------------------------------------------------------------------

def generate_draft(guest_name: str, message: str, msg_type: str) -> str:
    skill = "/reply" if msg_type == "routine" else "/complaint"
    user_content = (
        f"[Automated pipeline — {msg_type} guest message — use {skill} flow]\n\n"
        f"Guest name: {guest_name}\n\n"
        f"Guest message:\n{message}\n\n"
        "Return ONLY the reply text ready to send. No headings, no meta-commentary, "
        "no 'Here is a draft:' preamble. Just the message."
    )
    response = _client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=512,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_content}],
    )
    return response.content[0].text.strip()

# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------
app = FastAPI(title="Airbnb Host Response Router", version="1.0.0")


class ClassifyRequest(BaseModel):
    source: str           # "email" or "whatsapp"
    guest_name: str
    message: str
    reply_to: Optional[str] = None   # email address or WhatsApp chat ID


class ClassifyResponse(BaseModel):
    draft_id: str
    msg_type: str         # "routine" or "complex"
    draft: str


class ApproveRequest(BaseModel):
    draft_id: str
    action: str           # "approve", "edit", or "skip"
    edited_text: Optional[str] = None


@app.post("/classify", response_model=ClassifyResponse)
def classify(req: ClassifyRequest):
    msg_type = classify_message(req.message)
    draft    = generate_draft(req.guest_name, req.message, msg_type)

    draft_id = f"{req.source}_{datetime.utcnow().strftime('%Y%m%d%H%M%S%f')}"
    pending  = _load_pending()
    pending[draft_id] = {
        "source":     req.source,
        "guest_name": req.guest_name,
        "message":    req.message,
        "reply_to":   req.reply_to,
        "msg_type":   msg_type,
        "draft":      draft,
        "status":     "pending",
        "created_at": datetime.utcnow().isoformat(),
    }
    _save_pending(pending)
    log.info("Classified [%s] from %s (%s) → %s", msg_type, req.guest_name, req.source, draft_id)
    return ClassifyResponse(draft_id=draft_id, msg_type=msg_type, draft=draft)


@app.post("/approve")
def approve(req: ApproveRequest):
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
    _save_pending(pending)
    log.info("Draft %s approved", req.draft_id)
    return {"status": "approved", "final_text": final_text}


@app.get("/pending")
def list_pending():
    return _load_pending()


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=ROUTER_PORT)
