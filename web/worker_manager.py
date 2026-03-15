# © 2024 Jestin Rajan. All rights reserved.
"""
Worker Manager — starts/stops per-tenant background threads.
Each tenant that has email + API key configured gets:
  - one email_worker thread (IMAP poll loop)
  - one calendar_worker thread (iCal poll loop, only if iCal URLs present)

Call start_all_workers() at app startup and stop_all_workers() at shutdown.
Call restart_worker(tenant_id) whenever a tenant updates their settings.
"""

import logging
import threading
from typing import Optional

from web import email_worker, calendar_worker

log = logging.getLogger(__name__)

# Maps tenant_id → {"email": Thread, "email_stop": Event, "cal": Thread, "cal_stop": Event}
_workers: dict[str, dict] = {}
_lock = threading.Lock()


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


def start_all_workers():
    """Called at app startup — launch workers for all configured tenants."""
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


def stop_all_workers():
    """Called at app shutdown."""
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
        "email_running": entry.get("email", None) is not None and entry["email"].is_alive(),
        "cal_running":   entry.get("cal", None) is not None and entry["cal"].is_alive(),
    }
