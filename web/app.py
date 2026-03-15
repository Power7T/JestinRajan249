# © 2024 Jestin Rajan. All rights reserved.
"""
Airbnb Host Assistant — Web App
================================
FastAPI application serving the multi-tenant web dashboard.

Routes:
  GET  /              → redirect to /dashboard or /login
  GET  /login         → login/signup page
  POST /login         → authenticate + set session cookie
  POST /signup        → create account
  GET  /logout        → clear cookie
  GET  /dashboard     → pending drafts
  POST /drafts/{id}/approve  → approve draft (send via appropriate channel)
  POST /drafts/{id}/edit     → edit + approve draft
  POST /drafts/{id}/skip     → skip draft
  GET  /settings      → settings page
  POST /settings      → save settings (email, iCal, vendors, channels, API key)
  GET  /activity      → activity log
  GET  /health        → liveness

  GET  /pricing                → public pricing page
  POST /billing/subscribe/{plan} → create Stripe Checkout Session
  GET  /billing/success        → post-payment activation landing
  GET  /billing/cancel         → cancelled payment redirect
  POST /billing/stripe-webhook → Stripe event handler
  GET  /billing                → billing dashboard (plan, renewal, manage)
  POST /billing/portal         → redirect to Stripe Customer Portal

  GET  /wa/webhook/{tenant_id} → Meta Cloud API webhook verification
  POST /wa/webhook/{tenant_id} → Meta Cloud API inbound messages
  POST /sms/webhook/{tenant_id} → Twilio inbound SMS

  GET  /api/wa/pending         → Baileys bot polls for outbound drafts
  POST /api/wa/inbound         → Baileys bot pushes inbound guest/vendor message
  POST /api/wa/callback        → Baileys bot reports host WA command (APPROVE/EDIT/SKIP)
  GET  /api/download/baileys   → download pre-configured Baileys zip for this tenant

  GET  /api/drafts    → JSON list of pending drafts (HTMX polling)
  GET  /api/workers   → worker status JSON
"""

import io
import json
import logging
import os
import secrets
import zipfile
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional

import uvicorn
from fastapi import FastAPI, Request, Form, Depends, HTTPException, Header
from fastapi.responses import (
    HTMLResponse, RedirectResponse, JSONResponse, StreamingResponse
)
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

