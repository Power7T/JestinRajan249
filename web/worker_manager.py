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
import threading
import time
from typing import Optional

from web import email_worker, calendar_worker, reservation_scheduler, pms_worker

log = logging.getLogger(__name__)

# Maps tenant_id → {"email": Thread, "email_stop": Event, "cal": Thread, "cal_stop": Event,
#                    "email_cfg": cfg, "cal_cfg": cfg}
_workers: dict[str, dict] = {}
_lock = threading.Lock()

# Watchdog
_watchdog_stop = threading.Event()
_watchdog_thread: Optional[threading.Thread] = None
_WATCHDOG_INTERVAL = 60  # seconds


def _start_tenant(tenant_id: str):
    """Start (or restart) background threads for one tenant."""
    # Stop existing workers if running
    _stop_tenant(tenant_id)

    email_cfg = email_worker.make_config_from_db(tenant_id)
    cal_cfg   = calendar_worker.make_config_from_db(tenant_id)

    if not email_cfg:
        log.info("[%s] Email not configured — skipping worker start", tenant_id)
        return

    entry: dict = {}

    # Email worker
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

    # Reservation scheduler (only if API key + imported reservations exist)
    res_cfg = reservation_scheduler.make_config_from_db(tenant_id)
    if res_cfg:
        api_key, prop_ctx = res_cfg
        sched_stop = threading.Event()
        t_sched = threading.Thread(
            target=reservation_scheduler._run_scheduler,
            args=(tenant_id, api_key, prop_ctx, sched_stop),
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

    with _lock:
        _workers[tenant_id] = entry

    log.info("[%s] Workers started (email=%s, calendar=%s, pms=%s)",
             tenant_id, True, cal_cfg is not None, pms_worker.has_active_pms(tenant_id))


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
    except Exception as exc:
        log.warning("Scheduled draft processing error: %s", exc)
    finally:
        db.close()


def start_all_workers():
    """Called at app startup — launch workers for all configured tenants + watchdog."""
    global _watchdog_thread, _watchdog_stop
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
