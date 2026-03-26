# © 2024 Jestin Rajan. All rights reserved.
# Licensed under the Airbnb Host AI License Agreement.
# Unauthorized copying, distribution or use is prohibited.
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
import urllib.request as _ur
from datetime import datetime, timezone

_PROCESS_START = time.time()
from typing import Optional

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, HTMLResponse
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

# Health check config
WA_BOT_PORT       = int(os.getenv("WA_BOT_PORT", "7772"))
HEARTBEAT_DIR     = pathlib.Path(__file__).parent
HB_EMAIL          = HEARTBEAT_DIR / "heartbeat_email.json"
HB_CAL            = HEARTBEAT_DIR / "heartbeat_calendar.json"
HB_STALE_EMAIL    = 90    # seconds — email polls every 30s, 3× tolerance
HB_STALE_CAL      = 300   # seconds — calendar polls every 30min, generous tolerance

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
_AC_PATTERNS          = [r"\bac\b", r"\bair.?con", r"\bhvac\b", r"\bcooling\b", r"\bheat(ing)?\b", r"\bfurnace\b", r"\bthermostat\b"]
_PLUMBING_PATTERNS    = [r"\bleak\b", r"\bpipe\b", r"\btoilet\b", r"\bplumb", r"\bdrain\b", r"\bflood(ing)?\b", r"\bwater\s+(damage|leak|drip)"]
_ELECTRICAL_PATTERNS  = [r"\belectr", r"\bpower\s+out", r"\boutlet\b", r"\btripped?\b", r"\bcircuit\b", r"\bfuse\b", r"\bblackout\b", r"\bno\s+power\b"]
_LOCKSMITH_PATTERNS   = [r"\blocked\s+out\b", r"\bcan.?t\s+get\s+in\b", r"\bkey\s+broke", r"\bdoor\s+won.?t\s+open", r"\bsmartlock\b", r"\bkeypad\s+not\s+work"]


def detect_vendor_type(text: str) -> str | None:
    lower = text.lower()
    if any(re.search(p, lower) for p in _AC_PATTERNS):
        return "ac_technicians"
    if any(re.search(p, lower) for p in _PLUMBING_PATTERNS):
        return "plumbers"
    if any(re.search(p, lower) for p in _ELECTRICAL_PATTERNS):
        return "electricians"
    if any(re.search(p, lower) for p in _LOCKSMITH_PATTERNS):
        return "locksmiths"
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


class ServiceStatus(BaseModel):
    name: str
    status: str          # "ok" | "stale" | "down" | "unknown"
    detail: Optional[str] = None
    last_ts: Optional[float] = None
    polls: Optional[int] = None


class StatusResponse(BaseModel):
    router: dict
    whatsapp: ServiceStatus
    email: ServiceStatus
    calendar: ServiceStatus
    uptime_s: float
    checked_at: str


# ---------------------------------------------------------------------------
# Health check helpers
# ---------------------------------------------------------------------------

def _read_heartbeat(path: pathlib.Path, stale_s: int) -> ServiceStatus:
    """
    Read a heartbeat JSON file. Returns status: ok, stale, or unknown.
    """
    name = path.stem.replace("heartbeat_", "")
    if not path.exists():
        return ServiceStatus(name=name, status="unknown",
                             detail="heartbeat file not found")
    try:
        data = json.loads(path.read_text())
        ts = float(data.get("ts", 0))
        age = time.time() - ts
        st = "ok" if age < stale_s else "stale"
        return ServiceStatus(
            name=name,
            status=st,
            detail=f"age={age:.0f}s pid={data.get('pid')}",
            last_ts=ts,
            polls=data.get("polls"),
        )
    except Exception as exc:
        return ServiceStatus(name=name, status="unknown", detail=str(exc))


@app.get("/health")
def health():
    return {"status": "ok", "version": "1.1.0"}


