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
  GET  /logout        → logout confirmation page
  POST /logout        → clear cookie
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

import base64
import csv
import hashlib
import hmac
import io
import json
import logging
import os
import re
import secrets
import time
import traceback
import zipfile
from html import escape
from contextlib import asynccontextmanager
import contextvars
from uuid import uuid4
from datetime import datetime, timezone, timedelta, date as date_type
from typing import Optional
from urllib.parse import urlsplit, urlunsplit

import uvicorn
from fastapi import FastAPI, Request, Form, Depends, HTTPException, Header, UploadFile, File
from fastapi.responses import (
    HTMLResponse, RedirectResponse, JSONResponse, StreamingResponse
)
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

from web.db import get_db, init_db, SessionLocal
from web.db_read import get_read_db
from web.models import (
    SystemConfig, ApiUsageLog,
    Tenant, TenantConfig, Draft, Vendor, ActivityLog, BaileysOutbound, BaileysCallback,
    Reservation, ReservationSyncLog, ReservationIntakeBatch, GuestContact,
    AutomationRule, TeamMember, GuestTimelineEvent, ArrivalActivation, IssueTicket, TenantKpiSnapshot,
    PMSIntegration, PMSProcessedMessage,
    ProcessedEmail, PlanConfig, FailedDraftLog,
    PLAN_FREE, PLAN_BAILEYS, PLAN_META_CLOUD, PLAN_SMS, PLAN_PRO,
    PLAN_STARTER, PLAN_GROWTH,
)
from web.auth import (
    hash_password, verify_password, create_token, get_current_tenant_id,
    tenant_session_version, decode_token, create_member_token, get_current_member,
    member_session_version,
)
from web.crypto import encrypt, decrypt
from web import worker_manager
from web import billing as billing_mod
from web.mailer import send_verification_email, send_password_reset_email, send_welcome_email, send_weekly_digest, validate_smtp_config, send_team_invite, send_admin_alert
from web.billing import (
    PLAN_INFO, ACTIVE_STATUSES, tenant_has_channel, require_channel,
    create_checkout_session, create_portal_session, handle_stripe_webhook,
    generate_bot_token, verify_bot_token,
)
from web.security import (
    CSRFMiddleware, SecurityHeadersMiddleware,
    validate_csrf, rate_limit, client_ip, is_request_secure,
)
from web.request_safety import ensure_public_hostname, ensure_public_url
from web.workflow import (
    analyze_guest_sentiment,
    automation_rule_decision,
    build_activation_checklist,
    build_conversation_memory,
    build_guest_timeline,
    build_thread_key,
    compute_guest_history_score,
    compute_portfolio_benchmark,
    compute_review_velocity,
    compute_stay_stage,
    draft_policy_conflicts,
    derive_dashboard_kpis,
    surface_exception_queue,
)
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from web.metrics_prom import REQUEST_COUNT, REQUEST_DURATION, normalize_path
from web.flags import flags, require_flag

# Global context var for Request ID tracking (#18)
request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="")

class _JSONFormatter(logging.Formatter):
    """Emit log records as single-line JSON objects for structured log ingestion."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts":      self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level":   record.levelname,
            "logger":  record.name,
            "msg":     record.getMessage(),
        }
        if req_id := request_id_var.get():
            payload["req_id"] = req_id
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def _configure_logging() -> None:
    handler = logging.StreamHandler()
    if os.getenv("ENVIRONMENT", "production") != "development":
        handler.setFormatter(_JSONFormatter())
    else:
        class DevFormatter(logging.Formatter):
            def format(self, record: logging.LogRecord) -> str:
                msg = super().format(record)
                if rid := request_id_var.get():
                    return f"[{rid[:8]}] {msg}"
                return msg
        handler.setFormatter(DevFormatter("%(asctime)s %(levelname)s %(message)s"))
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    # Suppress noisy SQLAlchemy engine logs
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)


_configure_logging()
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

_ENVIRONMENT = os.getenv("ENVIRONMENT", "production").lower()
_IS_DEV_ENV = _ENVIRONMENT in {"development", "dev", "test"}

# Admin email allowlist — comma-separated in ADMIN_EMAILS env var
_ADMIN_EMAILS: set = {
    e.strip().lower()
    for e in os.getenv("ADMIN_EMAILS", "").split(",")
    if e.strip()
}
# Only add dev email in development mode (admin safeguard)
if _IS_DEV_ENV:
    _ADMIN_EMAILS.add("chandan@hostai.local")
templates.env.globals["is_admin"] = lambda email: bool(email) and email.lower() in _ADMIN_EMAILS

_APP_BASE_URL_RAW = os.getenv("APP_BASE_URL", "").strip().rstrip("/")
if not _APP_BASE_URL_RAW or _APP_BASE_URL_RAW == "https://your-domain.com":
    if not _IS_DEV_ENV:
        raise RuntimeError(
            "APP_BASE_URL must be set to your actual public domain in production "
            "(e.g. APP_BASE_URL=https://hostai.fly.dev). "
            "Email verification links and password-reset emails will be broken without it."
        )
    _APP_BASE_URL_RAW = _APP_BASE_URL_RAW or "http://localhost:8000"
APP_BASE_URL = _APP_BASE_URL_RAW
INBOUND_EMAIL_DOMAIN = os.getenv("INBOUND_EMAIL_DOMAIN", "inbound.hostai.local").strip().lower()

# ---------------------------------------------------------------------------
# App lifecycle
# ---------------------------------------------------------------------------

def _startup_checks() -> None:
    """Warn about common production misconfiguration at startup."""
    if _IS_DEV_ENV:
        return

    warnings: list[str] = []

    # Redis is required for cross-worker rate limiting and Stripe idempotency.
    # With multiple workers and no Redis, rate limits are per-process only.
    workers = int(os.getenv("WORKERS", "2"))
    if workers > 1 and not os.getenv("REDIS_URL", ""):
        warnings.append(
            f"REDIS_URL is not set but WORKERS={workers}. Rate limits and Stripe "
            "webhook idempotency are per-process only — set REDIS_URL to share state "
            "across workers."
        )

    # STRIPE_SECRET_KEY must be set for any billing operation.
    if not os.getenv("STRIPE_SECRET_KEY", ""):
        warnings.append(
            "STRIPE_SECRET_KEY is not set. All billing and subscription endpoints "
            "will fail at runtime."
        )

    # INTERNAL_TOKEN should be set when the Baileys WhatsApp bot is deployed
    # alongside this server so the bot ↔ server channel is authenticated.
    if not os.getenv("INTERNAL_TOKEN", ""):
        warnings.append(
            "INTERNAL_TOKEN is not set. If you are running the Baileys WhatsApp bot "
            "alongside this server, set INTERNAL_TOKEN to a shared secret so the "
            "bot-to-server channel is authenticated."
        )

    # Admin emails must be configured for production
    if not os.getenv("ADMIN_EMAILS", "").strip():
        warnings.append(
            "ADMIN_EMAILS is not set. Admin panel will require hardcoded dev email. "
            "Set ADMIN_EMAILS=you@yourdomain.com for production access."
        )

    # SMTP must be configured for all alerting features to work
    if not os.getenv("SMTP_HOST", "").strip():
        warnings.append(
            "SMTP_HOST is not set. All host alerting emails (worker failure, subscription "
            "expiry, integration failure) and admin alerts will be silently skipped."
        )

    for warning in warnings:
        log.warning("[startup] %s", warning)


def _gdpr_data_retention_job():
    """Background job to clean up old messages based on tenant retention settings. (GDPR #21)"""
    db = SessionLocal()
    try:
        from web.models import TenantConfig, BaileysOutbound, Draft, ProcessedEmail, ActivityLog, FailedDraftLog, PMSProcessedMessage
        now = datetime.now(timezone.utc)
        configs = db.query(TenantConfig).all()
        
        total_deleted = 0
        for cfg in configs:
            cutoff = now - timedelta(days=cfg.data_retention_days)
            tid = cfg.tenant_id
            
            total_deleted += db.query(BaileysOutbound).filter(BaileysOutbound.tenant_id == tid, BaileysOutbound.created_at < cutoff).delete(synchronize_session=False)
            total_deleted += db.query(Draft).filter(Draft.tenant_id == tid, Draft.created_at < cutoff).delete(synchronize_session=False)
            total_deleted += db.query(ProcessedEmail).filter(ProcessedEmail.tenant_id == tid, ProcessedEmail.created_at < cutoff).delete(synchronize_session=False)
            total_deleted += db.query(ActivityLog).filter(ActivityLog.tenant_id == tid, ActivityLog.timestamp < cutoff).delete(synchronize_session=False)
            total_deleted += db.query(FailedDraftLog).filter(FailedDraftLog.tenant_id == tid, FailedDraftLog.created_at < cutoff).delete(synchronize_session=False)
            total_deleted += db.query(PMSProcessedMessage).filter(PMSProcessedMessage.tenant_id == tid, PMSProcessedMessage.created_at < cutoff).delete(synchronize_session=False)

        db.commit()
        log.info("GDPR data retention cleanup: deleted %d old records", total_deleted)
    except Exception as e:
        log.error("Baileys cleanup job failed: %s", str(e))
        db.rollback()
    finally:
        db.close()


def _baileys_retry_stale_job():
    """Background job to retry stale in_transit Baileys messages. (Baileys fix #2)"""
    from web.redis_client import get_redis
    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        stale_cutoff = now - timedelta(minutes=5)

        # Find messages stuck in in_transit for >5 minutes
        stale_msgs = db.query(BaileysOutbound).filter(
            BaileysOutbound.status == "in_transit",
            BaileysOutbound.created_at < stale_cutoff
        ).all()

        r = get_redis()
        retried = 0
        failed = 0

        for row in stale_msgs:
            if row.retry_count >= 3:
                # Max retries exceeded — mark as failed
                row.status = "failed"
                row.delivered = False
                row.error_reason = "Max retries exceeded — message stuck in transit"
                # Alert host when message permanently fails (Failure gap fix #5)
                try:
                    _cfg = db.query(TenantConfig).filter_by(tenant_id=row.tenant_id).first()
                    _t = db.query(Tenant).filter_by(id=row.tenant_id).first()
                    if _cfg and _t:
                        from web.mailer import send_integration_alert
                        send_integration_alert(
                            _cfg.escalation_email or _t.email,
                            "WhatsApp (Baileys)", 3,
                            f"Message to ...{(row.to_phone or '')[-4:]} could not be delivered after 3 retries"
                        )
                except Exception:
                    pass  # non-critical — message already marked failed
                failed += 1
            else:
                # Re-queue to pending and increment retry counter
                row.status = "pending"
                row.retry_count += 1
                row.last_retry_at = now
                # Re-push to Redis queue if available
                if r:
                    try:
                        msg = json.dumps({"to": row.to_phone, "text": row.text, "db_id": row.id})
                        r.rpush(f"baileys_out:{row.tenant_id}", msg)
                        r.expire(f"baileys_out:{row.tenant_id}", 172800)
                    except Exception as exc:
                        log.warning("[%s] Failed to re-queue Baileys message %d: %s", row.tenant_id, row.id, exc)
                retried += 1

        db.commit()
        log.info("Baileys stale message retry job: retried=%d, failed=%d", retried, failed)
    except Exception as e:
        log.error("Baileys retry job crashed [%s]: %s", type(e).__name__, str(e))
        try:
            db.rollback()
        except Exception:
            pass
    finally:
        db.close()


async def lifespan(app: FastAPI):
    _startup_checks()
    init_db()
    validate_smtp_config()  # Validate SMTP at startup — fail fast, not on first email send
    worker_manager.start_all_workers()

    # Start background scheduler for GDPR cleanup and Baileys retry jobs
    scheduler = BackgroundScheduler()
    scheduler.add_job(
        _gdpr_data_retention_job,
        CronTrigger(hour=2, minute=0, timezone="UTC"),  # Daily at 2:00 AM UTC
        id="gdpr_retention_cleanup",
        name="GDPR Data Retention",
        replace_existing=True
    )
    scheduler.add_job(
        _baileys_retry_stale_job,
        "interval", minutes=5,  # Every 5 minutes (Baileys fix #2)
        id="baileys_retry_stale",
        name="Baileys Stale Message Retry",
        replace_existing=True
    )
    # Add APScheduler event listeners for job failures (Failure gap fix #5)
    from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_MISSED

    def _scheduler_error_listener(event):
        log.error("Scheduled job crashed: job_id=%s exception=%s",
                  event.job_id, event.exception)

    def _scheduler_missed_listener(event):
        log.warning("Scheduled job missed: job_id=%s scheduled_run_time=%s",
                    event.job_id, event.scheduled_run_time)

    scheduler.add_listener(_scheduler_error_listener, EVENT_JOB_ERROR)
    scheduler.add_listener(_scheduler_missed_listener, EVENT_JOB_MISSED)

    scheduler.start()
    log.info("Scheduled background jobs started (cleanup at 02:00 UTC daily, Baileys retry every 5 min)")

    flags.log_state()  # Log all feature flag values at startup
    log.info("Airbnb Host Assistant web app started")
    yield
    scheduler.shutdown()
    worker_manager.stop_all_workers()
    log.info("Airbnb Host Assistant web app stopped")


app = FastAPI(
    title="Airbnb Host Assistant",
    lifespan=lifespan,
    docs_url="/docs" if _IS_DEV_ENV else None,
    redoc_url="/redoc" if _IS_DEV_ENV else None,
)
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")

# Middleware (applied in reverse order — bottom first)
@app.middleware("http")
async def prometheus_middleware(request: Request, call_next):
    """Records per-request latency + status counters for Prometheus scraping."""
    path = normalize_path(request.url.path)
    # Skip metrics/health endpoints from latency tracking to avoid noise
    if path in {"/metrics", "/metrics/prometheus", "/health", "/ping"}:
        return await call_next(request)
    t0 = time.perf_counter()
    response = await call_next(request)
    duration = time.perf_counter() - t0
    status = response.status_code
    REQUEST_COUNT.labels(method=request.method, path=path, status=str(status)).inc()
    REQUEST_DURATION.labels(method=request.method, path=path).observe(duration)
    return response


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    """Injects and tracks an X-Request-ID for correlation logging (#18)."""
    req_id = request.headers.get("X-Request-ID") or str(uuid4())
    token = request_id_var.set(req_id)
    try:
        response = await call_next(request)
        response.headers["X-Request-ID"] = req_id
        return response
    finally:
        request_id_var.reset(token)

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
         "message": "Something went wrong on our end. Please try again in a moment.",
         "debug_detail": traceback.format_exc()},  # Admin-only via template conditional
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


def _slug_email_alias(seed: str) -> str:
    alias = re.sub(r"[^a-z0-9]+", "-", (seed or "").lower()).strip("-")
    return alias[:24] or "host"


def _ensure_inbound_email_alias(tenant: Tenant, cfg: TenantConfig, db: Session) -> str:
    alias = (cfg.inbound_email_alias or "").strip().lower()
    if alias:
        return alias

    base = _slug_email_alias((tenant.email or "").split("@")[0])
    suffix = (tenant.id or secrets.token_hex(4)).replace("-", "")[:6]
    candidate = f"{base}-{suffix}"
    counter = 1
    while db.query(TenantConfig).filter(
        TenantConfig.inbound_email_alias == candidate,
        TenantConfig.tenant_id != tenant.id,
    ).first():
        candidate = f"{base}-{suffix}{counter}"
        counter += 1
    cfg.inbound_email_alias = candidate
    return candidate


def _tenant_inbound_email_address(cfg: TenantConfig) -> str:
    alias = (cfg.inbound_email_alias or "").strip().lower()
    return f"{alias}@{INBOUND_EMAIL_DOMAIN}" if alias else ""


def _extract_recipient_alias(recipient: str) -> str:
    if not recipient:
        return ""
    address = recipient.strip().lower()
    if "<" in address and ">" in address:
        address = address.split("<", 1)[1].split(">", 1)[0]
    local = address.split("@", 1)[0]
    return local.split("+", 1)[0]

def _inbound_replay_guard(key: str, ttl_seconds: int) -> bool:
    """Prevent replay of inbound webhooks when Redis is available."""
    from web.redis_client import get_redis

    require_raw = os.getenv("INBOUND_PARSE_REQUIRE_REPLAY", "").strip().lower()
    require = require_raw in {"1", "true", "yes", "on"} or (not require_raw and not _IS_DEV_ENV)

    r = get_redis()
    if r is None:
        if require:
            log.warning("Inbound replay guard requires Redis; rejecting webhook")
            return False
        return True

    try:
        digest = hashlib.sha256(key.encode()).hexdigest()
        stored = r.set(f"inbound:replay:{digest}", "1", nx=True, ex=ttl_seconds)
        if not stored:
            log.warning("Inbound webhook replay detected")
            return False
        return True
    except Exception as exc:
        if require:
            log.warning("Inbound replay guard failed: %s", exc)
            return False
        return True


def _verify_inbound_email_webhook(request: Request, payload: dict, raw_body: bytes) -> bool:
    provider = os.getenv("INBOUND_PARSE_PROVIDER", "").strip().lower()
    secret = os.getenv("INBOUND_PARSE_WEBHOOK_SECRET", "").strip()

    if secret:
        supplied = request.headers.get("X-Inbound-Webhook-Secret", "").strip()
        if not supplied and _IS_DEV_ENV:
            supplied = (
                request.query_params.get("token", "").strip()
                or str(payload.get("token", "")).strip()
            )
        if not supplied:
            return False
        if not secrets.compare_digest(supplied, secret):
            return False
    elif not _IS_DEV_ENV and provider not in {"mailgun", "postmark"}:
        log.error("Inbound webhook requires INBOUND_PARSE_WEBHOOK_SECRET or provider signature in production")
        return False

    max_age = int(os.getenv("INBOUND_PARSE_MAX_AGE", "300"))

    if provider == "mailgun":
        signing_key = os.getenv("MAILGUN_SIGNING_KEY", "").strip()
        if not signing_key:
            return _IS_DEV_ENV
        timestamp = str(payload.get("timestamp", "")).strip()
        token = str(payload.get("token", "")).strip()
        signature = str(payload.get("signature", "")).strip()
        if not (timestamp and token and signature):
            return False
        try:
            if abs(time.time() - int(timestamp)) > max_age:
                log.warning("Mailgun webhook timestamp outside tolerance")
                return False
        except ValueError:
            return False
        expected = hmac.new(signing_key.encode(), f"{timestamp}{token}".encode(), hashlib.sha256).hexdigest()
        if not secrets.compare_digest(signature, expected):
            return False
        if not _inbound_replay_guard(f"mailgun:{timestamp}:{token}:{signature}", max_age):
            return False

    elif provider == "postmark":
        signing_key = os.getenv("POSTMARK_INBOUND_SECRET", "").strip()
        signature = request.headers.get("X-Postmark-Signature", "").strip()
        if not signing_key:
            return _IS_DEV_ENV
        if not signature:
            return False
        expected = base64.b64encode(hmac.new(signing_key.encode(), raw_body, hashlib.sha256).digest()).decode()
        if not secrets.compare_digest(signature, expected):
            return False
        replay_key = (
            signature
            or _payload_header(payload, "Message-Id", "Message-ID")
            or _payload_value(payload, "message-id", "Message-ID")
        )
        if replay_key and not _inbound_replay_guard(f"postmark:{replay_key}", max_age):
            return False

    return True


def _payload_value(payload: dict, *keys: str) -> str:
    for key in keys:
        value = payload.get(key)
        if value:
            return str(value)
    return ""


def _payload_header(payload: dict, *keys: str) -> str:
    headers = payload.get("headers")
    if isinstance(headers, dict):
        for key in keys:
            value = headers.get(key)
            if value:
                return str(value)
    return ""


def _split_csv_values(value: str | None) -> list[str]:
    raw = (value or "").replace("\n", ",").replace(";", ",")
    return [part.strip() for part in raw.split(",") if part.strip()]


def _draft_property_name(draft: Draft) -> str:
    if draft.property_name_snapshot:
        return draft.property_name_snapshot
    if draft.reservation and draft.reservation.listing_name:
        return draft.reservation.listing_name
    return ""


def _draft_unit_identifier(draft: Draft) -> str:
    if draft.unit_identifier_snapshot:
        return draft.unit_identifier_snapshot
    if draft.reservation and draft.reservation.unit_identifier:
        return draft.reservation.unit_identifier
    return ""


def _property_match(selected_property: str, candidate: str) -> bool:
    if not selected_property:
        return True
    return selected_property.strip().lower() == (candidate or "").strip().lower()


def _member_property_scope(member: TeamMember) -> list[str]:
    return _split_csv_values(member.property_scope)


def _team_member_matches_property(member: TeamMember, selected_property: str) -> bool:
    if not selected_property:
        return True
    scope = _member_property_scope(member)
    if not scope:
        return True
    return any(_property_match(selected_property, item) for item in scope)


def _collect_property_options(
    cfg: TenantConfig,
    reservations: list[Reservation],
    drafts: list[Draft],
    open_issues: list[IssueTicket],
    team_members: list[TeamMember],
) -> list[str]:
    values: set[str] = set()
    for name in _split_csv_values(cfg.property_names):
        values.add(name)
    for reservation in reservations:
        if reservation.listing_name:
            values.add(reservation.listing_name)
    for draft in drafts:
        prop = _draft_property_name(draft)
        if prop:
            values.add(prop)
    for issue in open_issues:
        if issue.property_name:
            values.add(issue.property_name)
    for member in team_members:
        for scoped in _member_property_scope(member):
            values.add(scoped)
    return sorted(values)


def _recent_reservation_drafts(db: Session, tenant_id: str, reservation: Optional[Reservation], limit: int = 12) -> list[Draft]:
    if not reservation:
        return []
    return (
        db.query(Draft)
        .filter(Draft.tenant_id == tenant_id, Draft.reservation_id == reservation.id)
        .order_by(Draft.created_at.desc())
        .limit(limit)
        .all()
    )


def _draft_thread_metadata(
    db: Session,
    tenant_id: str,
    reservation: Optional[Reservation],
    reply_to: str,
    guest_name: str,
    source: str,
) -> tuple[str, Optional[str], int]:
    thread_key = build_thread_key(
        tenant_id,
        reservation_id=reservation.id if reservation else None,
        reply_to=reply_to,
        guest_name=guest_name,
        channel=source,
    )
    parent = (
        db.query(Draft)
        .filter(Draft.tenant_id == tenant_id, Draft.thread_key == thread_key)
        .order_by(Draft.created_at.desc())
        .first()
    )
    return thread_key, (parent.id if parent else None), ((parent.guest_message_index + 1) if parent else 1)


def _draft_policy_conflicts_json(conflicts: list[str]) -> Optional[str]:
    return json.dumps(conflicts) if conflicts else None


def _average_response_seconds(drafts: list[Draft]) -> Optional[float]:
    durations: list[float] = []
    for draft in drafts:
        if draft.created_at and draft.approved_at and draft.approved_at >= draft.created_at:
            durations.append((draft.approved_at - draft.created_at).total_seconds())
    if not durations:
        return None
    return round(sum(durations) / len(durations), 1)


def _sentiment_summary(drafts: list[Draft], reservations: list[Reservation]) -> dict[str, object]:
    scores = [float(draft.sentiment_score) for draft in drafts if draft.sentiment_score is not None]
    review_scores = [float(res.review_sentiment_score) for res in reservations if res.review_sentiment_score is not None]
    recent_scores = scores[-5:]
    earlier_scores = scores[:-5]
    avg_guest = round(sum(scores) / len(scores), 2) if scores else 0.0
    avg_review = round(sum(review_scores) / len(review_scores), 2) if review_scores else 0.0
    trend = "stable"
    if recent_scores and earlier_scores:
        recent_avg = sum(recent_scores) / len(recent_scores)
        earlier_avg = sum(earlier_scores) / len(earlier_scores)
        if recent_avg - earlier_avg >= 0.15:
            trend = "improving"
        elif earlier_avg - recent_avg >= 0.15:
            trend = "worsening"
    return {
        "avg_guest": avg_guest,
        "avg_review": avg_review,
        "trend": trend,
    }


def _redirect_login():
    return RedirectResponse("/login", status_code=302)


def _token_digest(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode()).hexdigest()


def _store_token(raw_token: str) -> str:
    """Store only a digest for bearer-style one-time tokens."""
    return _token_digest(raw_token)


def _find_tenant_by_token(db: Session, column: str, token: str) -> Optional[Tenant]:
    """Lookup a tenant by a token column, supporting legacy plaintext rows."""
    token_digest = _token_digest(token)
    col = getattr(Tenant, column)
    tenant = db.query(Tenant).filter(col == token_digest).first()
    if tenant:
        return tenant
    return db.query(Tenant).filter(col == token).first()


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
            # Check token expiration (Improvement #11)
            if cfg.bot_api_token_expires_at and cfg.bot_api_token_expires_at < datetime.now(timezone.utc):
                raise HTTPException(status_code=401, detail="Bot token expired — regenerate in settings")
            return cfg
    raise HTTPException(status_code=401, detail="Invalid bot token")


def _bot_token_expiry_warning(cfg: TenantConfig) -> dict:
    """Return expiry warning dict if token expires within 7 days. (Baileys fix #8)"""
    if cfg.bot_api_token_expires_at:
        days_left = (cfg.bot_api_token_expires_at - datetime.now(timezone.utc)).days
        if 0 <= days_left <= 7:
            return {"token_expires_in_days": days_left,
                    "warning": f"Bot token expires in {days_left} day(s). Regenerate in Settings."}
    return {}


def _validate_phone_number(phone: str) -> bool:
    """Validate phone number is in E.164 format (~14 digits). (Improvement #5)"""
    # E.164: + followed by country code + number, max 15 digits total, no spaces/dashes
    phone = str(phone).strip()
    if not phone.startswith("+") or not phone[1:].isdigit():
        return False
    digits = len(phone) - 1  # exclude +
    return 8 <= digits <= 15


def _public_request_url(request: Request) -> str:
    """
    Build the public URL used by external webhook signature validators.
    Prefer APP_BASE_URL so validation remains stable behind reverse proxies.
    """
    path = request.url.path
    query = request.url.query
    base = APP_BASE_URL.strip()
    if base:
        parsed = urlsplit(base)
        return urlunsplit((parsed.scheme or "https", parsed.netloc, path, query, ""))
    return str(request.url)


def _validate_meta_signature(request_body: bytes, signature_header: str) -> bool:
    """
    Validate Meta webhook signatures when META_APP_SECRET is configured.
    In dev/test we allow missing configuration to keep local iteration simple.
    """
    app_secret = os.getenv("META_APP_SECRET", "").strip()
    if not app_secret:
        if _IS_DEV_ENV:
            return True
        log.error("META_APP_SECRET is required for Meta webhook verification")
        return False
    from web.meta_sender import verify_request_signature
    return verify_request_signature(request_body, signature_header, app_secret)


def _validate_twilio_signature(request: Request, form_data: dict, cfg: TenantConfig) -> bool:
    """
    Validate Twilio webhook signatures against the tenant's auth token.
    """
    auth_token = decrypt(cfg.twilio_auth_token_enc or "").strip()
    if not auth_token:
        if _IS_DEV_ENV:
            return True
        log.error("[%s] Twilio auth token missing; rejecting webhook", cfg.tenant_id)
        return False

    try:
        from twilio.request_validator import RequestValidator
        validator = RequestValidator(auth_token)
        signature = request.headers.get("X-Twilio-Signature", "")
        candidate_urls = [_public_request_url(request), str(request.url)]
        return any(validator.validate(url, form_data, signature) for url in dict.fromkeys(candidate_urls))
    except Exception as exc:
        log.warning("[%s] Twilio webhook validation error: %s", cfg.tenant_id, exc)
        return False


# ---------------------------------------------------------------------------
# Auth routes
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def root(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/terms", response_class=HTMLResponse)
def terms_page(request: Request):
    """Terms of Service page."""
    return templates.TemplateResponse("terms.html", {"request": request, "now": datetime.now(timezone.utc)})


@app.get("/privacy", response_class=HTMLResponse)
def privacy_page(request: Request):
    """Privacy Policy page."""
    return templates.TemplateResponse("privacy.html", {"request": request, "now": datetime.now(timezone.utc)})


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request, "error": None})


@app.post("/login", response_class=HTMLResponse)
def login_post(
    request:    Request,
    email:      str = Form(...),
    password:   str = Form(...),
    csrf_token: str = Form(None),
    db: Session = Depends(get_db),
):
    rate_limit(f"login:{client_ip(request)}", max_requests=10, window_seconds=900)
    validate_csrf(request, csrf_token)
    tenant = db.query(Tenant).filter_by(email=email.lower().strip()).first()
    if not tenant or not tenant.is_active or not verify_password(password, tenant.password_hash):
        return templates.TemplateResponse("login.html",
                                          {"request": request, "error": "Invalid email or password"})
    token = create_token(tenant.id, tenant_session_version(tenant))
    is_secure = is_request_secure(request)
    # Resume onboarding if not yet complete
    cfg = db.query(TenantConfig).filter_by(tenant_id=tenant.id).first()
    
    # If admin, redirect to /admin
    if tenant.email.lower().strip() in _ADMIN_EMAILS:
        redirect_to = "/admin"
    else:
        redirect_to = "/dashboard" if (cfg and cfg.onboarding_complete) else "/onboarding"
        
    resp = RedirectResponse(redirect_to, status_code=303)
    resp.set_cookie("session", token, httponly=True,
                    samesite="strict", secure=is_secure, max_age=72 * 3600)
    return resp


@app.get("/signup", response_class=HTMLResponse)
def signup_get(request: Request):
    """Display signup page."""
    return templates.TemplateResponse("signup.html", {"request": request})


@app.post("/signup", response_class=HTMLResponse)
def signup_post(
    request: Request,
    first_name: str = Form(...),
    last_name:  str = Form(...),
    email:      str = Form(...),
    country:    str = Form(...),
    phone:      str = Form(...),
    password:   str = Form(...),
    csrf_token: str = Form(None),
    db: Session = Depends(get_db),
):
    rate_limit(f"signup:{client_ip(request)}", max_requests=5, window_seconds=3600)
    validate_csrf(request, csrf_token)
    email = email.lower().strip()
    if db.query(Tenant).filter_by(email=email).first():
        return templates.TemplateResponse("signup.html",
                                          {"request": request, "error": "Email already registered"})
    if len(password) < 8:
        return templates.TemplateResponse("signup.html",
                                          {"request": request, "error": "Password must be 8+ characters"})
    ver_token = secrets.token_urlsafe(32)
    tenant = Tenant(
        first_name=first_name.strip(),
        last_name=last_name.strip(),
        email=email,
        country=country.strip(),
        phone=phone.strip(),
        password_hash=hash_password(password),
        verification_token=_store_token(ver_token),
        verification_sent_at=datetime.now(timezone.utc),
    )
    db.add(tenant)
    db.commit()
    db.refresh(tenant)
    db.add(TenantConfig(tenant_id=tenant.id))
    db.commit()
    # Send verification email (non-blocking — failure just logs a warning)
    send_verification_email(email, ver_token)
    token = create_token(tenant.id, tenant_session_version(tenant))
    is_secure = is_request_secure(request)
    resp = RedirectResponse("/onboarding", status_code=302)
    resp.set_cookie("session", token, httponly=True,
                    samesite="strict", secure=is_secure, max_age=72 * 3600)
    return resp


@app.get("/verify-email", response_class=HTMLResponse)
def verify_email(request: Request, token: str = "", db: Session = Depends(get_db)):
    if not token:
        return templates.TemplateResponse("verify_email.html",
                                          {"request": request, "success": False, "expired": False})
    tenant = _find_tenant_by_token(db, "verification_token", token)
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
        tenant.verification_token = _store_token(ver_token)
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
        tenant.reset_token = _store_token(reset_tok)
        tenant.reset_token_expires = datetime.now(timezone.utc) + timedelta(hours=1)
        db.commit()
        send_password_reset_email(email, reset_tok)
    # Always show success to prevent user enumeration
    return templates.TemplateResponse("forgot_password.html",
                                      {"request": request, "sent": True, "error": None})


@app.get("/reset-password", response_class=HTMLResponse)
def reset_password_page(request: Request, token: str = "", db: Session = Depends(get_db)):
    tenant = _find_tenant_by_token(db, "reset_token", token)
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
    tenant = _find_tenant_by_token(db, "reset_token", token)
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


