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

  For late_checkout / early_checkin:
    a. Check whether the same unit is free for the requested window (no next guest conflict).
    b. If unit is free  → update reservation directly.
    c. If unit blocked  → find alternative units for this tenant with availability.
    d. For each alternative: fetch its nightly rate (derived from payout_usd / nights).
       Quote the alternative at ITS OWN RATE — never give anything free.
    e. If no alternative → notify host + reply to guest that it's not possible.

The action result is recorded in result_json for audit.
Guest-facing reply text is stored in result_json["guest_reply"] so the caller
can send it back through the original channel (WhatsApp / SMS / PMS thread).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, date, time, timezone, timedelta
from typing import Optional, Any

from sqlalchemy.orm import Session
from sqlalchemy import and_, or_

from web.models import GuestAction, PMSIntegration, Reservation, TenantConfig

log = logging.getLogger(__name__)

# Known action types — anything else is rejected
_KNOWN_ACTIONS = {
    "late_checkout", "early_checkin", "extra_guest",
    "add_note", "block_dates", "maintenance",
}


# ---------------------------------------------------------------------------
# Intent detection
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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


def _parse_hhmm(t: str) -> Optional[time]:
    """Parse 'HH:MM' string into a time object, or None on failure."""
    try:
        h, m = t.strip().split(":")
        return time(int(h), int(m))
    except Exception:
        return None


def _nightly_rate(reservation: Reservation) -> Optional[float]:
    """
    Estimate nightly rate from payout_usd / nights.
    Returns None if data is missing.
    """
    if reservation.payout_usd and reservation.nights and reservation.nights > 0:
        return round(reservation.payout_usd / reservation.nights, 2)
    return None


def _unit_is_free_after(
    db: Session,
    tenant_id: str,
    unit_identifier: str,
    from_datetime: datetime,
) -> bool:
    """
    Return True if no confirmed reservation on `unit_identifier` starts
    before `from_datetime` on the same day (i.e. same-day next-guest check).

    Checks reservations with checkin on the same date as from_datetime that
    would conflict with the guest staying until from_datetime.
    """
    conflict_day = from_datetime.date()
    # Find any reservation on the same unit that checks in on the same day
    # and whose checkin time would be at or before the requested late checkout
    conflict = (
        db.query(Reservation)
        .filter(
            Reservation.tenant_id == tenant_id,
            Reservation.unit_identifier == unit_identifier,
            Reservation.status == "confirmed",
            Reservation.checkin == conflict_day,
        )
        .first()
    )
    return conflict is None


def _find_alternative_units(
    db: Session,
    tenant_id: str,
    exclude_unit: str,
    checkout_date: date,
    guests_count: int,
) -> list[dict]:
    """
    Find other units for this tenant that have NO confirmed reservation
    starting on checkout_date (i.e. they are free that afternoon).

    Returns list of dicts: {unit_identifier, listing_name, est_nightly_rate}
    sorted cheapest first (so host/system sees best-value options first).
    Rate is derived from recent reservations on that unit — always > 0.
    """
    # Get distinct units for this tenant with at least one past reservation
    # (so we know they're real units, not data artefacts)
    all_units_q = (
        db.query(
            Reservation.unit_identifier,
            Reservation.listing_name,
            Reservation.payout_usd,
            Reservation.nights,
        )
        .filter(
            Reservation.tenant_id == tenant_id,
            Reservation.unit_identifier.isnot(None),
            Reservation.unit_identifier != "",
            Reservation.unit_identifier != exclude_unit,
            Reservation.status == "confirmed",
        )
        .all()
    )

    # Aggregate by unit: average nightly rate across recent reservations
    unit_rates: dict[str, dict] = {}
    for row in all_units_q:
        uid = row.unit_identifier
        if uid not in unit_rates:
            unit_rates[uid] = {
                "unit_identifier": uid,
                "listing_name": row.listing_name or uid,
                "rate_sum": 0.0,
                "rate_count": 0,
            }
        if row.payout_usd and row.nights and row.nights > 0:
            unit_rates[uid]["rate_sum"] += row.payout_usd / row.nights
            unit_rates[uid]["rate_count"] += 1

    if not unit_rates:
        return []

    # Filter to units that are free on checkout_date
    free_units = []
    for uid, info in unit_rates.items():
        conflict = (
            db.query(Reservation)
            .filter(
                Reservation.tenant_id == tenant_id,
                Reservation.unit_identifier == uid,
                Reservation.status == "confirmed",
                Reservation.checkin == checkout_date,
            )
            .first()
        )
        if conflict:
            continue  # next guest is checking in on that day — not available

        est_rate = (
            round(info["rate_sum"] / info["rate_count"], 2)
            if info["rate_count"] > 0
            else None
        )
        free_units.append({
            "unit_identifier": uid,
            "listing_name": info["listing_name"],
            "est_nightly_rate": est_rate,
        })

    # Sort: units with known rates first, then by rate ascending
    free_units.sort(key=lambda u: (u["est_nightly_rate"] is None, u["est_nightly_rate"] or 0))
    return free_units