from web.db import get_db, init_db, SessionLocal
from web.models import (
    Tenant, TenantConfig, Draft, Vendor, ActivityLog,
    PLAN_FREE, PLAN_BAILEYS, PLAN_META_CLOUD, PLAN_SMS, PLAN_PRO,
)
from web.auth import hash_password, verify_password, create_token, get_current_tenant_id
from web.crypto import encrypt, decrypt
from web import worker_manager
from web import billing as billing_mod
from web.billing import (
    PLAN_INFO, ACTIVE_STATUSES, tenant_has_channel, require_channel,
    create_checkout_session, create_portal_session, handle_stripe_webhook,
    generate_bot_token, verify_bot_token,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

BASE_DIR  = os.path.dirname(__file__)
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

APP_BASE_URL = os.getenv("APP_BASE_URL", "https://your-domain.com")

# ---------------------------------------------------------------------------
# App lifecycle
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    worker_manager.start_all_workers()
    log.info("Airbnb Host Assistant web app started")
    yield
    worker_manager.stop_all_workers()
    log.info("Airbnb Host Assistant web app stopped")


app = FastAPI(title="Airbnb Host Assistant", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_tenant(tenant_id: str, db: Session) -> Tenant:
    t = db.query(Tenant).filter_by(id=tenant_id).first()
    if not t:
        raise HTTPException(status_code=404, detail="Tenant not found")
    return t


def _get_or_create_config(tenant_id: str, db: Session) -> TenantConfig:
    cfg = db.query(TenantConfig).filter_by(tenant_id=tenant_id).first()
    if not cfg:
        cfg = TenantConfig(tenant_id=tenant_id)
        db.add(cfg)
        db.commit()
        db.refresh(cfg)
    return cfg


def _redirect_login():
    return RedirectResponse("/login", status_code=302)


def _auth_bot(request: Request, db: Session) -> TenantConfig:
    """Authenticate a Baileys bot request via Bearer token. Returns TenantConfig."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bot token")
    raw_token = auth[7:].strip()
    # Find the tenant whose bot_api_token_hash matches
    cfgs = db.query(TenantConfig).filter(TenantConfig.bot_api_token_hash.isnot(None)).all()
    for cfg in cfgs:
        if verify_bot_token(raw_token, cfg):
            return cfg
    raise HTTPException(status_code=401, detail="Invalid bot token")


# ---------------------------------------------------------------------------
# Auth routes
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def root():
    return RedirectResponse("/dashboard", status_code=302)


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request, "error": None})


@app.post("/login", response_class=HTMLResponse)
def login_post(request: Request, email: str = Form(...), password: str = Form(...),
               db: Session = Depends(get_db)):
    tenant = db.query(Tenant).filter_by(email=email.lower().strip()).first()
    if not tenant or not verify_password(password, tenant.password_hash):
        return templates.TemplateResponse("login.html",
                                          {"request": request, "error": "Invalid email or password"})
    token = create_token(tenant.id)
    resp  = RedirectResponse("/dashboard", status_code=302)
    resp.set_cookie("session", token, httponly=True, samesite="lax", max_age=72 * 3600)
    return resp


@app.post("/signup", response_class=HTMLResponse)
def signup_post(request: Request, email: str = Form(...), password: str = Form(...),
                db: Session = Depends(get_db)):
    email = email.lower().strip()
    if db.query(Tenant).filter_by(email=email).first():
        return templates.TemplateResponse("login.html",
                                          {"request": request, "error": "Email already registered"})
    if len(password) < 8:
        return templates.TemplateResponse("login.html",
                                          {"request": request, "error": "Password must be 8+ characters"})
    tenant = Tenant(email=email, password_hash=hash_password(password))
    db.add(tenant)
    db.commit()
    db.refresh(tenant)
    db.add(TenantConfig(tenant_id=tenant.id))
    db.commit()
    token = create_token(tenant.id)
    resp  = RedirectResponse("/settings", status_code=302)
    resp.set_cookie("session", token, httponly=True, samesite="lax", max_age=72 * 3600)
    return resp


@app.get("/logout")
def logout():
    resp = RedirectResponse("/login", status_code=302)
    resp.delete_cookie("session")
    return resp


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db)):
    try:
        tenant_id = get_current_tenant_id(request)
    except HTTPException:
        return _redirect_login()

    tenant  = _get_tenant(tenant_id, db)
    cfg     = _get_or_create_config(tenant_id, db)
    pending = (db.query(Draft)
               .filter_by(tenant_id=tenant_id, status="pending")
               .order_by(Draft.created_at.desc())
               .all())
    status  = worker_manager.worker_status(tenant_id)
    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "tenant":  tenant,
        "cfg":     cfg,
        "drafts":  pending,
        "status":  status,
        "plan_info": PLAN_INFO.get(cfg.subscription_plan or PLAN_FREE, PLAN_INFO[PLAN_FREE]),
    })


# ---------------------------------------------------------------------------
# Draft actions
# ---------------------------------------------------------------------------

@app.post("/drafts/{draft_id}/approve")
def approve_draft(draft_id: str, request: Request, db: Session = Depends(get_db)):
    try:
        tenant_id = get_current_tenant_id(request)
    except HTTPException:
        return _redirect_login()

    draft = db.query(Draft).filter_by(id=draft_id, tenant_id=tenant_id).first()
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")

    _execute_draft(draft, draft.draft, tenant_id, db)
    return RedirectResponse("/dashboard", status_code=302)


@app.post("/drafts/{draft_id}/edit")
def edit_draft(draft_id: str, request: Request, edited_text: str = Form(...),
               db: Session = Depends(get_db)):
    try:
        tenant_id = get_current_tenant_id(request)
    except HTTPException:
        return _redirect_login()

    draft = db.query(Draft).filter_by(id=draft_id, tenant_id=tenant_id).first()
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")

    _execute_draft(draft, edited_text.strip(), tenant_id, db)
    return RedirectResponse("/dashboard", status_code=302)


@app.post("/drafts/{draft_id}/skip")
def skip_draft(draft_id: str, request: Request, db: Session = Depends(get_db)):
    try:
        tenant_id = get_current_tenant_id(request)
    except HTTPException:
        return _redirect_login()

    draft = db.query(Draft).filter_by(id=draft_id, tenant_id=tenant_id).first()
    if draft:
        draft.status = "skipped"
        db.add(ActivityLog(tenant_id=tenant_id, event_type="draft_skipped",
                           message=f"Draft skipped: {draft.guest_name}"))
        db.commit()
    return RedirectResponse("/dashboard", status_code=302)


def _execute_draft(draft: Draft, final_text: str, tenant_id: str, db: Session):
    """Send reply via the appropriate channel and mark draft approved."""
    cfg = db.query(TenantConfig).filter_by(tenant_id=tenant_id).first()

    if draft.source == "email" and draft.reply_to and cfg and cfg.email_address:
        try:
            from web.email_worker import _send_smtp_reply, EmailConfig
            ecfg = EmailConfig(
                tenant_id=tenant_id,
                imap_host=cfg.imap_host or "",
                imap_port=cfg.imap_port,
                smtp_host=cfg.smtp_host or "",
                smtp_port=cfg.smtp_port,
                email_address=cfg.email_address,
                email_password=decrypt(cfg.email_password_enc or ""),
                anthropic_api_key="",
            )
            _send_smtp_reply(ecfg, draft.reply_to,
                             f"Re: Airbnb message from {draft.guest_name}", final_text)
            log.info("[%s] Email reply sent to %s", tenant_id, draft.reply_to)
        except Exception as exc:
            log.error("[%s] SMTP send failed: %s", tenant_id, exc)

    elif draft.source == "whatsapp" and draft.reply_to and cfg:
        guest_phone = draft.reply_to
        if tenant_has_channel(cfg, PLAN_META_CLOUD):
            from web.meta_sender import send_whatsapp
            token = decrypt(cfg.whatsapp_token_enc or "")
            if token and cfg.whatsapp_phone_id:
                ok = send_whatsapp(cfg.whatsapp_phone_id, token, guest_phone, final_text)
                if not ok:
                    log.warning("[%s] Meta WA send failed for %s", tenant_id, guest_phone)
        elif tenant_has_channel(cfg, PLAN_BAILEYS):
            # Store as outbound pending — Baileys bot will pick it up on next poll
            _queue_baileys_outbound(tenant_id, guest_phone, final_text, db)

    elif draft.source == "sms" and draft.reply_to and cfg:
        guest_phone = draft.reply_to
        if tenant_has_channel(cfg, PLAN_SMS):
            from web.sms_sender import send_sms
            auth_token = decrypt(cfg.twilio_auth_token_enc or "")
            if cfg.twilio_account_sid and auth_token and cfg.twilio_from_number:
                ok = send_sms(cfg.twilio_account_sid, auth_token,
                              cfg.twilio_from_number, guest_phone, final_text)
                if not ok:
                    log.warning("[%s] Twilio SMS send failed for %s", tenant_id, guest_phone)

    draft.status      = "approved"
    draft.final_text  = final_text
    draft.approved_at = datetime.now(timezone.utc)
    db.add(ActivityLog(tenant_id=tenant_id, event_type="draft_approved",
                       message=f"Draft approved: {draft.guest_name}"))
    db.commit()


# Baileys outbound queue (in-memory per process; bot polls /api/wa/pending)
_baileys_outbound: dict[str, list[dict]] = {}   # tenant_id → [{"to": phone, "text": str}]


def _queue_baileys_outbound(tenant_id: str, to_phone: str, text: str, db: Session):
    _baileys_outbound.setdefault(tenant_id, []).append({"to": to_phone, "text": text})
    log.info("[%s] Queued Baileys outbound to %s", tenant_id, to_phone)


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, db: Session = Depends(get_db)):
    try:
        tenant_id = get_current_tenant_id(request)
    except HTTPException:
        return _redirect_login()

    tenant  = _get_tenant(tenant_id, db)
    cfg     = _get_or_create_config(tenant_id, db)
    vendors = db.query(Vendor).filter_by(tenant_id=tenant_id).order_by(Vendor.category, Vendor.name).all()
    return templates.TemplateResponse("settings.html", {
        "request":   request,
        "tenant":    tenant,
        "cfg":       cfg,
        "vendors":   vendors,
        "saved":     False,
        "plan_info": PLAN_INFO.get(cfg.subscription_plan or PLAN_FREE, PLAN_INFO[PLAN_FREE]),
        "has_baileys":    tenant_has_channel(cfg, PLAN_BAILEYS),
        "has_meta_cloud": tenant_has_channel(cfg, PLAN_META_CLOUD),
        "has_sms":        tenant_has_channel(cfg, PLAN_SMS),
        "app_base_url":   APP_BASE_URL,
    })


@app.post("/settings", response_class=HTMLResponse)
def settings_save(
    request:        Request,
    property_names:        str = Form(""),
    ical_urls:             str = Form(""),
    imap_host:             str = Form(""),
    smtp_host:             str = Form(""),
    email_address:         str = Form(""),
    email_password:        str = Form(""),
    anthropic_key:         str = Form(""),
    # WhatsApp Meta Cloud
    wa_mode:               str = Form("none"),
    whatsapp_number:       str = Form(""),
    whatsapp_token:        str = Form(""),
    whatsapp_phone_id:     str = Form(""),
    whatsapp_verify_token: str = Form(""),
    # SMS / Twilio
    sms_mode:              str = Form("none"),
    twilio_account_sid:    str = Form(""),
    twilio_auth_token:     str = Form(""),
    twilio_from_number:    str = Form(""),
    sms_notify_number:     str = Form(""),
    db: Session = Depends(get_db),
):
    try:
        tenant_id = get_current_tenant_id(request)
    except HTTPException:
        return _redirect_login()

    cfg = _get_or_create_config(tenant_id, db)

    # Core settings
    cfg.property_names = property_names.strip()
    cfg.ical_urls      = ical_urls.strip()
    cfg.imap_host      = imap_host.strip() or None
    cfg.smtp_host      = smtp_host.strip() or None
    cfg.email_address  = email_address.strip() or None
    if email_password.strip():
        cfg.email_password_enc = encrypt(email_password.strip())
    if anthropic_key.strip():
        cfg.anthropic_api_key_enc = encrypt(anthropic_key.strip())

    # WhatsApp Meta Cloud (only save if tenant has the right plan)
    if tenant_has_channel(cfg, PLAN_META_CLOUD):
        cfg.wa_mode           = wa_mode.strip() or "none"
        cfg.whatsapp_number   = whatsapp_number.strip() or None
        cfg.whatsapp_phone_id = whatsapp_phone_id.strip() or None
        if whatsapp_verify_token.strip():
            cfg.whatsapp_verify_token = whatsapp_verify_token.strip()
        if whatsapp_token.strip():
            cfg.whatsapp_token_enc = encrypt(whatsapp_token.strip())

    # SMS / Twilio (only save if tenant has the right plan)
    if tenant_has_channel(cfg, PLAN_SMS):
        cfg.sms_mode           = sms_mode.strip() or "none"
        cfg.twilio_account_sid = twilio_account_sid.strip() or None
        cfg.twilio_from_number = twilio_from_number.strip() or None
        cfg.sms_notify_number  = sms_notify_number.strip() or None
        if twilio_auth_token.strip():
            cfg.twilio_auth_token_enc = encrypt(twilio_auth_token.strip())

    db.add(ActivityLog(tenant_id=tenant_id, event_type="settings_saved",
                       message="Settings updated"))
    db.commit()
    worker_manager.restart_worker(tenant_id)

    tenant  = _get_tenant(tenant_id, db)
    vendors = db.query(Vendor).filter_by(tenant_id=tenant_id).order_by(Vendor.category, Vendor.name).all()
    return templates.TemplateResponse("settings.html", {
        "request":   request,
        "tenant":    tenant,
        "cfg":       cfg,
        "vendors":   vendors,
        "saved":     True,
        "plan_info": PLAN_INFO.get(cfg.subscription_plan or PLAN_FREE, PLAN_INFO[PLAN_FREE]),
        "has_baileys":    tenant_has_channel(cfg, PLAN_BAILEYS),
        "has_meta_cloud": tenant_has_channel(cfg, PLAN_META_CLOUD),
        "has_sms":        tenant_has_channel(cfg, PLAN_SMS),
        "app_base_url":   APP_BASE_URL,
    })


@app.post("/vendors/add")
def vendor_add(request: Request, category: str = Form(...), name: str = Form(...),
               phone: str = Form(...), notes: str = Form(""), db: Session = Depends(get_db)):
    try:
        tenant_id = get_current_tenant_id(request)
    except HTTPException:
        return _redirect_login()
    db.add(Vendor(tenant_id=tenant_id, category=category, name=name, phone=phone, notes=notes or None))
    db.commit()
    return RedirectResponse("/settings#vendors", status_code=302)


@app.post("/vendors/{vendor_id}/delete")
def vendor_delete(vendor_id: int, request: Request, db: Session = Depends(get_db)):
    try:
        tenant_id = get_current_tenant_id(request)
    except HTTPException:
        return _redirect_login()
    v = db.query(Vendor).filter_by(id=vendor_id, tenant_id=tenant_id).first()
    if v:
        db.delete(v)
        db.commit()
    return RedirectResponse("/settings#vendors", status_code=302)


# ---------------------------------------------------------------------------
# Billing
# ---------------------------------------------------------------------------

@app.get("/pricing", response_class=HTMLResponse)
def pricing_page(request: Request):
    logged_in = False
    try:
        get_current_tenant_id(request)
        logged_in = True
    except HTTPException:
        pass
    return templates.TemplateResponse("pricing.html", {
        "request":   request,
        "plan_info": PLAN_INFO,
        "logged_in": logged_in,
    })


@app.get("/billing", response_class=HTMLResponse)
def billing_page(request: Request, db: Session = Depends(get_db)):
    try:
        tenant_id = get_current_tenant_id(request)
    except HTTPException:
        return _redirect_login()

    tenant = _get_tenant(tenant_id, db)
    cfg    = _get_or_create_config(tenant_id, db)
    return templates.TemplateResponse("billing.html", {
        "request":   request,
        "tenant":    tenant,
        "cfg":       cfg,
        "plan_info": PLAN_INFO,
        "current_plan": PLAN_INFO.get(cfg.subscription_plan or PLAN_FREE, PLAN_INFO[PLAN_FREE]),
        "is_active": cfg.subscription_status in ACTIVE_STATUSES,
    })


@app.post("/billing/subscribe/{plan}")
def billing_subscribe(plan: str, request: Request, db: Session = Depends(get_db)):
    try:
        tenant_id = get_current_tenant_id(request)
    except HTTPException:
        return _redirect_login()

    if plan not in PLAN_INFO or plan == PLAN_FREE:
        raise HTTPException(status_code=400, detail="Invalid plan")

    cfg = _get_or_create_config(tenant_id, db)
    try:
        url = create_checkout_session(
            tenant_id=tenant_id,
            plan=plan,
            success_url=f"{APP_BASE_URL}/billing/success?plan={plan}",
            cancel_url=f"{APP_BASE_URL}/billing/cancel",
            customer_id=cfg.stripe_customer_id,
        )
    except HTTPException:
        raise
    except Exception as exc:
        log.error("Stripe checkout error: %s", exc)
        raise HTTPException(status_code=500, detail="Payment provider error")

    return RedirectResponse(url, status_code=302)


@app.get("/billing/success", response_class=HTMLResponse)
def billing_success(request: Request, plan: str = PLAN_FREE, db: Session = Depends(get_db)):
    try:
        tenant_id = get_current_tenant_id(request)
    except HTTPException:
        return _redirect_login()
    # Stripe webhook will update the DB; show a pending confirmation page
    cfg    = _get_or_create_config(tenant_id, db)
    tenant = _get_tenant(tenant_id, db)
    return templates.TemplateResponse("billing.html", {
        "request":      request,
        "tenant":       tenant,
        "cfg":          cfg,
        "plan_info":    PLAN_INFO,
        "current_plan": PLAN_INFO.get(cfg.subscription_plan or PLAN_FREE, PLAN_INFO[PLAN_FREE]),
        "is_active":    cfg.subscription_status in ACTIVE_STATUSES,
        "success_msg":  f"Payment received! Your {PLAN_INFO.get(plan, {}).get('name', plan)} plan is activating.",
    })


@app.get("/billing/cancel", response_class=HTMLResponse)
def billing_cancel(request: Request, db: Session = Depends(get_db)):
    try:
        tenant_id = get_current_tenant_id(request)
    except HTTPException:
        return _redirect_login()
    return RedirectResponse("/billing", status_code=302)


@app.post("/billing/portal")
def billing_portal(request: Request, db: Session = Depends(get_db)):
    try:
        tenant_id = get_current_tenant_id(request)
    except HTTPException:
        return _redirect_login()
    cfg = _get_or_create_config(tenant_id, db)
    if not cfg.stripe_customer_id:
        return RedirectResponse("/billing", status_code=302)
    try:
        url = create_portal_session(cfg.stripe_customer_id, f"{APP_BASE_URL}/billing")
    except Exception as exc:
        log.error("Stripe portal error: %s", exc)
        raise HTTPException(status_code=500, detail="Billing portal unavailable")
    return RedirectResponse(url, status_code=302)


@app.post("/billing/stripe-webhook")
async def stripe_webhook(request: Request, db: Session = Depends(get_db)):
    payload    = await request.body()
    sig_header = request.headers.get("stripe-signature", "")
    result     = handle_stripe_webhook(payload, sig_header, db)
    return JSONResponse(result)


# ---------------------------------------------------------------------------
# Meta WhatsApp Cloud API webhooks
# ---------------------------------------------------------------------------

@app.get("/wa/webhook/{tenant_id}")
def wa_webhook_verify(tenant_id: str, request: Request, db: Session = Depends(get_db)):
    """Meta webhook verification handshake."""
    cfg = db.query(TenantConfig).filter_by(tenant_id=tenant_id).first()
    if not cfg:
        raise HTTPException(status_code=404)

    from web.meta_sender import verify_webhook
    mode      = request.query_params.get("hub.mode", "")
    token     = request.query_params.get("hub.verify_token", "")
    challenge = request.query_params.get("hub.challenge", "")
    result    = verify_webhook(cfg.whatsapp_verify_token or "", mode, token, challenge)
    if result is None:
        raise HTTPException(status_code=403, detail="Verification failed")
    return HTMLResponse(content=result)


@app.post("/wa/webhook/{tenant_id}")
async def wa_webhook_inbound(tenant_id: str, request: Request, db: Session = Depends(get_db)):
    """Receive inbound messages from Meta Cloud API."""
    cfg = db.query(TenantConfig).filter_by(tenant_id=tenant_id).first()
    if not cfg:
        return JSONResponse({"status": "ok"})   # always 200 to Meta

    try:
        require_channel(cfg, PLAN_META_CLOUD)
    except HTTPException:
        return JSONResponse({"status": "ok"})

    body = await request.json()
    from web.meta_sender import extract_inbound
    for msg in extract_inbound(body):
        _handle_inbound_wa(tenant_id, msg["from"], msg["text"], db)

    return JSONResponse({"status": "ok"})


# ---------------------------------------------------------------------------
# Twilio SMS webhooks
# ---------------------------------------------------------------------------

@app.post("/sms/webhook/{tenant_id}")
async def sms_webhook_inbound(tenant_id: str, request: Request, db: Session = Depends(get_db)):
    """Receive inbound SMS from Twilio."""
    cfg = db.query(TenantConfig).filter_by(tenant_id=tenant_id).first()
    if not cfg:
        return HTMLResponse("<Response/>")

    try:
        require_channel(cfg, PLAN_SMS)
    except HTTPException:
        return HTMLResponse("<Response/>")

    form = await request.form()
    from web.sms_sender import parse_twilio_inbound
    msg = parse_twilio_inbound(dict(form))
    if msg:
        _handle_inbound_sms(tenant_id, msg["from"], msg["text"], db)

    return HTMLResponse("<Response/>")   # TwiML empty response


# ---------------------------------------------------------------------------
# Shared inbound handler — creates a Draft for host review
# ---------------------------------------------------------------------------

def _handle_inbound_wa(tenant_id: str, from_phone: str, text: str, db: Session):
    """Classify an inbound WhatsApp message and create a pending draft."""
    cfg = db.query(TenantConfig).filter_by(tenant_id=tenant_id).first()
    if not cfg:
        return
    try:
        from web.classifier import classify_message
        api_key = decrypt(cfg.anthropic_api_key_enc or "")
        if not api_key:
            return
        result = classify_message(api_key, "WhatsApp guest", text, cfg.property_names or "")
        draft_id = secrets.token_hex(8)
        db.add(Draft(
            id=draft_id,
            tenant_id=tenant_id,
            source="whatsapp",
            guest_name="WhatsApp guest",
            message=text,
            reply_to=from_phone,
            msg_type=result.get("msg_type", "complex"),
            draft=result.get("draft", text),
        ))
        db.add(ActivityLog(tenant_id=tenant_id, event_type="whatsapp_received",
                           message=f"WhatsApp from {from_phone}: {text[:80]}"))
        db.commit()
    except Exception as exc:
        log.error("[%s] WA inbound handler error: %s", tenant_id, exc)


def _handle_inbound_sms(tenant_id: str, from_phone: str, text: str, db: Session):
    """Classify an inbound SMS and create a pending draft."""
    cfg = db.query(TenantConfig).filter_by(tenant_id=tenant_id).first()
    if not cfg:
        return
    try:
        from web.classifier import classify_message
        api_key = decrypt(cfg.anthropic_api_key_enc or "")
        if not api_key:
            return
        result = classify_message(api_key, "SMS guest", text, cfg.property_names or "")
        draft_id = secrets.token_hex(8)
        db.add(Draft(
            id=draft_id,
            tenant_id=tenant_id,
            source="sms",
            guest_name="SMS guest",
            message=text,
            reply_to=from_phone,
            msg_type=result.get("msg_type", "complex"),
            draft=result.get("draft", text),
        ))
        db.add(ActivityLog(tenant_id=tenant_id, event_type="sms_received",
                           message=f"SMS from {from_phone}: {text[:80]}"))
        db.commit()
    except Exception as exc:
        log.error("[%s] SMS inbound handler error: %s", tenant_id, exc)


# ---------------------------------------------------------------------------
# Baileys bot API (bot runs on host's PC, calls back to here)
# ---------------------------------------------------------------------------

@app.get("/api/wa/pending")
def api_wa_pending(request: Request, db: Session = Depends(get_db)):
    """Baileys bot polls this to get outbound messages to deliver to guests."""
    cfg = _auth_bot(request, db)
    try:
        require_channel(cfg, PLAN_BAILEYS)
    except HTTPException:
        raise HTTPException(status_code=402, detail="Subscription required — renew at /billing")

    tenant_id = cfg.tenant_id
    msgs = _baileys_outbound.pop(tenant_id, [])
    return JSONResponse({"messages": msgs})


@app.post("/api/wa/inbound")
async def api_wa_inbound(request: Request, db: Session = Depends(get_db)):
    """Baileys bot pushes an inbound message from a guest/vendor."""
    cfg = _auth_bot(request, db)
    try:
        require_channel(cfg, PLAN_BAILEYS)
    except HTTPException:
        raise HTTPException(status_code=402, detail="Subscription required — renew at /billing")

    body = await request.json()
    from_phone = body.get("from", "")
    text       = body.get("text", "")
    if from_phone and text:
        _handle_inbound_wa(cfg.tenant_id, from_phone, text, db)
    return JSONResponse({"status": "ok"})


@app.post("/api/wa/callback")
async def api_wa_callback(request: Request, db: Session = Depends(get_db)):
    """
    Baileys bot reports that the host typed a command in WA (APPROVE / EDIT / SKIP).
    Body: {"action": "approve"|"edit"|"skip", "draft_id": str, "text": str}
    """
    cfg = _auth_bot(request, db)
    try:
        require_channel(cfg, PLAN_BAILEYS)
    except HTTPException:
        raise HTTPException(status_code=402, detail="Subscription required — renew at /billing")

    body     = await request.json()
    action   = body.get("action", "")
    draft_id = body.get("draft_id", "")
    text     = body.get("text", "")
    tenant_id = cfg.tenant_id

    draft = db.query(Draft).filter_by(id=draft_id, tenant_id=tenant_id).first()
    if not draft:
        return JSONResponse({"status": "not_found"}, status_code=404)

    if action == "approve":
        _execute_draft(draft, draft.draft, tenant_id, db)
    elif action == "edit" and text:
        _execute_draft(draft, text, tenant_id, db)
    elif action == "skip":
        draft.status = "skipped"
        db.add(ActivityLog(tenant_id=tenant_id, event_type="draft_skipped",
                           message=f"Draft skipped via WA: {draft.guest_name}"))
        db.commit()
    return JSONResponse({"status": "ok"})


@app.post("/api/wa/token/generate")
def api_generate_bot_token(request: Request, db: Session = Depends(get_db)):
    """Generate (or regenerate) the Baileys bot API token for this tenant."""
    try:
        tenant_id = get_current_tenant_id(request)
    except HTTPException:
        raise HTTPException(status_code=401)

    cfg = _get_or_create_config(tenant_id, db)
    try:
        require_channel(cfg, PLAN_BAILEYS)
    except HTTPException:
        raise HTTPException(status_code=402, detail="Baileys plan required")

    raw_token = generate_bot_token(cfg, db)
    return JSONResponse({"token": raw_token, "hint": cfg.bot_api_token_hint})


@app.get("/api/download/baileys")
def api_download_baileys(request: Request, db: Session = Depends(get_db)):
    """
    Generate and serve a pre-configured Baileys zip for the logged-in tenant.
    The zip contains bot.js, package.json, a pre-filled .env, and setup scripts.
    """
    try:
        tenant_id = get_current_tenant_id(request)
    except HTTPException:
        return _redirect_login()

    cfg = _get_or_create_config(tenant_id, db)
    try:
        require_channel(cfg, PLAN_BAILEYS)
    except HTTPException:
        raise HTTPException(status_code=402, detail="Baileys plan required")

    # Generate a fresh bot token for download
    raw_token = generate_bot_token(cfg, db)

    # Read bot.js source
    bot_js_path = os.path.join(
        os.path.dirname(__file__), "..", "airbnb-host", "scripts", "whatsapp", "bot.js"
    )
    try:
        with open(bot_js_path) as f:
            bot_js_content = f.read()
    except FileNotFoundError:
        raise HTTPException(status_code=500, detail="Bot source not found on server")

    pkg_json = json.dumps({
        "name": "hostai-baileys-bot",
        "version": "1.0.0",
        "main": "bot.js",
        "scripts": {"start": "node bot.js"},
        "dependencies": {
            "@whiskeysockets/baileys": "^6.7.9",
            "dotenv": "^16.4.5",
            "pino": "^9.4.0",
            "qrcode-terminal": "^0.12.0",
        },
        "engines": {"node": ">=22.0.0"},
    }, indent=2)

    env_content = (
        f"# HostAI Baileys Bot — auto-generated for your account\n"
        f"WA_MODE=saas_bridge\n"
        f"WEB_APP_URL={APP_BASE_URL}\n"
        f"TENANT_ID={tenant_id}\n"
        f"BOT_API_TOKEN={raw_token}\n"
        f"HOST_WHATSAPP_NUMBER=+1234567890\n"
        f"# Replace HOST_WHATSAPP_NUMBER with the phone number you will scan QR with\n"
    )

    setup_sh = (
        "#!/bin/bash\n"
        "echo '=== HostAI Baileys Bot Setup ==='\n"
        "command -v node >/dev/null || { echo 'Node.js not found. Install from nodejs.org'; exit 1; }\n"
        "npm install\n"
        "echo ''\n"
        "echo 'Done! Starting bot...'\n"
        "echo 'Scan the QR code with WhatsApp on your phone.'\n"
        "node bot.js\n"
    )
    setup_bat = (
        "@echo off\n"
        "echo === HostAI Baileys Bot Setup ===\n"
        "npm install\n"
        "echo.\n"
        "echo Done! Starting bot...\n"
        "echo Scan the QR code with WhatsApp on your phone.\n"
        "node bot.js\n"
        "pause\n"
    )
    readme = (
        "HostAI Baileys Bot — Quick Start\n"
        "=================================\n\n"
        "Requirements: Node.js 22+ (download from nodejs.org)\n\n"
        "Steps:\n"
        "  Mac/Linux:  chmod +x setup.sh && ./setup.sh\n"
        "  Windows:    Double-click setup.bat\n\n"
        "1. The bot will print a QR code in the terminal.\n"
        "2. Open WhatsApp on your phone → Linked Devices → Link a Device.\n"
        "3. Scan the QR code.\n"
        "4. Done! Keep this terminal running.\n\n"
        "The bot connects to your HostAI dashboard automatically.\n"
        "Your WhatsApp messages go through your home IP — no ban risk.\n\n"
        f"Dashboard: {APP_BASE_URL}/dashboard\n"
    )

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("hostai-bot/bot.js",       bot_js_content)
        zf.writestr("hostai-bot/package.json",  pkg_json)
        zf.writestr("hostai-bot/.env",           env_content)
        zf.writestr("hostai-bot/setup.sh",       setup_sh)
        zf.writestr("hostai-bot/setup.bat",      setup_bat)
        zf.writestr("hostai-bot/README.txt",     readme)
    buf.seek(0)

    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": "attachment; filename=hostai-bot.zip"},
    )


# ---------------------------------------------------------------------------
# Activity log
# ---------------------------------------------------------------------------

@app.get("/activity", response_class=HTMLResponse)
def activity_log(request: Request, db: Session = Depends(get_db)):
    try:
        tenant_id = get_current_tenant_id(request)
    except HTTPException:
        return _redirect_login()

    tenant = _get_tenant(tenant_id, db)
    logs   = (db.query(ActivityLog).filter_by(tenant_id=tenant_id)
              .order_by(ActivityLog.created_at.desc()).limit(200).all())
    return templates.TemplateResponse("activity.html", {"request": request, "tenant": tenant, "logs": logs})


# ---------------------------------------------------------------------------
# JSON / HTMX API
# ---------------------------------------------------------------------------

@app.get("/api/drafts")
def api_drafts(request: Request, db: Session = Depends(get_db)):
    try:
        tenant_id = get_current_tenant_id(request)
    except HTTPException:
        raise HTTPException(status_code=401)
    pending = db.query(Draft).filter_by(tenant_id=tenant_id, status="pending") \
                .order_by(Draft.created_at.desc()).all()
    return [{"id": d.id, "guest_name": d.guest_name, "source": d.source,
             "msg_type": d.msg_type, "draft": d.draft,
             "created_at": d.created_at.isoformat()} for d in pending]


@app.get("/api/workers")
def api_workers(request: Request):
    try:
        tenant_id = get_current_tenant_id(request)
    except HTTPException:
        raise HTTPException(status_code=401)
    return worker_manager.worker_status(tenant_id)


@app.get("/drafts/{draft_id}/edit-form", response_class=HTMLResponse)
def edit_form(draft_id: str, request: Request, db: Session = Depends(get_db)):
    try:
        tenant_id = get_current_tenant_id(request)
    except HTTPException:
        return HTMLResponse("")
    draft = db.query(Draft).filter_by(id=draft_id, tenant_id=tenant_id).first()
    if not draft:
        return HTMLResponse("")
    return HTMLResponse(f"""
    <form method="post" action="/drafts/{draft_id}/edit" style="margin-top:0.5rem">
      <textarea name="edited_text" style="width:100%;padding:8px;border:1px solid #ced4da;border-radius:6px;
        font-size:0.875rem;line-height:1.6;min-height:120px;resize:vertical"
      >{draft.draft}</textarea>
      <div style="display:flex;gap:0.5rem;margin-top:0.5rem">
        <button type="submit" class="btn btn-primary btn-sm">Send edited version</button>
      </div>
    </form>
    """)


@app.get("/health")
def health():
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("web.app:app", host="0.0.0.0", port=port, reload=False)