def _status_html(s: StatusResponse) -> HTMLResponse:
    """Render status as a dark-theme HTML table."""
    def row_color(st: str) -> str:
        return {"ok": "#16a34a", "stale": "#d97706",
                "down": "#dc2626", "unknown": "#6b7280"}.get(st, "#6b7280")

    def svc_row(svc: ServiceStatus) -> str:
        c = row_color(svc.status)
        return (
            f"<tr>"
            f"<td style='padding:8px 12px;font-weight:600'>{svc.name}</td>"
            f"<td style='padding:8px 12px'>"
            f"  <span style='color:{c};font-weight:700'>{svc.status.upper()}</span>"
            f"</td>"
            f"<td style='padding:8px 12px;color:#6b7280;font-size:0.85em'>"
            f"  {svc.detail or ''}"
            f"</td>"
            f"<td style='padding:8px 12px;color:#6b7280;font-size:0.85em'>"
            f"  {('polls=' + str(svc.polls)) if svc.polls is not None else ''}"
            f"</td>"
            f"</tr>"
        )

    rows = (
        svc_row(ServiceStatus(name="router", status="ok",
                              detail=f"uptime {s.uptime_s:.0f}s"))
        + svc_row(s.whatsapp)
        + svc_row(s.email)
        + svc_row(s.calendar)
    )

    html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Airbnb Host Pipeline — Status</title>
  <style>
    *{{box-sizing:border-box;margin:0;padding:0}}
    body{{font-family:system-ui,-apple-system,sans-serif;background:#0f172a;color:#e2e8f0;
          display:flex;justify-content:center;padding:2rem 1rem}}
    .card{{background:#1e293b;border:1px solid #334155;border-radius:12px;
           padding:1.5rem 2rem;max-width:700px;width:100%}}
    h1{{font-size:1.1rem;font-weight:700;margin-bottom:0.25rem;color:#f1f5f9}}
    .sub{{font-size:0.78rem;color:#64748b;margin-bottom:1.5rem}}
    table{{width:100%;border-collapse:collapse}}
    th{{text-align:left;padding:6px 12px;font-size:0.72rem;text-transform:uppercase;
        letter-spacing:.06em;color:#64748b;border-bottom:1px solid #334155}}
    tr:not(:last-child) td{{border-bottom:1px solid #1e293b}}
    tr:hover td{{background:#273549}}
    .footer{{margin-top:1.25rem;font-size:0.72rem;color:#475569;text-align:right}}
  </style>
</head>
<body>
<div class="card">
  <h1>Airbnb Host — Pipeline Status</h1>
  <p class="sub">Checked at {s.checked_at} &nbsp;|&nbsp; Router uptime {s.uptime_s:.0f}s</p>
  <table>
    <thead><tr>
      <th>Service</th><th>Status</th><th>Detail</th><th>Polls</th>
    </tr></thead>
    <tbody>{rows}</tbody>
  </table>
  <p class="footer">
    <a href="/status" style="color:#60a5fa">JSON</a>
    &nbsp;&middot;&nbsp;
    <a href="/health" style="color:#60a5fa">Liveness</a>
  </p>
</div>
</body></html>"""
    return HTMLResponse(content=html)


@app.get("/status")
def status(fmt: Optional[str] = None):
    """
    Aggregate health of all four pipeline services.
    ?fmt=html returns a minimal HTML table.
    Default: JSON.
    """
    # 1. WhatsApp bot — probe its /health endpoint
    wa_status: ServiceStatus
    try:
        with _ur.urlopen(
            f"http://127.0.0.1:{WA_BOT_PORT}/health", timeout=2
        ) as resp:
            wa_data = json.loads(resp.read())
        wa_status = ServiceStatus(
            name="whatsapp",
            status="ok" if wa_data.get("status") == "ok" else "down",
            detail=(f"mode={wa_data.get('mode')} "
                    f"connected={wa_data.get('connected')} "
                    f"uptime={wa_data.get('uptime_s')}s"),
        )
    except Exception as exc:
        wa_status = ServiceStatus(name="whatsapp", status="down",
                                  detail=str(exc))

    # 2. Email watcher — heartbeat file
    email_st = _read_heartbeat(HB_EMAIL, HB_STALE_EMAIL)

    # 3. Calendar watcher — heartbeat file (missing is ok if not configured)
    cal_st = _read_heartbeat(HB_CAL, HB_STALE_CAL)
    ical_url = os.getenv("AIRBNB_ICAL_URL") or os.getenv("AIRBNB_ICAL_URLS")
    if cal_st.status == "unknown" and not ical_url:
        cal_st = ServiceStatus(name="calendar", status="ok",
                               detail="not configured (no AIRBNB_ICAL_URL)")

    uptime = time.time() - _PROCESS_START
    payload = StatusResponse(
        router={"status": "ok", "version": "1.1.0"},
        whatsapp=wa_status,
        email=email_st,
        calendar=cal_st,
        uptime_s=round(uptime, 1),
        checked_at=datetime.now(timezone.utc).isoformat(),
    )

    if fmt == "html":
        return _status_html(payload)
    return payload


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