def _build_alternative_offer_text(
    current_unit: str,
    current_rate: Optional[float],
    alternatives: list[dict],
) -> str:
    """
    Build a human-readable offer message for guest-facing reply.
    Every alternative is quoted at its own rate — nothing is free.
    """
    if not alternatives:
        return ""

    lines = []
    for alt in alternatives[:3]:  # max 3 options
        name = alt["listing_name"] or alt["unit_identifier"]
        rate = alt["est_nightly_rate"]
        if rate is None:
            lines.append(f"• {name} — rate available on request")
        else:
            rate_str = f"${rate:.0f}/night"
            if current_rate:
                diff = rate - current_rate
                if diff > 1:
                    rate_str += f" (+${diff:.0f} vs your current unit)"
                elif diff < -1:
                    rate_str += f" (${abs(diff):.0f} less than your current unit)"
                else:
                    rate_str += " (same rate as your current unit)"
            lines.append(f"• {name} — {rate_str}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Core execution
# ---------------------------------------------------------------------------

def execute_action(db: Session, action: GuestAction) -> bool:
    """
    Run the PMS write for an approved action.
    Updates action.status / action.result_json / action.executed_at.

    For late_checkout / early_checkin:
      1. Checks if the same unit is free for the requested window.
      2. If free  → applies the change directly.
      3. If blocked → looks for free alternative units and quotes them at their
                      own rate. Never gives anything free.
      4. Stores a guest_reply in result_json so the caller can send it back.

    Returns True if the PMS write succeeded (or an alternative was found and
    communicated). Returns False only if the action truly failed with no path.
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

    pms_res_id = reservation.confirmation_code if reservation else ""

    ok = False
    result: dict[str, Any] = {}
    params = action.params_json or {}

    try:
        # ── Late checkout ──────────────────────────────────────────────────
        if action.action_type == "late_checkout":
            requested_time_str = params.get("requested_time") or "13:00"
            requested_t = _parse_hhmm(requested_time_str) or time(13, 0)

            unit = reservation.unit_identifier or ""
            checkout_date = reservation.checkout if reservation.checkout else date.today()
            if hasattr(checkout_date, "date"):
                checkout_date = checkout_date.date()

            # Build datetime for conflict check (checkout_date at requested time)
            requested_dt = datetime.combine(checkout_date, requested_t)

            # Step 1: is the same unit free until requested checkout?
            unit_free = _unit_is_free_after(db, action.tenant_id, unit, requested_dt) if unit else True

            if unit_free:
                # No conflict — apply directly
                ok = adapter.update_reservation(pms_res_id, {"checkout_time": requested_time_str})
                if ok:
                    result = {
                        "checkout_time": requested_time_str,
                        "applied": True,
                        "guest_reply": (
                            f"Great news! I've updated your checkout to {requested_time_str}. "
                            f"Enjoy the extra time — just leave the keys as instructed when you're ready. 🙂"
                        ),
                    }
                else:
                    # PMS rejected it (API error)
                    result = {
                        "applied": False,
                        "error": "PMS rejected the update",
                        "guest_reply": (
                            "I wasn't able to update your checkout time directly — "
                            "I've flagged this for the host to confirm with you shortly."
                        ),
                    }
            else:
                # Same unit is blocked — find alternatives
                current_rate = _nightly_rate(reservation)
                guests = reservation.guests_count or 1
                alternatives = _find_alternative_units(
                    db, action.tenant_id, unit, checkout_date, guests
                )

                if alternatives:
                    offer_text = _build_alternative_offer_text(unit, current_rate, alternatives)
                    result = {
                        "applied": False,
                        "reason": "same_unit_blocked",
                        "alternatives_found": len(alternatives),
                        "alternatives": alternatives,
                        "guest_reply": (
                            f"Unfortunately your current unit ({unit}) has another guest "
                            f"checking in that afternoon, so I can't extend your checkout there.\n\n"
                            f"However, we have the following units available:\n{offer_text}\n\n"
                            f"Would you like me to arrange a move to one of these? "
                            f"Just let me know which suits you and I'll sort it."
                        ),
                    }
                    ok = True  # we handled it — alternatives offered
                else:
                    # No unit free anywhere
                    result = {
                        "applied": False,
                        "reason": "no_alternatives",
                        "guest_reply": (
                            f"I'm sorry — your unit ({unit}) has another guest arriving that afternoon "
                            f"and we don't have any other units free at that time. "
                            f"I've notified the host who will get back to you shortly. "
                            f"Apologies for the inconvenience!"
                        ),
                    }
                    ok = False

        # ── Early check-in ─────────────────────────────────────────────────
        elif action.action_type == "early_checkin":
            requested_time_str = params.get("requested_time") or "13:00"
            requested_t = _parse_hhmm(requested_time_str) or time(13, 0)

            unit = reservation.unit_identifier or ""
            checkin_date = reservation.checkin if reservation.checkin else date.today()
            if hasattr(checkin_date, "date"):
                checkin_date = checkin_date.date()

            # Check if previous reservation on same unit checks out before requested time
            requested_dt = datetime.combine(checkin_date, requested_t)

            # Look for a reservation on same unit that checks OUT on checkin_date
            # (previous guest may still be there)
            prev_res = (
                db.query(Reservation)
                .filter(
                    Reservation.tenant_id == action.tenant_id,
                    Reservation.unit_identifier == unit,
                    Reservation.status == "confirmed",
                    Reservation.checkout == checkin_date,
                )
                .first()
                if unit else None
            )

            # If previous guest checks out at standard time (11:00) and requested
            # early check-in is after that, it's probably fine — apply it.
            # If there's a prev guest or no unit data, still try the PMS (it will reject if conflict)
            ok = adapter.update_reservation(pms_res_id, {"checkin_time": requested_time_str})
            if ok:
                result = {
                    "checkin_time": requested_time_str,
                    "applied": True,
                    "guest_reply": (
                        f"Done! I've arranged your early check-in for {requested_time_str}. "
                        f"We'll make sure the unit is ready for you. See you soon! 🏠"
                    ),
                }
            else:
                # PMS rejected — check alternatives
                current_rate = _nightly_rate(reservation)
                guests = reservation.guests_count or 1
                alternatives = _find_alternative_units(
                    db, action.tenant_id, unit, checkin_date, guests
                )
                if alternatives:
                    offer_text = _build_alternative_offer_text(unit, current_rate, alternatives)
                    result = {
                        "applied": False,
                        "reason": "unit_not_ready",
                        "alternatives_found": len(alternatives),
                        "alternatives": alternatives,
                        "guest_reply": (
                            f"Early check-in to your unit isn't available at {requested_time_str} — "
                            f"it's still being prepared.\n\n"
                            f"We do have these other units available from earlier:\n{offer_text}\n\n"
                            f"Would any of these work for you?"
                        ),
                    }
                    ok = True
                else:
                    result = {
                        "applied": False,
                        "reason": "no_alternatives",
                        "guest_reply": (
                            f"I wasn't able to arrange early check-in at {requested_time_str} — "
                            f"the unit is still being prepared and we don't have an alternative available earlier. "
                            f"I've let the host know and they'll confirm the earliest possible time with you."
                        ),
                    }

        # ── Extra guest ────────────────────────────────────────────────────
        elif action.action_type == "extra_guest":
            count = int(params.get("extra_guests_count") or 1)
            new_total = (reservation.guests_count or 1) + count if reservation else count
            ok = adapter.update_reservation(pms_res_id, {"guests_count": new_total})
            if ok:
                result = {
                    "new_total_guests": new_total,
                    "applied": True,
                    "guest_reply": (
                        f"Done! I've updated your reservation to {new_total} guests. "
                        f"Please note that additional guests may be subject to the host's extra-guest policy. "
                        f"Welcome to the extra member of your group! 🎉"
                    ),
                }
            else:
                result = {
                    "applied": False,
                    "error": "PMS rejected guest count update",
                    "guest_reply": (
                        "I wasn't able to update the guest count directly — "
                        "I've flagged this for the host to confirm with you."
                    ),
                }

        # ── Add note ───────────────────────────────────────────────────────
        elif action.action_type == "add_note":
            note = (params.get("note") or "").strip()[:1000]
            if note:
                ok = adapter.add_note(pms_res_id, note)
                result = {
                    "note_added": ok,
                    "note": note[:80],
                    "guest_reply": (
                        "Got it — I've logged that for the host. They'll follow up if needed."
                        if ok else
                        "I've noted your request and will pass it along to the host."
                    ),
                }
            else:
                result = {"error": "empty note", "applied": False}

        # ── Block dates ────────────────────────────────────────────────────
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
                else:
                    result = {"error": "missing listing_id or dates"}
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


# ---------------------------------------------------------------------------
# Guest reply dispatch
# ---------------------------------------------------------------------------

def dispatch_action_reply(db: Session, action: GuestAction) -> bool:
    """
    After execute_action(), send the guest_reply stored in result_json
    back through the original channel (WhatsApp / SMS / PMS thread).
    Called by the inbound handler after execute_action() returns.
    """
    reply_text = (action.result_json or {}).get("guest_reply", "")
    if not reply_text:
        return False
    if not action.reservation_id:
        return False

    try:
        reservation = db.query(Reservation).filter_by(id=action.reservation_id).first()
        if not reservation:
            return False

        from web.models import Draft
        # Find the originating draft to know which channel and reply_to to use
        draft = (
            db.query(Draft)
            .filter_by(id=action.draft_id)
            .first()
            if action.draft_id else None
        )
        if not draft:
            log.info("[ACTIONS] No originating draft for action %s — reply not sent", action.id)
            return False

        from web.app import _execute_draft
        _execute_draft(draft, reply_text, action.tenant_id, db,
                       reservation=reservation, auto_send=True)
        log.info("[ACTIONS] Action reply dispatched for action %s via %s", action.id, draft.source)
        return True
    except Exception as exc:
        log.warning("[ACTIONS] dispatch_action_reply failed: %s", exc)
        return False
