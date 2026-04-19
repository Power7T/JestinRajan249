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

  GET  /api/drafts    → JSON list of pending drafts (HTMX polling)
  GET  /api/workers   → worker status JSON
"""

import asyncio
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
import socket
import time
import threading
import traceback
import zipfile
from html import escape
from contextlib import asynccontextmanager
import contextvars
from uuid import uuid4
from datetime import datetime, timezone, timedelta, date as date_type
from typing import Optional
from urllib.parse import urlsplit, urlunsplit
import urllib.request
import urllib.error

import uvicorn
from fastapi import FastAPI, Request, Form, Depends, HTTPException, Header, UploadFile, File, WebSocket, WebSocketDisconnect
from fastapi.responses import (
    HTMLResponse, RedirectResponse, JSONResponse, StreamingResponse, Response
)
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
import sqlalchemy as sa
from sqlalchemy.orm import Session

from web.db import get_db, init_db, SessionLocal
from web.db_read import get_read_db
from web.models import (
    SystemConfig, APIUsageLog,
    Tenant, TenantConfig, Draft, Vendor, ActivityLog,
    Reservation, ReservationSyncLog, ReservationIntakeBatch, GuestContact,
    AutomationRule, TeamMember, GuestTimelineEvent, ArrivalActivation, IssueTicket, TenantKpiSnapshot,
    PMSIntegration, PMSProcessedMessage,
    ProcessedEmail, PlanConfig, VoicePricingConfig, FailedDraftLog,
    VoiceCall, VoiceKnowledgeGap, APIUsageLog, TenantRateLimit, RateLimitCounter, FeatureFlag, FeatureFlagOverride,
    PLAN_FREE, PLAN_META_CLOUD, PLAN_SMS, PLAN_PRO,
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
    validate_csrf, validate_csrf_header, rate_limit, client_ip, is_request_secure,
)
from web.system_config_store import (
    load_system_config,
    missing_system_config_columns,
    save_system_config,
    system_config_schema_is_behind,
)
from web.tenant_config_store import load_tenant_config
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

# SaaS reliability features
from web.phone_utils import normalize_phone, phones_match
from web.idempotency import check_idempotency, store_idempotency_result
from web.rate_limiter import check_rate_limit, increment_rate_limit
from web.cost_tracker import log_api_usage, estimate_cost


def _log_tts_usage(db, tenant_id: str, text: str, audio_bytes: bytes, call_id: str | None = None) -> None:
    """Log TTS usage under the correct provider (google_tts or elevenlabs) with character count.
    Always uses a fresh DB session so a dirty WebSocket/request session cannot cause failures."""
    try:
        from web.integrations.voice import VoiceAIService as _VAS
        from web.db import SessionLocal
        from uuid import uuid4 as _uuid4
        provider = (_VAS.TTS_PROVIDER or "google").lower()
        char_count = len(text)
        if provider == "google":
            service = "google_tts"
            cost = char_count / 1_000_000 * 16.0  # Neural2: $16/1M chars
        else:
            service = "elevenlabs"
            cost = estimate_cost("elevenlabs", "synthesize", characters=char_count)
        with SessionLocal() as fresh_db:
            entry = APIUsageLog(
                id=str(_uuid4()),
                tenant_id=tenant_id,
                call_id=call_id,
                service=service,
                operation="synthesize",
                characters=char_count,
                cost_usd=cost,
                status="success" if audio_bytes else "failed",
                created_at=datetime.now(timezone.utc),
            )
            fresh_db.add(entry)
            fresh_db.commit()
            log.debug("[TTS] Logged %s: %d chars, $%.6f", service, char_count, cost)
    except Exception as exc:
        log.warning("[TTS] Failed to log TTS usage: %s", exc)
from web.timeout_handler import call_with_timeout, TimeoutConfig, FALLBACKS
from web.call_consent import get_consent_prompt, handle_consent_response, should_record_call
from web.feature_flags import is_feature_enabled

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
templates.env.globals["max"] = max
templates.env.globals["min"] = min
templates.env.filters["from_json"] = lambda s: json.loads(s) if s else []


def _fmt_cost(v: float) -> str:
    """Format a USD cost with enough decimal places to show real precision."""
    if v is None:
        return "$0.00"
    v = float(v)
    if v == 0:
        return "$0.00"
    if v < 0.000001:
        return f"${v:.8f}"
    if v < 0.0001:
        return f"${v:.6f}"
    if v < 0.01:
        return f"${v:.5f}"
    if v < 1:
        return f"${v:.4f}"
    return f"${v:.2f}"


templates.env.globals["fmt_cost"] = _fmt_cost

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
GOOGLE_CLIENT_ID     = os.getenv("GOOGLE_CLIENT_ID", "").strip()
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "").strip()

# ---------------------------------------------------------------------------
# App lifecycle
# ---------------------------------------------------------------------------

def _startup_checks() -> None:
    """Warn about common production misconfiguration at startup."""
    if _IS_DEV_ENV:
        return

    warnings: list[str] = []

    # Redis is required for cross-worker rate limiting and Stripe idempotency.
    # Without it, multiple workers can process the same Stripe event multiple times
    # (duplicate subscription creation / double charges).
    workers = int(os.getenv("WORKERS", "2"))
    if workers > 1 and not os.getenv("REDIS_URL", ""):
        log.error(
            f"CRITICAL: REDIS_URL not set but WORKERS={workers}. "
            "Without Redis, Stripe webhook idempotency is not guaranteed across processes. "
            "This could lead to duplicate subscription events. SET REDIS_URL ASAP."
        )

    # STRIPE_SECRET_KEY must be set for any billing operation.
    if not os.getenv("STRIPE_SECRET_KEY", ""):
        warnings.append(
            "STRIPE_SECRET_KEY is not set. All billing and subscription endpoints "
            "will fail at runtime."
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
        from web.models import TenantConfig, Draft, ProcessedEmail, ActivityLog, FailedDraftLog, PMSProcessedMessage
        now = datetime.now(timezone.utc)
        configs = db.query(TenantConfig).all()

        total_deleted = 0
        for cfg in configs:
            cutoff = now - timedelta(days=cfg.data_retention_days)
            tid = cfg.tenant_id

            # Note: BaileysOutbound table cleanup removed (Baileys integration discontinued)
            total_deleted += db.query(Draft).filter(Draft.tenant_id == tid, Draft.created_at < cutoff).delete(synchronize_session=False)
            total_deleted += db.query(ProcessedEmail).filter(ProcessedEmail.tenant_id == tid, ProcessedEmail.created_at < cutoff).delete(synchronize_session=False)
            total_deleted += db.query(ActivityLog).filter(ActivityLog.tenant_id == tid, ActivityLog.created_at < cutoff).delete(synchronize_session=False)
            total_deleted += db.query(FailedDraftLog).filter(FailedDraftLog.tenant_id == tid, FailedDraftLog.created_at < cutoff).delete(synchronize_session=False)
            total_deleted += db.query(PMSProcessedMessage).filter(PMSProcessedMessage.tenant_id == tid, PMSProcessedMessage.created_at < cutoff).delete(synchronize_session=False)

        db.commit()
        log.info("GDPR data retention cleanup: deleted %d old records", total_deleted)
    except Exception as e:
        log.error("Baileys cleanup job failed: %s", str(e))
        db.rollback()
    finally:
        db.close()


# _baileys_retry_stale_job removed — Baileys integration discontinued


def _voice_scheduled_calls_job():
    """
    Daily job: for tenants with voice_scheduled_calls_enabled=True,
    find guests checking in within the next 24 hours and auto-call them
    with a pre-recorded reminder (synthesised via ElevenLabs).
    Runs at 09:00 UTC every day.
    """
    import asyncio
    db = SessionLocal()
    try:
        now       = datetime.now(timezone.utc)
        in_24h    = now + timedelta(hours=24)

        # Tenants that have opted in
        configs = db.query(TenantConfig).filter(
            TenantConfig.voice_scheduled_calls_enabled.is_(True)
        ).all()

        for cfg in configs:
            tenant = db.query(Tenant).filter(Tenant.id == cfg.tenant_id).first()
            if not tenant or not tenant.voice_enabled or not tenant.voice_phone_number:
                continue

            # Guests checking in today or tomorrow
            upcoming = db.query(GuestContact).filter(
                GuestContact.tenant_id == cfg.tenant_id,
                GuestContact.check_in >= now,
                GuestContact.check_in <= in_24h,
                GuestContact.status.in_(["pending", "active"]),
            ).all()

            for guest in upcoming:
                if not guest.guest_phone:
                    continue
                prop = cfg.property_names or "our property"
                checkin_time = cfg.check_in_time or "3:00 PM"
                message = (
                    f"Hello {guest.guest_name.split()[0]}! This is a friendly reminder "
                    f"from {prop}. Your check-in is today at {checkin_time}. "
                    f"If you have any questions, feel free to call this number anytime. "
                    f"We look forward to hosting you!"
                )
                try:
                    # Synthesize reminder message to speech
                    voice_id = cfg.voice_elevenlabs_voice_id if cfg else None
                    audio_bytes, s3_url = asyncio.run(VoiceAIService.synthesize_speech(message, voice_id=voice_id))
                    if not s3_url:
                        log.warning(f"[VOICE] Failed to synthesize audio for {guest.guest_name}")
                        continue

                    # Create outbound Twilio call with audio URL
                    from twilio.rest import Client as TwilioClient
                    from web.crypto import decrypt
                    sid   = cfg.voice_twilio_account_sid
                    token = decrypt(cfg.voice_twilio_auth_token_enc or "")
                    frm   = cfg.voice_twilio_from_number or tenant.voice_phone_number
                    if not (sid and token and frm):
                        continue
                    twilio_client = TwilioClient(sid, token)
                    app_url = os.getenv("APP_BASE_URL", "")
                    call = twilio_client.calls.create(
                        from_=frm,
                        to=guest.guest_phone,
                        url=f"{app_url}/api/calls/outbound-twiml?s3_url={s3_url}",
                    )
                    log.info(f"[VOICE] Scheduled call to {guest.guest_name} ({guest.guest_phone[-4:]}): {call.sid}")

                    # Record the outbound call
                    from web.models import VoiceCall
                    vc = VoiceCall(
                        id=str(uuid4()),
                        tenant_id=cfg.tenant_id,
                        guest_contact_id=guest.id,
                        twilio_call_id=call.sid,
                        twilio_phone_number=frm,
                        guest_phone_number=guest.guest_phone,
                        call_type="scheduled_reminder",
                        status="ringing",
                        created_at=datetime.now(timezone.utc),
                    )
                    db.add(vc)
                    db.commit()

                except Exception as e:
                    log.error(f"[VOICE] Scheduled call failed for {guest.guest_name}: {e}")

        # Process pending callback requests
        pending_callbacks = db.query(VoiceCall).filter(
            VoiceCall.callback_requested.is_(True),
            VoiceCall.callback_at <= now,
            VoiceCall.status == "completed",
        ).all()

        for call in pending_callbacks:
            try:
                tenant = db.query(Tenant).filter(Tenant.id == call.tenant_id).first()
                cfg = tenant.config if tenant else None

                if not tenant or not tenant.voice_enabled or not call.guest_phone_number or not cfg:
                    continue

                guest_name = call.guest_contact.guest_name if call.guest_contact else "Guest"
                message = f"Hi {guest_name.split()[0]}! This is your scheduled callback from us. How can we help?"

                try:
                    voice_id = cfg.voice_elevenlabs_voice_id if cfg else None
                    audio_bytes, s3_url = asyncio.run(VoiceAIService.synthesize_speech(message, voice_id=voice_id))
                    if not s3_url:
                        log.warning(f"[VOICE] Failed to synthesize callback audio for {guest_name}")
                        continue

                    from twilio.rest import Client as TwilioClient
                    from web.crypto import decrypt
                    sid   = cfg.voice_twilio_account_sid
                    token = decrypt(cfg.voice_twilio_auth_token_enc or "")
                    frm   = cfg.voice_twilio_from_number or tenant.voice_phone_number
                    if not (sid and token and frm):
                        continue

                    twilio_client = TwilioClient(sid, token)
                    app_url = os.getenv("APP_BASE_URL", "")
                    callback = twilio_client.calls.create(
                        from_=frm,
                        to=call.guest_phone_number,
                        url=f"{app_url}/api/calls/outbound-twiml?s3_url={s3_url}",
                    )
                    log.info(f"[VOICE] Callback placed to {guest_name} ({call.guest_phone_number[-4:]}): {callback.sid}")

                    # Create new VoiceCall record for callback
                    callback_call = VoiceCall(
                        id=str(uuid4()),
                        tenant_id=call.tenant_id,
                        guest_contact_id=call.guest_contact_id,
                        twilio_call_id=callback.sid,
                        twilio_phone_number=frm,
                        guest_phone_number=call.guest_phone_number,
                        call_type="scheduled_callback",
                        status="ringing",
                        created_at=datetime.now(timezone.utc),
                    )
                    db.add(callback_call)

                    # Mark original callback as processed
                    call.callback_requested = False
                    db.commit()

                except Exception as e:
                    log.error(f"[VOICE] Callback failed for {guest_name}: {e}")
            except Exception as e:
                log.error(f"[VOICE] Error in callback loop: {e}")

        log.info("[VOICE] Scheduled call job completed")
    except Exception as e:
        log.error(f"[VOICE] _voice_scheduled_calls_job error: {e}")
        db.rollback()
    finally:
        db.close()


def _fix_stale_model_ids():
    """Correct known-bad OpenRouter model IDs stored in existing SystemConfig rows."""
    _MODEL_RENAMES = {
        "anthropic/claude-3.5-sonnet": "anthropic/claude-3.7-sonnet",
        "anthropic/claude-3.5-sonnet-20241022": "anthropic/claude-3.7-sonnet",
        "google/gemini-2.5-flash": "google/gemini-2.5-flash",   # already correct
        "google/gemini-flash-1.5": "google/gemini-2.5-flash",
        "meta-llama/llama-3.1-70b-instruct": "meta-llama/llama-3.3-70b-instruct",
    }
    try:
        with SessionLocal() as db:
            sys_conf = load_system_config(db)
            if not sys_conf or system_config_schema_is_behind(sys_conf):
                return
            changed = False
            for old, new in _MODEL_RENAMES.items():
                if sys_conf.primary_model == old:
                    sys_conf.primary_model = new; changed = True
                if sys_conf.routine_model == old:
                    sys_conf.routine_model = new; changed = True
                if sys_conf.fallback_model == old:
                    sys_conf.fallback_model = new; changed = True
                if sys_conf.sentiment_model == old:
                    sys_conf.sentiment_model = new; changed = True
            if changed:
                db.commit()
                log.info("Auto-corrected stale OpenRouter model IDs in SystemConfig")
    except Exception as e:
        log.warning(f"Could not fix stale model IDs: {e}")


async def lifespan(app: FastAPI):
    _startup_checks()
    try:
        init_db()
    except Exception as e:
        log.error(f"Failed to initialize database: {e}")
        # We don't crash here; let the app start so we can show a schema error page instead of a process crash
    _fix_stale_model_ids()
    validate_smtp_config()  # Validate SMTP at startup — fail fast, not on first email send
    worker_manager.start_all_workers()
    scheduler: Optional[BackgroundScheduler] = None
    scheduler_started = False

    if _embedded_scheduler_enabled() and _acquire_scheduler_lock():
        scheduler = BackgroundScheduler()
        scheduler.add_job(
            _gdpr_data_retention_job,
            CronTrigger(hour=2, minute=0, timezone="UTC"),  # Daily at 2:00 AM UTC
            id="gdpr_retention_cleanup",
            name="GDPR Data Retention",
            replace_existing=True,
        )
        scheduler.add_job(
            _voice_scheduled_calls_job,
            CronTrigger(hour=9, minute=0, timezone="UTC"),  # Daily at 9:00 AM UTC
            id="voice_scheduled_calls",
            name="Voice Pre-checkin Calls",
            replace_existing=True,
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
        _start_scheduler_leader_refresh()
        scheduler_started = True
        log.info("Scheduled background jobs started (cleanup at 02:00 UTC daily)")
    else:
        log.info("Embedded scheduler disabled or already running in another process")

    flags.log_state()  # Log all feature flag values at startup
    log.info("Airbnb Host Assistant web app started")
    yield
    if scheduler_started and scheduler is not None:
        scheduler.shutdown()
    _release_scheduler_lock()
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


@app.exception_handler(403)
async def forbidden_error_handler(request: Request, exc: HTTPException):
    """Handle 403 Forbidden errors gracefully."""
    if request.url.path.startswith("/api/"):
        return JSONResponse({"detail": "Forbidden access"}, status_code=403)
    
    nonce = getattr(request.state, "csp_nonce", "")
    return templates.TemplateResponse(
        "error.html",
        {
            "request": request,
            "code": 403,
            "title": "Access denied",
            "message": "You don't have permission to view this page.",
            "csp_nonce": nonce
        },
        status_code=403,
    )


@app.exception_handler(500)
async def server_error_handler(request: Request, exc: Exception):
    """Deeply robust 500 handler — avoids crashing during error rendering."""
    log.error(f"FATAL Exception: {exc}", exc_info=True)
    if request.url.path.startswith("/api/"):
        return JSONResponse({"detail": "Internal server error"}, status_code=500)
    
    # Safely get variables for template context to avoid fallback crashes
    nonce = getattr(request.state, "csp_nonce", "")
    error_msg = str(exc)
    stack = traceback.format_exc()

    try:
        return templates.TemplateResponse(
            "error.html",
            {
                "request": request, 
                "code": 500, 
                "title": "Server error",
                "message": "Something went wrong on our end. Please try again in a moment.",
                "debug_detail": f"{error_msg}\n\n{stack}",
                "csp_nonce": nonce
            },
            status_code=500,
        )
    except Exception as render_err:
        log.error(f"Secondary crash in error handler: {render_err}")
        # Final safety net — NO HTML TEMPLATES
        from html import escape
        return HTMLResponse(
            f"""<html><body style="background:#08090F;color:#e3e1eb;font-family:sans-serif;padding:3rem;text-align:center;">
            <h1 style="font-size:3rem;color:#aec6ff;">500</h1>
            <h2>Internal Server Error</h2>
            <p style="color:#8d909e;">The app could not render its error page. This is usually a template or nonce error.</p>
            <div style="text-align:left;background:#1a1b22;padding:1rem;border-radius:1rem;font-size:0.8rem;margin-top:2rem;">
            <code>{escape(error_msg)}</code>
            </div>
            </body></html>""",
            status_code=500
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
    cfg = load_tenant_config(db, tenant_id, create_if_missing=True)
    if not cfg:
        raise RuntimeError(f"Could not load tenant config for {tenant_id}")
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


# ---------------------------------------------------------------------------
# Security Utilities (CRITICAL/HIGH severity fixes)
# ---------------------------------------------------------------------------

def _mask_token(token: str, keep_chars: int = 4) -> str:
    """Mask sensitive tokens in logs (CRITICAL severity fix #3)."""
    if not token or len(token) <= keep_chars:
        return "***"
    return token[:keep_chars] + "*" * (len(token) - keep_chars)


def _require_tenant_access(tenant_id: str, accessed_tenant_id: str, action: str = "access") -> None:
    """
    Validate that the current tenant can access another resource (CRITICAL severity fix #1).
    Raises 403 if tenant_id doesn't own accessed_tenant_id.
    """
    if tenant_id != accessed_tenant_id:
        log.warning(f"[SECURITY] Tenant isolation bypass attempt: {tenant_id} tried to {action} {accessed_tenant_id}")
        raise HTTPException(status_code=403, detail="Access denied")


def _require_admin_role(member_role: str, action: str = "perform admin action") -> None:
    """
    Validate that member has admin or owner role (CRITICAL severity fix #5).
    Raises 403 if not authorized.
    """
    if member_role not in ("owner", "admin"):
        log.warning(f"[SECURITY] Unauthorized admin attempt: role={member_role}, action={action}")
        raise HTTPException(status_code=403, detail="Only admins can perform this action")


def _audit_log_action(
    db: Session,
    tenant_id: str,
    actor_email: str,
    action: str,
    resource_id: Optional[str] = None,
    details: Optional[str] = None,
) -> None:
    """
    Log administrative or sensitive actions for auditing (HIGH severity fix #10).
    """
    try:
        db.add(ActivityLog(
            tenant_id=tenant_id,
            event_type=f"security_audit:{action}",
            actor_email=actor_email,
            message=f"{action.replace('_', ' ').title()}" + (f" {resource_id}" if resource_id else ""),
            details=details,
            created_at=datetime.now(timezone.utc),
        ))
        db.commit()
        log.info(f"[AUDIT] {tenant_id} | {actor_email} | {action}")
    except Exception as e:
        log.error(f"[AUDIT] Failed to log action {action}: {e}")


def _extract_property_whitelist(cfg: TenantConfig) -> set[str]:
    """
    Extract allowed property names for tenant (CRITICAL severity fix #1).
    Validates query parameters against this set.
    """
    if not cfg or not cfg.property_names:
        return set()
    return set(p.strip() for p in cfg.property_names.split(",") if p.strip())


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


def _percentile(data: list[float], p: float) -> Optional[float]:
    """Calculate percentile of response times. p=50 for p50, p=95 for p95."""
    if not data:
        return None
    sorted_data = sorted(data)
    idx = (p / 100.0) * (len(sorted_data) - 1)
    lower = int(idx)
    upper = min(lower + 1, len(sorted_data) - 1)
    weight = idx - lower
    return round(sorted_data[lower] * (1 - weight) + sorted_data[upper] * weight, 1)


def _response_time_stats(drafts: list[Draft]) -> dict:
    """Compute avg, p50, p95 response times from drafts."""
    durations: list[float] = []
    for draft in drafts:
        if draft.created_at and draft.approved_at and draft.approved_at >= draft.created_at:
            durations.append((draft.approved_at - draft.created_at).total_seconds())

    if not durations:
        return {"avg": 0, "p50": 0, "p95": 0, "count": 0}

    avg = round(sum(durations) / len(durations), 1)
    p50 = _percentile(durations, 50) or 0
    p95 = _percentile(durations, 95) or 0

    return {"avg": avg, "p50": p50, "p95": p95, "count": len(durations)}


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


def _find_team_member_by_invite_token(db: Session, token: str) -> Optional[TeamMember]:
    """Lookup a team member invite token, supporting legacy plaintext rows."""
    token_digest = _token_digest(token)
    member = db.query(TeamMember).filter(TeamMember.invite_token == token_digest).first()
    if member:
        return member
    return db.query(TeamMember).filter(TeamMember.invite_token == token).first()


def _find_reservation_by_checkin_token(db: Session, token: str) -> Optional[Reservation]:
    """Lookup a reservation check-in token, supporting legacy plaintext rows."""
    token_digest = _token_digest(token)
    reservation = db.query(Reservation).filter(Reservation.checkin_token == token_digest).first()
    if reservation:
        return reservation
    return db.query(Reservation).filter(Reservation.checkin_token == token).first()


def _require_authenticated_tenant_actor(request: Request, db: Session, *, action: str) -> Tenant:
    """
    Require an authenticated tenant owner/admin/manager-like actor and return the tenant.
    Accepts owner tenant sessions and privileged team-member sessions.
    """
    try:
        tenant_id = get_current_tenant_id(request)
    except HTTPException:
        tenant_id, _member_id, role = get_current_member(request, db)
        if role not in ("owner", "admin", "manager"):
            raise HTTPException(status_code=403, detail=f"You do not have permission to {action}")

    tenant = db.query(Tenant).filter_by(id=tenant_id).first()
    if not tenant or not tenant.is_active:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return tenant


def _require_internal_webhook_secret(
    request: Request,
    *,
    env_name: str,
    header_name: str = "X-Internal-Webhook-Secret",
) -> None:
    """
    Require a shared-secret header for internal callbacks.
    In development, missing configuration is tolerated to keep local iteration easy.
    """
    configured = os.getenv(env_name, "").strip()
    supplied = request.headers.get(header_name, "").strip()
    if not configured:
        if _IS_DEV_ENV:
            return
        raise HTTPException(status_code=403, detail=f"{env_name} is required in production")
    if not supplied or not secrets.compare_digest(supplied, configured):
        raise HTTPException(status_code=403, detail="Webhook authentication failed")


_SCHEDULER_LEADER_LOCK_KEY = os.getenv("SCHEDULER_LEADER_LOCK_KEY", "hostai:scheduler:leader")
_SCHEDULER_LEADER_LOCK_TTL = int(os.getenv("SCHEDULER_LEADER_LOCK_TTL", "120"))
_SCHEDULER_LEADER_REFRESH_INTERVAL = max(15, _SCHEDULER_LEADER_LOCK_TTL // 3)
_SCHEDULER_LEADER_TOKEN = f"{socket.gethostname()}:{os.getpid()}"
_scheduler_leader_refresh_stop = threading.Event()
_scheduler_leader_refresh_thread: Optional[threading.Thread] = None
_scheduler_lock_owned = False


def _embedded_scheduler_enabled() -> bool:
    raw = os.getenv("RUN_EMBEDDED_SCHEDULER", "").strip().lower()
    if not raw:
        return True
    return raw in {"1", "true", "yes", "on"}


def _acquire_scheduler_lock() -> bool:
    """Ensure only one web process owns APScheduler jobs when Redis is available."""
    global _scheduler_lock_owned
    from web.redis_client import get_redis

    redis_client = get_redis()
    if redis_client is None:
        _scheduler_lock_owned = True
        return True
    try:
        acquired = bool(redis_client.set(_SCHEDULER_LEADER_LOCK_KEY, _SCHEDULER_LEADER_TOKEN, nx=True, ex=_SCHEDULER_LEADER_LOCK_TTL))
        if acquired:
            _scheduler_lock_owned = True
            return True
        owner = redis_client.get(_SCHEDULER_LEADER_LOCK_KEY)
        if owner == _SCHEDULER_LEADER_TOKEN:
            redis_client.expire(_SCHEDULER_LEADER_LOCK_KEY, _SCHEDULER_LEADER_LOCK_TTL)
            _scheduler_lock_owned = True
            return True
        log.info("Embedded scheduler already owned by %s; skipping duplicate startup", owner)
        _scheduler_lock_owned = False
        return False
    except Exception as exc:
        log.warning("Scheduler leader lock unavailable (%s); continuing without coordination", exc)
        _scheduler_lock_owned = True
        return True


def _scheduler_leader_refresh_loop():
    from web.redis_client import get_redis

    redis_client = get_redis()
    if redis_client is None:
        return
    while not _scheduler_leader_refresh_stop.wait(timeout=_SCHEDULER_LEADER_REFRESH_INTERVAL):
        try:
            if redis_client.get(_SCHEDULER_LEADER_LOCK_KEY) == _SCHEDULER_LEADER_TOKEN:
                redis_client.expire(_SCHEDULER_LEADER_LOCK_KEY, _SCHEDULER_LEADER_LOCK_TTL)
            else:
                log.warning("Lost scheduler leadership; no longer refreshing lock")
                return
        except Exception as exc:
            log.warning("Scheduler leader lock refresh failed: %s", exc)


def _start_scheduler_leader_refresh():
    global _scheduler_leader_refresh_thread
    from web.redis_client import get_redis

    if get_redis() is None:
        return
    _scheduler_leader_refresh_stop.clear()
    _scheduler_leader_refresh_thread = threading.Thread(
        target=_scheduler_leader_refresh_loop,
        name="scheduler-leader-refresh",
        daemon=True,
    )
    _scheduler_leader_refresh_thread.start()


def _release_scheduler_lock():
    global _scheduler_lock_owned, _scheduler_leader_refresh_thread
    from web.redis_client import get_redis

    _scheduler_leader_refresh_stop.set()
    if _scheduler_leader_refresh_thread and _scheduler_leader_refresh_thread.is_alive():
        _scheduler_leader_refresh_thread.join(timeout=5)
    _scheduler_leader_refresh_thread = None

    redis_client = get_redis()
    if redis_client is None or not _scheduler_lock_owned:
        _scheduler_lock_owned = False
        return
    try:
        if redis_client.get(_SCHEDULER_LEADER_LOCK_KEY) == _SCHEDULER_LEADER_TOKEN:
            redis_client.delete(_SCHEDULER_LEADER_LOCK_KEY)
    except Exception as exc:
        log.warning("Failed to release scheduler leader lock: %s", exc)
    _scheduler_lock_owned = False


# _auth_bot and _bot_token_expiry_warning removed — Baileys integration discontinued


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


def _validate_twilio_signature(
    request: Request,
    form_data: dict,
    cfg: TenantConfig,
    *,
    channel: str = "sms",
) -> bool:
    """
    Validate Twilio webhook signatures against the tenant's auth token.
    """
    if channel == "voice":
        auth_token = decrypt(cfg.voice_twilio_auth_token_enc or cfg.twilio_auth_token_enc or "").strip()
    else:
        auth_token = decrypt(cfg.twilio_auth_token_enc or "").strip()
    if not auth_token:
        if _IS_DEV_ENV:
            return True
        log.error("[%s] Twilio %s auth token missing; rejecting webhook", cfg.tenant_id, channel)
        return False

    try:
        from twilio.request_validator import RequestValidator
        validator = RequestValidator(auth_token)
        signature = request.headers.get("X-Twilio-Signature", "")
        candidate_urls = [_public_request_url(request), str(request.url)]
        return any(validator.validate(url, form_data, signature) for url in dict.fromkeys(candidate_urls))
    except Exception as exc:
        log.warning("[%s] Twilio %s webhook validation error: %s", cfg.tenant_id, channel, exc)
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
def login_page(request: Request, db: Session = Depends(get_db)):
    tenant = None
    try:
        tenant_id = get_current_tenant_id(request)
        tenant = _get_tenant(tenant_id, db)
    except Exception as exc:
        log.debug("login_page: No valid session [%s]", exc)

    try:
        return templates.TemplateResponse("login.html", {"request": request, "error": None, "tenant": tenant})
    except Exception as exc:
        log.error("Failed to render login template: %s\n%s", exc, traceback.format_exc())
        raise


@app.post("/login", response_class=HTMLResponse)
def login_post(
    request:    Request,
    email:      str = Form(...),
    password:   str = Form(...),
    csrf_token: str = Form(None),
    db: Session = Depends(get_db),
):
    try:
        # CRITICAL severity fix #2: Rate limit login attempts
        rate_limit(f"login:{client_ip(request)}", max_requests=10, window_seconds=900)
        validate_csrf(request, csrf_token)

        try:
            log.info("Attempting to query tenant by email: %s", email)
            tenant = db.query(Tenant).filter_by(email=email.lower().strip()).first()
            log.info("Tenant query result: %s", "found" if tenant else "not found")
        except Exception as exc:
            log.error("CRITICAL: Failed to query tenant by email [%s]: %s\n%s", email, str(exc), traceback.format_exc())
            # Return detailed error for debugging
            error_msg = f"DB Error: {type(exc).__name__}: {str(exc)[:100]}"
            return templates.TemplateResponse("login.html",
                                              {"request": request, "error": error_msg})

        if not tenant or not tenant.is_active or not verify_password(password, tenant.password_hash):
            # HIGH severity fix #10: Track failed login attempts for audit
            if tenant:
                try:
                    _audit_log_action(db, tenant.id, email, "failed_login_attempt")
                except Exception as e:
                    log.warning("Failed to audit failed login: %s", e)
            return templates.TemplateResponse("login.html",
                                              {"request": request, "error": "Invalid email or password"})

        token = create_token(tenant.id, tenant_session_version(tenant))
        is_secure = is_request_secure(request)

        # MEDIUM severity fix #13: Use consistent TOKEN_HOURS for session cookie
        from web.auth import TOKEN_HOURS

        # Resume onboarding if not yet complete
        try:
            cfg = db.query(TenantConfig).filter_by(tenant_id=tenant.id).first()
            if not cfg:
                log.info("Creating missing TenantConfig for tenant [%s]", tenant.id)
                cfg = TenantConfig(tenant_id=tenant.id)
                db.add(cfg)
                db.commit()
        except Exception as exc:
            log.error("Failed to get/create TenantConfig [%s]: %s\n%s", tenant.id, exc, traceback.format_exc())
            cfg = None

        if tenant.email.lower().strip() in _ADMIN_EMAILS:
            redirect_to = "/admin"
        else:
            redirect_to = "/dashboard"

        # HIGH severity fix #10: Audit successful login
        try:
            _audit_log_action(db, tenant.id, email, "login_success")
        except Exception as e:
            log.warning("Failed to audit successful login: %s", e)

        resp = RedirectResponse(redirect_to, status_code=303)
        resp.set_cookie("session", token, httponly=True,
                        samesite="strict", secure=is_secure, max_age=TOKEN_HOURS * 3600)
        return resp
    except HTTPException as exc:
        log.warning("Login HTTPException [%s]: %s", exc.status_code, exc.detail)
        return templates.TemplateResponse("login.html",
                                          {"request": request, "error": exc.detail})
    except Exception as exc:
        log.error("Unexpected error in login_post: %s\n%s", exc, traceback.format_exc())
        return templates.TemplateResponse("login.html",
                                          {"request": request, "error": f"An error occurred: {type(exc).__name__}"})


@app.get("/signup", response_class=HTMLResponse)
def signup_get(request: Request, db: Session = Depends(get_db)):
    """Display signup page."""
    tenant = None
    try:
        tenant_id = get_current_tenant_id(request)
        tenant = _get_tenant(tenant_id, db)
    except Exception:
        pass
    return templates.TemplateResponse("signup.html", {"request": request, "tenant": tenant})


@app.post("/signup", response_class=HTMLResponse)
def signup_post(
    request: Request,
    first_name: str = Form(...),
    last_name:  str = Form(...),
    email:      str = Form(...),
    country:    str = Form(...),
    phone:      str = Form(""),
    password:   str = Form(...),
    csrf_token: str = Form(None),
    db: Session = Depends(get_db),
):
    # CRITICAL severity fix #2: Rate limit signup
    rate_limit(f"signup:{client_ip(request)}", max_requests=5, window_seconds=3600)
    validate_csrf(request, csrf_token)
    email = email.lower().strip()
    if db.query(Tenant).filter_by(email=email).first():
        return templates.TemplateResponse("signup.html",
                                          {"request": request, "error": "Email already registered"})

    # MEDIUM severity fix #11: Validate password strength
    from web.auth import validate_password_strength
    is_valid, error_msg = validate_password_strength(password)
    if not is_valid:
        return templates.TemplateResponse("signup.html",
                                          {"request": request, "error": error_msg})
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
    # MEDIUM severity fix #13: Reduce session timeout from 72h to 2h (TOKEN_HOURS from auth.py)
    from web.auth import TOKEN_HOURS
    resp.set_cookie("session", token, httponly=True,
                    samesite="strict", secure=is_secure, max_age=TOKEN_HOURS * 3600)
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


# ---------------------------------------------------------------------------
# Google OAuth
# ---------------------------------------------------------------------------
@app.get("/auth/google")
def google_oauth_start(request: Request):
    """Redirect to Google's OAuth consent screen."""
    if not GOOGLE_CLIENT_ID:
        return RedirectResponse("/signup?error=google_not_configured", status_code=302)
    state = secrets.token_urlsafe(20)
    from urllib.parse import urlencode
    params = urlencode({
        "client_id":    GOOGLE_CLIENT_ID,
        "redirect_uri": f"{APP_BASE_URL}/auth/google/callback",
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "access_type": "offline",
        "prompt": "select_account",
    })
    resp = RedirectResponse(f"https://accounts.google.com/o/oauth2/v2/auth?{params}", status_code=302)
    is_secure = is_request_secure(request)
    resp.set_cookie("_g_state", _store_token(state), httponly=True,
                    samesite="lax", secure=is_secure, max_age=300)
    return resp


@app.get("/auth/google/callback")
def google_oauth_callback(
    request: Request,
    code: str = "",
    state: str = "",
    error: str = "",
    db: Session = Depends(get_db),
):
    """Handle Google OAuth callback — create or log in user."""
    if error:
        return RedirectResponse(f"/signup?error=google_{error}", status_code=302)

    stored = request.cookies.get("_g_state", "")
    if not state or not stored or not hmac.compare_digest(_store_token(state), stored):
        return RedirectResponse("/signup?error=oauth_state", status_code=302)

    # Exchange code for tokens
    try:
        token_resp = requests.post("https://oauth2.googleapis.com/token", data={
            "client_id":     GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "redirect_uri":  f"{APP_BASE_URL}/auth/google/callback",
            "code":          code,
            "grant_type":    "authorization_code",
        }, timeout=10)
        token_resp.raise_for_status()
        tokens = token_resp.json()
    except Exception as exc:
        log.error("Google token exchange failed: %s", exc)
        return RedirectResponse("/signup?error=google_token", status_code=302)

    # Fetch user info
    try:
        ui_resp = requests.get("https://www.googleapis.com/oauth2/v3/userinfo",
            headers={"Authorization": f"Bearer {tokens['access_token']}"}, timeout=10)
        ui_resp.raise_for_status()
        info = ui_resp.json()
    except Exception as exc:
        log.error("Google userinfo fetch failed: %s", exc)
        return RedirectResponse("/signup?error=google_userinfo", status_code=302)

    email = (info.get("email") or "").lower().strip()
    if not email:
        return RedirectResponse("/signup?error=no_email", status_code=302)

    tenant = db.query(Tenant).filter_by(email=email).first()
    is_new = tenant is None
    if is_new:
        tenant = Tenant(
            first_name=info.get("given_name", "").strip() or email.split("@")[0],
            last_name=info.get("family_name", "").strip(),
            email=email,
            country="",
            phone="",
            password_hash=hash_password(secrets.token_urlsafe(32)),
            email_verified=True,
        )
        db.add(tenant)
        db.commit()
        db.refresh(tenant)
        db.add(TenantConfig(tenant_id=tenant.id))
        db.commit()

    cfg = db.query(TenantConfig).filter_by(tenant_id=tenant.id).first()
    redirect_to = "/onboarding" if (is_new or not (cfg and cfg.onboarding_complete)) else "/dashboard"

    from web.auth import TOKEN_HOURS
    token = create_token(tenant.id, tenant_session_version(tenant))
    is_secure = is_request_secure(request)
    resp = RedirectResponse(redirect_to, status_code=302)
    resp.set_cookie("session", token, httponly=True, samesite="strict",
                    secure=is_secure, max_age=TOKEN_HOURS * 3600)
    resp.delete_cookie("_g_state")
    return resp


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
    from web.auth import validate_password_strength

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
    is_valid, error_msg = validate_password_strength(password)
    if not is_valid:
        return templates.TemplateResponse("reset_password.html",
                                          {"request": request, "invalid": False, "success": False, "token": token, "error": error_msg})
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

    member.invite_token = _store_token(invite_token)
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
    member = _find_team_member_by_invite_token(db, token)
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
    from web.auth import validate_password_strength

    validate_csrf(request, csrf_token)

    member = _find_team_member_by_invite_token(db, token)
    if not member or not member.invite_token_expires_at:
        return templates.TemplateResponse(
            "invite_accept.html",
            {"request": request, "token": token, "member_name": None, "invalid_token": True, "error": None},
            status_code=404,
        )

    if datetime.now(timezone.utc) > member.invite_token_expires_at:
        return templates.TemplateResponse(
            "invite_accept.html",
            {"request": request, "token": token, "member_name": member.display_name, "invalid_token": True, "error": None},
            status_code=404,
        )

    if password != password_confirm:
        return templates.TemplateResponse(
            "invite_accept.html",
            {"request": request, "token": token, "member_name": member.display_name, "invalid_token": False, "error": "Passwords do not match"},
            status_code=400,
        )
    is_valid, error_msg = validate_password_strength(password)
    if not is_valid:
        return templates.TemplateResponse(
            "invite_accept.html",
            {"request": request, "token": token, "member_name": member.display_name, "invalid_token": False, "error": error_msg},
            status_code=400,
        )

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
                    secure=is_sec, max_age=TOKEN_HOURS * 3600)

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

def _serialize_conversation_messages(all_drafts: list[Draft]) -> list[dict]:
    messages = []
    for d in all_drafts:
        created_at = d.created_at
        if isinstance(created_at, str):
            try:
                created_at = datetime.strptime(created_at.split(".")[0], "%Y-%m-%d %H:%M:%S")
            except Exception:
                created_at = datetime.now(timezone.utc)
        elif not created_at:
            created_at = datetime.now(timezone.utc)

        # Inbound message (guest message)
        messages.append({
            "id": f"{d.id}-inbound",
            "direction": "inbound",
            "body": d.message,
            "channel": d.source,
            "timestamp": created_at.isoformat(),
            "display_time": created_at.strftime("%I:%M %p").lstrip("0"),
            "display_date": created_at.strftime("%a, %b %d"),
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
                "timestamp": created_at.isoformat(),
                "display_time": created_at.strftime("%I:%M %p").lstrip("0"),
                "display_date": created_at.strftime("%a, %b %d"),
                "status": d.status,
                "badge": auto_sent_badge,
            })
    return messages


def _conversation_messages_for_thread(db: Session, tenant_id: str, thread_key: str) -> list[dict]:
    all_drafts = db.query(Draft).filter(
        Draft.tenant_id == tenant_id,
        Draft.thread_key == thread_key,
    ).order_by(Draft.created_at).all()
    return _serialize_conversation_messages(all_drafts)


@app.get("/api/conversation/{thread_key}")
def api_conversation(thread_key: str, request: Request, db: Session = Depends(get_db), _=Depends(require_flag("CONVERSATION_VIEW"))):
    """Get all messages for a thread (both inbound and outbound, including auto-sent)."""
    try:
        tenant_id = get_current_tenant_id(request)
    except HTTPException:
        raise HTTPException(status_code=401, detail="Not authenticated")

    return {
        "thread_key": thread_key,
        "messages": _conversation_messages_for_thread(db, tenant_id, thread_key),
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

    try:
        tenant = _get_tenant(tenant_id, db)
        cfg = _get_or_create_config(tenant_id, db)
    except Exception as exc:
        log.error("Failed to load tenant/config [%s]: %s\n%s", tenant_id, exc, traceback.format_exc())
        raise

    selected_property = request.query_params.get("property", "").strip()
    search_query = request.query_params.get("q", "").strip().lower()

    # Build base query
    try:
        query = rdb.query(Draft).filter_by(tenant_id=tenant_id)
    except Exception as exc:
        log.error("Failed to build Draft query [%s]: %s\n%s", tenant_id, exc, traceback.format_exc())
        raise

    # Apply search filter
    if search_query:
        # Search by guest name or thread_key
        query = query.filter(
            (Draft.guest_name.ilike(f"%{search_query}%")) |
            (Draft.thread_key.ilike(f"%{search_query}%"))
        )

    try:
        draft_rows_all = (
            query
            .order_by(Draft.created_at.desc())
            .limit(500)
            .all()
        )
    except Exception as exc:
        log.error("Failed to query drafts [%s]: %s\n%s", tenant_id, exc, traceback.format_exc())
        draft_rows_all = []

    try:
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
    except Exception as exc:
        log.error("Failed to query entities [%s]: %s\n%s", tenant_id, exc, traceback.format_exc())
        sync_log = None
        all_reservations = []
        workflow_rules = []
        team_members_all = []
        open_issues_all = []
        timeline_events_all = []

    try:
        property_options = _collect_property_options(cfg, all_reservations, draft_rows_all, open_issues_all, team_members_all)
    except Exception as exc:
        log.error("Failed to collect property options [%s]: %s\n%s", tenant_id, exc, traceback.format_exc())
        property_options = []

    if selected_property and selected_property not in property_options:
        selected_property = ""

    try:
        draft_rows = [draft for draft in draft_rows_all if _property_match(selected_property, _draft_property_name(draft))]
    except Exception as exc:
        log.error("Failed to filter draft_rows [%s]: %s\n%s", tenant_id, exc, traceback.format_exc())
        draft_rows = draft_rows_all
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

    try:
        kpis = derive_dashboard_kpis(draft_rows, filtered_reservations, now=now)
        approval_streak = kpis["drafts"].get("approval_streak", 0)
        occupancy_gaps = kpis["reservations"].get("occupancy_gaps", [])
    except Exception as exc:
        log.error("Failed to derive KPIs [%s]: %s\n%s", tenant_id, exc, traceback.format_exc())
        kpis = {"drafts": {}, "reservations": {}}
        approval_streak = 0
        occupancy_gaps = []

    try:
        review_velocity = compute_review_velocity(filtered_reservations)
    except Exception as exc:
        db.rollback()
        log.error("Failed to compute review velocity [%s]: %s\n%s", tenant_id, exc, traceback.format_exc())
        review_velocity = None

    try:
        sentiment_summary = _sentiment_summary(draft_rows, filtered_reservations)
    except Exception as exc:
        db.rollback()
        log.error("Failed to compute sentiment summary [%s]: %s\n%s", tenant_id, exc, traceback.format_exc())
        sentiment_summary = {}

    try:
        activation_checklist = build_activation_checklist(
            cfg,
            reservations=filtered_reservations or all_reservations,
            inbound_email_address=_tenant_inbound_email_address(cfg),
            inbound_webhook_url=f"{APP_BASE_URL}/email/inbound",
        )
    except Exception as exc:
        db.rollback()
        log.error("Failed to build activation checklist [%s]: %s\n%s", tenant_id, exc, traceback.format_exc())
        activation_checklist = []

    try:
        exception_queue = surface_exception_queue(pending, filtered_reservations, now=now, stale_minutes=60, limit=8)
        recent_timeline = build_guest_timeline(reversed(timeline_events), limit=8)
    except Exception as exc:
        db.rollback()
        log.error("Failed to build exception queue/timeline [%s]: %s\n%s", tenant_id, exc, traceback.format_exc())
        exception_queue = []
        recent_timeline = []
    if not selected_property:
        try:
            _upsert_tenant_kpi_snapshot(db, tenant_id, kpis, open_issues, now)
        except Exception as exc:
            log.warning("[%s] KPI snapshot update failed: %s", tenant_id, exc)
            db.rollback()

    try:
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
    except Exception as exc:
        db.rollback()
        log.error("Failed to compute benchmarks [%s]: %s\n%s", tenant_id, exc, traceback.format_exc())
        response_benchmark = {}
        review_benchmark = {}

    # Stale CSV warning: > 12 hours since last upload
    csv_stale = False
    if sync_log:
        last = sync_log.last_synced
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        csv_stale = (datetime.now(timezone.utc) - last).total_seconds() > 43200

    # Group pending drafts into conversations by thread_key
    try:
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
    except Exception as exc:
        log.error("Failed to build conversations [%s]: %s\n%s", tenant_id, exc, traceback.format_exc())
        conversations = []

    # Calculate real AI Context Map percentages from TenantConfig field completeness
    def _ctx_pct(fields: list) -> int:
        filled = sum(1 for f in fields if f)
        return round((filled / len(fields)) * 100) if fields else 0

    checkin_fields = [cfg.check_in_time, cfg.check_out_time, cfg.early_checkin_policy,
                      cfg.late_checkout_policy, cfg.house_rules]
    local_fields = [cfg.property_city, cfg.google_maps_url, cfg.nearby_restaurants]
    rules_fields = [cfg.house_rules, cfg.pet_policy, cfg.smoking_policy, cfg.quiet_hours,
                    cfg.refund_policy, cfg.parking_policy]
    checkin_pct = _ctx_pct(checkin_fields)
    local_pct   = _ctx_pct(local_fields)
    rules_pct   = _ctx_pct(rules_fields)
    overall_pct = _ctx_pct(checkin_fields + local_fields + rules_fields)
    ctx_map = {"checkin": checkin_pct, "local": local_pct, "rules": rules_pct, "overall": overall_pct}

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
        "search_query": search_query,
        "active_arrivals": db.query(ArrivalActivation).filter(
            ArrivalActivation.tenant_id == tenant_id,
            ArrivalActivation.status.in_(["active", "pending"]),
        ).count(),
        "today":         today,
        "setup_alerts":  _get_setup_alerts(cfg, tenant, all_reservations),
        "ctx_map":       ctx_map,
        "onboarding_step": cfg.onboarding_step if cfg else 0,
    })
    if show_tour:
        response.delete_cookie("show_tour")
    return response


