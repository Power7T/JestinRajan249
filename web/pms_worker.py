# © 2024 Jestin Rajan. All rights reserved.
"""
PMS Worker — polls a host's PMS API for new guest messages and creates Drafts.

One thread per tenant (same model as email_worker.py).
Managed by worker_manager.py.

For PMS-sourced messages:
  - Routine messages: draft is auto-approved, reply sent via PMS API
  - Complex messages: draft saved as pending — host approves on dashboard,
    then the approve route sends reply via PMS API (draft.source == "pms")
"""

import logging
import threading
from datetime import datetime, timezone, timedelta
from typing import Optional

from web.db import SessionLocal
from web.models import Draft, ActivityLog, Reservation, PMSIntegration, PMSProcessedMessage
from web.classifier import (
    classify_message, detect_vendor_type, generate_draft,
    make_draft_id, needs_escalation, build_property_context,
)
from web.crypto import decrypt
from web.pms_base import make_adapter, PMSMessage

log = logging.getLogger(__name__)

_POLL_INTERVAL = 60   # seconds between polls
_MAX_BACKOFF   = 600  # 10 minutes max


def _load_config(tenant_id: str) -> Optional[dict]:
    """Return tenant config dict needed by the worker, or None if not ready."""
    db = SessionLocal()
    try:
        from web.models import TenantConfig
        cfg = db.query(TenantConfig).filter_by(tenant_id=tenant_id).first()
        if not cfg or not cfg.anthropic_api_key_enc:
            return None
        integrations = db.query(PMSIntegration).filter_by(
            tenant_id=tenant_id, is_active=True
        ).all()
        if not integrations:
            return None
        return {
            "anthropic_api_key": decrypt(cfg.anthropic_api_key_enc),
            "property_context":  build_property_context(cfg),
            "escalation_email":  cfg.escalation_email or cfg.email_address or "",
            "integrations": [
                {
                    "id":           i.id,
                    "pms_type":     i.pms_type,
                    "api_key":      decrypt(i.api_key_enc),
                    "account_id":   i.account_id or "",
                    "base_url":     i.api_base_url or "",
                    "last_synced":  i.last_synced_at,
                }
                for i in integrations
            ],
        }
    finally:
        db.close()


def _already_processed(db, integration_id: int, message_id: str) -> bool:
    return db.query(PMSProcessedMessage).filter_by(
        pms_integration_id=integration_id,
        pms_message_id=message_id,
    ).first() is not None


def _mark_processed(db, tenant_id: str, integration_id: int, message_id: str):
    db.add(PMSProcessedMessage(
        tenant_id=tenant_id,
        pms_integration_id=integration_id,
        pms_message_id=message_id,
    ))


def _lookup_reservation(db, tenant_id: str, guest_name: str) -> Optional[str]:
    from datetime import date as date_type
    today = datetime.now(timezone.utc).date()
    window_start = today - timedelta(days=7)
    window_end   = today + timedelta(days=90)
    rows = db.query(Reservation).filter(
        Reservation.tenant_id == tenant_id,
        Reservation.status == "confirmed",
        Reservation.checkin >= window_start,
        Reservation.checkin <= window_end,
    ).all()
    name_parts = guest_name.lower().split()
    for r in rows:
        db_name = r.guest_name.lower()
        if any(p in db_name or db_name in p for p in name_parts if len(p) > 2):
            lines = [f"Reservation: {r.confirmation_code}"]
            if r.listing_name:
                lines.append(f"Property: {r.listing_name}")
            if r.checkin:
                lines.append(f"Check-in: {r.checkin.strftime('%A, %B %d, %Y')}")
            if r.checkout:
                lines.append(f"Check-out: {r.checkout.strftime('%A, %B %d, %Y')}")
            if r.nights:
                lines.append(f"Nights: {r.nights}")
            if r.guests_count:
                lines.append(f"Guests: {r.guests_count}")
            return "\n".join(lines)
    return None


def _save_draft(db, tenant_id: str, draft_id: str, msg: PMSMessage,
                msg_type: str, vendor_type: Optional[str], draft_text: str,
                integration_id: int):
    db.add(Draft(
        id=draft_id,
        tenant_id=tenant_id,
        source="pms",
        guest_name=msg.guest_name,
        message=msg.text,
        reply_to=f"{integration_id}:{msg.reservation_id}",  # "int_id:res_id" for _execute_draft
        msg_type=msg_type,
        vendor_type=vendor_type,
        draft=draft_text,
        status="pending",
        created_at=datetime.now(timezone.utc),
    ))
    db.add(ActivityLog(
        tenant_id=tenant_id,
        event_type="pms_message_received",
        message=f"PMS message from {msg.guest_name} — {msg_type} ({msg.channel})",
    ))


