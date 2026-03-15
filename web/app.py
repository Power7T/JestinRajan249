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
from datetime import datetime, timezone, timedelta
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
    Tenant, TenantConfig, Draft, Vendor, ActivityLog, BaileysOutbound,
    PLAN_FREE, PLAN_BAILEYS, PLAN_META_CLOUD, PLAN_SMS, PLAN_PRO,
)
from web.auth import hash_password, verify_password, create_token, get_current_tenant_id
from web.crypto import encrypt, decrypt
from web import worker_manager
from web import billing as billing_mod
from web.mailer import send_verification_email, send_password_reset_email
from web.billing import (
    PLAN_INFO, ACTIVE_STATUSES, tenant_has_channel, require_channel,
    create_checkout_session, create_portal_session, handle_stripe_webhook,
    generate_bot_token, verify_bot_token,
)
from web.security import (
    CSRFMiddleware, SecurityHeadersMiddleware,
    validate_csrf, rate_limit, client_ip,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Sentry — error tracking (optional; only active when SENTRY_DSN is set)
# ---------------------------------------------------------------------------
_SENTRY_DSN = os.getenv("SENTRY_DSN", "")
if _SENTRY_DSN:
    try:
        import sentry_sdk
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration
        sentry_sdk.init(
            dsn=_SENTRY_DSN,
            integrations=[FastApiIntegration(), SqlalchemyIntegration()],
            traces_sample_rate=0.05,
            environment=os.getenv("ENVIRONMENT", "production"),
            send_default_pii=False,
        )
        log.info("Sentry initialized")
    except ImportError:
        log.warning("sentry-sdk not installed — pip install 'sentry-sdk[fastapi]' to enable")

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


app = FastAPI(
    title="Airbnb Host Assistant",
    lifespan=lifespan,
    docs_url=None,     # disable Swagger UI in production
    redoc_url=None,
)
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")

# Middleware (applied in reverse order — bottom first)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(CSRFMiddleware)

# ---------------------------------------------------------------------------
# Global error handlers
# ---------------------------------------------------------------------------

@app.exception_handler(404)
async def not_found_handler(request: Request, exc: HTTPException):
    if request.url.path.startswith("/api/"):
        return JSONResponse({"detail": "Not found"}, status_code=404)
    return templates.TemplateResponse(
        "error.html",
        {"request": request, "code": 404, "title": "Page not found",
         "message": "The page you're looking for doesn't exist."},
        status_code=404,
    )


@app.exception_handler(403)
async def forbidden_handler(request: Request, exc: HTTPException):
    if request.url.path.startswith("/api/"):
        return JSONResponse({"detail": exc.detail}, status_code=403)
    return templates.TemplateResponse(
        "error.html",
        {"request": request, "code": 403, "title": "Forbidden",
         "message": str(exc.detail)},
        status_code=403,
    )


@app.exception_handler(429)
async def rate_limit_handler(request: Request, exc: HTTPException):
    if request.url.path.startswith("/api/"):
        return JSONResponse({"detail": exc.detail}, status_code=429)
    return templates.TemplateResponse(
        "error.html",
        {"request": request, "code": 429, "title": "Too many requests",
         "message": "You've made too many requests. Please wait a moment and try again."},
        status_code=429,
    )


@app.exception_handler(500)
async def server_error_handler(request: Request, exc: Exception):
    log.exception("Unhandled server error: %s", exc)
    if request.url.path.startswith("/api/"):
        return JSONResponse({"detail": "Internal server error"}, status_code=500)
    return templates.TemplateResponse(
        "error.html",
        {"request": request, "code": 500, "title": "Server error",
         "message": "Something went wrong on our end. Please try again in a moment."},
        status_code=500,
    )

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
def login_post(
    request: Request,
    email:       str = Form(...),
    password:    str = Form(...),
    csrf_token:  str = Form(None),
    db: Session = Depends(get_db),
):
    rate_limit(f"login:{client_ip(request)}", max_requests=10, window_seconds=900)
    validate_csrf(request, csrf_token)
    tenant = db.query(Tenant).filter_by(email=email.lower().strip()).first()
    if not tenant or not verify_password(password, tenant.password_hash):
        return templates.TemplateResponse("login.html",
                                          {"request": request, "error": "Invalid email or password"})
    token = create_token(tenant.id)
    is_secure = (request.url.scheme == "https"
                 or request.headers.get("X-Forwarded-Proto") == "https")
    resp = RedirectResponse("/dashboard", status_code=302)
    resp.set_cookie("session", token, httponly=True,
                    samesite="strict", secure=is_secure, max_age=72 * 3600)
    return resp


@app.post("/signup", response_class=HTMLResponse)
def signup_post(
    request: Request,
    email:      str = Form(...),
    password:   str = Form(...),
    csrf_token: str = Form(None),
    db: Session = Depends(get_db),
):
    rate_limit(f"signup:{client_ip(request)}", max_requests=5, window_seconds=3600)
    validate_csrf(request, csrf_token)
    email = email.lower().strip()
    if db.query(Tenant).filter_by(email=email).first():
        return templates.TemplateResponse("login.html",
                                          {"request": request, "error": "Email already registered"})
    if len(password) < 8:
        return templates.TemplateResponse("login.html",
                                          {"request": request, "error": "Password must be 8+ characters"})
    ver_token = secrets.token_urlsafe(32)
    tenant = Tenant(
        email=email,
        password_hash=hash_password(password),
        verification_token=ver_token,
        verification_sent_at=datetime.now(timezone.utc),
    )
    db.add(tenant)
    db.commit()
    db.refresh(tenant)
    db.add(TenantConfig(tenant_id=tenant.id))
    db.commit()
    # Send verification email (non-blocking — failure just logs a warning)
    send_verification_email(email, ver_token)
    token = create_token(tenant.id)
    is_secure = (request.url.scheme == "https"
                 or request.headers.get("X-Forwarded-Proto") == "https")
    resp = RedirectResponse("/settings", status_code=302)
    resp.set_cookie("session", token, httponly=True,
                    samesite="strict", secure=is_secure, max_age=72 * 3600)
    return resp


@app.get("/verify-email", response_class=HTMLResponse)
def verify_email(request: Request, token: str = "", db: Session = Depends(get_db)):
    if not token:
        return templates.TemplateResponse("verify_email.html",
                                          {"request": request, "success": False, "expired": False})
    tenant = db.query(Tenant).filter_by(verification_token=token).first()
    if not tenant:
        return templates.TemplateResponse("verify_email.html",
                                          {"request": request, "success": False, "expired": False})
    # Check 24h expiry
    if tenant.verification_sent_at:
        sent_at = tenant.verification_sent_at
        if sent_at.tzinfo is None:
            sent_at = sent_at.replace(tzinfo=timezone.utc)
        age = datetime.now(timezone.utc) - sent_at
        if age.total_seconds() > 86400:
            return templates.TemplateResponse("verify_email.html",
                                              {"request": request, "success": False, "expired": True})
    tenant.email_verified = True
    tenant.verification_token = None
    db.commit()
    return templates.TemplateResponse("verify_email.html",
                                      {"request": request, "success": True, "expired": False})


@app.post("/resend-verification")
def resend_verification(
    request: Request,
    csrf_token: str = Form(None),
    db: Session = Depends(get_db),
):
    try:
        tenant_id = get_current_tenant_id(request)
    except HTTPException:
        return _redirect_login()
    validate_csrf(request, csrf_token)
    rate_limit(f"resend-ver:{tenant_id}", max_requests=3, window_seconds=3600)
    tenant = _get_tenant(tenant_id, db)
    if not tenant.email_verified:
        ver_token = secrets.token_urlsafe(32)
        tenant.verification_token = ver_token
        tenant.verification_sent_at = datetime.now(timezone.utc)
        db.commit()
        send_verification_email(tenant.email, ver_token)
    return RedirectResponse("/dashboard", status_code=302)


@app.get("/forgot-password", response_class=HTMLResponse)
def forgot_password_page(request: Request):
    return templates.TemplateResponse("forgot_password.html",
                                      {"request": request, "sent": False, "error": None})


@app.post("/forgot-password", response_class=HTMLResponse)
def forgot_password_post(
    request: Request,
    email: str = Form(...),
    csrf_token: str = Form(None),
    db: Session = Depends(get_db),
):
    rate_limit(f"forgot:{client_ip(request)}", max_requests=5, window_seconds=3600)
    validate_csrf(request, csrf_token)
    email = email.lower().strip()
    tenant = db.query(Tenant).filter_by(email=email).first()
    if tenant:
        reset_tok = secrets.token_urlsafe(32)
        tenant.reset_token = reset_tok
        tenant.reset_token_expires = datetime.now(timezone.utc) + timedelta(hours=1)
        db.commit()
        send_password_reset_email(email, reset_tok)
    # Always show success to prevent user enumeration
    return templates.TemplateResponse("forgot_password.html",
                                      {"request": request, "sent": True, "error": None})


@app.get("/reset-password", response_class=HTMLResponse)
def reset_password_page(request: Request, token: str = "", db: Session = Depends(get_db)):
    tenant = db.query(Tenant).filter_by(reset_token=token).first()
    if not tenant or not token:
        return templates.TemplateResponse("reset_password.html",
                                          {"request": request, "invalid": True, "success": False, "token": "", "error": None})
    if tenant.reset_token_expires:
        if datetime.now(timezone.utc) > tenant.reset_token_expires.replace(tzinfo=timezone.utc):
            return templates.TemplateResponse("reset_password.html",
                                              {"request": request, "invalid": True, "success": False, "token": "", "error": None})
    return templates.TemplateResponse("reset_password.html",
                                      {"request": request, "invalid": False, "success": False, "token": token, "error": None})


@app.post("/reset-password", response_class=HTMLResponse)
def reset_password_post(
    request: Request,
    token: str = Form(...),
    password: str = Form(...),
    confirm: str = Form(...),
    csrf_token: str = Form(None),
    db: Session = Depends(get_db),
):
    rate_limit(f"reset:{client_ip(request)}", max_requests=10, window_seconds=3600)
    validate_csrf(request, csrf_token)
    tenant = db.query(Tenant).filter_by(reset_token=token).first()
    if not tenant:
        return templates.TemplateResponse("reset_password.html",
                                          {"request": request, "invalid": True, "success": False, "token": "", "error": None})
    if tenant.reset_token_expires:
        if datetime.now(timezone.utc) > tenant.reset_token_expires.replace(tzinfo=timezone.utc):
            return templates.TemplateResponse("reset_password.html",
                                              {"request": request, "invalid": True, "success": False, "token": "", "error": None})
    if password != confirm:
        return templates.TemplateResponse("reset_password.html",
                                          {"request": request, "invalid": False, "success": False, "token": token, "error": "Passwords do not match"})
    if len(password) < 8:
        return templates.TemplateResponse("reset_password.html",
                                          {"request": request, "invalid": False, "success": False, "token": token, "error": "Password must be at least 8 characters"})
    tenant.password_hash = hash_password(password)
    tenant.reset_token = None
    tenant.reset_token_expires = None
    db.commit()
    return templates.TemplateResponse("reset_password.html",
                                      {"request": request, "invalid": False, "success": True, "token": "", "error": None})


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
def approve_draft(draft_id: str, request: Request,
                  csrf_token: str = Form(None),
                  db: Session = Depends(get_db)):
    try:
        tenant_id = get_current_tenant_id(request)
    except HTTPException:
        return _redirect_login()
    validate_csrf(request, csrf_token)

    draft = db.query(Draft).filter_by(id=draft_id, tenant_id=tenant_id).first()
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")

    _execute_draft(draft, draft.draft, tenant_id, db)
    return RedirectResponse("/dashboard", status_code=302)


@app.post("/drafts/{draft_id}/edit")
def edit_draft(draft_id: str, request: Request, edited_text: str = Form(...),
               csrf_token: str = Form(None),
               db: Session = Depends(get_db)):
    try:
        tenant_id = get_current_tenant_id(request)
    except HTTPException:
        return _redirect_login()
    validate_csrf(request, csrf_token)

    draft = db.query(Draft).filter_by(id=draft_id, tenant_id=tenant_id).first()
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")

    _execute_draft(draft, edited_text.strip(), tenant_id, db)
    return RedirectResponse("/dashboard", status_code=302)


@app.post("/drafts/{draft_id}/skip")
def skip_draft(draft_id: str, request: Request,
               csrf_token: str = Form(None),
               db: Session = Depends(get_db)):
    try:
        tenant_id = get_current_tenant_id(request)
    except HTTPException:
        return _redirect_login()
    validate_csrf(request, csrf_token)

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


# ---------------------------------------------------------------------------
# Baileys outbound queue
# Priority: Redis (fast, shared across workers) → DB (durable, survives restarts)
# Redis TTL is 48h — survives any plausible server downtime.
# DB rows are written alongside Redis; popping marks them delivered=True
# so we have a permanent audit trail and zero message loss even on Redis failure.
# ---------------------------------------------------------------------------

def _queue_baileys_outbound(tenant_id: str, to_phone: str, text: str, db: Session):
    from web.redis_client import get_redis
    r = get_redis()
    # Always persist to DB first — durable audit trail regardless of Redis
    try:
        row = BaileysOutbound(tenant_id=tenant_id, to_phone=to_phone, text=text)
        db.add(row)
        db.commit()
        db.refresh(row)
        row_id = row.id
    except Exception as exc:
        log.error("[%s] Failed to persist Baileys outbound to DB: %s", tenant_id, exc)
        db.rollback()
        row_id = None

    msg = json.dumps({"to": to_phone, "text": text, "db_id": row_id})
    if r is not None:
        try:
            r.rpush(f"baileys_out:{tenant_id}", msg)
            r.expire(f"baileys_out:{tenant_id}", 172800)  # 48h — survives server downtime
            log.info("[%s] Queued Baileys outbound (Redis+DB) to %s", tenant_id, to_phone)
            return
        except Exception as exc:
            log.warning("[%s] Redis push failed — will serve from DB on next poll: %s", tenant_id, exc)
    log.info("[%s] Queued Baileys outbound (DB-only) to %s", tenant_id, to_phone)


def _pop_baileys_outbound(tenant_id: str) -> list[dict]:
    """
    Return all pending outbound messages for a tenant and mark them delivered.
    Tries Redis first (fast); falls back to DB if Redis is empty or unavailable.
    """
    from web.redis_client import get_redis
    r = get_redis()
    msgs: list[dict] = []
    db_ids: list[int] = []

    if r is not None:
        try:
            key = f"baileys_out:{tenant_id}"
            pipe = r.pipeline()
            pipe.lrange(key, 0, -1)
            pipe.delete(key)
            results, _ = pipe.execute()
            for raw in results:
                item = json.loads(raw)
                msgs.append({"to": item["to"], "text": item["text"]})
                if item.get("db_id"):
                    db_ids.append(item["db_id"])
        except Exception as exc:
            log.warning("[%s] Redis pop failed, falling back to DB: %s", tenant_id, exc)

    # Fallback: if Redis returned nothing (empty or failed), check DB for undelivered rows
    if not msgs:
        db = SessionLocal()
        try:
            rows = (db.query(BaileysOutbound)
                    .filter_by(tenant_id=tenant_id, delivered=False)
                    .order_by(BaileysOutbound.created_at)
                    .all())
            for row in rows:
                msgs.append({"to": row.to_phone, "text": row.text})
                db_ids.append(row.id)
        finally:
            db.close()

    # Mark DB rows as delivered
    if db_ids:
        db = SessionLocal()
        try:
            (db.query(BaileysOutbound)
             .filter(BaileysOutbound.id.in_(db_ids))
             .update({"delivered": True, "delivered_at": datetime.now(timezone.utc)},
                     synchronize_session=False))
            db.commit()
        except Exception as exc:
            log.warning("[%s] Failed to mark Baileys rows delivered: %s", tenant_id, exc)
            db.rollback()
        finally:
            db.close()

    return msgs


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
    csrf_token:            str = Form(None),
    db: Session = Depends(get_db),
):
    try:
        tenant_id = get_current_tenant_id(request)
    except HTTPException:
        return _redirect_login()
    validate_csrf(request, csrf_token)

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
               phone: str = Form(...), notes: str = Form(""),
               csrf_token: str = Form(None),
               db: Session = Depends(get_db)):
    try:
        tenant_id = get_current_tenant_id(request)
    except HTTPException:
        return _redirect_login()
    validate_csrf(request, csrf_token)
    db.add(Vendor(tenant_id=tenant_id, category=category, name=name, phone=phone, notes=notes or None))
    db.commit()
    return RedirectResponse("/settings#vendors", status_code=302)


