# © 2024 Jestin Rajan. All rights reserved.
"""
Agentic Actions — detect guest intents that require PMS write-back and execute them.

Action types:
  late_checkout  — push checkout time later
  early_checkin  — pull check-in time earlier
  extra_guest    — add a guest to the booking
  add_note       — log an internal note for the host
  block_dates    — block availability (host-initiated only)
  maintenance    — route to operations dispatch (handled separately)

Flow:
  1. detect_action_intent(guest_msg) → AI returns action_type + params (or None)
  2. create_action_record(...) — persists a GuestAction row
  3. execute_action(...) — if auto-approved, runs the PMS write; else marks pending

The action result is recorded in result_json for audit.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, date, timezone
from typing import Optional, Any

from sqlalchemy.orm import Session

from web.models import GuestAction, PMSIntegration, Reservation, TenantConfig

log = logging.getLogger(__name__)

# Known action types — anything else is rejected
_KNOWN_ACTIONS = {
    "late_checkout", "early_checkin", "extra_guest",
    "add_note", "block_dates", "maintenance",
}


def detect_action_intent(guest_message: str) -> Optional[dict]:
    """
    Use an LLM to detect if a guest message contains an actionable request.
    Returns {"action_type": str, "params": dict, "confidence": float} or None.
    Cheap model — uses Gemini Flash via OpenRouter.
    """
    if not guest_message or len(guest_message.strip()) < 4:
        return None

    prompt = f"""Decide if this guest message asks for a specific action that requires changing the reservation.

Message: "{guest_message[:400]}"

Action types you can detect:
- late_checkout: guest wants to depart later than scheduled
- early_checkin: guest wants to arrive earlier than scheduled
- extra_guest: guest wants to add a person not in original booking
- maintenance: guest is reporting something broken / not working
- add_note: any other actionable request the host should record

Respond with ONLY JSON (no markdown):
{{
  "has_action": true|false,
  "action_type": "late_checkout|early_checkin|extra_guest|maintenance|add_note|none",
  "params": {{
    "requested_time": "HH:MM if mentioned",
    "extra_guests_count": 0,
    "issue_description": "for maintenance",
    "note": "for add_note"
  }},
  "confidence": 0.0-1.0
}}