@app.get("/logout", response_class=HTMLResponse)
def logout_confirm(request: Request):
    return templates.TemplateResponse("logout_confirm.html", {"request": request})


@app.post("/logout")
def logout_post(request: Request, csrf_token: str = Form(None)):
    validate_csrf(request, csrf_token)
    resp = RedirectResponse("/login", status_code=302)
    resp.delete_cookie("session")
    resp.delete_cookie("admin_session")
    return resp


# ---------------------------------------------------------------------------
# Team Member Login
# ---------------------------------------------------------------------------

@app.get("/team/login", response_class=HTMLResponse)
def team_login_page(request: Request, _=Depends(require_flag("TEAM_MEMBERS"))):
    return templates.TemplateResponse("login.html", {
        "request": request,
        "show_team_tab": True,
    })


@app.post("/team/login")
def team_login(request: Request,
               email: str = Form(...),
               password: str = Form(...),
               csrf_token: str = Form(None),
               db: Session = Depends(get_db),
               _=Depends(require_flag("TEAM_MEMBERS"))):
    validate_csrf(request, csrf_token)
    rate_limit(f"team-login:{client_ip(request)}", 10, 900)  # 10/15min per IP

    member = db.query(TeamMember).filter(
        TeamMember.email == email.lower(),
        TeamMember.is_active == True,
    ).first()

    if not member or not member.password_hash:
        raise HTTPException(status_code=401, detail="Invalid email or password")

    if not verify_password(password, member.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    version = member_session_version(member)
    token = create_member_token(member.id, member.tenant_id, member.role, version)

    is_sec = is_request_secure(request)
    resp = RedirectResponse("/dashboard", status_code=302)
    resp.set_cookie("session", token, httponly=True, samesite="strict",
                    secure=is_sec, max_age=72*3600)

    member.last_login_at = datetime.now(timezone.utc)
    db.add(ActivityLog(
        tenant_id=member.tenant_id,
        event_type="team_member_login",
        message=f"Team member {member.display_name} ({member.role}) logged in",
    ))
    db.commit()

    return resp


@app.post("/api/team/{member_id}/invite")
def send_team_invite(member_id: int, request: Request,
                     csrf_token: str = Form(None),
                     db: Session = Depends(get_db),
                     _=Depends(require_flag("TEAM_MEMBERS"))):
    try:
        tenant_id = get_current_tenant_id(request)
    except HTTPException:
        raise HTTPException(status_code=401, detail="Not authenticated")
    validate_csrf(request, csrf_token)

    member = db.query(TeamMember).filter_by(id=member_id, tenant_id=tenant_id).first()
    if not member:
        raise HTTPException(status_code=404, detail="Team member not found")

    # Generate invite token (48h TTL)
    invite_token = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(hours=48)

    member.invite_token = invite_token
    member.invite_token_expires_at = expires_at
    db.add(member)
    db.commit()

    # Send invite email
    base_url = os.getenv("APP_BASE_URL", str(request.base_url).rstrip("/"))
    invite_url = f"{base_url}/invite/{invite_token}"
    tenant = db.query(Tenant).filter_by(id=tenant_id).first()
    cfg = db.query(TenantConfig).filter_by(tenant_id=tenant_id).first()
    property_name = cfg.property_name if cfg else "your property"
    inviter_name = tenant.email if tenant else "Your manager"
    send_team_invite(member.email, invite_url, inviter_name, property_name)

    return RedirectResponse("/settings?msg=invite_sent&tab=team", status_code=302)


@app.get("/invite/{token}", response_class=HTMLResponse)
def invite_accept_page(token: str, request: Request, db: Session = Depends(get_db)):
    member = db.query(TeamMember).filter_by(invite_token=token).first()
    if not member or not member.invite_token_expires_at:
        raise HTTPException(status_code=404, detail="Invite not found or expired")

    if datetime.now(timezone.utc) > member.invite_token_expires_at:
        raise HTTPException(status_code=404, detail="Invite link has expired")

    return templates.TemplateResponse("invite_accept.html", {
        "request": request,
        "token": token,
        "member_name": member.display_name,
    })


@app.post("/invite/{token}")
def accept_invite(token: str, request: Request,
                  password: str = Form(...),
                  password_confirm: str = Form(...),
                  csrf_token: str = Form(None),
                  db: Session = Depends(get_db)):
    validate_csrf(request, csrf_token)

    if password != password_confirm:
        raise HTTPException(status_code=400, detail="Passwords do not match")
    if len(password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")

    member = db.query(TeamMember).filter_by(invite_token=token).first()
    if not member or not member.invite_token_expires_at:
        raise HTTPException(status_code=404, detail="Invite not found or expired")

    if datetime.now(timezone.utc) > member.invite_token_expires_at:
        raise HTTPException(status_code=404, detail="Invite link has expired")

    # Set password and clear invite
    member.password_hash = hash_password(password)
    member.invite_token = None
    member.invite_token_expires_at = None

    # Create session and log in
    version = member_session_version(member)
    token_jwt = create_member_token(member.id, member.tenant_id, member.role, version)

    is_sec = is_request_secure(request)
    resp = RedirectResponse("/dashboard", status_code=302)
    resp.set_cookie("session", token_jwt, httponly=True, samesite="strict",
                    secure=is_sec, max_age=72*3600)

    member.last_login_at = datetime.now(timezone.utc)
    db.add(ActivityLog(
        tenant_id=member.tenant_id,
        event_type="team_member_invite_accepted",
        message=f"Team member {member.display_name} accepted invite and set password",
    ))
    db.commit()

    return resp


# ---------------------------------------------------------------------------
# Conversation API
# ---------------------------------------------------------------------------

@app.get("/api/conversation/{thread_key}")
def api_conversation(thread_key: str, request: Request, db: Session = Depends(get_db), _=Depends(require_flag("CONVERSATION_VIEW"))):
    """Get all messages for a thread (both inbound and outbound, including auto-sent)."""
    try:
        tenant_id = get_current_tenant_id(request)
    except HTTPException:
        raise HTTPException(status_code=401, detail="Not authenticated")

    # Get all drafts for this thread
    all_drafts = db.query(Draft).filter(
        Draft.tenant_id == tenant_id,
        Draft.thread_key == thread_key,
    ).order_by(Draft.created_at).all()

    messages = []
    for d in all_drafts:
        # Inbound message (guest message)
        messages.append({
            "id": f"{d.id}-inbound",
            "direction": "inbound",
            "body": d.message,
            "channel": d.source,
            "timestamp": d.created_at.isoformat(),
            "status": d.status,
        })

        # Outbound message (host reply) - if approved, auto_sent, or escalation
        if d.status in ["approved", "auto_sent", "failed", "escalation"] and d.final_text:
            auto_sent_badge = "🤖 Auto" if d.status == "auto_sent" else ""
            messages.append({
                "id": f"{d.id}-outbound",
                "direction": "outbound",
                "body": d.final_text,
                "channel": d.source,
                "timestamp": d.created_at.isoformat(),
                "status": d.status,
                "badge": auto_sent_badge,
            })

    return {
        "thread_key": thread_key,
        "messages": messages,
    }


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request,
              db: Session = Depends(get_db),
              rdb: Session = Depends(get_read_db)):
    try:
        tenant_id = get_current_tenant_id(request)
    except HTTPException:
        return _redirect_login()

    tenant = _get_tenant(tenant_id, db)
    cfg = _get_or_create_config(tenant_id, db)
    selected_property = request.query_params.get("property", "").strip()
    draft_rows_all = (
        rdb.query(Draft)
        .filter_by(tenant_id=tenant_id)
        .order_by(Draft.created_at.desc())
        .limit(500)
        .all()
    )
    status = worker_manager.worker_status(tenant_id)
    now = datetime.now(timezone.utc)
    today = now.date()

    sync_log = db.query(ReservationSyncLog).filter_by(tenant_id=tenant_id).first()
    all_reservations = db.query(Reservation).filter_by(tenant_id=tenant_id).all()
    workflow_rules = db.query(AutomationRule).filter_by(tenant_id=tenant_id).order_by(AutomationRule.priority.asc()).all()
    team_members_all = db.query(TeamMember).filter_by(tenant_id=tenant_id).order_by(TeamMember.role.asc(), TeamMember.display_name.asc()).all()
    open_issues_all = (
        db.query(IssueTicket)
        .filter(IssueTicket.tenant_id == tenant_id, IssueTicket.status != "resolved")
        .order_by(IssueTicket.created_at.desc())
        .all()
    )
    timeline_events_all = (
        db.query(GuestTimelineEvent)
        .filter_by(tenant_id=tenant_id)
        .order_by(GuestTimelineEvent.created_at.desc())
        .limit(40)
        .all()
    )

    property_options = _collect_property_options(cfg, all_reservations, draft_rows_all, open_issues_all, team_members_all)
    if selected_property and selected_property not in property_options:
        selected_property = ""

    draft_rows = [draft for draft in draft_rows_all if _property_match(selected_property, _draft_property_name(draft))]
    pending = [draft for draft in draft_rows if draft.status == "pending"]
    recent_sent_drafts = [draft for draft in draft_rows if draft.status == "approved"][:12]
    filtered_reservations = [
        reservation for reservation in all_reservations
        if _property_match(selected_property, reservation.listing_name or "")
    ]
    team_members = [member for member in team_members_all if _team_member_matches_property(member, selected_property)]
    open_issues = [issue for issue in open_issues_all if _property_match(selected_property, issue.property_name or "")]
    timeline_events = [event for event in timeline_events_all if _property_match(selected_property, event.property_name or "")]

    month_start = today.replace(day=1)
    month_rows = [
        reservation for reservation in filtered_reservations
        if reservation.status == "confirmed" and reservation.checkin and reservation.checkin >= month_start
    ]
    month_revenue = sum(r.payout_usd or 0 for r in month_rows)
    month_nights = sum(r.nights or 0 for r in month_rows)
    occupancy_pct = round((month_nights / 30) * 100) if month_nights else 0
    upcoming_rows = [
        reservation for reservation in filtered_reservations
        if reservation.status == "confirmed" and reservation.checkin and reservation.checkin >= today
    ]
    upcoming_count = len(upcoming_rows)
    next_checkin = sorted(upcoming_rows, key=lambda row: row.checkin)[0] if upcoming_rows else None

    kpis = derive_dashboard_kpis(draft_rows, filtered_reservations, now=now)
    approval_streak = kpis["drafts"].get("approval_streak", 0)
    occupancy_gaps = kpis["reservations"].get("occupancy_gaps", [])
    review_velocity = compute_review_velocity(filtered_reservations)
    sentiment_summary = _sentiment_summary(draft_rows, filtered_reservations)
    activation_checklist = build_activation_checklist(
        cfg,
        reservations=filtered_reservations or all_reservations,
        inbound_email_address=_tenant_inbound_email_address(cfg),
        inbound_webhook_url=f"{APP_BASE_URL}/email/inbound",
    )
    exception_queue = surface_exception_queue(pending, filtered_reservations, now=now, stale_minutes=60, limit=8)
    recent_timeline = build_guest_timeline(reversed(timeline_events), limit=8)
    if not selected_property:
        try:
            _upsert_tenant_kpi_snapshot(db, tenant_id, kpis, open_issues, now)
        except Exception as exc:
            log.warning("[%s] KPI snapshot update failed: %s", tenant_id, exc)
            db.rollback()

    response_seconds = _average_response_seconds([draft for draft in draft_rows if draft.status == "approved"])
    response_peer_values = []
    review_peer_values = []
    for property_name in property_options:
        property_drafts = [
            draft for draft in draft_rows_all
            if _property_match(property_name, _draft_property_name(draft)) and draft.status == "approved"
        ]
        property_response = _average_response_seconds(property_drafts)
        if property_response is not None:
            response_peer_values.append(property_response)
        property_ratings = [
            float(reservation.review_rating)
            for reservation in all_reservations
            if _property_match(property_name, reservation.listing_name or "") and reservation.review_rating is not None
        ]
        if property_ratings:
            review_peer_values.append(round(sum(property_ratings) / len(property_ratings), 2))
    response_benchmark = compute_portfolio_benchmark(response_seconds, response_peer_values, lower_is_better=True)
    response_benchmark["hours"] = round(response_seconds / 3600.0, 2) if response_seconds is not None else None
    review_benchmark = compute_portfolio_benchmark(
        kpis["reservations"].get("avg_review_rating"),
        review_peer_values,
        lower_is_better=False,
    )

    # Stale CSV warning: > 12 hours since last upload
    csv_stale = False
    if sync_log:
        last = sync_log.last_synced
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        csv_stale = (datetime.now(timezone.utc) - last).total_seconds() > 43200

    # Group pending drafts into conversations by thread_key
    from collections import defaultdict
    conv_map = defaultdict(lambda: {
        "guest_name": "", "reply_to": "", "reservation": None,
        "thread_key": None, "drafts": [], "last_at": None,
    })
    res_by_id = {r.id: r for r in filtered_reservations}
    _dt_min = datetime.min.replace(tzinfo=timezone.utc)
    for d in sorted(pending, key=lambda x: x.created_at or _dt_min):
        key = d.thread_key or f"solo:{d.id}"
        c = conv_map[key]
        c["guest_name"] = d.guest_name
        c["reply_to"] = d.reply_to
        c["thread_key"] = d.thread_key
        c["last_at"] = d.created_at
        if d.reservation_id and not c["reservation"]:
            c["reservation"] = res_by_id.get(d.reservation_id)
        c["drafts"].append(d)
    conversations = sorted(conv_map.values(),
                          key=lambda c: c["last_at"] or _dt_min, reverse=True)

    # Show one-time tour overlay after onboarding completion (cookie-based)
    show_tour = request.cookies.get("show_tour") == "1"
    response  = templates.TemplateResponse("dashboard.html", {
        "request":       request,
        "tenant":        tenant,
        "cfg":           cfg,
        "drafts":        pending,
        "conversations": conversations,
        "recent_sent_drafts": recent_sent_drafts,
        "status":        status,
        "show_tour":     show_tour,
        "plan_info":     PLAN_INFO.get(cfg.subscription_plan or PLAN_FREE, PLAN_INFO[PLAN_FREE]),
        # Reservation analytics
        "sync_log":      sync_log,
        "csv_stale":     csv_stale,
        "month_revenue": month_revenue,
        "occupancy_pct": occupancy_pct,
        "upcoming_count": upcoming_count,
        "next_checkin":  next_checkin,
        "now":           now,
        "workflow_rules": workflow_rules,
        "team_members":   team_members,
        "open_issues":    open_issues,
        "recent_timeline": recent_timeline,
        "activation_checklist": activation_checklist,
        "exception_queue": exception_queue,
        "kpis":              kpis,
        "approval_streak":   approval_streak,
        "occupancy_gaps":    occupancy_gaps,
        "review_velocity":   review_velocity,
        "sentiment_summary": sentiment_summary,
        "response_benchmark": response_benchmark,
        "review_benchmark": review_benchmark,
        "selected_property": selected_property,
        "property_options": property_options,
        "active_arrivals": db.query(ArrivalActivation).filter(
            ArrivalActivation.tenant_id == tenant_id,
            ArrivalActivation.status.in_(["active", "pending"]),
        ).count(),
        "today":         today,
    })
    if show_tour:
        response.delete_cookie("show_tour")
    return response


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
    # Per-tenant rate limit: 120 draft actions/hour (prevents runaway Claude API spend)
    rate_limit(f"draft:{tenant_id}", max_requests=120, window_seconds=3600)

    draft = db.query(Draft).filter_by(id=draft_id, tenant_id=tenant_id).first()
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")

    _execute_draft(draft, draft.draft, tenant_id, db)
    redirect_to = "/dashboard"
    selected_property = request.query_params.get("property", "").strip()
    if selected_property:
        redirect_to += f"?property={selected_property}"
    return RedirectResponse(redirect_to, status_code=302)


@app.post("/drafts/{draft_id}/edit")
def edit_draft(draft_id: str, request: Request, edited_text: str = Form(...),
               csrf_token: str = Form(None),
               db: Session = Depends(get_db)):
    try:
        tenant_id = get_current_tenant_id(request)
    except HTTPException:
        return _redirect_login()
    validate_csrf(request, csrf_token)
    rate_limit(f"draft:{tenant_id}", max_requests=120, window_seconds=3600)

    draft = db.query(Draft).filter_by(id=draft_id, tenant_id=tenant_id).first()
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")

    _execute_draft(draft, edited_text.strip(), tenant_id, db)
    redirect_to = "/dashboard"
    selected_property = request.query_params.get("property", "").strip()
    if selected_property:
        redirect_to += f"?property={selected_property}"
    return RedirectResponse(redirect_to, status_code=302)


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
    redirect_to = "/dashboard"
    selected_property = request.query_params.get("property", "").strip()
    if selected_property:
        redirect_to += f"?property={selected_property}"
    return RedirectResponse(redirect_to, status_code=302)


@app.post("/drafts/{draft_id}/feedback")
def draft_feedback(
    draft_id: str,
    request: Request,
    score: str = Form(...),
    note: str = Form(""),
    csrf_token: str = Form(None),
    db: Session = Depends(get_db),
):
    try:
        tenant_id = get_current_tenant_id(request)
    except HTTPException:
        return _redirect_login()
    validate_csrf(request, csrf_token)

    draft = db.query(Draft).filter_by(id=draft_id, tenant_id=tenant_id).first()
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")

    feedback_score = 1.0 if str(score).strip() in {"1", "up", "positive"} else -1.0
    draft.host_feedback_score = feedback_score
    draft.host_feedback_note = note.strip() or None
    draft.host_feedback_at = datetime.now(timezone.utc)

    if draft.reservation_id:
        reservation = db.query(Reservation).filter_by(id=draft.reservation_id, tenant_id=tenant_id).first()
        if reservation:
            if feedback_score > 0:
                reservation.guest_feedback_positive = (reservation.guest_feedback_positive or 0) + 1
            else:
                reservation.guest_feedback_negative = (reservation.guest_feedback_negative or 0) + 1
            total_feedback = (reservation.guest_feedback_positive or 0) + (reservation.guest_feedback_negative or 0)
            if total_feedback:
                reservation.guest_satisfaction_score = round(
                    ((reservation.guest_feedback_positive or 0) - (reservation.guest_feedback_negative or 0)) / total_feedback,
                    2,
                )
            _record_timeline_event(
                db,
                tenant_id,
                reservation,
                "draft_feedback_recorded",
                f"Host marked reply as {'positive' if feedback_score > 0 else 'negative'}",
                channel=_draft_channel(draft),
                draft=draft,
                body=note.strip(),
                payload_json={"score": feedback_score},
            )

    db.add(ActivityLog(
        tenant_id=tenant_id,
        event_type="draft_feedback_recorded",
        message=f"Draft feedback captured for {draft.guest_name}: {feedback_score:+.0f}",
    ))
    db.commit()
    redirect_to = "/dashboard"
    selected_property = request.query_params.get("property", "").strip()
    if selected_property:
        redirect_to += f"?property={selected_property}"
    return RedirectResponse(redirect_to, status_code=302)


def _execute_draft(
    draft: Draft,
    final_text: str,
    tenant_id: str,
    db: Session,
    *,
    reservation: Optional[Reservation] = None,
    automation_rule: Optional[AutomationRule] = None,
):
    """Send reply via the appropriate channel and mark draft approved."""
    if draft.status != "pending":
        return
    cfg = db.query(TenantConfig).filter_by(tenant_id=tenant_id).first()
    reservation = reservation or (
        db.query(Reservation).filter_by(id=draft.reservation_id, tenant_id=tenant_id).first()
        if draft.reservation_id else None
    )

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
                    log.warning("[%s] Meta WA send failed for ***%s", tenant_id, guest_phone[-4:] if guest_phone else "")
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
                    log.warning("[%s] Twilio SMS send failed for ***%s", tenant_id, guest_phone[-4:] if guest_phone else "")

    elif draft.source == "pms" and draft.reply_to:
        # reply_to format: "{integration_id}:{reservation_id}"
        parts = draft.reply_to.split(":", 1)
        if len(parts) == 2:
            try:
                from web.models import PMSIntegration
                from web.pms_base import make_adapter
                integration = db.query(PMSIntegration).filter_by(
                    id=int(parts[0]), tenant_id=tenant_id, is_active=True
                ).first()
                if integration:
                    adapter = make_adapter(
                        integration.pms_type,
                        decrypt(integration.api_key_enc),
                        integration.account_id or "",
                        integration.api_base_url or "",
                    )
                    ok = adapter.send_message(parts[1], final_text)
                    if not ok:
                        log.warning("[%s] PMS reply send failed for reservation %s",
                                    tenant_id, parts[1])
                    else:
                        log.info("[%s] PMS reply sent via %s for reservation %s",
                                 tenant_id, integration.pms_type, parts[1])
                else:
                    log.warning("[%s] PMS integration %s not found or inactive", tenant_id, parts[0])
            except Exception as exc:
                log.error("[%s] PMS reply error: %s", tenant_id, exc)

    draft.status      = "approved"
    draft.final_text  = final_text
    draft.approved_at = datetime.now(timezone.utc)
    if reservation:
        reservation.last_host_reply_at = draft.approved_at
    db.add(ActivityLog(tenant_id=tenant_id, event_type="draft_approved",
                       message=f"Draft approved: {draft.guest_name}"))
    _record_timeline_event(
        db,
        tenant_id,
        reservation,
        "draft_approved",
        f"Reply sent for {draft.guest_name}",
        channel=_draft_channel(draft),
        direction="outbound",
        body=final_text,
        draft=draft,
        automation_rule=automation_rule,
    )
    db.commit()


# ---------------------------------------------------------------------------
# Baileys outbound queue
# Priority: Redis (fast, shared across workers) → DB (durable, survives restarts)
# Redis TTL is 48h — survives any plausible server downtime.
# DB rows are written alongside Redis; popping marks them delivered=True
# so we have a permanent audit trail and zero message loss even on Redis failure.
# ---------------------------------------------------------------------------

def _queue_baileys_outbound(tenant_id: str, to_phone: str, text: str, db: Session):
    """Queue a Baileys outbound message with validation and idempotency. (Improvements #5, #6, #10)"""
    from web.redis_client import get_redis

    # Validate phone number (Improvement #5)
    if not _validate_phone_number(to_phone):
        log.error("[%s] Invalid phone number: ***%s", tenant_id, to_phone[-4:] if to_phone else "")
        return

    r = get_redis()
    # Generate idempotency key to prevent duplicate sends — guaranteed unique via uuid (Baileys fix #3)
    idempotency_key = f"{tenant_id}:{to_phone}:{uuid4().hex}"

    # Always persist to DB first — durable audit trail regardless of Redis (Improvement #4)
    try:
        row = BaileysOutbound(
            tenant_id=tenant_id,
            to_phone=to_phone,
            text=text,
            status="pending",  # Improvement #4 (two-phase commit)
            idempotency_key=idempotency_key
        )
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
            log.info("[%s] Queued Baileys outbound (Redis+DB) to ***%s", tenant_id, to_phone[-4:] if to_phone else "")
            return
        except Exception as exc:
            log.warning("[%s] Redis push failed — will serve from DB on next poll: %s", tenant_id, exc)
    log.info("[%s] Queued Baileys outbound (DB-only) to ***%s", tenant_id, to_phone[-4:] if to_phone else "")


def _pop_baileys_outbound(tenant_id: str, db: Session) -> dict:
    """
    Return pending outbound messages with rate limiting and bandwidth control.
    Implements two-phase commit (pending → in_transit → delivered).
    (Improvements #1, #2, #4, #10)
    """
    from web.redis_client import get_redis

    # Get tenant config for rate limiting settings
    cfg = db.query(TenantConfig).filter_by(tenant_id=tenant_id).first()
    if not cfg:
        return {"messages": [], "batch_id": "", "remaining_quota": 0, "pending_count": 0}

    max_batch_size = cfg.baileys_max_batch_size or 50
    max_per_minute = cfg.baileys_max_per_minute or 60

    r = get_redis()
    msgs: list[dict] = []
    db_ids: list[int] = []
    batch_id = secrets.token_hex(16)  # Unique batch identifier

    # Try Redis first
    if r is not None:
        try:
            key = f"baileys_out:{tenant_id}"
            pipe = r.pipeline()
            pipe.lrange(key, 0, max_batch_size - 1)
            pipe.ltrim(key, max_batch_size, -1)
            results, _ = pipe.execute()
            for raw in results:
                item = json.loads(raw)
                msgs.append({"to": item["to"], "text": item["text"]})
                if item.get("db_id"):
                    db_ids.append(item["db_id"])
        except Exception as exc:
            log.warning("[%s] Redis pop failed, falling back to DB: %s", tenant_id, exc)

    # Fallback: if Redis returned nothing, check DB for pending rows
    if not msgs:
        rows = (db.query(BaileysOutbound)
                .filter_by(tenant_id=tenant_id, status="pending")
                .order_by(BaileysOutbound.created_at)
                .limit(max_batch_size)
                .all())
        for row in rows:
            msgs.append({"to": row.to_phone, "text": row.text})
            db_ids.append(row.id)

    # Calculate remaining quota based on messages delivered in last 60 seconds (Improvement #10)
    now = datetime.now(timezone.utc)
    delivered_in_last_min = db.query(BaileysOutbound).filter(
        BaileysOutbound.tenant_id == tenant_id,
        BaileysOutbound.status == "delivered",
        BaileysOutbound.delivered_at >= now - timedelta(seconds=60)
    ).count()

    remaining_quota = max(0, max_per_minute - delivered_in_last_min)

    # Limit batch size to respect remaining quota (smooth delivery over ~6 polls)
    effective_batch_size = min(len(msgs), max(1, remaining_quota // 6))
    msgs = msgs[:effective_batch_size]
    db_ids = db_ids[:effective_batch_size]

    # Two-phase commit: mark as in_transit (not delivered yet) (Improvement #4)
    if db_ids:
        try:
            (db.query(BaileysOutbound)
             .filter(BaileysOutbound.id.in_(db_ids))
             .update({"status": "in_transit", "delivered": False},
                     synchronize_session=False))
            db.commit()
        except Exception as exc:
            log.warning("[%s] Failed to mark Baileys rows in_transit: %s", tenant_id, exc)
            db.rollback()

    # Count total pending messages for response
    pending_count = db.query(BaileysOutbound).filter_by(
        tenant_id=tenant_id, status="pending"
    ).count()

    return {
        "messages": msgs,
        "batch_id": batch_id,
        "remaining_quota": remaining_quota,
        "pending_count": pending_count
    }


def _normalize_phone(phone: str) -> str:
    """Normalize phone number for comparison (remove spaces, dashes, etc.)."""
    return ''.join(c for c in phone if c.isdigit())


def _handle_host_command(tenant_id: str, command: str, db: Session):
    """Process management commands from the host via WhatsApp."""
    cfg = db.query(TenantConfig).filter_by(tenant_id=tenant_id).first()
    if not cfg:
        return

    cmd_lower = command.lower().strip()
    parts = cmd_lower.split()

    if not parts:
        return

    action = parts[0]

    # Normalize action to full command (support aliases)
    action_map = {
        "p": "pending",
        "a": "approve",
        "s": "skip",
        "sh": "show",
        "ed": "edit",
        "h": "help",
        "?": "help",
    }
    if action in action_map:
        action = action_map[action]

    # Command: pending / list — show pending drafts (exclude auto-sent)
    if action in ["pending", "list"]:
        pending_drafts = db.query(Draft).filter(
            Draft.tenant_id == tenant_id,
            Draft.status == "pending"
        ).order_by(Draft.created_at.desc()).limit(5).all()

        auto_sent_count = db.query(Draft).filter(
            Draft.tenant_id == tenant_id,
            Draft.status == "auto_sent",
            Draft.created_at >= datetime.now(timezone.utc) - timedelta(hours=24)
        ).count()

        if not pending_drafts:
            response = f"✓ All caught up!\n\n{auto_sent_count} auto-sent today\n\nType 'h' for help"
        else:
            lines = [f"📋 {len(pending_drafts)} pending (need you):\n"]
            for i, d in enumerate(pending_drafts, 1):
                msg_preview = d.message[:30].replace('\n', ' ')
                confidence_icon = "✓" if d.confidence >= 0.9 else "⚠️" if d.confidence >= 0.7 else "❓"
                lines.append(f"{i}. {confidence_icon} {d.guest_name}: {msg_preview}")
            lines.append(f"\n{auto_sent_count} auto-sent today")
            lines.append("\nQuick: a 1  s 1  sh 1  ed 1: text")
            lines.append("Help: h")
            response = "\n".join(lines)

        _queue_baileys_outbound(tenant_id, cfg.whatsapp_number, response, db)
        log.info(f"[{tenant_id}] Host list command processed")

    # Command: approve <index_or_id> — approve a draft by index (1, 2, 3) or full ID
    elif action == "approve" and len(parts) > 1:
        identifier = parts[1]

        # Try to parse as index number (1, 2, 3)
        draft = None
        try:
            index = int(identifier)
            pending_drafts = db.query(Draft).filter(
                Draft.tenant_id == tenant_id,
                Draft.status == "pending"
            ).order_by(Draft.created_at.desc()).all()

            if 1 <= index <= len(pending_drafts):
                draft = pending_drafts[index - 1]
        except ValueError:
            # Not a number, try as draft ID
            draft = db.query(Draft).filter_by(id=identifier, tenant_id=tenant_id).first()

        if not draft:
            _queue_baileys_outbound(tenant_id, cfg.whatsapp_number, f"❌ Draft #{identifier} not found", db)
            return

        if draft.status != "pending":
            _queue_baileys_outbound(tenant_id, cfg.whatsapp_number, f"❌ Already {draft.status}: {draft.guest_name}", db)
            return

        # Approve and send the draft
        draft.status = "approved"
        draft.final_text = draft.draft
        draft.updated_at = datetime.now(timezone.utc)

        # Queue the response to guest
        _queue_baileys_outbound(tenant_id, draft.reply_to, draft.final_text, db)

        # Log timeline event
        from web.models import Reservation
        reservation = db.query(Reservation).filter_by(id=draft.reservation_id).first() if draft.reservation_id else None
        _record_timeline_event(
            db,
            tenant_id,
            reservation,
            "draft_approved",
            f"Host approved response to {draft.guest_name}",
            channel="whatsapp_command",
            direction="outbound",
            body=draft.final_text,
            draft=draft,
        )

        db.add(ActivityLog(
            tenant_id=tenant_id,
            event_type="draft_approved",
            message=f"Host approved: {draft.guest_name} - {draft.final_text[:60]}"
        ))
        db.commit()

        _queue_baileys_outbound(tenant_id, cfg.whatsapp_number, f"✓ Sent to {draft.guest_name}", db)
        log.info(f"[{tenant_id}] Host approved: {draft.guest_name}")

    # Command: skip <index_or_id> — skip a draft by index (1, 2, 3) or full ID
    elif action == "skip" and len(parts) > 1:
        identifier = parts[1]

        # Try to parse as index number (1, 2, 3)
        draft = None
        try:
            index = int(identifier)
            pending_drafts = db.query(Draft).filter(
                Draft.tenant_id == tenant_id,
                Draft.status == "pending"
            ).order_by(Draft.created_at.desc()).all()

            if 1 <= index <= len(pending_drafts):
                draft = pending_drafts[index - 1]
        except ValueError:
            # Not a number, try as draft ID
            draft = db.query(Draft).filter_by(id=identifier, tenant_id=tenant_id).first()

        if not draft:
            _queue_baileys_outbound(tenant_id, cfg.whatsapp_number, f"❌ Draft #{identifier} not found", db)
            return

        draft.status = "skipped"
        draft.updated_at = datetime.now(timezone.utc)

        db.add(ActivityLog(
            tenant_id=tenant_id,
            event_type="draft_skipped",
            message=f"Host skipped: {draft.guest_name}"
        ))
        db.commit()

        _queue_baileys_outbound(tenant_id, cfg.whatsapp_number, f"✓ Skipped {draft.guest_name}'s message", db)
        log.info(f"[{tenant_id}] Host skipped: {draft.guest_name}")

    # Command: show <index> — preview draft before approving
    elif action == "show" and len(parts) > 1:
        identifier = parts[1]

        # Try to parse as index or ID
        draft = None
        try:
            index = int(identifier)
            pending_drafts = db.query(Draft).filter(
                Draft.tenant_id == tenant_id,
                Draft.status == "pending"
            ).order_by(Draft.created_at.desc()).all()

            if 1 <= index <= len(pending_drafts):
                draft = pending_drafts[index - 1]
        except ValueError:
            draft = db.query(Draft).filter_by(id=identifier, tenant_id=tenant_id).first()

        if not draft:
            _queue_baileys_outbound(tenant_id, cfg.whatsapp_number, f"❌ Draft #{identifier} not found", db)
            return

        # Show the draft
        preview = f"""📋 Draft for {draft.guest_name}:

"{draft.draft}"

Status: {draft.status}
Confidence: {draft.confidence:.0%}

Reply: approve {identifier} or skip {identifier}"""
        _queue_baileys_outbound(tenant_id, cfg.whatsapp_number, preview, db)
        log.info(f"[{tenant_id}] Host viewed draft preview")

    # Command: edit <index> <new_text> — edit draft before sending
    elif action == "edit" and len(parts) > 2:
        identifier = parts[1]
        # Reconstruct the new text (everything after "edit <id>")
        new_text = " ".join(parts[2:])

        # Try to parse as index or ID
        draft = None
        try:
            index = int(identifier)
            pending_drafts = db.query(Draft).filter(
                Draft.tenant_id == tenant_id,
                Draft.status == "pending"
            ).order_by(Draft.created_at.desc()).all()

            if 1 <= index <= len(pending_drafts):
                draft = pending_drafts[index - 1]
        except ValueError:
            draft = db.query(Draft).filter_by(id=identifier, tenant_id=tenant_id).first()

        if not draft:
            _queue_baileys_outbound(tenant_id, cfg.whatsapp_number, f"❌ Draft #{identifier} not found", db)
            return

        if draft.status != "pending":
            _queue_baileys_outbound(tenant_id, cfg.whatsapp_number, f"❌ Can't edit {draft.status} draft", db)
            return

        # Update the draft
        draft.draft = new_text
        draft.updated_at = datetime.now(timezone.utc)
        db.commit()

        response = f"""✏️ Draft updated:

"{new_text}"

Reply: approve {identifier} to send"""
        _queue_baileys_outbound(tenant_id, cfg.whatsapp_number, response, db)
        log.info(f"[{tenant_id}] Host edited draft")

    # Command: help / ? — show available commands
    elif action in ["help"]:
        help_text = """🤖 HostAI Manager

⚡ Quick Commands:
p — pending list
a 1 — approve draft #1
s 1 — skip draft #1
sh 1 — show draft #1
ed 1: new text — edit draft #1
h — this help
stats — show today's stats

Example:
p
(sees pending drafts)
a 1
(approves first one)"""
        _queue_baileys_outbound(tenant_id, cfg.whatsapp_number, help_text, db)

    else:
        # Unknown command
        _queue_baileys_outbound(tenant_id, cfg.whatsapp_number, "❓ Unknown command. Type 'h' or 'help'", db)


def _send_host_notification(tenant_id: str, notify_phone: str, text: str, guest_name: str, guest_message: str, channel: str, db: Session):
    """Send multi-channel notification to the host when a guest messages the bot."""
    cfg = db.query(TenantConfig).filter_by(tenant_id=tenant_id).first()
    if not cfg:
        return

    try:
        # WhatsApp notification (to host's own number)
        if cfg.wa_mode == "baileys" and cfg.whatsapp_number:
            _queue_baileys_outbound(tenant_id, notify_phone, text, db)
            log.info(f"[{tenant_id}] Host notification queued via Baileys WhatsApp")
        elif cfg.wa_mode == "meta_cloud":
            from web.meta_sender import send_whatsapp
            from web.crypto import decrypt

            phone_id = cfg.whatsapp_phone_id
            token = decrypt(cfg.whatsapp_token_enc) if cfg.whatsapp_token_enc else None

            if phone_id and token:
                if send_whatsapp(phone_id, token, notify_phone, text):
                    log.info(f"[{tenant_id}] Host notification sent via Meta Cloud API")
                else:
                    log.warning(f"[{tenant_id}] Host notification failed via Meta")

        # Email notification (if configured)
        try:
            tenant = db.query(Tenant).filter_by(id=tenant_id).first()
            if tenant and tenant.email:
                from web.mailer import send_guest_message_alert
                send_guest_message_alert(tenant.email, guest_name, guest_message[:500], channel)
                log.info(f"[{tenant_id}] Host notification sent via email")
        except Exception as e:
            log.warning(f"[{tenant_id}] Failed to send email notification: {e}")

        # SMS notification (if configured)
        if cfg.sms_notify_number and cfg.sms_mode == "twilio":
            try:
                from web.sms_sender import send_sms
                sms_text = f"📩 {guest_name}: {guest_message[:80]}..."
                send_sms(
                    cfg.twilio_account_sid,
                    cfg.twilio_auth_token_enc,
                    cfg.twilio_from_number,
                    cfg.sms_notify_number,
                    sms_text,
                )
                log.info(f"[{tenant_id}] Host notification sent via SMS")
            except Exception as e:
                log.warning(f"[{tenant_id}] Failed to send SMS notification: {e}")

    except Exception as e:
        log.error(f"[{tenant_id}] Error sending host notification: {e}")


# ---------------------------------------------------------------------------
# Onboarding wizard
# ---------------------------------------------------------------------------

_ONBOARDING_STEPS = 5

def _onboarding_redirect(step: int):
    return RedirectResponse(f"/onboarding?step={step}", status_code=302)


def _recommended_house_rules(cfg: TenantConfig) -> str:
    checkout = cfg.check_out_time or "11:00 AM"
    return "\n".join([
        "No parties or events.",
        "No smoking inside the property.",
        "Quiet hours are 10:00 PM to 8:00 AM.",
        f"Standard checkout is by {checkout}.",
        "If guests need an exception, the bot should say it will confirm with the host.",
    ])


def _recommended_faq(cfg: TenantConfig) -> str:
    property_name = (cfg.property_names or "the property").split(",")[0].strip()
    checkin = cfg.check_in_time or "3:00 PM"
    checkout = cfg.check_out_time or "11:00 AM"
    return "\n\n".join([
        f"Q: What time is check-in?\nA: Standard check-in for {property_name} starts at {checkin}. If you need early access, ask and the host will confirm if the room is ready.",
        f"Q: What time is check-out?\nA: Standard check-out is by {checkout}. Late checkout is never promised automatically; the host must confirm it.",
        "Q: What if something is not working?\nA: The guest should describe the issue and the room or unit. HostAI should reassure the guest, open an issue if needed, and escalate urgent problems.",
        "Q: Can the guest ask for Wi-Fi, parking, towels, directions, and local recommendations?\nA: Yes. HostAI should answer directly when the information exists in the property context, FAQ, or reservation timeline.",
    ])


def _recommended_custom_instructions() -> str:
    return "\n".join([
        "Be warm, concise, and practical.",
        "Use the guest's stay context, room number, and reservation details whenever available.",
        "Never promise refunds, late checkout, or policy exceptions without host confirmation.",
        "If the guest reports a maintenance, safety, billing, or complaint issue, move into escalation-aware behavior.",
    ])


def _ensure_effortless_defaults(tenant: Tenant, cfg: TenantConfig, db: Session) -> None:
    if not cfg.email_ingest_mode or cfg.email_ingest_mode == "imap":
        cfg.email_ingest_mode = "forwarding"
    if not cfg.check_in_time:
        cfg.check_in_time = "3:00 PM"
    if not cfg.check_out_time:
        cfg.check_out_time = "11:00 AM"
    if not cfg.house_rules:
        cfg.house_rules = _recommended_house_rules(cfg)
    if not cfg.faq:
        cfg.faq = _recommended_faq(cfg)
    if not cfg.custom_instructions:
        cfg.custom_instructions = _recommended_custom_instructions()
    if not cfg.escalation_email:
        cfg.escalation_email = tenant.email

    owner = (
        db.query(TeamMember)
        .filter_by(tenant_id=tenant.id, email=tenant.email)
        .first()
    )
    if not owner:
        db.add(TeamMember(
            tenant_id=tenant.id,
            display_name=(tenant.email.split("@")[0].replace(".", " ").replace("_", " ").title() or "Owner"),
            email=tenant.email,
            role="owner",
        ))

    existing_rules = db.query(AutomationRule).filter_by(tenant_id=tenant.id).count()
    if existing_rules == 0:
        db.add_all([
            AutomationRule(
                tenant_id=tenant.id,
                name="Auto-send routine stay questions",
                channel="any",
                priority=10,
                confidence_threshold=0.88,
                conditions_json={"msg_types": ["routine"]},
                actions_json={"mode": "auto_send"},
            ),
            AutomationRule(
                tenant_id=tenant.id,
                name="Review complex guest requests",
                channel="any",
                priority=20,
                confidence_threshold=0.45,
                conditions_json={"msg_types": ["complex"], "allow_complex": True},
                actions_json={"mode": "review"},
            ),
            AutomationRule(
                tenant_id=tenant.id,
                name="Escalate maintenance, safety, and complaint language",
                channel="any",
                priority=5,
                confidence_threshold=0.0,
                conditions_json={
                    "allow_keywords": [
                        "refund", "broken", "not working", "leak", "unsafe",
                        "emergency", "complaint", "angry", "dirty",
                    ]
                },
                actions_json={"mode": "escalate"},
            ),
        ])


@app.get("/onboarding", response_class=HTMLResponse)
def onboarding_get(request: Request, step: int = None, db: Session = Depends(get_db)):
    try:
        tenant_id = get_current_tenant_id(request)
    except HTTPException:
        return _redirect_login()
    tenant = _get_tenant(tenant_id, db)
    cfg    = _get_or_create_config(tenant_id, db)
    if not cfg.inbound_email_alias:
        _ensure_inbound_email_alias(tenant, cfg, db)
        db.commit()
    if cfg.onboarding_complete and step is None:
        return RedirectResponse("/dashboard", status_code=302)
    current_step = step if step is not None else max(cfg.onboarding_step + 1, 1)
    current_step = max(1, min(current_step, 6))
    reservations = db.query(Reservation).filter_by(tenant_id=tenant_id).all()
    return templates.TemplateResponse("onboarding.html", {
        "request": request,
        "tenant":  tenant,
        "cfg":     cfg,
        "step":    current_step,
        "saved":   False,
        "inbound_email_address": _tenant_inbound_email_address(cfg),
        "activation_checklist": build_activation_checklist(
            cfg,
            reservations=reservations,
            inbound_email_address=_tenant_inbound_email_address(cfg),
            inbound_webhook_url=f"{APP_BASE_URL}/email/inbound",
        ),
    })


@app.post("/onboarding/quick-start")
def onboarding_quick_start(
    request: Request,
    property_names: str = Form(""),
    property_city: str = Form(""),
    check_in_time: str = Form(""),
    check_out_time: str = Form(""),
    csrf_token: str = Form(None),
    db: Session = Depends(get_db),
):
    try:
        tenant_id = get_current_tenant_id(request)
    except HTTPException:
        return _redirect_login()
    validate_csrf(request, csrf_token)

    tenant = _get_tenant(tenant_id, db)
    cfg = _get_or_create_config(tenant_id, db)
    _ensure_inbound_email_alias(tenant, cfg, db)

    if property_names.strip():
        cfg.property_names = property_names.strip()
    if property_city.strip():
        cfg.property_city = property_city.strip()
    if check_in_time.strip():
        cfg.check_in_time = check_in_time.strip()
    if check_out_time.strip():
        cfg.check_out_time = check_out_time.strip()

    _ensure_effortless_defaults(tenant, cfg, db)
    cfg.onboarding_step = max(cfg.onboarding_step, 4)
    db.add(ActivityLog(
        tenant_id=tenant_id,
        event_type="onboarding_quick_start",
        message="Recommended quick-start defaults applied",
    ))
    db.commit()
    return RedirectResponse("/onboarding?step=5", status_code=302)


@app.post("/onboarding", response_class=HTMLResponse)
async def onboarding_post(
    request:     Request,
    step:        int  = Form(...),
    skip:        str  = Form(""),
    csrf_token:  str  = Form(None),
    db: Session = Depends(get_db),
    # Step 1 fields
    property_names: str = Form(""),
    property_type:  str = Form(""),
    property_city:  str = Form(""),
    check_in_time:  str = Form(""),
    check_out_time: str = Form(""),
    max_guests:     str = Form(""),
    # Step 2 fields
    house_rules:    str = Form(""),
    amenities:      list = Form([]),
    quiet_hours:    str = Form(""),
    pet_policy:     str = Form(""),
    refund_policy:  str = Form(""),
    early_checkin_policy: str = Form(""),
    early_checkin_fee: str = Form(""),
    late_checkout_policy: str = Form(""),
    late_checkout_fee: str = Form(""),
    parking_policy: str = Form(""),
    smoking_policy: str = Form(""),
    # Step 3 fields
    food_menu:           str = Form(""),
    menu_pdf:            UploadFile = File(None),
    breakfast_included:  str = Form(""),
    nearby_restaurants:  str = Form(""),
    extra_services:      list = Form([]),
    # Step 4 fields
    faq:                 str = Form(""),
    emergency_contacts:  str = Form(""),
    custom_instructions: str = Form(""),
    escalation_email:    str = Form(""),
    # Step 5 fields
    ical_urls:           str = Form(""),
    email_ingest_mode:   str = Form("imap"),
    imap_host:           str = Form(""),
    smtp_host:           str = Form(""),
    email_address:       str = Form(""),
    email_password:      str = Form(""),
):
    try:
        tenant_id = get_current_tenant_id(request)
    except HTTPException:
        return _redirect_login()
    validate_csrf(request, csrf_token)

    tenant = _get_tenant(tenant_id, db)
    cfg    = _get_or_create_config(tenant_id, db)
    _ensure_inbound_email_alias(tenant, cfg, db)

    if not skip:
        if step == 1:
            cfg.property_names = property_names.strip() or cfg.property_names
            cfg.property_type  = property_type.strip() or None
            cfg.property_city  = property_city.strip() or None
            cfg.check_in_time  = check_in_time.strip() or None
            cfg.check_out_time = check_out_time.strip() or None
            cfg.max_guests     = int(max_guests) if max_guests.strip().isdigit() else cfg.max_guests

        elif step == 2:
            # Merge quiet hours and pet policy into house rules
            extra_rules = ""
            if quiet_hours.strip():
                extra_rules += f"\nQuiet hours: {quiet_hours.strip()}"
            if pet_policy.strip():
                extra_rules += f"\nPet policy: {pet_policy.strip()}"
            cfg.house_rules = (house_rules.strip() + extra_rules).strip() or cfg.house_rules
            cfg.quiet_hours = quiet_hours.strip() or cfg.quiet_hours
            cfg.pet_policy = pet_policy.strip() or cfg.pet_policy
            cfg.refund_policy = refund_policy.strip() or cfg.refund_policy
            cfg.early_checkin_policy = early_checkin_policy.strip() or cfg.early_checkin_policy
            cfg.early_checkin_fee = early_checkin_fee.strip() or cfg.early_checkin_fee
            cfg.late_checkout_policy = late_checkout_policy.strip() or cfg.late_checkout_policy
            cfg.late_checkout_fee = late_checkout_fee.strip() or cfg.late_checkout_fee
            cfg.parking_policy = parking_policy.strip() or cfg.parking_policy
            cfg.smoking_policy = smoking_policy.strip() or cfg.smoking_policy
            cfg.amenities   = ", ".join(amenities) if amenities else cfg.amenities

        elif step == 3:
            # PDF extraction takes priority over pasted text
            extracted = ""
            if menu_pdf and menu_pdf.filename:
                try:
                    import io
                    import pdfplumber
                    pdf_bytes = await menu_pdf.read(10 * 1024 * 1024 + 1)
                    if len(pdf_bytes) > 10 * 1024 * 1024:
                        return RedirectResponse(f"/onboarding?step={step}&error=file_too_large", status_code=302)
                    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
                        extracted = "\n".join(
                            page.extract_text() or "" for page in pdf.pages
                        ).strip()
                except Exception as exc:
                    log.warning("[%s] PDF extraction failed: %s", tenant_id, exc)
            cfg.food_menu          = extracted or food_menu.strip() or cfg.food_menu
            cfg.nearby_restaurants = nearby_restaurants.strip() or cfg.nearby_restaurants
            # Append breakfast and extra services to food_menu context
            if breakfast_included.strip():
                cfg.food_menu = (cfg.food_menu or "") + f"\n\nBreakfast: {breakfast_included.strip()}"
            if extra_services:
                cfg.food_menu = (cfg.food_menu or "") + f"\n\nAdditional services: {', '.join(extra_services)}"
            # Save extra_services to its own column for re-population on revisit
            cfg.extra_services = ",".join(extra_services) if extra_services else ""

        elif step == 4:
            combined_faq = faq.strip()
            if emergency_contacts.strip():
                combined_faq = (combined_faq + "\n\nEmergency contacts:\n" + emergency_contacts.strip()).strip()
            cfg.faq                 = combined_faq or cfg.faq
            cfg.custom_instructions = custom_instructions.strip() or cfg.custom_instructions
            cfg.escalation_email    = escalation_email.strip() or cfg.escalation_email

        elif step == 5:
            cfg.email_ingest_mode = email_ingest_mode.strip() or cfg.email_ingest_mode or "imap"
            cfg.ical_urls     = ical_urls.strip() or cfg.ical_urls
            cfg.imap_host     = imap_host.strip() or cfg.imap_host
            cfg.smtp_host     = smtp_host.strip() or cfg.smtp_host
            cfg.email_address = email_address.strip() or cfg.email_address
            if email_password.strip():
                cfg.email_password_enc = encrypt(email_password.strip())

    cfg.onboarding_step = step
    _ensure_effortless_defaults(tenant, cfg, db)
    db.commit()

    next_step = step + 1
    if next_step > _ONBOARDING_STEPS:
        # Onboarding complete
        cfg.onboarding_complete = True
        _ensure_effortless_defaults(tenant, cfg, db)
        db.commit()
        worker_manager.restart_worker(tenant_id)
        # Send welcome email
        try:
            send_welcome_email(tenant.email, cfg.property_names or "")
        except Exception as exc:
            log.warning("[%s] Welcome email failed: %s", tenant_id, exc)
        # Set cookie so dashboard shows one-time tour
        resp = RedirectResponse("/onboarding?step=6", status_code=302)
        resp.set_cookie(
            "show_tour",
            "1",
            max_age=300,
            httponly=True,
            samesite="lax",
            secure=is_request_secure(request),
        )
        return resp

    return _onboarding_redirect(next_step)


@app.post("/onboarding/dismiss-tour")
async def dismiss_tour(request: Request):
    """Called by JS after the tour overlay is dismissed — no-op (cookie already deleted by dashboard)."""
    return JSONResponse({"ok": True})


@app.post("/onboarding/demo")
async def onboarding_demo(request: Request, db: Session = Depends(get_db)):
    """Generate a live demo draft using the host's own property context."""
    try:
        tenant_id = get_current_tenant_id(request)
    except HTTPException:
        raise HTTPException(status_code=401)
    cfg = _get_or_create_config(tenant_id, db)
    
    # Use system-wide configuration
    sys_conf = db.query(SystemConfig).first()
    if not sys_conf or not sys_conf.openrouter_api_key_enc:
        return JSONResponse({"error": "AI reply engine is not available right now. Please try again later."})

    try:
        from web.classifier import generate_draft, build_property_context
        ctx = build_property_context(cfg)
        demo_message = (
            "Hi! We just arrived at the property. "
            "Could you tell us the WiFi password? Also, what time is checkout and is there parking? Thanks!"
        )
        # generate_draft will use the system key if provided with tenant_id and no explicit user key
        draft = generate_draft("", "Demo Guest", demo_message, "routine", property_context=ctx, tenant_id=tenant_id)
        return JSONResponse({"draft": draft})
    except Exception as exc:
        log.error("[%s] Demo draft failed: %s", tenant_id, exc)
        return JSONResponse({"error": "Demo draft generation failed. Please try again."})


@app.post("/onboarding/import-listing")
async def import_listing(request: Request, db: Session = Depends(get_db)):
    """Fetch a public Airbnb listing URL and extract property details."""
    try:
        tenant_id = get_current_tenant_id(request)
    except HTTPException:
        raise HTTPException(status_code=401)
    body = await request.json()
    url  = (body.get("url") or "").strip()
    # Check for any Airbnb domain (airbnb.com, airbnb.co.in, airbnb.co.uk, etc.)
    import re as _re
    if not url or not _re.search(r"airbnb\.[a-z.]+", url.lower()):
        return JSONResponse({"error": "Please paste a valid Airbnb listing URL."})
    try:
        # Allow all Airbnb domains (airbnb.com, airbnb.co.in, airbnb.co.uk, etc.)
        url = ensure_public_url(url)
        import requests as req_lib
        from bs4 import BeautifulSoup
        headers = {"User-Agent": "Mozilla/5.0 (compatible; HostAI/1.0)"}
        resp = req_lib.get(url, headers=headers, timeout=10)
        soup = BeautifulSoup(resp.text, "html.parser")

        result: dict = {}

        # Title → property name (remove Airbnb suffix and location)
        title_tag = soup.find("h1") or soup.find("title")
        if title_tag:
            title = title_tag.get_text(strip=True)
            # Remove common Airbnb suffixes
            title = _re.sub(r"\s*-\s*Airbnb\s*$", "", title)
            title = _re.sub(r"\s*\|\s*Airbnb\s*$", "", title)

            # Remove location part that comes after common separators
            # Pattern: "Property Name - Property Type/Category in/near City, State, Country"
            # Keep only the property name before the separator
            prop_name = title

            # Priority 1: Remove everything after " - " (most common)
            if " - " in prop_name:
                prop_name = prop_name.split(" - ")[0].strip()

            # Priority 2: Remove everything after " in " if still has location
            if " in " in prop_name and any(city in prop_name.lower() for city in ["goa", "mumbai", "delhi", "bangalore", "hyderabad", "pune", "cochin", "bangalore"]):
                prop_name = prop_name.split(" in ")[0].strip()

            # Priority 3: Remove common location city names at the end
            location_cities = ["goa", "mumbai", "delhi", "bangalore", "hyderabad", "pune", "cochin", "kolkata", "assagao", "assagaon"]
            for city in location_cities:
                # Remove city name followed by end of string or comma
                prop_name = _re.sub(rf"\s+{city}\s*(?:,|$)", "", prop_name, flags=_re.I).strip()

            result["property_names"] = prop_name[:120]

        # Extract location from property name first (most reliable)
        if "property_names" in result:
            prop_name = result["property_names"]
            # Look for pattern like "City, State, Country" at end (after last "in" or "at")
            # First try to split by common separators
            if " in " in prop_name:
                # Get everything after the last " in "
                parts = prop_name.split(" in ")
                location_candidate = parts[-1].strip()
            else:
                # Otherwise get the last part after a hyphen or dash
                parts = _re.split(r'\s*[-–—]\s*', prop_name)
                location_candidate = parts[-1].strip() if len(parts) > 1 else prop_name

            # Now parse "City, State, Country" from the candidate
            if "," in location_candidate:
                commas = location_candidate.split(",")
                if len(commas) >= 3:
                    city = commas[-3].strip()
                    state = commas[-2].strip()
                    country = commas[-1].strip()
                    # Validate components
                    if all(2 <= len(x) < 50 for x in [city, state, country]):
                        result["property_city"] = f"{city}, {state}, {country}"[:80]

        # Fallback: Extract location from breadcrumb/meta if not found in property name
        if "property_city" not in result:
            for tag in soup.find_all(["span", "div", "p"]):
                if tag.get("data-testid") or tag.get("class"):
                    text = tag.get_text(strip=True)
                    # Skip error messages, warnings, and short fragments
                    if (text and 20 < len(text) < 100 and
                        "sorry" not in text.lower() and
                        "javascript" not in text.lower() and
                        "don't" not in text.lower() and
                        "{" not in text):
                        if any(x in text.lower() for x in ["goa", "mumbai", "delhi", "bangalore", "hyderabad", "pune", "kolkata", "chennai", "cochin"]):
                            if "," in text:
                                result["property_city"] = text[:80]
                                break

        # Extract property type from title and page content
        property_types = ["villa", "apartment", "house", "cottage", "bungalow", "studio", "condo", "townhouse", "flat", "chalet", "penthouse", "resort"]
        if "property_names" in result:
            prop_name_lower = result["property_names"].lower()
            for ptype in property_types:
                if ptype in prop_name_lower:
                    result["property_type"] = ptype.capitalize()
                    break

        # Fallback: search page for property type keywords
        if "property_type" not in result:
            page_text = soup.get_text(strip=True).lower()
            for ptype in property_types:
                if ptype in page_text:
                    result["property_type"] = ptype.capitalize()
                    break

        # Guests from structured data or text - exhaustive search
        if "max_guests" not in result:
            # Search all text nodes for guest patterns
            for tag in soup.find_all(string=True):
                t_str = tag.strip()
                if "{" in t_str or "[" in t_str:
                    continue
                # Look for "X guests", "X guest", "up to X guests", etc.
                guest_match = _re.search(r'(\d+)\s*(?:guests?|person|people)', t_str.lower())
                if guest_match:
                    num = guest_match.group(1)
                    try:
                        if int(num) <= 16:
                            result["max_guests"] = num
                            break
                    except ValueError:
                        pass

        # Check-in / check-out - exhaustive regex-based search
        page_text = soup.get_text()

        # Search for check-in time patterns
        if "check_in_time" not in result:
            # Look for various formats: "Check-in 3:00 PM", "Check-in: 3:00 PM", etc.
            patterns = [
                r'check[- ]?in[:\s]+(\d{1,2}:\d{2}\s*(?:am|pm))',
                r'(?:check[- ]?in|arrival)[:\s]+(\d{1,2}:\d{2}\s*(?:am|pm))',
            ]
            for pattern in patterns:
                match = _re.search(pattern, page_text, _re.I)
                if match:
                    result["check_in_time"] = match.group(1).strip()
                    break

        # Search for check-out time patterns
        if "check_out_time" not in result:
            patterns = [
                r'check[- ]?out[:\s]+(\d{1,2}:\d{2}\s*(?:am|pm))',
                r'(?:check[- ]?out|departure)[:\s]+(\d{1,2}:\d{2}\s*(?:am|pm))',
            ]
            for pattern in patterns:
                match = _re.search(pattern, page_text, _re.I)
                if match:
                    result["check_out_time"] = match.group(1).strip()
                    break

        if not result:
            return JSONResponse({"error": "Could not extract listing details. Please fill in manually."})
        return JSONResponse(result)
    except Exception as exc:
        log.warning("Airbnb listing import failed for %s: %s", url, exc)
        return JSONResponse({"error": "Could not reach that page. Please fill in manually."})


# ---------------------------------------------------------------------------
# Connection testing endpoints (HTMX inline)
# ---------------------------------------------------------------------------

@app.post("/test/imap", response_class=HTMLResponse)
async def test_imap(
    request:       Request,
    imap_host:     str = Form(""),
    email_address: str = Form(""),
    email_password: str = Form(""),
    csrf_token:    str = Form(None),
    db: Session = Depends(get_db),
):
    try:
        tenant_id = get_current_tenant_id(request)
    except HTTPException:
        return HTMLResponse('<p class="test-result test-fail">Not logged in.</p>')
    validate_csrf(request, csrf_token)

    if not imap_host or not email_address or not email_password:
        # If password blank, try existing encrypted password
        cfg = _get_or_create_config(tenant_id, db)
        email_password = email_password or decrypt(cfg.email_password_enc or "")
        imap_host      = imap_host or cfg.imap_host or ""
        email_address  = email_address or cfg.email_address or ""

    if not all([imap_host, email_address, email_password]):
        return HTMLResponse('<p class="test-result test-fail">Fill in host, email and password first.</p>')

    try:
        import imapclient
        safe_host = ensure_public_hostname(imap_host)
        c = imapclient.IMAPClient(safe_host, port=993, ssl=True, timeout=10)
        c.login(email_address, email_password)
        c.select_folder("INBOX")
        c.logout()
        return HTMLResponse('<p class="test-result test-ok">✓ Connected to email successfully</p>')
    except Exception as exc:
        msg = str(exc)
        hint = " — try an App Password" if "authentication" in msg.lower() else ""
        return HTMLResponse(f'<p class="test-result test-fail">✗ {msg[:120]}{hint}</p>')


@app.post("/test/anthropic", response_class=HTMLResponse)
async def test_anthropic(
    request:       Request,
    csrf_token:    str = Form(None),
    db: Session = Depends(get_db),
):
    """Test the system-managed AI reply engine configuration."""
    try:
        tenant_id = get_current_tenant_id(request)
    except HTTPException:
        return HTMLResponse('<p class="test-result test-fail">Not logged in.</p>')
    validate_csrf(request, csrf_token)

    sys_conf = db.query(SystemConfig).first()
    if not sys_conf or not sys_conf.openrouter_api_key_enc:
        return HTMLResponse('<p class="test-result test-fail">✗ AI engine not configured by admin.</p>')

    try:
        import openai
        api_key = decrypt(sys_conf.openrouter_api_key_enc)
        client = openai.OpenAI(base_url="https://openrouter.ai/api/v1", api_key=api_key)
        client.chat.completions.create(
            model="openai/gpt-4o-mini",
            messages=[{"role": "user", "content": "ping"}],
            max_tokens=5
        )
        return HTMLResponse('<p class="test-result test-ok">✓ AI engine connection successful!</p>')
    except Exception as exc:
        log.error("[%s] API test failed: %s", tenant_id, exc)
        return HTMLResponse(f'<p class="test-result test-fail">✗ Request failed: {str(exc)[:120]}</p>')


@app.post("/test/ical", response_class=HTMLResponse)
async def test_ical(
    request:   Request,
    ical_urls: str = Form(""),
    csrf_token: str = Form(None),
    db: Session = Depends(get_db),
):
    try:
        tenant_id = get_current_tenant_id(request)
    except HTTPException:
        return HTMLResponse('<p class="test-result test-fail">Not logged in.</p>')
    validate_csrf(request, csrf_token)

    urls = [u.strip() for u in ical_urls.replace("\n", ",").split(",") if u.strip()]
    if not urls:
        cfg = _get_or_create_config(tenant_id, db)
        urls = [u.strip() for u in (cfg.ical_urls or "").split(",") if u.strip()]

    if not urls:
        return HTMLResponse('<p class="test-result test-fail">Enter an iCal URL first.</p>')

    try:
        import urllib.request as _urlreq
        from icalendar import Calendar
        results = []
        for url in urls[:3]:
            safe_url = ensure_public_url(url)
            req = _urlreq.Request(safe_url, headers={"User-Agent": "HostAI/1.0"})
            with _urlreq.urlopen(req, timeout=10) as r:
                raw = r.read()
            cal = Calendar.from_ical(raw)
            count = sum(1 for c in cal.walk() if c.name == "VEVENT")
            results.append(f"{count} event(s)")
        summary = " | ".join(results)
        return HTMLResponse(f'<p class="test-result test-ok">✓ Calendar connected — {summary} found</p>')
    except Exception as exc:
        return HTMLResponse(f'<p class="test-result test-fail">✗ {str(exc)[:120]}</p>')


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
    if not cfg.inbound_email_alias:
        _ensure_inbound_email_alias(tenant, cfg, db)
        db.commit()
    vendors = db.query(Vendor).filter_by(tenant_id=tenant_id).order_by(Vendor.category, Vendor.name).all()
    pms_integrations = db.query(PMSIntegration).filter_by(
        tenant_id=tenant_id, is_active=True
    ).order_by(PMSIntegration.created_at).all()
    automation_rules = (
        db.query(AutomationRule)
        .filter_by(tenant_id=tenant_id)
        .order_by(AutomationRule.priority.asc(), AutomationRule.created_at.asc())
        .all()
    )
    team_members = (
        db.query(TeamMember)
        .filter_by(tenant_id=tenant_id)
        .order_by(TeamMember.role.asc(), TeamMember.display_name.asc())
        .all()
    )
    reservations = db.query(Reservation).filter_by(tenant_id=tenant_id).all()
    return templates.TemplateResponse("settings.html", {
        "request":          request,
        "tenant":           tenant,
        "cfg":              cfg,
        "vendors":          vendors,
        "pms_integrations": pms_integrations,
        "automation_rules": automation_rules,
        "team_members":     team_members,
        "saved":            False,
        "plan_info": PLAN_INFO.get(cfg.subscription_plan or PLAN_FREE, PLAN_INFO[PLAN_FREE]),
        "has_baileys":    tenant_has_channel(cfg, PLAN_BAILEYS),
        "has_meta_cloud": tenant_has_channel(cfg, PLAN_META_CLOUD),
        "has_sms":        tenant_has_channel(cfg, PLAN_SMS),
        "app_base_url":   APP_BASE_URL,
        "inbound_email_address": _tenant_inbound_email_address(cfg),
        "inbound_webhook_url": f"{APP_BASE_URL}/email/inbound",
        "activation_checklist": build_activation_checklist(
            cfg,
            reservations=reservations,
            inbound_email_address=_tenant_inbound_email_address(cfg),
            inbound_webhook_url=f"{APP_BASE_URL}/email/inbound",
        ),
    })


@app.post("/api/tenant/delete")
def api_gdpr_delete_tenant(
    req: Request,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_current_tenant_id)
):
    """
    GDPR Delete Route (Fixes #21)
    Wipes the current tenant's entire database state.
    """
    try:
        from web.models import (
            Tenant, TenantConfig, TeamMember, AutomationRule, PMSIntegration, Vendor, 
            Draft, ActivityLog, Reservation, ReservationIntakeBatch, BaileysOutbound, 
            ProcessedEmail, CalendarState, FailedDraftLog, PMSProcessedMessage,
            ReservationSyncLog, GuestTimelineEvent, ArrivalActivation, IssueTicket
        )
        
        # We must delete in referential order (children first)
        db.query(Draft).filter_by(tenant_id=tenant_id).delete(synchronize_session=False)
        db.query(ActivityLog).filter_by(tenant_id=tenant_id).delete(synchronize_session=False)
        db.query(BaileysOutbound).filter_by(tenant_id=tenant_id).delete(synchronize_session=False)
        db.query(ProcessedEmail).filter_by(tenant_id=tenant_id).delete(synchronize_session=False)
        db.query(CalendarState).filter_by(tenant_id=tenant_id).delete(synchronize_session=False)
        db.query(FailedDraftLog).filter_by(tenant_id=tenant_id).delete(synchronize_session=False)
        db.query(PMSProcessedMessage).filter_by(tenant_id=tenant_id).delete(synchronize_session=False)
        db.query(GuestTimelineEvent).filter_by(tenant_id=tenant_id).delete(synchronize_session=False)
        db.query(ArrivalActivation).filter_by(tenant_id=tenant_id).delete(synchronize_session=False)
        db.query(IssueTicket).filter_by(tenant_id=tenant_id).delete(synchronize_session=False)
        db.query(AutomationRule).filter_by(tenant_id=tenant_id).delete(synchronize_session=False)
        db.query(ReservationIntakeBatch).filter_by(tenant_id=tenant_id).delete(synchronize_session=False)
        db.query(ReservationSyncLog).filter_by(tenant_id=tenant_id).delete(synchronize_session=False)
        db.query(Reservation).filter_by(tenant_id=tenant_id).delete(synchronize_session=False)
        db.query(PMSIntegration).filter_by(tenant_id=tenant_id).delete(synchronize_session=False)
        db.query(Vendor).filter_by(tenant_id=tenant_id).delete(synchronize_session=False)
        db.query(TeamMember).filter_by(tenant_id=tenant_id).delete(synchronize_session=False)
        db.query(TenantConfig).filter_by(tenant_id=tenant_id).delete(synchronize_session=False)
        db.query(Tenant).filter_by(id=tenant_id).delete(synchronize_session=False)
        
        db.commit()
        
        resp = RedirectResponse(url="/logout", status_code=303)
        return resp
    except Exception as exc:
        db.rollback()
        log.error("[%s] GDPR deletion failed: %s", tenant_id, exc)
        return RedirectResponse(url="/settings?error=DeletionFailed", status_code=303)


@app.post("/settings", response_class=HTMLResponse)
async def settings_save(
    request:        Request,
    property_names:        str = Form(""),
    ical_urls:             str = Form(""),
    email_ingest_mode:     str = Form("imap"),
    imap_host:             str = Form(""),
    smtp_host:             str = Form(""),
    email_address:         str = Form(""),
    email_password:        str = Form(""),
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
    # Per-tenant settings save rate limit (prevents config spam / test-loop abuse)
    rate_limit(f"settings:{tenant_id}", max_requests=30, window_seconds=3600)

    cfg = _get_or_create_config(tenant_id, db)
    tenant = _get_tenant(tenant_id, db)
    _ensure_inbound_email_alias(tenant, cfg, db)

    # Core settings
    cfg.property_names = property_names.strip()
    cfg.ical_urls      = ical_urls.strip()
    cfg.email_ingest_mode = email_ingest_mode.strip() or cfg.email_ingest_mode or "imap"
    cfg.imap_host      = imap_host.strip() or None
    cfg.smtp_host      = smtp_host.strip() or None
    cfg.email_address  = email_address.strip() or None
    if email_password.strip():
        cfg.email_password_enc = encrypt(email_password.strip())

    # Extended property context fields (editable from Settings after onboarding)
    form_data = await request.form()
    for field in (
        "property_type",
        "property_city",
        "check_in_time",
        "check_out_time",
        "house_rules",
        "pet_policy",
        "refund_policy",
        "early_checkin_policy",
        "early_checkin_fee",
        "late_checkout_policy",
        "late_checkout_fee",
        "parking_policy",
        "smoking_policy",
        "quiet_hours",
        "amenities",
        "food_menu",
        "nearby_restaurants",
        "faq",
        "custom_instructions",
        "escalation_email",
    ):
        val = form_data.get(field, "")
        if val is not None and str(val).strip():
            setattr(cfg, field, str(val).strip())
    max_g = str(form_data.get("max_guests","")).strip()
    if max_g.isdigit():
        cfg.max_guests = int(max_g)

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

    vendors = db.query(Vendor).filter_by(tenant_id=tenant_id).order_by(Vendor.category, Vendor.name).all()
    pms_integrations = db.query(PMSIntegration).filter_by(
        tenant_id=tenant_id, is_active=True
    ).order_by(PMSIntegration.created_at).all()
    automation_rules = (
        db.query(AutomationRule)
        .filter_by(tenant_id=tenant_id)
        .order_by(AutomationRule.priority.asc(), AutomationRule.created_at.asc())
        .all()
    )
    team_members = (
        db.query(TeamMember)
        .filter_by(tenant_id=tenant_id)
        .order_by(TeamMember.role.asc(), TeamMember.display_name.asc())
        .all()
    )
    reservations = db.query(Reservation).filter_by(tenant_id=tenant_id).all()
    return templates.TemplateResponse("settings.html", {
        "request":          request,
        "tenant":           tenant,
        "cfg":              cfg,
        "vendors":          vendors,
        "pms_integrations": pms_integrations,
        "automation_rules": automation_rules,
        "team_members":     team_members,
        "saved":            True,
        "plan_info": PLAN_INFO.get(cfg.subscription_plan or PLAN_FREE, PLAN_INFO[PLAN_FREE]),
        "has_baileys":    tenant_has_channel(cfg, PLAN_BAILEYS),
        "has_meta_cloud": tenant_has_channel(cfg, PLAN_META_CLOUD),
        "has_sms":        tenant_has_channel(cfg, PLAN_SMS),
        "app_base_url":   APP_BASE_URL,
        "inbound_email_address": _tenant_inbound_email_address(cfg),
        "inbound_webhook_url": f"{APP_BASE_URL}/email/inbound",
        "activation_checklist": build_activation_checklist(
            cfg,
            reservations=reservations,
            inbound_email_address=_tenant_inbound_email_address(cfg),
            inbound_webhook_url=f"{APP_BASE_URL}/email/inbound",
        ),
    })


@app.post("/settings/automation")
def automation_rule_add(
    request: Request,
    name: str = Form(...),
    channel: str = Form("any"),
    msg_types: list[str] = Form([]),
    mode: str = Form("auto_send"),
    min_confidence: str = Form("0.85"),
    properties: str = Form(""),
    allow_complex: str = Form(""),
    allow_negative_sentiment: str = Form(""),
    min_guest_history_score: str = Form(""),
    stay_stages: list[str] = Form([]),
    csrf_token: str = Form(None),
    db: Session = Depends(get_db),
):
    try:
        tenant_id = get_current_tenant_id(request)
    except HTTPException:
        return _redirect_login()
    validate_csrf(request, csrf_token)

    rule = AutomationRule(
        tenant_id=tenant_id,
        name=name.strip(),
        channel=channel.strip() or "any",
        confidence_threshold=float(min_confidence) if min_confidence.strip() else 0.85,
        conditions_json={
            "msg_types": msg_types or ["routine"],
            "properties": _split_csv_values(properties),
            "allow_complex": str(allow_complex).strip().lower() in {"1", "true", "yes", "on"},
            "allow_negative_sentiment": str(allow_negative_sentiment).strip().lower() in {"1", "true", "yes", "on"},
            "min_guest_history_score": float(min_guest_history_score) if min_guest_history_score.strip() else None,
            "stay_stages": stay_stages or [],
        },
        actions_json={"mode": mode.strip() or "auto_send"},
        priority=100,
    )
    db.add(rule)
    db.add(ActivityLog(
        tenant_id=tenant_id,
        event_type="automation_rule_added",
        message=f"Automation rule added: {rule.name}",
    ))
    db.commit()
    return RedirectResponse("/settings#workflow", status_code=302)


@app.post("/settings/automation/{rule_id}/delete")
def automation_rule_delete(
    rule_id: int,
    request: Request,
    csrf_token: str = Form(None),
    db: Session = Depends(get_db),
):
    try:
        tenant_id = get_current_tenant_id(request)
    except HTTPException:
        return _redirect_login()
    validate_csrf(request, csrf_token)
    rule = db.query(AutomationRule).filter_by(id=rule_id, tenant_id=tenant_id).first()
    if rule:
        db.delete(rule)
        db.commit()
    return RedirectResponse("/settings#workflow", status_code=302)


@app.post("/settings/team")
def team_member_add(
    request: Request,
    display_name: str = Form(...),
    role: str = Form("manager"),
    email: str = Form(""),
    phone: str = Form(""),
    property_scope: str = Form(""),
    csrf_token: str = Form(None),
    db: Session = Depends(get_db),
    _=Depends(require_flag("TEAM_MEMBERS")),
):
    try:
        tenant_id = get_current_tenant_id(request)
    except HTTPException:
        return _redirect_login()
    validate_csrf(request, csrf_token)

    member = TeamMember(
        tenant_id=tenant_id,
        display_name=display_name.strip(),
        role=role.strip() or "manager",
        email=email.strip() or None,
        phone=phone.strip() or None,
        property_scope=property_scope.strip() or None,
    )
    db.add(member)
    db.add(ActivityLog(
        tenant_id=tenant_id,
        event_type="team_member_added",
        message=f"Team member added: {member.display_name} ({member.role})",
    ))
    db.commit()
    return RedirectResponse("/settings#workflow", status_code=302)


@app.post("/settings/team/{member_id}/delete")
def team_member_delete(
    member_id: int,
    request: Request,
    csrf_token: str = Form(None),
    db: Session = Depends(get_db),
    _=Depends(require_flag("TEAM_MEMBERS")),
):
    try:
        tenant_id = get_current_tenant_id(request)
    except HTTPException:
        return _redirect_login()
    validate_csrf(request, csrf_token)
    member = db.query(TeamMember).filter_by(id=member_id, tenant_id=tenant_id).first()
    if member:
        db.delete(member)
        db.commit()
    return RedirectResponse("/settings#workflow", status_code=302)


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
def pricing_page(request: Request, db: Session = Depends(get_db)):
    tenant = None
    cfg = None
    try:
        tenant_id = get_current_tenant_id(request)
        tenant = _get_tenant(tenant_id, db)
        cfg = _get_or_create_config(tenant_id, db)
    except HTTPException:
        pass
    return templates.TemplateResponse("pricing.html", {
        "request":   request,
        "plan_info": PLAN_INFO,
        "tenant":    tenant,
        "cfg":       cfg,
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


@app.post("/billing/subscribe/{plan_key}")
def billing_subscribe(plan_key: str, request: Request,
                      num_units: int = Form(1),
                      csrf_token: str = Form(None),
                      db: Session = Depends(get_db)):
    try:
        tenant_id = get_current_tenant_id(request)
    except HTTPException:
        return _redirect_login()
    validate_csrf(request, csrf_token)

    # Validate plan and units
    plan = db.query(PlanConfig).filter_by(plan_key=plan_key, is_active=True).first()
    if not plan:
        raise HTTPException(status_code=400, detail="Invalid plan")
    if not (plan.min_units <= num_units <= plan.max_units):
        raise HTTPException(status_code=400, detail=f"Plan requires {plan.min_units}-{plan.max_units} units")

    cfg = _get_or_create_config(tenant_id, db)
    try:
        url = create_checkout_session(
            tenant_id=tenant_id,
            plan_key=plan_key,
            num_units=num_units,
            success_url=f"{APP_BASE_URL}/billing/success?plan={plan_key}",
            cancel_url=f"{APP_BASE_URL}/billing/cancel",
            customer_id=cfg.stripe_customer_id,
            db=db,
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


@app.get("/api/plan-pricing")
def api_plan_pricing(db: Session = Depends(get_db)):
    """Public JSON endpoint: current plan pricing and tiers."""
    plans = db.query(PlanConfig).filter_by(is_active=True).order_by(PlanConfig.min_units).all()
    return [
        {
            "plan_key": p.plan_key,
            "display_name": p.display_name,
            "base_fee": p.base_fee_usd,
            "per_unit_fee": p.per_unit_fee_usd,
            "min_units": p.min_units,
            "max_units": p.max_units,
        }
        for p in plans
    ]


@app.get("/admin/pricing", response_class=HTMLResponse)
def admin_pricing_page(request: Request, db: Session = Depends(get_db)):
    _require_admin(request, db)
    plans = db.query(PlanConfig).order_by(PlanConfig.min_units).all()
    return templates.TemplateResponse("admin_pricing.html", {
        "request": request,
        "plans": plans,
    })


@app.post("/admin/pricing/{plan_key}")
def admin_update_pricing(plan_key: str, request: Request,
                         base_fee: float = Form(...),
                         per_unit_fee: float = Form(...),
                         min_units: int = Form(...),
                         max_units: int = Form(...),
                         display_name: str = Form(...),
                         csrf_token: str = Form(None),
                         db: Session = Depends(get_db)):
    admin = _require_admin(request, db)
    validate_csrf(request, csrf_token)

    plan = db.query(PlanConfig).filter_by(plan_key=plan_key).first()
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")

    # Validation
    if min_units > max_units:
        raise HTTPException(status_code=400, detail="min_units cannot be greater than max_units")
    if base_fee < 0 or per_unit_fee < 0:
        raise HTTPException(status_code=400, detail="Fees cannot be negative")

    plan.base_fee_usd = base_fee
    plan.per_unit_fee_usd = per_unit_fee
    plan.min_units = min_units
    plan.max_units = max_units
    plan.display_name = display_name
    plan.updated_at = datetime.now(timezone.utc)

    db.add(plan)

    # Audit log
    db.add(ActivityLog(
        tenant_id=admin.id, event_type="admin_pricing_change",
        message=f"Plan {plan_key} updated: base_fee={base_fee} per_unit={per_unit_fee} min={min_units} max={max_units} by {admin.email}"
    ))
    db.commit()

    # Admin alert
    send_admin_alert(
        f"Plan pricing changed: {plan_key}",
        f"Admin: {admin.email}\nPlan: {plan_key}\nbase_fee={base_fee} per_unit={per_unit_fee} min_units={min_units} max_units={max_units}"
    )

    return RedirectResponse(f"/admin/pricing?msg=updated", status_code=302)


# ---------------------------------------------------------------------------
# Meta WhatsApp Cloud API webhooks
# ---------------------------------------------------------------------------

@app.get("/wa/webhook/{tenant_id}")
def wa_webhook_verify(tenant_id: str, request: Request, db: Session = Depends(get_db)):
    """Meta webhook verification handshake."""
    rate_limit(f"wa-verify:{tenant_id}:{client_ip(request)}", max_requests=120, window_seconds=60)
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
    rate_limit(f"wa-inbound:{tenant_id}:{client_ip(request)}", max_requests=300, window_seconds=60)
    cfg = db.query(TenantConfig).filter_by(tenant_id=tenant_id).first()
    if not cfg:
        return JSONResponse({"status": "ok"})   # always 200 to Meta

    try:
        require_channel(cfg, PLAN_META_CLOUD)
    except HTTPException:
        return JSONResponse({"status": "ok"})

    raw_body = await request.body()
    if not _validate_meta_signature(raw_body, request.headers.get("X-Hub-Signature-256", "")):
        return JSONResponse({"status": "forbidden"}, status_code=403)
    body = json.loads(raw_body.decode("utf-8"))
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
    rate_limit(f"sms-inbound:{tenant_id}:{client_ip(request)}", max_requests=200, window_seconds=60)
    cfg = db.query(TenantConfig).filter_by(tenant_id=tenant_id).first()
    if not cfg:
        return HTMLResponse("<Response/>")

    try:
        require_channel(cfg, PLAN_SMS)
    except HTTPException:
        return HTMLResponse("<Response/>")

    form = await request.form()
    form_data = dict(form)
    if not _validate_twilio_signature(request, form_data, cfg):
        return HTMLResponse("<Response/>", status_code=403)
    from web.sms_sender import parse_twilio_inbound
    msg = parse_twilio_inbound(form_data)
    if msg:
        _handle_inbound_sms(tenant_id, msg["from"], msg["text"], db)

    return HTMLResponse("<Response/>")   # TwiML empty response


@app.post("/email/inbound")
async def inbound_email_webhook(request: Request, db: Session = Depends(get_db)):
    """
    Generic inbound email webhook for forwarding + parse providers.
    Expected fields are flexible enough for Mailgun/Postmark/SendGrid style payloads.
    """
    content_type = request.headers.get("content-type", "").lower()
    rate_limit(f"inbound-email:{client_ip(request)}", max_requests=120, window_seconds=60)
    raw_body = await request.body()
    if "application/json" in content_type:
        try:
            payload = json.loads(raw_body.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="Invalid JSON payload")
    else:
        payload = dict(await request.form())
    if not _verify_inbound_email_webhook(request, payload, raw_body):
        raise HTTPException(status_code=403, detail="Invalid inbound email webhook authentication")

    recipient = _payload_value(
        payload,
        "recipient",
        "to",
        "To",
        "envelope[to]",
        "original_recipient",
    )
    alias = _extract_recipient_alias(recipient)
    if not alias:
        raise HTTPException(status_code=400, detail="Recipient address missing")

    cfg = db.query(TenantConfig).filter_by(inbound_email_alias=alias).first()
    if not cfg:
        raise HTTPException(status_code=404, detail="Inbound email route not found")

    subject = _payload_value(payload, "subject", "Subject")
    sender = _payload_value(payload, "sender", "from", "From") or _payload_header(payload, "From")
    reply_to = (
        _payload_value(payload, "reply_to", "Reply-To", "reply-to")
        or _payload_header(payload, "Reply-To", "reply-to")
        or sender
    )
    text_body = _payload_value(payload, "stripped-text", "body-plain", "text", "body_plain", "body")
    html_body = _payload_value(payload, "stripped-html", "body-html", "html", "body_html")
    message_id = (
        _payload_value(payload, "Message-Id", "message-id", "message_id", "Message-ID")
        or _payload_header(payload, "Message-Id", "Message-ID")
    )
    dedupe_key = message_id.strip() or hashlib.sha256(
        f"{recipient}|{sender}|{subject}|{text_body[:500]}".encode()
    ).hexdigest()
    email_uid = f"inbound:{dedupe_key}"
    if db.query(ProcessedEmail).filter_by(tenant_id=cfg.tenant_id, email_uid=email_uid).first():
        return JSONResponse({"status": "duplicate"})

    from web.email_worker import parse_structured_email, process_parsed_email_with_config

    parsed = parse_structured_email(subject, sender, reply_to, text_body, html_body)
    if not parsed:
        return JSONResponse({"status": "ignored"})
    if not process_parsed_email_with_config(cfg, parsed, subject or "Forwarded Airbnb message", db_session=db):
        raise HTTPException(status_code=422, detail="Tenant email processing is not ready")

    cfg.last_inbound_email_at = datetime.now(timezone.utc)
    db.add(ProcessedEmail(tenant_id=cfg.tenant_id, email_uid=email_uid))
    db.add(ActivityLog(
        tenant_id=cfg.tenant_id,
        event_type="email_forward_received",
        message=f"Forwarded inbound email received for {recipient}",
    ))
    db.commit()
    return JSONResponse({"status": "ok", "tenant_id": cfg.tenant_id})


# ---------------------------------------------------------------------------
# Shared inbound handler — creates a Draft for host review
# ---------------------------------------------------------------------------

def _handle_guest_inbound_message(tenant_id: str, source: str, reply_to: str, text: str, db: Session):
    """Classify an inbound guest message and create a draft with thread + policy context."""
    cfg = db.query(TenantConfig).filter_by(tenant_id=tenant_id).first()
    if not cfg:
        return

    # Check if message is from the HOST themselves (host management via WhatsApp)
    if source == "whatsapp" and cfg.whatsapp_number and _normalize_phone(reply_to) == _normalize_phone(cfg.whatsapp_number):
        _handle_host_command(tenant_id, text.strip(), db)
        return

    # Check guest whitelisting (GuestContact system)
    from web.guest_contact_service import is_guest_whitelisted, get_guest_contact_for_phone
    if not is_guest_whitelisted(tenant_id, reply_to, db):
        guest_contact = get_guest_contact_for_phone(tenant_id, reply_to, db)
        if not guest_contact:
            log.warning(f"[{tenant_id}] Inbound from unregistered guest {reply_to} ({source}) — rejecting")
            return  # Don't process unregistered guests
        # Guest contact found but outside check-in window — allow but log
        log.info(f"[{tenant_id}] Message from {guest_contact.guest_name} outside check-in window")

    try:
        from web.classifier import classify_message_with_confidence, detect_vendor_type, generate_draft, build_property_context
        reservation = _find_reservation_for_guest_context(
            tenant_id, db, guest_phone=reply_to, guest_name=f"{source.title()} guest"
        )
        guest_name = reservation.guest_name if reservation else f"{source.title()} guest"
        msg_type, confidence, _matched_patterns = classify_message_with_confidence(text)

        from web import classifier as classifier_mod
        sentiment = classifier_mod.analyze_sentiment_and_intent_llm(tenant_id, text)

        vendor_type = detect_vendor_type(text) if msg_type == "complex" else None
        property_context = build_property_context(cfg)
        if reservation:
            property_context = (
                property_context
                + "\n\n<reservation>\n"
                + _reservation_context_text(reservation, cfg)
                + "\n</reservation>"
            ).strip()
            memory_context = _timeline_memory_context(tenant_id, reservation, db)
            if memory_context:
                property_context = (
                    property_context
                    + "\n\n<recent_guest_history>\n"
                    + memory_context
                    + "\n</recent_guest_history>"
                ).strip()
        draft_text = generate_draft(guest_name, text, msg_type, property_context=property_context, tenant_id=tenant_id)
        recent_drafts = _recent_reservation_drafts(db, tenant_id, reservation)
        guest_history_score = compute_guest_history_score(reservation, recent_drafts)
        stay_stage = compute_stay_stage(reservation)
        policy_conflicts = draft_policy_conflicts(text, draft_text, cfg)
        thread_key, parent_draft_id, guest_message_index = _draft_thread_metadata(
            db, tenant_id, reservation, reply_to, guest_name, source
        )
        draft_id = secrets.token_hex(8)
        draft = Draft(
            id=draft_id,
            tenant_id=tenant_id,
            source=source,
            reservation_id=reservation.id if reservation else None,
            parent_draft_id=parent_draft_id,
            thread_key=thread_key,
            guest_message_index=guest_message_index,
            guest_name=guest_name,
            message=text,
            reply_to=reply_to,
            msg_type=msg_type,
            vendor_type=vendor_type,
            draft=draft_text,
            confidence=confidence,
            property_name_snapshot=reservation.listing_name if reservation else None,
            unit_identifier_snapshot=reservation.unit_identifier if reservation else None,
            auto_send_eligible=(msg_type == "routine" and confidence >= 0.7 and sentiment["label"] != "negative" and not policy_conflicts and guest_history_score >= 0.4),
            guest_history_score=guest_history_score,
            guest_sentiment=sentiment["label"],
            sentiment_score=sentiment["score"],
            stay_stage=stay_stage,
            policy_conflicts_json=_draft_policy_conflicts_json(policy_conflicts),
        )
        db.add(draft)
        if reservation:
            reservation.last_guest_message_at = datetime.now(timezone.utc)
            reservation.message_count = (reservation.message_count or 0) + 1
            reservation.latest_guest_sentiment = sentiment["label"]
            reservation.latest_guest_sentiment_score = sentiment["score"]
        _record_timeline_event(
            db,
            tenant_id,
            reservation,
            "guest_message_received",
            f"{source.title()} message from {guest_name}",
            channel=source,
            direction="inbound",
            body=text,
            draft=draft,
            payload_json={
                "reply_to": reply_to,
                "thread_key": thread_key,
                "guest_sentiment": sentiment["label"],
                "policy_conflicts": policy_conflicts,
            },
        )
        db.add(ActivityLog(tenant_id=tenant_id, event_type=f"{source}_received",
                           message=f"{source.upper()} from {reply_to}: {text[:80]}"))
        db.commit()

        # Check if message should be auto-sent (routine + high confidence + no issues)
        should_auto_send = (
            draft.msg_type == "routine"
            and draft.confidence >= 0.9
            and draft.guest_sentiment != "negative"
            and not policy_conflicts
            and guest_history_score >= 0.4
        )

        if should_auto_send:
            # Auto-send the message directly to guest
            draft.status = "auto_sent"
            draft.final_text = draft.draft
            draft.updated_at = datetime.now(timezone.utc)

            # Queue message to guest
            _queue_baileys_outbound(tenant_id, reply_to, draft.final_text, db)

            # Log auto-send
            db.add(ActivityLog(
                tenant_id=tenant_id,
                event_type="draft_auto_sent",
                message=f"Auto-sent to {guest_name}: {draft.draft[:80]}"
            ))
            db.commit()

            log.info(f"[{tenant_id}] Auto-sent routine message to {guest_name} (confidence: {draft.confidence:.0%})")

            # Record outbound event
            _record_timeline_event(
                db,
                tenant_id,
                reservation,
                "guest_message_sent",
                f"Auto-response to {guest_name}",
                channel=source,
                direction="outbound",
                body=draft.final_text,
                draft=draft,
                payload_json={"auto_sent": True, "confidence": draft.confidence},
            )
            db.commit()
        else:
            # Keep as pending for host review
            draft.status = "pending"
            db.commit()

            # Publish real-time notification for SSE subscribers (only for pending)
            try:
                from web.redis_client import get_redis as _get_redis
                r = _get_redis()
                if r:
                    r.publish(f"hostai:notify:{tenant_id}", json.dumps({
                        "guest_name": draft.guest_name,
                        "source": draft.source,
                        "msg_type": draft.msg_type,
                        "draft_id": draft.id,
                    }))
            except Exception:
                pass  # non-critical

            # Send host notification if enabled (only for pending)
            if cfg.notify_host_on_guest_msg:
                notify_phone = cfg.host_notify_phone or cfg.whatsapp_number
                if notify_phone:
                    notify_text = f"📩 New guest message from {guest_name}:\n\n\"{text}\"\n\n— Reply in your HostAI dashboard"
                    _send_host_notification(tenant_id, notify_phone, notify_text, guest_name, text, source, db)

            log.info(f"[{tenant_id}] Pending review: {guest_name} ({draft.msg_type}, confidence: {draft.confidence:.0%})")

        _apply_automation_if_matched(db, tenant_id, draft, reservation)

    except Exception as exc:
        log.error("[%s] %s inbound handler error: %s", tenant_id, source.upper(), exc)


def _handle_inbound_wa(tenant_id: str, from_phone: str, text: str, db: Session):
    """Classify an inbound WhatsApp message and create a pending draft."""
    _handle_guest_inbound_message(tenant_id, "whatsapp", from_phone, text, db)


def _handle_inbound_sms(tenant_id: str, from_phone: str, text: str, db: Session):
    """Classify an inbound SMS and create a pending draft."""
    _handle_guest_inbound_message(tenant_id, "sms", from_phone, text, db)


# ---------------------------------------------------------------------------
# Baileys bot API (bot runs on host's PC, calls back to here)
# ---------------------------------------------------------------------------

@app.get("/api/wa/pending")
def api_wa_pending(request: Request, db: Session = Depends(get_db)):
    """Baileys bot polls this to get outbound messages to deliver to guests. (Improvements #1, #2, #10)"""
    cfg = _auth_bot(request, db)
    try:
        require_channel(cfg, PLAN_BAILEYS)
    except HTTPException:
        raise HTTPException(status_code=402, detail="Subscription required — renew at /billing")

    # Rate limit: max 100 polls/minute per bot (Improvement #1)
    rate_limit(f"wa:poll:{cfg.tenant_id}", 100, 60)

    result = _pop_baileys_outbound(cfg.tenant_id, db)
    result.update(_bot_token_expiry_warning(cfg))  # Baileys fix #8: include token expiry warning
    return JSONResponse(result)


@app.post("/api/wa/inbound")
async def api_wa_inbound(request: Request, db: Session = Depends(get_db)):
    """Baileys bot pushes an inbound message from a guest/vendor. (Improvements #1, #5)"""
    cfg = _auth_bot(request, db)
    try:
        require_channel(cfg, PLAN_BAILEYS)
    except HTTPException:
        raise HTTPException(status_code=402, detail="Subscription required — renew at /billing")

    # Rate limit: max 300 inbound/minute per bot (Improvement #1)
    rate_limit(f"wa:inbound:{cfg.tenant_id}", 300, 60)

    body = await request.json()
    from_phone = body.get("from", "")
    text = body.get("text", "")

    # Validate phone number (Improvement #5)
    if not _validate_phone_number(from_phone):
        return JSONResponse({"status": "error", "detail": "Invalid phone number"}, status_code=400)

    if from_phone and text:
        _handle_inbound_wa(cfg.tenant_id, from_phone, text, db)
        log.info("[%s] Inbound WA message from ***%s received and queued for processing", cfg.tenant_id, from_phone[-4:] if from_phone else "")  # Baileys fix #9
    return JSONResponse({"status": "ok"})


@app.post("/api/wa/callback")
async def api_wa_callback(request: Request, db: Session = Depends(get_db)):
    """
    Baileys bot reports that the host typed a command in WA (APPROVE / EDIT / SKIP).
    Body: {"action": "approve"|"edit"|"skip", "draft_id": str, "text": str, "idempotency_key": str}
    (Improvements #6, #9)
    """
    cfg = _auth_bot(request, db)
    try:
        require_channel(cfg, PLAN_BAILEYS)
    except HTTPException:
        raise HTTPException(status_code=402, detail="Subscription required — renew at /billing")

    rate_limit(f"wa:callback:{cfg.tenant_id}", 50, 60)  # Baileys fix #1: rate limit callback requests

    body     = await request.json()
    action   = body.get("action", "")
    draft_id = body.get("draft_id", "")
    text     = body.get("text", "")
    idempotency_key = body.get("idempotency_key", "")
    tenant_id = cfg.tenant_id

    # Idempotency: check if this callback was already processed (Improvement #6)
    if idempotency_key:
        existing = db.query(BaileysCallback).filter_by(idempotency_key=idempotency_key).first()
        if existing:
            return JSONResponse({"status": "already_processed"})

    draft = db.query(Draft).filter_by(id=draft_id, tenant_id=tenant_id).first()
    if not draft:
        return JSONResponse({"status": "not_found"}, status_code=404)

    try:
        if action == "approve":
            _execute_draft(draft, draft.draft, tenant_id, db)
        elif action == "edit" and text:
            _execute_draft(draft, text, tenant_id, db)
        elif action == "skip":
            draft.status = "skipped"
            db.add(ActivityLog(tenant_id=tenant_id, event_type="draft_skipped",
                               message=f"Draft skipped via WA: {draft.guest_name}"))
            db.commit()

        # Record the callback (Improvement #6)
        if idempotency_key:
            cb = BaileysCallback(
                tenant_id=tenant_id,
                draft_id=draft_id,
                action=action,
                idempotency_key=idempotency_key
            )
            db.add(cb)
            db.commit()

        log.info("[%s] WA callback processed: action=%s draft_id=%s", tenant_id, action, draft_id)  # Baileys fix #9
        result = {"status": "ok"}
        result.update(_bot_token_expiry_warning(cfg))  # Baileys fix #8: include token expiry warning
        return JSONResponse(result)
    except Exception as e:
        # Better error context for debugging (Improvement #9)
        log.error("[%s] Failed to process callback: action=%s draft_id=%s error=%s",
                  tenant_id, action, draft_id, str(e))
        return JSONResponse({"status": "error", "detail": "Internal error — check server logs"}, status_code=500)


@app.post("/api/wa/token/generate")
def api_generate_bot_token(request: Request,
                           csrf_token: str = Form(None),
                           db: Session = Depends(get_db)):
    """Generate (or regenerate) the Baileys bot API token for this tenant. (Improvement #11)"""
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

    # Set token expiration to 90 days from now (Improvement #11)
    cfg.bot_api_token_expires_at = datetime.now(timezone.utc) + timedelta(days=90)
    db.add(cfg)
    db.commit()

    return JSONResponse({
        "token": raw_token,
        "hint": cfg.bot_api_token_hint,
        "expires_at": cfg.bot_api_token_expires_at.isoformat()
    })


@app.post("/api/wa/ack")
async def api_wa_ack(request: Request, db: Session = Depends(get_db)):
    """
    Bot confirms it successfully sent messages.
    Completes two-phase commit: marks in_transit messages from last 60s as delivered.
    Body: {"batch_id": str}
    (Improvement #4)
    """
    cfg = _auth_bot(request, db)
    try:
        require_channel(cfg, PLAN_BAILEYS)
    except HTTPException:
        raise HTTPException(status_code=402, detail="Subscription required — renew at /billing")

    rate_limit(f"wa:ack:{cfg.tenant_id}", 100, 60)  # Baileys fix #1: rate limit ack requests

    body = await request.json()
    batch_id = body.get("batch_id", "")
    tenant_id = cfg.tenant_id

    # Mark in_transit messages from last 60s as delivered (Improvement #4)
    now = datetime.now(timezone.utc)
    sixty_seconds_ago = now - timedelta(seconds=60)

    try:
        updated = db.query(BaileysOutbound).filter(
            BaileysOutbound.tenant_id == tenant_id,
            BaileysOutbound.status == "in_transit",
            BaileysOutbound.created_at >= sixty_seconds_ago
        ).update(
            {"status": "delivered", "delivered": True, "delivered_at": now},
            synchronize_session=False
        )
        db.commit()
        log.info("[%s] Marked %d Baileys messages as delivered (batch %s)", tenant_id, updated, batch_id)
        return JSONResponse({"status": "ok", "confirmed_count": updated})
    except Exception as e:
        log.error("[%s] Failed to mark messages delivered: %s", tenant_id, str(e))
        db.rollback()
        return JSONResponse({"status": "error", "detail": "Internal error — check server logs"}, status_code=500)


@app.post("/api/wa/heartbeat")
async def api_wa_heartbeat(request: Request, db: Session = Depends(get_db)):
    """
    Bot pings every 5 minutes. Updates heartbeat timestamp and returns pending count.
    (Improvement #8)
    """
    cfg = _auth_bot(request, db)
    try:
        require_channel(cfg, PLAN_BAILEYS)
    except HTTPException:
        raise HTTPException(status_code=402, detail="Subscription required — renew at /billing")

    rate_limit(f"wa:heartbeat:{cfg.tenant_id}", 20, 60)  # Baileys fix #1: rate limit heartbeat requests

    tenant_id = cfg.tenant_id

    try:
        # Update last heartbeat (Improvement #8)
        cfg.bot_last_heartbeat = datetime.now(timezone.utc)
        db.add(cfg)
        db.commit()

        # Count pending messages
        pending_count = db.query(BaileysOutbound).filter_by(
            tenant_id=tenant_id,
            status="pending"
        ).count()

        log.info("[%s] Heartbeat received. Pending count: %d", tenant_id, pending_count)  # Baileys fix #9: log success
        result = {
            "status": "alive",
            "pending_count": pending_count,
            "next_poll_in_seconds": 10
        }
        result.update(_bot_token_expiry_warning(cfg))  # Baileys fix #8: include token expiry warning
        return JSONResponse(result)
    except Exception as e:
        log.error("[%s] Failed to process heartbeat: %s", tenant_id, str(e))
        return JSONResponse({"status": "error", "detail": "Internal error — check server logs"}, status_code=500)


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
    bot_js_path = os.path.join(os.path.dirname(__file__), "bot.js")
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

    phone_comment = ("# Phone pre-filled from your settings" if cfg.whatsapp_number
                     else "# IMPORTANT: Replace HOST_WHATSAPP_NUMBER with the WhatsApp number you will scan QR with")
    env_content = (
        f"# HostAI Baileys Bot — auto-generated for your account\n"
        f"WA_MODE=saas_bridge\n"
        f"WEB_APP_URL={APP_BASE_URL}\n"
        f"TENANT_ID={tenant_id}\n"
        f"BOT_API_TOKEN={raw_token}\n"
        f"HOST_WHATSAPP_NUMBER={cfg.whatsapp_number or '+1234567890'}\n"
        f"{phone_comment}\n"
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
# Reservations — CSV import, list, analytics
# ---------------------------------------------------------------------------

# Airbnb CSV column name aliases (different export locales/versions use different names)
_CSV_ALIASES = {
    "confirmation_code": ["confirmation code", "confirmation_code", "reservationid", "reservation id"],
    "guest_name":        ["guest name", "guest_name", "guest"],
    "guest_phone":       ["guest phone", "guest_phone", "phone", "phone number", "guest phone number"],
    "listing_name":      ["listing", "listing name", "listing_name", "property"],
    "unit_identifier":   [
        "unit",
        "unit identifier",
        "unit number",
        "unit / room",
        "room",
        "room number",
        "room / unit",
        "property no",
        "property number",
    ],
    "checkin":           ["start date", "start_date", "check-in", "check_in", "checkin", "arrival"],
    "checkout":          ["end date", "end_date", "check-out", "check_out", "checkout", "departure"],
    "nights":            ["nights", "# nights", "number of nights", "duration"],
    "guests_count":      ["# guests", "guests", "number of guests", "guest count"],
    "payout_usd":        ["amount", "total payout", "payout", "earnings", "host payout"],
    "status":            ["status", "booking status"],
    "review_rating":     ["review rating", "rating", "guest rating", "review stars", "stars"],
    "review_text":       ["review text", "guest review", "review", "review body"],
    "review_submitted_at": ["review date", "review submitted at", "reviewed at"],
    "repeat_guest_count": ["repeat guest count", "repeat stays", "previous stays"],
}


def _csv_col(headers: list[str], field: str) -> Optional[str]:
    """Find the matching column name in CSV headers for a given field."""
    lower_headers = {h.lower().strip(): h for h in headers}
    for alias in _CSV_ALIASES.get(field, []):
        if alias in lower_headers:
            return lower_headers[alias]
    return None


def _normalize_phone(phone: str | None) -> str:
    """Return a digits-only E.164-like phone string for stable matching."""
    if not phone:
        return ""
    digits = re.sub(r"\D+", "", phone)
    if not digits:
        return ""
    if len(digits) == 10:
        return f"1{digits}"
    return digits


def _reservation_sort_key(res: Reservation, today: date_type) -> tuple[int, int, float]:
    """
    Rank reservations by how relevant they are to a live guest conversation.
    Current stays win, then upcoming, then recent past stays.
    """
    imported_at = res.imported_at.timestamp() if res.imported_at else 0.0
    if res.checkin and res.checkout and res.checkin <= today <= res.checkout:
        return (0, 0, -imported_at)
    if res.checkin and res.checkin >= today:
        return (1, (res.checkin - today).days, -imported_at)
    if res.checkout and res.checkout < today:
        return (2, (today - res.checkout).days, -imported_at)
    return (3, 9999, -imported_at)


def _reservation_context_lines(res: Reservation, cfg: TenantConfig | None = None) -> list[str]:
    """Build a compact context block for chat prompts and logs."""
    lines = [f"Reservation: {res.confirmation_code}"]
    if res.guest_phone:
        lines.append(f"Guest phone: {res.guest_phone}")
    if res.listing_name:
        lines.append(f"Listing: {res.listing_name}")
    if res.unit_identifier:
        lines.append(f"Room / unit / property #: {res.unit_identifier}")
    if res.checkin:
        lines.append(f"Check-in: {res.checkin.strftime('%A, %B %d, %Y')}")
    if res.checkout:
        lines.append(f"Check-out: {res.checkout.strftime('%A, %B %d, %Y')}")
    if res.nights:
        lines.append(f"Nights: {res.nights}")
    if res.guests_count:
        lines.append(f"Guests: {res.guests_count}")
    stay_stage = compute_stay_stage(res)
    if stay_stage:
        lines.append(f"Stay stage: {stay_stage.replace('_', ' ')}")
    today = datetime.now(timezone.utc).date()
    if res.checkin and res.checkout and res.nights and res.checkin <= today <= res.checkout:
        lines.append(f"Guest is on day {(today - res.checkin).days + 1} of {res.nights} nights.")
    if cfg:
        if cfg.early_checkin_policy:
            line = f"Early check-in: {cfg.early_checkin_policy}"
            if cfg.early_checkin_fee:
                line += f" (fee: {cfg.early_checkin_fee})"
            lines.append(line)
        if cfg.late_checkout_policy:
            line = f"Late checkout: {cfg.late_checkout_policy}"
            if cfg.late_checkout_fee:
                line += f" (fee: {cfg.late_checkout_fee})"
            lines.append(line)
        if cfg.pet_policy:
            lines.append(f"Pet policy: {cfg.pet_policy}")
        if cfg.refund_policy:
            lines.append(f"Refund policy: {cfg.refund_policy}")
    return lines


def _reservation_context_text(res: Reservation, cfg: TenantConfig | None = None) -> str:
    return "\n".join(_reservation_context_lines(res, cfg))


def _find_reservation_for_guest_context(
    tenant_id: str,
    db: Session,
    guest_phone: str = "",
    guest_name: str = "",
) -> Optional[Reservation]:
    """Find the most relevant reservation for an inbound guest message."""
    today = datetime.now(timezone.utc).date()
    window_start = today - timedelta(days=30)
    window_end = today + timedelta(days=120)
    phone_digits = _normalize_phone(guest_phone)
    if phone_digits:
        phone_matches = [
            res for res in (
            db.query(Reservation)
            .filter(
                Reservation.tenant_id == tenant_id,
                Reservation.status == "confirmed",
                Reservation.guest_phone.isnot(None),
                Reservation.checkout >= window_start,
                Reservation.checkin <= window_end,
            )
            .all()
            )
            if _normalize_phone(res.guest_phone) == phone_digits
        ]
        if phone_matches:
            phone_matches.sort(key=lambda res: _reservation_sort_key(res, today))
            return phone_matches[0]

    if guest_name:
        name_parts = guest_name.lower().split()
        candidate_rows = (
            db.query(Reservation)
            .filter(
                Reservation.tenant_id == tenant_id,
                Reservation.status == "confirmed",
                Reservation.checkout >= window_start,
                Reservation.checkin <= window_end,
            )
            .all()
        )
        matches: list[Reservation] = []
        for res in candidate_rows:
            db_name_lower = res.guest_name.lower()
            if any(part in db_name_lower or db_name_lower in part for part in name_parts if len(part) > 2):
                matches.append(res)
        if matches:
            matches.sort(key=lambda res: _reservation_sort_key(res, today))
            return matches[0]
    return None


def _draft_channel(draft: Draft) -> str:
    source = (draft.source or "").lower()
    if source in {"whatsapp", "wa"}:
        return "whatsapp"
    if source == "sms":
        return "sms"
    if source == "email":
        return "email"
    if source == "pms":
        return "pms"
    return source or "system"


def _recent_timeline_events(tenant_id: str, reservation: Optional[Reservation], db: Session, limit: int = 10) -> list[GuestTimelineEvent]:
    if not reservation:
        return []
    return (
        db.query(GuestTimelineEvent)
        .filter(
            GuestTimelineEvent.tenant_id == tenant_id,
            GuestTimelineEvent.reservation_id == reservation.id,
        )
        .order_by(GuestTimelineEvent.created_at.desc())
        .limit(limit)
        .all()
    )


def _timeline_memory_context(tenant_id: str, reservation: Optional[Reservation], db: Session) -> str:
    events = _recent_timeline_events(tenant_id, reservation, db)
    return build_conversation_memory(reversed(events), limit=8)


def _record_timeline_event(
    db: Session,
    tenant_id: str,
    reservation: Optional[Reservation],
    event_type: str,
    summary: str,
    *,
    channel: str = "system",
    direction: str = "internal",
    body: str = "",
    draft: Optional[Draft] = None,
    issue: Optional[IssueTicket] = None,
    automation_rule: Optional[AutomationRule] = None,
    payload_json: Optional[dict] = None,
) -> GuestTimelineEvent:
    event = GuestTimelineEvent(
        tenant_id=tenant_id,
        reservation_id=reservation.id if reservation else None,
        draft_id=draft.id if draft else None,
        issue_ticket_id=issue.id if issue else None,
        automation_rule_id=automation_rule.id if automation_rule else None,
        guest_name=reservation.guest_name if reservation else (draft.guest_name if draft else None),
        guest_phone=reservation.guest_phone if reservation else None,
        property_name=reservation.listing_name if reservation else None,
        unit_identifier=reservation.unit_identifier if reservation else None,
        channel=channel,
        direction=direction,
        event_type=event_type,
        summary=summary,
        body=body or None,
        payload_json=payload_json or {},
    )
    db.add(event)
    return event


def _matching_automation_rule(
    tenant_id: str,
    db: Session,
    draft: Draft,
    reservation: Optional[Reservation],
) -> Optional[AutomationRule]:
    rules = (
        db.query(AutomationRule)
        .filter_by(tenant_id=tenant_id, is_active=True)
        .order_by(AutomationRule.priority.asc(), AutomationRule.created_at.asc())
        .all()
    )
    draft_view = {
        "status": draft.status,
        "source": _draft_channel(draft),
        "channel": _draft_channel(draft),
        "msg_type": draft.msg_type,
        "message": draft.message,
        "draft": draft.draft,
        "listing_name": reservation.listing_name if reservation else "",
        "property_name": reservation.listing_name if reservation else "",
        "unit_identifier": reservation.unit_identifier if reservation else "",
        "needs_escalation": draft.msg_type == "escalation",
        "confidence": draft.confidence if draft.confidence is not None else (0.95 if draft.msg_type == "routine" else 0.45),
        "reply_to": draft.reply_to or "",
        "guest_history_score": draft.guest_history_score,
        "guest_sentiment": draft.guest_sentiment,
        "sentiment_score": draft.sentiment_score,
        "stay_stage": draft.stay_stage,
        "policy_conflicts": json.loads(draft.policy_conflicts_json) if draft.policy_conflicts_json else [],
    }
    for rule in rules:
        conditions = rule.conditions_json or {}
        action_mode = (rule.actions_json or {}).get("mode", "auto_send")
        decision = automation_rule_decision(
            {
                "enabled": rule.is_active,
                "status": "active" if rule.is_active else "disabled",
                "channels": conditions.get("channels") or ([rule.channel] if rule.channel != "any" else []),
                "msg_types": conditions.get("msg_types") or [],
                "min_confidence": rule.confidence_threshold,
                "properties": conditions.get("properties") or [],
                "allow_complex": conditions.get("allow_complex", False),
                "allow_negative_sentiment": conditions.get("allow_negative_sentiment", False),
                "min_guest_history_score": conditions.get("min_guest_history_score"),
                "block_keywords": conditions.get("block_keywords") or [],
                "allow_keywords": conditions.get("allow_keywords") or [],
                "stay_stages": conditions.get("stay_stages") or [],
                "requires_approval": False if action_mode in {"review", "escalate"} else action_mode == "review",
            },
            draft_view,
        )
        if decision["should_send"] or decision["reason"] == "rule matched":
            return rule
    return None


def _apply_automation_if_matched(
    db: Session,
    tenant_id: str,
    draft: Draft,
    reservation: Optional[Reservation],
) -> None:
    rule = _matching_automation_rule(tenant_id, db, draft, reservation)
    if not rule:
        return
    draft.automation_rule_id = rule.id
    action_mode = (rule.actions_json or {}).get("mode", "auto_send")
    if action_mode == "auto_send":
        rule.last_triggered_at = datetime.now(timezone.utc)
        _record_timeline_event(
            db,
            tenant_id,
            reservation,
            "automation_rule_matched",
            f"Automation rule matched: {rule.name}",
            channel=_draft_channel(draft),
            draft=draft,
            automation_rule=rule,
            payload_json={"action": action_mode},
        )
        _execute_draft(draft, draft.draft, tenant_id, db, reservation=reservation, automation_rule=rule)
    elif action_mode == "review":
        rule.last_triggered_at = datetime.now(timezone.utc)
        _record_timeline_event(
            db,
            tenant_id,
            reservation,
            "automation_rule_matched",
            f"Automation review rule matched: {rule.name}",
            channel=_draft_channel(draft),
            draft=draft,
            automation_rule=rule,
            payload_json={"action": action_mode},
        )
        db.add(ActivityLog(
            tenant_id=tenant_id,
            event_type="automation_rule_review",
            message=f"Automation review rule matched for {draft.guest_name}: {rule.name}",
        ))
        db.commit()
    elif action_mode == "escalate":
        draft.status = "escalation"
        issue = IssueTicket(
            tenant_id=tenant_id,
            reservation_id=reservation.id if reservation else None,
            property_name=reservation.listing_name if reservation else None,
            unit_identifier=reservation.unit_identifier if reservation else None,
            guest_name=draft.guest_name,
            guest_phone=reservation.guest_phone if reservation else None,
            category="guest_issue",
            priority="high",
            status="open",
            title=f"Escalated guest issue: {draft.guest_name}",
            description=draft.message[:500],
            payload_json={"source": "automation", "rule_id": rule.id},
        )
        db.add(issue)
        db.flush()
        _record_timeline_event(
            db,
            tenant_id,
            reservation,
            "issue_opened",
            issue.title,
            channel=_draft_channel(draft),
            draft=draft,
            issue=issue,
            automation_rule=rule,
            body=draft.message,
            payload_json={"action": action_mode},
        )
        rule.last_triggered_at = datetime.now(timezone.utc)
        db.add(ActivityLog(
            tenant_id=tenant_id,
            event_type="automation_rule_escalation",
            message=f"Automation escalated issue for {draft.guest_name}: {rule.name}",
        ))
        db.commit()


def _upsert_tenant_kpi_snapshot(
    db: Session,
    tenant_id: str,
    kpis: dict,
    open_issues: list[IssueTicket],
    now: datetime,
) -> None:
    period_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    period_end = period_start + timedelta(days=1)
    snapshot = (
        db.query(TenantKpiSnapshot)
        .filter_by(
            tenant_id=tenant_id,
            property_name=None,
            period_start=period_start,
            period_end=period_end,
        )
        .first()
    )
    if not snapshot:
        snapshot = TenantKpiSnapshot(
            tenant_id=tenant_id,
            property_name=None,
            period_start=period_start,
            period_end=period_end,
        )

    draft_kpis = kpis.get("drafts", {})
    reservation_kpis = kpis.get("reservations", {})
    approvals = int(draft_kpis.get("approved", 0) or 0)
    auto_sent = int(draft_kpis.get("auto_sent", 0) or 0)

    snapshot.messages_total = int(draft_kpis.get("total", 0) or 0)
    snapshot.drafts_total = int(draft_kpis.get("total", 0) or 0)
    snapshot.auto_sent_total = auto_sent
    snapshot.approvals_total = approvals
    snapshot.escalations_total = int(draft_kpis.get("escalations", 0) or 0)
    snapshot.open_issues_total = len([issue for issue in open_issues if issue.status != "resolved"])
    snapshot.resolved_issues_total = len([issue for issue in open_issues if issue.status == "resolved"])
    snapshot.avg_response_seconds = (
        float(draft_kpis.get("avg_response_seconds"))
        if draft_kpis.get("avg_response_seconds") is not None else None
    )
    snapshot.automation_rate_pct = float(kpis.get("ops", {}).get("automation_ready_ratio", 0.0) or 0.0)
    snapshot.edit_rate_pct = max(0.0, round(100.0 - float(draft_kpis.get("approval_rate", 0.0) or 0.0), 1))
    snapshot.saved_hours = round((approvals + auto_sent) * 0.08, 2)
    snapshot.payload_json = {
        "drafts": draft_kpis,
        "reservations": reservation_kpis,
        "ops": kpis.get("ops", {}),
        "reviews": {
            "count": reservation_kpis.get("review_count"),
            "avg_rating": reservation_kpis.get("avg_review_rating"),
        },
        "sentiment": {
            "avg_guest": kpis.get("ops", {}).get("avg_guest_sentiment"),
            "avg_review": kpis.get("ops", {}).get("avg_review_sentiment"),
            "positive_feedback": draft_kpis.get("positive_feedback"),
            "negative_feedback": draft_kpis.get("negative_feedback"),
        },
        "captured_at": now.isoformat(),
    }
    db.add(snapshot)
    db.commit()


def _issue_role_queue(issue: IssueTicket, team_members: list[TeamMember]) -> str:
    member_by_id = {member.id: member for member in team_members}
    assignee = member_by_id.get(issue.assigned_to_member_id)
    if assignee and assignee.role:
        return assignee.role
    category = (issue.category or "").lower()
    if category in {"maintenance", "cleaning"}:
        return "maintenance" if category == "maintenance" else "cleaner"
    if category in {"billing", "complaint", "refund"}:
        return "owner"
    return "front_desk"


def _issue_priority_rank(issue: IssueTicket) -> int:
    return {"urgent": 0, "high": 1, "medium": 2, "low": 3}.get((issue.priority or "medium").lower(), 4)


def _parse_date(val: str) -> Optional[date_type]:
    val = val.strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%b %d, %Y", "%d-%b-%Y"):
        try:
            return datetime.strptime(val, fmt).date()
        except ValueError:
            continue
    return None


def _parse_float(val: str) -> Optional[float]:
    try:
        return float(val.strip().replace("$", "").replace(",", "").replace("€", "").replace("£", ""))
    except (ValueError, AttributeError):
        return None


@app.get("/reservations", response_class=HTMLResponse)
def reservations_page(request: Request,
                      page: int = 1,
                      db: Session = Depends(get_db)):
    try:
        tenant_id = get_current_tenant_id(request)
    except HTTPException:
        return _redirect_login()

    tenant = _get_tenant(tenant_id, db)
    cfg = _get_or_create_config(tenant_id, db)
    selected_property = request.query_params.get("property", "").strip()
    all_rows = (
        db.query(Reservation)
        .filter_by(tenant_id=tenant_id)
        .order_by(Reservation.checkin.desc())
        .all()
    )
    property_options = _collect_property_options(cfg, all_rows, [], [], [])
    if selected_property and selected_property not in property_options:
        selected_property = ""
    filtered_rows = [row for row in all_rows if _property_match(selected_property, row.listing_name or "")]
    per_page = 25
    offset = (page - 1) * per_page
    total = len(filtered_rows)
    rows = filtered_rows[offset: offset + per_page]
    row_ids = [row.id for row in rows]
    issue_counts: dict[int, int] = {}
    if row_ids:
        for ticket in db.query(IssueTicket).filter(
            IssueTicket.tenant_id == tenant_id,
            IssueTicket.reservation_id.in_(row_ids),
            IssueTicket.status != "resolved",
        ).all():
            issue_counts[ticket.reservation_id] = issue_counts.get(ticket.reservation_id, 0) + 1

    sync_log = db.query(ReservationSyncLog).filter_by(tenant_id=tenant_id).first()
    recent_batches = (
        db.query(ReservationIntakeBatch)
        .filter_by(tenant_id=tenant_id)
        .order_by(ReservationIntakeBatch.created_at.desc())
        .limit(5)
        .all()
    )
    team_members = (
        db.query(TeamMember)
        .filter_by(tenant_id=tenant_id, is_active=True)
        .order_by(TeamMember.role.asc(), TeamMember.display_name.asc())
        .all()
    )
    team_members = [member for member in team_members if _team_member_matches_property(member, selected_property)]
    csv_stale = False
    if sync_log and sync_log.last_synced:
        last_synced = sync_log.last_synced
        if last_synced.tzinfo is None:
            last_synced = last_synced.replace(tzinfo=timezone.utc)
        csv_stale = (datetime.now(timezone.utc) - last_synced) > timedelta(hours=24)

    # Analytics
    today   = datetime.now(timezone.utc).date()
    month_start = today.replace(day=1)
    month_rows = [
        row for row in filtered_rows
        if row.status == "confirmed" and row.checkin and row.checkin >= month_start
    ]
    month_revenue = sum(r.payout_usd or 0 for r in month_rows)
    month_nights  = sum(r.nights or 0 for r in month_rows)
    days_in_month = 30
    occupancy_pct = round((month_nights / days_in_month) * 100) if month_nights else 0
    upcoming = len([
        row for row in filtered_rows
        if row.status == "confirmed" and row.checkin and row.checkin >= today
    ])
    activation_count = (
        db.query(ArrivalActivation)
        .filter(
            ArrivalActivation.tenant_id == tenant_id,
            ArrivalActivation.status.in_(["active", "pending"]),
        )
        .count()
    )
    review_velocity = compute_review_velocity(filtered_rows)
    avg_review_rating = round(
        sum(float(row.review_rating) for row in filtered_rows if row.review_rating is not None)
        / len([row for row in filtered_rows if row.review_rating is not None]),
        2,
    ) if [row for row in filtered_rows if row.review_rating is not None] else None
    sentiment_summary = _sentiment_summary([], filtered_rows)

    return templates.TemplateResponse("reservations.html", {
        "request":       request,
        "tenant":        tenant,
        "cfg":           cfg,
        "rows":          rows,
        "total":         total,
        "page":          page,
        "per_page":      per_page,
        "pages":         max(1, (total + per_page - 1) // per_page),
        "sync_log":      sync_log,
        "csv_stale":     csv_stale,
        "month_revenue": month_revenue,
        "occupancy_pct": occupancy_pct,
        "upcoming":      upcoming,
        "activation_count": activation_count,
        "team_members": team_members,
        "recent_batches": recent_batches,
        "issue_counts": issue_counts,
        "today":         today,
        "review_velocity": review_velocity,
        "avg_review_rating": avg_review_rating,
        "sentiment_summary": sentiment_summary,
        "selected_property": selected_property,
        "property_options": property_options,
    })


@app.post("/reservations/upload", response_class=HTMLResponse)
async def reservations_upload(
    request:    Request,
    csv_file:   UploadFile = File(...),
    csrf_token: str = Form(None),
    db: Session = Depends(get_db),
):
    try:
        tenant_id = get_current_tenant_id(request)
    except HTTPException:
        return _redirect_login()
    validate_csrf(request, csrf_token)
    rate_limit(f"csv-upload:{tenant_id}", max_requests=20, window_seconds=3600)

    if not csv_file.filename or not csv_file.filename.lower().endswith(".csv"):
        return RedirectResponse("/reservations?error=invalid_file", status_code=302)

    raw_bytes = await csv_file.read(10 * 1024 * 1024 + 1)
    if len(raw_bytes) > 10 * 1024 * 1024:
        return RedirectResponse("/reservations?error=file_too_large", status_code=302)
    # Try UTF-8 then latin-1 (Airbnb sometimes exports in latin-1)
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            text = raw_bytes.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        return RedirectResponse("/reservations?error=encoding", status_code=302)

    reader  = csv.DictReader(io.StringIO(text))
    headers = reader.fieldnames or []
    imported = 0
    skipped  = 0
    batch = ReservationIntakeBatch(
        tenant_id=tenant_id,
        source_kind="csv",
        source_name=csv_file.filename,
        status="processing",
    )
    db.add(batch)
    db.flush()

    for row in reader:
        code_col   = _csv_col(headers, "confirmation_code")
        if not code_col:
            break  # Can't parse without confirmation code
        code = row.get(code_col, "").strip()
        if not code:
            skipped += 1
            continue

        existing = db.query(Reservation).filter_by(
            tenant_id=tenant_id, confirmation_code=code
        ).first()

        def _get(field: str) -> str:
            col = _csv_col(headers, field)
            return row.get(col, "").strip() if col else ""

        checkin_str  = _get("checkin")
        checkout_str = _get("checkout")
        nights_str   = _get("nights")
        guests_str   = _get("guests_count")
        payout_str   = _get("payout_usd")
        review_rating_str = _get("review_rating")
        review_text = _get("review_text")
        review_submitted_at_str = _get("review_submitted_at")
        repeat_guest_count_str = _get("repeat_guest_count")
        status_raw   = _get("status").lower()
        status = "cancelled" if "cancel" in status_raw else "confirmed"

        checkin  = _parse_date(checkin_str)  if checkin_str  else None
        checkout = _parse_date(checkout_str) if checkout_str else None
        nights   = int(nights_str) if nights_str.isdigit() else (
            (checkout - checkin).days if checkin and checkout else None
        )
        guests   = int(guests_str) if guests_str.isdigit() else None
        payout   = _parse_float(payout_str)
        review_rating = _parse_float(review_rating_str)
        review_submitted_at_date = _parse_date(review_submitted_at_str) if review_submitted_at_str else None
        review_submitted_at = (
            datetime.combine(review_submitted_at_date, datetime.min.time(), tzinfo=timezone.utc)
            if review_submitted_at_date else None
        )
        repeat_guest_count = int(repeat_guest_count_str) if repeat_guest_count_str.isdigit() else 0
        review_sentiment = analyze_guest_sentiment(review_text) if review_text else {"label": None, "score": None}

        guest_col   = _csv_col(headers, "guest_name")
        phone_col   = _csv_col(headers, "guest_phone")
        listing_col = _csv_col(headers, "listing_name")
        unit_col    = _csv_col(headers, "unit_identifier")
        guest_phone = row.get(phone_col, "").strip() if phone_col else ""
        unit_id     = row.get(unit_col, "").strip() if unit_col else ""

        if existing:
            existing.status       = status
            existing.payout_usd   = payout   or existing.payout_usd
            existing.guests_count = guests   or existing.guests_count
            existing.intake_batch_id = batch.id
            existing.review_rating = review_rating if review_rating is not None else existing.review_rating
            existing.review_text = review_text or existing.review_text
            existing.review_submitted_at = review_submitted_at or existing.review_submitted_at
            existing.review_sentiment = review_sentiment["label"] or existing.review_sentiment
            existing.review_sentiment_score = (
                review_sentiment["score"] if review_sentiment["score"] is not None else existing.review_sentiment_score
            )
            existing.repeat_guest_count = max(existing.repeat_guest_count or 0, repeat_guest_count)
            if guest_phone:
                existing.guest_phone = _normalize_phone(guest_phone)
            if unit_id:
                existing.unit_identifier = unit_id
            if guest_col:
                existing.guest_name = row.get(guest_col, existing.guest_name).strip() or existing.guest_name
            if listing_col:
                listing_value = row.get(listing_col, "").strip()
                if listing_value:
                    existing.listing_name = listing_value
        else:
            db.add(Reservation(
                tenant_id=tenant_id,
                confirmation_code=code,
                guest_name=(row.get(guest_col, "Guest").strip() if guest_col else "Guest"),
                guest_phone=guest_phone or None,
                listing_name=(row.get(listing_col, "").strip() if listing_col else None),
                unit_identifier=unit_id or None,
                checkin=checkin,
                checkout=checkout,
                nights=nights,
                guests_count=guests,
                payout_usd=payout,
                review_rating=review_rating,
                review_text=review_text or None,
                review_submitted_at=review_submitted_at,
                review_sentiment=review_sentiment["label"],
                review_sentiment_score=review_sentiment["score"],
                repeat_guest_count=repeat_guest_count,
                status=status,
                intake_batch_id=batch.id,
            ))
            imported += 1

    # Update sync log
    sync_log = db.query(ReservationSyncLog).filter_by(tenant_id=tenant_id).first()
    if sync_log:
        sync_log.last_synced   = datetime.now(timezone.utc)
        sync_log.rows_imported = imported
    else:
        db.add(ReservationSyncLog(tenant_id=tenant_id, rows_imported=imported))
    batch.status = "completed"
    batch.rows_total = imported + skipped
    batch.rows_imported = imported
    batch.rows_failed = skipped
    batch.completed_at = datetime.now(timezone.utc)

    db.add(ActivityLog(tenant_id=tenant_id, event_type="csv_imported",
                       message=f"Reservation CSV imported: {imported} new, {skipped} skipped"))
    db.commit()
    return RedirectResponse(f"/reservations?imported={imported}", status_code=302)


@app.post("/reservations/{reservation_id}/context")
def update_reservation_context(
    reservation_id: int,
    request: Request,
    guest_phone: str = Form(""),
    unit_identifier: str = Form(""),
    csrf_token: str = Form(None),
    db: Session = Depends(get_db),
):
    """Attach guest phone and room/unit context to a reservation."""
    try:
        tenant_id = get_current_tenant_id(request)
    except HTTPException:
        return _redirect_login()
    validate_csrf(request, csrf_token)

    res = db.query(Reservation).filter_by(id=reservation_id, tenant_id=tenant_id).first()
    if not res:
        raise HTTPException(status_code=404, detail="Reservation not found")

    res.guest_phone = _normalize_phone(guest_phone) if guest_phone.strip() else None
    res.unit_identifier = unit_identifier.strip() or None
    db.add(ActivityLog(
        tenant_id=tenant_id,
        event_type="reservation_context_updated",
        message=(
            f"Reservation context updated for {res.guest_name} ({res.confirmation_code}): "
            f"phone={res.guest_phone or '—'}, unit={res.unit_identifier or '—'}"
        ),
    ))
    _record_timeline_event(
        db,
        tenant_id,
        res,
        "reservation_context_updated",
        f"Guest context mapped for {res.guest_name}",
        body=f"phone={res.guest_phone or '—'}\nunit={res.unit_identifier or '—'}",
    )
    db.commit()
    redirect_to = "/reservations?context_updated=1"
    selected_property = request.query_params.get("property", "").strip()
    if selected_property:
        redirect_to += f"&property={selected_property}"
    return RedirectResponse(redirect_to, status_code=302)


@app.post("/reservations/manual", response_class=HTMLResponse)
def reservations_manual_create(
    request: Request,
    guest_name: str = Form(...),
    confirmation_code: str = Form(""),
    listing_name: str = Form(""),
    unit_identifier: str = Form(""),
    guest_phone: str = Form(""),
    checkin: str = Form(""),
    checkout: str = Form(""),
    guests_count: str = Form(""),
    notes: str = Form(""),
    csrf_token: str = Form(None),
    db: Session = Depends(get_db),
):
    try:
        tenant_id = get_current_tenant_id(request)
    except HTTPException:
        return _redirect_login()
    validate_csrf(request, csrf_token)

    batch = ReservationIntakeBatch(
        tenant_id=tenant_id,
        source_kind="manual",
        source_name="manual booking",
        status="completed",
        rows_total=1,
        rows_imported=1,
        notes=notes.strip() or None,
        completed_at=datetime.now(timezone.utc),
    )
    db.add(batch)
    db.flush()

    checkin_date = _parse_date(checkin) if checkin.strip() else None
    checkout_date = _parse_date(checkout) if checkout.strip() else None
    reservation = Reservation(
        tenant_id=tenant_id,
        confirmation_code=confirmation_code.strip() or f"MANUAL-{secrets.token_hex(4).upper()}",
        guest_name=guest_name.strip(),
        guest_phone=guest_phone.strip() or None,
        listing_name=listing_name.strip() or None,
        unit_identifier=unit_identifier.strip() or None,
        checkin=checkin_date,
        checkout=checkout_date,
        guests_count=int(guests_count) if guests_count.strip().isdigit() else None,
        status="confirmed",
        intake_batch_id=batch.id,
    )
    db.add(reservation)
    db.flush()
    _record_timeline_event(
        db,
        tenant_id,
        reservation,
        "reservation_manually_created",
        f"Manual booking added for {reservation.guest_name}",
        body=notes.strip(),
        payload_json={"source": "manual"},
    )
    db.add(ActivityLog(
        tenant_id=tenant_id,
        event_type="manual_booking_created",
        message=f"Manual reservation created for {reservation.guest_name}",
    ))
    db.commit()
    return RedirectResponse("/reservations?imported=1", status_code=302)


@app.post("/reservations/{reservation_id}/activate")
def activate_reservation_chat(
    reservation_id: int,
    request: Request,
    guest_phone: str = Form(""),
    unit_identifier: str = Form(""),
    notes: str = Form(""),
    csrf_token: str = Form(None),
    db: Session = Depends(get_db),
):
    try:
        tenant_id = get_current_tenant_id(request)
    except HTTPException:
        return _redirect_login()
    validate_csrf(request, csrf_token)

    reservation = db.query(Reservation).filter_by(id=reservation_id, tenant_id=tenant_id).first()
    if not reservation:
        raise HTTPException(status_code=404, detail="Reservation not found")

    if guest_phone.strip():
        reservation.guest_phone = _normalize_phone(guest_phone)
    if unit_identifier.strip():
        reservation.unit_identifier = unit_identifier.strip()

    activation = ArrivalActivation(
        tenant_id=tenant_id,
        reservation_id=reservation.id,
        property_name=reservation.listing_name,
        unit_identifier=reservation.unit_identifier,
        guest_name=reservation.guest_name,
        guest_phone=reservation.guest_phone,
        activation_source="manual",
        status="active",
        notes=notes.strip() or None,
        activated_at=datetime.now(timezone.utc),
        payload_json={
            "checkin": reservation.checkin.isoformat() if reservation.checkin else "",
            "checkout": reservation.checkout.isoformat() if reservation.checkout else "",
        },
    )
    db.add(activation)
    db.flush()
    _record_timeline_event(
        db,
        tenant_id,
        reservation,
        "arrival_activation",
        f"Arrival activated for {reservation.guest_name}",
        body=notes.strip(),
        payload_json={"activation_id": activation.id},
    )
    db.add(ActivityLog(
        tenant_id=tenant_id,
        event_type="arrival_activation",
        message=f"Guest chat activated for {reservation.guest_name} ({reservation.confirmation_code})",
    ))
    db.commit()
    redirect_to = "/reservations?context_updated=1"
    selected_property = request.query_params.get("property", "").strip()
    if selected_property:
        redirect_to += f"&property={selected_property}"
    return RedirectResponse(redirect_to, status_code=302)


@app.get("/reservations/{reservation_id}/timeline", response_class=HTMLResponse)
def reservation_timeline(
    reservation_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    try:
        tenant_id = get_current_tenant_id(request)
    except HTTPException:
        return _redirect_login()

    tenant = _get_tenant(tenant_id, db)
    reservation = db.query(Reservation).filter_by(id=reservation_id, tenant_id=tenant_id).first()
    if not reservation:
        raise HTTPException(status_code=404, detail="Reservation not found")

    events = (
        db.query(GuestTimelineEvent)
        .filter_by(tenant_id=tenant_id, reservation_id=reservation.id)
        .order_by(GuestTimelineEvent.created_at.asc())
        .all()
    )
    issues = (
        db.query(IssueTicket)
        .filter_by(tenant_id=tenant_id, reservation_id=reservation.id)
        .order_by(IssueTicket.created_at.desc())
        .all()
    )
    team_members = (
        db.query(TeamMember)
        .filter_by(tenant_id=tenant_id, is_active=True)
        .order_by(TeamMember.role.asc(), TeamMember.display_name.asc())
        .all()
    )
    vendors = (
        db.query(Vendor)
        .filter_by(tenant_id=tenant_id)
        .order_by(Vendor.category.asc(), Vendor.name.asc())
        .all()
    )
    return templates.TemplateResponse(
        "guest_timeline.html",
        {
            "request": request,
            "tenant": tenant,
            "reservation": reservation,
            "timeline_events": build_guest_timeline(events, limit=100),
            "open_issues": [issue for issue in issues if issue.status != "resolved"],
            "conversation_memory": build_conversation_memory(events, limit=10),
            "team_members": team_members,
            "vendors": vendors,
        },
    )


@app.post("/reservations/{reservation_id}/issues")
def create_issue_ticket(
    reservation_id: int,
    request: Request,
    title: str = Form(...),
    category: str = Form("general"),
    priority: str = Form("medium"),
    description: str = Form(""),
    assigned_to_member_id: str = Form(""),
    vendor_id: str = Form(""),
    csrf_token: str = Form(None),
    db: Session = Depends(get_db),
):
    try:
        tenant_id = get_current_tenant_id(request)
    except HTTPException:
        return _redirect_login()
    validate_csrf(request, csrf_token)

    reservation = db.query(Reservation).filter_by(id=reservation_id, tenant_id=tenant_id).first()
    if not reservation:
        raise HTTPException(status_code=404, detail="Reservation not found")

    issue = IssueTicket(
        tenant_id=tenant_id,
        reservation_id=reservation.id,
        property_name=reservation.listing_name,
        unit_identifier=reservation.unit_identifier,
        guest_name=reservation.guest_name,
        guest_phone=reservation.guest_phone,
        category=category.strip() or "general",
        priority=priority.strip() or "medium",
        status="open",
        title=title.strip(),
        description=description.strip() or None,
        assigned_to_member_id=int(assigned_to_member_id) if assigned_to_member_id.strip().isdigit() else None,
        vendor_id=int(vendor_id) if vendor_id.strip().isdigit() else None,
    )
    db.add(issue)
    db.flush()
    _record_timeline_event(
        db,
        tenant_id,
        reservation,
        "issue_opened",
        issue.title,
        body=issue.description or "",
        issue=issue,
        payload_json={"priority": issue.priority, "category": issue.category},
    )
    db.add(ActivityLog(
        tenant_id=tenant_id,
        event_type="issue_opened",
        message=f"Issue opened for {reservation.guest_name}: {issue.title}",
    ))
    db.commit()
    return RedirectResponse(f"/reservations/{reservation.id}/timeline", status_code=302)


@app.post("/issues/{issue_id}/update")
def update_issue_ticket(
    issue_id: int,
    request: Request,
    status: str = Form("open"),
    assigned_to_member_id: str = Form(""),
    vendor_id: str = Form(""),
    resolution_notes: str = Form(""),
    next_path: str = Form(""),
    csrf_token: str = Form(None),
    db: Session = Depends(get_db),
):
    try:
        tenant_id = get_current_tenant_id(request)
    except HTTPException:
        return _redirect_login()
    validate_csrf(request, csrf_token)

    issue = db.query(IssueTicket).filter_by(id=issue_id, tenant_id=tenant_id).first()
    if not issue:
        raise HTTPException(status_code=404, detail="Issue not found")

    issue.status = status.strip() or issue.status
    issue.assigned_to_member_id = int(assigned_to_member_id) if assigned_to_member_id.strip().isdigit() else None
    issue.vendor_id = int(vendor_id) if vendor_id.strip().isdigit() else None
    issue.resolution_notes = resolution_notes.strip() or issue.resolution_notes
    if issue.status == "resolved":
        issue.resolved_at = datetime.now(timezone.utc)
    elif issue.status in {"open", "triage", "assigned", "vendor_dispatched"}:
        issue.resolved_at = None

    reservation = None
    if issue.reservation_id:
        reservation = db.query(Reservation).filter_by(id=issue.reservation_id, tenant_id=tenant_id).first()

    assignee_name = issue.assigned_to_member.display_name if issue.assigned_to_member else "Unassigned"
    vendor_name = issue.vendor.name if issue.vendor else "No vendor"
    summary = f"Issue updated: {issue.title}"
    body = f"Status={issue.status}; assignee={assignee_name}; vendor={vendor_name}"
    if issue.resolution_notes:
        body = f"{body}; notes={issue.resolution_notes}"
    _record_timeline_event(
        db,
        tenant_id,
        reservation,
        "issue_updated",
        summary,
        body=body,
        issue=issue,
        payload_json={"status": issue.status},
    )
    db.add(ActivityLog(
        tenant_id=tenant_id,
        event_type="issue_updated",
        message=f"Issue updated: {issue.title} ({issue.status})",
    ))
    db.commit()

    destination = next_path.strip()
    if not destination.startswith("/"):
        destination = f"/reservations/{issue.reservation_id}/timeline" if issue.reservation_id else "/activity"
    return RedirectResponse(destination, status_code=302)


# ---------------------------------------------------------------------------
# Activity log
# ---------------------------------------------------------------------------

@app.get("/activity", response_class=HTMLResponse)
def activity_log(request: Request,
                 db: Session = Depends(get_db),
                 rdb: Session = Depends(get_read_db)):
    try:
        tenant_id = get_current_tenant_id(request)
    except HTTPException:
        return _redirect_login()

    tenant = _get_tenant(tenant_id, db)
    logs   = (rdb.query(ActivityLog).filter_by(tenant_id=tenant_id)  # read replica
              .order_by(ActivityLog.created_at.desc()).limit(200).all())
    timeline_events = (
        rdb.query(GuestTimelineEvent)
        .filter_by(tenant_id=tenant_id)
        .order_by(GuestTimelineEvent.created_at.desc())
        .limit(60)
        .all()
    )
    reservations = rdb.query(Reservation).filter_by(tenant_id=tenant_id).all()
    drafts = rdb.query(Draft).filter_by(tenant_id=tenant_id).order_by(Draft.created_at.desc()).limit(60).all()
    open_issues = (
        db.query(IssueTicket)
        .filter(IssueTicket.tenant_id == tenant_id, IssueTicket.status != "resolved")
        .order_by(IssueTicket.priority.desc(), IssueTicket.created_at.desc())
        .all()
    )
    team_members = (
        db.query(TeamMember)
        .filter_by(tenant_id=tenant_id, is_active=True)
        .order_by(TeamMember.role.asc(), TeamMember.display_name.asc())
        .all()
    )
    vendors = (
        db.query(Vendor)
        .filter_by(tenant_id=tenant_id)
        .order_by(Vendor.category.asc(), Vendor.name.asc())
        .all()
    )
    activations = (
        db.query(ArrivalActivation)
        .filter(ArrivalActivation.tenant_id == tenant_id, ArrivalActivation.status.in_(["pending", "active"]))
        .order_by(ArrivalActivation.created_at.desc())
        .limit(20)
        .all()
    )
    exceptions = surface_exception_queue(drafts, reservations, now=datetime.now(timezone.utc), stale_minutes=60, limit=12)
    return templates.TemplateResponse(
        "activity.html",
        {
            "request": request,
            "tenant": tenant,
            "logs": logs,
            "timeline_events": build_guest_timeline(reversed(timeline_events), limit=60),
            "exceptions": exceptions,
            "open_issues": open_issues,
            "team_members": team_members,
            "vendors": vendors,
            "activations": activations,
        },
    )


@app.get("/analytics", response_class=HTMLResponse)
def analytics_page(request: Request,
                   range_days: int = 30,
                   property: str = "",
                   db: Session = Depends(get_db),
                   rdb: Session = Depends(get_read_db)):
    try:
        tenant_id = get_current_tenant_id(request)
    except HTTPException:
        return _redirect_login()

    tenant = _get_tenant(tenant_id, db)
    cfg = _get_or_create_config(tenant_id, db)

    # Fetch KPI snapshots for date range
    cutoff = datetime.now(timezone.utc) - timedelta(days=range_days)
    snapshots = db.query(TenantKpiSnapshot).filter(
        TenantKpiSnapshot.tenant_id == tenant_id,
        TenantKpiSnapshot.period_start >= cutoff,
    ).order_by(TenantKpiSnapshot.period_start).all()

    # Get current KPIs from pending drafts and reservations
    pending_drafts = rdb.query(Draft).filter_by(tenant_id=tenant_id, status="pending").all()
    all_reservations = rdb.query(Reservation).filter_by(tenant_id=tenant_id).all()
    kpis = derive_dashboard_kpis(pending_drafts, all_reservations)

    # Serialize ORM objects to plain dicts — Jinja2 tojson can't serialize SQLAlchemy models
    snapshots_data = [
        {
            "period_start": s.period_start.isoformat(),
            "period_end":   s.period_end.isoformat(),
            "total_drafts": s.drafts_total,
            "approval_rate": (s.approvals_total / s.drafts_total) if s.drafts_total else 0,
            "approvals_total": s.approvals_total,
            "escalations_total": s.escalations_total,
            "avg_response_seconds": s.avg_response_seconds,
            "automation_rate_pct": s.automation_rate_pct,
            "messages_total": s.messages_total,
            "saved_hours": s.saved_hours,
        }
        for s in snapshots
    ]

    # Guest contact statistics
    all_guest_contacts = rdb.query(GuestContact).filter_by(tenant_id=tenant_id).all()
    guest_contact_stats = {
        "total_guests": len(all_guest_contacts),
        "welcome_sent": len([gc for gc in all_guest_contacts if gc.welcome_status == "sent"]),
        "welcome_pending": len([gc for gc in all_guest_contacts if gc.welcome_status == "pending"]),
        "welcome_failed": len([gc for gc in all_guest_contacts if gc.welcome_status == "failed"]),
        "sent_pct": round((len([gc for gc in all_guest_contacts if gc.welcome_status == "sent"]) / len(all_guest_contacts) * 100) if all_guest_contacts else 0, 1),
    }

    # Auto-send statistics (last 30 days)
    cutoff = datetime.now(timezone.utc) - timedelta(days=range_days)
    auto_sent_count = rdb.query(Draft).filter(
        Draft.tenant_id == tenant_id,
        Draft.status == "auto_sent",
        Draft.created_at >= cutoff,
    ).count()
    pending_count = rdb.query(Draft).filter(
        Draft.tenant_id == tenant_id,
        Draft.status == "pending",
        Draft.created_at >= cutoff,
    ).count()
    total_incoming = auto_sent_count + pending_count + rdb.query(Draft).filter(
        Draft.tenant_id == tenant_id,
        Draft.status.in_(["approved", "skipped", "failed"]),
        Draft.created_at >= cutoff,
    ).count()

    auto_send_stats = {
        "auto_sent": auto_sent_count,
        "pending": pending_count,
        "total": total_incoming,
        "auto_send_pct": round((auto_sent_count / total_incoming * 100) if total_incoming > 0 else 0, 1),
    }

    return templates.TemplateResponse("analytics.html", {
        "request": request,
        "tenant": tenant,
        "cfg": cfg,
        "kpis": kpis,
        "snapshots": snapshots,          # kept for Jinja table rendering
        "snapshots_data": snapshots_data, # JSON-safe for Chart.js
        "range_days": range_days,
        "guest_contact_stats": guest_contact_stats,
        "auto_send_stats": auto_send_stats,
    })


@app.get("/workflow", response_class=HTMLResponse)
def workflow_center(request: Request,
                    db: Session = Depends(get_db),
                    rdb: Session = Depends(get_read_db)):
    try:
        tenant_id = get_current_tenant_id(request)
    except HTTPException:
        return _redirect_login()

    tenant = _get_tenant(tenant_id, db)
    cfg = _get_or_create_config(tenant_id, db)
    now = datetime.now(timezone.utc)
    drafts = (
        rdb.query(Draft)
        .filter_by(tenant_id=tenant_id)
        .order_by(Draft.created_at.desc())
        .limit(80)
        .all()
    )
    reservations = rdb.query(Reservation).filter_by(tenant_id=tenant_id).all()
    rules = (
        rdb.query(AutomationRule)
        .filter_by(tenant_id=tenant_id)
        .order_by(AutomationRule.priority.asc(), AutomationRule.created_at.asc())
        .all()
    )
    issues = (
        rdb.query(IssueTicket)
        .filter(IssueTicket.tenant_id == tenant_id, IssueTicket.status != "resolved")
        .order_by(IssueTicket.created_at.desc())
        .limit(20)
        .all()
    )
    batches = (
        rdb.query(ReservationIntakeBatch)
        .filter_by(tenant_id=tenant_id)
        .order_by(ReservationIntakeBatch.created_at.desc())
        .limit(10)
        .all()
    )
    timeline_events = (
        rdb.query(GuestTimelineEvent)
        .filter_by(tenant_id=tenant_id)
        .order_by(GuestTimelineEvent.created_at.desc())
        .limit(20)
        .all()
    )
    kpis = derive_dashboard_kpis(drafts, reservations, now=now)
    checklist = build_activation_checklist(
        cfg,
        reservations=reservations,
        inbound_email_address=_tenant_inbound_email_address(cfg),
        inbound_webhook_url=f"{APP_BASE_URL}/email/inbound",
    )
    exceptions = surface_exception_queue(drafts, reservations, now=now, stale_minutes=60, limit=12)
    return templates.TemplateResponse(
        "workflow_center.html",
        {
            "request": request,
            "tenant": tenant,
            "cfg": cfg,
            "kpis": kpis,
            "automation_rules": rules,
            "open_issues": issues,
            "recent_batches": batches,
            "timeline_events": build_guest_timeline(reversed(timeline_events), limit=20),
            "activation_checklist": checklist,
            "exception_queue": exceptions,
        },
    )


@app.get("/ops", response_class=HTMLResponse)
def ops_queue(request: Request,
              role: str = "",
              db: Session = Depends(get_db),
              rdb: Session = Depends(get_read_db)):
    try:
        tenant_id = get_current_tenant_id(request)
    except HTTPException:
        return _redirect_login()

    tenant = _get_tenant(tenant_id, db)
    team_members = (
        rdb.query(TeamMember)
        .filter_by(tenant_id=tenant_id, is_active=True)
        .order_by(TeamMember.role.asc(), TeamMember.display_name.asc())
        .all()
    )
    vendors = (
        rdb.query(Vendor)
        .filter_by(tenant_id=tenant_id)
        .order_by(Vendor.category.asc(), Vendor.name.asc())
        .all()
    )
    issues = (
        rdb.query(IssueTicket)
        .filter(IssueTicket.tenant_id == tenant_id, IssueTicket.status != "resolved")
        .order_by(IssueTicket.created_at.desc())
        .all()
    )
    reservations = rdb.query(Reservation).filter_by(tenant_id=tenant_id).all()
    drafts = (
        rdb.query(Draft)
        .filter_by(tenant_id=tenant_id)
        .order_by(Draft.created_at.desc())
        .limit(80)
        .all()
    )
    exceptions = surface_exception_queue(drafts, reservations, now=datetime.now(timezone.utc), stale_minutes=60, limit=20)
    role_counts: dict[str, int] = {"all": len(issues), "unassigned": len([issue for issue in issues if not issue.assigned_to_member_id])}
    for issue in issues:
        queue_name = _issue_role_queue(issue, team_members)
        role_counts[queue_name] = role_counts.get(queue_name, 0) + 1
    selected_role = (role or "all").strip().lower()
    filtered_issues = issues
    if selected_role == "unassigned":
        filtered_issues = [issue for issue in issues if not issue.assigned_to_member_id]
    elif selected_role and selected_role != "all":
        filtered_issues = [issue for issue in issues if _issue_role_queue(issue, team_members) == selected_role]
    filtered_issues.sort(key=lambda issue: (_issue_priority_rank(issue), issue.created_at or datetime.now(timezone.utc)))
    return templates.TemplateResponse(
        "ops_queue.html",
        {
            "request": request,
            "tenant": tenant,
            "selected_role": selected_role,
            "role_counts": role_counts,
            "team_members": team_members,
            "vendors": vendors,
            "issues": filtered_issues,
            "exceptions": exceptions,
        },
    )


@app.get("/vendors/workflow", response_class=HTMLResponse)
def vendor_workflow(request: Request,
                    db: Session = Depends(get_db),
                    rdb: Session = Depends(get_read_db)):
    try:
        tenant_id = get_current_tenant_id(request)
    except HTTPException:
        return _redirect_login()

    tenant = _get_tenant(tenant_id, db)
    vendors = db.query(Vendor).filter_by(tenant_id=tenant_id).order_by(Vendor.category.asc(), Vendor.name.asc()).all()
    team_members = (
        db.query(TeamMember)
        .filter_by(tenant_id=tenant_id, is_active=True)
        .order_by(TeamMember.role.asc(), TeamMember.display_name.asc())
        .all()
    )
    vendor_issues = (
        rdb.query(IssueTicket)
        .filter(
            IssueTicket.tenant_id == tenant_id,
            IssueTicket.category.in_(["maintenance", "cleaning", "guest_request"]),
        )
        .order_by(IssueTicket.created_at.desc())
        .limit(40)
        .all()
    )
    open_vendor_issues = [issue for issue in vendor_issues if issue.status != "resolved"]
    resolved_vendor_issues = [issue for issue in vendor_issues if issue.status == "resolved"][:10]
    return templates.TemplateResponse(
        "vendor_workflow.html",
        {
            "request": request,
            "tenant": tenant,
            "vendors": vendors,
            "team_members": team_members,
            "open_vendor_issues": open_vendor_issues,
            "resolved_vendor_issues": resolved_vendor_issues,
        },
    )


@app.post("/issues/{issue_id}/assign")
def assign_issue_ticket(
    issue_id: int,
    request: Request,
    assigned_to_member_id: str = Form(""),
    vendor_id: str = Form(""),
    status: str = Form("assigned"),
    csrf_token: str = Form(None),
    db: Session = Depends(get_db),
):
    try:
        tenant_id = get_current_tenant_id(request)
    except HTTPException:
        return _redirect_login()
    validate_csrf(request, csrf_token)

    issue = db.query(IssueTicket).filter_by(id=issue_id, tenant_id=tenant_id).first()
    if not issue:
        raise HTTPException(status_code=404, detail="Issue not found")

    issue.assigned_to_member_id = int(assigned_to_member_id) if assigned_to_member_id.strip().isdigit() else None
    issue.vendor_id = int(vendor_id) if vendor_id.strip().isdigit() else None
    issue.status = status.strip() or issue.status
    _record_timeline_event(
        db,
        tenant_id,
        issue.reservation,
        "issue_assigned",
        f"Issue assigned: {issue.title}",
        issue=issue,
        body=f"status={issue.status}",
        payload_json={"assigned_to_member_id": issue.assigned_to_member_id, "vendor_id": issue.vendor_id},
    )
    db.add(ActivityLog(
        tenant_id=tenant_id,
        event_type="issue_assigned",
        message=f"Issue assigned: {issue.title}",
    ))
    db.commit()
    return RedirectResponse(request.headers.get("referer") or "/ops", status_code=302)


@app.post("/issues/{issue_id}/update")
def update_issue_ticket(
    issue_id: int,
    request: Request,
    assigned_to_member_id: str = Form(""),
    vendor_id: str = Form(""),
    status: str = Form("open"),
    resolution_notes: str = Form(""),
    next_path: str = Form(""),
    csrf_token: str = Form(None),
    db: Session = Depends(get_db),
):
    try:
        tenant_id = get_current_tenant_id(request)
    except HTTPException:
        return _redirect_login()
    validate_csrf(request, csrf_token)

    issue = db.query(IssueTicket).filter_by(id=issue_id, tenant_id=tenant_id).first()
    if not issue:
        raise HTTPException(status_code=404, detail="Issue not found")

    issue.assigned_to_member_id = int(assigned_to_member_id) if assigned_to_member_id.strip().isdigit() else None
    issue.vendor_id = int(vendor_id) if vendor_id.strip().isdigit() else None
    issue.status = status.strip() or issue.status
    issue.resolution_notes = resolution_notes.strip() or issue.resolution_notes
    if issue.status == "resolved" and not issue.resolved_at:
        issue.resolved_at = datetime.now(timezone.utc)
    elif issue.status != "resolved":
        issue.resolved_at = None
    _record_timeline_event(
        db,
        tenant_id,
        issue.reservation,
        "issue_updated",
        f"Issue updated: {issue.title}",
        issue=issue,
        body=issue.resolution_notes or "",
        payload_json={"status": issue.status, "vendor_id": issue.vendor_id, "assigned_to_member_id": issue.assigned_to_member_id},
    )
    db.add(ActivityLog(
        tenant_id=tenant_id,
        event_type="issue_updated",
        message=f"Issue updated: {issue.title}",
    ))
    db.commit()
    target = next_path.strip() or request.headers.get("referer") or "/ops"
    return RedirectResponse(target, status_code=302)


@app.post("/issues/{issue_id}/resolve")
def resolve_issue_ticket(
    issue_id: int,
    request: Request,
    resolution_notes: str = Form(""),
    csrf_token: str = Form(None),
    db: Session = Depends(get_db),
):
    try:
        tenant_id = get_current_tenant_id(request)
    except HTTPException:
        return _redirect_login()
    validate_csrf(request, csrf_token)

    issue = db.query(IssueTicket).filter_by(id=issue_id, tenant_id=tenant_id).first()
    if not issue:
        raise HTTPException(status_code=404, detail="Issue not found")

    issue.status = "resolved"
    issue.resolved_at = datetime.now(timezone.utc)
    issue.resolution_notes = resolution_notes.strip() or issue.resolution_notes
    _record_timeline_event(
        db,
        tenant_id,
        issue.reservation,
        "issue_resolved",
        f"Issue resolved: {issue.title}",
        issue=issue,
        body=issue.resolution_notes or "",
    )
    db.add(ActivityLog(
        tenant_id=tenant_id,
        event_type="issue_resolved",
        message=f"Issue resolved: {issue.title}",
    ))
    db.commit()
    return RedirectResponse(request.headers.get("referer") or "/vendors/workflow", status_code=302)


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


@app.get("/api/service-status")
def api_service_status(request: Request):
    """Rich service status for dashboard widget."""
    try:
        tenant_id = get_current_tenant_id(request)
    except HTTPException:
        raise HTTPException(status_code=401)

    ws = worker_manager.worker_status(tenant_id)
    now_ts = time.time()

    def _ago(ts: float | None) -> str | None:
        if ts is None:
            return None
        age = now_ts - ts
        if age < 60:
            return f"{int(age)}s ago"
        if age < 3600:
            return f"{int(age // 60)}m ago"
        return f"{int(age // 3600)}h ago"

    return JSONResponse({
        "email": {
            "running": ws.get("email_running", False),
            "last_ts": None,
            "last_ago": None,
        },
        "calendar": {
            "running": ws.get("cal_running", False),
            "configured": ws.get("cal_configured", False),
            "last_ago": None,
        },
        "whatsapp": {
            "running": False,
            "connected": False,
        },
        "ai": {
            "running": True,
        },
        "checked_at": datetime.now(timezone.utc).isoformat(),
    })


@app.get("/api/sse/drafts")
async def sse_drafts(request: Request, db: Session = Depends(get_db)):
    """Server-Sent Events stream for real-time draft notifications."""
    try:
        tenant_id = get_current_tenant_id(request)
    except HTTPException:
        raise HTTPException(status_code=401, detail="Not authenticated")

    async def event_generator():
        import asyncio
        from web.redis_client import get_redis

        r = get_redis()

        if r:
            # Redis pubsub path
            pubsub = r.pubsub()
            pubsub.subscribe(f"hostai:notify:{tenant_id}")
            try:
                while not await request.is_disconnected():
                    msg = pubsub.get_message(timeout=25)
                    if msg and msg["type"] == "message":
                        data = msg["data"]
                        if isinstance(data, bytes):
                            data = data.decode()
                        yield f"data: {data}\n\n"
                    else:
                        yield ": keepalive\n\n"
                    await asyncio.sleep(0.5)
            finally:
                pubsub.unsubscribe()
        else:
            # Fallback: DB polling every 8s
            last_count = db.query(Draft).filter_by(tenant_id=tenant_id, status="pending").count()
            while not await request.is_disconnected():
                await asyncio.sleep(8)
                count = db.query(Draft).filter_by(tenant_id=tenant_id, status="pending").count()
                if count != last_count:
                    yield f"data: {json.dumps({'type': 'refresh'})}\n\n"
                    last_count = count
                else:
                    yield ": keepalive\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream",
                            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ---------------------------------------------------------------------------
# PMS Integration routes
# ---------------------------------------------------------------------------

@app.post("/settings/pms")
async def pms_settings_save(
    request:    Request,
    pms_type:   str = Form(...),
    api_key:    str = Form(""),
    account_id: str = Form(""),
    base_url:   str = Form(""),
    csrf_token: str = Form(None),
    db: Session = Depends(get_db),
):
    """Save or replace the PMS integration for this tenant."""
    validate_csrf(request, csrf_token)
    try:
        tenant_id = get_current_tenant_id(request)
    except HTTPException:
        return _redirect_login()

    cfg = db.query(TenantConfig).filter_by(tenant_id=tenant_id).first()
    if not cfg:
        raise HTTPException(status_code=404)

    # Require at least free plan (any authenticated user can connect a PMS)
    pms_type = pms_type.strip().lower()
    if pms_type not in ("guesty", "hostaway", "lodgify", "generic"):
        raise HTTPException(status_code=400, detail="Unknown PMS type")
    if not api_key.strip():
        raise HTTPException(status_code=400, detail="API key is required")

    # Deactivate any existing integration of the same type for this tenant
    existing = db.query(PMSIntegration).filter_by(
        tenant_id=tenant_id, pms_type=pms_type
    ).first()
    if existing:
        existing.api_key_enc  = encrypt(api_key.strip())
        existing.account_id   = account_id.strip() or None
        existing.api_base_url = base_url.strip() or None
        existing.is_active    = True
    else:
        db.add(PMSIntegration(
            tenant_id=tenant_id,
            pms_type=pms_type,
            api_key_enc=encrypt(api_key.strip()),
            account_id=account_id.strip() or None,
            api_base_url=base_url.strip() or None,
            is_active=True,
        ))
    db.commit()

    # Restart workers so PMS thread picks up the new config
    worker_manager.restart_worker(tenant_id)

    return RedirectResponse("/settings?saved=pms", status_code=302)


@app.post("/api/pms/test")
async def pms_test_connection(
    request:    Request,
    pms_type:   str = Form(...),
    api_key:    str = Form(""),
    account_id: str = Form(""),
    base_url:   str = Form(""),
    csrf_token: str = Form(None),
):
    """Test a PMS API connection — returns JSON {ok: bool, message: str}."""
    validate_csrf(request, csrf_token)
    try:
        get_current_tenant_id(request)
    except HTTPException:
        raise HTTPException(status_code=401)

    pms_type = pms_type.strip().lower()
    if pms_type not in ("guesty", "hostaway", "lodgify", "generic"):
        return JSONResponse({"ok": False, "message": "Unknown PMS type"})
    if not api_key.strip():
        return JSONResponse({"ok": False, "message": "API key is required"})

    try:
        from web.pms_base import make_adapter
        adapter = make_adapter(pms_type, api_key.strip(),
                               account_id.strip(), base_url.strip())
        ok = adapter.test_connection()
        return JSONResponse({
            "ok": ok,
            "message": "Connection successful!" if ok else "Connection failed — check credentials",
        })
    except Exception as exc:
        log.warning("PMS test_connection error: %s", exc)
        return JSONResponse({"ok": False, "message": str(exc)})


@app.delete("/settings/pms/{integration_id}")
async def pms_delete(
    integration_id: int,
    request:        Request,
    db: Session = Depends(get_db),
):
    """Deactivate (soft-delete) a PMS integration."""
    try:
        tenant_id = get_current_tenant_id(request)
    except HTTPException:
        raise HTTPException(status_code=401)

    integration = db.query(PMSIntegration).filter_by(
        id=integration_id, tenant_id=tenant_id
    ).first()
    if not integration:
        raise HTTPException(status_code=404)

    integration.is_active = False
    db.commit()
    worker_manager.restart_worker(tenant_id)
    return JSONResponse({"ok": True})


@app.get("/api/pms/status")
def pms_status(request: Request, db: Session = Depends(get_db)):
    """Return PMS integration status for the current tenant (used by dashboard)."""
    try:
        tenant_id = get_current_tenant_id(request)
    except HTTPException:
        raise HTTPException(status_code=401)

    integrations = db.query(PMSIntegration).filter_by(
        tenant_id=tenant_id, is_active=True
    ).all()
    return JSONResponse([
        {
            "id":           i.id,
            "pms_type":     i.pms_type,
            "last_synced":  i.last_synced_at.isoformat() if i.last_synced_at else None,
            "created_at":   i.created_at.isoformat(),
        }
        for i in integrations
    ])


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
    draft_text = escape(draft.draft or "")
    selected_property = request.query_params.get("property", "").strip()
    action_suffix = f"?property={escape(selected_property)}" if selected_property else ""
    return HTMLResponse(f"""
    <form method="post" action="/drafts/{draft_id}/edit{action_suffix}" style="margin-top:0.5rem">
      <input type="hidden" name="csrf_token" value="{escape(str(csrf))}">
      <textarea name="edited_text" style="width:100%;padding:8px;border:1px solid #ced4da;border-radius:6px;
        font-size:0.875rem;line-height:1.6;min-height:120px;resize:vertical"
      >{draft_text}</textarea>
      <div style="display:flex;gap:0.5rem;margin-top:0.5rem">
        <button type="submit" class="btn btn-primary btn-sm">Send edited version</button>
      </div>
    </form>
    """)


@app.get("/ping")
def ping():
    """
    Ultra-lightweight liveness probe — no DB hit, no auth.
    Point your uptime monitor (UptimeRobot, BetterStack, etc.) at /ping.
    Responds in <1ms. Use /health for a full dependency check.
    """
    return JSONResponse({"ok": True})


@app.get("/health")
def health(db: Session = Depends(get_db)):
    """Full health check: DB + Redis. Used by load balancers for liveness.
    Returns 503 if DB is down, 200 otherwise (Redis optional).
    Does NOT run during startup — only after app is fully initialized.
    """
    try:
        db.execute(__import__("sqlalchemy").text("SELECT 1"))
        db_ok = True
    except Exception as e:
        log.warning("Health check: DB query failed: %s", e)
        db_ok = False

    from web.redis_client import get_redis
    r = get_redis()
    redis_ok = False
    if r is not None:
        try:
            r.ping()
            redis_ok = True
        except Exception as e:
            log.warning("Health check: Redis ping failed: %s", e)

    status = "ok" if db_ok else "degraded"
    return JSONResponse(
        {"status": status, "db": "ok" if db_ok else "error",
         "redis": "ok" if redis_ok else ("disabled" if r is None else "error")},
        status_code=200 if db_ok else 503,
    )


@app.get("/startup")
def startup_check():
    """Lightweight startup probe — just check the app is running.
    Used during container startup before DB is necessarily ready.
    Returns 200 immediately if the app has loaded.
    """
    return JSONResponse({"status": "starting"}, status_code=200)


def _require_metrics_auth(request: Request) -> None:
    if _IS_DEV_ENV:
        return
    if os.getenv("METRICS_PUBLIC", "").strip().lower() in {"1", "true", "yes", "on"}:
        return
    token = os.getenv("METRICS_TOKEN", "").strip()
    if not token:
        raise HTTPException(status_code=404, detail="Not found")
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        supplied = auth[7:].strip()
    else:
        supplied = request.headers.get("X-Metrics-Token", "").strip()
    if not supplied or not secrets.compare_digest(supplied, token):
        raise HTTPException(status_code=403, detail="Forbidden")


@app.get("/metrics")
def metrics(request: Request, db: Session = Depends(get_db)):
    """Basic operational metrics — JSON format. Protect with your monitoring auth or firewall."""
    _require_metrics_auth(request)
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
# Simulate — generate a demo draft from a fake guest message (onboarding UX)
# ---------------------------------------------------------------------------

@app.post("/api/simulate")
def simulate_guest(request: Request,
                   guest_name: str = Form("Demo Guest"),
                   message:    str = Form("Hi, what time is check-in?"),
                   csrf_token: str = Form(None),
                   db: Session = Depends(get_db)):
    """Generate a simulated guest message draft for onboarding preview."""
    try:
        tenant_id = get_current_tenant_id(request)
    except HTTPException:
        raise HTTPException(status_code=401)
    validate_csrf(request, csrf_token)
    rate_limit(f"simulate:{tenant_id}", max_requests=10, window_seconds=3600)

    cfg = _get_or_create_config(tenant_id, db)

    from web.classifier import (
        classify_message_with_confidence, extract_context_sources, generate_draft, make_draft_id
    )
    import json

    guest_name = guest_name.strip()[:128] or "Demo Guest"
    message    = message.strip()[:2000] or "Hi, what time is check-in?"

    msg_type, confidence, matched = classify_message_with_confidence(message)
    ctx_sources = matched + extract_context_sources(cfg)
    property_context = build_property_context(cfg)

    try:
        draft_text = generate_draft(guest_name, message, msg_type,
                                    property_context=property_context, tenant_id=tenant_id)
    except Exception as exc:
        log.error("[%s] Simulate draft generation failed: %s", tenant_id, exc)
        # Alert admin about missing OpenRouter config
        try:
            send_admin_alert(
                "OpenRouter API key not configured",
                f"A host ({tenant_id}) attempted to generate a draft but the OpenRouter API key is not set. Configure it at /admin/ai."
            )
        except Exception:
            pass  # non-critical
        raise HTTPException(status_code=503, detail="Draft generation is temporarily unavailable. Please try again later.")

    draft_id = make_draft_id("simulate")
    db.add(Draft(
        id=draft_id, tenant_id=tenant_id, source="simulate",
        guest_name=guest_name, message=message,
        reply_to=None, msg_type=msg_type, vendor_type=None,
        draft=draft_text, status="pending",
        confidence=confidence,
        context_sources=json.dumps(ctx_sources),
    ))
    db.commit()
    return RedirectResponse("/dashboard", status_code=302)


# ---------------------------------------------------------------------------
# Team member CRUD (quick-add / deactivate)
# ---------------------------------------------------------------------------

@app.post("/api/team")
def add_team_member(request: Request,
                    display_name: str = Form(...),
                    email:        str = Form(""),
                    phone:        str = Form(""),
                    role:         str = Form("manager"),
                    csrf_token:   str = Form(None),
                    db: Session = Depends(get_db),
                    _=Depends(require_flag("TEAM_MEMBERS"))):
    try:
        tenant_id = get_current_tenant_id(request)
    except HTTPException:
        raise HTTPException(status_code=401)
    validate_csrf(request, csrf_token)
    rate_limit(f"team:{tenant_id}", max_requests=30, window_seconds=3600)

    display_name = display_name.strip()[:128]
    if not display_name:
        raise HTTPException(status_code=400, detail="display_name is required")
    email = email.strip()[:255] or None
    phone = phone.strip()[:32]  or None
    valid_roles = {"owner", "manager", "front_desk", "maintenance", "cleaner"}
    if role not in valid_roles:
        role = "manager"

    member = TeamMember(
        tenant_id=tenant_id,
        display_name=display_name,
        email=email,
        phone=phone,
        role=role,
        is_active=True,
        permissions_json={},
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db.add(member)
    db.commit()
    return RedirectResponse("/dashboard", status_code=302)


@app.delete("/api/team/{member_id}")
def deactivate_team_member(member_id: int, request: Request,
                           db: Session = Depends(get_db)):
    try:
        tenant_id = get_current_tenant_id(request)
    except HTTPException:
        raise HTTPException(status_code=401)
    member = db.query(TeamMember).filter_by(id=member_id, tenant_id=tenant_id).first()
    if not member:
        raise HTTPException(status_code=404)
    member.is_active  = False
    member.updated_at = datetime.now(timezone.utc)
    db.commit()
    return JSONResponse({"ok": True})


# ---------------------------------------------------------------------------
# Weekly digest — manual send trigger
# ---------------------------------------------------------------------------

@app.post("/api/digest/send")
def send_digest(request: Request, db: Session = Depends(get_db)):
    """Trigger an on-demand weekly digest email for the current tenant."""
    try:
        tenant_id = get_current_tenant_id(request)
    except HTTPException:
        raise HTTPException(status_code=401)
    rate_limit(f"digest:{tenant_id}", max_requests=3, window_seconds=86400)

    tenant = _get_tenant(tenant_id, db)
    cfg    = _get_or_create_config(tenant_id, db)

    now  = datetime.now(timezone.utc)
    week_start = now - timedelta(days=7)
    draft_rows = db.query(Draft).filter(
        Draft.tenant_id == tenant_id,
        Draft.created_at >= week_start,
    ).all()
    all_reservations = db.query(Reservation).filter_by(tenant_id=tenant_id).all()
    kpis = derive_dashboard_kpis(draft_rows, all_reservations, now=now)

    stats = {
        "property_name":    (cfg.property_names or "your property").split(",")[0].strip(),
        "week_label":       f"week of {week_start.strftime('%b %d')}",
        **kpis["drafts"],
        **kpis["reservations"],
        "review_velocity":  compute_review_velocity(all_reservations),
    }
    ok = send_weekly_digest(tenant.email, stats)
    if not ok:
        raise HTTPException(status_code=500, detail="Digest email could not be sent. Please try again later.")
    return JSONResponse({"ok": True})


# ---------------------------------------------------------------------------
# Onboarding: prefill listing URL
# ---------------------------------------------------------------------------

@app.post("/api/prefill-listing")
def prefill_listing(request: Request,
                    listing_url: str = Form(...),
                    csrf_token:  str = Form(None),
                    db: Session = Depends(get_db)):
    """
    Accept a public Airbnb/VRBO listing URL and extract the iCal feed URL from it.
    Saves the iCal URL into tenant config so onboarding is faster.
    """
    try:
        tenant_id = get_current_tenant_id(request)
    except HTTPException:
        raise HTTPException(status_code=401)
    validate_csrf(request, csrf_token)
    rate_limit(f"prefill:{tenant_id}", max_requests=10, window_seconds=3600)

    listing_url = listing_url.strip()
    if not listing_url:
        raise HTTPException(status_code=400, detail="listing_url is required")

    # SSRF protection: only allow public internet URLs
    try:
        ensure_public_url(listing_url)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    # Airbnb iCal export pattern: replace /rooms/ URL with /ical/LISTING_ID.ics
    import re as _re
    airbnb_match = _re.search(r"airbnb\.[a-z.]+/rooms/(\d+)", listing_url)
    if airbnb_match:
        listing_id = airbnb_match.group(1)
        ical_url = f"https://www.airbnb.com/calendar/ical/{listing_id}.ics?currency=USD"
        cfg = _get_or_create_config(tenant_id, db)
        existing = [u.strip() for u in (cfg.ical_urls or "").split(",") if u.strip()]
        if ical_url not in existing:
            existing.append(ical_url)
            cfg.ical_urls = ",".join(existing)
            db.commit()
        return JSONResponse({"ok": True, "ical_url": ical_url})

    # For non-Airbnb URLs (e.g. VRBO), just store the URL as-is if it ends with .ics
    if listing_url.endswith(".ics"):
        cfg = _get_or_create_config(tenant_id, db)
        existing = [u.strip() for u in (cfg.ical_urls or "").split(",") if u.strip()]
        if listing_url not in existing:
            existing.append(listing_url)
            cfg.ical_urls = ",".join(existing)
            db.commit()
        return JSONResponse({"ok": True, "ical_url": listing_url})

    raise HTTPException(
        status_code=400,
        detail="Could not extract an iCal URL from the listing. For Airbnb: paste the /rooms/ URL. For other platforms: paste the .ics URL directly."
    )


# ---------------------------------------------------------------------------
# Admin — super-admin panel (configure ADMIN_EMAILS env var)
# ---------------------------------------------------------------------------

import threading as _threading_mod

_PLAN_MRR: dict = {
    "free":       0,
    "baileys":    19,
    "meta_cloud": 29,
    "sms":        19,
    "pro":        49,
}


def _require_admin(request: Request, db: Session) -> Tenant:
    """Returns the authenticated admin Tenant or raises 403."""
    try:
        tenant_id = get_current_tenant_id(request)
    except HTTPException:
        raise HTTPException(status_code=403, detail="Admin access required")
    tenant = db.query(Tenant).filter_by(id=tenant_id).first()
    if (
        not tenant
        or not tenant.is_active
        or not tenant.email_verified
        or tenant.email.lower() not in _ADMIN_EMAILS
    ):
        raise HTTPException(status_code=403, detail="Admin access required")
    return tenant


@app.get("/admin", response_class=HTMLResponse)
def admin_overview(request: Request, db: Session = Depends(get_db)):
    admin = _require_admin(request, db)

    tenants = db.query(Tenant).order_by(Tenant.created_at.desc()).all()
    configs = {c.tenant_id: c for c in db.query(TenantConfig).all()}

    now_utc = datetime.now(timezone.utc)
    thirty_days_ago  = now_utc - timedelta(days=30)
    fourteen_days_ago = now_utc - timedelta(days=14)

    tenant_rows = []
    plan_counts: dict = {}
    mrr = 0
    paid_count = 0

    for t in tenants:
        cfg = configs.get(t.id)
        plan      = cfg.subscription_plan   if cfg else "free"
        sub_status = cfg.subscription_status if cfg else "inactive"
        is_paid   = sub_status in ("active", "trialing") and plan != "free"

        onboarding_complete = cfg.onboarding_complete if cfg else False
        onboarding_step     = cfg.onboarding_step     if cfg else 0

        ws         = worker_manager.worker_status(t.id)
        email_conf = bool(cfg and cfg.imap_host and cfg.email_address)
        worker_dead = email_conf and not ws["email_running"]

        last_log = (
            db.query(ActivityLog)
            .filter_by(tenant_id=t.id)
            .order_by(ActivityLog.created_at.desc())
            .first()
        )
        last_active  = last_log.created_at if last_log else t.created_at
        inactive_14d = last_active < fourteen_days_ago

        tenant_rows.append({
            "tenant":              t,
            "cfg":                 cfg,
            "plan":                plan,
            "sub_status":          sub_status,
            "is_paid":             is_paid,
            "onboarding_complete": onboarding_complete,
            "onboarding_step":     onboarding_step,
            "ws":                  ws,
            "worker_dead":         worker_dead,
            "last_active":         last_active,
            "inactive_14d":        inactive_14d,
        })

        plan_counts[plan] = plan_counts.get(plan, 0) + 1
        if is_paid:
            mrr       += _PLAN_MRR.get(plan, 0)
            paid_count += 1

    # Sort: paid & active first
    tenant_rows.sort(key=lambda r: (not r["is_paid"], -r["tenant"].created_at.timestamp()))

    # Onboarding funnel
    funnel: dict = {str(i): 0 for i in range(6)}
    funnel["complete"] = 0
    for row in tenant_rows:
        if row["onboarding_complete"]:
            funnel["complete"] += 1
        else:
            key = str(row["onboarding_step"])
            funnel[key] = funnel.get(key, 0) + 1

    # Draft quality last 30 days
    drafts_30d = db.query(Draft).filter(Draft.created_at >= thirty_days_ago).all()
    total_d   = len(drafts_30d)
    approved_d = sum(1 for d in drafts_30d if d.status == "approved")
    skipped_d  = sum(1 for d in drafts_30d if d.status == "skipped")
    pending_d  = sum(1 for d in drafts_30d if d.status == "pending")
    edited_d   = sum(
        1 for d in drafts_30d
        if d.status == "approved" and d.final_text and d.final_text != d.draft
    )
    draft_stats = {
        "total":         total_d,
        "approved":      approved_d,
        "skipped":       skipped_d,
        "pending":       pending_d,
        "edited":        edited_d,
        "approval_rate": round(approved_d / total_d * 100, 1) if total_d else 0,
    }

    # Churn signals
    churn_signals = [r for r in tenant_rows if r["worker_dead"] or r["inactive_14d"]]

    # Plan breakdown
    plan_breakdown = []
    for pk in ["free", "baileys", "meta_cloud", "sms", "pro"]:
        cnt = plan_counts.get(pk, 0)
        plan_breakdown.append({
            "plan":         pk,
            "count":        cnt,
            "price":        _PLAN_MRR.get(pk, 0),
            "contribution": cnt * _PLAN_MRR.get(pk, 0),
        })

    return templates.TemplateResponse("admin_overview.html", {
        "request":       request,
        "admin":         admin,
        "tenant_rows":   tenant_rows,
        "total_tenants": len(tenants),
        "paid_count":    paid_count,
        "free_count":    len(tenants) - paid_count,
        "mrr":           mrr,
        "plan_breakdown": plan_breakdown,
        "funnel":        funnel,
        "draft_stats":   draft_stats,
        "churn_signals": churn_signals,
        "now":           now_utc,
    })


@app.get("/admin/tenants/{tid}", response_class=HTMLResponse)
def admin_tenant_detail(tid: str, request: Request, db: Session = Depends(get_db)):
    _require_admin(request, db)
    t = db.query(Tenant).filter_by(id=tid).first()
    if not t:
        raise HTTPException(status_code=404, detail="Tenant not found")
    cfg = db.query(TenantConfig).filter_by(tenant_id=t.id).first()

    all_drafts = db.query(Draft).filter_by(tenant_id=t.id).all()
    draft_stats = {
        "total":    len(all_drafts),
        "pending":  sum(1 for d in all_drafts if d.status == "pending"),
        "approved": sum(1 for d in all_drafts if d.status == "approved"),
        "skipped":  sum(1 for d in all_drafts if d.status == "skipped"),
        "edited":   sum(
            1 for d in all_drafts
            if d.status == "approved" and d.final_text and d.final_text != d.draft
        ),
    }

    reservation_count = db.query(Reservation).filter_by(tenant_id=t.id).count()
    sync_log          = db.query(ReservationSyncLog).filter_by(tenant_id=t.id).first()

    activity_logs = (
        db.query(ActivityLog)
        .filter_by(tenant_id=t.id)
        .order_by(ActivityLog.created_at.desc())
        .limit(30)
        .all()
    )

    ws          = worker_manager.worker_status(t.id)
    last_active = activity_logs[0].created_at if activity_logs else t.created_at
    msg         = request.query_params.get("msg", "")

    return templates.TemplateResponse("admin_tenant.html", {
        "request":           request,
        "t":                 t,
        "cfg":               cfg,
        "draft_stats":       draft_stats,
        "reservation_count": reservation_count,
        "sync_log":          sync_log,
        "activity_logs":     activity_logs,
        "ws":                ws,
        "last_active":       last_active,
        "plans":             ["free", "baileys", "meta_cloud", "sms", "pro"],
        "plan_mrr":          _PLAN_MRR,
        "now":               datetime.now(timezone.utc),
        "msg":               msg,
    })


@app.post("/admin/tenants/{tid}/plan", response_class=HTMLResponse)
def admin_change_plan(
    tid: str, request: Request,
    plan:       str = Form(...),
    sub_status: str = Form(...),
    csrf_token: str = Form(None),
    db: Session = Depends(get_db),
):
    admin = _require_admin(request, db)  # Capture admin identity (Admin safeguard)
    validate_csrf(request, csrf_token)
    t   = db.query(Tenant).filter_by(id=tid).first()
    cfg = db.query(TenantConfig).filter_by(tenant_id=tid).first()
    if not t or not cfg:
        raise HTTPException(status_code=404, detail="Tenant not found")
    if plan not in _PLAN_MRR or sub_status not in ("active", "trialing", "inactive", "cancelled", "past_due"):
        raise HTTPException(status_code=400, detail="Invalid plan or status")
    cfg.subscription_plan   = plan
    cfg.subscription_status = sub_status
    db.commit()
    db.add(ActivityLog(tenant_id=t.id, event_type="admin_plan_change",
                       message=f"Plan set to {plan}/{sub_status} by admin {admin.email}"))  # Include admin email
    db.commit()
    return RedirectResponse(f"/admin/tenants/{tid}?msg=plan_updated", status_code=302)


@app.post("/admin/tenants/{tid}/deactivate", response_class=HTMLResponse)
def admin_deactivate(
    tid: str, request: Request,
    csrf_token: str = Form(None),
    db: Session = Depends(get_db),
):
    admin = _require_admin(request, db)  # Capture admin identity (Admin safeguard)
    validate_csrf(request, csrf_token)
    t = db.query(Tenant).filter_by(id=tid).first()
    if not t:
        raise HTTPException(status_code=404, detail="Tenant not found")
    t.is_active = False
    db.commit()
    worker_manager._stop_tenant(t.id)
    db.add(ActivityLog(tenant_id=t.id, event_type="admin_deactivated",
                       message=f"Account deactivated by admin {admin.email}"))  # Include admin email
    db.commit()
    return RedirectResponse(f"/admin/tenants/{tid}?msg=deactivated", status_code=302)


@app.post("/admin/tenants/{tid}/reactivate", response_class=HTMLResponse)
def admin_reactivate(
    tid: str, request: Request,
    csrf_token: str = Form(None),
    db: Session = Depends(get_db),
):
    admin = _require_admin(request, db)  # Capture admin identity (Admin safeguard)
    validate_csrf(request, csrf_token)
    t = db.query(Tenant).filter_by(id=tid).first()
    if not t:
        raise HTTPException(status_code=404, detail="Tenant not found")
    t.is_active = True
    db.commit()
    worker_manager.restart_worker(t.id)
    db.add(ActivityLog(tenant_id=t.id, event_type="admin_reactivated",
                       message=f"Account reactivated by admin {admin.email}"))  # Include admin email
    db.commit()
    return RedirectResponse(f"/admin/tenants/{tid}?msg=reactivated", status_code=302)


@app.post("/admin/tenants/{tid}/impersonate", response_class=HTMLResponse)
def admin_impersonate(
    tid: str,
    request: Request,
    csrf_token: str = Form(None),
    db: Session = Depends(get_db),
):
    admin = _require_admin(request, db)
    validate_csrf(request, csrf_token)
    t = db.query(Tenant).filter_by(id=tid).first()
    if not t:
        raise HTTPException(status_code=404, detail="Tenant not found")
    admin_token = request.cookies.get("session")
    new_token   = create_token(t.id, tenant_session_version(t))
    is_secure   = is_request_secure(request)
    cfg         = db.query(TenantConfig).filter_by(tenant_id=t.id).first()
    redirect_to = "/dashboard" if (cfg and cfg.onboarding_complete) else "/onboarding"
    resp = RedirectResponse(redirect_to, status_code=302)
    resp.set_cookie("session",       new_token,   httponly=True, samesite="strict",
                    secure=is_secure, max_age=72 * 3600)
    resp.set_cookie("admin_session", admin_token, httponly=True, samesite="strict",
                    secure=is_secure, max_age=72 * 3600)
    db.add(ActivityLog(
        tenant_id=admin.id,
        event_type="admin_impersonate",
        message=f"Impersonated {t.email}",
    ))
    db.add(ActivityLog(
        tenant_id=t.id,
        event_type="admin_impersonated",
        message=f"Admin {admin.email} impersonated this account",
    ))
    db.commit()
    return resp


@app.post("/admin/unimpersonate", response_class=HTMLResponse)
def admin_unimpersonate(
    request: Request,
    csrf_token: str = Form(None),
    db: Session = Depends(get_db),
):
    admin_token = request.cookies.get("admin_session")
    if not admin_token:
        return RedirectResponse("/admin", status_code=302)
    validate_csrf(request, csrf_token)
    is_secure = is_request_secure(request)
    resp = RedirectResponse("/admin", status_code=302)
    resp.set_cookie("session",       admin_token, httponly=True, samesite="strict",
                    secure=is_secure, max_age=72 * 3600)
    resp.delete_cookie("admin_session")
    admin_id = decode_token(admin_token)
    if admin_id:
        admin = db.query(Tenant).filter_by(id=admin_id).first()
    else:
        admin = None
    try:
        impersonated_id = get_current_tenant_id(request)
    except HTTPException:
        impersonated_id = None
    if admin:
        db.add(ActivityLog(
            tenant_id=admin.id,
            event_type="admin_unimpersonate",
            message="Exited impersonation session",
        ))
    if impersonated_id:
        db.add(ActivityLog(
            tenant_id=impersonated_id,
            event_type="admin_unimpersonated",
            message=f"Admin {admin.email if admin else 'unknown'} ended impersonation",
        ))
    if admin or impersonated_id:
        db.commit()
    return resp


@app.get("/admin/system", response_class=HTMLResponse)
def admin_system(request: Request, db: Session = Depends(get_db)):
    _require_admin(request, db)

    tenants = db.query(Tenant).order_by(Tenant.email).all()
    configs = {c.tenant_id: c for c in db.query(TenantConfig).all()}

    system_rows = []
    now_utc = datetime.now(timezone.utc)
    for t in tenants:
        cfg          = configs.get(t.id)
        ws           = worker_manager.worker_status(t.id)
        email_conf   = bool(cfg and cfg.imap_host and cfg.email_address)
        cal_conf     = bool(cfg and cfg.ical_urls)
        any_dead     = (email_conf and not ws["email_running"]) or (cal_conf and not ws["cal_running"])

        # Baileys fix #7: bot status tracking
        bot_online = bool(cfg and cfg.bot_last_heartbeat and
                         (now_utc - cfg.bot_last_heartbeat).total_seconds() < 600)
        baileys_pending = db.query(BaileysOutbound).filter_by(
            tenant_id=t.id, status="pending"
        ).count() if (cfg and cfg.wa_mode == "baileys") else 0
        bot_heartbeat_age_min = None
        if cfg and cfg.bot_last_heartbeat:
            bot_heartbeat_age_min = max(0, (now_utc - cfg.bot_last_heartbeat).total_seconds() // 60)

        system_rows.append({
            "tenant":              t,
            "cfg":                 cfg,
            "ws":                  ws,
            "email_conf":          email_conf,
            "cal_conf":            cal_conf,
            "any_dead":            any_dead,
            "bot_online":          bot_online,
            "baileys_pending":     baileys_pending,
            "wa_mode":             cfg.wa_mode if cfg else None,
            "bot_heartbeat_age_min": bot_heartbeat_age_min,
        })

    system_rows.sort(key=lambda r: (not r["any_dead"], r["tenant"].email))

    watchdog_ok = (worker_manager._watchdog_thread is not None
                   and worker_manager._watchdog_thread.is_alive())

    from web.redis_client import get_redis as _get_redis
    r = _get_redis()
    redis_ok = False
    if r is not None:
        try:
            r.ping()
            redis_ok = True
        except Exception:
            pass

    import sqlalchemy as _sa
    db_ok = True
    try:
        db.execute(_sa.text("SELECT 1"))
    except Exception:
        db_ok = False

    return templates.TemplateResponse("admin_system.html", {
        "request":        request,
        "system_rows":    system_rows,
        "watchdog_ok":    watchdog_ok,
        "redis_ok":       redis_ok,
        "db_ok":          db_ok,
        "thread_count":   _threading_mod.active_count(),
        "total_tenants":  len(tenants),
        "active_workers": sum(1 for r in system_rows if r["ws"]["email_running"]),
        "dead_workers":   sum(1 for r in system_rows if r["any_dead"]),
        "now":            datetime.now(timezone.utc),
    })


@app.get("/admin/ai", response_class=HTMLResponse)
def admin_ai_engine(request: Request, db: Session = Depends(get_db)):
    _require_admin(request, db)

    sys_conf = db.query(SystemConfig).first()
    if not sys_conf:
        sys_conf = SystemConfig()
        db.add(sys_conf)
        db.commit()
        db.refresh(sys_conf)

    usage_logs = []
    total_cost = 0.0

    try:
        usage_logs = db.query(ApiUsageLog).order_by(ApiUsageLog.created_at.desc()).limit(100).all()
        total_cost = sum(log.cost_usd for log in db.query(ApiUsageLog).all())
    except Exception:
        # api_usage_logs table may not exist yet if migrations haven't fully run
        pass

    return templates.TemplateResponse("admin_ai.html", {
        "request": request,
        "sys_conf": sys_conf,
        "usage_logs": usage_logs,
        "total_cost": total_cost,
    })


@app.get("/admin/host-profitability", response_class=HTMLResponse)
def admin_host_profitability(request: Request, db: Session = Depends(get_db)):
    """Per-host profitability breakdown — see revenue, costs, and profit for each tenant."""
    _require_admin(request, db)

    from sqlalchemy.sql import func
    from datetime import timedelta

    # Get all tenants with their subscription info
    configs = db.query(TenantConfig, Tenant).join(Tenant).all()

    # Get API costs per tenant (last 30 days)
    cost_dict = {}
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(days=30)
        costs_30d = db.query(ApiUsageLog.tenant_id, func.sum(ApiUsageLog.cost_usd).label("total_cost"))\
            .filter(ApiUsageLog.created_at >= cutoff)\
            .group_by(ApiUsageLog.tenant_id).all()
        cost_dict = {t_id: float(cost or 0) for t_id, cost in costs_30d}
    except Exception:
        # api_usage_logs table may not exist yet if migrations haven't fully run
        pass

    # Get message counts per tenant (last 30 days)
    msg_counts = db.query(Draft.tenant_id, func.count(Draft.id).label("msg_count"))\
        .filter(Draft.created_at >= cutoff)\
        .group_by(Draft.tenant_id).all()
    msg_dict = {t_id: int(count or 0) for t_id, count in msg_counts}

    # Build per-host metrics
    hosts = []
    for cfg, tenant in configs:
        plan_key = cfg.subscription_plan.lower()

        # Revenue calculation based on new unit-based pricing
        if plan_key == "starter":
            base_revenue = 20.0
            per_unit_revenue = 10.0
        elif plan_key == "growth":
            base_revenue = 30.0
            per_unit_revenue = 9.0
        elif plan_key == "pro":
            base_revenue = 50.0
            per_unit_revenue = 8.0
        else:
            base_revenue = 0.0
            per_unit_revenue = 0.0

        num_units = cfg.num_units or 1
        monthly_revenue = base_revenue + (per_unit_revenue * num_units)

        # Costs
        api_cost = cost_dict.get(cfg.tenant_id, 0.0)
        msg_count = msg_dict.get(cfg.tenant_id, 0)
        infra_cost = 3.0  # $2 hosting + $1 ops per property per month
        total_cost = api_cost + (infra_cost * num_units)

        # Profit
        profit = monthly_revenue - total_cost
        margin_pct = (profit / monthly_revenue * 100) if monthly_revenue > 0 else 0

        hosts.append({
            "tenant_id": cfg.tenant_id,
            "email": tenant.email if tenant else "Unknown",
            "plan": plan_key.title(),
            "units": num_units,
            "revenue": monthly_revenue,
            "api_cost": api_cost,
            "infra_cost": infra_cost * num_units,
            "total_cost": total_cost,
            "profit": profit,
            "margin_pct": margin_pct,
            "messages_30d": msg_count,
            "status": "✅ Profitable" if profit > 0 else "⚠️ Loss",
        })

    # Sort by profit (highest first)
    hosts.sort(key=lambda x: x["profit"], reverse=True)

    # Totals
    total_revenue = sum(h["revenue"] for h in hosts)
    total_cost = sum(h["total_cost"] for h in hosts)
    total_profit = total_revenue - total_cost
    total_margin_pct = (total_profit / total_revenue * 100) if total_revenue > 0 else 0

    return templates.TemplateResponse("admin_host_profitability.html", {
        "request": request,
        "hosts": hosts,
        "total_revenue": total_revenue,
        "total_cost": total_cost,
        "total_profit": total_profit,
        "total_margin_pct": total_margin_pct,
    })


@app.get("/admin/costs", response_class=HTMLResponse)
def admin_costs_dashboard(request: Request, db: Session = Depends(get_db)):
    """Phase 2: Internal Profitability Analysis."""
    _require_admin(request, db)

    configs = db.query(TenantConfig).all()

    # Assume static ARR/MRR for standard plans
    plan_revenue = {"free": 0, "pro": 29, "growth": 79, "enterprise": 1000}

    metrics = {
        "free": {"users": 0, "revenue": 0.0, "cost": 0.0},
        "pro": {"users": 0, "revenue": 0.0, "cost": 0.0},
        "growth": {"users": 0, "revenue": 0.0, "cost": 0.0},
        "enterprise": {"users": 0, "revenue": 0.0, "cost": 0.0},
    }

    from sqlalchemy.sql import func
    cost_dict = {}
    try:
        costs = db.query(ApiUsageLog.tenant_id, func.sum(ApiUsageLog.cost_usd).label("total_cost")).group_by(ApiUsageLog.tenant_id).all()
        cost_dict = {t_id: float(cost or 0) for t_id, cost in costs}
    except Exception:
        # api_usage_logs table may not exist yet if migrations haven't fully run
        pass
    
    for c in configs:
        plan = c.subscription_plan.lower()
        if plan not in metrics:
            if plan in ["baileys", "sms"]:
                plan = "pro"
            else:
                plan = "free"
            
        metrics[plan]["users"] += 1
        metrics[plan]["revenue"] += plan_revenue.get(plan, 0.0)
        metrics[plan]["cost"] += cost_dict.get(c.tenant_id, 0.0)
        
    for p in metrics:
        metrics[p]["margin"] = metrics[p]["revenue"] - metrics[p]["cost"]
        metrics[p]["margin_pct"] = (metrics[p]["margin"] / metrics[p]["revenue"] * 100) if metrics[p]["revenue"] > 0 else 0
        
    total_rev = sum(m["revenue"] for m in metrics.values())
    total_cost = sum(m["cost"] for m in metrics.values())
    total_margin = total_rev - total_cost
    margin_pct = (total_margin / total_rev * 100) if total_rev > 0 else 0
    
    return templates.TemplateResponse("admin_costs.html", {
        "request": request,
        "metrics": metrics,
        "total_rev": total_rev,
        "total_cost": total_cost,
        "total_margin": total_margin,
        "margin_pct": margin_pct,
    })


@app.get("/admin/health_api", response_class=HTMLResponse)
def admin_api_health(request: Request, db: Session = Depends(get_db)):
    """Phase 4: API Health & Performance Monitoring."""
    _require_admin(request, db)

    # Calculate average cost per request and actual monthly cost
    avg_cost = 0.0
    predicted_monthly = 0.0

    try:
        from datetime import timedelta
        now_utc = datetime.now(timezone.utc)
        thirty_days_ago = now_utc - timedelta(days=30)

        # Count total API calls for average
        total_count = db.query(ApiUsageLog).count()
        # Calculate actual cost from last 30 days
        monthly_cost = db.query(func.sum(ApiUsageLog.cost_usd)).filter(
            ApiUsageLog.created_at >= thirty_days_ago
        ).scalar() or 0.0
        # Calculate average cost per call (from all history)
        total_cost = db.query(func.sum(ApiUsageLog.cost_usd)).scalar() or 0.0
        avg_cost = (total_cost / total_count) if total_count > 0 else 0.0
        predicted_monthly = monthly_cost  # actual 30-day cost, not projection
    except Exception:
        # api_usage_logs table may not exist yet if migrations haven't fully run
        pass

    return templates.TemplateResponse("admin_api.html", {
        "request": request,
        "avg_cost": avg_cost,
        "predicted_monthly": predicted_monthly,
    })

@app.post("/admin/ai/save", response_class=HTMLResponse)
def admin_ai_save(
    request: Request,
    openrouter_api_key_enc: str = Form(""),
    primary_model: str = Form(...),
    routine_model: str = Form("google/gemini-2.5-flash"),
    fallback_model: str = Form(...),
    sentiment_model: str = Form("openai/gpt-4o-mini"),
    csrf_token: str = Form(None),
    db: Session = Depends(get_db)
):
    admin = _require_admin(request, db)
    validate_csrf(request, csrf_token)  # Admin safeguard
    sys_conf = db.query(SystemConfig).first()
    if not sys_conf:
        sys_conf = SystemConfig()
        db.add(sys_conf)

    if openrouter_api_key_enc.strip() and openrouter_api_key_enc != "********":
        sys_conf.openrouter_api_key_enc = openrouter_api_key_enc.strip()
    sys_conf.primary_model = primary_model.strip()
    sys_conf.routine_model = routine_model.strip()
    sys_conf.fallback_model = fallback_model.strip()
    sys_conf.sentiment_model = sentiment_model.strip()
    db.commit()

    # Audit log + admin alert
    db.add(ActivityLog(
        tenant_id=admin.id, event_type="admin_ai_config_changed",
        message=f"AI config updated: primary={primary_model} routine={routine_model} fallback={fallback_model} by {admin.email}"
    ))
    db.commit()

    return RedirectResponse("/admin/ai?msg=saved", status_code=302)


# ---------------------------------------------------------------------------
# Bulk draft actions
# ---------------------------------------------------------------------------

@app.post("/drafts/bulk-approve")
def bulk_approve_drafts(
    request: Request,
    csrf_token: str = Form(None),
    db: Session = Depends(get_db),
):
    """Approve all pending drafts for the current tenant at once."""
    try:
        tenant_id = get_current_tenant_id(request)
    except HTTPException:
        return _redirect_login()
    validate_csrf(request, csrf_token)
    rate_limit(f"draft:{tenant_id}", max_requests=120, window_seconds=3600)

    pending = db.query(Draft).filter_by(tenant_id=tenant_id, status="pending").all()
    for draft in pending:
        try:
            _execute_draft(draft, draft.draft, tenant_id, db)
        except Exception as exc:
            log.error("[%s] Bulk approve failed for draft %s: %s", tenant_id, draft.id, exc)
    return RedirectResponse("/dashboard", status_code=302)


@app.post("/drafts/bulk-skip")
def bulk_skip_drafts(
    request: Request,
    csrf_token: str = Form(None),
    db: Session = Depends(get_db),
):
    """Skip (dismiss) all pending drafts for the current tenant at once."""
    try:
        tenant_id = get_current_tenant_id(request)
    except HTTPException:
        return _redirect_login()
    validate_csrf(request, csrf_token)

    pending = db.query(Draft).filter_by(tenant_id=tenant_id, status="pending").all()
    for draft in pending:
        draft.status = "skipped"
    if pending:
        db.add(ActivityLog(
            tenant_id=tenant_id,
            event_type="bulk_skipped",
            message=f"Bulk-skipped {len(pending)} pending draft(s)",
        ))
        db.commit()
    return RedirectResponse("/dashboard", status_code=302)


# ---------------------------------------------------------------------------
# Draft scheduling
# ---------------------------------------------------------------------------

@app.post("/drafts/{draft_id}/schedule")
def schedule_draft(
    draft_id: str,
    request: Request,
    scheduled_at: str = Form(...),
    csrf_token: str = Form(None),
    db: Session = Depends(get_db),
):
    """Set a scheduled send time on a pending draft."""
    try:
        tenant_id = get_current_tenant_id(request)
    except HTTPException:
        return _redirect_login()
    validate_csrf(request, csrf_token)

    draft = db.query(Draft).filter_by(id=draft_id, tenant_id=tenant_id, status="pending").first()
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")

    try:
        parsed = datetime.fromisoformat(scheduled_at)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid datetime format — use ISO 8601")

    draft.scheduled_at = parsed
    db.add(ActivityLog(
        tenant_id=tenant_id,
        event_type="draft_scheduled",
        message=f"Draft scheduled for {parsed.strftime('%Y-%m-%d %H:%M UTC')}: {draft.guest_name}",
    ))
    db.commit()
    redirect_to = "/dashboard"
    selected_property = request.query_params.get("property", "").strip()
    if selected_property:
        redirect_to += f"?property={selected_property}"
    return RedirectResponse(redirect_to, status_code=302)


# ---------------------------------------------------------------------------
# Reservations analytics export (CSV)
# ---------------------------------------------------------------------------

@app.get("/reservations/export.csv")
def reservations_export_csv(
    request: Request,
    db: Session = Depends(get_db),
):
    """Export all reservations as a CSV download."""
    try:
        tenant_id = get_current_tenant_id(request)
    except HTTPException:
        return _redirect_login()

    selected_property = request.query_params.get("property", "").strip()
    rows = (
        db.query(Reservation)
        .filter_by(tenant_id=tenant_id)
        .order_by(Reservation.checkin.desc())
        .all()
    )
    if selected_property:
        rows = [row for row in rows if _property_match(selected_property, row.listing_name or "")]

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "Confirmation Code", "Guest Name", "Guest Phone", "Listing", "Unit / Room",
        "Check-in", "Check-out", "Nights", "Guests", "Payout (USD)", "Review Rating",
        "Review Sentiment", "Status", "Imported At",
    ])
    for r in rows:
        writer.writerow([
            r.confirmation_code,
            r.guest_name,
            r.guest_phone or "",
            r.listing_name or "",
            r.unit_identifier or "",
            r.checkin.isoformat() if r.checkin else "",
            r.checkout.isoformat() if r.checkout else "",
            r.nights or "",
            r.guests_count or "",
            f"{r.payout_usd:.2f}" if r.payout_usd is not None else "",
            r.review_rating if r.review_rating is not None else "",
            r.review_sentiment or "",
            r.status,
            r.imported_at.strftime("%Y-%m-%d %H:%M") if r.imported_at else "",
        ])

    buf.seek(0)
    filename = f"reservations_{datetime.now(timezone.utc).strftime('%Y%m%d')}.csv"
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


# ---------------------------------------------------------------------------
# Settings: FAQ and House Rules PDF upload
# ---------------------------------------------------------------------------

@app.post("/settings/upload-faq")
async def upload_faq_pdf(
    request: Request,
    faq_pdf: UploadFile = File(None),
    csrf_token: str = Form(None),
    db: Session = Depends(get_db),
):
    """Extract text from an uploaded PDF and save it to the faq field."""
    try:
        tenant_id = get_current_tenant_id(request)
    except HTTPException:
        return _redirect_login()
    validate_csrf(request, csrf_token)
    rate_limit(f"settings:{tenant_id}", max_requests=30, window_seconds=3600)

    cfg = _get_or_create_config(tenant_id, db)

    if faq_pdf and faq_pdf.filename:
        try:
            import pdfplumber
            pdf_bytes = await faq_pdf.read(10 * 1024 * 1024 + 1)
            if len(pdf_bytes) > 10 * 1024 * 1024:
                return RedirectResponse("/settings?error=file_too_large", status_code=302)
            with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
                text = "\n".join(page.extract_text() or "" for page in pdf.pages).strip()
            if text:
                cfg.faq = text
                db.add(ActivityLog(
                    tenant_id=tenant_id,
                    event_type="faq_uploaded",
                    message=f"FAQ PDF uploaded: {faq_pdf.filename} ({len(text)} chars extracted)",
                ))
                db.commit()
        except Exception as exc:
            log.warning("[%s] FAQ PDF extraction failed: %s", tenant_id, exc)

    return RedirectResponse("/settings?saved=faq", status_code=302)


@app.post("/settings/upload-house-rules")
async def upload_house_rules_pdf(
    request: Request,
    rules_pdf: UploadFile = File(None),
    csrf_token: str = Form(None),
    db: Session = Depends(get_db),
):
    """Extract text from an uploaded PDF and save it to the house_rules field."""
    try:
        tenant_id = get_current_tenant_id(request)
    except HTTPException:
        return _redirect_login()
    validate_csrf(request, csrf_token)
    rate_limit(f"settings:{tenant_id}", max_requests=30, window_seconds=3600)

    cfg = _get_or_create_config(tenant_id, db)

    if rules_pdf and rules_pdf.filename:
        try:
            import pdfplumber
            pdf_bytes = await rules_pdf.read(10 * 1024 * 1024 + 1)
            if len(pdf_bytes) > 10 * 1024 * 1024:
                return RedirectResponse("/settings?error=file_too_large", status_code=302)
            with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
                text = "\n".join(page.extract_text() or "" for page in pdf.pages).strip()
            if text:
                cfg.house_rules = text
                db.add(ActivityLog(
                    tenant_id=tenant_id,
                    event_type="house_rules_uploaded",
                    message=f"House rules PDF uploaded: {rules_pdf.filename} ({len(text)} chars extracted)",
                ))
                db.commit()
        except Exception as exc:
            log.warning("[%s] House rules PDF extraction failed: %s", tenant_id, exc)

    return RedirectResponse("/settings?saved=rules", status_code=302)


# ---------------------------------------------------------------------------
# Vendor edit
# ---------------------------------------------------------------------------

@app.post("/vendors/{vendor_id}/edit")
def vendor_edit(
    vendor_id: int,
    request: Request,
    name: str = Form(...),
    phone: str = Form(...),
    notes: str = Form(""),
    csrf_token: str = Form(None),
    db: Session = Depends(get_db),
):
    """Update an existing vendor's details."""
    try:
        tenant_id = get_current_tenant_id(request)
    except HTTPException:
        return _redirect_login()
    validate_csrf(request, csrf_token)

    v = db.query(Vendor).filter_by(id=vendor_id, tenant_id=tenant_id).first()
    if v:
        v.name  = name.strip() or v.name
        v.phone = phone.strip() or v.phone
        v.notes = notes.strip() or None
        db.commit()
    return RedirectResponse("/settings#vendors", status_code=302)


# ---------------------------------------------------------------------------
# Guest check-in portal
# ---------------------------------------------------------------------------

@app.post("/reservations/{reservation_id}/checkin-link")
def generate_checkin_link(
    reservation_id: int,
    request: Request,
    csrf_token: str = Form(None),
    db: Session = Depends(get_db),
):
    """Generate (or regenerate) a unique check-in portal link for a reservation."""
    try:
        tenant_id = get_current_tenant_id(request)
    except HTTPException:
        return _redirect_login()
    validate_csrf(request, csrf_token)

    res = db.query(Reservation).filter_by(id=reservation_id, tenant_id=tenant_id).first()
    if not res:
        raise HTTPException(status_code=404, detail="Reservation not found")

    res.checkin_token = secrets.token_urlsafe(32)
    # Token expires 24 hours after checkout (or now + 30 days if checkout date unknown)
    from datetime import timedelta
    from zoneinfo import ZoneInfo
    cfg = db.query(TenantConfig).filter_by(tenant_id=tenant_id).first()
    tz = ZoneInfo(cfg.timezone) if cfg and cfg.timezone else ZoneInfo("UTC")
    if res.checkout:
        res.checkin_token_expires_at = datetime.fromordinal(res.checkout.toordinal()).replace(tzinfo=tz) + timedelta(hours=24)
    else:
        res.checkin_token_expires_at = datetime.now(tz) + timedelta(days=30)

    db.add(ActivityLog(
        tenant_id=tenant_id,
        event_type="checkin_link_generated",
        message=f"Check-in link generated for {res.guest_name} ({res.confirmation_code})",
    ))
    db.commit()
    return RedirectResponse(f"/reservations?checkin_link={res.checkin_token}", status_code=302)


@app.get("/checkin/{token}", response_class=HTMLResponse)
def checkin_portal(token: str, request: Request, db: Session = Depends(get_db)):
    """Public guest check-in page — no auth required, only the token."""
    res = db.query(Reservation).filter_by(checkin_token=token).first()
    if not res:
        raise HTTPException(status_code=404, detail="Check-in link not found or expired")

    # Verify token hasn't expired
    if res.checkin_token_expires_at and datetime.now(timezone.utc) > res.checkin_token_expires_at:
        raise HTTPException(status_code=404, detail="Check-in link has expired")

    cfg = db.query(TenantConfig).filter_by(tenant_id=res.tenant_id).first()
    if not cfg:
        raise HTTPException(status_code=404, detail="Property not found")

    # Parse FAQ into Q&A pairs if formatted as "Q: ...\nA: ..." blocks
    faq_items: list[dict] = []
    if cfg.faq:
        lines = cfg.faq.strip().splitlines()
        current_q = current_a = ""
        for line in lines:
            if line.upper().startswith("Q:") or line.upper().startswith("Q."):
                if current_q:
                    faq_items.append({"q": current_q, "a": current_a.strip()})
                current_q = line[2:].strip()
                current_a = ""
            elif line.upper().startswith("A:") or line.upper().startswith("A."):
                current_a = line[2:].strip()
            elif current_q:
                current_a += " " + line.strip()
        if current_q:
            faq_items.append({"q": current_q, "a": current_a.strip()})

    return templates.TemplateResponse("checkin.html", {
        "request":     request,
        "reservation": res,
        "cfg":         cfg,
        "faq_items":   faq_items,
    })


# ---------------------------------------------------------------------------
# Prometheus metrics endpoint
# ---------------------------------------------------------------------------

@app.get("/metrics/prometheus")
def metrics_prometheus(request: Request, db: Session = Depends(get_db)):
    """
    Prometheus text format metrics endpoint — scrape with Grafana/Prometheus.
    Exposes both time-series counters/histograms (from middleware) and
    point-in-time gauges refreshed on each scrape.

    Protect with METRICS_TOKEN env var or IP allowlist in production.
    """
    _require_metrics_auth(request)
    import threading
    from prometheus_client import Gauge, generate_latest, CONTENT_TYPE_LATEST

    # Point-in-time gauges — refreshed each scrape.
    # Use a module-level cache dict to avoid duplicate-metric errors across scrapes.
    if not hasattr(metrics_prometheus, "_gauges"):
        metrics_prometheus._gauges = {
            "up":          Gauge("hostai_up",                    "Application is up"),
            "db":          Gauge("hostai_db_up",                 "Database reachable"),
            "redis":       Gauge("hostai_redis_up",              "Redis reachable"),
            "tenants":     Gauge("hostai_tenants_total",         "Registered tenants"),
            "pending":     Gauge("hostai_drafts_pending",        "Pending drafts"),
            "approved":    Gauge("hostai_drafts_approved_today", "Drafts approved today"),
            "reservations":Gauge("hostai_reservations_confirmed","Confirmed reservations"),
            "workers":     Gauge("hostai_workers_active",        "Active worker threads"),
            "threads":     Gauge("hostai_threads_total",         "OS thread count"),
            "watchdog":    Gauge("hostai_watchdog_up",           "Watchdog thread alive"),
        }

    g = metrics_prometheus._gauges
    g["up"].set(1)

    try:
        g["tenants"].set(db.query(Tenant).count())
        g["pending"].set(db.query(Draft).filter_by(status="pending").count())
        g["approved"].set(db.query(Draft).filter(
            Draft.status == "approved",
            Draft.approved_at >= datetime.now(timezone.utc).replace(
                hour=0, minute=0, second=0, microsecond=0
            ),
        ).count())
        g["reservations"].set(db.query(Reservation).filter_by(status="confirmed").count())
        g["db"].set(1)
    except Exception:
        g["db"].set(0)

    g["workers"].set(sum(
        1 for tid in list(worker_manager._workers.keys())
        if worker_manager.worker_status(tid)["email_running"]
    ))

    from web.redis_client import get_redis
    r = get_redis()
    redis_val = 0
    if r is not None:
        try:
            r.ping()
            redis_val = 1
        except Exception:
            pass
    g["redis"].set(redis_val)
    g["threads"].set(threading.active_count())
    g["watchdog"].set(int(
        worker_manager._watchdog_thread is not None
        and worker_manager._watchdog_thread.is_alive()
    ))

    # generate_latest() uses the default registry which includes:
    #   - all gauges above
    #   - hostai_http_requests_total (Counter from prometheus_middleware)
    #   - hostai_http_request_duration_seconds (Histogram from prometheus_middleware)
    #   - hostai_messages_sent_total, hostai_drafts_actioned_total, etc.
    output = generate_latest()
    return StreamingResponse(
        iter([output]),
        media_type=CONTENT_TYPE_LATEST,
    )


# ---------------------------------------------------------------------------
# Conversations — view guest message threads
# ---------------------------------------------------------------------------

@app.get("/conversations", response_class=HTMLResponse)
def conversations_page(
    request: Request,
    db: Session = Depends(get_db),
    _=Depends(require_flag("CONVERSATION_VIEW")),
):
    """Display guest conversation threads."""
    try:
        tenant_id = get_current_tenant_id(request)
    except HTTPException:
        raise HTTPException(status_code=401, detail="Unauthorized")

    from datetime import datetime, timezone

    # Get unique conversations grouped by thread_key
    # We'll get the most recent draft for each conversation
    all_drafts = (
        db.query(Draft)
        .filter(Draft.tenant_id == tenant_id, Draft.source.in_(["whatsapp", "sms", "email"]))
        .order_by(Draft.created_at.desc())
        .all()
    )

    # Group by thread_key and guest phone
    conversations = {}
    for draft in all_drafts:
        key = (draft.thread_key, draft.reply_to, draft.guest_name)
        if key not in conversations:
            conversations[key] = {
                "thread_key": draft.thread_key,
                "guest_phone": draft.reply_to,
                "guest_name": draft.guest_name,
                "message_count": 0,
                "last_at": draft.created_at,
            }
        conversations[key]["message_count"] += 1
        conversations[key]["last_at"] = max(conversations[key]["last_at"], draft.created_at)

    # Convert to list and sort by last activity
    conv_list = sorted(conversations.values(), key=lambda x: x["last_at"], reverse=True)

    return templates.TemplateResponse("conversations.html", {
        "request": request,
        "conversations": conv_list,
    })


# ---------------------------------------------------------------------------
# Guest Contacts — bot whitelisting for guests
# ---------------------------------------------------------------------------

@app.get("/guest-contacts", response_class=HTMLResponse)
def guest_contacts_dashboard(
    request: Request,
    db: Session = Depends(get_db),
):
    """Dashboard for managing today's guest check-ins."""
    from datetime import datetime, timezone, timedelta

    try:
        tenant_id = get_current_tenant_id(request)
    except HTTPException:
        raise HTTPException(status_code=401, detail="Unauthorized")

    # Get today's guest contacts
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    today_end = today_start + timedelta(days=1)

    guest_contacts = (
        db.query(GuestContact)
        .filter(
            GuestContact.tenant_id == tenant_id,
            GuestContact.check_in >= today_start,
            GuestContact.check_in < today_end,
        )
        .order_by(GuestContact.check_in.asc())
        .all()
    )

    return templates.TemplateResponse("guest_contacts.html", {
        "request": request,
        "guest_contacts": guest_contacts,
        "today": today_start.date(),
    })


@app.post("/api/guest-contacts/add")
async def add_guest_contact(
    request: Request,
    db: Session = Depends(get_db),
):
    """Create a guest contact and send welcome messages."""
    from web.guest_contact_service import create_guest_contact
    from datetime import datetime, timezone

    try:
        tenant_id = get_current_tenant_id(request)
    except HTTPException:
        raise HTTPException(status_code=401, detail="Unauthorized")

    data = await request.json()

    guest_name = data.get("guest_name", "").strip()
    guest_phone = data.get("guest_phone", "").strip()
    property_name = data.get("property_name", "").strip()
    room_identifier = data.get("room_identifier", "").strip()
    check_in_str = data.get("check_in")  # ISO format
    check_out_str = data.get("check_out")  # ISO format

    if not guest_name or not guest_phone or not check_in_str or not check_out_str:
        raise HTTPException(status_code=400, detail="Missing required fields")

    try:
        check_in = datetime.fromisoformat(check_in_str.replace("Z", "+00:00"))
        check_out = datetime.fromisoformat(check_out_str.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        raise HTTPException(status_code=400, detail="Invalid datetime format")

    try:
        guest_contact = await create_guest_contact(
            tenant_id=tenant_id,
            guest_name=guest_name,
            guest_phone=guest_phone,
            check_in=check_in,
            check_out=check_out,
            property_name=property_name,
            room_identifier=room_identifier,
            db=db,
        )

        return {
            "status": "ok",
            "message": f"Welcome sent to {guest_name}",
            "guest_contact_id": guest_contact.id,
        }

    except Exception as e:
        log.error(f"[{tenant_id}] Error creating guest contact: {e}")
        raise HTTPException(status_code=500, detail="Failed to create guest contact")


@app.get("/api/guest-contacts/today")
def get_todays_guest_contacts(
    request: Request,
    db: Session = Depends(get_db),
):
    """Get guest contacts checking in today."""
    from datetime import datetime, timezone, timedelta

    try:
        tenant_id = get_current_tenant_id(request)
    except HTTPException:
        raise HTTPException(status_code=401, detail="Unauthorized")

    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    today_end = today_start + timedelta(days=1)

    guest_contacts = (
        db.query(GuestContact)
        .filter(
            GuestContact.tenant_id == tenant_id,
            GuestContact.check_in >= today_start,
            GuestContact.check_in < today_end,
        )
        .all()
    )

    return {
        "status": "ok",
        "guest_contacts": [
            {
                "id": gc.id,
                "guest_name": gc.guest_name,
                "guest_phone": gc.guest_phone,
                "property_name": gc.property_name,
                "room_identifier": gc.room_identifier,
                "check_in": gc.check_in.isoformat(),
                "check_out": gc.check_out.isoformat(),
                "status": gc.status,
                "welcome_status": gc.welcome_status,
                "welcome_sent_at": gc.welcome_sent_at.isoformat() if gc.welcome_sent_at else None,
            }
            for gc in guest_contacts
        ],
    }


@app.post("/api/guest-contacts/{gc_id}/resend")
async def resend_guest_welcome(
    gc_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    """Resend welcome message to a guest."""
    try:
        tenant_id = get_current_tenant_id(request)
    except HTTPException:
        raise HTTPException(status_code=401, detail="Unauthorized")

    guest_contact = (
        db.query(GuestContact)
        .filter(GuestContact.id == gc_id, GuestContact.tenant_id == tenant_id)
        .first()
    )
    if not guest_contact:
        raise HTTPException(status_code=404, detail="Guest contact not found")

    try:
        from web.guest_contact_service import send_welcome_messages
        await send_welcome_messages(tenant_id, guest_contact, db)
        return {
            "status": "ok",
            "message": f"Welcome resent to {guest_contact.guest_name}",
        }
    except Exception as e:
        log.error(f"[{tenant_id}] Error resending welcome: {e}")
        raise HTTPException(status_code=500, detail="Failed to resend welcome message")


@app.post("/api/guest-contacts/{gc_id}/edit")
async def edit_guest_contact(
    gc_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    """Edit a guest contact."""
    try:
        tenant_id = get_current_tenant_id(request)
    except HTTPException:
        raise HTTPException(status_code=401, detail="Unauthorized")

    guest_contact = (
        db.query(GuestContact)
        .filter(GuestContact.id == gc_id, GuestContact.tenant_id == tenant_id)
        .first()
    )
    if not guest_contact:
        raise HTTPException(status_code=404, detail="Guest contact not found")

    data = await request.json()

    # Update fields
    guest_contact.guest_name = data.get("guest_name", guest_contact.guest_name).strip()
    guest_contact.guest_phone = data.get("guest_phone", guest_contact.guest_phone).strip()
    guest_contact.property_name = data.get("property_name", guest_contact.property_name or "").strip()
    guest_contact.room_identifier = data.get("room_identifier", guest_contact.room_identifier or "").strip()

    # Update check-in/out if provided
    if "check_in" in data:
        try:
            from datetime import datetime
            check_in_str = data.get("check_in")
            guest_contact.check_in = datetime.fromisoformat(check_in_str.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            raise HTTPException(status_code=400, detail="Invalid check_in datetime format")

    if "check_out" in data:
        try:
            from datetime import datetime
            check_out_str = data.get("check_out")
            guest_contact.check_out = datetime.fromisoformat(check_out_str.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            raise HTTPException(status_code=400, detail="Invalid check_out datetime format")

    db.commit()

    return {
        "status": "ok",
        "message": f"Guest {guest_contact.guest_name} updated",
        "guest_contact": {
            "id": guest_contact.id,
            "guest_name": guest_contact.guest_name,
            "guest_phone": guest_contact.guest_phone,
            "property_name": guest_contact.property_name,
            "room_identifier": guest_contact.room_identifier,
            "check_in": guest_contact.check_in.isoformat(),
            "check_out": guest_contact.check_out.isoformat(),
            "welcome_status": guest_contact.welcome_status,
        },
    }


# ---------------------------------------------------------------------------
# Catch-all 404 handler for unmapped routes
# ---------------------------------------------------------------------------

@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def catch_all_404(request: Request, path: str):
    """Catch-all handler for any unmatched routes (404 errors)"""
    if request.url.path.startswith("/api/"):
        return JSONResponse({"detail": "Not found"}, status_code=404)
    try:
        return templates.TemplateResponse(
            "error.html",
            {
                "request": request,
                "code": 404,
                "title": "Page not found",
                "message": "The page you're looking for doesn't exist."
            },
            status_code=404,
        )
    except Exception as e:
        log.error(f"Error rendering 404 template: {e}")
        return HTMLResponse(
            """<html><head><title>404 - Page Not Found</title></head>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; display: flex; align-items: center; justify-content: center; height: 100vh; margin: 0; background: #f5f5f5;">
<div style="text-align: center; max-width: 600px; padding: 2rem;">
<h1 style="font-size: 3.5rem; font-weight: 800; color: #ddd; margin: 0 0 0.5rem;">404</h1>
<h2 style="font-size: 1.4rem; margin-bottom: 0.75rem;">Page not found</h2>
<p style="color: #666; margin: 0 0 1.5rem; line-height: 1.6;">The page you're looking for doesn't exist.</p>
<a href="/dashboard" style="display: inline-block; padding: 0.6rem 1.25rem; background: #3B82F6; color: white; text-decoration: none; border-radius: 8px; font-weight: 600; margin-right: 0.5rem;">Go to dashboard</a>
<a href="javascript:history.back()" style="display: inline-block; padding: 0.6rem 1.25rem; background: transparent; color: #333; text-decoration: none; border-radius: 8px; font-weight: 600; border: 1px solid #ddd;">Go back</a>
</div>
</body></html>""",
            status_code=404
        )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("web.app:app", host="0.0.0.0", port=port, reload=False)