@app.post("/vendors/{vendor_id}/delete")
def vendor_delete(vendor_id: int, request: Request,
                  csrf_token: str = Form(None),
                  db: Session = Depends(get_db)):
    try:
        tenant_id = get_current_tenant_id(request)
    except HTTPException:
        return _redirect_login()
    validate_csrf(request, csrf_token)
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
def billing_subscribe(plan: str, request: Request,
                      csrf_token: str = Form(None),
                      db: Session = Depends(get_db)):
    try:
        tenant_id = get_current_tenant_id(request)
    except HTTPException:
        return _redirect_login()
    validate_csrf(request, csrf_token)

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
def billing_portal(request: Request,
                   csrf_token: str = Form(None),
                   db: Session = Depends(get_db)):
    try:
        tenant_id = get_current_tenant_id(request)
    except HTTPException:
        return _redirect_login()
    validate_csrf(request, csrf_token)
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
    msgs = _pop_baileys_outbound(tenant_id)
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
def api_generate_bot_token(request: Request,
                           csrf_token: str = Form(None),
                           db: Session = Depends(get_db)):
    """Generate (or regenerate) the Baileys bot API token for this tenant."""
    try:
        tenant_id = get_current_tenant_id(request)
    except HTTPException:
        raise HTTPException(status_code=401)
    validate_csrf(request, csrf_token)

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
        "#!/usr/bin/env bash\n"
        "set -e\n"
        "echo '=== HostAI Baileys Bot Setup ==='\n"
        "command -v node >/dev/null 2>&1 || { echo 'Node.js not found. Download from https://nodejs.org (v22+)'; exit 1; }\n"
        "echo \"Node $(node --version)\"\n"
        "npm install --silent\n"
        "echo ''\n"
        "# Use PM2 if available (auto-restart on crash, runs in background)\n"
        "if command -v pm2 >/dev/null 2>&1; then\n"
        "  pm2 start ecosystem.config.js\n"
        "  pm2 save\n"
        "  echo ''\n"
        "  echo 'Bot started with PM2 (auto-restarts on crash).'\n"
        "  echo 'To see QR code: pm2 logs hostai-bot'\n"
        "  echo 'To stop:        pm2 stop hostai-bot'\n"
        "else\n"
        "  echo 'Starting bot... Scan the QR code with WhatsApp.'\n"
        "  echo '(Tip: install PM2 for auto-restart: npm install -g pm2)'\n"
        "  node bot.js\n"
        "fi\n"
    )
    setup_bat = (
        "@echo off\n"
        "echo === HostAI Baileys Bot Setup ===\n"
        "where node >nul 2>&1 || (echo Node.js not found. Download from https://nodejs.org ^(v22+^) && pause && exit /b 1)\n"
        "npm install --silent\n"
        "echo.\n"
        "where pm2 >nul 2>&1\n"
        "if %ERRORLEVEL% EQU 0 (\n"
        "  pm2 start ecosystem.config.js\n"
        "  pm2 save\n"
        "  echo Bot started with PM2. To see QR: pm2 logs hostai-bot\n"
        ") else (\n"
        "  echo Starting bot... Scan the QR code with WhatsApp.\n"
        "  echo Tip: install PM2 for auto-restart: npm install -g pm2\n"
        "  node bot.js\n"
        ")\n"
        "pause\n"
    )
    pm2_config = json.dumps({
        "apps": [{
            "name":       "hostai-bot",
            "script":     "bot.js",
            "watch":      False,
            "restart_delay": 3000,
            "max_restarts":  10,
            "env": {
                "NODE_ENV": "production",
            },
        }]
    }, indent=2)
    readme = (
        "HostAI Baileys Bot — Quick Start\n"
        "=================================\n\n"
        "Requirements: Node.js 22+ — download from https://nodejs.org\n\n"
        "━━ First time setup ━━━━━━━━━━━━━━━━━━━━━\n\n"
        "  Mac / Linux:\n"
        "    chmod +x setup.sh\n"
        "    ./setup.sh\n\n"
        "  Windows:\n"
        "    Double-click setup.bat\n\n"
        "━━ Steps ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "1. Run setup.sh (or setup.bat on Windows)\n"
        "2. Scan the QR code printed in the terminal:\n"
        "     WhatsApp → ... → Linked Devices → Link a Device\n"
        "3. Done! You only need to scan once.\n\n"
        "━━ Recommended: PM2 (auto-restart on crash) ━\n\n"
        "  npm install -g pm2\n"
        "  Then re-run setup.sh — PM2 starts automatically.\n"
        "  pm2 startup   ← makes bot start on computer boot\n\n"
        "━━ Keep bot running ━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "  - Your PC must be on and connected to the internet\n"
        "  - Bot reconnects automatically if connection drops\n"
        "  - WhatsApp messages go through your home IP (no ban risk)\n\n"
        "━━ Commands (type in WhatsApp to your host number) ━\n\n"
        "  APPROVE [id]         Send AI draft to guest\n"
        "  EDIT [id]: [text]    Edit draft then send\n"
        "  SKIP [id]            Discard draft\n\n"
        f"Dashboard: {APP_BASE_URL}/dashboard\n"
        f"Settings:  {APP_BASE_URL}/settings\n"
    )

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("hostai-bot/bot.js",              bot_js_content)
        zf.writestr("hostai-bot/package.json",         pkg_json)
        zf.writestr("hostai-bot/.env",                 env_content)
        zf.writestr("hostai-bot/ecosystem.config.js",  pm2_config)
        zf.writestr("hostai-bot/setup.sh",             setup_sh)
        zf.writestr("hostai-bot/setup.bat",            setup_bat)
        zf.writestr("hostai-bot/README.txt",           readme)
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
    csrf = getattr(request.state, "csrf_token", "")
    return HTMLResponse(f"""
    <form method="post" action="/drafts/{draft_id}/edit" style="margin-top:0.5rem">
      <input type="hidden" name="csrf_token" value="{csrf}">
      <textarea name="edited_text" style="width:100%;padding:8px;border:1px solid #ced4da;border-radius:6px;
        font-size:0.875rem;line-height:1.6;min-height:120px;resize:vertical"
      >{draft.draft}</textarea>
      <div style="display:flex;gap:0.5rem;margin-top:0.5rem">
        <button type="submit" class="btn btn-primary btn-sm">Send edited version</button>
      </div>
    </form>
    """)


