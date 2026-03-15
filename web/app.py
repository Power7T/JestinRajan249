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
  POST /drafts/{id}/approve  → approve draft (auto-send email)
  POST /drafts/{id}/edit     → edit + approve draft
  POST /drafts/{id}/skip     → skip draft
  GET  /settings      → settings page
  POST /settings      → save settings (email, iCal, vendors, API key)
  GET  /activity      → activity log
  GET  /health        → liveness
  GET  /api/drafts    → JSON list of pending drafts (for HTMX polling)
  GET  /api/workers   → worker status JSON
"""

import os
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional

import uvicorn
from fastapi import FastAPI, Request, Form, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

from web.db import get_db, init_db, SessionLocal
from web.models import Tenant, TenantConfig, Draft, Vendor, ActivityLog
from web.auth import hash_password, verify_password, create_token, get_current_tenant_id
from web.crypto import encrypt, decrypt
from web import worker_manager

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

BASE_DIR   = os.path.dirname(__file__)
templates  = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

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
    # Create blank config
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
    pending = (db.query(Draft)
               .filter_by(tenant_id=tenant_id, status="pending")
               .order_by(Draft.created_at.desc())
               .all())
    status  = worker_manager.worker_status(tenant_id)
    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "tenant":  tenant,
        "drafts":  pending,
        "status":  status,
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
    """Send reply (if email source) and mark draft approved."""
    if draft.source == "email" and draft.reply_to:
        cfg_row = db.query(TenantConfig).filter_by(tenant_id=tenant_id).first()
        if cfg_row and cfg_row.email_address:
            try:
                from web.email_worker import _send_smtp_reply, EmailConfig
                cfg = EmailConfig(
                    tenant_id=tenant_id,
                    imap_host=cfg_row.imap_host or "",
                    imap_port=cfg_row.imap_port,
                    smtp_host=cfg_row.smtp_host or "",
                    smtp_port=cfg_row.smtp_port,
                    email_address=cfg_row.email_address,
                    email_password=decrypt(cfg_row.email_password_enc or ""),
                    anthropic_api_key="",
                )
                _send_smtp_reply(cfg, draft.reply_to, f"Re: Airbnb message from {draft.guest_name}", final_text)
                log.info("[%s] Reply sent to %s", tenant_id, draft.reply_to)
            except Exception as exc:
                log.error("[%s] SMTP send failed: %s", tenant_id, exc)

    draft.status     = "approved"
    draft.final_text = final_text
    draft.approved_at = datetime.now(timezone.utc)
    db.add(ActivityLog(tenant_id=tenant_id, event_type="draft_approved",
                       message=f"Draft approved: {draft.guest_name}"))
    db.commit()


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
        "request": request,
        "tenant":  tenant,
        "cfg":     cfg,
        "vendors": vendors,
        "saved":   False,
    })


@app.post("/settings", response_class=HTMLResponse)
def settings_save(
    request:       Request,
    property_names: str = Form(""),
    ical_urls:      str = Form(""),
    imap_host:      str = Form(""),
    smtp_host:      str = Form(""),
    email_address:  str = Form(""),
    email_password: str = Form(""),
    anthropic_key:  str = Form(""),
    db: Session = Depends(get_db),
):
    try:
        tenant_id = get_current_tenant_id(request)
    except HTTPException:
        return _redirect_login()

    cfg = _get_or_create_config(tenant_id, db)
    cfg.property_names = property_names.strip()
    cfg.ical_urls      = ical_urls.strip()
    cfg.imap_host      = imap_host.strip() or None
    cfg.smtp_host      = smtp_host.strip() or None
    cfg.email_address  = email_address.strip() or None

    if email_password.strip():
        cfg.email_password_enc = encrypt(email_password.strip())
    if anthropic_key.strip():
        cfg.anthropic_api_key_enc = encrypt(anthropic_key.strip())

    db.add(ActivityLog(tenant_id=tenant_id, event_type="settings_saved",
                       message="Settings updated"))
    db.commit()

    # Restart workers with new config
    worker_manager.restart_worker(tenant_id)

    tenant  = _get_tenant(tenant_id, db)
    vendors = db.query(Vendor).filter_by(tenant_id=tenant_id).order_by(Vendor.category, Vendor.name).all()
    return templates.TemplateResponse("settings.html", {
        "request": request,
        "tenant":  tenant,
        "cfg":     cfg,
        "vendors": vendors,
        "saved":   True,
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
    """Returns inline edit textarea loaded by HTMX."""
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