# ---------------------------------------------------------------------------
# Google Maps Places — auto-fetch nearby places for bot context
# ---------------------------------------------------------------------------

def _fetch_nearby_places(maps_url: str, api_key: str) -> str | None:
    """
    Given a Google Maps URL and a Places API key, fetch nearby places
    (restaurants, shops, attractions, transit) and return a formatted string
    suitable for storing in cfg.nearby_restaurants.
    Returns None on failure or if no results found.
    """
    import re as _re
    import urllib.parse as _urlparse

    # --- Extract lat/lng from Maps URL ---
    lat, lng = None, None
    # Format: /@lat,lng,zoom or /@lat,lng  (most common share format)
    m = _re.search(r'/@(-?\d+\.\d+),(-?\d+\.\d+)', maps_url)
    if m:
        lat, lng = m.group(1), m.group(2)
    else:
        # Format: ?q=lat,lng or &q=lat,lng
        m = _re.search(r'[?&]q=(-?\d+\.\d+),(-?\d+\.\d+)', maps_url)
        if m:
            lat, lng = m.group(1), m.group(2)
        else:
            # Format: /maps/place/Name/@lat,lng or ll=lat,lng
            m = _re.search(r'[?&]ll=(-?\d+\.\d+),(-?\d+\.\d+)', maps_url)
            if m:
                lat, lng = m.group(1), m.group(2)

    if not lat or not lng:
        log.warning("_fetch_nearby_places: could not extract lat/lng from %s", maps_url)
        return None

    location = f"{lat},{lng}"
    base = "https://maps.googleapis.com/maps/api/place/nearbysearch/json"

    # Categories to fetch: (label, type, radius_m)
    categories = [
        ("Restaurants & Cafes",  "restaurant",          500),
        ("Shopping & Groceries", "supermarket",         1000),
        ("Cafes",                "cafe",                500),
        ("Attractions",          "tourist_attraction",  2000),
        ("Transport",            "transit_station",     1000),
        ("Pharmacy / Hospital",  "pharmacy",            1000),
    ]

    sections = []
    seen_names: set[str] = set()

    for label, place_type, radius in categories:
        params = _urlparse.urlencode({
            "location": location,
            "radius": radius,
            "type": place_type,
            "key": api_key,
        })
        url = f"{base}?{params}"
        try:
            req = urllib.request.Request(url, headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode(errors="replace"))
        except Exception as exc:
            log.warning("_fetch_nearby_places: %s request failed: %s", label, exc)
            continue

        if data.get("status") not in ("OK", "ZERO_RESULTS"):
            log.warning("_fetch_nearby_places: %s API status %s", label, data.get("status"))
            continue

        results = data.get("results", [])[:5]
        if not results:
            continue

        lines = []
        for place in results:
            name = place.get("name", "")
            if name in seen_names:
                continue
            seen_names.add(name)
            rating = place.get("rating")
            vicinity = place.get("vicinity", "")
            # rough distance label from vicinity (just use "nearby")
            rating_str = f" ★{rating}" if rating else ""
            lines.append(f"- {name}{rating_str} ({vicinity})")

        if lines:
            sections.append(f"**{label}:**\n" + "\n".join(lines))

    if not sections:
        return None

    header = f"[Auto-fetched nearby places — {lat},{lng}]\n"
    return header + "\n\n".join(sections)


# ---------------------------------------------------------------------------
# Setup alerts — missing configuration notifications
# ---------------------------------------------------------------------------

def _get_setup_alerts(cfg, tenant, reservations: list) -> list[dict]:
    """
    Return a list of setup alert dicts for the dashboard notification bar.
    Each dict has: level ('error'|'warning'|'info'), message, link, tab, icon.
    """
    alerts = []

    # 1. No property name
    if not (cfg.property_names or "").strip():
        alerts.append({
            "level": "error",
            "icon": "home",
            "message": "No property name set — the AI can't personalise any messages.",
            "cta": "Add property name",
            "link": "/settings#property-name",
            "tab": "general",
        })

    # 2. WhatsApp not configured
    wa_ok = cfg.wa_mode == "meta_cloud" and cfg.whatsapp_phone_id and cfg.whatsapp_token_enc
    if not wa_ok:
        alerts.append({
            "level": "error",
            "icon": "chat",
            "message": "WhatsApp is not connected — guests can't receive any automated replies.",
            "cta": "Connect WhatsApp",
            "link": "/settings#wa-setup",
            "tab": "channels",
        })

    # 3. Knowledge base empty (house rules + FAQ both blank)
    kb_empty = not (cfg.house_rules or "").strip() and not (cfg.faq or "").strip()
    if kb_empty:
        alerts.append({
            "level": "warning",
            "icon": "menu_book",
            "message": "House rules and FAQ are empty — the AI will give generic answers without your property info.",
            "cta": "Fill Knowledge Base",
            "link": "/settings#knowledge-base",
            "tab": "general",
        })

    # 4. No check-in / check-out times
    if not (cfg.check_in_time or "").strip() or not (cfg.check_out_time or "").strip():
        alerts.append({
            "level": "warning",
            "icon": "schedule",
            "message": "Check-in / check-out times are not set — guests asking about arrival or departure will get vague answers.",
            "cta": "Set times",
            "link": "/settings#checkin-times",
            "tab": "general",
        })

    # 5. No escalation email
    if not (cfg.escalation_email or "").strip():
        alerts.append({
            "level": "warning",
            "icon": "mail",
            "message": "No escalation email set — urgent guest issues won't be forwarded anywhere.",
            "cta": "Set escalation email",
            "link": "/settings#escalation",
            "tab": "general",
        })

    # 6. No reservations imported
    if not reservations:
        alerts.append({
            "level": "info",
            "icon": "event_available",
            "message": "No reservations yet — import from Airbnb CSV or add one manually so the AI has guest context.",
            "cta": "Import reservations",
            "link": "/reservations",
            "tab": None,
        })

    # 7. No welcome message template
    if not (cfg.guest_welcome_template or "").strip():
        alerts.append({
            "level": "info",
            "icon": "waving_hand",
            "message": "No custom welcome message — a default greeting will be sent when guests check in.",
            "cta": "Set welcome message",
            "link": "/settings#welcome-msg",
            "tab": "general",
        })

    return alerts


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
    """Send reply via the appropriate channel and mark the final delivery state."""
    if draft.status != "pending":
        return
    cfg = db.query(TenantConfig).filter_by(tenant_id=tenant_id).first()
    reservation = reservation or (
        db.query(Reservation).filter_by(id=draft.reservation_id, tenant_id=tenant_id).first()
        if draft.reservation_id else None
    )
    send_ok = False
    failure_reason = "No configured delivery path for draft"

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
            send_ok = True
        except Exception as exc:
            log.error("[%s] SMTP send failed: %s", tenant_id, exc)
            failure_reason = f"SMTP send failed: {exc}"

    elif draft.source == "whatsapp" and draft.reply_to and cfg:
        guest_phone = draft.reply_to
        if cfg.wa_mode == "twilio":
            from web.sms_sender import send_whatsapp_twilio
            auth_token = decrypt(cfg.twilio_auth_token_enc or "")
            wa_num = cfg.twilio_whatsapp_number or ""
            if cfg.twilio_account_sid and auth_token and wa_num:
                ok = send_whatsapp_twilio(cfg.twilio_account_sid, auth_token,
                                          wa_num, guest_phone, final_text)
                if not ok:
                    log.warning("[%s] Twilio WA send failed for ***%s", tenant_id, guest_phone[-4:] if guest_phone else "")
                    failure_reason = "Twilio WhatsApp delivery failed"
                else:
                    send_ok = True
            else:
                failure_reason = "Twilio WhatsApp is not fully configured"
        elif tenant_has_channel(cfg, PLAN_META_CLOUD):
            from web.meta_sender import send_whatsapp
            token = decrypt(cfg.whatsapp_token_enc or "")
            if token and cfg.whatsapp_phone_id:
                ok = send_whatsapp(cfg.whatsapp_phone_id, token, guest_phone, final_text)
                if not ok:
                    log.warning("[%s] Meta WA send failed for ***%s", tenant_id, guest_phone[-4:] if guest_phone else "")
                    failure_reason = "Meta WhatsApp delivery failed"
                else:
                    send_ok = True
            else:
                failure_reason = "Meta WhatsApp is not fully configured"
        else:
            failure_reason = "Tenant plan does not include WhatsApp delivery"

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
                    failure_reason = "Twilio SMS delivery failed"
                else:
                    send_ok = True
            else:
                failure_reason = "Twilio SMS is not fully configured"
        else:
            failure_reason = "Tenant plan does not include SMS delivery"

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
                        failure_reason = f"PMS delivery failed for reservation {parts[1]}"
                    else:
                        log.info("[%s] PMS reply sent via %s for reservation %s",
                                 tenant_id, integration.pms_type, parts[1])
                        send_ok = True
                else:
                    log.warning("[%s] PMS integration %s not found or inactive", tenant_id, parts[0])
                    failure_reason = f"PMS integration {parts[0]} not found or inactive"
            except Exception as exc:
                log.error("[%s] PMS reply error: %s", tenant_id, exc)
                failure_reason = f"PMS reply error: {exc}"
        else:
            failure_reason = "PMS reply target is malformed"
    else:
        failure_reason = f"Unsupported draft delivery source: {draft.source or 'unknown'}"

    draft.final_text = final_text
    if not send_ok:
        draft.status = "failed"
        db.add(FailedDraftLog(
            tenant_id=tenant_id,
            draft_id=draft.id,
            error_reason=failure_reason,
        ))
        db.add(ActivityLog(
            tenant_id=tenant_id,
            event_type="draft_send_failed",
            message=f"Draft send failed for {draft.guest_name}: {failure_reason}",
        ))
        db.commit()
        return

    draft.status = "approved"
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


# _queue_baileys_outbound and _pop_baileys_outbound removed — Baileys integration discontinued


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

            # _queue_baileys_outbound(tenant_id, cfg.whatsapp_number, response, db)
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
            # _queue_baileys_outbound(tenant_id, cfg.whatsapp_number, f"❌ Draft #{identifier} not found", db)
            return

        if draft.status != "pending":
            # _queue_baileys_outbound(tenant_id, cfg.whatsapp_number, f"❌ Already {draft.status}: {draft.guest_name}", db)
            return

        # Approve and send the draft
        draft.status = "approved"
        draft.final_text = draft.draft
        draft.updated_at = datetime.now(timezone.utc)

        # Queue the response to guest
            # _queue_baileys_outbound(tenant_id, draft.reply_to, draft.final_text, db)

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

            # _queue_baileys_outbound(tenant_id, cfg.whatsapp_number, f"✓ Sent to {draft.guest_name}", db)
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
            # _queue_baileys_outbound(tenant_id, cfg.whatsapp_number, f"❌ Draft #{identifier} not found", db)
            return

        draft.status = "skipped"
        draft.updated_at = datetime.now(timezone.utc)

        db.add(ActivityLog(
            tenant_id=tenant_id,
            event_type="draft_skipped",
            message=f"Host skipped: {draft.guest_name}"
        ))
        db.commit()

            # _queue_baileys_outbound(tenant_id, cfg.whatsapp_number, f"✓ Skipped {draft.guest_name}'s message", db)
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
            # _queue_baileys_outbound(tenant_id, cfg.whatsapp_number, f"❌ Draft #{identifier} not found", db)
            return

        # Show the draft
        preview = f"""📋 Draft for {draft.guest_name}:

"{draft.draft}"

Status: {draft.status}
Confidence: {draft.confidence:.0%}

Reply: approve {identifier} or skip {identifier}"""
            # _queue_baileys_outbound(tenant_id, cfg.whatsapp_number, preview, db)
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
            # _queue_baileys_outbound(tenant_id, cfg.whatsapp_number, f"❌ Draft #{identifier} not found", db)
            return

        if draft.status != "pending":
            # _queue_baileys_outbound(tenant_id, cfg.whatsapp_number, f"❌ Can't edit {draft.status} draft", db)
            return

        # Update the draft
        draft.draft = new_text
        draft.updated_at = datetime.now(timezone.utc)
        db.commit()

        response = f"""✏️ Draft updated:

"{new_text}"

Reply: approve {identifier} to send"""
            # _queue_baileys_outbound(tenant_id, cfg.whatsapp_number, response, db)
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
            # _queue_baileys_outbound(tenant_id, cfg.whatsapp_number, help_text, db)

    else:
        # Unknown command (Baileys integration removed)
        pass
            # _queue_baileys_outbound(tenant_id, cfg.whatsapp_number, "❓ Unknown command. Type 'h' or 'help'", db)


def _send_host_notification(tenant_id: str, notify_phone: str, text: str, guest_name: str, guest_message: str, channel: str, db: Session):
    """Send multi-channel notification to the host when a guest messages the bot."""
    cfg = db.query(TenantConfig).filter_by(tenant_id=tenant_id).first()
    if not cfg:
        return

    try:
        # WhatsApp notification (to host's own number)
        if cfg.wa_mode == "baileys" and cfg.whatsapp_number:
            # _queue_baileys_outbound(tenant_id, notify_phone, text, db)
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
    phone:               str = Form(""),
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
            if phone.strip() and not tenant.phone:
                tenant.phone = phone.strip()

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
    sys_conf = load_system_config(db)
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

        # Title → property name (try multiple sources)
        title = None
        # Try h1, title, meta og:title in order
        title_tag = soup.find("h1")
        if title_tag:
            title = title_tag.get_text(strip=True)
        else:
            title_tag = soup.find("title")
            if title_tag:
                title = title_tag.get_text(strip=True)
        if not title:
            og_title = soup.find("meta", property="og:title")
            if og_title and og_title.get("content"):
                title = og_title.get("content")

        if title:
            # Remove Airbnb suffix
            title = _re.sub(r"\s*[-|]\s*Airbnb\s*$", "", title)
            original_title = title  # Keep original for location extraction

            # Extract location from original title first
            # Look for the cleanest "City, State, Country" pattern
            if "," in title:
                # Find all comma-separated sequences
                parts = title.split(",")

                # Work backwards to find City, State, Country
                # Skip parts with " - " or other separators
                cleaned_parts = []
                for part in reversed(parts):
                    part = part.strip()
                    # Skip if contains hyphen followed by text (likely category info)
                    if " - " in part or "Rent" in part or "Apartment" in part or "Villa" in part:
                        continue
                    # Only keep simple location words
                    if 2 <= len(part) < 30 and part.replace(" ", "").isalpha():
                        cleaned_parts.insert(0, part)
                    if len(cleaned_parts) == 3:
                        break

                if len(cleaned_parts) >= 3:
                    city = cleaned_parts[-3]
                    state = cleaned_parts[-2]
                    country = cleaned_parts[-1]
                    result["property_city"] = f"{city}, {state}, {country}"[:80]

            # Extract property name: take only the part before the first " - "
            if " - " in original_title:
                prop_name = original_title.split(" - ")[0].strip()
            else:
                prop_name = original_title

            # Remove trailing city/location names from property name
            prop_name = _re.sub(r"\s+(?:Assagao|Assagaon|Goa|Mumbai|Delhi|Bangalore|Hyderabad|Pune|Cochin|Kolkata)\s*$", "", prop_name, flags=_re.I)

            result["property_names"] = prop_name[:120]

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

    sys_conf = load_system_config(db)
    if not sys_conf or not sys_conf.openrouter_api_key_enc:
        return HTMLResponse('<p class="test-result test-fail">✗ AI engine not configured by admin.</p>')

    try:
        import openai
        api_key = decrypt(sys_conf.openrouter_api_key_enc) or sys_conf.openrouter_api_key_enc
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
    db.rollback() # Ensure safe start for multi-query route
    try:
        tenant_id = get_current_tenant_id(request)
    except HTTPException:
        return _redirect_login()

    try:
        tenant  = _get_tenant(tenant_id, db)
        cfg     = _get_or_create_config(tenant_id, db)
    except Exception:
        db.rollback()
        raise
    
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


def _save_voice_ai_settings(
    tenant: Tenant,
    cfg: TenantConfig,
    *,
    voice_enabled: str,
    voice_phone_number: str,
    voice_twilio_account_sid: str,
    voice_twilio_auth_token: str,
    voice_twilio_from_number: str,
    voice_elevenlabs_voice_id: str,
    voice_google_tts_voice: str = "",
    voice_send_channel: str,
    voice_post_call_summary: str,
    voice_scheduled_calls_enabled: str,
    sms_notify_number: str,
) -> None:
    tenant.voice_enabled = voice_enabled.strip().lower() == "true"
    tenant.voice_phone_number = voice_phone_number.strip() or None
    cfg.voice_twilio_account_sid = voice_twilio_account_sid.strip() or None
    cfg.voice_twilio_from_number = voice_twilio_from_number.strip() or None
    if voice_twilio_auth_token.strip():
        cfg.voice_twilio_auth_token_enc = encrypt(voice_twilio_auth_token.strip())
    cfg.voice_elevenlabs_voice_id = voice_elevenlabs_voice_id.strip() or "EXAVITQu4vr4xnSDxMaL"
    if voice_google_tts_voice.strip():
        cfg.voice_google_tts_voice = voice_google_tts_voice.strip()
    cfg.voice_send_channel = voice_send_channel.strip() or "disabled"
    cfg.voice_post_call_summary = voice_post_call_summary.strip().lower() == "true"
    cfg.voice_scheduled_calls_enabled = voice_scheduled_calls_enabled.strip().lower() == "true"
    cfg.sms_notify_number = sms_notify_number.strip() or None


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
            Draft, ActivityLog, Reservation, ReservationIntakeBatch,
            ProcessedEmail, CalendarState, FailedDraftLog, PMSProcessedMessage,
            ReservationSyncLog, GuestTimelineEvent, ArrivalActivation, IssueTicket
        )

        # We must delete in referential order (children first)
        db.query(Draft).filter_by(tenant_id=tenant_id).delete(synchronize_session=False)
        db.query(ActivityLog).filter_by(tenant_id=tenant_id).delete(synchronize_session=False)
        # Note: BaileysOutbound cleanup removed (Baileys integration discontinued)
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
    # Twilio WhatsApp
    twilio_whatsapp_number: str = Form(""),
    # SMS / Twilio
    sms_mode:              str = Form("none"),
    twilio_account_sid:    str = Form(""),
    twilio_auth_token:     str = Form(""),
    twilio_from_number:    str = Form(""),
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
        "google_maps_url",
        "faq",
        "custom_instructions",
        "escalation_email",
    ):
        val = form_data.get(field, "")
        if val is not None and str(val).strip():
            setattr(cfg, field, str(val).strip())
        elif field == "google_maps_url" and val is not None and str(val).strip() == "":
            setattr(cfg, field, None)  # allow clearing the URL
    max_g = str(form_data.get("max_guests","")).strip()
    if max_g.isdigit():
        cfg.max_guests = int(max_g)

    # Auto-fetch nearby places when Google Maps URL is saved
    new_maps_url = str(form_data.get("google_maps_url", "")).strip()
    if new_maps_url and new_maps_url != (cfg.google_maps_url or ""):
        try:
            sys_conf = load_system_config(db)
            gmaps_key = None
            if sys_conf and sys_conf.google_maps_api_key_enc:
                gmaps_key = decrypt(sys_conf.google_maps_api_key_enc) or sys_conf.google_maps_api_key_enc
            if gmaps_key:
                fetched = _fetch_nearby_places(new_maps_url, gmaps_key)
                if fetched:
                    cfg.nearby_restaurants = fetched
                    log.info("[%s] Auto-fetched nearby places from Maps URL", tenant_id)
            else:
                log.info("[%s] Google Maps API key not configured — skipping nearby fetch", tenant_id)
        except Exception as _exc:
            log.warning("[%s] Failed to fetch nearby places: %s", tenant_id, _exc)

    # WhatsApp Meta Cloud
    cfg.whatsapp_number   = whatsapp_number.strip() or None
    cfg.whatsapp_phone_id = whatsapp_phone_id.strip() or None
    if whatsapp_verify_token.strip():
        cfg.whatsapp_verify_token = whatsapp_verify_token.strip()
    if whatsapp_token.strip():
        cfg.whatsapp_token_enc = encrypt(whatsapp_token.strip())

    # SMS / Twilio
    cfg.sms_mode           = sms_mode.strip() or "none"
    cfg.twilio_account_sid = twilio_account_sid.strip() or None
    cfg.twilio_from_number = twilio_from_number.strip() or None
    if twilio_auth_token.strip():
        cfg.twilio_auth_token_enc = encrypt(twilio_auth_token.strip())

    # Twilio WhatsApp number
    if twilio_whatsapp_number.strip():
        cfg.twilio_whatsapp_number = twilio_whatsapp_number.strip()

    # Auto-detect wa_mode — Twilio WA if configured, else Meta Cloud, else none
    if cfg.twilio_whatsapp_number and cfg.twilio_account_sid and cfg.twilio_auth_token_enc:
        cfg.wa_mode = "twilio"
    elif cfg.whatsapp_phone_id and cfg.whatsapp_token_enc:
        cfg.wa_mode = "meta_cloud"
    else:
        cfg.wa_mode = "none"

    # Guest engagement toggles
    cfg.satisfaction_pulse_enabled = form_data.get("satisfaction_pulse_enabled") == "true"
    cfg.review_request_enabled = form_data.get("review_request_enabled") == "true"
    cfg.upsell_enabled = form_data.get("upsell_enabled") == "true"
    if form_data.get("review_request_url", "").strip():
        cfg.review_request_url = form_data.get("review_request_url", "").strip()[:512]

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


@app.post("/api/test-whatsapp")
async def test_whatsapp(
    request: Request,
    to_phone: str = Form(""),
    csrf_token: str = Form(None),
    db: Session = Depends(get_db),
):
    """Send a test WhatsApp message to verify configuration."""
    tenant_id = get_current_tenant_id(request)
    cfg = _get_or_create_config(tenant_id, db)

    if not cfg.whatsapp_phone_id or not cfg.whatsapp_token_enc:
        return JSONResponse(
            {"ok": False, "error": "WhatsApp credentials not configured yet."},
            status_code=400
        )

    try:
        from web.meta_sender import send_whatsapp
        token = decrypt(cfg.whatsapp_token_enc)
        to_phone_normalized = to_phone.replace("+", "").strip()
        wa_error: dict = {}
        ok = send_whatsapp(
            cfg.whatsapp_phone_id,
            token,
            to_phone_normalized,
            "👋 This is a test message from your HostAI account. If you see this, WhatsApp is working!",
            error_detail=wa_error,
        )
        if ok:
            return JSONResponse({"ok": True, "message": f"✅ Test message sent to {to_phone}"})
        detail = wa_error.get('body', '')
        try:
            import json as _j
            parsed = _j.loads(detail)
            meta_msg = (parsed.get('error', {}) or {}).get('message') or detail
        except Exception:
            meta_msg = detail
        err = f"WhatsApp API error (HTTP {wa_error.get('code','?')}): {meta_msg}" if meta_msg else "❌ Failed to send. Check your credentials and try again."
        return JSONResponse({"ok": False, "error": err}, status_code=400)
    except Exception as e:
        logger.error(f"WhatsApp test error: {e}")
        return JSONResponse(
            {"ok": False, "error": f"❌ Error: {str(e)}"},
            status_code=500
        )


@app.post("/api/test-sms")
async def test_sms(
    request: Request,
    to_phone: str = Form(""),
    csrf_token: str = Form(None),
    db: Session = Depends(get_db),
):
    """Send a test SMS to verify configuration."""
    tenant_id = get_current_tenant_id(request)
    cfg = _get_or_create_config(tenant_id, db)

    if not cfg.twilio_account_sid or not cfg.twilio_auth_token_enc or not cfg.twilio_from_number:
        return JSONResponse(
            {"ok": False, "error": "SMS credentials not fully configured yet."},
            status_code=400
        )

    try:
        auth_token = decrypt(cfg.twilio_auth_token_enc)
        ok = send_sms(
            cfg.twilio_account_sid,
            auth_token,
            cfg.twilio_from_number,
            to_phone.strip(),
            "👋 This is a test SMS from your HostAI account. If you see this, SMS is working!"
        )
        if ok:
            return JSONResponse({"ok": True, "message": f"✅ Test SMS sent to {to_phone}"})
        return JSONResponse(
            {"ok": False, "error": f"❌ Failed to send. Check your Twilio credentials and try again."},
            status_code=400
        )
    except Exception as e:
        logger.error(f"SMS test error: {e}")
        return JSONResponse(
            {"ok": False, "error": f"❌ Error: {str(e)}"},
            status_code=500
        )


@app.post("/voice-calls/settings", response_class=HTMLResponse)
async def voice_ai_settings_save(
    request: Request,
    voice_enabled: str = Form("false"),
    voice_phone_number: str = Form(""),
    voice_twilio_account_sid: str = Form(""),
    voice_twilio_auth_token: str = Form(""),
    voice_twilio_from_number: str = Form(""),
    voice_elevenlabs_voice_id: str = Form("EXAVITQu4vr4xnSDxMaL"),
    voice_google_tts_voice: str = Form(""),
    voice_send_channel: str = Form("disabled"),
    voice_post_call_summary: str = Form("false"),
    voice_scheduled_calls_enabled: str = Form("false"),
    sms_notify_number: str = Form(""),
    # Property knowledge fields
    property_city: str = Form(""),
    check_in_time: str = Form(""),
    check_out_time: str = Form(""),
    max_guests: str = Form(""),
    amenities: str = Form(""),
    house_rules: str = Form(""),
    parking_policy: str = Form(""),
    pet_policy: str = Form(""),
    nearby_restaurants: str = Form(""),
    faq: str = Form(""),
    csrf_token: str = Form(None),
    db: Session = Depends(get_db),
):
    try:
        tenant_id = get_current_tenant_id(request)
    except HTTPException:
        return _redirect_login()
    validate_csrf(request, csrf_token)
    rate_limit(f"voice-settings:{tenant_id}", max_requests=30, window_seconds=3600)

    cfg = _get_or_create_config(tenant_id, db)
    tenant = _get_tenant(tenant_id, db)
    _save_voice_ai_settings(
        tenant,
        cfg,
        voice_enabled=voice_enabled,
        voice_phone_number=voice_phone_number,
        voice_twilio_account_sid=voice_twilio_account_sid,
        voice_twilio_auth_token=voice_twilio_auth_token,
        voice_twilio_from_number=voice_twilio_from_number,
        voice_elevenlabs_voice_id=voice_elevenlabs_voice_id,
        voice_google_tts_voice=voice_google_tts_voice,
        voice_send_channel=voice_send_channel,
        voice_post_call_summary=voice_post_call_summary,
        voice_scheduled_calls_enabled=voice_scheduled_calls_enabled,
        sms_notify_number=sms_notify_number,
    )
    # Save property knowledge fields
    cfg.property_city = property_city.strip() or None
    cfg.check_in_time = check_in_time.strip() or None
    cfg.check_out_time = check_out_time.strip() or None
    if max_guests.strip():
        try:
            cfg.max_guests = int(max_guests.strip())
        except ValueError:
            pass
    else:
        cfg.max_guests = None
    cfg.amenities = amenities.strip() or None
    cfg.house_rules = house_rules.strip() or None
    cfg.parking_policy = parking_policy.strip() or None
    cfg.pet_policy = pet_policy.strip() or None
    cfg.nearby_restaurants = nearby_restaurants.strip() or None
    cfg.faq = faq.strip() or None

    db.add(ActivityLog(tenant_id=tenant_id, event_type="voice_ai_settings_saved", message="Voice AI settings updated"))
    db.commit()
    worker_manager.restart_worker(tenant_id)
    return RedirectResponse(url="/voice-calls?tab=settings&saved=1#voice-ai-setup", status_code=303)


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

    # HIGH severity fix #6: Rate limit Stripe checkout creation
    rate_limit(f"checkout:{tenant_id}", max_requests=5, window_seconds=60)

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


# ---------------------------------------------------------------------------
# PayPal Subscription Routes
# ---------------------------------------------------------------------------

@app.post("/billing/paypal/subscribe/{plan_key}")
def paypal_subscribe(plan_key: str, request: Request,
                     num_units: int = Form(1),
                     csrf_token: str = Form(None),
                     db: Session = Depends(get_db)):
    """Create PayPal subscription and redirect to checkout."""
    try:
        tenant_id = get_current_tenant_id(request)
    except HTTPException:
        return _redirect_login()
    validate_csrf(request, csrf_token)

    # Rate limit PayPal checkout creation
    rate_limit(f"paypal_checkout:{tenant_id}", max_requests=5, window_seconds=60)

    # Validate plan and units
    plan = db.query(PlanConfig).filter_by(plan_key=plan_key, is_active=True).first()
    if not plan:
        raise HTTPException(status_code=400, detail="Invalid plan")
    if not (plan.min_units <= num_units <= plan.max_units):
        raise HTTPException(status_code=400, detail=f"Plan requires {plan.min_units}-{plan.max_units} units")

    _get_or_create_config(tenant_id, db)
    try:
        from web.paypal import create_subscription
        approval_url = create_subscription(
            tenant_id=tenant_id,
            plan_key=plan_key,
            num_units=num_units,
            return_url=f"{APP_BASE_URL}/billing/paypal/return",
            cancel_url=f"{APP_BASE_URL}/billing/paypal/cancel",
            db=db,
        )
    except HTTPException:
        raise
    except Exception as exc:
        log.error("PayPal subscription error: %s", exc)
        raise HTTPException(status_code=500, detail="Payment provider error")

    return RedirectResponse(approval_url, status_code=302)


