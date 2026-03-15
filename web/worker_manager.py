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

from web import email_worker, calendar_worker

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

    with _lock:
        _workers[tenant_id] = entry

    log.info("[%s] Workers started (email=%s, calendar=%s)", tenant_id, True, cal_cfg is not None)


def _stop_tenant(tenant_id: str):
    """Signal stop + join threads for one tenant."""
    with _lock:
        entry = _workers.pop(tenant_id, None)
    if not entry:
        return
    for key in ("email_stop", "cal_stop"):
        evt = entry.get(key)
        if evt:
            evt.set()
    for key in ("email", "cal"):
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

            email_dead = email_thread is not None and not email_thread.is_alive()
            cal_dead   = cal_thread is not None and not cal_thread.is_alive()

            if email_dead or cal_dead:
                log.warning(
                    "[%s] Dead workers detected (email=%s cal=%s) — restarting",
                    tenant_id, email_dead, cal_dead,
                )
                try:
                    _start_tenant(tenant_id)
                except Exception as exc:
                    log.error("[%s] Watchdog restart failed: %s", tenant_id, exc)

    log.info("Worker watchdog stopped")


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
        "watchdog_ok":   _watchdog_thread is not None and _watchdog_thread.is_alive(),
    }