@app.get("/health")
def health(db: Session = Depends(get_db)):
    try:
        db.execute(__import__("sqlalchemy").text("SELECT 1"))
        db_ok = True
    except Exception:
        db_ok = False

    from web.redis_client import get_redis
    r = get_redis()
    redis_ok = False
    if r is not None:
        try:
            r.ping()
            redis_ok = True
        except Exception:
            pass

    status = "ok" if db_ok else "degraded"
    return JSONResponse(
        {"status": status, "db": "ok" if db_ok else "error",
         "redis": "ok" if redis_ok else ("disabled" if r is None else "error")},
        status_code=200 if db_ok else 503,
    )


@app.get("/metrics")
def metrics(db: Session = Depends(get_db)):
    """Basic operational metrics — JSON format. Protect with your monitoring auth or firewall."""
    import threading
    from web.redis_client import get_redis

    # DB stats
    try:
        total_tenants = db.query(Tenant).count()
        active_drafts = db.query(Draft).filter_by(status="pending").count()
        db_ok = True
    except Exception:
        total_tenants = active_drafts = -1
        db_ok = False

    # Worker stats
    active_workers = sum(
        1 for tid in list(worker_manager._workers.keys())
        if worker_manager.worker_status(tid)["email_running"]
    )

    # Redis
    r = get_redis()
    redis_ok = False
    if r is not None:
        try:
            r.ping()
            redis_ok = True
        except Exception:
            pass

    return JSONResponse({
        "db":             "ok" if db_ok else "error",
        "redis":          "ok" if redis_ok else ("disabled" if r is None else "error"),
        "total_tenants":  total_tenants,
        "pending_drafts": active_drafts,
        "active_workers": active_workers,
        "threads":        threading.active_count(),
        "watchdog_ok":    (worker_manager._watchdog_thread is not None
                           and worker_manager._watchdog_thread.is_alive()),
    })


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("web.app:app", host="0.0.0.0", port=port, reload=False)