@app.get("/billing/paypal/return", response_class=HTMLResponse)
def paypal_return(request: Request, subscription_id: str = None,
                  ba_token: str = None,
                  db: Session = Depends(get_db)):
    """PayPal redirects here after host approves subscription."""
    try:
        tenant_id = get_current_tenant_id(request)
    except HTTPException:
        return _redirect_login()

    # PayPal sends subscription_id after approval
    if not subscription_id:
        log.warning("PayPal return: no subscription_id provided")
        return RedirectResponse("/billing?msg=error", status_code=302)

    cfg = _get_or_create_config(tenant_id, db)
    try:
        from web.paypal import get_subscription
        sub_data = get_subscription(subscription_id)
        status = sub_data.get("status", "").upper()

        # APPROVAL_PENDING → host approved but subscription not yet active
        # ACTIVE → ready to charge
        if status not in ("APPROVAL_PENDING", "ACTIVE"):
            log.warning("PayPal subscription unexpected status: %s", status)
            return RedirectResponse("/billing?msg=error", status_code=302)

        # Store subscription details
        cfg.paypal_subscription_id = subscription_id
        cfg.subscription_payment_method = "paypal"

        # Update plan from subscription data if available
        plan_id = sub_data.get("plan_id", "")
        if plan_id:
            from web.paypal import PAYPAL_PLAN_IDS
            for plan_key, paypal_plan_id in PAYPAL_PLAN_IDS.items():
                if paypal_plan_id == plan_id:
                    cfg.subscription_plan = plan_key
                    break

        if status == "ACTIVE":
            cfg.subscription_status = "active"
        else:
            # APPROVAL_PENDING → will become ACTIVE after webhook
            cfg.subscription_status = "trialing"

        db.commit()

        plan_name = PLAN_INFO.get(cfg.subscription_plan or PLAN_FREE, {}).get("name", cfg.subscription_plan)
        msg = f"Success! Your {plan_name} plan via PayPal is activating."
        return RedirectResponse(f"/billing?msg=success&text={msg}", status_code=302)

    except HTTPException:
        return RedirectResponse("/billing?msg=error", status_code=302)
    except Exception as exc:
        log.error("PayPal return processing error: %s", exc)
        return RedirectResponse("/billing?msg=error", status_code=302)


@app.get("/billing/paypal/cancel", response_class=HTMLResponse)
def paypal_cancel(request: Request, db: Session = Depends(get_db)):
    """User cancelled PayPal checkout."""
    try:
        tenant_id = get_current_tenant_id(request)
    except HTTPException:
        return _redirect_login()
    return RedirectResponse("/billing?msg=cancelled", status_code=302)


