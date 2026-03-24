# © 2024 Jestin Rajan. All rights reserved.
"""
Worker Manager — starts/stops per-tenant background threads.
Each tenant that has email + API key configured gets:
  - one email_worker thread (IMAP poll loop)
  - one calendar_worker thread (iCal poll loop, only if iCal URLs present)

A watchdog thread runs every 60 s and automatically restarts any dead worker
threads, so silent crashes are recovered without a full app restart.

Call start_all_workers() at app startup and stop_all_workers() at shutdown.
Call restart_worker(tenant_id) whenever a tenant updates their settings.
"""

import logging
import os
import socket
import threading
import time
from typing import Optional

from web import email_worker, calendar_worker, reservation_scheduler, pms_worker
from web.redis_client import get_redis

log = logging.getLogger(__name__)

# Maps tenant_id → {"email": Thread, "email_stop": Event, "cal": Thread, "cal_stop": Event,
#                    "email_cfg": cfg, "cal_cfg": cfg}
_workers: dict[str, dict] = {}
_lock = threading.Lock()

# Watchdog
_watchdog_stop = threading.Event()
_watchdog_thread: Optional[threading.Thread] = None
_WATCHDOG_INTERVAL = 60  # seconds
_LEADER_LOCK_KEY = os.getenv("WORKER_LEADER_LOCK_KEY", "hostai:workers:leader")
_LEADER_LOCK_TTL = int(os.getenv("WORKER_LEADER_LOCK_TTL", "120"))
_LEADER_REFRESH_INTERVAL = max(15, _LEADER_LOCK_TTL // 3)
_LEADER_TOKEN = f"{socket.gethostname()}:{os.getpid()}"
_leader_refresh_stop = threading.Event()
_leader_refresh_thread: Optional[threading.Thread] = None
_leader_lock_owned = False


def _embedded_workers_enabled() -> bool:
    raw = os.getenv("RUN_EMBEDDED_WORKERS", "").strip().lower()
    if not raw:
        return True
    return raw in {"1", "true", "yes", "on"}


def _acquire_leader_lock() -> bool:
    """
    Ensure only one web process owns embedded workers when Redis is available.
    """
    global _leader_lock_owned
    redis_client = get_redis()
    if redis_client is None:
        _leader_lock_owned = True
        return True
    try:
        acquired = bool(redis_client.set(_LEADER_LOCK_KEY, _LEADER_TOKEN, nx=True, ex=_LEADER_LOCK_TTL))
        if acquired:
            _leader_lock_owned = True
            return True
        owner = redis_client.get(_LEADER_LOCK_KEY)
        if owner == _LEADER_TOKEN:
            redis_client.expire(_LEADER_LOCK_KEY, _LEADER_LOCK_TTL)
            _leader_lock_owned = True
            return True
        log.info("Embedded workers already owned by %s; skipping duplicate startup", owner)
        _leader_lock_owned = False
        return False
    except Exception as exc:
        log.warning("Worker leader lock unavailable (%s); continuing without coordination", exc)
        _leader_lock_owned = True
        return True


def _leader_refresh_loop():
    redis_client = get_redis()
    if redis_client is None:
        return
    while not _leader_refresh_stop.wait(timeout=_LEADER_REFRESH_INTERVAL):
        try:
            if redis_client.get(_LEADER_LOCK_KEY) == _LEADER_TOKEN:
                redis_client.expire(_LEADER_LOCK_KEY, _LEADER_LOCK_TTL)
            else:
                log.warning("Lost embedded worker leadership; no longer refreshing lock")
                return
        except Exception as exc:
            log.warning("Worker leader lock refresh failed: %s", exc)


def _start_leader_refresh():
    global _leader_refresh_thread
    redis_client = get_redis()
    if redis_client is None:
        return
    _leader_refresh_stop.clear()
    _leader_refresh_thread = threading.Thread(
        target=_leader_refresh_loop,
        name="worker-leader-refresh",
        daemon=True,
    )
    _leader_refresh_thread.start()


def _release_leader_lock():
    global _leader_lock_owned, _leader_refresh_thread
    _leader_refresh_stop.set()
    if _leader_refresh_thread and _leader_refresh_thread.is_alive():
        _leader_refresh_thread.join(timeout=5)
    _leader_refresh_thread = None
    redis_client = get_redis()
    if redis_client is None or not _leader_lock_owned:
        _leader_lock_owned = False
        return
    try:
        if redis_client.get(_LEADER_LOCK_KEY) == _LEADER_TOKEN:
            redis_client.delete(_LEADER_LOCK_KEY)
    except Exception as exc:
        log.warning("Failed to release worker leader lock: %s", exc)
    _leader_lock_owned = False


def _start_tenant(tenant_id: str):
    """Start (or restart) background threads for one tenant."""
    # Stop existing workers if running
    _stop_tenant(tenant_id)

    email_cfg = email_worker.make_config_from_db(tenant_id)
    cal_cfg   = calendar_worker.make_config_from_db(tenant_id)

    entry: dict = {}

    # Email worker
    if email_cfg:
        email_stop = threading.Event()
        t_email = threading.Thread(
            target=email_worker.run_for_tenant,
            args=(email_cfg, email_stop),
            name=f"email-{tenant_id[:8]}",
            daemon=True,
        )
        t_email.start()
        entry["email"]      = t_email
        entry["email_stop"] = email_stop
        entry["email_cfg"]  = email_cfg  # saved for watchdog restarts

    # Calendar worker (only if iCal URLs present)
    if cal_cfg:
        cal_stop = threading.Event()
        t_cal = threading.Thread(
            target=calendar_worker.run_for_tenant,
            args=(cal_cfg, cal_stop),
            name=f"cal-{tenant_id[:8]}",
            daemon=True,
        )
        t_cal.start()
        entry["cal"]      = t_cal
        entry["cal_stop"] = cal_stop
        entry["cal_cfg"]  = cal_cfg  # saved for watchdog restarts

    # Reservation scheduler (only if tenant has imported reservations)
    res_cfg = reservation_scheduler.make_config_from_db(tenant_id)
    if res_cfg:
        sched_stop = threading.Event()
        t_sched = threading.Thread(
            target=reservation_scheduler._run_scheduler,
            args=(tenant_id, res_cfg, sched_stop),
            name=f"sched-{tenant_id[:8]}",
            daemon=True,
        )
        t_sched.start()
        entry["sched"]      = t_sched
        entry["sched_stop"] = sched_stop

    # PMS worker (only if tenant has at least one active PMS integration)
    if pms_worker.has_active_pms(tenant_id):
        pms_stop = threading.Event()
        t_pms = threading.Thread(
            target=pms_worker.run_for_tenant,
            args=(tenant_id, pms_stop),
            name=f"pms-{tenant_id[:8]}",
            daemon=True,
        )
        t_pms.start()
        entry["pms"]      = t_pms
        entry["pms_stop"] = pms_stop

    if not entry:
        log.info("[%s] No workers configured — skipping worker start", tenant_id)
        return

    with _lock:
        _workers[tenant_id] = entry

    log.info("[%s] Workers started (email=%s, calendar=%s, pms=%s)",
             tenant_id, email_cfg is not None, cal_cfg is not None, pms_worker.has_active_pms(tenant_id))


def _stop_tenant(tenant_id: str):
    """Signal stop + join threads for one tenant."""
    with _lock:
        entry = _workers.pop(tenant_id, None)
    if not entry:
        return
    for key in ("email_stop", "cal_stop", "sched_stop", "pms_stop"):
        evt = entry.get(key)
        if evt:
            evt.set()
    for key in ("email", "cal", "sched", "pms"):
        t = entry.get(key)
        if t and t.is_alive():
            t.join(timeout=5)
    log.info("[%s] Workers stopped", tenant_id)


def _watchdog_loop():
    """
    Watchdog: every _WATCHDOG_INTERVAL seconds, scan all registered workers.
    If a thread has died, restart it using the saved config.
    This recovers from silent crashes (e.g. IMAP connection hang, uncaught exception).
    """
    log.info("Worker watchdog started (interval=%ds)", _WATCHDOG_INTERVAL)
    while not _watchdog_stop.wait(timeout=_WATCHDOG_INTERVAL):
        with _lock:
            tenant_ids = list(_workers.keys())

        for tenant_id in tenant_ids:
            with _lock:
                entry = _workers.get(tenant_id, {})
            if not entry:
                continue

            email_thread = entry.get("email")
            cal_thread   = entry.get("cal")

            pms_thread   = entry.get("pms")
            email_dead = email_thread is not None and not email_thread.is_alive()
            cal_dead   = cal_thread is not None and not cal_thread.is_alive()
            pms_dead   = pms_thread is not None and not pms_thread.is_alive()

            if email_dead or cal_dead or pms_dead:
                log.warning(
                    "[%s] Dead workers detected (email=%s cal=%s pms=%s) — restarting",
                    tenant_id, email_dead, cal_dead, pms_dead,
                )
                try:
                    _start_tenant(tenant_id)
                except Exception as exc:
                    log.error("[%s] Watchdog restart failed: %s", tenant_id, exc)

        # ── Scheduled draft auto-send ────────────────────────────────
        # Check for pending drafts whose scheduled_at has passed and
        # auto-approve them (send via appropriate channel).
        _process_scheduled_drafts()

        # ── Data retention cleanup ───────────────────────────────────
        # Purge old data (activity logs, drafts, timeline events) based on
        # tenant's retention policy. Runs once per hour.
        _process_data_retention()

        # ── KPI Snapshot computation ─────────────────────────────────────
        # Compute and store KPI snapshots for all tenants (once per 24h).
        _process_kpi_snapshots()

    log.info("Worker watchdog stopped")


def _process_scheduled_drafts():
    """Find and auto-approve drafts whose scheduled_at has passed."""
    from datetime import datetime, timezone
    from web.db import SessionLocal
    from web.models import Draft, ActivityLog

    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        due_drafts = (
            db.query(Draft)
            .filter(
                Draft.status == "pending",
                Draft.scheduled_at.isnot(None),
                Draft.scheduled_at <= now,
            )
            .with_for_update(skip_locked=True)
            .all()
        )
        if not due_drafts:
            return

        # Import the send function from app (deferred to avoid circular import)
        from web.app import _execute_draft

        for draft in due_drafts:
            try:
                _execute_draft(draft, draft.draft, draft.tenant_id, db)
                db.add(ActivityLog(
                    tenant_id=draft.tenant_id,
                    event_type="draft_auto_sent",
                    message=f"Scheduled draft auto-sent: {draft.guest_name}",
                ))
                db.commit()
                log.info("[%s] Scheduled draft %s auto-sent", draft.tenant_id, draft.id)
            except Exception as exc:
                log.error("[%s] Scheduled draft %s auto-send failed: %s",
                          draft.tenant_id, draft.id, exc)
                db.rollback()
                try:
                    failed_draft = db.query(Draft).filter(Draft.id == draft.id).first()
                    if failed_draft:
                        failed_draft.status = "failed"
                        from web.models import FailedDraftLog
                        db.add(FailedDraftLog(
                            tenant_id=failed_draft.tenant_id,
                            draft_id=failed_draft.id,
                            error_reason=str(exc)
                        ))
                    db.commit()
                except Exception as inner:
                    log.error("Failed to write to dead-letter log for draft %s: %s", draft.id, inner)
                    db.rollback()
    except Exception as exc:
        log.warning("Scheduled draft processing error: %s", exc)
    finally:
        db.close()


_last_retention_cleanup = 0.0  # Timestamp of last retention cleanup run


def _process_data_retention():
    """
    Purge old data from all tenants based on their data_retention_days setting.
    Runs once per hour (expensive operation).
    """
    global _last_retention_cleanup
    from datetime import datetime, timezone, timedelta
    import time

    now_ts = time.time()
    if now_ts - _last_retention_cleanup < 3600:  # Run max once per hour
        return

    _last_retention_cleanup = now_ts

    from web.db import SessionLocal
    from web.models import TenantConfig, ActivityLog, Draft, GuestTimelineEvent

    db = SessionLocal()
    try:
        # Get all tenants with retention policies
        tenant_configs = db.query(TenantConfig).all()
        for cfg in tenant_configs:
            if not cfg.data_retention_days or cfg.data_retention_days <= 0:
                continue

            cutoff_date = datetime.now(timezone.utc) - timedelta(days=cfg.data_retention_days)

            # Delete old activity logs
            deleted_logs = (
                db.query(ActivityLog)
                .filter(
                    ActivityLog.tenant_id == cfg.tenant_id,
                    ActivityLog.created_at < cutoff_date,
                )
                .delete()
            )

            # Delete old drafts (keep reservation link)
            deleted_drafts = (
                db.query(Draft)
                .filter(
                    Draft.tenant_id == cfg.tenant_id,
                    Draft.created_at < cutoff_date,
                )
                .delete()
            )

            # Delete old timeline events
            deleted_events = (
                db.query(GuestTimelineEvent)
                .filter(
                    GuestTimelineEvent.tenant_id == cfg.tenant_id,
                    GuestTimelineEvent.created_at < cutoff_date,
                )
                .delete()
            )

            if deleted_logs or deleted_drafts or deleted_events:
                db.commit()
                log.info(
                    "[%s] Data retention cleanup: deleted %d logs, %d drafts, %d timeline events",
                    cfg.tenant_id, deleted_logs, deleted_drafts, deleted_events,
                )
            else:
                db.rollback()
    except Exception as exc:
        log.error("Data retention cleanup error: %s", exc)
        db.rollback()
    finally:
        db.close()


_last_kpi_snapshot = 0.0

def _process_kpi_snapshots():
    """
    Compute and store KPI snapshots for all tenants (once per 24h).
    Requires _upsert_tenant_kpi_snapshot to be available in web.app.
    """
    global _last_kpi_snapshot
    import time
    from datetime import datetime, timezone
    from web.db import SessionLocal
    from web.models import TenantConfig, Draft, Reservation

    now_ts = time.time()
    if now_ts - _last_kpi_snapshot < 86400:  # 24h
        return

    _last_kpi_snapshot = now_ts

    db = SessionLocal()
    try:
        from web.app import _upsert_tenant_kpi_snapshot, derive_dashboard_kpis

        now = datetime.now(timezone.utc)
        for cfg in db.query(TenantConfig).all():
            try:
                pending = db.query(Draft).filter_by(tenant_id=cfg.tenant_id, status="pending").all()
                reservations = db.query(Reservation).filter_by(tenant_id=cfg.tenant_id).all()
                kpis = derive_dashboard_kpis(pending, reservations, now=now)
                open_issues = []  # skip issue tickets in scheduler snapshot
                _upsert_tenant_kpi_snapshot(db, cfg.tenant_id, kpis, open_issues, now)
            except Exception as exc:
                log.warning("[%s] KPI snapshot error: %s", cfg.tenant_id, exc)
        db.commit()
    except Exception as exc:
        log.error("KPI snapshot processing error: %s", exc)
        db.rollback()
    finally:
        db.close()


def start_all_workers():
    """Called at app startup — launch workers for all configured tenants + watchdog."""
    global _watchdog_thread, _watchdog_stop
    if not _embedded_workers_enabled():
        log.info("Embedded workers disabled by RUN_EMBEDDED_WORKERS")
        return
    if not _acquire_leader_lock():
        return
    from web.db import SessionLocal
    from web.models import TenantConfig
    db = SessionLocal()
    try:
        tenant_ids = [row.tenant_id for row in db.query(TenantConfig).all()]
    finally:
        db.close()

    for tenant_id in tenant_ids:
        try:
            _start_tenant(tenant_id)
        except Exception as exc:
            log.error("[%s] Failed to start workers: %s", tenant_id, exc)

    # Start watchdog
    _watchdog_stop.clear()
    _watchdog_thread = threading.Thread(
        target=_watchdog_loop,
        name="worker-watchdog",
        daemon=True,
    )
    _watchdog_thread.start()
    _start_leader_refresh()


def stop_all_workers():
    """Called at app shutdown."""
    global _watchdog_thread
    _watchdog_stop.set()
    if _watchdog_thread and _watchdog_thread.is_alive():
        _watchdog_thread.join(timeout=5)

    with _lock:
        ids = list(_workers.keys())
    for tenant_id in ids:
        _stop_tenant(tenant_id)
    _release_leader_lock()


def restart_worker(tenant_id: str):
    """Call after a tenant updates their settings."""
    _start_tenant(tenant_id)


def worker_status(tenant_id: str) -> dict:
    """Return dict describing running state of workers for a tenant."""
    with _lock:
        entry = _workers.get(tenant_id, {})
    return {
        "email_running": entry.get("email") is not None and entry["email"].is_alive(),
        "cal_running":   entry.get("cal") is not None and entry["cal"].is_alive(),
        "sched_running": entry.get("sched") is not None and entry["sched"].is_alive(),
        "pms_running":   entry.get("pms") is not None and entry["pms"].is_alive(),
        "watchdog_ok":   _watchdog_thread is not None and _watchdog_thread.is_alive(),
    }