If the message is just a question or thanks, set has_action=false."""

    try:
        import openai
        from web.db import SessionLocal
        from web.system_config_store import load_system_config
        from web.crypto import decrypt
        with SessionLocal() as db:
            sys_conf = load_system_config(db)
            if not sys_conf or not sys_conf.openrouter_api_key_enc:
                return None
            key = decrypt(sys_conf.openrouter_api_key_enc) or sys_conf.openrouter_api_key_enc
        client = openai.OpenAI(base_url="https://openrouter.ai/api/v1", api_key=key)
        resp = client.chat.completions.create(
            model="google/gemini-2.5-flash",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=200,
            temperature=0,
        )
        raw = (resp.choices[0].message.content or "").strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        data = json.loads(raw.strip())
        if not data.get("has_action"):
            return None
        action_type = (data.get("action_type") or "").strip()
        if action_type not in _KNOWN_ACTIONS:
            return None
        return {
            "action_type": action_type,
            "params": data.get("params") or {},
            "confidence": float(data.get("confidence") or 0.0),
        }
    except Exception as exc:
        log.warning("[ACTIONS] detect_action_intent failed: %s", exc)
        return None


def is_auto_approved(cfg: TenantConfig, action_type: str) -> bool:
    """Check whether this tenant has whitelisted this action type for autonomous execution."""
    if not cfg:
        return False
    allowed = {a.strip() for a in (cfg.action_auto_approve or "").split(",") if a.strip()}
    return action_type in allowed


def create_action(db: Session, tenant_id: str, action_type: str, params: dict,
                  reservation: Optional[Reservation], draft_id: Optional[int],
                  cfg: TenantConfig) -> GuestAction:
    """Persist a GuestAction row. Returns the new row."""
    requires_approval = not is_auto_approved(cfg, action_type)
    action = GuestAction(
        tenant_id=tenant_id,
        reservation_id=reservation.id if reservation else None,
        draft_id=draft_id,
        action_type=action_type,
        params_json=params or {},
        status="pending",
        requires_approval=requires_approval,
    )
    db.add(action)
    db.commit()
    db.refresh(action)
    return action


def _get_pms_adapter(db: Session, tenant_id: str):
    """Return the first active PMS adapter for this tenant, or None."""
    integration = db.query(PMSIntegration).filter_by(
        tenant_id=tenant_id, is_active=True
    ).first()
    if not integration:
        return None, None
    try:
        from web.crypto import decrypt
        from web.pms_base import make_adapter
        adapter = make_adapter(
            integration.pms_type,
            decrypt(integration.api_key_enc),
            integration.account_id or "",
            integration.api_base_url or "",
        )
        return adapter, integration
    except Exception as exc:
        log.error("[ACTIONS] Could not load PMS adapter for tenant %s: %s", tenant_id, exc)
        return None, integration


def execute_action(db: Session, action: GuestAction) -> bool:
    """
    Run the PMS write for an approved action.
    Updates action.status / action.result_json / action.executed_at.
    Returns True on success.
    """
    adapter, integration = _get_pms_adapter(db, action.tenant_id)
    if not adapter:
        action.status = "failed"
        action.result_json = {"error": "No PMS adapter configured"}
        db.commit()
        return False

    reservation = (
        db.query(Reservation).filter_by(id=action.reservation_id).first()
        if action.reservation_id else None
    )
    if action.action_type in ("late_checkout", "early_checkin", "extra_guest", "add_note") and not reservation:
        action.status = "failed"
        action.result_json = {"error": "Reservation not found"}
        db.commit()
        return False

    # We need the PMS-side reservation id (confirmation_code) if available
    pms_res_id = reservation.confirmation_code if reservation else ""

    ok = False
    result: dict[str, Any] = {}
    params = action.params_json or {}

    try:
        if action.action_type == "late_checkout":
            new_time = params.get("requested_time") or "13:00"
            ok = adapter.update_reservation(pms_res_id, {"checkout_time": new_time})
            result = {"checkout_time": new_time, "applied": ok}

        elif action.action_type == "early_checkin":
            new_time = params.get("requested_time") or "13:00"
            ok = adapter.update_reservation(pms_res_id, {"checkin_time": new_time})
            result = {"checkin_time": new_time, "applied": ok}

        elif action.action_type == "extra_guest":
            count = int(params.get("extra_guests_count") or 1)
            new_total = (reservation.guests_count or 1) + count if reservation else count
            ok = adapter.update_reservation(pms_res_id, {"guests_count": new_total})
            result = {"new_total_guests": new_total, "applied": ok}

        elif action.action_type == "add_note":
            note = (params.get("note") or "").strip()[:1000]
            if note:
                ok = adapter.add_note(pms_res_id, note)
                result = {"note_added": ok, "note": note[:80]}

        elif action.action_type == "block_dates":
            listing_id = params.get("listing_id") or ""
            from_str   = params.get("from_date") or ""
            to_str     = params.get("to_date") or ""
            reason     = params.get("reason") or ""
            try:
                fd = date.fromisoformat(from_str) if from_str else None
                td = date.fromisoformat(to_str) if to_str else None
                if listing_id and fd and td:
                    ok = adapter.block_dates(listing_id, fd, td, reason)
                    result = {"listing": listing_id, "from": from_str, "to": to_str, "applied": ok}
            except Exception as exc:
                result = {"error": f"date parsing failed: {exc}"}
                ok = False

        else:
            result = {"error": f"unsupported action_type: {action.action_type}"}
            ok = False
    except Exception as exc:
        log.error("[ACTIONS] execute_action error (%s): %s", action.action_type, exc, exc_info=True)
        result = {"error": str(exc)[:240]}
        ok = False

    action.status = "executed" if ok else "failed"
    action.executed_at = datetime.now(timezone.utc) if ok else None
    action.result_json = result
    db.commit()
    return ok