@app.post("/billing/paypal/webhook")
async def paypal_webhook(request: Request, db: Session = Depends(get_db)):
    """Handle PayPal webhook events."""
    try:
        payload = await request.body()
        from web.paypal import handle_paypal_webhook
        result = handle_paypal_webhook(payload, dict(request.headers), db)
        return JSONResponse(result)
    except HTTPException as exc:
        return JSONResponse({"status": "error"}, status_code=exc.status_code)
    except Exception as exc:
        log.error("PayPal webhook error: %s", exc)
        return JSONResponse({"status": "error"}, status_code=500)


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
    admin = _require_admin(request, db)
    plans = db.query(PlanConfig).order_by(PlanConfig.min_units).all()
    return templates.TemplateResponse("admin_pricing.html", {
        "request": request,
        "admin": admin,
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
# Admin Voice Pricing Management
# ---------------------------------------------------------------------------

@app.get("/admin/voice-pricing", response_class=HTMLResponse)
def admin_voice_pricing_page(request: Request, db: Session = Depends(get_db)):
    """Display voice pricing configuration dashboard."""
    admin = _require_admin(request, db)
    voice_tiers = db.query(VoicePricingConfig).order_by(VoicePricingConfig.id).all()
    return templates.TemplateResponse("admin_voice_pricing.html", {
        "request": request,
        "admin": admin,
        "voice_tiers": voice_tiers,
        "csrf_token": request.state.csrf_token,
    })


@app.post("/admin/voice-pricing/{voice_tier}")
def admin_update_voice_pricing(voice_tier: str, request: Request,
                               monthly_price: float = Form(...),
                               overage_per_minute: float = Form(...),
                               surge_threshold: float = Form(...),
                               surge_multiplier: float = Form(...),
                               minutes_included: int = Form(None),
                               display_name: str = Form(...),
                               csrf_token: str = Form(None),
                               db: Session = Depends(get_db)):
    """Update voice pricing for a tier."""
    admin = _require_admin(request, db)
    validate_csrf(request, csrf_token)

    tier = db.query(VoicePricingConfig).filter_by(voice_tier=voice_tier).first()
    if not tier:
        raise HTTPException(status_code=404, detail="Voice tier not found")

    # Validation
    if monthly_price < 0 or overage_per_minute < 0:
        raise HTTPException(status_code=400, detail="Prices cannot be negative")
    if surge_threshold < 0 or surge_threshold > 1:
        raise HTTPException(status_code=400, detail="surge_threshold must be between 0 and 1")
    if surge_multiplier < 1:
        raise HTTPException(status_code=400, detail="surge_multiplier must be >= 1.0")
    if minutes_included is not None and minutes_included < 0:
        raise HTTPException(status_code=400, detail="minutes_included cannot be negative")

    # Update tier
    tier.monthly_price_usd = monthly_price
    tier.overage_per_minute_usd = overage_per_minute
    tier.surge_threshold = surge_threshold
    tier.surge_multiplier = surge_multiplier
    tier.minutes_included = minutes_included
    tier.display_name = display_name
    tier.updated_at = datetime.now(timezone.utc)

    db.add(tier)

    # Audit log
    db.add(ActivityLog(
        tenant_id=admin.id, event_type="admin_voice_pricing_change",
        message=f"Voice tier {voice_tier} updated: price={monthly_price} overage={overage_per_minute} mins={minutes_included} by {admin.email}"
    ))
    db.commit()

    # Admin alert
    send_admin_alert(
        f"Voice pricing changed: {voice_tier}",
        f"Admin: {admin.email}\nTier: {voice_tier}\nPrice: ${monthly_price}/mo\nOverage: ${overage_per_minute}/min\nMinutes: {minutes_included or 'Unlimited'}"
    )

    return RedirectResponse(f"/admin/voice-pricing?msg=updated&tier={voice_tier}", status_code=302)


@app.get("/api/admin/voice-pricing", response_class="application/json")
def api_get_voice_pricing(request: Request, db: Session = Depends(get_db)):
    """Get all voice pricing config (for API access)."""
    admin = _require_admin(request, db)
    tiers = db.query(VoicePricingConfig).order_by(VoicePricingConfig.id).all()
    return {
        "voice_tiers": [
            {
                "tier": t.voice_tier,
                "display_name": t.display_name,
                "monthly_price_usd": t.monthly_price_usd,
                "minutes_included": t.minutes_included,
                "overage_per_minute_usd": t.overage_per_minute_usd,
                "surge_threshold": t.surge_threshold,
                "surge_multiplier": t.surge_multiplier,
                "cost_basis_usd": t.cost_basis_usd,
                "markup_ratio": t.markup_ratio,
                "is_active": t.is_active,
                "updated_at": t.updated_at.isoformat(),
            }
            for t in tiers
        ]
    }


# ---------------------------------------------------------------------------
# Meta WhatsApp Cloud API webhooks
# ---------------------------------------------------------------------------

@app.get("/whatsapp/webhook")
def whatsapp_webhook_verify_global(request: Request, db: Session = Depends(get_db)):
    """Generic Meta webhook verification handshake for cases without tenant_id in URL."""
    from web.meta_sender import verify_webhook
    mode      = request.query_params.get("hub.mode", "")
    token     = request.query_params.get("hub.verify_token", "")
    challenge = request.query_params.get("hub.challenge", "")

    # Look for ANY tenant configuration that matches this verify_token
    # This assumes that the person setting up the app has a unique verify token.
    if token:
        cfg = db.query(TenantConfig).filter(TenantConfig.whatsapp_verify_token == token).first()
        if cfg:
            result = verify_webhook(cfg.whatsapp_verify_token or "", mode, token, challenge)
            if result:
                return HTMLResponse(content=result)

    # Fallback to shared verify token if defined in ENV (optional)
    system_token = os.environ.get("WHATSAPP_VERIFY_TOKEN", "")
    if system_token and token == system_token:
        return HTMLResponse(content=challenge)

    raise HTTPException(status_code=403, detail="Verification failed")


@app.post("/whatsapp/webhook")
async def whatsapp_webhook_inbound_global(request: Request, db: Session = Depends(get_db)):
    """Receive inbound messages from Meta Cloud API without tenant_id in URL."""
    raw_body = await request.body()
    try:
        body = json.loads(raw_body.decode("utf-8"))
    except Exception:
        return JSONResponse({"status": "ok"})

    from web.meta_sender import extract_inbound
    msgs = extract_inbound(body)
    if not msgs:
        return JSONResponse({"status": "ok"})

    # Iterate through all inbound messages extracted from this single webhook
    for msg in msgs:
        phone_number_id = (msg.get("phone_number_id") or "").strip()
        if not phone_number_id: continue

        # Identify which tenant this message belongs to based on the phone_number_id
        cfg = db.query(TenantConfig).filter(TenantConfig.whatsapp_phone_id == phone_number_id).first()
        if not cfg:
            log.warning("[META GLOBAL] No tenant found for phone_number_id=%s", phone_number_id)
            continue

        # Guard: only process if WhatsApp is actually enabled for this tenant
        if cfg.wa_mode != "meta_cloud":
            log.warning("[META GLOBAL] Ignoring message for tenant %s — wa_mode is %s", cfg.tenant_id, cfg.wa_mode)
            continue

        # Deduplicate: Meta retries webhooks — skip if we already processed this message_id
        wa_msg_id = (msg.get("message_id") or "").strip()
        if wa_msg_id:
            dedup_key = f"wa:{wa_msg_id}"
            already = db.query(ProcessedEmail).filter_by(tenant_id=cfg.tenant_id, email_uid=dedup_key).first()
            if already:
                log.info("[META GLOBAL] Duplicate WA message %s for tenant %s — skipping", wa_msg_id, cfg.tenant_id)
                continue
            db.add(ProcessedEmail(tenant_id=cfg.tenant_id, email_uid=dedup_key))
            db.commit()

        # Hand off to handler with the identified tenant_id
        _handle_inbound_wa(cfg.tenant_id, msg["from"], msg["text"], db)

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

    try:
        form = await request.form()
        form_data = dict(form)
    except Exception as e:
        log.warning("[%s] Twilio form parsing error: %s", tenant_id, e)
        return HTMLResponse("<Response/>")

    if not _validate_twilio_signature(request, form_data, cfg, channel="sms"):
        return HTMLResponse("<Response/>", status_code=403)
    from web.sms_sender import parse_twilio_inbound
    msg = parse_twilio_inbound(form_data)
    if msg:
        sms_sid = form_data.get("SmsSid") or form_data.get("MessageSid", "")
        if sms_sid:
            dedup_key = f"sms:{sms_sid}"
            already = db.query(ProcessedEmail).filter_by(tenant_id=tenant_id, email_uid=dedup_key).first()
            if already:
                log.info("[%s] Duplicate SMS %s — skipping", tenant_id, sms_sid)
                return HTMLResponse("<Response/>")
            db.add(ProcessedEmail(tenant_id=tenant_id, email_uid=dedup_key))
            db.commit()
        _handle_inbound_sms(tenant_id, msg["from"], msg["text"], db)

    return HTMLResponse("<Response/>")   # TwiML empty response


@app.post("/whatsapp/twilio/inbound/{tenant_id}")
async def twilio_whatsapp_inbound(tenant_id: str, request: Request, db: Session = Depends(get_db)):
    """Receive inbound WhatsApp messages from Twilio (same format as SMS webhook)."""
    rate_limit(f"wa-twilio-inbound:{tenant_id}:{client_ip(request)}", max_requests=200, window_seconds=60)
    cfg = db.query(TenantConfig).filter_by(tenant_id=tenant_id).first()
    if not cfg or cfg.wa_mode != "twilio":
        return HTMLResponse("<Response/>")

    try:
        form = await request.form()
        form_data = dict(form)
    except Exception as e:
        log.warning("[%s] Twilio WA form parsing error: %s", tenant_id, e)
        return HTMLResponse("<Response/>")

    if not _validate_twilio_signature(request, form_data, cfg, channel="sms"):
        return HTMLResponse("<Response/>", status_code=403)

    from web.sms_sender import parse_twilio_inbound
    msg = parse_twilio_inbound(form_data)
    if msg and msg.get("is_whatsapp"):
        sms_sid = form_data.get("SmsSid") or form_data.get("MessageSid", "")
        if sms_sid:
            dedup_key = f"twa:{sms_sid}"
            already = db.query(ProcessedEmail).filter_by(tenant_id=tenant_id, email_uid=dedup_key).first()
            if already:
                log.info("[%s] Duplicate Twilio WA message %s — skipping", tenant_id, sms_sid)
                return HTMLResponse("<Response/>")
            db.add(ProcessedEmail(tenant_id=tenant_id, email_uid=dedup_key))
            db.commit()
        _handle_inbound_wa(tenant_id, msg["from"], msg["text"], db)

    return HTMLResponse("<Response/>")


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

def _detect_language(text: str) -> Optional[str]:
    """
    Lightweight language detection using Unicode block analysis + keyword matching.
    Returns BCP 47 language code (e.g. 'es', 'fr', 'ar') or None for English/unknown.
    No external libraries required.
    """
    if not text or len(text.strip()) < 4:
        return None

    # Check Unicode blocks for non-Latin scripts first (fast path)
    for ch in text:
        cp = ord(ch)
        if 0x0600 <= cp <= 0x06FF or 0x0750 <= cp <= 0x077F:
            return "ar"  # Arabic
        if 0x4E00 <= cp <= 0x9FFF or 0x3400 <= cp <= 0x4DBF:
            return "zh"  # Chinese
        if 0x3040 <= cp <= 0x309F or 0x30A0 <= cp <= 0x30FF:
            return "ja"  # Japanese (Hiragana/Katakana)
        if 0xAC00 <= cp <= 0xD7AF or 0x1100 <= cp <= 0x11FF:
            return "ko"  # Korean
        if 0x0400 <= cp <= 0x04FF:
            return "ru"  # Cyrillic (Russian)

    low = text.lower()
    _LANG_KEYWORDS = {
        "es": ["hola", "gracias", "buenas", "habitación", "cuánto", "dónde", "por favor", "buenos días", "habitacion"],
        "fr": ["bonjour", "merci", "chambre", "s'il vous plaît", "bonsoir", "où est", "comment", "excusez"],
        "de": ["hallo", "danke", "zimmer", "bitte", "guten morgen", "guten tag", "wie lange", "wann"],
        "it": ["ciao", "grazie", "buongiorno", "camera", "quando", "dove", "prego", "buonasera"],
        "pt": ["olá", "obrigado", "obrigada", "quarto", "quando", "onde fica", "por favor", "bom dia"],
        "nl": ["hallo", "dank je", "kamer", "alsjeblieft", "goedemorgen", "wanneer"],
        "ru": ["привет", "спасибо", "комнат", "пожалуйста", "добрый"],
    }
    for lang, keywords in _LANG_KEYWORDS.items():
        if any(kw in low for kw in keywords):
            return lang
    return None  # English or unknown — don't store


def _check_upsell_opportunity(tenant_id: str, text: str, guest_contact, cfg, db: Session) -> bool:
    """
    Check if the guest message triggers an upsell offer.
    Returns True if an upsell offer was sent, False otherwise.
    """
    if not cfg or not getattr(cfg, "upsell_enabled", False):
        return False
    try:
        from web.models import UpsellOffer
        offers = db.query(UpsellOffer).filter_by(tenant_id=tenant_id, is_active=True).all()
        if not offers:
            return False
        low_text = text.lower()
        for offer in offers:
            keywords = [k.strip().lower() for k in (offer.trigger_keywords or "").split(",") if k.strip()]
            if keywords and any(kw in low_text for kw in keywords):
                log.info("[%s] Upsell triggered: %s for guest %s", tenant_id, offer.title, getattr(guest_contact, "guest_name", ""))
                # Send the upsell message via whatsapp/sms
                if guest_contact and cfg.wa_mode in ("meta_cloud", "twilio"):
                    phone = guest_contact.guest_phone
                    msg = offer.message_template
                    if cfg.wa_mode == "meta_cloud":
                        from web.meta_sender import send_whatsapp as _send_wa
                        from web.crypto import decrypt as _decrypt
                        token = _decrypt(cfg.whatsapp_token_enc) if cfg.whatsapp_token_enc else None
                        if cfg.whatsapp_phone_id and token:
                            _send_wa(cfg.whatsapp_phone_id, token, phone, msg)
                    elif cfg.wa_mode == "twilio":
                        from web.sms_sender import send_whatsapp_twilio as _send_twa
                        from web.crypto import decrypt as _decrypt
                        auth_token = _decrypt(cfg.twilio_auth_token_enc) if cfg.twilio_auth_token_enc else None
                        if cfg.twilio_whatsapp_number and cfg.twilio_account_sid and auth_token:
                            _send_twa(cfg.twilio_account_sid, auth_token, cfg.twilio_whatsapp_number, phone, msg)
                return True
    except Exception as exc:
        log.warning("[%s] Upsell check failed: %s", tenant_id, exc)
    return False


def _notify_host_pending(cfg, tenant_id: str, guest_name: str, text: str, source: str,
                         is_negative: bool, send_failed: bool, db):
    """Send host notification for a pending message. Always fires for negative/complex/failed; respects setting otherwise."""
    notify_phone = (cfg.host_notify_phone or cfg.whatsapp_number) if cfg else None

    # Fix #5: urgent prefix for negative sentiment
    # Fix #6: always notify for negative or failed auto-send regardless of setting
    force_notify = is_negative or send_failed
    if not force_notify and not getattr(cfg, "notify_host_on_guest_msg", False):
        return
    if not notify_phone:
        return

    if send_failed:
        prefix = "⚠️ Auto-reply FAILED — guest needs a manual response"
        notify_text = f"{prefix}\n\nGuest: {guest_name}\nMessage: \"{text}\"\n\n— Reply in your HostAI dashboard"
    elif is_negative:
        prefix = "🚨 UNHAPPY GUEST — needs immediate attention"
        notify_text = f"{prefix}\n\nGuest: {guest_name}\nMessage: \"{text}\"\n\n— Reply urgently in your HostAI dashboard"
    else:
        notify_text = f"📩 New guest message from {guest_name}:\n\n\"{text}\"\n\n— Reply in your HostAI dashboard"

    _send_host_notification(tenant_id, notify_phone, notify_text, guest_name, text, source, db)


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
    else:
        guest_contact = get_guest_contact_for_phone(tenant_id, reply_to, db)

    # Detect and persist guest language from their first message
    if guest_contact and not guest_contact.language_code:
        try:
            detected_lang = _detect_language(text)
            if detected_lang:
                guest_contact.language_code = detected_lang
                db.commit()
        except Exception:
            pass

    # Capture satisfaction score if guest replies with a single digit 1-5
    if guest_contact and guest_contact.satisfaction_sent_at and not guest_contact.satisfaction_score:
        stripped = text.strip()
        if stripped in ("1", "2", "3", "4", "5"):
            guest_contact.satisfaction_score = int(stripped)
            guest_contact.satisfaction_scored_at = datetime.now(timezone.utc)
            db.commit()
            log.info("[%s] Satisfaction score %s captured from %s", tenant_id, stripped, guest_contact.guest_name)
            # Thank the guest for their feedback
            score = int(stripped)
            if score >= 4:
                thank_msg = f"Thank you so much, {guest_contact.guest_name}! We're thrilled you had a great stay. Hope to see you again soon! 🌟"
            elif score == 3:
                thank_msg = f"Thank you for your feedback, {guest_contact.guest_name}! We're always working to improve and hope to exceed your expectations next time."
            else:
                thank_msg = f"Thank you for being honest, {guest_contact.guest_name}. We're sorry we didn't fully meet your expectations. We take this seriously and will work on it."
            # Auto-send review link for happy guests (4-5 stars)
            review_link_msg = None
            if score >= 4 and getattr(cfg, "review_request_enabled", False) and getattr(cfg, "review_request_url", None):
                review_link_msg = (
                    f"We're so glad you had a great experience! ⭐ Would you mind leaving us a quick review? "
                    f"It takes just 30 seconds and means the world to us:\n{cfg.review_request_url}\n\nThank you! 🙏"
                )
            try:
                if cfg.wa_mode == "twilio":
                    from web.sms_sender import send_whatsapp_twilio as _stw
                    from web.crypto import decrypt as _dcr
                    _at = _dcr(cfg.twilio_auth_token_enc) if cfg.twilio_auth_token_enc else None
                    if cfg.twilio_whatsapp_number and cfg.twilio_account_sid and _at:
                        _stw(cfg.twilio_account_sid, _at, cfg.twilio_whatsapp_number, reply_to, thank_msg)
                        if review_link_msg:
                            import time as _time; _time.sleep(1)
                            _stw(cfg.twilio_account_sid, _at, cfg.twilio_whatsapp_number, reply_to, review_link_msg)
                elif cfg.wa_mode == "meta_cloud":
                    from web.meta_sender import send_whatsapp as _swa
                    from web.crypto import decrypt as _dcr
                    _tk = _dcr(cfg.whatsapp_token_enc) if cfg.whatsapp_token_enc else None
                    if cfg.whatsapp_phone_id and _tk:
                        _swa(cfg.whatsapp_phone_id, _tk, reply_to, thank_msg)
                        if review_link_msg:
                            import time as _time; _time.sleep(1)
                            _swa(cfg.whatsapp_phone_id, _tk, reply_to, review_link_msg)
            except Exception:
                pass
            return  # Don't create a draft for satisfaction replies

    try:
        from web.classifier import classify_message_with_confidence, detect_vendor_type, generate_draft
        reservation = _find_reservation_for_guest_context(
            tenant_id, db, guest_phone=reply_to, guest_name=f"{source.title()} guest"
        )
        guest_name = reservation.guest_name if reservation else f"{source.title()} guest"
        msg_type, confidence, _matched_patterns = classify_message_with_confidence(text)

        from web import classifier as classifier_mod
        sentiment = classifier_mod.analyze_sentiment_and_intent_llm(tenant_id, text)

        vendor_type = detect_vendor_type(text) if msg_type == "complex" else None
        property_context = _property_context_for_reservation(reservation, cfg, db)
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

        # Fix #8: sync auto_send_eligible with actual auto-send conditions
        is_negative   = draft.guest_sentiment == "negative"
        is_routine    = draft.msg_type == "routine"
        should_auto_send = is_routine and not is_negative

        # Keep auto_send_eligible consistent with what we actually do
        draft.auto_send_eligible = should_auto_send

        if should_auto_send:
            draft.final_text  = draft.draft
            draft.updated_at  = datetime.now(timezone.utc)

            # Fix #1: only mark auto_sent AFTER confirmed delivery
            _cfg = _get_or_create_config(tenant_id, db)
            wa_delivered = False
            if _cfg.wa_mode == "meta_cloud" and _cfg.whatsapp_phone_id and _cfg.whatsapp_token_enc:
                try:
                    from web.meta_sender import send_whatsapp
                    from web.crypto import decrypt as _decrypt
                    _token = _decrypt(_cfg.whatsapp_token_enc)
                    _to = reply_to.replace("+", "").strip()
                    wa_delivered = send_whatsapp(_cfg.whatsapp_phone_id, _token, _to, draft.final_text)
                    if wa_delivered:
                        log.info("[%s] Auto-sent WhatsApp reply to %s", tenant_id, reply_to[-4:])
                    else:
                        log.warning("[%s] Auto-send WhatsApp failed for %s — falling back to pending", tenant_id, reply_to[-4:])
                except Exception as _wa_err:
                    log.error("[%s] Auto-send WhatsApp error: %s", tenant_id, _wa_err)

            if wa_delivered:
                draft.status = "auto_sent"
                db.add(ActivityLog(
                    tenant_id=tenant_id,
                    event_type="draft_auto_sent",
                    message=f"Auto-sent to {guest_name}: {draft.draft[:80]}"
                ))
                db.commit()
                log.info("[%s] Auto-sent routine reply to %s", tenant_id, guest_name)
                _record_timeline_event(
                    db, tenant_id, reservation,
                    "guest_message_sent",
                    f"Auto-response to {guest_name}",
                    channel=source, direction="outbound",
                    body=draft.final_text, draft=draft,
                    payload_json={"auto_sent": True, "confidence": draft.confidence},
                )
                db.commit()
            else:
                # Delivery failed — keep as pending so host sees it
                draft.status = "pending"
                db.add(ActivityLog(
                    tenant_id=tenant_id,
                    event_type="draft_auto_send_failed",
                    message=f"Auto-send failed for {guest_name} — moved to pending review",
                ))
                db.commit()
                # Notify host that delivery failed and they need to reply manually
                _notify_host_pending(cfg, tenant_id, guest_name, text, source,
                                     is_negative=False, send_failed=True, db=db)
        else:
            # Keep as pending for host review
            draft.status = "pending"
            db.commit()

            # Publish real-time notification for SSE subscribers
            try:
                from web.redis_client import get_redis as _get_redis
                r = _get_redis()
                if r:
                    r.publish(f"hostai:notify:{tenant_id}", json.dumps({
                        "guest_name": draft.guest_name,
                        "source": draft.source,
                        "msg_type": draft.msg_type,
                        "draft_id": draft.id,
                        "sentiment": draft.guest_sentiment,
                    }))
            except Exception:
                pass  # non-critical

            # Fix #5+#6: always notify for negative/complex, respect setting for routine
            _notify_host_pending(cfg, tenant_id, guest_name, text, source,
                                 is_negative=is_negative, send_failed=False, db=db)

            log.info("[%s] Pending review: %s (%s, sentiment=%s)", tenant_id, guest_name, draft.msg_type, draft.guest_sentiment)

        # Check for upsell opportunities on every inbound message
        _check_upsell_opportunity(tenant_id, text, guest_contact, cfg, db)

        _apply_automation_if_matched(db, tenant_id, draft, reservation)

    except Exception as exc:
        log.error("[%s] %s inbound handler error: %s", tenant_id, source.upper(), exc, exc_info=True)
        # Fix #3: record failed message so host and admin can see it
        try:
            import traceback as _tb
            db.add(ActivityLog(
                tenant_id=tenant_id,
                event_type="inbound_processing_failed",
                message=f"Failed to process {source.upper()} from {reply_to}: {exc}",
            ))
            db.add(FailedDraftLog(
                tenant_id=tenant_id,
                draft_id=f"inbound:{source}:{reply_to}:{datetime.now(timezone.utc).isoformat()}",
                error_reason=f"Inbound message from {reply_to} could not be processed.\nMessage: {text[:200]}\nError: {_tb.format_exc()[-800:]}",
            ))
            db.commit()
            # Alert admin so they know the AI pipeline is broken for this tenant
            from web.mailer import send_admin_alert as _admin_alert
            _admin_alert(
                f"Inbound message processing failed — tenant {tenant_id}",
                f"Source: {source}\nFrom: {reply_to}\nMessage: {text[:300]}\n\nError:\n{_tb.format_exc()[-1000:]}",
            )
        except Exception:
            pass  # never let error-recording crash the webhook


def _handle_inbound_wa(tenant_id: str, from_phone: str, text: str, db: Session):
    """Classify an inbound WhatsApp message and create a pending draft."""
    _handle_guest_inbound_message(tenant_id, "whatsapp", from_phone, text, db)


def _handle_inbound_sms(tenant_id: str, from_phone: str, text: str, db: Session):
    """Classify an inbound SMS and create a pending draft."""
    _handle_guest_inbound_message(tenant_id, "sms", from_phone, text, db)



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


def _property_context_for_reservation(
    reservation: Optional["Reservation"],
    cfg: "TenantConfig",
    db: Session,
) -> str:
    """
    Return build_property_context() using per-property config when available,
    falling back to tenant-level config if no matching property is found.
    """
    from web.classifier import build_property_context
    if reservation:
        unit = (reservation.unit_identifier or reservation.listing_name or "").strip()
        if unit:
            from web.models import Property, PropertyConfig
            prop = (
                db.query(Property)
                .filter(
                    Property.tenant_id == cfg.tenant_id,
                    Property.name == unit,
                    Property.status == "active",
                )
                .first()
            )
            if prop and prop.config:
                # Merge: per-property config takes precedence for fields it has,
                # tenant-level cfg fills in any gaps
                pc = prop.config

                class _MergedCtx:
                    """Duck-type object merging PropertyConfig over TenantConfig."""
                    property_names = prop.name
                    property_type  = pc.voice_enabled and getattr(cfg, "property_type", None) or getattr(cfg, "property_type", None)
                    property_city  = prop.city or getattr(cfg, "property_city", None)
                    check_in_time  = pc.check_in_time  or getattr(cfg, "check_in_time", None)
                    check_out_time = pc.check_out_time or getattr(cfg, "check_out_time", None)
                    max_guests     = pc.max_guests     or getattr(cfg, "max_guests", None)
                    amenities      = pc.amenities      or getattr(cfg, "amenities", None)
                    house_rules    = pc.house_rules    or getattr(cfg, "house_rules", None)
                    pet_policy     = pc.pet_policy     or getattr(cfg, "pet_policy", None)
                    parking_policy = pc.parking_policy or getattr(cfg, "parking_policy", None)
                    faq            = pc.faq            or getattr(cfg, "faq", None)
                    food_menu      = pc.food_menu      or getattr(cfg, "food_menu", None)
                    nearby_restaurants = pc.nearby_restaurants or getattr(cfg, "nearby_restaurants", None)
                    google_maps_url    = getattr(cfg, "google_maps_url", None)
                    wifi_password  = pc.wifi_password  or getattr(cfg, "wifi_password", None)
                    wifi_network_name = pc.wifi_network_name or getattr(cfg, "wifi_network_name", None)
                    # Policy fields from tenant config (not on PropertyConfig)
                    refund_policy       = getattr(cfg, "refund_policy", None)
                    early_checkin_policy= getattr(cfg, "early_checkin_policy", None)
                    early_checkin_fee   = getattr(cfg, "early_checkin_fee", None)
                    late_checkout_policy= getattr(cfg, "late_checkout_policy", None)
                    late_checkout_fee   = getattr(cfg, "late_checkout_fee", None)
                    smoking_policy      = getattr(cfg, "smoking_policy", None)
                    quiet_hours         = getattr(cfg, "quiet_hours", None)
                    extra_services      = getattr(cfg, "extra_services", None)
                    custom_instructions = getattr(cfg, "custom_instructions", None)

                return build_property_context(_MergedCtx())
    return build_property_context(cfg)


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
        # Fix #4: also normalize with phone_utils (E.164) for consistency with GuestContact storage
        from web.phone_utils import normalize_phone as _pu_norm
        phone_e164 = _pu_norm(guest_phone) or ""  # e.g. +918669024169
        candidate_reservations = (
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
        phone_matches = [
            res for res in candidate_reservations
            if _normalize_phone(res.guest_phone) == phone_digits
            or (phone_e164 and (_pu_norm(res.guest_phone) or "") == phone_e164)
        ]
        if phone_matches:
            # Fix #7: for overlapping reservations, prefer the one with an active GuestContact
            # (i.e. the one the host explicitly activated for this stay) to avoid wrong context
            if len(phone_matches) > 1:
                from web.models import GuestContact as _GC2
                now_utc = datetime.now(timezone.utc)
                active_gc_res_ids = {
                    gc.reservation_id for gc in
                    db.query(_GC2).filter(
                        _GC2.tenant_id == tenant_id,
                        _GC2.status == "active",
                        _GC2.check_out >= now_utc,
                    ).all()
                    if gc.reservation_id
                }
                gc_matches = [r for r in phone_matches if r.id in active_gc_res_ids]
                if gc_matches:
                    phone_matches = gc_matches
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
    search_query = request.query_params.get("q", "").strip().lower()

    # Build base query
    query = db.query(Reservation).filter_by(tenant_id=tenant_id)

    # Apply search filter
    if search_query:
        query = query.filter(
            (Reservation.guest_name.ilike(f"%{search_query}%")) |
            (Reservation.listing_name.ilike(f"%{search_query}%"))
        )

    all_rows = (
        query
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
        "search_query": search_query,
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
async def reservations_manual_create(
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

    checkin_date  = _parse_date(checkin)  if checkin.strip()  else None
    checkout_date = _parse_date(checkout) if checkout.strip() else None
    phone_clean   = guest_phone.strip()
    reservation = Reservation(
        tenant_id=tenant_id,
        confirmation_code=confirmation_code.strip() or f"MANUAL-{secrets.token_hex(4).upper()}",
        guest_name=guest_name.strip(),
        guest_phone=phone_clean or None,
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

    # ── Auto-activate bot + send welcome message ──────────────────────────
    # Create a GuestContact so the bot can auto-reply to this guest until checkout.
    # Also send an immediate welcome WhatsApp/SMS message.
    if phone_clean and checkin_date and checkout_date:
        from datetime import date as date_type
        from web.models import GuestContact as _GC
        from web.phone_utils import normalize_phone as _norm

        _norm_phone = _norm(phone_clean) or phone_clean
        _now = datetime.now(timezone.utc)
        # Use NOW as check_in so the bot is immediately active regardless of the reservation date.
        # checkout is end-of-day on the actual checkout date.
        _checkin_dt  = _now
        _checkout_dt = datetime.combine(checkout_date, datetime.max.time().replace(microsecond=0), tzinfo=timezone.utc)

        # Only create if not already exists (avoid duplicates on re-import)
        existing_gc = db.query(_GC).filter(
            _GC.tenant_id == tenant_id,
            _GC.guest_phone == _norm_phone,
            _GC.check_out == _checkout_dt,
        ).first()
        if not existing_gc:
            gc = _GC(
                tenant_id=tenant_id,
                reservation_id=reservation.id,
                guest_name=reservation.guest_name,
                guest_phone=_norm_phone,
                property_name=reservation.listing_name,
                room_identifier=reservation.unit_identifier,
                check_in=_checkin_dt,
                check_out=_checkout_dt,
                status="active",
                welcome_status="pending",
            )
            db.add(gc)
            db.commit()
            db.refresh(gc)

            # Send welcome message via WhatsApp or SMS
            from web.crypto import decrypt as _decrypt_manual
            cfg = _get_or_create_config(tenant_id, db)
            sent_via = None
            if cfg and cfg.wa_mode == "meta_cloud" and cfg.whatsapp_phone_id and cfg.whatsapp_token_enc:
                from web.meta_sender import send_whatsapp
                _token = _decrypt_manual(cfg.whatsapp_token_enc or "")
                if _token:
                    _listing  = reservation.listing_name or "the property"
                    _unit     = f" (Room/Unit: {reservation.unit_identifier})" if reservation.unit_identifier else ""
                    _checkout = checkout_date.strftime("%b %d, %Y")
                    if cfg.guest_welcome_template:
                        try:
                            _welcome = cfg.guest_welcome_template.format(
                                guest_name=reservation.guest_name,
                                property_name=_listing,
                                room=reservation.unit_identifier or "your room",
                            )
                        except KeyError:
                            _welcome = None
                    else:
                        _welcome = None
                    if not _welcome:
                        _welcome = (
                            f"Hi {reservation.guest_name}! 👋 Welcome to {_listing}{_unit}. "
                            f"Your checkout is on {_checkout}. "
                            f"I'm your AI host assistant — message me anytime during your stay. Enjoy! 🏠"
                        )
                    ok = send_whatsapp(cfg.whatsapp_phone_id, _token, phone_clean, _welcome)
                    if ok:
                        sent_via = "whatsapp"
                        gc.welcome_sent_at = datetime.now(timezone.utc)
                        gc.welcome_status  = "sent"
                        reservation.pre_arrival_sent = True

            if not sent_via and cfg and cfg.twilio_account_sid and cfg.twilio_auth_token_enc and cfg.twilio_from_number:
                from web.sms_sender import send_sms
                _auth = _decrypt_manual(cfg.twilio_auth_token_enc or "")
                if _auth:
                    _listing  = reservation.listing_name or "the property"
                    _welcome  = (
                        f"Hi {reservation.guest_name}! Welcome to {_listing}. "
                        f"I'm your AI host assistant — text me anytime. Checkout: {checkout_date.strftime('%b %d')}."
                    )
                    ok = send_sms(cfg.twilio_account_sid, _auth, cfg.twilio_from_number, phone_clean, _welcome)
                    if ok:
                        sent_via = "sms"
                        gc.welcome_sent_at = datetime.now(timezone.utc)
                        gc.welcome_status  = "sent"
                        reservation.pre_arrival_sent = True

            _record_timeline_event(
                db, tenant_id, reservation,
                event_type="manual_checkin",
                summary=f"Bot activated & welcome sent to {reservation.guest_name}",
                body=f"Welcome message sent via {sent_via.upper()}" if sent_via else "Bot activated (welcome not sent — no messaging channel configured)",
                payload_json={"channel": sent_via, "phone": phone_clean},
            )
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


@app.post("/reservations/{reservation_id}/checkin-now")
def reservation_checkin_now(
    reservation_id: int,
    request: Request,
    csrf_token: str = Form(None),
    db: Session = Depends(get_db),
):
    """Host manually triggers check-in: sends an immediate WhatsApp/SMS welcome message to the guest."""
    try:
        tenant_id = get_current_tenant_id(request)
    except HTTPException:
        return _redirect_login()
    validate_csrf(request, csrf_token)

    reservation = db.query(Reservation).filter_by(id=reservation_id, tenant_id=tenant_id).first()
    if not reservation:
        raise HTTPException(status_code=404, detail="Reservation not found")

    cfg = _get_or_create_config(tenant_id, db)
    guest_phone = (reservation.guest_phone or "").strip()
    guest_name  = reservation.guest_name or "Guest"
    listing     = reservation.listing_name or "the property"
    unit        = reservation.unit_identifier or ""
    checkout    = reservation.checkout.strftime("%b %d, %Y") if reservation.checkout else ""

    unit_line = f" (Room/Unit: {unit})" if unit else ""
    checkout_line = f" Your checkout is on {checkout}." if checkout else ""

    welcome_msg = (
        f"Hi {guest_name}! 👋 Welcome to {listing}{unit_line}. "
        f"You're all checked in!{checkout_line} "
        f"I'm your AI host assistant — feel free to message me anytime if you need anything during your stay. "
        f"Enjoy! 🏠"
    )

    sent_via = None
    error_msg = None

    if not guest_phone:
        error_msg = "no_phone"
    else:
        # Try WhatsApp first
        if cfg and cfg.whatsapp_phone_id and cfg.whatsapp_token_enc:
            from web.meta_sender import send_whatsapp
            token = decrypt(cfg.whatsapp_token_enc or "")
            if token:
                ok = send_whatsapp(cfg.whatsapp_phone_id, token, guest_phone, welcome_msg)
                if ok:
                    sent_via = "whatsapp"

        # Fallback: SMS via Twilio
        if not sent_via and cfg and cfg.twilio_account_sid and cfg.twilio_auth_token_enc and cfg.twilio_from_number:
            from web.sms_sender import send_sms
            auth_token = decrypt(cfg.twilio_auth_token_enc or "")
            if auth_token:
                ok = send_sms(cfg.twilio_account_sid, auth_token,
                              cfg.twilio_from_number, guest_phone, welcome_msg)
                if ok:
                    sent_via = "sms"

        if not sent_via:
            error_msg = "send_failed"

    # Record in timeline regardless of outcome
    _record_timeline_event(
        db, tenant_id, reservation,
        event_type="manual_checkin",
        title=f"Manual check-in triggered for {guest_name}",
        body=(
            f"Welcome message sent via {sent_via.upper()}" if sent_via
            else f"Check-in triggered but message could not be sent ({error_msg})"
        ),
        payload_json={"channel": sent_via, "phone": guest_phone},
    )
    if sent_via:
        reservation.pre_arrival_sent = True
    db.add(ActivityLog(
        tenant_id=tenant_id,
        event_type="manual_checkin",
        message=(
            f"Check-in now triggered for {guest_name} — sent via {sent_via.upper() if sent_via else 'N/A'}"
        ),
    ))
    db.commit()

    redirect_to = f"/reservations?checkin_sent={sent_via or 'failed'}"
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


@app.get("/analytics/roi", response_class=HTMLResponse)
def roi_dashboard(request: Request,
                  range_days: int = 30,
                  db: Session = Depends(get_db),
                  rdb: Session = Depends(get_read_db)):
    """ROI dashboard for users — show value delivered by the AI."""
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

    # Aggregate metrics
    total_hours_saved = sum(s.saved_hours or 0 for s in snapshots)
    total_messages_handled = sum(s.messages_total or 0 for s in snapshots)
    total_approvals = sum(s.approvals_total or 0 for s in snapshots)

    # Get latest automation rate
    latest_snapshot = snapshots[-1] if snapshots else None
    automation_rate = latest_snapshot.automation_rate_pct if latest_snapshot else 0

    # Calculate estimated value (hours × $25/hr)
    hourly_rate = 25.0
    estimated_value = total_hours_saved * hourly_rate

    # Serialize for chart
    snapshots_data = [
        {
            "period_start": s.period_start.isoformat(),
            "saved_hours": s.saved_hours or 0,
            "messages": s.messages_total or 0,
            "approvals": s.approvals_total or 0,
        }
        for s in snapshots
    ]

    # Satisfaction scores
    from web.models import GuestContact, UpsellOffer
    satisfaction_scores = db.query(GuestContact).filter(
        GuestContact.tenant_id == tenant_id,
        GuestContact.satisfaction_score != None,  # noqa: E711
        GuestContact.satisfaction_scored_at >= cutoff,
    ).all()
    avg_satisfaction = round(sum(g.satisfaction_score for g in satisfaction_scores) / len(satisfaction_scores), 2) if satisfaction_scores else None
    satisfaction_dist = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0}
    for g in satisfaction_scores:
        satisfaction_dist[g.satisfaction_score] = satisfaction_dist.get(g.satisfaction_score, 0) + 1

    # Upsell revenue
    upsell_offers = db.query(UpsellOffer).filter_by(tenant_id=tenant_id, is_active=True).all()
    total_upsell_revenue = sum(o.total_revenue for o in upsell_offers)
    total_upsell_accepted = sum(o.accepted_count for o in upsell_offers)

    # Language breakdown
    from collections import Counter
    lang_counts_raw = db.query(GuestContact.language_code).filter(
        GuestContact.tenant_id == tenant_id,
        GuestContact.language_code != None,  # noqa: E711
    ).all()
    lang_counts = dict(Counter(r[0] for r in lang_counts_raw if r[0]))

    return templates.TemplateResponse("analytics_roi.html", {
        "request": request,
        "tenant": tenant,
        "cfg": cfg,
        "total_hours_saved": round(total_hours_saved, 1),
        "total_messages_handled": total_messages_handled,
        "total_approvals": total_approvals,
        "automation_rate": round(automation_rate, 1),
        "estimated_value": round(estimated_value, 2),
        "hourly_rate": hourly_rate,
        "range_days": range_days,
        "snapshots_data": snapshots_data,
        # New SaaS metrics
        "avg_satisfaction": avg_satisfaction,
        "satisfaction_count": len(satisfaction_scores),
        "satisfaction_dist": satisfaction_dist,
        "total_upsell_revenue": round(total_upsell_revenue, 2),
        "total_upsell_accepted": total_upsell_accepted,
        "lang_counts": lang_counts,
    })


@app.get("/voice-calls/gaps", response_class=HTMLResponse)
def voice_gaps_page(request: Request, db: Session = Depends(get_db)):
    """Unanswered question dashboard — host fills in answers."""
    try:
        tenant_id = get_current_tenant_id(request)
    except HTTPException:
        return _redirect_login()

    from web.models import VoiceKnowledgeGap
    tenant = _get_tenant(tenant_id, db)
    cfg    = _get_or_create_config(tenant_id, db)
    open_gaps = []
    resolved_gaps = []
    voice_gaps_error = None
    try:
        open_gaps = (
            db.query(VoiceKnowledgeGap)
            .filter(VoiceKnowledgeGap.tenant_id == tenant_id, VoiceKnowledgeGap.resolved.is_(False))
            .order_by(VoiceKnowledgeGap.created_at.desc())
            .all()
        )
        resolved_gaps = (
            db.query(VoiceKnowledgeGap)
            .filter(VoiceKnowledgeGap.tenant_id == tenant_id, VoiceKnowledgeGap.resolved.is_(True))
            .order_by(VoiceKnowledgeGap.resolved_at.desc())
            .limit(20)
            .all()
        )
    except SQLAlchemyError:
        db.rollback()
        log.exception("Voice gaps page failed to load for tenant %s", tenant_id)
        voice_gaps_error = "Voice knowledge-gap data is temporarily unavailable. Run the latest database migrations, then reload this page."

    return templates.TemplateResponse("voice_gaps.html", {
        "request": request,
        "tenant": tenant,
        "cfg": cfg,
        "open_gaps": open_gaps,
        "resolved_gaps": resolved_gaps,
        "voice_gaps_error": voice_gaps_error,
    })


@app.post("/voice-calls/gaps/{gap_id}/resolve")
async def resolve_voice_gap(
    request: Request,
    gap_id: str,
    answer:          str = Form(...),
    save_to:         str = Form("faq"),
    send_to_guest:   str = Form("no"),       # yes | no
    reply_channel:   str = Form("sms"),      # sms | whatsapp
    guest_phone:     str = Form(""),         # host can correct the number
    guest_name:      str = Form(""),         # host can correct the name
    csrf_token:      str = Form(None),
    db: Session = Depends(get_db),
):
    """
    Save host's answer to property config, mark gap resolved,
    and optionally send the answer back to the guest via SMS/WhatsApp.
    """
    try:
        tenant_id = get_current_tenant_id(request)
    except HTTPException:
        return _redirect_login()
    validate_csrf(request, csrf_token)

    from web.models import VoiceKnowledgeGap
    gap = db.query(VoiceKnowledgeGap).filter(
        VoiceKnowledgeGap.id == gap_id,
        VoiceKnowledgeGap.tenant_id == tenant_id,
    ).first()
    if not gap:
        raise HTTPException(status_code=404, detail="Gap not found")

    cfg     = _get_or_create_config(tenant_id, db)
    tenant  = _get_tenant(tenant_id, db)
    now     = datetime.now(timezone.utc)
    answer  = answer.strip()

    # ── 1. Save answer to chosen property config field ────────────────────────
    qa_entry = f"\nQ: {gap.question}\nA: {answer}"
    if save_to == "faq":
        cfg.faq = (cfg.faq or "") + qa_entry
    elif save_to == "custom_instructions":
        cfg.custom_instructions = (cfg.custom_instructions or "") + qa_entry
    elif save_to == "house_rules":
        cfg.house_rules = (cfg.house_rules or "") + qa_entry
    elif save_to == "amenities":
        cfg.amenities = (cfg.amenities or "") + f"\n{answer}"
    elif save_to == "parking_policy":
        cfg.parking_policy = (cfg.parking_policy or "") + f"\n{answer}"
    elif save_to == "nearby_restaurants":
        cfg.nearby_restaurants = (cfg.nearby_restaurants or "") + f"\n{answer}"

    # ── 2. Resolve gap ────────────────────────────────────────────────────────
    # Host may have corrected the phone/name on the form
    effective_phone = guest_phone.strip() or gap.guest_phone or ""
    effective_name  = guest_name.strip()  or gap.guest_name  or ""

    gap.host_answer  = answer
    gap.saved_to     = save_to
    gap.resolved     = True
    gap.resolved_at  = now
    gap.guest_phone  = effective_phone or gap.guest_phone
    gap.guest_name   = effective_name  or gap.guest_name

    # ── 3. Send reply to guest (if host opted in) ─────────────────────────────
    reply_sent = False
    if send_to_guest == "yes" and effective_phone:
        prop_name  = cfg.property_names or "your host"
        name_part  = f"Hi {effective_name.split()[0]}! " if effective_name else "Hi! "
        room_part  = f" (Room/Unit: {gap.guest_room})" if gap.guest_room else ""
        message    = (
            f"{name_part}Following up on your call to {prop_name}{room_part}.\n\n"
            f"❓ You asked:\n\"{gap.question}\"\n\n"
            f"✅ Answer from your host:\n{answer}\n\n"
            f"Feel free to call us back anytime if you need more help!"
        )
        reply_sent = _send_voice_message(cfg, effective_phone, message, reply_channel)
        if reply_sent:
            gap.reply_sent    = True
            gap.reply_sent_at = now
            gap.reply_channel = reply_channel
            log.info(f"[VOICE] Gap reply sent to {effective_phone[-4:]} via {reply_channel}")

    # ── 4. Close related issue ticket (if exists) ──────────────────────────────
    if gap.issue_ticket_id:
        try:
            from web.models import IssueTicket
            ticket = db.query(IssueTicket).filter(IssueTicket.id == gap.issue_ticket_id).first()
            if ticket:
                ticket.status = "closed"
                ticket.resolution_notes = answer
                ticket.resolved_at = now
                log.info(f"[VOICE] Closed issue ticket #{ticket.id} for gap {gap_id}")
        except Exception as e:
            log.error(f"[VOICE] Error closing ticket: {e}")

    # ── 5. Activity log ───────────────────────────────────────────────────────
    detail = f"saved to {save_to}"
    if reply_sent:
        detail += f", replied to guest via {reply_channel}"
    db.add(ActivityLog(
        tenant_id=tenant_id,
        event_type="voice_gap_resolved",
        message=f"Knowledge gap answered: \"{gap.question[:80]}\" — {detail}",
        created_at=now,
    ))
    db.commit()

    return RedirectResponse("/voice-calls/gaps", status_code=303)


@app.get("/voice-calls", response_class=HTMLResponse)
def voice_calls_page(request: Request,
                     page: int = 1,
                     date_from: str = None,
                     date_to: str = None,
                     status: str = None,
                     sentiment: str = None,
                     search: str = None,
                     tab: str = "panel",
                     saved: bool = False,
                     db: Session = Depends(get_db)):
    try:
        tenant_id = get_current_tenant_id(request)
    except HTTPException:
        return _redirect_login()

    tenant = _get_tenant(tenant_id, db)
    cfg = _get_or_create_config(tenant_id, db)

    from web.models import VoiceCall
    per_page = 25
    offset = (page - 1) * per_page
    calls = []
    total = 0
    open_gaps_count = 0
    voice_calls_error = None

    tab_normalized = (tab or "").strip().lower()
    if tab_normalized not in {"panel", "settings", "test"}:
        tab_normalized = "panel"

    # Only load call-log tables when on the Panel tab. This keeps Settings usable
    # even if voice call tables are behind migrations.
    if tab_normalized == "panel":
        try:
            # Build query with filters
            query = db.query(VoiceCall).filter(VoiceCall.tenant_id == tenant_id)

            # Date range filter
            if date_from:
                try:
                    from_dt = datetime.fromisoformat(date_from).replace(tzinfo=timezone.utc)
                    query = query.filter(VoiceCall.created_at >= from_dt)
                except Exception:
                    pass

            if date_to:
                try:
                    to_dt = datetime.fromisoformat(date_to).replace(tzinfo=timezone.utc, hour=23, minute=59, second=59)
                    query = query.filter(VoiceCall.created_at <= to_dt)
                except Exception:
                    pass

            # Status filter
            if status:
                query = query.filter(VoiceCall.status == status)

            # Sentiment filter
            if sentiment:
                query = query.filter(VoiceCall.sentiment == sentiment)

            # Search filter (phone, name, transcript)
            if search:
                search_term = f"%{search}%"
                query = query.filter(
                    (VoiceCall.guest_phone_number.ilike(search_term)) |
                    (VoiceCall.full_transcript.ilike(search_term))
                )

            total = query.count()
            calls = (
                query
                .order_by(VoiceCall.created_at.desc())
                .offset(offset)
                .limit(per_page)
                .all()
            )

            from web.models import VoiceKnowledgeGap
            open_gaps_count = (
                db.query(VoiceKnowledgeGap)
                .filter(VoiceKnowledgeGap.tenant_id == tenant_id, VoiceKnowledgeGap.resolved.is_(False))
                .count()
            )
        except SQLAlchemyError:
            db.rollback()
            log.exception("Voice calls page failed to load for tenant %s", tenant_id)
            voice_calls_error = "Voice calling data is temporarily unavailable. Run the latest database migrations, then reload this page."

    return templates.TemplateResponse("voice_calls.html", {
        "request": request,
        "tenant": tenant,
        "cfg": cfg,
        "calls": calls,
        "total": total,
        "page": page,
        "per_page": per_page,
        "total_pages": max(1, (total + per_page - 1) // per_page),
        "open_gaps_count": open_gaps_count,
        "date_from": date_from,
        "date_to": date_to,
        "status": status,
        "sentiment": sentiment,
        "search": search,
        "tab": tab_normalized,
        "saved": saved,
        "app_base_url": APP_BASE_URL,
        "voice_calls_error": voice_calls_error,
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


@app.post("/api/twilio/phone-numbers")
async def fetch_twilio_phone_numbers(request: Request, db: Session = Depends(get_db)):
    """Fetch available phone numbers from Twilio account"""
    try:
        tenant_id = get_current_tenant_id(request)
    except HTTPException:
        raise HTTPException(status_code=401)

    try:
        data = await request.json()
        account_sid = data.get("account_sid")
        auth_token = data.get("auth_token")

        if not account_sid or not auth_token:
            return JSONResponse({
                "status": 400,
                "error": "Missing Account SID or Auth Token"
            })

        from twilio.rest import Client as TwilioClient

        client = TwilioClient(account_sid, auth_token)
        incoming_numbers = client.incoming_phone_numbers.stream()

        numbers = []
        for phone in incoming_numbers:
            numbers.append({
                "number": phone.phone_number,
                "friendly_name": phone.friendly_name or "Unnamed"
            })

        if not numbers:
            return JSONResponse({
                "status": 400,
                "error": "No phone numbers found. Create one in Twilio Console first."
            })

        return JSONResponse({
            "status": 200,
            "numbers": numbers
        })
    except Exception as e:
        log.error(f"Twilio fetch error: {str(e)}")
        return JSONResponse({
            "status": 400,
            "error": f"Invalid credentials or Twilio error: {str(e)}"
        })


@app.get("/api/properties")
async def get_properties(request: Request, db: Session = Depends(get_db)):
    """Get list of properties for the current tenant"""
    try:
        tenant_id = get_current_tenant_id(request)
    except HTTPException:
        raise HTTPException(status_code=401)

    from web.models import Property

    properties = db.query(Property).filter(
        Property.tenant_id == tenant_id,
        Property.status == "active"
    ).order_by(Property.created_at).all()

    return JSONResponse({
        "status": 200,
        "properties": [
            {
                "id": p.id,
                "name": p.name,
                "type": p.property_type,
                "city": p.city,
                "status": p.status,
            }
            for p in properties
        ]
    })


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
    rate_limit(f"simulate:{tenant_id}", max_requests=100, window_seconds=3600)

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


@app.post("/api/simulate/json")
async def simulate_guest_json(
    request: Request,
    guest_name: str = Form("Demo Guest"),
    message: str = Form("Hi, what time is check-in?"),
    history: str = Form(None),
    csrf_token: str = Form(None),
    db: Session = Depends(get_db),
):
    """Simulate guest message → return AI draft as JSON (for settings widget)."""
    import json as _json
    try:
        tenant_id = get_current_tenant_id(request)
        validate_csrf(request, csrf_token)
        rate_limit(f"simulate:{tenant_id}", max_requests=100, window_seconds=3600)
        cfg = _get_or_create_config(tenant_id, db)

        from web.classifier import classify_message_with_confidence, generate_draft, make_draft_id

        guest_name = (guest_name or "Demo Guest").strip()[:128]
        message = (message or "Hi, what time is check-in?").strip()[:2000]

        history_list = None
        if history:
            try:
                parsed = _json.loads(history)
                if isinstance(parsed, list):
                    history_list = [
                        {"role": h["role"], "content": str(h["content"])[:1000]}
                        for h in parsed
                        if isinstance(h, dict) and h.get("role") in ("user", "assistant") and h.get("content")
                    ][-20:]
            except Exception:
                pass

        msg_type, confidence, _ = classify_message_with_confidence(message)
        property_context = _property_context_for_reservation(None, cfg, db)
        draft_text = generate_draft(
            guest_name,
            message,
            msg_type,
            property_context=property_context,
            tenant_id=tenant_id,
            history=history_list,
        )

        try:
            draft_id = make_draft_id("simulate")
            db.add(Draft(
                id=draft_id, tenant_id=tenant_id, source="simulate",
                guest_name=guest_name, message=message,
                reply_to=None, msg_type=msg_type, vendor_type=None,
                draft=draft_text, status="pending", confidence=confidence,
            ))
            db.commit()
        except Exception as save_err:
            log.warning(f"[simulate/json] Draft save failed (non-critical): {save_err}")
            db.rollback()
            draft_id = "unsaved"

        return JSONResponse({
            "ok": True,
            "draft": draft_text,
            "msg_type": msg_type,
            "confidence": int(round(confidence * 100)),
            "draft_id": draft_id,
        })

    except HTTPException:
        raise  # let FastAPI handle 401/403/429 normally
    except Exception as e:
        log.error(f"[simulate/json] Unhandled error: {e}", exc_info=True)
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.post("/api/simulate/send")
async def simulate_and_send(
    request: Request,
    to_phone: str = Form(...),
    guest_name: str = Form("Demo Guest"),
    message: str = Form("Hi, what time is check-in?"),
    csrf_token: str = Form(None),
    db: Session = Depends(get_db),
):
    """Generate AI response to a simulated guest message and send it via WhatsApp."""
    try:
        tenant_id = get_current_tenant_id(request)
        validate_csrf(request, csrf_token)
        rate_limit(f"simulate:{tenant_id}", max_requests=100, window_seconds=3600)
        cfg = _get_or_create_config(tenant_id, db)

        from web.classifier import classify_message_with_confidence, generate_draft, make_draft_id

        if not cfg.whatsapp_phone_id or not cfg.whatsapp_token_enc:
            return JSONResponse(
                {"ok": False, "error": "WhatsApp not configured. Add your Phone ID and Access Token in Messaging Settings first."},
                status_code=400,
            )

        guest_name = (guest_name or "Demo Guest").strip()[:128]
        message = (message or "Hi, what time is check-in?").strip()[:2000]
        to_phone = to_phone.strip()

        msg_type, confidence, _ = classify_message_with_confidence(message)
        property_context = _property_context_for_reservation(None, cfg, db)
        draft_text = generate_draft(
            guest_name, message, msg_type,
            property_context=property_context, tenant_id=tenant_id,
        )

        # Send via WhatsApp
        from web.meta_sender import send_whatsapp
        token = decrypt(cfg.whatsapp_token_enc)
        to_normalized = to_phone.replace("+", "").strip()
        wa_error: dict = {}
        sent = send_whatsapp(cfg.whatsapp_phone_id, token, to_normalized, draft_text, error_detail=wa_error)

        if not sent:
            detail = wa_error.get('body', '')
            try:
                import json as _json
                parsed = _json.loads(detail)
                meta_msg = (parsed.get('error', {}) or {}).get('message') or detail
            except Exception:
                meta_msg = detail
            err_msg = (
                f"WhatsApp API error (HTTP {wa_error.get('code', '?')}): {meta_msg}"
                if meta_msg else
                "WhatsApp send failed. Check your Phone ID, Access Token, and phone number format (+E.164)."
            )
            return JSONResponse({"ok": False, "error": err_msg}, status_code=400)

        # Save as simulate draft for audit trail (non-critical)
        try:
            draft_id = make_draft_id("simulate")
            db.add(Draft(
                id=draft_id, tenant_id=tenant_id, source="simulate",
                guest_name=guest_name, message=message,
                reply_to=to_phone, msg_type=msg_type, vendor_type=None,
                draft=draft_text, status="auto_sent", confidence=confidence,
            ))
            db.commit()
        except Exception as save_err:
            log.warning(f"[simulate/send] Draft save failed (non-critical): {save_err}")
            db.rollback()
            draft_id = "unsaved"

        return JSONResponse({
            "ok": True,
            "draft": draft_text,
            "msg_type": msg_type,
            "confidence": int(round(confidence * 100)),
            "message": f"✅ AI response sent to {to_phone} via WhatsApp",
        })

    except HTTPException:
        raise
    except Exception as e:
        log.error(f"[simulate/send] Unhandled error: {e}", exc_info=True)
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.post("/api/simulate/booking")
async def simulate_booking_welcome(
    request: Request,
    guest_name: str = Form("Alex Guest"),
    guest_phone: str = Form(...),
    check_in: str = Form(...),
    check_out: str = Form(...),
    csrf_token: str = Form(None),
    db: Session = Depends(get_db),
):
    """Simulate a new booking and send the bot's welcome message via WhatsApp."""
    tenant_id = get_current_tenant_id(request)
    validate_csrf(request, csrf_token)
    rate_limit(f"simulate:{tenant_id}", max_requests=100, window_seconds=3600)
    cfg = _get_or_create_config(tenant_id, db)

    if not cfg.whatsapp_phone_id or not cfg.whatsapp_token_enc:
        return JSONResponse(
            {"ok": False, "error": "WhatsApp not configured. Add your Phone ID and Access Token in Messaging Settings first."},
            status_code=400,
        )

    guest_name = (guest_name or "Alex Guest").strip()[:128]
    guest_phone = guest_phone.strip()

    # Parse dates (accept YYYY-MM-DD)
    try:
        from datetime import date
        check_in_dt = datetime.strptime(check_in, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        check_out_dt = datetime.strptime(check_out, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return JSONResponse({"ok": False, "error": "Invalid date format. Use YYYY-MM-DD."}, status_code=400)

    # Build fake guest contact (in-memory only, no DB save)
    from web.models import GuestContact as GCModel
    fake_gc = GCModel(
        tenant_id=tenant_id,
        guest_name=guest_name,
        guest_phone=guest_phone,
        check_in=check_in_dt,
        check_out=check_out_dt,
        property_name=cfg.property_names or None,
        room_identifier=None,
        status="active",
    )

    # Build the welcome message using the same logic as real bookings
    from web.guest_contact_service import _build_guest_welcome_message
    welcome_text = _build_guest_welcome_message(fake_gc, cfg)

    # Send via WhatsApp
    try:
        from web.meta_sender import send_whatsapp
        from web.crypto import decrypt
        token = decrypt(cfg.whatsapp_token_enc)
        phone_normalized = guest_phone.replace("+", "").strip()
        wa_detail: dict = {}
        sent = send_whatsapp(cfg.whatsapp_phone_id, token, phone_normalized, welcome_text, error_detail=wa_detail)
    except Exception as e:
        logger.error(f"[{tenant_id}] Booking simulate send error: {e}")
        return JSONResponse({"ok": False, "error": f"Failed to send via WhatsApp: {str(e)}"}, status_code=500)

    if not sent:
        detail = wa_detail.get("body", "")
        try:
            import json as _json
            parsed = _json.loads(detail)
            meta_msg = (parsed.get("error", {}) or {}).get("message") or detail
        except Exception:
            meta_msg = detail
        err_msg = (
            f"WhatsApp API error (HTTP {wa_detail.get('code', '?')}): {meta_msg}"
            if meta_msg
            else "WhatsApp send failed. Check your Phone ID, Access Token, and phone number format (+E.164)."
        )
        return JSONResponse({"ok": False, "error": err_msg}, status_code=400)

    # Extract Meta message ID from response body for confirmation
    meta_msg_id = None
    try:
        import json as _json
        resp_data = _json.loads(wa_detail.get("body", "{}"))
        messages = resp_data.get("messages", [])
        if messages:
            meta_msg_id = messages[0].get("id")
    except Exception:
        pass

    return JSONResponse({
        "ok": True,
        "welcome_text": welcome_text,
        "meta_msg_id": meta_msg_id,
        "message": f"✅ Booking welcome message sent to {guest_phone} via WhatsApp",
    })


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

    try:
        tenants = db.query(Tenant).order_by(Tenant.created_at.desc()).all()
    except Exception as e:
        db.rollback()
        log.error(f"Failed to query tenants: {e}")
        return templates.TemplateResponse("error.html", {
            "request": request,
            "code": 503,
            "title": "Database Schema Error",
            "message": "Database schema is inconsistent. Migrations may still be running.",
            "debug_detail": str(e)
        }, status_code=503)

    # Load configs - try ORM first, fall back to raw query
    configs = {}
    try:
        configs = {c.tenant_id: c for c in db.query(TenantConfig).all()}
    except Exception as e:
        db.rollback()
        log.warning(f"ORM query failed for TenantConfig: {e}. Trying raw SQL.")
        try:
            # Query only columns that definitely exist
            result = db.execute(sa.text("""
                SELECT id, tenant_id, subscription_plan, subscription_status,
                       onboarding_complete, onboarding_step, imap_host,
                       email_address FROM tenant_configs
            """))
            for row in result:
                configs[row.tenant_id] = type('Config', (), {
                    'subscription_plan': row.subscription_plan,
                    'subscription_status': row.subscription_status,
                    'onboarding_complete': row.onboarding_complete,
                    'onboarding_step': row.onboarding_step,
                    'imap_host': row.imap_host,
                    'email_address': row.email_address,
                })()
        except Exception as e2:
            db.rollback()
            log.error(f"Raw SQL query also failed: {e2}")

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

    # Per-tenant pending draft counts
    pending_by_tenant = {}
    for d in drafts_30d:
        if d.status == "pending":
            pending_by_tenant[d.tenant_id] = pending_by_tenant.get(d.tenant_id, 0) + 1
    for row in tenant_rows:
        row["pending_drafts"] = pending_by_tenant.get(row["tenant"].id, 0)

    # Response time analytics per tenant (last 30 days)
    response_time_by_tenant = {}
    for row in tenant_rows:
        tenant_drafts = [d for d in drafts_30d if d.tenant_id == row["tenant"].id]
        response_time_by_tenant[row["tenant"].id] = _response_time_stats(tenant_drafts)

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

    # Ensure response_time_by_tenant has all required keys for aggregation
    for row in tenant_rows:
        if row["tenant"].id not in response_time_by_tenant:
            response_time_by_tenant[row["tenant"].id] = {"avg": 0, "p50": 0, "p95": 0, "count": 0}

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
        "drafts":        drafts_30d if drafts_30d else [],
        "response_time_by_tenant": response_time_by_tenant,
        "churn_signals": churn_signals,
        "now":           now_utc,
    })


@app.get("/admin/tenants", response_class=HTMLResponse)
def admin_tenants_list(
    request: Request,
    db: Session = Depends(get_db),
    q: str = "",
    plan: str = "",
    status: str = "",
):
    """Searchable tenant listing page."""
    admin = _require_admin(request, db)
    query = db.query(Tenant, TenantConfig).outerjoin(TenantConfig, TenantConfig.tenant_id == Tenant.id)
    if q:
        query = query.filter(Tenant.email.ilike(f"%{q}%"))
    tenants_raw = query.order_by(Tenant.created_at.desc()).all()

    rows = []
    for t, cfg in tenants_raw:
        plan_val = (cfg.subscription_plan if cfg else "free") or "free"
        sub_status = (cfg.subscription_status if cfg else "") or "—"
        if plan and plan_val.lower() != plan.lower():
            continue
        if status and sub_status.lower() != status.lower():
            continue
        rows.append({
            "tenant": t,
            "plan": plan_val.title(),
            "sub_status": sub_status,
            "onboarding_step": cfg.onboarding_step if cfg else 0,
        })

    return templates.TemplateResponse("admin_tenants_list.html", {
        "request": request,
        "admin": admin,
        "rows": rows,
        "q": q,
        "plan_filter": plan,
        "status_filter": status,
        "total": len(rows),
    })


@app.get("/admin/tenants/{tid}", response_class=HTMLResponse)
def admin_tenant_detail(tid: str, request: Request, db: Session = Depends(get_db)):
    admin = _require_admin(request, db)
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
        "admin":             admin,
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


@app.get("/admin/tenants/{tid}/nudge", response_class=HTMLResponse)
def admin_nudge_tenant(tid: str, request: Request, db: Session = Depends(get_db)):
    """Send a re-engagement nudge email to a churning tenant."""
    admin = _require_admin(request, db)
    tenant = db.query(Tenant).filter_by(id=tid).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    try:
        from services.email_service import send_email
        send_email(
            to=tenant.email,
            subject="We miss you on HostAI!",
            body=(
                f"Hi {tenant.first_name or 'there'},\n\n"
                "We noticed you haven't been active on HostAI recently. "
                "Your AI assistant is ready to help handle guest messages whenever you need it.\n\n"
                "Log in to see what's waiting for you: https://app.hostai.com/dashboard\n\n"
                "If you have any questions, just reply to this email.\n\n"
                "— The HostAI Team"
            ),
        )
    except Exception:
        pass
    db.add(ActivityLog(
        tenant_id=tid,
        event_type="admin_nudge_sent",
        message=f"Re-engagement nudge sent by admin {admin.email}",
    ))
    db.commit()
    return RedirectResponse("/admin", status_code=303)


@app.post("/admin/tenants/{tid}/mark-contacted")
def admin_mark_contacted(
    tid: str,
    request: Request,
    csrf_token: str = Form(None),
    db: Session = Depends(get_db),
):
    """Mark a churning tenant as contacted so they leave the churn signals list."""
    admin = _require_admin(request, db)
    validate_csrf(request, csrf_token)
    db.add(ActivityLog(
        tenant_id=tid,
        event_type="admin_mark_contacted",
        message=f"Marked as contacted by admin {admin.email}",
    ))
    db.commit()
    return RedirectResponse("/admin", status_code=303)


@app.get("/admin/system", response_class=HTMLResponse)
def admin_system(request: Request, db: Session = Depends(get_db)):
    db.rollback() # Ensure safe start for system inventory
    admin = _require_admin(request, db)

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

        # Baileys support removed (using official WhatsApp Business API instead)
        bot_heartbeat_age_min = None
        if cfg and cfg.bot_last_heartbeat:
            # Timezone-aware comparison safety
            last_hb = cfg.bot_last_heartbeat
            if last_hb.tzinfo is None:
                last_hb = last_hb.replace(tzinfo=timezone.utc)
            bot_heartbeat_age_min = max(0, (now_utc - last_hb).total_seconds() // 60)

        system_rows.append({
            "tenant":              t,
            "cfg":                 cfg,
            "ws":                  ws,
            "email_conf":          email_conf,
            "cal_conf":            cal_conf,
            "any_dead":            any_dead,
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
        "admin":          admin,
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
    admin = _require_admin(request, db)

    sys_conf = load_system_config(db, create_if_missing=True) or SystemConfig()
    schema_drift = system_config_schema_is_behind(sys_conf)

    usage_logs = []
    total_cost = 0.0

    try:
        from datetime import timedelta
        start_date = datetime.now(timezone.utc) - timedelta(days=30)
        usage_logs = db.query(APIUsageLog).filter(APIUsageLog.created_at >= start_date).order_by(APIUsageLog.created_at.desc()).limit(100).all()
        total_cost = sum(log.cost_usd for log in db.query(APIUsageLog).filter(APIUsageLog.created_at >= start_date).all())
    except Exception:
        # api_usage_logs table may not exist yet if migrations haven't fully run
        pass

    return templates.TemplateResponse("admin_ai.html", {
        "request": request,
        "admin": admin,
        "sys_conf": sys_conf,
        "schema_drift": schema_drift,
        "logs": usage_logs,
        "total_cost": total_cost,
    })


# Voice AI pages only depend on this subset of system_config fields. Optional
# R2 storage fields are intentionally excluded so they do not keep the warning
# visible when live calling still works.
VOICE_AI_CRITICAL_SYSTEM_CONFIG_FIELDS = {
    "openrouter_api_key_enc",
    "deepgram_api_key_enc",
    "elevenlabs_api_key_enc",
    "google_tts_api_key_enc",
    "voice_tts_provider",
    "voice_llm_model",
    "voice_llm_backup_model",
    "voice_llm_emergency_model",
    "voice_deepgram_model",
    "voice_llm_max_tokens",
    "voice_llm_temperature",
    "voice_elevenlabs_model",
    "voice_elevenlabs_stability",
    "voice_elevenlabs_similarity",
    "voice_elevenlabs_voice_id",
    "voice_google_tts_voice",
    "voice_google_tts_language",
    "voice_google_tts_speaking_rate",
}


def _voice_ai_critical_schema_missing(db: Session) -> set[str]:
    try:
        missing = missing_system_config_columns(db) & VOICE_AI_CRITICAL_SYSTEM_CONFIG_FIELDS
    except Exception:
        return set(VOICE_AI_CRITICAL_SYSTEM_CONFIG_FIELDS)

    if not missing:
        return missing

    try:
        from web.db import db_migrate

        db_migrate()
        db.rollback()
        return missing_system_config_columns(db) & VOICE_AI_CRITICAL_SYSTEM_CONFIG_FIELDS
    except Exception as exc:
        log.warning("Voice AI schema repair failed: %s", exc)
        return missing


@app.get("/admin/voice-ai", response_class=HTMLResponse)
def admin_voice_ai(request: Request, db: Session = Depends(get_db)):
    """Admin Voice AI configuration and live calling"""
    admin = _require_admin(request, db)
    sys_conf = load_system_config(db, create_if_missing=True) or SystemConfig()
    schema_drift = bool(_voice_ai_critical_schema_missing(db))

    return templates.TemplateResponse("admin_voice_ai.html", {
        "request": request,
        "admin": admin,
        "sys_conf": sys_conf,
        "schema_drift": schema_drift,
    })


@app.post("/admin/voice-ai/save")
async def admin_voice_ai_save(
    request: Request,
    openrouter_api_key_enc: str = Form(""),
    deepgram_api_key_enc: str = Form(""),
    elevenlabs_api_key_enc: str = Form(""),
    google_tts_api_key_enc: str = Form(""),
    voice_tts_provider: str = Form("google"),
    voice_google_tts_voice: str = Form(""),
    voice_google_tts_language: str = Form(""),
    voice_google_tts_speaking_rate: str = Form(""),
    cloudflare_account_id: str = Form(""),
    cloudflare_r2_access_key_enc: str = Form(""),
    cloudflare_r2_secret_key_enc: str = Form(""),
    cloudflare_r2_bucket: str = Form(""),
    voice_llm_model: str = Form(""),
    voice_llm_backup_model: str = Form(""),
    voice_llm_emergency_model: str = Form(""),
    voice_deepgram_model: str = Form(""),
    voice_llm_max_tokens: str = Form(""),
    voice_llm_temperature: str = Form(""),
    voice_elevenlabs_model: str = Form(""),
    voice_elevenlabs_voice_id: str = Form(""),
    voice_elevenlabs_voice_id_custom: str = Form(""),
    voice_elevenlabs_stability: str = Form(""),
    voice_elevenlabs_similarity: str = Form(""),
    db: Session = Depends(get_db),
):
    """Save Voice AI configuration"""
    admin = _require_admin(request, db)
    sys_conf = load_system_config(db, create_if_missing=True) or SystemConfig()

    # Update API Keys (only if provided and not masked)
    if openrouter_api_key_enc.strip() and openrouter_api_key_enc != "********":
        sys_conf.openrouter_api_key_enc = encrypt(openrouter_api_key_enc.strip())
    if deepgram_api_key_enc.strip() and deepgram_api_key_enc != "********":
        sys_conf.deepgram_api_key_enc = encrypt(deepgram_api_key_enc.strip())
    if elevenlabs_api_key_enc.strip() and elevenlabs_api_key_enc != "********":
        sys_conf.elevenlabs_api_key_enc = encrypt(elevenlabs_api_key_enc.strip())
    if google_tts_api_key_enc.strip() and google_tts_api_key_enc != "********":
        sys_conf.google_tts_api_key_enc = encrypt(google_tts_api_key_enc.strip())

    # TTS provider selection
    if voice_tts_provider.strip() in ("google", "elevenlabs"):
        sys_conf.voice_tts_provider = voice_tts_provider.strip()
    if voice_google_tts_voice.strip():
        sys_conf.voice_google_tts_voice = voice_google_tts_voice.strip()
    if voice_google_tts_language.strip():
        # Normalize to first two parts: "en-US-Neural2-F" or "en-US-Neural2" → "en-US"
        lang_parts = voice_google_tts_language.strip().split('-')
        sys_conf.voice_google_tts_language = '-'.join(lang_parts[:2]) if len(lang_parts) >= 2 else voice_google_tts_language.strip()
    try:
        if voice_google_tts_speaking_rate.strip():
            sys_conf.voice_google_tts_speaking_rate = float(voice_google_tts_speaking_rate.strip())
    except (ValueError, AttributeError):
        sys_conf.voice_google_tts_speaking_rate = 1.0

    # R2 credentials (account_id and bucket are plain text; keys are encrypted)
    if cloudflare_account_id.strip():
        sys_conf.cloudflare_account_id = cloudflare_account_id.strip()
    if cloudflare_r2_bucket.strip():
        sys_conf.cloudflare_r2_bucket = cloudflare_r2_bucket.strip()
    if cloudflare_r2_access_key_enc.strip() and cloudflare_r2_access_key_enc != "********":
        sys_conf.cloudflare_r2_access_key_enc = encrypt(cloudflare_r2_access_key_enc.strip())
    if cloudflare_r2_secret_key_enc.strip() and cloudflare_r2_secret_key_enc != "********":
        sys_conf.cloudflare_r2_secret_key_enc = encrypt(cloudflare_r2_secret_key_enc.strip())

    # Update Voice AI settings
    if voice_llm_model.strip():
        sys_conf.voice_llm_model = voice_llm_model.strip()
    if voice_llm_backup_model.strip():
        sys_conf.voice_llm_backup_model = voice_llm_backup_model.strip()
    if voice_llm_emergency_model.strip():
        sys_conf.voice_llm_emergency_model = voice_llm_emergency_model.strip()
    if voice_deepgram_model.strip():
        sys_conf.voice_deepgram_model = voice_deepgram_model.strip()

    # Parse numeric values safely
    try:
        if voice_llm_max_tokens.strip():
            sys_conf.voice_llm_max_tokens = int(voice_llm_max_tokens.strip())
    except (ValueError, AttributeError):
        sys_conf.voice_llm_max_tokens = 300

    try:
        if voice_llm_temperature.strip():
            sys_conf.voice_llm_temperature = float(voice_llm_temperature.strip())
    except (ValueError, AttributeError):
        sys_conf.voice_llm_temperature = 0.7

    if voice_elevenlabs_model.strip():
        sys_conf.voice_elevenlabs_model = voice_elevenlabs_model.strip()

    # Voice ID — prefer custom field if "Custom Voice ID..." was selected
    _vid = voice_elevenlabs_voice_id_custom.strip() if voice_elevenlabs_voice_id.strip() == "custom" else voice_elevenlabs_voice_id.strip()
    if _vid:
        sys_conf.voice_elevenlabs_voice_id = _vid

    # Parse ElevenLabs voice settings safely
    try:
        if voice_elevenlabs_stability.strip():
            sys_conf.voice_elevenlabs_stability = float(voice_elevenlabs_stability.strip())
    except (ValueError, AttributeError):
        sys_conf.voice_elevenlabs_stability = 0.5

    try:
        if voice_elevenlabs_similarity.strip():
            sys_conf.voice_elevenlabs_similarity = float(voice_elevenlabs_similarity.strip())
    except (ValueError, AttributeError):
        sys_conf.voice_elevenlabs_similarity = 0.75

    schema_drift = bool(_voice_ai_critical_schema_missing(db))
    save_system_config(db, sys_conf)

    if schema_drift:
        return RedirectResponse(url="/admin/voice-ai?msg=compat_saved", status_code=303)
    return RedirectResponse(url="/admin/voice-ai?msg=saved", status_code=303)


@app.get("/admin/host-profitability", response_class=HTMLResponse)
def admin_host_profitability(request: Request, db: Session = Depends(get_db)):
    """Per-host profitability breakdown — see revenue, costs, and profit for each tenant."""
    admin = _require_admin(request, db)

    from sqlalchemy.sql import func
    from datetime import timedelta

    # Get all tenants with their subscription info
    configs = db.query(TenantConfig, Tenant).join(Tenant).all()

    # Get API costs per tenant (last 30 days)
    cost_dict = {}
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(days=30)
        costs_30d = db.query(APIUsageLog.tenant_id, func.sum(APIUsageLog.cost_usd).label("total_cost"))\
            .filter(APIUsageLog.created_at >= cutoff)\
            .group_by(APIUsageLog.tenant_id).all()
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
        is_paying = cfg.subscription_status in ("active", "trialing")

        # Revenue only counts for active/trialing subscriptions
        if is_paying:
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
            "sub_status": cfg.subscription_status or "free",
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
        "admin": admin,
        "hosts": hosts,
        "total_revenue": total_revenue,
        "total_cost": total_cost,
        "total_profit": total_profit,
        "total_margin_pct": total_margin_pct,
    })


@app.get("/admin/costs", response_class=HTMLResponse)
def admin_costs_dashboard(request: Request, db: Session = Depends(get_db)):
    """Phase 2: Internal Profitability Analysis."""
    admin = _require_admin(request, db)

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
    from datetime import timedelta
    cost_dict = {}
    try:
        start_date = datetime.now(timezone.utc) - timedelta(days=30)
        costs = (
            db.query(APIUsageLog.tenant_id, func.sum(APIUsageLog.cost_usd).label("total_cost"))
            .filter(APIUsageLog.created_at >= start_date)
            .group_by(APIUsageLog.tenant_id)
            .all()
        )
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

    # Cost forecasting for this month
    now = datetime.now(timezone.utc)
    start_of_month = now.replace(day=1)
    days_in_month = 30 if now.month != 2 else (29 if now.year % 4 == 0 else 28)
    days_passed = (now - start_of_month).days + 1
    days_remaining = days_in_month - days_passed

    # Query last 3 months of costs
    try:
        costs_by_date = {}
        for i in range(90):
            date = (now - timedelta(days=i)).date()
            daily_cost = (
                db.query(func.sum(APIUsageLog.cost_usd))
                .filter(
                    func.date(APIUsageLog.created_at) == date,
                )
                .scalar() or 0
            )
            costs_by_date[date] = float(daily_cost)

        # Simple linear regression: forecast based on average daily cost
        recent_daily_avg = sum(list(costs_by_date.values())[:7]) / 7 if len(costs_by_date) >= 7 else (total_cost / max(days_passed, 1))
        forecast_this_month = (total_cost / max(days_passed, 1)) * days_in_month
        forecast_next_month = recent_daily_avg * 30

        # Also compute month-over-month costs
        costs_last_month = (
            db.query(func.sum(APIUsageLog.cost_usd))
            .filter(
                APIUsageLog.created_at >= start_of_month - timedelta(days=30),
                APIUsageLog.created_at < start_of_month
            )
            .scalar() or 0
        )
        costs_two_months_ago = (
            db.query(func.sum(APIUsageLog.cost_usd))
            .filter(
                APIUsageLog.created_at >= start_of_month - timedelta(days=60),
                APIUsageLog.created_at < start_of_month - timedelta(days=30)
            )
            .scalar() or 0
        )

        cost_history = [
            {"month": "2 months ago", "cost": round(float(costs_two_months_ago), 2)},
            {"month": "Last month", "cost": round(float(costs_last_month), 2)},
            {"month": "This month (so far)", "cost": round(total_cost, 2)},
            {"month": "Forecast (end of month)", "cost": round(forecast_this_month, 2)},
        ]
    except Exception:
        forecast_this_month = 0
        forecast_next_month = 0
        cost_history = []

    return templates.TemplateResponse("admin_costs.html", {
        "request": request,
        "admin": admin,
        "metrics": metrics,
        "total_rev": total_rev,
        "total_cost": total_cost,
        "total_margin": total_margin,
        "margin_pct": margin_pct,
        "forecast_this_month": round(forecast_this_month, 2),
        "forecast_next_month": round(forecast_next_month, 2),
        "cost_history": cost_history,
    })


@app.get("/admin/health_api", response_class=HTMLResponse)
def admin_api_health(request: Request, db: Session = Depends(get_db)):
    """Phase 4: API Health & Performance Monitoring."""
    admin = _require_admin(request, db)

    # Calculate average cost per request and actual monthly cost
    avg_cost = 0.0
    predicted_monthly = 0.0

    try:
        from datetime import timedelta
        now_utc = datetime.now(timezone.utc)
        thirty_days_ago = now_utc - timedelta(days=30)

        # Count total API calls for average
        total_count = db.query(APIUsageLog).count()
        # Calculate actual cost from last 30 days
        monthly_cost = db.query(func.sum(APIUsageLog.cost_usd)).filter(
            APIUsageLog.created_at >= thirty_days_ago
        ).scalar() or 0.0
        # For rows with zero cost but known tokens, compute on-the-fly
        if monthly_cost == 0.0 and total_count > 0:
            from web.classifier import _calc_model_cost as _cmc2
            token_rows = db.query(APIUsageLog).filter(
                APIUsageLog.cost_usd == 0.0,
                APIUsageLog.input_tokens.isnot(None),
                APIUsageLog.input_tokens > 0,
                APIUsageLog.created_at >= thirty_days_ago,
            ).all()
            for row in token_rows:
                model = row.operation.split(":", 1)[1] if ":" in (row.operation or "") else "openai/gpt-4o-mini"
                row.cost_usd = _cmc2(model, row.input_tokens or 0, row.output_tokens or 0)
            try:
                db.commit()
                monthly_cost = db.query(func.sum(APIUsageLog.cost_usd)).filter(
                    APIUsageLog.created_at >= thirty_days_ago
                ).scalar() or 0.0
            except Exception:
                db.rollback()
        # Calculate average cost per call (from all history)
        total_cost = db.query(func.sum(APIUsageLog.cost_usd)).scalar() or 0.0
        avg_cost = (total_cost / total_count) if total_count > 0 else 0.0
        predicted_monthly = monthly_cost  # actual 30-day cost, not projection
    except Exception:
        # api_usage_logs table may not exist yet if migrations haven't fully run
        pass

    return templates.TemplateResponse("admin_api.html", {
        "request": request,
        "admin": admin,
        "avg_cost": avg_cost,
        "predicted_monthly": predicted_monthly,
    })


@app.get("/admin/api-status")
async def admin_api_status(request: Request, db: Session = Depends(get_db)):
    """Live health check for every external API the app uses."""
    _require_admin(request, db)
    sys_conf = load_system_config(db) or SystemConfig()
    import time as _time
    import urllib.request as _urllib_req
    import urllib.error as _urllib_err
    import httpx as _httpx

    results = {}

    def _no_key(name: str) -> dict:
        return {"status": "unconfigured", "msg": "No API key configured", "ms": None}

    def _ok(msg: str, ms: float) -> dict:
        return {"status": "ok", "msg": msg, "ms": round(ms)}

    def _err(msg: str, ms: float | None = None) -> dict:
        return {"status": "error", "msg": msg, "ms": round(ms) if ms is not None else None}

    # ── OpenRouter ────────────────────────────────────────────────────────────
    try:
        if sys_conf.openrouter_api_key_enc:
            key = decrypt(sys_conf.openrouter_api_key_enc)
            if not key:
                results["openrouter"] = _err("Decryption failed")
            else:
                t0 = _time.monotonic()
                req = _urllib_req.Request(
                    "https://openrouter.ai/api/v1/models",
                    headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                )
                try:
                    _urllib_req.urlopen(req, timeout=6)
                    results["openrouter"] = _ok("Connected", (_time.monotonic() - t0) * 1000)
                except _urllib_err.HTTPError as e:
                    ms = (_time.monotonic() - t0) * 1000
                    results["openrouter"] = _err(f"HTTP {e.code} — Invalid API key" if e.code in (401, 403) else f"HTTP {e.code}", ms)
        else:
            results["openrouter"] = _no_key("openrouter")
    except Exception as e:
        results["openrouter"] = _err(str(e)[:80])

    # ── Deepgram ──────────────────────────────────────────────────────────────
    try:
        if sys_conf.deepgram_api_key_enc:
            key = decrypt(sys_conf.deepgram_api_key_enc)
            if not key:
                results["deepgram"] = _err("Decryption failed")
            else:
                t0 = _time.monotonic()
                req = _urllib_req.Request(
                    "https://api.deepgram.com/v1/models",
                    headers={"Authorization": f"Token {key}", "Content-Type": "application/json"},
                )
                try:
                    _urllib_req.urlopen(req, timeout=6)
                    results["deepgram"] = _ok("Connected", (_time.monotonic() - t0) * 1000)
                except _urllib_err.HTTPError as e:
                    ms = (_time.monotonic() - t0) * 1000
                    results["deepgram"] = _err(f"Invalid API key" if e.code == 401 else f"HTTP {e.code}", ms)
        else:
            results["deepgram"] = _no_key("deepgram")
    except Exception as e:
        results["deepgram"] = _err(str(e)[:80])

    # ── Google TTS ────────────────────────────────────────────────────────────
    try:
        if sys_conf.google_tts_api_key_enc:
            key = decrypt(sys_conf.google_tts_api_key_enc)
            if not key:
                results["google_tts"] = _err("Decryption failed")
            else:
                lang = getattr(sys_conf, "voice_google_tts_language", None) or "en-US"
                voice_name = getattr(sys_conf, "voice_google_tts_voice", None) or "en-US-Neural2-F"
                t0 = _time.monotonic()
                async with _httpx.AsyncClient(timeout=8) as client:
                    resp = await client.post(
                        f"https://texttospeech.googleapis.com/v1/text:synthesize?key={key}",
                        json={"input": {"text": "OK"}, "voice": {"languageCode": lang, "name": voice_name}, "audioConfig": {"audioEncoding": "MP3"}},
                    )
                ms = (_time.monotonic() - t0) * 1000
                if resp.status_code == 200:
                    results["google_tts"] = _ok("Connected — synthesis OK", ms)
                elif resp.status_code in (401, 403):
                    results["google_tts"] = _err("Invalid or unauthorized API key", ms)
                elif resp.status_code == 400:
                    results["google_tts"] = _err(f"Bad request (check voice/language config): {resp.text[:60]}", ms)
                else:
                    results["google_tts"] = _err(f"HTTP {resp.status_code}", ms)
        else:
            results["google_tts"] = _no_key("google_tts")
    except Exception as e:
        results["google_tts"] = _err(str(e)[:80])

    # ── ElevenLabs ────────────────────────────────────────────────────────────
    try:
        if sys_conf.elevenlabs_api_key_enc:
            key = decrypt(sys_conf.elevenlabs_api_key_enc)
            if not key:
                results["elevenlabs"] = _err("Decryption failed")
            else:
                t0 = _time.monotonic()
                async with _httpx.AsyncClient(timeout=8) as client:
                    resp = await client.get("https://api.elevenlabs.io/v1/user", headers={"xi-api-key": key})
                ms = (_time.monotonic() - t0) * 1000
                if resp.status_code == 200:
                    results["elevenlabs"] = _ok("Connected", ms)
                elif resp.status_code in (401, 403):
                    results["elevenlabs"] = _err("Invalid API key", ms)
                else:
                    results["elevenlabs"] = _err(f"HTTP {resp.status_code}", ms)
        else:
            results["elevenlabs"] = _no_key("elevenlabs")
    except Exception as e:
        results["elevenlabs"] = _err(str(e)[:80])

    # ── Twilio (SMS) — credentials live on TenantConfig, pick first configured ──
    def _twilio_check(sid: str | None, tok_enc: str | None) -> dict:
        if not sid or not tok_enc:
            return _no_key("twilio")
        tok = decrypt(tok_enc)
        if not tok:
            return _err("Decryption failed")
        import base64 as _b64
        creds = _b64.b64encode(f"{sid}:{tok}".encode()).decode()
        t0 = _time.monotonic()
        req = _urllib_req.Request(
            f"https://api.twilio.com/2010-04-01/Accounts/{sid}.json",
            headers={"Authorization": f"Basic {creds}"},
        )
        try:
            _urllib_req.urlopen(req, timeout=6)
            return _ok("Connected", (_time.monotonic() - t0) * 1000)
        except _urllib_err.HTTPError as e:
            ms = (_time.monotonic() - t0) * 1000
            return _err("Invalid credentials" if e.code in (401, 403) else f"HTTP {e.code}", ms)

    try:
        # twilio_account_sid is on TenantConfig, not SystemConfig
        sms_cfg = (
            db.query(TenantConfig)
            .filter(TenantConfig.twilio_account_sid.isnot(None),
                    TenantConfig.twilio_auth_token_enc.isnot(None))
            .first()
        )
        if sms_cfg:
            results["twilio_sms"] = _twilio_check(sms_cfg.twilio_account_sid, sms_cfg.twilio_auth_token_enc)
        else:
            results["twilio_sms"] = _no_key("twilio_sms")
    except Exception as e:
        results["twilio_sms"] = _err(str(e)[:80])

    # ── Twilio (Voice) — voice creds on TenantConfig too ─────────────────────
    try:
        voice_cfg = (
            db.query(TenantConfig)
            .filter(TenantConfig.voice_twilio_account_sid.isnot(None),
                    TenantConfig.voice_twilio_auth_token_enc.isnot(None))
            .first()
        )
        if voice_cfg:
            results["twilio_voice"] = _twilio_check(voice_cfg.voice_twilio_account_sid, voice_cfg.voice_twilio_auth_token_enc)
        else:
            results["twilio_voice"] = _no_key("twilio_voice")
    except Exception as e:
        results["twilio_voice"] = _err(str(e)[:80])

    # ── Google Maps ───────────────────────────────────────────────────────────
    try:
        if sys_conf.google_maps_api_key_enc:
            key = decrypt(sys_conf.google_maps_api_key_enc)
            if not key:
                results["google_maps"] = _err("Decryption failed")
            else:
                t0 = _time.monotonic()
                req = _urllib_req.Request(
                    f"https://maps.googleapis.com/maps/api/geocode/json?address=test&key={key}"
                )
                try:
                    import json as _json
                    with _urllib_req.urlopen(req, timeout=6) as r:
                        body = _json.loads(r.read())
                    ms = (_time.monotonic() - t0) * 1000
                    status = body.get("status", "")
                    if status in ("OK", "ZERO_RESULTS"):
                        results["google_maps"] = _ok("Connected", ms)
                    elif status == "REQUEST_DENIED":
                        results["google_maps"] = _err("API key denied — check billing/restrictions", ms)
                    else:
                        results["google_maps"] = _err(f"API status: {status}", ms)
                except _urllib_err.HTTPError as e:
                    ms = (_time.monotonic() - t0) * 1000
                    results["google_maps"] = _err(f"HTTP {e.code}", ms)
        else:
            results["google_maps"] = _no_key("google_maps")
    except Exception as e:
        results["google_maps"] = _err(str(e)[:80])

    # ── Cloudflare R2 ─────────────────────────────────────────────────────────
    try:
        acct = sys_conf.cloudflare_account_id
        ak_enc = sys_conf.cloudflare_r2_access_key_enc
        sk_enc = sys_conf.cloudflare_r2_secret_key_enc
        bucket = sys_conf.cloudflare_r2_bucket
        if acct and ak_enc and sk_enc and bucket:
            ak = decrypt(ak_enc)
            sk = decrypt(sk_enc)
            if not ak or not sk:
                results["cloudflare_r2"] = _err("Decryption failed")
            else:
                import hmac as _hmac, hashlib as _hashlib, datetime as _dt_mod
                endpoint = f"https://{acct}.r2.cloudflarestorage.com"
                now = _dt_mod.datetime.now(timezone.utc)
                date_str = now.strftime("%Y%m%d")
                amz_date = now.strftime("%Y%m%dT%H%M%SZ")
                host = f"{acct}.r2.cloudflarestorage.com"
                canonical = f"GET\n/{bucket}\n\nhost:{host}\nx-amz-date:{amz_date}\n\nhost;x-amz-date\ne3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
                string_to_sign = f"AWS4-HMAC-SHA256\n{amz_date}\n{date_str}/auto/s3/aws4_request\n{_hashlib.sha256(canonical.encode()).hexdigest()}"
                def _sign(k, msg):
                    return _hmac.new(k, msg.encode(), _hashlib.sha256).digest()
                signing_key = _sign(_sign(_sign(_sign(f"AWS4{sk}".encode(), date_str), "auto"), "s3"), "aws4_request")
                sig = _hmac.new(signing_key, string_to_sign.encode(), _hashlib.sha256).hexdigest()
                auth = f"AWS4-HMAC-SHA256 Credential={ak}/{date_str}/auto/s3/aws4_request,SignedHeaders=host;x-amz-date,Signature={sig}"
                t0 = _time.monotonic()
                req = _urllib_req.Request(
                    f"{endpoint}/{bucket}",
                    headers={"Host": host, "x-amz-date": amz_date, "Authorization": auth},
                )
                try:
                    _urllib_req.urlopen(req, timeout=6)
                    results["cloudflare_r2"] = _ok("Connected", (_time.monotonic() - t0) * 1000)
                except _urllib_err.HTTPError as e:
                    ms = (_time.monotonic() - t0) * 1000
                    if e.code in (403, 401):
                        results["cloudflare_r2"] = _err("Invalid credentials", ms)
                    elif e.code == 200:
                        results["cloudflare_r2"] = _ok("Connected", ms)
                    else:
                        # 404 / 405 still means credentials are valid
                        results["cloudflare_r2"] = _ok(f"Connected (HTTP {e.code})", (_time.monotonic() - t0) * 1000)
        else:
            results["cloudflare_r2"] = _no_key("cloudflare_r2")
    except Exception as e:
        results["cloudflare_r2"] = _err(str(e)[:80])

    results["timestamp"] = datetime.now(timezone.utc).isoformat()
    return JSONResponse(results)


@app.get("/admin/api-usage-stats")
def admin_api_usage_stats(request: Request, db: Session = Depends(get_db)):
    """Return 30-day usage & cost stats per API service from api_usage_logs."""
    _require_admin(request, db)
    from datetime import timedelta
    from sqlalchemy import func as _func, case as _case

    cutoff = datetime.now(timezone.utc) - timedelta(days=30)

    try:
        rows = (
            db.query(
                APIUsageLog.service,
                _func.count(APIUsageLog.id).label("calls"),
                _func.sum(APIUsageLog.cost_usd).label("cost"),
                _func.sum(APIUsageLog.input_tokens).label("in_tokens"),
                _func.sum(APIUsageLog.output_tokens).label("out_tokens"),
                _func.sum(APIUsageLog.duration_seconds).label("duration_s"),
                _func.sum(APIUsageLog.characters).label("characters"),
            )
            .filter(APIUsageLog.created_at >= cutoff)
            .group_by(APIUsageLog.service)
            .all()
        )
    except Exception:
        rows = []

    # ── Backfill cost_usd for rows logged before the cost fix ────────────────
    try:
        from web.classifier import _calc_model_cost as _cmc
        zero_cost_rows = (
            db.query(APIUsageLog)
            .filter(
                APIUsageLog.cost_usd == 0.0,
                APIUsageLog.service == "openrouter",
                APIUsageLog.input_tokens.isnot(None),
                APIUsageLog.input_tokens > 0,
            )
            .limit(500)
            .all()
        )
        if zero_cost_rows:
            for row in zero_cost_rows:
                # Extract model from operation e.g. "generate_draft:openai/gpt-4o-mini"
                model = row.operation.split(":", 1)[1] if ":" in (row.operation or "") else "openai/gpt-4o-mini"
                row.cost_usd = _cmc(model, row.input_tokens or 0, row.output_tokens or 0)
            db.commit()
    except Exception:
        db.rollback()

    stats: dict = {}
    for r in rows:
        stats[r.service] = {
            "calls":      int(r.calls or 0),
            "cost":       round(float(r.cost or 0), 6),
            "in_tokens":  int(r.in_tokens or 0),
            "out_tokens": int(r.out_tokens or 0),
            "duration_s": round(float(r.duration_s or 0), 1),
            "characters": int(r.characters or 0),
        }

    # Re-query cost after backfill so totals reflect updated rows
    try:
        updated_rows = (
            db.query(
                APIUsageLog.service,
                _func.sum(APIUsageLog.cost_usd).label("cost"),
            )
            .filter(APIUsageLog.created_at >= cutoff)
            .group_by(APIUsageLog.service)
            .all()
        )
        for r in updated_rows:
            if r.service in stats:
                stats[r.service]["cost"] = round(float(r.cost or 0), 8)
    except Exception:
        pass

    # Also pull total across all services
    total_cost = sum(v["cost"] for v in stats.values())
    total_calls = sum(v["calls"] for v in stats.values())

    # Last 7 daily totals (for sparkline feel)
    daily: list = []
    try:
        for day_offset in range(6, -1, -1):
            day_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=day_offset)
            day_end   = day_start + timedelta(days=1)
            day_cost  = db.query(_func.sum(APIUsageLog.cost_usd)).filter(
                APIUsageLog.created_at >= day_start,
                APIUsageLog.created_at < day_end,
            ).scalar() or 0.0
            day_calls = db.query(_func.count(APIUsageLog.id)).filter(
                APIUsageLog.created_at >= day_start,
                APIUsageLog.created_at < day_end,
            ).scalar() or 0
            daily.append({"date": day_start.strftime("%m/%d"), "cost": round(float(day_cost), 6), "calls": int(day_calls)})
    except Exception:
        daily = []

    return JSONResponse({
        "by_service":   stats,
        "total_cost":   round(total_cost, 6),
        "total_calls":  total_calls,
        "daily":        daily,
        "period_days":  30,
        "timestamp":    datetime.now(timezone.utc).isoformat(),
    })


@app.post("/admin/ai/save", response_class=HTMLResponse)
def admin_ai_save(
    request: Request,
    openrouter_api_key_enc: str = Form(""),
    google_maps_api_key_enc: str = Form(""),
    primary_model: str = Form(...),
    routine_model: str = Form("google/gemini-2.5-flash"),
    fallback_model: str = Form(...),
    sentiment_model: str = Form("openai/gpt-4o-mini"),
    # Voice AI settings
    voice_llm_model: str = Form("openai/gpt-4o-mini"),
    voice_llm_backup_model: str = Form("anthropic/claude-3.5-haiku"),
    voice_llm_emergency_model: str = Form("meta-llama/llama-3.3-70b-instruct"),
    voice_deepgram_model: str = Form("nova-2"),
    voice_llm_max_tokens: int = Form(300),
    voice_llm_temperature: float = Form(0.7),
    voice_elevenlabs_model: str = Form("eleven_turbo_v2"),
    voice_elevenlabs_stability: float = Form(0.5),
    voice_elevenlabs_similarity: float = Form(0.75),
    csrf_token: str = Form(None),
    db: Session = Depends(get_db)
):
    admin = _require_admin(request, db)
    validate_csrf(request, csrf_token)  # Admin safeguard
    sys_conf = load_system_config(db, create_if_missing=True) or SystemConfig()
    if system_config_schema_is_behind(sys_conf):
        log.error("Cannot save AI config while system_config schema is behind the application model")
        return RedirectResponse("/admin/ai?msg=schema_sync_required", status_code=302)

    if openrouter_api_key_enc.strip() and openrouter_api_key_enc != "********":
        sys_conf.openrouter_api_key_enc = encrypt(openrouter_api_key_enc.strip())
    if google_maps_api_key_enc.strip() and google_maps_api_key_enc != "********":
        sys_conf.google_maps_api_key_enc = encrypt(google_maps_api_key_enc.strip())
    sys_conf.primary_model = primary_model.strip()
    sys_conf.routine_model = routine_model.strip()
    sys_conf.fallback_model = fallback_model.strip()
    sys_conf.sentiment_model = sentiment_model.strip()
    # Save voice AI settings
    sys_conf.voice_llm_model = voice_llm_model.strip()
    sys_conf.voice_llm_backup_model = voice_llm_backup_model.strip()
    sys_conf.voice_llm_emergency_model = voice_llm_emergency_model.strip()
    sys_conf.voice_deepgram_model = voice_deepgram_model.strip()
    sys_conf.voice_llm_max_tokens = voice_llm_max_tokens
    sys_conf.voice_llm_temperature = voice_llm_temperature
    sys_conf.voice_elevenlabs_model = voice_elevenlabs_model.strip()
    sys_conf.voice_elevenlabs_stability = voice_elevenlabs_stability
    sys_conf.voice_elevenlabs_similarity = voice_elevenlabs_similarity
    db.commit()

    # Audit log + admin alert
    db.add(ActivityLog(
        tenant_id=admin.id, event_type="admin_ai_config_changed",
        message=f"AI config updated: primary={primary_model} routine={routine_model} fallback={fallback_model} by {admin.email}"
    ))
    db.commit()

    return RedirectResponse("/admin/ai?msg=saved", status_code=302)


@app.get("/admin/voice-chat", response_class=HTMLResponse)
def admin_voice_chat_page(request: Request, db: Session = Depends(get_db)):
    """Voice AI direct chat testing interface"""
    admin = _require_admin(request, db)
    sys_conf = load_system_config(db) or SystemConfig()
    return templates.TemplateResponse(
        "admin_voice_chat.html",
        {
            "request": request,
            "admin": admin,
            "sys_conf": sys_conf
        }
    )


@app.post("/admin/voice/chat")
async def admin_voice_chat(request: Request, db: Session = Depends(get_db)):
    """Test voice AI response - no actual call routing"""
    admin = _require_admin(request, db)
    sys_conf = load_system_config(db) or SystemConfig()

    data = await request.json()
    user_message = data.get("message", "").strip()
    conversation_history = data.get("conversation_history", [])

    if not user_message:
        return JSONResponse({"error": "Empty message"}, status_code=400)

    try:
        # Import voice integration
        from web.integrations.voice import VoiceAIService

        # Get tenant config (mock for testing)
        tenant_config = {
            "property_type": "apartment",
            "property_city": "Test City",
            "check_in_time": "15:00",
            "check_out_time": "11:00",
            "amenities": "WiFi, Pool, Gym",
            "house_rules": "No parties after 10pm",
            "parking_policy": "Parking included",
            "max_guests": "4",
            "faq": "Common questions answered",
            "nearby_restaurants": "Many great options nearby",
        }

        # Build conversation history for LLM
        llm_history = [{"role": h["role"], "text": h["text"]} for h in conversation_history[-6:]]

        # Generate response using voice AI
        response, send_action, unanswered = await VoiceAIService.generate_response(
            guest_message=user_message,
            tenant_config=tenant_config,
            conversation_history=llm_history,
            guest_name="Test Guest",
            guest_language="en"
        )

        # Parse response if it's JSON
        if isinstance(response, str) and response.startswith("{"):
            try:
                import json
                parsed = json.loads(response)
                response_text = parsed.get("voice", response)
            except:
                response_text = response
        else:
            response_text = response

        return JSONResponse({
            "response": response_text,
            "model_used": sys_conf.voice_llm_model,
            "send_action": send_action,
            "unanswered": unanswered
        })

    except Exception as e:
        import traceback
        log.error(f"Voice chat error: {str(e)}\n{traceback.format_exc()}")
        return JSONResponse({
            "error": f"Failed to generate response: {str(e)[:100]}",
            "detail": str(e)
        }, status_code=500)


# ---------------------------------------------------------------------------
# Voice AI — Host Interface (Direct Chat Testing)
# ---------------------------------------------------------------------------

def _require_auth(request: Request, db: Session) -> "Tenant":
    """Fetch and return the authenticated tenant, raising 401/redirect on failure."""
    tenant_id = get_current_tenant_id(request)  # raises HTTPException(401) if not logged in
    return _get_tenant(tenant_id, db)

@app.get("/voice-ai-chat", response_class=HTMLResponse)
def host_voice_chat_page(request: Request, db: Session = Depends(get_db)):
    """Host-facing voice AI chat interface for direct testing without phone calls"""
    tenant = _require_auth(request, db)
    tenant_config = load_tenant_config(db, tenant.id)
    sys_conf = load_system_config(db) or SystemConfig()

    return templates.TemplateResponse(
        "voice_ai_chat.html",
        {
            "request": request,
            "tenant": tenant,
            "tenant_config": tenant_config,
            "sys_conf": sys_conf
        }
    )


@app.post("/api/voice-ai/test-message")
async def host_voice_chat_message(request: Request, db: Session = Depends(get_db)):
    """Test voice AI response for host (uses tenant config settings)"""
    try:
        tenant = _require_auth(request, db)
        data = await request.json()
        user_message = data.get("message", "").strip()
        conversation_history = data.get("conversation_history", [])

        if not user_message:
            return JSONResponse({"error": "Empty message"}, status_code=400)

        # Get tenant config
        tenant_config_obj = load_tenant_config(db, tenant.id)
        sys_conf = load_system_config(db) or SystemConfig()

        tenant_config = {
            "property_type": tenant_config_obj.property_type if tenant_config_obj else "apartment",
            "property_city": tenant_config_obj.property_city if tenant_config_obj else "",
            "check_in_time": tenant_config_obj.check_in_time if tenant_config_obj else "15:00",
            "check_out_time": tenant_config_obj.check_out_time if tenant_config_obj else "11:00",
            "amenities": tenant_config_obj.amenities if tenant_config_obj else "",
            "house_rules": tenant_config_obj.house_rules if tenant_config_obj else "",
            "parking_policy": tenant_config_obj.parking_policy if tenant_config_obj else "",
            "max_guests": str(tenant_config_obj.max_guests) if tenant_config_obj and tenant_config_obj.max_guests else "4",
            "faq": tenant_config_obj.faq if tenant_config_obj else "",
            "nearby_restaurants": tenant_config_obj.nearby_restaurants if tenant_config_obj else "",
        }

        # Use tenant's voice AI settings if available, else fall back to system config
        llm_model = tenant_config_obj.voice_llm_model if tenant_config_obj and tenant_config_obj.voice_llm_model else (sys_conf.voice_llm_model or "openai/gpt-4o-mini")

        llm_history = [{"role": h["role"], "text": h["text"]} for h in conversation_history[-6:]]

        response, send_action, unanswered = await VoiceAIService.generate_response(
            guest_message=user_message,
            tenant_config=tenant_config,
            conversation_history=llm_history,
            guest_name="Test Guest",
            guest_language="en"
        )

        response_text = response
        if isinstance(response, str) and response.startswith("{"):
            try:
                import json as json_lib
                parsed = json_lib.loads(response)
                response_text = parsed.get("response", response)
            except:
                response_text = response

        return JSONResponse({
            "response": response_text,
            "model_used": llm_model,
            "send_action": send_action,
            "unanswered": unanswered
        })

    except Exception as e:
        import traceback
        log.error(f"Host voice chat error: {str(e)}\n{traceback.format_exc()}")
        return JSONResponse({
            "error": f"Failed to generate response: {str(e)[:100]}"
        }, status_code=500)


@app.post("/api/voice-ai/voice-message")
async def voice_ai_voice_message(request: Request, audio: UploadFile = File(...), db: Session = Depends(get_db)):
    """Handle audio voice message: transcribe → generate response → synthesize audio"""
    try:
        tenant = _require_auth(request, db)
        from web.integrations.voice import VoiceAIService

        # Read audio file
        audio_bytes = await audio.read()

        # Step 1: Upload audio to S3/R2 to get URL for transcription
        audio_url = await VoiceAIService.upload_to_r2(audio_bytes, f"voice-chat-{uuid4()}.wav")
        if not audio_url:
            audio_url = await VoiceAIService.upload_to_s3(audio_bytes, f"voice-chat-{uuid4()}.wav")

        transcript, confidence = await VoiceAIService.transcribe_audio(audio_url)

        if not transcript:
            return JSONResponse({"error": "Could not transcribe audio"}, status_code=400)

        # Step 2: Generate LLM response
        tenant_config_obj = load_tenant_config(db, tenant.id)
        sys_conf = load_system_config(db) or SystemConfig()

        tenant_config = {
            "property_type": tenant_config_obj.property_type if tenant_config_obj else "apartment",
            "property_city": tenant_config_obj.property_city if tenant_config_obj else "",
            "check_in_time": tenant_config_obj.check_in_time if tenant_config_obj else "15:00",
            "check_out_time": tenant_config_obj.check_out_time if tenant_config_obj else "11:00",
            "amenities": tenant_config_obj.amenities if tenant_config_obj else "",
            "house_rules": tenant_config_obj.house_rules if tenant_config_obj else "",
            "parking_policy": tenant_config_obj.parking_policy if tenant_config_obj else "",
            "max_guests": str(tenant_config_obj.max_guests) if tenant_config_obj and tenant_config_obj.max_guests else "4",
            "faq": tenant_config_obj.faq if tenant_config_obj else "",
            "nearby_restaurants": tenant_config_obj.nearby_restaurants if tenant_config_obj else "",
        }

        response, send_action, unanswered = await VoiceAIService.generate_response(
            guest_message=transcript,
            tenant_config=tenant_config,
            conversation_history=[],
            guest_name="Voice Guest",
            guest_language="en"
        )

        response_text = response
        if isinstance(response, str) and response.startswith("{"):
            try:
                import json as json_lib
                parsed = json_lib.loads(response)
                response_text = parsed.get("response", response)
            except:
                response_text = response

        # Step 3: Synthesize response to audio
        voice_id = tenant_config_obj.voice_elevenlabs_voice_id if tenant_config_obj else None
        audio_bytes_response, s3_url = await VoiceAIService.synthesize_speech(response_text, voice_id=voice_id)
        _log_tts_usage(db, tenant.id, response_text, audio_bytes_response)

        return JSONResponse({
            "transcript": transcript,
            "response": response_text,
            "audio_url": s3_url,
            "confidence": confidence
        })

    except Exception as e:
        import traceback
        log.error(f"Voice message error: {str(e)}\n{traceback.format_exc()}")
        return JSONResponse({
            "error": f"Failed to process voice message: {str(e)[:100]}"
        }, status_code=500)


@app.post("/api/voice/save-voice")
async def api_save_voice(request: Request, db: Session = Depends(get_db)):
    """Save per-tenant Google TTS voice selection (called from inline voice picker)."""
    try:
        tenant = _require_auth(request, db)
        validate_csrf_header(request)
        data = await request.json()
        voice = (data.get("voice") or "").strip()
        if not voice:
            return JSONResponse({"error": "No voice specified"}, status_code=400)
        cfg = _get_or_create_config(tenant.id, db)
        # Use raw SQL update as fallback in case ORM column mapping is behind migrations
        try:
            cfg.voice_google_tts_voice = voice
            db.commit()
        except Exception:
            db.rollback()
            db.execute(
                text("UPDATE tenant_configs SET voice_google_tts_voice = :v WHERE tenant_id = :tid"),
                {"v": voice, "tid": tenant.id}
            )
            db.commit()
        return JSONResponse({"ok": True, "voice": voice})
    except HTTPException:
        raise
    except Exception as exc:
        log.error(f"save-voice error: {exc}")
        return JSONResponse({"error": str(exc)[:120]}, status_code=500)


@app.post("/api/voice-ai/synthesize")
async def voice_ai_synthesize(request: Request, db: Session = Depends(get_db)):
    """Synthesize text to speech (text-to-speech)"""
    try:
        tenant = _require_auth(request, db)
        data = await request.json()
        text = data.get("text", "").strip()

        if not text:
            return JSONResponse({"error": "Empty text"}, status_code=400)

        from web.integrations.voice import VoiceAIService

        tenant_config_obj = load_tenant_config(db, tenant.id)
        voice_id = tenant_config_obj.voice_elevenlabs_voice_id if tenant_config_obj else None

        audio_bytes, s3_url = await VoiceAIService.synthesize_speech(text, voice_id=voice_id)
        _log_tts_usage(db, tenant.id, text, audio_bytes)

        return JSONResponse({
            "audio_url": s3_url
        })

    except Exception as e:
        import traceback
        log.error(f"Voice synthesize error: {str(e)}\n{traceback.format_exc()}")
        return JSONResponse({
            "error": f"Failed to synthesize audio: {str(e)[:100]}"
        }, status_code=500)


@app.websocket("/ws/voice-ai/live")
async def websocket_voice_ai_live(websocket: WebSocket, db: Session = Depends(get_db)):
    """Real-time two-way audio streaming for Voice AI conversation via Deepgram WebSocket"""
    await websocket.accept()
    log.info("Voice AI WebSocket connection accepted")

    dg_connection = None
    process_task = None
    listener_task = None

    try:
        from web.integrations.voice import VoiceAIService

        # Receive initial config message
        try:
            init_msg = await websocket.receive_text()
            init_data = json.loads(init_msg)
            log.info(f"Voice AI init message: {init_data}")
        except Exception as e:
            log.error(f"Failed to receive init message: {str(e)}")
            await websocket.send_json({"type": "error", "message": "Failed to receive init message"})
            await websocket.close()
            return

        # Resolve the current authenticated tenant first. Falling back to the
        # first tenant in the database makes the admin test harness use the
        # wrong voice settings and can silently point TTS at an invalid voice.
        tenant = None
        try:
            tenant_id = get_current_tenant_id(websocket)  # type: ignore[arg-type]
            tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
        except Exception:
            pass
        if not tenant:
            try:
                tenant_id = init_data.get("tenant_id")
                if tenant_id:
                    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
            except Exception:
                pass
        if not tenant:
            await websocket.send_json({"type": "error", "message": "No authenticated tenant found"})
            await websocket.close()
            return

        tenant_config_obj = load_tenant_config(db, tenant.id)
        sys_conf = load_system_config(db) or SystemConfig()

        # Load API keys
        if sys_conf.deepgram_api_key_enc:
            dec = decrypt(sys_conf.deepgram_api_key_enc)
            if dec:
                VoiceAIService.DEEPGRAM_API_KEY = dec
            else:
                log.error("Failed to decrypt Deepgram API key")
        if sys_conf.voice_deepgram_model:
            VoiceAIService.DEEPGRAM_MODEL = sys_conf.voice_deepgram_model

        if sys_conf.openrouter_api_key_enc:
            dec = decrypt(sys_conf.openrouter_api_key_enc)
            if dec:
                VoiceAIService.OPENROUTER_API_KEY = dec
            else:
                log.error("Failed to decrypt OpenRouter API key")
        if sys_conf.voice_llm_model:
            VoiceAIService.LLM_MODEL = sys_conf.voice_llm_model

        if sys_conf.elevenlabs_api_key_enc:
            dec = decrypt(sys_conf.elevenlabs_api_key_enc)
            if dec:
                VoiceAIService.ELEVENLABS_API_KEY = dec
            else:
                log.error("Failed to decrypt ElevenLabs API key")
        if sys_conf.voice_elevenlabs_model:
            VoiceAIService.ELEVENLABS_MODEL = sys_conf.voice_elevenlabs_model
        if sys_conf.voice_elevenlabs_stability is not None:
            VoiceAIService.ELEVENLABS_STABILITY = float(sys_conf.voice_elevenlabs_stability)
        if sys_conf.voice_elevenlabs_similarity is not None:
            VoiceAIService.ELEVENLABS_SIMILARITY = float(sys_conf.voice_elevenlabs_similarity)

        # Google Cloud TTS
        if getattr(sys_conf, "google_tts_api_key_enc", None):
            dec = decrypt(sys_conf.google_tts_api_key_enc)
            if dec:
                VoiceAIService.GOOGLE_TTS_API_KEY = dec
            else:
                log.error("Failed to decrypt Google TTS API key")
        if getattr(sys_conf, "voice_tts_provider", None):
            VoiceAIService.TTS_PROVIDER = sys_conf.voice_tts_provider
        if getattr(sys_conf, "voice_google_tts_voice", None):
            VoiceAIService.GOOGLE_TTS_VOICE = sys_conf.voice_google_tts_voice
        if getattr(sys_conf, "voice_google_tts_language", None):
            VoiceAIService.GOOGLE_TTS_LANGUAGE = sys_conf.voice_google_tts_language
        if getattr(sys_conf, "voice_google_tts_speaking_rate", None) is not None:
            VoiceAIService.GOOGLE_TTS_SPEAKING_RATE = float(sys_conf.voice_google_tts_speaking_rate)

        if not VoiceAIService.DEEPGRAM_API_KEY:
            await websocket.send_json({"type": "error", "message": "Deepgram API key not configured"})
            await websocket.close()
            return
        if not VoiceAIService.OPENROUTER_API_KEY and not VoiceAIService.OPENAI_API_KEY:
            await websocket.send_json({"type": "error", "message": "OpenRouter or OpenAI API key not configured"})
            await websocket.close()
            return
        # Require at least one TTS provider
        tts_provider = VoiceAIService.TTS_PROVIDER or "google"
        if tts_provider == "google" and not VoiceAIService.GOOGLE_TTS_API_KEY:
            await websocket.send_json({"type": "error", "message": "Google TTS API key not configured"})
            await websocket.close()
            return
        if tts_provider == "elevenlabs" and not VoiceAIService.ELEVENLABS_API_KEY:
            await websocket.send_json({"type": "error", "message": "ElevenLabs API key not configured"})
            await websocket.close()
            return

        tenant_config = {
            "property_type": tenant_config_obj.property_type if tenant_config_obj else "apartment",
            "property_city": tenant_config_obj.property_city if tenant_config_obj else "",
            "check_in_time": tenant_config_obj.check_in_time if tenant_config_obj else "15:00",
            "check_out_time": tenant_config_obj.check_out_time if tenant_config_obj else "11:00",
            "amenities": tenant_config_obj.amenities if tenant_config_obj else "",
            "house_rules": tenant_config_obj.house_rules if tenant_config_obj else "",
            "parking_policy": tenant_config_obj.parking_policy if tenant_config_obj else "",
            "max_guests": str(tenant_config_obj.max_guests) if tenant_config_obj and tenant_config_obj.max_guests else "4",
            "faq": tenant_config_obj.faq if tenant_config_obj else "",
            "nearby_restaurants": tenant_config_obj.nearby_restaurants if tenant_config_obj else "",
        }
        # Apply Google TTS voice: init_data['voice'] (inline picker) > tenant DB > system default
        session_voice = (init_data.get("voice") or "").strip()
        tenant_google_voice = session_voice or (getattr(tenant_config_obj, "voice_google_tts_voice", None) if tenant_config_obj else None)
        if tenant_google_voice:
            VoiceAIService.GOOGLE_TTS_VOICE = tenant_google_voice
            # Auto-derive language code from voice name (e.g. "en-GB-Neural2-A" → "en-GB")
            parts = tenant_google_voice.split("-")
            if len(parts) >= 2:
                VoiceAIService.GOOGLE_TTS_LANGUAGE = f"{parts[0]}-{parts[1]}"
        if session_voice:
            log.info(f"Session voice override: {session_voice}")

        # ElevenLabs voice: prefer tenant setting over system default
        voice_id = (
            (tenant_config_obj.voice_elevenlabs_voice_id if tenant_config_obj else None)
            or getattr(sys_conf, "voice_elevenlabs_voice_id", None)
            or "EXAVITQu4vr4xnSDxMaL"  # Rachel (default)
        )

        log.info("Voice AI API keys loaded — opening Deepgram streaming connection")

        # Queue to pass final transcripts from Deepgram listener → processing coroutine
        transcript_queue: asyncio.Queue = asyncio.Queue()
        is_muted = False

        # Inactivity timeout: close connection if no audio received for 3 minutes
        INACTIVITY_TIMEOUT_SECONDS = 180  # 3 minutes
        last_activity_time = time.time()

        # Build Deepgram streaming WebSocket URL
        dg_model = VoiceAIService.DEEPGRAM_MODEL or "nova-2"
        dg_url = (
            f"wss://api.deepgram.com/v1/listen"
            f"?model={dg_model}"
            f"&encoding=linear16"
            f"&sample_rate=16000"
            f"&channels=1"
            f"&punctuate=true"
            f"&interim_results=true"
            f"&utterance_end_ms=1200"
            f"&vad_events=true"
        )

        import websockets as ws_lib

        try:
            dg_connection = await ws_lib.connect(
                dg_url,
                additional_headers={"Authorization": f"Token {VoiceAIService.DEEPGRAM_API_KEY}"},
            )
        except Exception as e:
            import traceback
            log.error(f"Deepgram WS connect error: {e}\n{traceback.format_exc()}")
            await websocket.send_json({"type": "error", "message": f"Deepgram connect error: {str(e)[:80]}"})
            await websocket.close()
            return

        await websocket.send_json({"type": "ready", "message": "Connected"})
        log.info("Deepgram WebSocket stream opened — ready for real-time audio")

        # Coroutine 1: Listen to Deepgram → put final transcripts into queue
        async def deepgram_listener():
            try:
                async for message in dg_connection:
                    try:
                        data = json.loads(message)
                        msg_type = data.get("type", "")
                        if msg_type == "Results":
                            channel = data.get("channel", {})
                            alts = channel.get("alternatives", [])
                            if alts:
                                transcript = alts[0].get("transcript", "").strip()
                                is_final = data.get("is_final", False)
                                if transcript:
                                    if is_final:
                                        log.info(f"Deepgram final: '{transcript}'")
                                        await transcript_queue.put(transcript)
                                    else:
                                        try:
                                            await websocket.send_json({"type": "interim", "text": transcript})
                                        except Exception:
                                            pass
                        elif msg_type == "Metadata":
                            log.debug(f"Deepgram metadata: {data}")
                        elif msg_type == "Error":
                            log.error(f"Deepgram error event: {data}")
                    except Exception as exc:
                        log.warning(f"Deepgram message parse error: {exc}")
            except Exception as exc:
                log.info(f"Deepgram listener ended: {exc}")
            finally:
                await transcript_queue.put(None)  # Signal processor to stop

        # Coroutine 2: consume final transcripts → LLM → TTS → send audio to client
        async def process_transcripts():
            conversation_history = []
            while True:
                try:
                    transcript = await transcript_queue.get()
                    if transcript is None:
                        break
                    try:
                        await websocket.send_json({"type": "transcript", "text": transcript})

                        response, _send_action, unanswered = await VoiceAIService.generate_response(
                            guest_message=transcript,
                            tenant_config=tenant_config,
                            conversation_history=conversation_history,
                            guest_name="Host Voice",
                            guest_language="en"
                        )
                        log.info(f"LLM response: {str(response)[:120]}")

                        response_text = response
                        if isinstance(response, str) and response.strip().startswith("{"):
                            try:
                                parsed = json.loads(response)
                                response_text = parsed.get("voice", parsed.get("response", response))
                            except Exception:
                                pass

                        await websocket.send_json({"type": "response", "text": response_text})

                        # Emit knowledge gap if AI couldn't answer (host can fill in missing info)
                        if unanswered and str(unanswered).strip():
                            await websocket.send_json({"type": "gap", "text": str(unanswered).strip()})

                        conversation_history.append({"role": "user", "content": transcript})
                        conversation_history.append({"role": "assistant", "content": response_text})
                        if len(conversation_history) > 20:
                            conversation_history = conversation_history[-20:]

                        audio_bytes, _ = await VoiceAIService.synthesize_speech(response_text, voice_id=voice_id)
                        if audio_bytes:
                            import base64
                            await websocket.send_json({"type": "audio", "audio": base64.b64encode(audio_bytes).decode()})
                            log.info(f"Sent TTS audio: {len(audio_bytes)} bytes")
                            _log_tts_usage(db, tenant.id, response_text, audio_bytes)
                        else:
                            log.warning("TTS returned no audio for live admin voice test (provider=%s)", VoiceAIService.TTS_PROVIDER)
                            await websocket.send_json({
                                "type": "error",
                                "message": "TTS returned no audio. Check the Google TTS API key and config in Voice AI settings."
                            })

                    except Exception as exc:
                        import traceback
                        log.error(f"Voice processing error: {exc}\n{traceback.format_exc()}")
                        try:
                            await websocket.send_json({"type": "error", "message": str(exc)[:100]})
                        except Exception:
                            pass
                except asyncio.CancelledError:
                    break

        listener_task = asyncio.create_task(deepgram_listener())  # noqa: F841 (referenced in finally via outer scope)
        process_task = asyncio.create_task(process_transcripts())

        # Main loop: receive binary audio + control messages from browser
        # PCM audio → forward to Deepgram; text → handle mute/control
        while True:
            try:
                # Check for inactivity timeout
                elapsed_since_activity = time.time() - last_activity_time
                if elapsed_since_activity > INACTIVITY_TIMEOUT_SECONDS:
                    log.warning(f"Voice AI call inactive for {elapsed_since_activity:.0f}s (timeout: {INACTIVITY_TIMEOUT_SECONDS}s) — closing")
                    await websocket.send_json({"type": "error", "message": "Call ended due to inactivity (3+ minutes without input)"})
                    break

                # Use a timeout on receive to periodically check inactivity
                try:
                    msg = await asyncio.wait_for(websocket.receive(), timeout=30.0)
                except asyncio.TimeoutError:
                    # Timeout is ok, we'll loop back and check inactivity again
                    continue

                if msg.get("type") == "websocket.disconnect":
                    break
                raw_bytes = msg.get("bytes")
                text_data = msg.get("text")
                if raw_bytes:
                    last_activity_time = time.time()
                    if not is_muted:
                        await dg_connection.send(raw_bytes)
                elif text_data:
                    last_activity_time = time.time()
                    try:
                        control = json.loads(text_data)
                        if control.get("type") == "mute":
                            is_muted = control.get("muted", False)
                            log.debug(f"Mute: {is_muted}")
                    except Exception:
                        pass
            except WebSocketDisconnect:
                break
            except Exception as e:
                log.info(f"Voice AI receive loop ended: {e}")
                break

    except WebSocketDisconnect:
        log.info("Voice AI live call disconnected")
    except Exception as e:
        import traceback
        log.error(f"Voice AI WebSocket error: {e}\n{traceback.format_exc()}")
        try:
            await websocket.send_json({"type": "error", "message": str(e)[:100]})
        except Exception:
            pass
    finally:
        # Cancel background tasks
        for t in [listener_task, process_task]:
            if t:
                t.cancel()
                try:
                    await t
                except (asyncio.CancelledError, Exception):
                    pass
        # Close Deepgram WebSocket
        if dg_connection:
            try:
                await dg_connection.close()
            except Exception:
                pass
        try:
            await websocket.close()
        except Exception:
            pass
        log.info("Voice AI live call ended")


# ---------------------------------------------------------------------------
# Voice AI — Admin Backend Configuration
# ---------------------------------------------------------------------------

@app.get("/admin/voice-ai-backend", response_class=HTMLResponse)
def admin_voice_ai_backend(request: Request, db: Session = Depends(get_db)):
    """Admin voice AI backend configuration and testing"""
    admin = _require_admin(request, db)
    sys_conf = load_system_config(db, create_if_missing=True) or SystemConfig()
    schema_drift = bool(_voice_ai_critical_schema_missing(db))

    return templates.TemplateResponse(
        "admin_voice_ai_backend.html",
        {
            "request": request,
            "admin": admin,
            "sys_conf": sys_conf,
            "schema_drift": schema_drift,
        }
    )


@app.post("/admin/voice-ai/test-connection")
async def admin_test_voice_ai_connection(request: Request, db: Session = Depends(get_db)):
    """Test voice AI service connections (Deepgram, OpenRouter, ElevenLabs)"""
    try:
        _require_admin(request, db)
        sys_conf = load_system_config(db) or SystemConfig()

        results = {
            "deepgram": None,
            "openrouter": None,
            "google_tts": None,
            "elevenlabs": None,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "schema_drift": bool(_voice_ai_critical_schema_missing(db)),
        }

        # Test Deepgram
        try:
            if sys_conf and sys_conf.deepgram_api_key_enc:
                api_key = decrypt(sys_conf.deepgram_api_key_enc)
                if not api_key:
                    results["deepgram"] = "✗ Decryption failed (key corruption?)"
                else:
                    import urllib.request
                    req = urllib.request.Request(
                        "https://api.deepgram.com/v1/models",
                        headers={
                            "Authorization": f"Token {api_key}",
                            "Content-Type": "application/json",
                        }
                    )
                    try:
                        urllib.request.urlopen(req, timeout=5)
                        results["deepgram"] = "✓ Deepgram API key valid"
                    except urllib.error.HTTPError as e:
                        if e.code == 401:
                            results["deepgram"] = "✗ Invalid API key"
                        else:
                            results["deepgram"] = f"✗ HTTP {e.code}"
            else:
                results["deepgram"] = "✗ No API key configured"
        except Exception as e:
            results["deepgram"] = f"✗ {str(e)[:50]}"

        # Test OpenRouter
        try:
            if sys_conf and sys_conf.openrouter_api_key_enc:
                api_key = decrypt(sys_conf.openrouter_api_key_enc)
                if not api_key:
                    results["openrouter"] = "✗ Decryption failed (key corruption?)"
                else:
                    import urllib.request
                    # Use /models endpoint which is more reliable for API key validation
                    req = urllib.request.Request(
                        "https://openrouter.ai/api/v1/models",
                        headers={
                            "Authorization": f"Bearer {api_key}",
                            "Content-Type": "application/json",
                        }
                    )
                    try:
                        resp = urllib.request.urlopen(req, timeout=5)
                        results["openrouter"] = "✓ OpenRouter API key valid"
                    except urllib.error.HTTPError as e:
                        if e.code == 401 or e.code == 403:
                            results["openrouter"] = "✗ Invalid API key"
                        else:
                            results["openrouter"] = f"✗ HTTP {e.code}"
            else:
                results["openrouter"] = "✗ No API key configured"
        except Exception as e:
            results["openrouter"] = f"✗ {str(e)[:50]}"

        # Test Google Cloud TTS
        try:
            if sys_conf and sys_conf.google_tts_api_key_enc:
                api_key = decrypt(sys_conf.google_tts_api_key_enc)
                if not api_key:
                    results["google_tts"] = "✗ Decryption failed (key corruption?)"
                else:
                    import httpx as _httpx
                    import base64 as _base64
                    async with _httpx.AsyncClient(timeout=10) as client:
                        resp = await client.post(
                            f"https://texttospeech.googleapis.com/v1/text:synthesize?key={api_key}",
                            json={
                                "input": {"text": "OK"},
                                "voice": {
                                    "languageCode": getattr(sys_conf, "voice_google_tts_language", None) or "en-US",
                                    "name": getattr(sys_conf, "voice_google_tts_voice", None) or "en-US-Neural2-F",
                                },
                                "audioConfig": {"audioEncoding": "MP3"},
                            },
                        )
                    if resp.status_code == 200 and resp.json().get("audioContent"):
                        results["google_tts"] = "✓ Google TTS API key valid and synthesis working"
                    elif resp.status_code == 400:
                        results["google_tts"] = f"✗ Bad request (check voice/language config): {resp.text[:80]}"
                    elif resp.status_code in (401, 403):
                        results["google_tts"] = "✗ Invalid or unauthorized API key"
                    else:
                        results["google_tts"] = f"✗ HTTP {resp.status_code}: {resp.text[:60]}"
            else:
                results["google_tts"] = "✗ No Google TTS API key configured"
        except Exception as e:
            results["google_tts"] = f"✗ {str(e)[:50]}"

        # Test ElevenLabs
        try:
            if sys_conf and sys_conf.elevenlabs_api_key_enc:
                api_key = decrypt(sys_conf.elevenlabs_api_key_enc)
                if not api_key:
                    results["elevenlabs"] = "✗ Decryption failed (key corruption?)"
                else:
                    import httpx

                    selected_voice_id = getattr(sys_conf, "voice_elevenlabs_voice_id", None) or "EXAVITQu4vr4xnSDxMaL"
                    model_id = sys_conf.voice_elevenlabs_model or "eleven_turbo_v2"
                    stability = float(sys_conf.voice_elevenlabs_stability or 0.5)
                    similarity = float(sys_conf.voice_elevenlabs_similarity or 0.75)

                    async with httpx.AsyncClient(timeout=10) as client:
                        auth_resp = await client.get(
                            "https://api.elevenlabs.io/v1/user",
                            headers={"xi-api-key": api_key},
                        )

                        if auth_resp.status_code in {401, 403}:
                            body_lower = auth_resp.text.lower()
                            if (
                                "invalid api key" in body_lower
                                or "invalid_api_key" in body_lower
                                or auth_resp.status_code == 401
                            ):
                                results["elevenlabs"] = "✗ Invalid API key"
                            else:
                                results["elevenlabs"] = "✓ ElevenLabs API key valid, but this account cannot access the requested endpoint"
                        elif auth_resp.status_code >= 400:
                            results["elevenlabs"] = f"✗ HTTP {auth_resp.status_code}: {auth_resp.text[:60]}"
                        else:
                            tts_resp = await client.post(
                                f"https://api.elevenlabs.io/v1/text-to-speech/{selected_voice_id}",
                                headers={
                                    "xi-api-key": api_key,
                                    "Content-Type": "application/json",
                                },
                                json={
                                    "text": "Voice test OK.",
                                    "model_id": model_id,
                                    "voice_settings": {
                                        "stability": stability,
                                        "similarity_boost": similarity,
                                    },
                                },
                            )
                            body_lower = tts_resp.text.lower()
                            if tts_resp.status_code == 200 and tts_resp.content:
                                results["elevenlabs"] = "✓ ElevenLabs API key valid and sample synthesis returned audio"
                            elif tts_resp.status_code == 401 and (
                                "detected_unusual_activity" in body_lower
                                or "free tier usage disabled" in body_lower
                            ):
                                results["elevenlabs"] = "✗ ElevenLabs blocked TTS from this Railway environment (free-tier unusual-activity restriction)"
                            elif tts_resp.status_code == 401:
                                results["elevenlabs"] = "✗ Invalid API key"
                            elif tts_resp.status_code == 403:
                                results["elevenlabs"] = "✓ ElevenLabs API key valid, but this account cannot access the requested TTS endpoint/model"
                            elif tts_resp.status_code == 400 and ("model" in body_lower or "voice" in body_lower):
                                results["elevenlabs"] = "✓ ElevenLabs API key valid, but the selected ElevenLabs voice/model was rejected"
                            elif tts_resp.status_code == 400 and ("credit" in body_lower or "quota" in body_lower or "limit" in body_lower):
                                results["elevenlabs"] = "✓ ElevenLabs API key valid, but the ElevenLabs account has no usable credits/quota"
                            elif tts_resp.status_code >= 400:
                                results["elevenlabs"] = f"✓ ElevenLabs API key valid, but sample synthesis failed (HTTP {tts_resp.status_code}: {tts_resp.text[:60]})"
                            else:
                                results["elevenlabs"] = "✓ ElevenLabs API key valid, but ElevenLabs returned an empty audio response"
            else:
                results["elevenlabs"] = "✗ No API key configured"
        except Exception as e:
            results["elevenlabs"] = f"✗ {str(e)[:50]}"

        return JSONResponse(results)

    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/admin/voice-ai/status")
async def admin_voice_ai_status(request: Request, db: Session = Depends(get_db)):
    """Get current voice AI configuration status"""
    _require_admin(request, db)
    sys_conf = load_system_config(db) or SystemConfig()

    return JSONResponse({
        "primary_model": sys_conf.voice_llm_model or "Not set",
        "backup_model": sys_conf.voice_llm_backup_model or "Not set",
        "emergency_model": sys_conf.voice_llm_emergency_model or "Not set",
        "deepgram_model": sys_conf.voice_deepgram_model or "nova-2",
        "elevenlabs_model": sys_conf.voice_elevenlabs_model or "eleven_turbo_v2",
        "voice_id": getattr(sys_conf, "voice_elevenlabs_voice_id", None) or "EXAVITQu4vr4xnSDxMaL",
        "max_tokens": sys_conf.voice_llm_max_tokens or 300,
        "temperature": sys_conf.voice_llm_temperature or 0.7,
        "stability": sys_conf.voice_elevenlabs_stability or 0.5,
        "similarity": sys_conf.voice_elevenlabs_similarity or 0.75,
        "schema_drift": bool(_voice_ai_critical_schema_missing(db)),
    })


@app.get("/api/admin/openrouter-models")
async def admin_openrouter_models(request: Request, db: Session = Depends(get_db)):
    """Fetch all models available on this OpenRouter API key. Admin only."""
    _require_admin(request, db)
    sys_conf = load_system_config(db)
    if not sys_conf or not sys_conf.openrouter_api_key_enc:
        return JSONResponse({"ok": False, "error": "OpenRouter API key not configured."}, status_code=400)
    api_key = decrypt(sys_conf.openrouter_api_key_enc) or sys_conf.openrouter_api_key_enc
    try:
        req = urllib.request.Request(
            "https://openrouter.ai/api/v1/models",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://hostai.app",
                "X-OpenRouter-Title": "HostAI",
            },
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = json.loads(resp.read().decode())
        models = body.get("data", [])
        # Return id + name, sorted alphabetically
        result = sorted(
            [{"id": m["id"], "name": m.get("name", m["id"])} for m in models],
            key=lambda x: x["id"],
        )
        return JSONResponse({"ok": True, "models": result, "count": len(result)})
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")[:500]
        return JSONResponse({"ok": False, "error": f"HTTP {e.code}: {body}"}, status_code=400)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.post("/api/admin/model-test")
async def admin_model_test(request: Request, db: Session = Depends(get_db)):
    """Test any OpenRouter model directly. Admin only."""
    _require_admin(request, db)
    validate_csrf_header(request)

    data = await request.json()
    model_id = (data.get("model") or "").strip()
    prompt = (data.get("prompt") or "").strip()
    system_prompt = (data.get("system_prompt") or "You are a helpful AI assistant.").strip()

    if not model_id or not prompt:
        return JSONResponse({"ok": False, "error": "model and prompt are required"}, status_code=400)

    sys_conf = load_system_config(db)
    if not sys_conf or not sys_conf.openrouter_api_key_enc:
        return JSONResponse({"ok": False, "error": "OpenRouter API key not configured in AI Engine settings."}, status_code=400)

    api_key = decrypt(sys_conf.openrouter_api_key_enc) or sys_conf.openrouter_api_key_enc
    payload = json.dumps({
        "model": model_id,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": 512,
        "temperature": 0.7,
    }).encode()
    try:
        req = urllib.request.Request(
            "https://openrouter.ai/api/v1/chat/completions",
            data=payload,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://hostai.app",
                "X-OpenRouter-Title": "HostAI",
            },
            method="POST",
        )
        t0 = datetime.now(timezone.utc)
        with urllib.request.urlopen(req, timeout=30) as resp:
            resp_body = json.loads(resp.read().decode())
        elapsed_ms = int((datetime.now(timezone.utc) - t0).total_seconds() * 1000)
        content = resp_body["choices"][0]["message"]["content"] or ""
        usage = resp_body.get("usage", {})
        return JSONResponse({
            "ok": True,
            "reply": content,
            "model": model_id,
            "input_tokens": usage.get("prompt_tokens"),
            "output_tokens": usage.get("completion_tokens"),
            "elapsed_ms": elapsed_ms,
        })
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        try:
            err_json = json.loads(body)
            err_msg = err_json.get("error", {}).get("message") or body
        except Exception:
            err_msg = body
        return JSONResponse({"ok": False, "error": f"HTTP {e.code}: {err_msg}"}, status_code=400)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


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
# JSON API endpoints for draft actions (no page reload)
# ---------------------------------------------------------------------------

@app.post("/api/drafts/{draft_id}/approve")
def api_approve_draft(draft_id: str, request: Request, db: Session = Depends(get_db)):
    """Approve a single draft — returns JSON, no redirect."""
    try:
        tenant_id = get_current_tenant_id(request)
    except HTTPException:
        return JSONResponse({"ok": False, "error": "not_authenticated"}, status_code=401)
    csrf = request.headers.get("X-CSRF-Token", "")
    validate_csrf(request, csrf)
    rate_limit(f"draft:{tenant_id}", max_requests=120, window_seconds=3600)

    draft = db.query(Draft).filter_by(id=draft_id, tenant_id=tenant_id).first()
    if not draft:
        return JSONResponse({"ok": False, "error": "not_found"}, status_code=404)
    try:
        _execute_draft(draft, draft.draft, tenant_id, db)
        return JSONResponse({"ok": True})
    except Exception as exc:
        log.error("[%s] api_approve_draft %s failed: %s", tenant_id, draft_id, exc)
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)


@app.post("/api/drafts/{draft_id}/skip")
def api_skip_draft(draft_id: str, request: Request, db: Session = Depends(get_db)):
    """Skip a single draft — returns JSON, no redirect."""
    try:
        tenant_id = get_current_tenant_id(request)
    except HTTPException:
        return JSONResponse({"ok": False, "error": "not_authenticated"}, status_code=401)
    csrf = request.headers.get("X-CSRF-Token", "")
    validate_csrf(request, csrf)

    draft = db.query(Draft).filter_by(id=draft_id, tenant_id=tenant_id).first()
    if not draft:
        return JSONResponse({"ok": False, "error": "not_found"}, status_code=404)
    draft.status = "skipped"
    db.commit()
    return JSONResponse({"ok": True})


@app.post("/api/drafts/bulk-approve")
def api_bulk_approve(request: Request, db: Session = Depends(get_db)):
    """Approve all pending drafts — returns JSON {ok, count}."""
    try:
        tenant_id = get_current_tenant_id(request)
    except HTTPException:
        return JSONResponse({"ok": False, "error": "not_authenticated"}, status_code=401)
    csrf = request.headers.get("X-CSRF-Token", "")
    validate_csrf(request, csrf)
    rate_limit(f"draft:{tenant_id}", max_requests=120, window_seconds=3600)

    pending = db.query(Draft).filter_by(tenant_id=tenant_id, status="pending").all()
    count = 0
    for draft in pending:
        try:
            _execute_draft(draft, draft.draft, tenant_id, db)
            count += 1
        except Exception as exc:
            log.error("[%s] bulk approve draft %s: %s", tenant_id, draft.id, exc)
    return JSONResponse({"ok": True, "count": count})


@app.post("/api/drafts/bulk-skip")
def api_bulk_skip(request: Request, db: Session = Depends(get_db)):
    """Skip all pending drafts — returns JSON {ok, count}."""
    try:
        tenant_id = get_current_tenant_id(request)
    except HTTPException:
        return JSONResponse({"ok": False, "error": "not_authenticated"}, status_code=401)
    csrf = request.headers.get("X-CSRF-Token", "")
    validate_csrf(request, csrf)

    pending = db.query(Draft).filter_by(tenant_id=tenant_id, status="pending").all()
    for draft in pending:
        draft.status = "skipped"
    if pending:
        db.add(ActivityLog(tenant_id=tenant_id, event_type="bulk_skipped",
                           message=f"Bulk-skipped {len(pending)} pending draft(s)"))
        db.commit()
    return JSONResponse({"ok": True, "count": len(pending)})


# ---------------------------------------------------------------------------
# Manual host-to-guest message send
# ---------------------------------------------------------------------------

@app.post("/api/conversations/send")
async def manual_send_message(request: Request, db: Session = Depends(get_db)):
    """Host sends a manual message to a guest via their active WhatsApp/SMS channel."""
    try:
        tenant_id = get_current_tenant_id(request)
    except HTTPException:
        return JSONResponse({"ok": False, "error": "not_authenticated"}, status_code=401)
    csrf = request.headers.get("X-CSRF-Token", "")
    validate_csrf(request, csrf)
    data = await request.json()
    to_phone = (data.get("to_phone") or "").strip()
    message = (data.get("message") or "").strip()
    if not to_phone or not message:
        return JSONResponse({"ok": False, "error": "to_phone and message required"}, status_code=400)

    cfg = db.query(TenantConfig).filter_by(tenant_id=tenant_id).first()
    if not cfg:
        return JSONResponse({"ok": False, "error": "no config"}, status_code=400)

    sent = False
    if cfg.wa_mode == "twilio":
        try:
            from web.sms_sender import send_whatsapp_twilio
            from web.crypto import decrypt
            auth_token = decrypt(cfg.twilio_auth_token_enc) if cfg.twilio_auth_token_enc else None
            if cfg.twilio_whatsapp_number and cfg.twilio_account_sid and auth_token:
                sent = send_whatsapp_twilio(cfg.twilio_account_sid, auth_token, cfg.twilio_whatsapp_number, to_phone, message)
        except Exception as exc:
            log.error("[%s] Manual send Twilio WA error: %s", tenant_id, exc)
    elif cfg.wa_mode == "meta_cloud":
        try:
            from web.meta_sender import send_whatsapp
            from web.crypto import decrypt
            token = decrypt(cfg.whatsapp_token_enc) if cfg.whatsapp_token_enc else None
            if cfg.whatsapp_phone_id and token:
                sent = send_whatsapp(cfg.whatsapp_phone_id, token, to_phone, message)
        except Exception as exc:
            log.error("[%s] Manual send Meta WA error: %s", tenant_id, exc)

    if sent:
        db.add(ActivityLog(tenant_id=tenant_id, event_type="manual_message_sent",
                           message=f"Host manual send to {to_phone}: {message[:80]}"))
        db.commit()
        return JSONResponse({"ok": True})
    return JSONResponse({"ok": False, "error": "Send failed — check WhatsApp configuration"}, status_code=500)


# ---------------------------------------------------------------------------
# Quick Replies — host-defined canned responses
# ---------------------------------------------------------------------------

@app.get("/api/quick-replies")
def list_quick_replies(request: Request, db: Session = Depends(get_db)):
    """Return all quick replies for the current tenant."""
    try:
        tenant_id = get_current_tenant_id(request)
    except HTTPException:
        return JSONResponse({"ok": False, "error": "not_authenticated"}, status_code=401)
    from web.models import QuickReply
    items = db.query(QuickReply).filter_by(tenant_id=tenant_id, is_active=True).order_by(QuickReply.sort_order, QuickReply.id).all()
    return JSONResponse({"ok": True, "items": [{"id": q.id, "label": q.label, "message_template": q.message_template} for q in items]})


@app.post("/api/quick-replies")
async def create_quick_reply(request: Request, db: Session = Depends(get_db)):
    """Create a new quick reply."""
    try:
        tenant_id = get_current_tenant_id(request)
    except HTTPException:
        return JSONResponse({"ok": False, "error": "not_authenticated"}, status_code=401)
    csrf = request.headers.get("X-CSRF-Token", "")
    validate_csrf(request, csrf)
    from web.models import QuickReply
    data = await request.json()
    label = (data.get("label") or "").strip()[:128]
    message = (data.get("message_template") or "").strip()
    if not label or not message:
        return JSONResponse({"ok": False, "error": "label and message_template required"}, status_code=400)
    qr = QuickReply(tenant_id=tenant_id, label=label, message_template=message)
    db.add(qr)
    db.commit()
    db.refresh(qr)
    return JSONResponse({"ok": True, "id": qr.id, "label": qr.label})


@app.delete("/api/quick-replies/{qr_id}")
def delete_quick_reply(qr_id: int, request: Request, db: Session = Depends(get_db)):
    """Soft-delete a quick reply."""
    try:
        tenant_id = get_current_tenant_id(request)
    except HTTPException:
        return JSONResponse({"ok": False, "error": "not_authenticated"}, status_code=401)
    csrf = request.headers.get("X-CSRF-Token", "")
    validate_csrf(request, csrf)
    from web.models import QuickReply
    qr = db.query(QuickReply).filter_by(id=qr_id, tenant_id=tenant_id).first()
    if not qr:
        return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
    qr.is_active = False
    db.commit()
    return JSONResponse({"ok": True})


# ---------------------------------------------------------------------------
# Upsell Offers — host-configured revenue opportunities
# ---------------------------------------------------------------------------

@app.get("/api/upsell-offers")
def list_upsell_offers(request: Request, db: Session = Depends(get_db)):
    """Return all upsell offers for the current tenant."""
    try:
        tenant_id = get_current_tenant_id(request)
    except HTTPException:
        return JSONResponse({"ok": False, "error": "not_authenticated"}, status_code=401)
    from web.models import UpsellOffer
    items = db.query(UpsellOffer).filter_by(tenant_id=tenant_id, is_active=True).all()
    return JSONResponse({"ok": True, "items": [
        {"id": o.id, "offer_type": o.offer_type, "title": o.title, "price_str": o.price_str,
         "trigger_keywords": o.trigger_keywords, "accepted_count": o.accepted_count,
         "total_revenue": o.total_revenue}
        for o in items
    ]})


@app.post("/api/upsell-offers")
async def create_upsell_offer(request: Request, db: Session = Depends(get_db)):
    """Create a new upsell offer."""
    try:
        tenant_id = get_current_tenant_id(request)
    except HTTPException:
        return JSONResponse({"ok": False, "error": "not_authenticated"}, status_code=401)
    csrf = request.headers.get("X-CSRF-Token", "")
    validate_csrf(request, csrf)
    from web.models import UpsellOffer
    data = await request.json()
    offer = UpsellOffer(
        tenant_id=tenant_id,
        offer_type=(data.get("offer_type") or "custom")[:32],
        title=(data.get("title") or "")[:128],
        price_str=(data.get("price_str") or "")[:32],
        trigger_keywords=data.get("trigger_keywords") or "",
        message_template=data.get("message_template") or "",
    )
    db.add(offer)
    db.commit()
    db.refresh(offer)
    return JSONResponse({"ok": True, "id": offer.id})


@app.delete("/api/upsell-offers/{offer_id}")
def delete_upsell_offer(offer_id: int, request: Request, db: Session = Depends(get_db)):
    """Remove a upsell offer."""
    try:
        tenant_id = get_current_tenant_id(request)
    except HTTPException:
        return JSONResponse({"ok": False, "error": "not_authenticated"}, status_code=401)
    csrf = request.headers.get("X-CSRF-Token", "")
    validate_csrf(request, csrf)
    from web.models import UpsellOffer
    offer = db.query(UpsellOffer).filter_by(id=offer_id, tenant_id=tenant_id).first()
    if not offer:
        return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
    offer.is_active = False
    db.commit()
    return JSONResponse({"ok": True})


# ---------------------------------------------------------------------------
# Guest CRM notes — per guest_contact notes for hosts
# ---------------------------------------------------------------------------

@app.post("/api/guest-contacts/{contact_id}/crm-note")
async def add_crm_note(contact_id: str, request: Request, db: Session = Depends(get_db)):
    """Append a CRM note to a guest contact."""
    try:
        tenant_id = get_current_tenant_id(request)
    except HTTPException:
        return JSONResponse({"ok": False, "error": "not_authenticated"}, status_code=401)
    csrf = request.headers.get("X-CSRF-Token", "")
    validate_csrf(request, csrf)
    data = await request.json()
    note = (data.get("note") or "").strip()
    if not note:
        return JSONResponse({"ok": False, "error": "note required"}, status_code=400)
    contact = db.query(GuestContact).filter_by(id=contact_id, tenant_id=tenant_id).first()
    if not contact:
        return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
    import json as _json
    existing = []
    if contact.crm_notes:
        try:
            existing = _json.loads(contact.crm_notes)
        except Exception:
            existing = []
    existing.append({"note": note, "at": datetime.now(timezone.utc).isoformat()})
    contact.crm_notes = _json.dumps(existing)
    db.commit()
    return JSONResponse({"ok": True, "count": len(existing)})


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

    raw_checkin_token = secrets.token_urlsafe(32)
    res.checkin_token = _store_token(raw_checkin_token)
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
    return RedirectResponse(f"/reservations?checkin_link={raw_checkin_token}", status_code=302)


@app.get("/checkin/{token}", response_class=HTMLResponse)
def checkin_portal(token: str, request: Request, db: Session = Depends(get_db)):
    """Public guest check-in page — no auth required, only the token."""
    res = _find_reservation_by_checkin_token(db, token)
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
# Conversations Settings — manage SMS / WhatsApp Cloud integrations
# ---------------------------------------------------------------------------

@app.post("/conversations/settings", response_class=HTMLResponse)
async def conversations_settings_save(
    request: Request,
    wa_mode: str = Form("none"),
    whatsapp_number: str = Form(""),
    whatsapp_token: str = Form(""),
    whatsapp_phone_id: str = Form(""),
    whatsapp_verify_token: str = Form(""),
    sms_mode: str = Form("none"),
    twilio_account_sid: str = Form(""),
    twilio_auth_token: str = Form(""),
    twilio_from_number: str = Form(""),
    sms_notify_number: str = Form(""),
    csrf_token: str = Form(None),
    db: Session = Depends(get_db),
):
    try:
        tenant_id = get_current_tenant_id(request)
    except HTTPException:
        return _redirect_login()
    validate_csrf(request, csrf_token)
    rate_limit(f"conv-settings:{tenant_id}", max_requests=30, window_seconds=3600)

    cfg = _get_or_create_config(tenant_id, db)
    
    # WhatsApp Meta Cloud
    cfg.whatsapp_number   = whatsapp_number.strip() or None
    cfg.whatsapp_phone_id = whatsapp_phone_id.strip() or None
    if whatsapp_verify_token.strip():
        cfg.whatsapp_verify_token = whatsapp_verify_token.strip()
    if whatsapp_token.strip():
        cfg.whatsapp_token_enc = encrypt(whatsapp_token.strip())
    # Auto-detect wa_mode from credentials — form field is unreliable
    if cfg.whatsapp_phone_id and cfg.whatsapp_token_enc:
        cfg.wa_mode = "meta_cloud"
    else:
        cfg.wa_mode = "none"

    # SMS / Twilio
    cfg.sms_mode           = sms_mode.strip() or "none"
    cfg.twilio_account_sid = twilio_account_sid.strip() or None
    cfg.twilio_from_number = twilio_from_number.strip() or None
    if sms_notify_number.strip():
        cfg.sms_notify_number = sms_notify_number.strip()
    if twilio_auth_token.strip():
        cfg.twilio_auth_token_enc = encrypt(twilio_auth_token.strip())

    db.add(ActivityLog(tenant_id=tenant_id, event_type="settings_saved",
                       message="Messaging settings updated"))
    db.commit()
    worker_manager.restart_worker(tenant_id)

    return RedirectResponse("/conversations?tab=settings&saved=true", status_code=303)


# ---------------------------------------------------------------------------
# Conversations — view guest message threads
# ---------------------------------------------------------------------------

@app.get("/conversations", response_class=HTMLResponse)
def conversations_page(
    request: Request,
    q: str = None,
    tab: str = "panel",
    db: Session = Depends(get_db),
    _=Depends(require_flag("CONVERSATION_VIEW")),
):
    """Display guest conversation threads."""
    try:
        tenant_id = get_current_tenant_id(request)
    except HTTPException:
        raise HTTPException(status_code=401, detail="Unauthorized")

    tenant = _get_tenant(tenant_id, db)

    try:
        tenant = _get_tenant(tenant_id, db)
    except Exception:
        db.rollback()
        raise

    cfg = _get_or_create_config(tenant_id, db)
    from datetime import datetime, timezone

    # Get unique conversations grouped by thread_key
    # We'll get the most recent draft for each conversation
    query = (
        db.query(Draft)
        .filter(Draft.tenant_id == tenant_id, Draft.source.in_(["whatsapp", "sms", "email"]))
    )
    if q:
        search_term = f"%{q.strip()}%"
        query = query.filter(
            (Draft.guest_name.ilike(search_term))
            | (Draft.reply_to.ilike(search_term))
            | (Draft.thread_key.ilike(search_term))
        )

    all_drafts = query.order_by(Draft.created_at.desc()).all()

    # Group by thread_key and guest phone
    conversations = {}
    for draft in all_drafts:
        key = (draft.thread_key, draft.reply_to, draft.guest_name)
        
        # Ensure created_at is a datetime object in case SQLite mapped it as string
        d_created = draft.created_at
        if isinstance(d_created, str):
            try:
                # Basic string parsing fallback
                d_created = datetime.strptime(d_created.split(".")[0], "%Y-%m-%d %H:%M:%S")
            except Exception:
                d_created = datetime.now(timezone.utc)
        elif d_created is None:
            d_created = datetime.now(timezone.utc)
            
        if key not in conversations:
            conversations[key] = {
                "thread_key": draft.thread_key,
                "guest_phone": draft.reply_to,
                "guest_name": draft.guest_name,
                "message_count": 0,
                "last_at": d_created,
            }
        conversations[key]["message_count"] += 1
        conversations[key]["last_at"] = max(conversations[key]["last_at"], d_created)

    # Convert to list and sort by last activity
    conv_list = sorted(conversations.values(), key=lambda x: x["last_at"], reverse=True)

    initial_messages = _conversation_messages_for_thread(db, tenant_id, conv_list[0]["thread_key"]) if conv_list else []

    return templates.TemplateResponse("conversations.html", {
        "request": request,
        "tenant": tenant,
        "cfg": cfg,
        "tab": tab,
        "saved": request.query_params.get("saved") == "true",
        "conversations": conv_list,
        "initial_messages": initial_messages,
        "search_query": q or "",
        "app_base_url": APP_BASE_URL,
    })


# ---------------------------------------------------------------------------
# Mission Control — unified property management dashboard
# ---------------------------------------------------------------------------

@app.get("/mission-control", response_class=HTMLResponse)
def mission_control_dashboard(request: Request, db: Session = Depends(get_db)):
    """Unified dashboard for managing multiple properties."""
    try:
        tenant_id = get_current_tenant_id(request)
    except HTTPException:
        return _redirect_login()

    from web.models import Property, PropertyConfig, EscalatedMessage

    tenant = _get_tenant(tenant_id, db)

    # Get all properties for this tenant
    properties = db.query(Property).filter(
        Property.tenant_id == tenant_id,
        Property.status == "active"
    ).order_by(Property.name).all()

    # Get escalated messages grouped by property
    escalated_all = db.query(EscalatedMessage).filter(
        EscalatedMessage.tenant_id == tenant_id,
        EscalatedMessage.status != "resolved"
    ).order_by(EscalatedMessage.priority.desc(), EscalatedMessage.created_at.desc()).all()

    # Group by property and priority
    alerts_by_property = {}
    for alert in escalated_all:
        if alert.property_id not in alerts_by_property:
            alerts_by_property[alert.property_id] = {
                "critical": [],
                "high": [],
                "medium": [],
                "low": []
            }
        alerts_by_property[alert.property_id][alert.priority].append(alert)

    # Calculate summary stats
    total_alerts = len(escalated_all)
    critical_count = sum(len(v["critical"]) for v in alerts_by_property.values())
    high_count = sum(len(v["high"]) for v in alerts_by_property.values())

    return templates.TemplateResponse("mission_control.html", {
        "request": request,
        "tenant": tenant,
        "properties": properties,
        "escalated_alerts": escalated_all,
        "alerts_by_property": alerts_by_property,
        "total_alerts": total_alerts,
        "critical_count": critical_count,
        "high_count": high_count,
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
    tenant = _get_tenant(tenant_id, db)

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
        "tenant": tenant,
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
    validate_csrf_header(request)

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
    validate_csrf_header(request)

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
    validate_csrf_header(request)

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
# Voice Calling Routes (Twilio)
# ---------------------------------------------------------------------------

from web.integrations.voice import VoiceAIService
from web.models import VoiceCall

def _voice_twiml_error(msg: str = "Sorry, something went wrong. Please try again later."):
    from twilio.twiml.voice_response import VoiceResponse
    r = VoiceResponse()
    r.say(msg)
    r.hangup()
    return Response(str(r), media_type="application/xml")


def _send_voice_message(cfg, guest_phone: str, content: str, channel: str) -> bool:
    """
    Send `content` to `guest_phone` via SMS or WhatsApp depending on `channel`.
    Returns True on success. Uses the tenant's Twilio or Meta credentials.
    """
    try:
        if channel == "sms":
            from twilio.rest import Client as TwilioClient
            from web.crypto import decrypt
            # Try voice Twilio creds first, then SMS creds
            sid   = cfg.voice_twilio_account_sid or cfg.twilio_account_sid
            token = decrypt(cfg.voice_twilio_auth_token_enc or cfg.twilio_auth_token_enc or "")
            frm   = cfg.voice_twilio_from_number or cfg.twilio_from_number
            if not (sid and token and frm):
                log.warning("[VOICE] SMS send skipped — Twilio not fully configured")
                return False
            client = TwilioClient(sid, token)
            client.messages.create(from_=frm, to=guest_phone, body=content)
            log.info(f"[VOICE] SMS sent to {guest_phone[-4:]}: {content[:40]}")
            return True

        if channel == "whatsapp":
            if cfg.wa_mode == "meta_cloud" and cfg.whatsapp_phone_id:
                from web.meta_sender import send_whatsapp
                from web.crypto import decrypt
                token = decrypt(cfg.whatsapp_token_enc or "")
                if token:
                    ok = send_whatsapp(cfg.whatsapp_phone_id, token, guest_phone, content)
                    log.info(f"[VOICE] WhatsApp sent to {guest_phone[-4:]}: ok={ok}")
                    return ok
            elif cfg.sms_mode == "twilio":
                # Fall back to Twilio WhatsApp
                from twilio.rest import Client as TwilioClient
                from web.crypto import decrypt
                # Try voice Twilio creds first, then SMS creds
                sid   = cfg.voice_twilio_account_sid or cfg.twilio_account_sid
                token = decrypt(cfg.voice_twilio_auth_token_enc or cfg.twilio_auth_token_enc or "")
                frm   = f"whatsapp:{cfg.voice_twilio_from_number or cfg.twilio_from_number}"
                if sid and token and frm:
                    client = TwilioClient(sid, token)
                    client.messages.create(from_=frm, to=f"whatsapp:{guest_phone}", body=content)
                    log.info(f"[VOICE] WhatsApp (Twilio) sent to {guest_phone[-4:]}")
                    return True
        return False
    except Exception as e:
        log.error(f"[VOICE] _send_voice_message error: {e}")
        return False


def _handle_knowledge_gap(db, tenant, cfg, voice_call, question: str) -> None:
    """
    Record a question the AI couldn't answer, deduplicate within 24h,
    and alert the host with a direct link to fill in the answer.
    """
    from web.models import VoiceKnowledgeGap
    now = datetime.now(timezone.utc)

    # Deduplicate: don't create a new gap if the same question was logged in the last 24h
    existing = (
        db.query(VoiceKnowledgeGap)
        .filter(
            VoiceKnowledgeGap.tenant_id == tenant.id,
            VoiceKnowledgeGap.question == question,
            VoiceKnowledgeGap.resolved.is_(False),
            VoiceKnowledgeGap.created_at >= now - timedelta(hours=24),
        )
        .first()
    )
    if existing:
        log.info(f"[VOICE] Gap already recorded (dedup): {question[:60]}")
        return

    # Resolve guest identity from call record
    gc = voice_call.guest_contact
    gap = VoiceKnowledgeGap(
        id=str(uuid4()),
        tenant_id=tenant.id,
        call_id=voice_call.id,
        question=question,
        guest_phone=voice_call.guest_phone_number,
        guest_name=gc.guest_name if gc else None,
        guest_room=(gc.room_identifier or gc.property_name) if gc else None,
        resolved=False,
        alerted_at=now,
        created_at=now,
    )
    db.add(gap)
    db.commit()
    db.refresh(gap)

    # Create an IssueTicket for this gap (for integration with ticket system)
    _create_voice_ticket(tenant.id, gap, db)

    # Alert host (SMS to notify number if configured)
    if cfg and cfg.sms_notify_number:
        app_url = os.getenv("APP_BASE_URL", "")
        alert = (
            f"❓ Guest question I couldn't answer:\n"
            f"\"{question}\"\n\n"
            f"Add the answer so I can handle it next time:\n"
            f"{app_url}/voice-calls/gaps"
        )
        _send_voice_message(cfg, cfg.sms_notify_number, alert, "sms")
        log.info(f"[VOICE] Knowledge gap alert sent for: {question[:60]}")


# ──────────────────────────────────────────────────────────────────────────────
# Voice Helper Functions
# ──────────────────────────────────────────────────────────────────────────────

def _get_guest_call_history(tenant_id: str, guest_phone: str, db: Session, limit: int = 3) -> str:
    """
    Fetch previous calls for this guest phone and summarize key topics.
    Returns a brief summary for AI context.
    """
    try:
        previous_calls = (
            db.query(VoiceCall)
            .filter(
                VoiceCall.tenant_id == tenant_id,
                VoiceCall.guest_phone_number == guest_phone,
                VoiceCall.id != None,
            )
            .order_by(VoiceCall.created_at.desc())
            .limit(limit)
            .all()
        )

        if not previous_calls:
            return ""

        # Summarize key topics from previous calls
        topics = set()
        for call in previous_calls:
            if call.ai_responses:
                for resp in call.ai_responses:
                    text = resp.get("text", "").lower()
                    if "wifi" in text:
                        topics.add("WiFi")
                    if "checkout" in text or "check-out" in text:
                        topics.add("Checkout")
                    if "check-in" in text or "checkin" in text:
                        topics.add("Check-in")
                    if "maintenance" in text or "broken" in text or "fix" in text:
                        topics.add("Maintenance")
                    if "parking" in text:
                        topics.add("Parking")

        if topics:
            return f"Guest previously asked about: {', '.join(sorted(topics))}"
        return ""
    except Exception as e:
        log.error(f"[VOICE] Error fetching call history: {e}")
        return ""


def _detect_language(text: str) -> str:
    """
    Detect guest language from transcribed text.
    Uses heuristics to identify common non-English patterns.
    Returns language code (e.g., 'en', 'es', 'fr', 'de', 'zh', 'ja').
    """
    if not text:
        return "en"

    text_lower = text.lower()

    # Common Spanish words
    if any(word in text_lower for word in ["gracias", "hola", "si", "no", "por favor", "agua", "baño"]):
        return "es"

    # Common French words
    if any(word in text_lower for word in ["merci", "bonjour", "oui", "non", "s'il vous", "toilette"]):
        return "fr"

    # Common German words
    if any(word in text_lower for word in ["danke", "guten", "ja", "nein", "bitte", "bad"]):
        return "de"

    # Common Mandarin/Chinese patterns (simplified detection)
    if any(ord(char) >= 0x4E00 and ord(char) <= 0x9FFF for char in text):
        return "zh"

    # Common Japanese patterns
    if any(ord(char) >= 0x3040 and ord(char) <= 0x309F for char in text):
        return "ja"

    return "en"


def _find_guest_by_name(tenant_id: str, name: str, db: Session) -> dict | None:
    """
    Search for guest by name (fuzzy match).
    Checks GuestContact first, then falls back to Reservation.
    Returns guest info dict with source, name, room, property, dates, etc.
    """
    if not name or len(name) < 2:
        return None

    now = datetime.now(timezone.utc)
    name_pattern = f"%{name.strip()}%"

    try:
        # Search GuestContact first (higher priority)
        gc = (
            db.query(GuestContact)
            .filter(
                GuestContact.tenant_id == tenant_id,
                GuestContact.guest_name.ilike(name_pattern),
                GuestContact.status.in_(["active", "pending"]),
                GuestContact.check_out >= now,
            )
            .first()
        )

        if gc:
            return {
                "source": "guest_contact",
                "name": gc.guest_name,
                "phone": gc.guest_phone,
                "room": gc.room_identifier,
                "property": gc.property_name,
                "check_in": gc.check_in,
                "check_out": gc.check_out,
                "guest_contact_id": gc.id,
            }

        # Fallback to Reservation (from CSV imports, PMS syncs, or iCal)
        res = (
            db.query(Reservation)
            .filter(
                Reservation.tenant_id == tenant_id,
                Reservation.guest_name.ilike(name_pattern),
                Reservation.status == "confirmed",
                Reservation.checkout >= now.date(),
            )
            .first()
        )

        if res:
            return {
                "source": "reservation",
                "name": res.guest_name,
                "phone": res.guest_phone,
                "unit": res.unit_identifier,
                "property": res.listing_name,
                "confirmation": res.confirmation_code,
                "check_in": res.checkin,
                "check_out": res.checkout,
                "reservation_id": res.id,
            }

        return None
    except Exception as e:
        log.error(f"[VOICE] Error searching guest by name '{name}': {e}")
        return None


def _find_guest_by_confirmation(tenant_id: str, code: str, db: Session) -> dict | None:
    """
    Search for guest by confirmation code (e.g., Airbnb confirmation or iCal UID).
    Returns guest info dict if found.
    """
    if not code or len(code) < 2:
        return None

    try:
        now = datetime.now(timezone.utc)
        code_pattern = f"%{code.strip()}%"

        # Search Reservation by confirmation_code
        res = (
            db.query(Reservation)
            .filter(
                Reservation.tenant_id == tenant_id,
                Reservation.confirmation_code.ilike(code_pattern),
                Reservation.status == "confirmed",
            )
            .first()
        )

        if res:
            return {
                "source": "confirmation",
                "name": res.guest_name,
                "phone": res.guest_phone,
                "unit": res.unit_identifier,
                "property": res.listing_name,
                "confirmation": res.confirmation_code,
                "check_in": res.checkin,
                "check_out": res.checkout,
                "reservation_id": res.id,
            }

        return None
    except Exception as e:
        log.error(f"[VOICE] Error searching guest by confirmation '{code}': {e}")
        return None


def _create_voice_ticket(tenant_id: str, knowledge_gap: VoiceKnowledgeGap, db: Session) -> Optional[int]:
    """
    Create an IssueTicket from a VoiceKnowledgeGap.
    Returns the ticket ID if created successfully.
    """
    try:
        from web.models import IssueTicket

        ticket = IssueTicket(
            tenant_id=tenant_id,
            category="voice_faq",
            priority="medium",
            status="open",
            title=f"Voice FAQ: {knowledge_gap.question[:100]}",
            description=knowledge_gap.question,
            guest_phone=knowledge_gap.guest_phone,
            guest_name=knowledge_gap.guest_name,
            unit_identifier=knowledge_gap.guest_room,
        )
        db.add(ticket)
        db.commit()
        db.refresh(ticket)

        # Link gap to ticket
        knowledge_gap.issue_ticket_id = ticket.id
        db.commit()

        log.info(f"[VOICE] Created ticket #{ticket.id} for gap {knowledge_gap.id}")
        return ticket.id
    except Exception as e:
        log.error(f"[VOICE] Error creating ticket for gap: {e}")
        return None


@app.post("/api/calls/incoming")
async def handle_incoming_call(request: Request, db: Session = Depends(get_db)):
    """
    Handle incoming Twilio call — greet guest by name if recognised.
    Includes: idempotency, rate limiting, phone normalization, consent flow.
    """
    try:
        form_data = await request.form()
        from_number = form_data.get("From", "")
        call_sid    = form_data.get("CallSid", "")
        to_number   = form_data.get("To", "")

        log.info(f"[VOICE] Incoming call from {from_number} to {to_number}, CallSid={call_sid}")

        # ── Idempotency check: prevent duplicate processing of webhook retries
        idempotency_check = check_idempotency(
            db, "", call_sid, "voice.incoming_call"
        )
        if idempotency_check["is_duplicate"]:
            log.info(f"[VOICE] Duplicate call detected: {call_sid}")
            return _voice_twiml_error("Call already processed.")

        # Find tenant that owns this Twilio number (for voice calls)
        tenant = (
            db.query(Tenant)
            .join(TenantConfig)
            .filter(TenantConfig.voice_twilio_from_number == to_number)
            .first()
        )
        if not tenant or not tenant.voice_enabled:
            return _voice_twiml_error("Sorry, this number is not configured for voice support.")
        if not tenant.config or not _validate_twilio_signature(request, dict(form_data), tenant.config, channel="voice"):
            log.warning("[VOICE] Rejected inbound call webhook for tenant=%s sid=%s", tenant.id if tenant else "unknown", call_sid)
            return _voice_twiml_error("Webhook authentication failed.")

        # ── Rate limiting: check if tenant exceeded call limits
        rate_limit_check = check_rate_limit(db, tenant.id, "voice_calls")
        if not rate_limit_check["allowed"]:
            log.warning(f"[VOICE] Rate limit exceeded for {tenant.id}: {rate_limit_check['reason']}")
            return _voice_twiml_error("Service temporarily unavailable. Please try again later.")
        increment_rate_limit(db, tenant.id, "voice_calls")

        # ── Normalize phone numbers for consistent matching
        normalized_from = normalize_phone(from_number)
        if not normalized_from:
            normalized_from = from_number

        # Try to identify the guest from GuestContact (current stay only)
        now = datetime.now(timezone.utc)
        guest_contact = (
            db.query(GuestContact)
            .filter(
                GuestContact.tenant_id == tenant.id,
                GuestContact.guest_phone == normalized_from,
                GuestContact.status.in_(["active", "pending"]),
                GuestContact.check_in <= now + timedelta(days=1),
                GuestContact.check_out >= now,
            )
            .order_by(GuestContact.check_in.desc())
            .first()
        )

        # Fallback: check Reservation table (CSV imports, PMS syncs)
        reservation = None
        if not guest_contact:
            reservation = (
                db.query(Reservation)
                .filter(
                    Reservation.tenant_id == tenant.id,
                    Reservation.guest_phone == normalized_from,
                    Reservation.status == "confirmed",
                    Reservation.checkin <= (now + timedelta(days=1)).date(),
                    Reservation.checkout >= now.date(),
                )
                .order_by(Reservation.checkin.desc())
                .first()
            )

        # Create VoiceCall record
        voice_call = VoiceCall(
            id=str(uuid4()),
            tenant_id=tenant.id,
            guest_contact_id=guest_contact.id if guest_contact else None,
            reservation_id=reservation.id if reservation else None,
            twilio_call_id=call_sid,
            twilio_phone_number=to_number,
            guest_phone_number=normalized_from,  # Store normalized phone
            call_type="incoming",
            status="ringing",
            created_at=datetime.now(timezone.utc),
        )
        db.add(voice_call)
        db.commit()
        db.refresh(voice_call)

        # Store idempotency result for future webhook retries
        store_idempotency_result(
            db, tenant.id, call_sid, "voice.incoming_call",
            status="success",
            result_data={"voice_call_id": voice_call.id}
        )

        property_name = (tenant.config.property_names or "our property") if tenant.config else "our property"
        guest_name_for_greeting = None
        if guest_contact:
            guest_name_for_greeting = guest_contact.guest_name.split()[0]
        elif reservation:
            guest_name_for_greeting = reservation.guest_name.split()[0]

        if guest_name_for_greeting:
            greeting = f"Hello {guest_name_for_greeting}, welcome back to {property_name}! How can I help you today?"
        else:
            greeting = f"Hello, welcome to {property_name}. How can I help you today?"

        from twilio.twiml.voice_response import VoiceResponse
        response = VoiceResponse()
        response.say(greeting)
        response.record(
            action=f"/api/calls/process-speech?call_id={voice_call.id}",
            method="POST",
            max_length=60,
            play_beep=True,
        )
        response.hangup()
        return Response(str(response), media_type="application/xml")

    except Exception as e:
        # CRITICAL severity fix #3: Don't expose stack traces in production logs
        log.error(f"[VOICE] Error in handle_incoming_call: {type(e).__name__}")
        if _ENVIRONMENT == "development":
            log.debug(f"[VOICE] Full error: {traceback.format_exc()}")
        return _voice_twiml_error()


@app.post("/api/calls/process-speech")
async def process_speech(request: Request, call_id: str, db: Session = Depends(get_db)):
    """
    Full pipeline: STT → LLM (with send-action detection) → TTS → TwiML.
    If the LLM returns a send_action, dispatch SMS/WhatsApp to the guest live.
    """
    try:
        form_data    = await request.form()
        recording_url = form_data.get("RecordingUrl", "")

        log.info(f"[VOICE] Processing speech call_id={call_id}")

        voice_call = db.query(VoiceCall).filter(VoiceCall.id == call_id).first()
        if not voice_call:
            return _voice_twiml_error("Call record not found.")
        cfg = voice_call.tenant.config if voice_call.tenant else None
        if not cfg or not _validate_twilio_signature(request, dict(form_data), cfg, channel="voice"):
            log.warning("[VOICE] Rejected process-speech webhook for call_id=%s", call_id)
            return _voice_twiml_error("Webhook authentication failed.")

        voice_call.started_at = datetime.now(timezone.utc)
        voice_call.status = "answered"
        db.commit()

        # ── Step 1: Transcribe ────────────────────────────────────────────────
        deepgram_cost = estimate_cost("deepgram", "transcribe", duration_seconds=60)
        daily_cost_check = check_rate_limit(db, voice_call.tenant_id, "daily_cost", cost_increment=deepgram_cost)
        if not daily_cost_check["allowed"]:
            log.warning(f"[VOICE] Daily cost limit exceeded for {voice_call.tenant_id}: {daily_cost_check['reason']}")
            from twilio.twiml.voice_response import VoiceResponse
            r = VoiceResponse()
            r.say("Sorry, this service is temporarily unavailable. Your host has been notified.")
            r.hangup()
            return Response(str(r), media_type="application/xml")

        guest_message, confidence = await VoiceAIService.transcribe_audio(recording_url)
        log.info(f"[VOICE] Transcribed: '{guest_message}' (conf={confidence:.2f})")

        # Log Deepgram cost (approximately 1 minute = $0.0043)
        log_api_usage(
            db, voice_call.tenant_id, "deepgram", "transcribe",
            cost_usd=deepgram_cost,
            duration_seconds=60,
            call_id=call_id,
            status="success" if guest_message else "partial"
        )
        increment_rate_limit(db, voice_call.tenant_id, "daily_cost", deepgram_cost)

        if not guest_message:
            from twilio.twiml.voice_response import VoiceResponse
            r = VoiceResponse()
            r.say("Sorry, I didn't catch that. Please try again.")
            r.record(action=f"/api/calls/process-speech?call_id={call_id}", method="POST", max_length=60, play_beep=True)
            r.hangup()
            return Response(str(r), media_type="application/xml")

        voice_call.guest_messages = list(voice_call.guest_messages or [])
        voice_call.guest_messages.append({
            "text": guest_message,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "confidence": confidence,
        })

        # ── Step 1b: Detect guest language ────────────────────────────────────
        detected_lang = _detect_language(guest_message)
        voice_call.guest_language = detected_lang
        log.info(f"[VOICE] Detected language: {detected_lang}")

        # ── Step 2: Build full tenant context ────────────────────────────────
        tenant = voice_call.tenant
        cfg    = tenant.config
        guest_contact = (
            db.query(GuestContact).filter(GuestContact.id == voice_call.guest_contact_id).first()
            if voice_call.guest_contact_id else None
        )
        # Pull active reservation for this guest (door codes etc.)
        active_reservation = None
        if guest_contact and guest_contact.reservation_id:
            active_reservation = db.query(Reservation).filter(
                Reservation.id == guest_contact.reservation_id
            ).first()

        # Phase 2: Try name-based lookup if phone lookup failed
        found_by_name = None
        found_by_confirmation = None
        if not guest_contact and not voice_call.reservation_id and guest_message:
            # Extract potential name from first message (e.g., "Hi, I'm John" or "This is John")
            msg_lower = guest_message.lower()
            name_keywords = ["i'm", "i am", "this is", "my name is", "call me"]
            extracted_name = None
            for keyword in name_keywords:
                if keyword in msg_lower:
                    # Simple extraction: grab the word(s) after the keyword
                    parts = msg_lower.split(keyword)
                    if len(parts) > 1:
                        # Get next 1-2 words after keyword
                        remaining = parts[1].strip().rstrip('.,!?')
                        words = remaining.split()[:2]
                        if words:
                            extracted_name = " ".join(words)
                            break

            # Try name lookup
            if extracted_name and len(extracted_name) >= 2:
                found_by_name = _find_guest_by_name(tenant.id, extracted_name, db)
                if found_by_name:
                    log.info(f"[VOICE] Found guest by name: {found_by_name['name']} (source={found_by_name['source']})")
                    if found_by_name['source'] == 'guest_contact':
                        guest_contact = db.query(GuestContact).filter(
                            GuestContact.id == found_by_name['guest_contact_id']
                        ).first()
                    elif found_by_name['source'] == 'reservation':
                        voice_call.reservation_id = found_by_name['reservation_id']

        # Phase 3: Try confirmation-based lookup if name lookup also failed
        if not guest_contact and not voice_call.reservation_id and guest_message and not found_by_name:
            # Extract potential confirmation code (4-16 char alphanumeric token)
            import re
            tokens = re.findall(r'\b[A-Z0-9]{4,16}\b', guest_message.upper())
            for token in tokens:
                found_by_confirmation = _find_guest_by_confirmation(tenant.id, token, db)
                if found_by_confirmation:
                    log.info(f"[VOICE] Found guest by confirmation code: {found_by_confirmation['name']}")
                    voice_call.reservation_id = found_by_confirmation['reservation_id']
                    break

        # Persist any updates to voice_call from name/confirmation lookup
        if found_by_name or found_by_confirmation:
            db.commit()

        tenant_config_dict = {
            "property_type":        cfg.property_type        if cfg else "property",
            "property_city":        cfg.property_city        if cfg else "",
            "check_in_time":        cfg.check_in_time        if cfg else "15:00",
            "check_out_time":       cfg.check_out_time       if cfg else "11:00",
            "max_guests":           cfg.max_guests           if cfg else "",
            "house_rules":          cfg.house_rules          if cfg else "",
            "pet_policy":           cfg.pet_policy           if cfg else "",
            "parking_policy":       cfg.parking_policy       if cfg else "",
            "smoking_policy":       cfg.smoking_policy       if cfg else "",
            "quiet_hours":          cfg.quiet_hours          if cfg else "",
            "amenities":            cfg.amenities            if cfg else "",
            "food_menu":            cfg.food_menu            if cfg else "",
            "nearby_restaurants":   cfg.nearby_restaurants   if cfg else "",
            "faq":                  cfg.faq                  if cfg else "",
            "custom_instructions":  cfg.custom_instructions  if cfg else "",
            "early_checkin_policy": cfg.early_checkin_policy if cfg else "",
            "late_checkout_policy": cfg.late_checkout_policy if cfg else "",
            "refund_policy":        cfg.refund_policy        if cfg else "",
        }

        # Enrich with GuestContact room/property info
        if guest_contact:
            if guest_contact.room_identifier:
                tenant_config_dict["guest_room"] = guest_contact.room_identifier
            if guest_contact.property_name:
                tenant_config_dict["guest_property"] = guest_contact.property_name

        # Inject reservation details if available
        if active_reservation:
            res = active_reservation
            tenant_config_dict["guest_reservation"] = (
                f"Confirmation: {res.confirmation_code}, "
                f"Check-in: {res.checkin.strftime('%b %d') if res.checkin else 'N/A'}, "
                f"Check-out: {res.checkout.strftime('%b %d') if res.checkout else 'N/A'}"
            )
            if res.unit_identifier:
                tenant_config_dict["guest_room"] = res.unit_identifier
            if res.listing_name:
                tenant_config_dict["guest_property"] = res.listing_name
        # Fallback: if no GuestContact but we have voice_call.reservation_id, fetch that reservation
        elif voice_call.reservation_id:
            res = db.query(Reservation).filter(Reservation.id == voice_call.reservation_id).first()
            if res:
                tenant_config_dict["guest_reservation"] = (
                    f"Confirmation: {res.confirmation_code}, "
                    f"Check-in: {res.checkin.strftime('%b %d') if res.checkin else 'N/A'}, "
                    f"Check-out: {res.checkout.strftime('%b %d') if res.checkout else 'N/A'}"
                )
                if res.unit_identifier:
                    tenant_config_dict["guest_room"] = res.unit_identifier
                if res.listing_name:
                    tenant_config_dict["guest_property"] = res.listing_name

        guest_name = guest_contact.guest_name if guest_contact else (found_by_name.get('name') if found_by_name else (found_by_confirmation.get('name') if found_by_confirmation else None))

        # ── Step 2b: Add conversation history and guest email ─────────────────
        call_history = _get_guest_call_history(tenant.id, voice_call.guest_phone_number, db)
        if call_history:
            tenant_config_dict["guest_call_history"] = call_history

        # Try to capture guest email from GuestContact or Reservation
        if guest_contact and guest_contact.guest_email:
            voice_call.guest_email = guest_contact.guest_email
        elif active_reservation and hasattr(active_reservation, 'guest_email') and active_reservation.guest_email:
            voice_call.guest_email = active_reservation.guest_email
        elif found_by_name and found_by_name.get('phone'):
            # If found by name, try to get email from reservation
            if found_by_name.get('reservation_id'):
                res = db.query(Reservation).filter(Reservation.id == found_by_name['reservation_id']).first()
                if res and hasattr(res, 'guest_email') and res.guest_email:
                    voice_call.guest_email = res.guest_email
        elif found_by_confirmation and found_by_confirmation.get('reservation_id'):
            # If found by confirmation, try to get email from reservation
            res = db.query(Reservation).filter(Reservation.id == found_by_confirmation['reservation_id']).first()
            if res and hasattr(res, 'guest_email') and res.guest_email:
                voice_call.guest_email = res.guest_email

        # ── Step 2c: Detect callback requests ────────────────────────────────
        callback_keywords = ["callback", "call me", "call back", "ring me", "call later", "later time"]
        if any(kw in guest_message.lower() for kw in callback_keywords):
            voice_call.callback_requested = True
            tenant_config_dict["callback_requested"] = True

            # Try to extract time from message (e.g., "30 minutes", "in an hour", "at 5pm")
            import re
            msg_lower = guest_message.lower()
            callback_at = datetime.now(timezone.utc)

            # Check for "in X minutes/hours"
            match = re.search(r"in (\d+)\s*(minute|hour)s?", msg_lower)
            if match:
                amount = int(match.group(1))
                unit = match.group(2)
                if unit == "hour":
                    callback_at = callback_at + timedelta(hours=amount)
                else:
                    callback_at = callback_at + timedelta(minutes=amount)
                voice_call.callback_at = callback_at
                log.info(f"[VOICE] Guest requested callback in {amount} {unit}(s)")
            # Check for "tomorrow", "in the morning", etc.
            elif "tomorrow" in msg_lower or "next" in msg_lower:
                callback_at = callback_at + timedelta(days=1)
                callback_at = callback_at.replace(hour=10, minute=0, second=0)  # Default to 10am
                voice_call.callback_at = callback_at
                log.info(f"[VOICE] Guest requested callback tomorrow")
            else:
                # Default: 1 hour from now
                voice_call.callback_at = callback_at + timedelta(hours=1)
                log.info(f"[VOICE] Guest requested callback (default 1 hour)")

        # ── Step 3: Generate response ─────────────────────────────────────────
        # Estimate OpenAI cost before making the call (gpt-4o-mini with context)
        # Rough estimate: ~400 tokens input + 150 tokens output per call
        estimated_input_tokens = 400
        estimated_output_tokens = 150
        openai_cost = estimate_cost(
            "openai", "generate_response",
            input_tokens=estimated_input_tokens,
            output_tokens=estimated_output_tokens,
            model="gpt-4o-mini"
        )
        daily_cost_check = check_rate_limit(db, voice_call.tenant_id, "daily_cost", cost_increment=openai_cost)
        if not daily_cost_check["allowed"]:
            log.warning(f"[VOICE] Daily cost limit exceeded for {voice_call.tenant_id}: {daily_cost_check['reason']}")
            from twilio.twiml.voice_response import VoiceResponse
            r = VoiceResponse()
            r.say("Sorry, this service is temporarily unavailable. Your host has been notified.")
            r.hangup()
            return Response(str(r), media_type="application/xml")

        ai_text, send_action, unanswered_question = await VoiceAIService.generate_response(
            guest_message,
            tenant_config_dict,
            voice_call.guest_messages,
            guest_name=guest_name,
            guest_language=voice_call.guest_language,
        )
        log.info(f"[VOICE] Response: '{ai_text[:80]}' | send={send_action} | gap={unanswered_question}")

        log_api_usage(
            db, voice_call.tenant_id, "openai", "generate_response",
            cost_usd=openai_cost,
            input_tokens=estimated_input_tokens,
            output_tokens=estimated_output_tokens,
            call_id=call_id,
            status="success"
        )
        increment_rate_limit(db, voice_call.tenant_id, "daily_cost", openai_cost)

        voice_call.ai_responses = list(voice_call.ai_responses or [])
        voice_call.ai_responses.append({
            "text": ai_text,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "sent_message": send_action,
        })

        # ── Step 4: Dispatch send_action (SMS or WhatsApp to guest live) ─────
        if send_action and cfg:
            channel = getattr(cfg, "voice_send_channel", "disabled")
            if channel in ("sms", "whatsapp"):
                _send_voice_message(cfg, voice_call.guest_phone_number, send_action["content"], channel)

        # ── Step 4b: Knowledge gap — AI didn't know the answer ───────────────
        if unanswered_question:
            _handle_knowledge_gap(
                db=db,
                tenant=tenant,
                cfg=cfg,
                voice_call=voice_call,
                question=unanswered_question,
            )

        # ── Step 5: Escalate low-confidence turns to host ────────────────────
        notify_phone = cfg.sms_notify_number or getattr(cfg, "host_notify_phone", None) if cfg else None
        if confidence < 0.6 and notify_phone:
            log.warning(f"[VOICE] Low confidence ({confidence:.2f}), alerting host")
            alert = (
                f"⚠️ Voice call alert\n"
                f"Guest: {voice_call.guest_phone_number}\n"
                f"Said: \"{guest_message}\"\n"
                f"AI confidence: {int(confidence*100)}% — may need follow-up."
            )
            _send_voice_message(cfg, notify_phone, alert, "sms")

        # ── Step 6: Synthesize AI reply to audio ─────────────────────────────
        voice_id = cfg.voice_elevenlabs_voice_id if cfg else None

        # Estimate TTS cost before synthesis
        tts_provider = (VoiceAIService.TTS_PROVIDER or "google").lower()
        if tts_provider == "google":
            tts_cost = len(ai_text) / 1_000_000 * 16.0
        else:
            tts_cost = estimate_cost("elevenlabs", "synthesize", characters=len(ai_text))

        daily_cost_check = check_rate_limit(db, voice_call.tenant_id, "daily_cost", cost_increment=tts_cost)
        if not daily_cost_check["allowed"]:
            log.warning(f"[VOICE] Daily cost limit exceeded for {voice_call.tenant_id}: {daily_cost_check['reason']}")
            from twilio.twiml.voice_response import VoiceResponse
            r = VoiceResponse()
            r.say("Sorry, this service is temporarily unavailable. Your host has been notified.")
            r.hangup()
            return Response(str(r), media_type="application/xml")

        audio_bytes, audio_url = await VoiceAIService.synthesize_speech(ai_text, voice_id=voice_id)

        # Log TTS cost under the correct provider
        _log_tts_usage(db, voice_call.tenant_id, ai_text, audio_bytes, call_id=call_id)
        increment_rate_limit(db, voice_call.tenant_id, "daily_cost", tts_cost)

        # Update running confidence average
        prev_avg = voice_call.confidence_avg or confidence
        n_turns  = len(voice_call.guest_messages)
        voice_call.confidence_avg = ((prev_avg * (n_turns - 1)) + confidence) / n_turns
        if audio_url:
            voice_call.recording_url = audio_url
        db.commit()

        # ── Step 7: Return TwiML ──────────────────────────────────────────────
        from twilio.twiml.voice_response import VoiceResponse
        r = VoiceResponse()
        if audio_url:
            r.play(audio_url)
        else:
            r.say(ai_text)
        r.record(
            action=f"/api/calls/process-speech?call_id={call_id}",
            method="POST",
            max_length=60,
            play_beep=True,
        )
        r.hangup()
        return Response(str(r), media_type="application/xml")

    except Exception as e:
        # CRITICAL severity fix #3: Don't expose stack traces in production logs
        log.error(f"[VOICE] Error in process_speech: {type(e).__name__}")
        if _ENVIRONMENT == "development":
            log.debug(f"[VOICE] Full error: {traceback.format_exc()}")
        return _voice_twiml_error("Sorry, something went wrong. Goodbye.")


@app.post("/api/calls/hangup")
async def handle_hangup(request: Request, db: Session = Depends(get_db)):
    """Log call end — duration, interleaved transcript, sentiment, post-call summary."""
    try:
        form_data   = await request.form()
        call_sid    = form_data.get("CallSid", "")
        call_status = form_data.get("CallStatus", "completed")
        recording_url = form_data.get("RecordingUrl", "")

        log.info(f"[VOICE] Hangup call_sid={call_sid}, status={call_status}")

        voice_call = db.query(VoiceCall).filter(VoiceCall.twilio_call_id == call_sid).first()
        if not voice_call:
            return {"status": "not_found"}
        cfg = voice_call.tenant.config if voice_call.tenant else None
        if not cfg or not _validate_twilio_signature(request, dict(form_data), cfg, channel="voice"):
            log.warning("[VOICE] Rejected hangup webhook for call_sid=%s", call_sid)
            return JSONResponse({"status": "forbidden"}, status_code=403)

        voice_call.status   = call_status
        voice_call.ended_at = datetime.now(timezone.utc)
        if recording_url:
            voice_call.recording_url = recording_url

        # Duration
        if voice_call.started_at and voice_call.ended_at:
            voice_call.duration_seconds = int(
                (voice_call.ended_at - voice_call.started_at).total_seconds()
            )

        # Interleaved transcript (by timestamp, guest first per turn)
        turns = []
        for msg in (voice_call.guest_messages or []):
            turns.append(("Guest", msg.get("timestamp", ""), msg.get("text", "")))
        for resp in (voice_call.ai_responses or []):
            turns.append(("AI", resp.get("timestamp", ""), resp.get("text", "")))
        turns.sort(key=lambda t: t[1])
        voice_call.full_transcript = "\n".join(f"{role}: {text}" for role, _, text in turns)

        db.commit()

        # Sentiment analysis (async, non-blocking for Twilio)
        if voice_call.full_transcript:
            try:
                sentiment = await VoiceAIService.analyze_sentiment(voice_call.full_transcript)
                voice_call.sentiment = sentiment
                db.commit()
            except Exception:
                pass

        # Post-call summary to host
        cfg = voice_call.tenant.config if voice_call.tenant else None
        if cfg and getattr(cfg, "voice_post_call_summary", False):
            notify_phone = cfg.sms_notify_number or cfg.host_notify_phone
            if notify_phone:
                dur = f"{voice_call.duration_seconds}s" if voice_call.duration_seconds else "unknown"
                sentiment_emoji = {"positive": "😊", "negative": "😟", "neutral": "😐"}.get(
                    voice_call.sentiment or "neutral", "😐"
                )
                summary = (
                    f"📞 Voice Call Summary\n"
                    f"Guest: {voice_call.guest_phone_number}\n"
                    f"Duration: {dur} | Sentiment: {sentiment_emoji} {(voice_call.sentiment or 'neutral').title()}\n"
                    f"Confidence: {int((voice_call.confidence_avg or 0) * 100)}%\n\n"
                    f"Transcript:\n{voice_call.full_transcript[:800]}"
                )
                # Use voice_send_channel if not disabled, else SMS
                send_channel = cfg.voice_send_channel if cfg.voice_send_channel != "disabled" else "sms"
                _send_voice_message(cfg, notify_phone, summary, send_channel)

        # Post-call satisfaction survey (send SMS with rating request)
        if cfg and voice_call.call_type == "incoming":
            survey_msg = (
                f"👋 Thanks for calling! Quick question: How was your experience? "
                f"Reply with a number 1-5 (1=Poor, 5=Excellent). "
                f"Your feedback helps us improve. 🙏"
            )
            send_channel = cfg.voice_send_channel if cfg.voice_send_channel != "disabled" else "sms"
            _send_voice_message(cfg, voice_call.guest_phone_number, survey_msg, send_channel)

        # Activity log
        db.add(ActivityLog(
            tenant_id=voice_call.tenant_id,
            event_type="voice_call_completed",
            message=(
                f"Voice call with {voice_call.guest_phone_number}: "
                f"{voice_call.duration_seconds or '?'}s, "
                f"status={call_status}, sentiment={voice_call.sentiment or 'n/a'}"
            ),
            created_at=datetime.now(timezone.utc),
        ))
        db.commit()

        return {"status": "logged"}

    except Exception as e:
        # CRITICAL severity fix #3: Don't expose stack traces in production logs
        log.error(f"[VOICE] Error in handle_hangup: {type(e).__name__}")
        if _ENVIRONMENT == "development":
            log.debug(f"[VOICE] Full error: {traceback.format_exc()}")
        return {"error": "An error occurred during hangup processing"}


@app.post("/api/calls/rating")
def receive_rating(
    request: Request,
    phone: str = None,
    rating: int = None,
    call_id: str = None,
    db: Session = Depends(get_db),
):
    """
    Receive satisfaction rating from guest (typically via webhook from SMS handler).
    Updates the most recent VoiceCall record with the rating.
    """
    try:
        _require_internal_webhook_secret(request, env_name="VOICE_RATING_WEBHOOK_SECRET")
        if not rating or rating < 1 or rating > 5:
            return {"status": "invalid"}
        if not phone and not call_id:
            return {"status": "invalid"}

        recent_call = None
        if call_id:
            recent_call = db.query(VoiceCall).filter(VoiceCall.id == call_id).first()
        if not recent_call and phone:
            recent_call = (
                db.query(VoiceCall)
                .filter(VoiceCall.guest_phone_number == phone)
                .order_by(VoiceCall.created_at.desc())
                .first()
            )

        if recent_call:
            recent_call.guest_rating = rating
            db.commit()
            log.info(
                "[VOICE] Rating %s/5 recorded for call=%s phone=%s",
                rating,
                recent_call.id,
                phone or recent_call.guest_phone_number,
            )

            # If rating is 1-star, escalate to host
            if rating == 1:
                cfg = recent_call.tenant.config if recent_call.tenant else None
                if cfg and cfg.sms_notify_number:
                    alert = (
                        f"⚠️ Low satisfaction rating on voice call\n"
                        f"Guest: {phone}\n"
                        f"Rating: 1/5 ⭐\n"
                        f"Consider following up with this guest."
                    )
                    _send_voice_message(cfg, cfg.sms_notify_number, alert, "sms")

            return {"status": "recorded", "rating": rating}
        return {"status": "call_not_found"}

    except HTTPException:
        raise
    except Exception as e:
        log.error(f"[VOICE] Error processing rating: {e}")
        return {"status": "error"}


@app.post("/api/calls/send-voice")
async def send_outbound_voice(
    request: Request,
    guest_phone: str,
    message: str,
    tenant_id: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """Initiate outbound call with message synthesis."""
    try:
        validate_csrf_header(request)
        actor_tenant = _require_authenticated_tenant_actor(request, db, action="send outbound voice calls")
        resolved_tenant_id = actor_tenant.id
        if tenant_id and tenant_id != actor_tenant.id:
            _require_admin(request, db)
            resolved_tenant_id = tenant_id
        tenant = db.query(Tenant).filter(Tenant.id == resolved_tenant_id).first()
        if not tenant or not tenant.voice_enabled:
            return JSONResponse({"error": "Voice not enabled"}, status_code=400)

        # Synthesize message
        audio_bytes, s3_url = await VoiceAIService.synthesize_speech(message)
        if not s3_url:
            return JSONResponse({"error": "Could not synthesize speech"}, status_code=500)

        # Create Twilio call
        from twilio.rest import Client as TwilioClient
        voice_cfg = tenant.config
        voice_sid = (voice_cfg.voice_twilio_account_sid if voice_cfg else None) or os.getenv("TWILIO_ACCOUNT_SID")
        voice_auth_token = decrypt(voice_cfg.voice_twilio_auth_token_enc or "") if voice_cfg and voice_cfg.voice_twilio_auth_token_enc else os.getenv("TWILIO_AUTH_TOKEN")
        from_number = (
            voice_cfg.voice_twilio_from_number if voice_cfg else None
        ) or (
            voice_cfg.twilio_from_number if voice_cfg else None
        ) or os.getenv("TWILIO_PHONE_NUMBER")
        if not voice_sid or not voice_auth_token or not from_number:
            return JSONResponse({"error": "Voice Twilio is not configured"}, status_code=400)
        twilio_client = TwilioClient(
            voice_sid,
            voice_auth_token,
        )

        app_url = APP_BASE_URL or os.getenv("APP_URL", "http://localhost:8000")
        call = twilio_client.calls.create(
            from_=from_number,
            to=guest_phone,
            url=f"{app_url}/api/calls/outbound-twiml?s3_url={s3_url}"
        )

        # Log in database
        voice_call = VoiceCall(
            id=str(uuid4()),
            tenant_id=resolved_tenant_id,
            twilio_call_id=call.sid,
            twilio_phone_number=from_number,
            guest_phone_number=guest_phone,
            call_type="outbound",
            status="ringing",
            recording_url=s3_url,
            created_at=datetime.now(timezone.utc)
        )
        db.add(voice_call)
        db.commit()

        log.info(f"[VOICE] Outbound call initiated: call_id={call.sid}, to={guest_phone}")

        return {"call_id": call.sid, "status": "initiated"}

    except HTTPException:
        raise
    except Exception as e:
        # CRITICAL severity fix #3: Don't expose stack traces in production logs
        log.error(f"[VOICE] Error in send_outbound_voice: {type(e).__name__}")
        if _ENVIRONMENT == "development":
            log.debug(f"[VOICE] Full error: {traceback.format_exc()}")
        return JSONResponse({"error": "Failed to initiate outbound call"}, status_code=500)


@app.get("/api/calls/outbound-twiml")
async def outbound_twiml(s3_url: str):
    """TwiML for outbound call - play message."""
    try:
        from twilio.twiml.voice_response import VoiceResponse
        response = VoiceResponse()
        response.play(s3_url)
        response.say("Press 1 to repeat, or hangup.")
        response.hangup()
        return Response(str(response), media_type="application/xml")
    except Exception as e:
        log.error(f"[VOICE] Error in outbound_twiml: {e}")
        from twilio.twiml.voice_response import VoiceResponse
        response = VoiceResponse()
        response.say("Sorry, something went wrong.")
        response.hangup()
        return Response(str(response), media_type="application/xml")


# ---------------------------------------------------------------------------
# Admin SaaS Operations Dashboard
# ---------------------------------------------------------------------------

@app.get("/admin/voice-analytics", response_class=HTMLResponse)
def admin_voice_analytics_view(request: Request, days: int = 30, db: Session = Depends(get_db)):
    """Phase 5: Voice Analytics Dashboard"""
    try:
        tenant = _require_admin(request, db)
    except HTTPException:
        raise HTTPException(status_code=401, detail="Admin access required")
    
    from web.voice_analytics import get_voice_analytics
    # Use tenant.id for analytics
    analytics_data = get_voice_analytics(db, tenant.id, days=days)
    
    return templates.TemplateResponse(
        "admin_voice_analytics.html",
        {
            "request": request,
            "admin": tenant,
            "tenant": tenant,
            "analytics_data": analytics_data,
            "active_page": "voice_analytics"
        }
    )

@app.get("/admin/voice-routing", response_class=HTMLResponse)
def admin_voice_routing_view(request: Request, db: Session = Depends(get_db)):
    """Phase 7: Smart Routing UI"""
    try:
        tenant = _require_admin(request, db)
    except HTTPException:
        raise HTTPException(status_code=401, detail="Admin access required")
    
    from web.models import VoiceRoutingConfig, RoutingRule
    config = db.query(VoiceRoutingConfig).filter(VoiceRoutingConfig.tenant_id == tenant.id).first()
    rules = db.query(RoutingRule).filter(RoutingRule.tenant_id == tenant.id).order_by(RoutingRule.priority.asc()).all()
    
    return templates.TemplateResponse(
        "admin_voice_routing.html",
        {
            "request": request,
            "admin": tenant,
            "tenant": tenant,
            "routing_config": config,
            "routing_rules": rules,
            "active_page": "voice_routing"
        }
    )

@app.get("/admin/saas-dashboard", response_class=HTMLResponse)
def admin_saas_dashboard(request: Request, db: Session = Depends(get_db)):
    """SaaS operations dashboard: costs, rate limits, feature flags, API logs."""
    try:
        tenant = _require_admin(request, db)
    except HTTPException:
        raise HTTPException(status_code=401, detail="Admin access required")

    from sqlalchemy import func, desc, text
    from datetime import timedelta as td

    def _tenant_label(t: Tenant | None, fallback: str) -> str:
        if not t:
            return fallback[:8]
        full_name = " ".join(part for part in [t.first_name, t.last_name] if part).strip()
        return full_name or t.email or fallback[:8]

    # Get cost summary for last 30 days
    cutoff = datetime.now(timezone.utc) - td(days=30)
    usage_logs = db.query(APIUsageLog).filter(
        APIUsageLog.created_at >= cutoff,
        APIUsageLog.status == "success"
    ).all()

    total_cost_30d = sum(log.cost_usd for log in usage_logs)
    total_calls_30d = len(usage_logs)

    # Costs by service
    costs_by_service = {}
    for log in usage_logs:
        if log.service not in costs_by_service:
            costs_by_service[log.service] = {"count": 0, "cost": 0.0}
        costs_by_service[log.service]["count"] += 1
        costs_by_service[log.service]["cost"] += log.cost_usd

    # Top tenants by cost today
    start_of_day = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    daily_costs = db.query(
        APIUsageLog.tenant_id,
        func.sum(APIUsageLog.cost_usd).label('total_cost'),
        func.count(APIUsageLog.id).label('call_count')
    ).filter(
        APIUsageLog.created_at >= start_of_day
    ).group_by(APIUsageLog.tenant_id).order_by(desc('total_cost')).limit(10).all()

    top_tenants_today = []
    for tenant_id, cost, calls in daily_costs:
        t = db.query(Tenant).filter_by(id=tenant_id).first()
        limit_cfg = db.query(TenantRateLimit).filter_by(tenant_id=tenant_id).first()
        limit_usd = (limit_cfg.max_daily_cost_usd if limit_cfg else 50)
        top_tenants_today.append({
            "name": _tenant_label(t, tenant_id),
            "cost_today": cost or 0,
            "calls_today": calls,
            "daily_limit": limit_usd,
        })

    # Rate limit status — use SQL aggregation instead of loading all rows
    from sqlalchemy import func as _func
    rate_limits = []
    all_limits = db.query(TenantRateLimit).all()
    start_of_day = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    current_hour = datetime.now(timezone.utc).hour

    # Batch-fetch daily costs via SQL SUM per tenant
    daily_cost_rows = db.query(
        APIUsageLog.tenant_id,
        _func.sum(APIUsageLog.cost_usd).label("total")
    ).filter(
        APIUsageLog.created_at >= start_of_day
    ).group_by(APIUsageLog.tenant_id).all()
    daily_cost_map = {row.tenant_id: float(row.total or 0) for row in daily_cost_rows}

    for limit in all_limits:
        t = db.query(Tenant).filter_by(id=limit.tenant_id).first()

        # Voice calls this hour
        hour_key = f"{limit.tenant_id}:voice_calls:{current_hour}"
        vc_counter = db.query(RateLimitCounter).filter_by(counter_id=hour_key).first()
        calls_current = vc_counter.count if vc_counter else 0

        # External API calls this hour (from RateLimitCounter)
        api_hour_key = f"{limit.tenant_id}:external_api:{current_hour}"
        api_counter = db.query(RateLimitCounter).filter_by(counter_id=api_hour_key).first()
        api_calls_current = api_counter.count if api_counter else 0

        daily_cost = daily_cost_map.get(limit.tenant_id, 0.0)

        rate_limits.append({
            "tenant_name": _tenant_label(t, limit.tenant_id),
            "calls_per_hour": limit.voice_calls_per_hour,
            "calls_current": calls_current,
            "calls_usage_pct": min(100, (calls_current / max(limit.voice_calls_per_hour, 1)) * 100),
            "api_calls_per_hour": limit.external_api_calls_per_hour,
            "api_calls_current": api_calls_current,
            "api_usage_pct": min(100, (api_calls_current / max(limit.external_api_calls_per_hour, 1)) * 100),
            "daily_cost_current": daily_cost,
            "max_daily_cost": limit.max_daily_cost_usd,
        })

    # Feature flags
    feature_flags = []
    flags = db.query(FeatureFlag).all()
    for flag in flags:
        feature_flags.append({
            "name": flag.flag_name,
            "enabled": flag.enabled,
            "rollout_percentage": flag.rollout_percentage,
            "description": flag.description,
        })

    # Recent API logs
    recent_logs = db.query(APIUsageLog).order_by(desc(APIUsageLog.created_at)).limit(50).all()

    return templates.TemplateResponse(
        "admin_saas_dashboard.html",
        {
            "request": request,
            "admin": tenant,
            "total_cost_30d": total_cost_30d,
            "total_calls_30d": total_calls_30d,
            "costs_by_service": costs_by_service,
            "top_tenants_today": top_tenants_today,
            "rate_limits": rate_limits,
            "feature_flags": feature_flags,
            "recent_logs": recent_logs,
        }
    )


@app.post("/admin/saas-dashboard/seed-flags")
def admin_seed_feature_flags(request: Request, db: Session = Depends(get_db)):
    """Seed default feature flags into the database."""
    _require_admin(request, db)
    defaults = [
        ("voice_ai_enabled", True, 100, "Enable Voice AI phone answering for tenants"),
        ("upsell_engine", False, 0, "AI-powered upsell offers sent to guests mid-stay"),
        ("satisfaction_pulse", False, 0, "Post-checkout guest satisfaction survey"),
        ("bulk_csv_import", True, 100, "Allow hosts to bulk-import reservations via CSV"),
        ("auto_send_drafts", False, 0, "Auto-send AI drafts without host approval"),
        ("multilingual_ai", True, 100, "Auto-detect and reply in guest language"),
    ]
    created = 0
    for flag_name, enabled, rollout, description in defaults:
        existing = db.query(FeatureFlag).filter_by(flag_name=flag_name).first()
        if not existing:
            db.add(FeatureFlag(
                flag_name=flag_name,
                enabled=enabled,
                rollout_percentage=rollout,
                description=description,
            ))
            created += 1
    db.commit()
    from fastapi.responses import RedirectResponse
    return RedirectResponse("/admin/saas-dashboard#flags", status_code=303)


# ---------------------------------------------------------------------------
# Escalation Rules API — manage per-property escalation rules
# ---------------------------------------------------------------------------

@app.get("/api/properties/{property_id}/escalation-rules")
async def get_escalation_rules(property_id: str, request: Request, db: Session = Depends(get_db)):
    """Get escalation rules for a specific property."""
    try:
        tenant_id = get_current_tenant_id(request)
    except HTTPException:
        raise HTTPException(status_code=401)

    from web.models import EscalationRule

    rules = db.query(EscalationRule).filter(
        EscalationRule.property_id == property_id,
        EscalationRule.tenant_id == tenant_id,
    ).order_by(EscalationRule.priority.desc(), EscalationRule.created_at.desc()).all()

    return JSONResponse({
        "status": 200,
        "rules": [
            {
                "id": r.id,
                "name": r.name,
                "description": r.description,
                "condition_type": r.condition_type,
                "action": r.action,
                "escalation_priority": r.escalation_priority,
                "is_active": r.is_active,
                "priority": r.priority,
            }
            for r in rules
        ]
    })


@app.post("/api/properties/{property_id}/escalation-rules")
async def create_escalation_rule(property_id: str, request: Request, db: Session = Depends(get_db)):
    """Create a new escalation rule for a property."""
    try:
        tenant_id = get_current_tenant_id(request)
    except HTTPException:
        raise HTTPException(status_code=401)

    from web.models import EscalationRule

    # Verify property ownership
    property_obj = db.query(Property).filter(
        Property.id == property_id,
        Property.tenant_id == tenant_id
    ).first()

    if not property_obj:
        return JSONResponse({"error": "Property not found"}, status_code=404)

    try:
        data = await request.json()

        rule = EscalationRule(
            property_id=property_id,
            tenant_id=tenant_id,
            name=data.get("name", "New Rule"),
            description=data.get("description"),
            priority=data.get("priority", 100),
            is_active=data.get("is_active", True),
            condition_type=data.get("condition_type", "confidence_below"),
            confidence_threshold=data.get("confidence_threshold"),
            keywords=data.get("keywords"),
            min_repeat_count=data.get("min_repeat_count"),
            time_window_minutes=data.get("time_window_minutes"),
            channels=data.get("channels"),
            action=data.get("action", "escalate"),
            escalation_priority=data.get("escalation_priority", "high"),
            assign_to_team_member=data.get("assign_to_team_member"),
        )

        db.add(rule)
        db.commit()
        db.refresh(rule)

        return JSONResponse({
            "status": 201,
            "rule": {
                "id": rule.id,
                "name": rule.name,
                "condition_type": rule.condition_type,
                "action": rule.action,
            }
        })
    except Exception as e:
        log.error(f"Error creating escalation rule: {str(e)}")
        return JSONResponse({"error": str(e)}, status_code=400)


@app.post("/api/escalation-rules/{rule_id}")
async def update_escalation_rule(rule_id: str, request: Request, db: Session = Depends(get_db)):
    """Update an escalation rule."""
    try:
        tenant_id = get_current_tenant_id(request)
    except HTTPException:
        raise HTTPException(status_code=401)

    from web.models import EscalationRule

    rule = db.query(EscalationRule).filter(
        EscalationRule.id == rule_id,
        EscalationRule.tenant_id == tenant_id
    ).first()

    if not rule:
        return JSONResponse({"error": "Rule not found"}, status_code=404)

    try:
        data = await request.json()

        # Update fields
        if "name" in data:
            rule.name = data["name"]
        if "description" in data:
            rule.description = data["description"]
        if "priority" in data:
            rule.priority = data["priority"]
        if "is_active" in data:
            rule.is_active = data["is_active"]
        if "condition_type" in data:
            rule.condition_type = data["condition_type"]
        if "confidence_threshold" in data:
            rule.confidence_threshold = data["confidence_threshold"]
        if "keywords" in data:
            rule.keywords = data["keywords"]
        if "action" in data:
            rule.action = data["action"]
        if "escalation_priority" in data:
            rule.escalation_priority = data["escalation_priority"]

        db.commit()
        db.refresh(rule)

        return JSONResponse({
            "status": 200,
            "rule": {"id": rule.id, "name": rule.name}
        })
    except Exception as e:
        log.error(f"Error updating escalation rule: {str(e)}")
        return JSONResponse({"error": str(e)}, status_code=400)


@app.delete("/api/escalation-rules/{rule_id}")
async def delete_escalation_rule(rule_id: str, request: Request, db: Session = Depends(get_db)):
    """Delete an escalation rule."""
    try:
        tenant_id = get_current_tenant_id(request)
    except HTTPException:
        raise HTTPException(status_code=401)

    from web.models import EscalationRule

    rule = db.query(EscalationRule).filter(
        EscalationRule.id == rule_id,
        EscalationRule.tenant_id == tenant_id
    ).first()

    if not rule:
        return JSONResponse({"error": "Rule not found"}, status_code=404)

    try:
        db.delete(rule)
        db.commit()
        return JSONResponse({"status": 200})
    except Exception as e:
        log.error(f"Error deleting escalation rule: {str(e)}")
        return JSONResponse({"error": str(e)}, status_code=400)


# ---------------------------------------------------------------------------
# Batch Operations — handle multiple escalated messages/alerts
# ---------------------------------------------------------------------------

@app.post("/api/batch/escalated-messages/resolve")
async def batch_resolve_escalations(request: Request, db: Session = Depends(get_db)):
    """Resolve multiple escalated messages at once."""
    try:
        tenant_id = get_current_tenant_id(request)
    except HTTPException:
        raise HTTPException(status_code=401)

    from web.models import EscalatedMessage

    try:
        data = await request.json()
        message_ids = data.get("message_ids", [])
        host_response = data.get("host_response", "")

        if not message_ids:
            return JSONResponse({"error": "No messages provided"}, status_code=400)

        # Update all messages
        updated = db.query(EscalatedMessage).filter(
            EscalatedMessage.id.in_(message_ids),
            EscalatedMessage.tenant_id == tenant_id
        ).update({
            EscalatedMessage.status: "resolved",
            EscalatedMessage.resolved_at: datetime.now(timezone.utc),
            EscalatedMessage.host_response: host_response,
        }, synchronize_session=False)

        db.commit()

        return JSONResponse({
            "status": 200,
            "resolved_count": updated,
        })
    except Exception as e:
        log.error(f"Error in batch resolve: {str(e)}")
        return JSONResponse({"error": str(e)}, status_code=400)


@app.post("/api/batch/escalated-messages/assign")
async def batch_assign_escalations(request: Request, db: Session = Depends(get_db)):
    """Assign multiple escalated messages to a team member."""
    try:
        tenant_id = get_current_tenant_id(request)
    except HTTPException:
        raise HTTPException(status_code=401)

    from web.models import EscalatedMessage

    try:
        data = await request.json()
        message_ids = data.get("message_ids", [])
        team_member_id = data.get("team_member_id")

        if not message_ids or not team_member_id:
            return JSONResponse({"error": "Missing required fields"}, status_code=400)

        # Update all messages
        updated = db.query(EscalatedMessage).filter(
            EscalatedMessage.id.in_(message_ids),
            EscalatedMessage.tenant_id == tenant_id
        ).update({
            EscalatedMessage.assigned_to: team_member_id,
            EscalatedMessage.status: "in_progress",
        }, synchronize_session=False)

        db.commit()

        return JSONResponse({
            "status": 200,
            "assigned_count": updated,
        })
    except Exception as e:
        log.error(f"Error in batch assign: {str(e)}")
        return JSONResponse({"error": str(e)}, status_code=400)


@app.post("/api/batch/escalated-messages/update-priority")
async def batch_update_priority(request: Request, db: Session = Depends(get_db)):
    """Update priority for multiple escalated messages."""
    try:
        tenant_id = get_current_tenant_id(request)
    except HTTPException:
        raise HTTPException(status_code=401)

    from web.models import EscalatedMessage

    try:
        data = await request.json()
        message_ids = data.get("message_ids", [])
        priority = data.get("priority", "medium")

        if not message_ids:
            return JSONResponse({"error": "No messages provided"}, status_code=400)

        if priority not in ["critical", "high", "medium", "low"]:
            return JSONResponse({"error": "Invalid priority"}, status_code=400)

        # Update all messages
        updated = db.query(EscalatedMessage).filter(
            EscalatedMessage.id.in_(message_ids),
            EscalatedMessage.tenant_id == tenant_id
        ).update({
            EscalatedMessage.priority: priority,
        }, synchronize_session=False)

        db.commit()

        return JSONResponse({
            "status": 200,
            "updated_count": updated,
        })
    except Exception as e:
        log.error(f"Error in batch update priority: {str(e)}")
        return JSONResponse({"error": str(e)}, status_code=400)


# ---------------------------------------------------------------------------
# Team Member Delegation — smart task assignment and workload management
# ---------------------------------------------------------------------------

@app.get("/api/team-members/available")
async def get_available_team_members(request: Request, db: Session = Depends(get_db)):
    """Get available team members with their current workload."""
    try:
        tenant_id = get_current_tenant_id(request)
    except HTTPException:
        raise HTTPException(status_code=401)

    from web.models import TeamMember, TeamMemberWorkload

    # Get active team members available for assignment
    team_members = db.query(TeamMember).filter(
        TeamMember.tenant_id == tenant_id,
        TeamMember.is_active == True,
        TeamMember.is_available_for_assignment == True
    ).all()

    members_data = []
    for tm in team_members:
        # Count current active assignments
        active_tasks = db.query(TeamMemberWorkload).filter(
            TeamMemberWorkload.team_member_id == tm.id,
            TeamMemberWorkload.status.in_(["assigned", "in_progress"])
        ).count()

        workload_percent = (active_tasks / tm.max_concurrent_tasks * 100) if tm.max_concurrent_tasks > 0 else 0

        members_data.append({
            "id": tm.id,
            "name": tm.display_name,
            "role": tm.role,
            "expertise_areas": (tm.expertise_areas or "").split(",") if tm.expertise_areas else [],
            "current_tasks": active_tasks,
            "max_tasks": tm.max_concurrent_tasks,
            "workload_percent": round(workload_percent, 1),
            "available": active_tasks < tm.max_concurrent_tasks,
        })

    return JSONResponse({
        "status": 200,
        "team_members": members_data
    })


@app.post("/api/escalated-messages/{message_id}/assign-smart")
async def smart_assign_escalation(message_id: str, request: Request, db: Session = Depends(get_db)):
    """Intelligently assign escalated message to best-fit team member based on skills and workload."""
    try:
        tenant_id = get_current_tenant_id(request)
    except HTTPException:
        raise HTTPException(status_code=401)

    from web.models import EscalatedMessage, TeamMember, TeamMemberWorkload

    # Get the escalated message
    message = db.query(EscalatedMessage).filter(
        EscalatedMessage.id == message_id,
        EscalatedMessage.tenant_id == tenant_id
    ).first()

    if not message:
        return JSONResponse({"error": "Message not found"}, status_code=404)

    try:
        # Find best matching team member
        # Priority: availability > expertise match > lowest workload
        available_members = db.query(TeamMember).filter(
            TeamMember.tenant_id == tenant_id,
            TeamMember.is_active == True,
            TeamMember.is_available_for_assignment == True
        ).all()

        if not available_members:
            return JSONResponse({"error": "No available team members"}, status_code=400)

        best_member = None
        best_score = -1

        for member in available_members:
            # Check workload
            active_tasks = db.query(TeamMemberWorkload).filter(
                TeamMemberWorkload.team_member_id == member.id,
                TeamMemberWorkload.status.in_(["assigned", "in_progress"])
            ).count()

            if active_tasks >= member.max_concurrent_tasks:
                continue  # Member at capacity

            # Score based on expertise and workload
            expertise_match = 0
            if member.expertise_areas:
                # Simple keyword matching on message reason
                expertise_list = [e.strip().lower() for e in member.expertise_areas.split(",")]
                reason_words = message.reason.lower().split("_")
                expertise_match = sum(1 for word in reason_words if any(word in exp for exp in expertise_list))

            workload_score = 1 - (active_tasks / member.max_concurrent_tasks)
            score = expertise_match * 2 + workload_score

            if score > best_score:
                best_score = score
                best_member = member

        if not best_member:
            return JSONResponse({"error": "No suitable team member found"}, status_code=400)

        # Create workload record and update message
        workload = TeamMemberWorkload(
            team_member_id=best_member.id,
            tenant_id=tenant_id,
            escalated_message_id=message_id,
            property_id=message.property_id,
            status="assigned"
        )

        message.assigned_to = best_member.id
        message.status = "in_progress"

        db.add(workload)
        db.commit()
        db.refresh(message)

        return JSONResponse({
            "status": 200,
            "assigned_to": best_member.id,
            "team_member_name": best_member.display_name,
            "message": {
                "id": message.id,
                "status": message.status,
            }
        })
    except Exception as e:
        log.error(f"Error in smart assignment: {str(e)}")
        return JSONResponse({"error": str(e)}, status_code=400)


@app.post("/api/team-member-workload/{workload_id}/mark-completed")
async def mark_workload_completed(workload_id: str, request: Request, db: Session = Depends(get_db)):
    """Mark a team member's task as completed."""
    try:
        tenant_id = get_current_tenant_id(request)
    except HTTPException:
        raise HTTPException(status_code=401)

    from web.models import TeamMemberWorkload

    workload = db.query(TeamMemberWorkload).filter(
        TeamMemberWorkload.id == workload_id,
        TeamMemberWorkload.tenant_id == tenant_id
    ).first()

    if not workload:
        return JSONResponse({"error": "Workload not found"}, status_code=404)

    try:
        data = await request.json()

        workload.status = "completed"
        workload.completed_at = datetime.now(timezone.utc)
        workload.resolution_notes = data.get("resolution_notes", "")

        # Also mark the escalated message as resolved
        if workload.escalated_message:
            workload.escalated_message.status = "resolved"
            workload.escalated_message.resolved_at = datetime.now(timezone.utc)
            workload.escalated_message.host_response = data.get("resolution_notes", "")

        db.commit()

        return JSONResponse({
            "status": 200,
            "workload_id": workload_id,
            "completed_at": workload.completed_at.isoformat()
        })
    except Exception as e:
        log.error(f"Error marking workload complete: {str(e)}")
        return JSONResponse({"error": str(e)}, status_code=400)


@app.post("/api/admin/rate-limits")
async def api_admin_set_rate_limits(request: Request, db: Session = Depends(get_db)):
    """Set rate limits for a tenant."""
    try:
        admin = _require_admin(request, db)
    except HTTPException:
        return JSONResponse({"error": "Admin access required"}, status_code=401)

    # CRITICAL severity fix #2: Rate limit admin API access
    rate_limit(f"admin-api:{admin.id}:rate-limits", max_requests=10, window_seconds=60)
    validate_csrf_header(request)

    data = await request.json()
    tenant_id = data.get("tenant_id")
    voice_calls = data.get("voice_calls_per_hour", 100)
    api_calls = data.get("external_api_calls_per_hour", 500)
    daily_cost = data.get("max_daily_cost_usd", 50)

    try:
        # CRITICAL severity fix #1: Validate tenant_id belongs to an actual tenant
        target_tenant = db.query(Tenant).filter_by(id=tenant_id).first()
        if not target_tenant:
            log.warning(f"[SECURITY] Admin {admin.email} attempted to set rate limits for non-existent tenant {tenant_id}")
            return JSONResponse({"error": "Tenant not found"}, status_code=404)

        limit = db.query(TenantRateLimit).filter_by(tenant_id=tenant_id).first()
        if not limit:
            limit = TenantRateLimit(
                tenant_id=tenant_id,
                voice_calls_per_hour=voice_calls,
                external_api_calls_per_hour=api_calls,
                max_daily_cost_usd=daily_cost
            )
            db.add(limit)
        else:
            limit.voice_calls_per_hour = voice_calls
            limit.external_api_calls_per_hour = api_calls
            limit.max_daily_cost_usd = daily_cost

        db.commit()

        # HIGH severity fix #10: Audit log this action
        _audit_log_action(
            db, admin.id, admin.email, "admin_rate_limits_changed",
            resource_id=tenant_id,
            details=f"Voice: {voice_calls}/h, API: {api_calls}/h, Daily cost: ${daily_cost}"
        )

        return JSONResponse({"message": "Rate limits updated successfully"})
    except Exception as e:
        db.rollback()
        # CRITICAL severity fix #3: Don't expose internal error details
        log.error(f"Error setting rate limits for {tenant_id}: {type(e).__name__}")
        return JSONResponse({"error": "Failed to update rate limits"}, status_code=500)


@app.post("/api/admin/feature-flags/override")
async def api_admin_feature_flag_override(request: Request, db: Session = Depends(get_db)):
    """Set per-tenant feature flag override."""
    try:
        admin = _require_admin(request, db)
    except HTTPException:
        return JSONResponse({"error": "Admin access required"}, status_code=401)

    # CRITICAL severity fix #2: Rate limit admin API access
    rate_limit(f"admin-api:{admin.id}:feature-flags", max_requests=10, window_seconds=60)
    validate_csrf_header(request)

    data = await request.json()
    flag_name = data.get("flag_name")
    tenant_id = data.get("tenant_id")
    enabled = data.get("enabled")

    try:
        # CRITICAL severity fix #1: Validate tenant exists
        target_tenant = db.query(Tenant).filter_by(id=tenant_id).first()
        if not target_tenant:
            log.warning(f"[SECURITY] Admin {admin.email} attempted to override flag for non-existent tenant {tenant_id}")
            return JSONResponse({"error": "Tenant not found"}, status_code=404)

        set_tenant_override(db, flag_name, tenant_id, enabled)

        # HIGH severity fix #10: Audit log this action
        _audit_log_action(
            db, admin.id, admin.email, "admin_feature_flag_override",
            resource_id=tenant_id,
            details=f"Flag: {flag_name}, Enabled: {enabled}"
        )

        return JSONResponse({"message": "Feature flag override set successfully"})
    except Exception as e:
        # CRITICAL severity fix #3: Don't expose internal error details
        log.error(f"Error setting flag override for {flag_name}/{tenant_id}: {type(e).__name__}")
        return JSONResponse({"error": "Failed to set feature flag override"}, status_code=500)


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