def _process_message(tenant_id: str, cfg: dict, msg: PMSMessage,
                     integration_id: int, adapter):
    db = SessionLocal()
    try:
        guest_msg = msg.text
        if not guest_msg.strip():
            return

        # Human handoff escalation check
        if needs_escalation(guest_msg):
            draft_id = make_draft_id("pms")
            escalation_note = (
                f"[ESCALATION ALERT] This message requires immediate human attention.\n\n"
                f"Guest: {msg.guest_name}\nMessage:\n{guest_msg}"
            )
            _save_draft(db, tenant_id, draft_id, msg, "complex", None,
                        escalation_note, integration_id)
            _mark_processed(db, tenant_id, integration_id, msg.message_id)
            db.commit()
            log.warning("[%s] PMS escalation triggered for %s", tenant_id, msg.guest_name)
            if cfg["escalation_email"]:
                try:
                    from web.mailer import send_escalation_alert
                    send_escalation_alert(cfg["escalation_email"], msg.guest_name, guest_msg)
                except Exception as exc:
                    log.error("[%s] Escalation alert failed: %s", tenant_id, exc)
            return

        msg_type    = classify_message(guest_msg)
        vendor_type = detect_vendor_type(guest_msg) if msg_type == "complex" else None

        # Enrich with reservation context
        res_ctx = _lookup_reservation(db, tenant_id, msg.guest_name)
        full_ctx = cfg["property_context"]
        if res_ctx:
            full_ctx = (full_ctx + "\n\n<reservation>\n" + res_ctx + "\n</reservation>").strip()

        try:
            draft_text = generate_draft(
                cfg["anthropic_api_key"], msg.guest_name, guest_msg, msg_type,
                property_context=full_ctx,
            )
        except RuntimeError as exc:
            log.error("[%s] PMS draft generation failed: %s", tenant_id, exc)
            return

        draft_id = make_draft_id("pms")
        _save_draft(db, tenant_id, draft_id, msg, msg_type, vendor_type,
                    draft_text, integration_id)
        _mark_processed(db, tenant_id, integration_id, msg.message_id)
        db.commit()

        # Auto-send routine messages immediately via PMS
        if msg_type == "routine":
            ok = adapter.send_message(msg.reservation_id, draft_text)
            if ok:
                draft = db.query(Draft).filter_by(id=draft_id).first()
                if draft:
                    draft.status      = "approved"
                    draft.final_text  = draft_text
                    draft.approved_at = datetime.now(timezone.utc)
                    db.commit()
                log.info("[%s] PMS routine reply auto-sent to %s", tenant_id, msg.guest_name)
            else:
                log.error("[%s] PMS auto-send failed for %s — draft kept pending",
                          tenant_id, msg.guest_name)
        else:
            log.info("[%s] PMS complex draft %s saved — awaiting host approval",
                     tenant_id, draft_id)
    finally:
        db.close()


def _poll_integration(tenant_id: str, cfg: dict, int_cfg: dict):
    """Poll one PMS integration for new messages."""
    adapter = make_adapter(
        int_cfg["pms_type"],
        int_cfg["api_key"],
        int_cfg["account_id"],
        int_cfg["base_url"],
    )

    # Default: look back 1 hour on first run; otherwise since last sync
    since = int_cfg["last_synced"] or (datetime.now(timezone.utc) - timedelta(hours=1))

    try:
        messages = adapter.get_new_messages(since)
    except Exception as exc:
        log.error("[%s] PMS get_new_messages failed (%s): %s",
                  tenant_id, int_cfg["pms_type"], exc)
        return

    db = SessionLocal()
    try:
        for msg in messages:
            if _already_processed(db, int_cfg["id"], msg.message_id):
                continue
            _process_message(tenant_id, cfg, msg, int_cfg["id"], adapter)

        # Update last_synced_at
        integration = db.query(PMSIntegration).filter_by(id=int_cfg["id"]).first()
        if integration:
            integration.last_synced_at = datetime.now(timezone.utc)
            db.commit()
    finally:
        db.close()


def run_for_tenant(tenant_id: str, stop_flag: threading.Event):
    """Main poll loop for one tenant. Runs until stop_flag is set."""
    fail_streak = 0
    log.info("[%s] PMS worker started", tenant_id)

    while not stop_flag.is_set():
        try:
            cfg = _load_config(tenant_id)
            if not cfg:
                # No active integrations — stop this worker
                log.info("[%s] PMS worker: no active integrations, stopping", tenant_id)
                break

            for int_cfg in cfg["integrations"]:
                _poll_integration(tenant_id, cfg, int_cfg)

            fail_streak = 0
        except Exception as exc:
            fail_streak += 1
            backoff = min(_POLL_INTERVAL * (2 ** (fail_streak - 1)), _MAX_BACKOFF)
            log.error("[%s] PMS worker error (streak=%d): %s — backoff %ds",
                      tenant_id, fail_streak, exc, backoff)
            stop_flag.wait(backoff)
            continue

        stop_flag.wait(_POLL_INTERVAL)

    log.info("[%s] PMS worker stopped", tenant_id)


def has_active_pms(tenant_id: str) -> bool:
    """Return True if the tenant has at least one active PMS integration."""
    db = SessionLocal()
    try:
        return db.query(PMSIntegration).filter_by(
            tenant_id=tenant_id, is_active=True
        ).first() is not None
    finally:
        db.close()
