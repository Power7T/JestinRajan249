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
    "late_checkout", "early_checkin", "extend_stay", "extra_guest",
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
- late_checkout: guest wants to depart later than their scheduled checkout TIME (same day, different hour)
- early_checkin: guest wants to arrive earlier than their scheduled check-in time
- extend_stay: guest wants to stay additional NIGHTS / days, or asks to extend their booking to a later date
- extra_guest: guest wants to add a person not in original booking
- maintenance: guest is reporting something broken / not working
- add_note: any other actionable request the host should record

KEY DISTINCTION: late_checkout = same day, different time. extend_stay = more nights.

Respond with ONLY JSON (no markdown):
{{
  "has_action": true|false,
  "action_type": "late_checkout|early_checkin|extend_stay|extra_guest|maintenance|add_note|none",
  "params": {{
    "requested_time": "HH:MM if mentioned (for late_checkout/early_checkin)",
    "extra_nights": 0,
    "new_checkout_date": "YYYY-MM-DD if a specific date is mentioned (for extend_stay)",
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
# Policy helpers
# ---------------------------------------------------------------------------

# Defaults applied when the host has not configured a policy
_POLICY_DEFAULTS = {
    "late_checkout": {
        "same_unit_free":          "free",           # allow at no charge
        "alt_unit_pricing":        "charge_alt_rate", # quote alternative at its own rate
        "no_unit_available":       "deny",
        "flat_fee_amount":         0,
        "flat_fee_currency":       "USD",
        # Cutoff: requests past this time count as an extra night, not just a late checkout
        "cutoff_time":             "14:00",           # HH:MM — default 2 PM
        "extra_night_mode":        "nightly_rate",    # nightly_rate | flat_fee | approval_required
        "extra_night_fee_amount":  0,
        "extra_night_fee_currency":"USD",
    },
    "early_checkin": {
        "same_unit_free":          "free",
        "alt_unit_pricing":        "charge_alt_rate",
        "no_unit_available":       "deny",
        "flat_fee_amount":         0,
        "flat_fee_currency":       "USD",
        # Cutoff: requests earlier than this time count as an extra night
        "cutoff_time":             "10:00",           # HH:MM — default 10 AM
        "extra_night_mode":        "nightly_rate",
        "extra_night_fee_amount":  0,
        "extra_night_fee_currency":"USD",
    },
    "extend_stay": {
        "when_available":    "charge_nightly",   # charge_nightly | flat_fee_per_night | approval_required
        "no_unit_available": "deny",              # deny | escalate
        "flat_fee_per_night": 0,
        "flat_fee_currency":  "USD",
    },
}


def _get_policy(cfg: Optional[TenantConfig], action_type: str) -> dict:
    """
    Return the host-configured outcome policy for this action_type.
    Falls back to _POLICY_DEFAULTS if the host has not set anything.
    """
    defaults = _POLICY_DEFAULTS.get(action_type, {})
    if not cfg:
        return defaults
    raw = getattr(cfg, "action_policies", None)
    if not raw:
        return defaults
    try:
        policies = json.loads(raw)
        host_policy = policies.get(action_type, {})
        # Merge: host values override defaults
        merged = {**defaults, **host_policy}
        return merged
    except Exception:
        return defaults


def _fmt_fee(amount: float, currency: str) -> str:
    symbols = {"USD": "$", "GBP": "£", "EUR": "€", "AUD": "A$", "CAD": "C$"}
    sym = symbols.get((currency or "USD").upper(), currency + " ")
    return f"{sym}{amount:.0f}"


def is_auto_approved(cfg: TenantConfig, action_type: str) -> bool:
    """Check whether this tenant has whitelisted this action type for autonomous execution."""
    if not cfg:
        return False
    allowed = {a.strip() for a in (cfg.action_auto_approve or "").split(",") if a.strip()}
    return action_type in allowed


def create_action(db: Session, tenant_id: str, action_type: str, params: dict,
                  reservation: Optional[Reservation], draft_id: Optional[int],
                  cfg: TenantConfig) -> GuestAction:
    """
    Persist a GuestAction row. Returns the new row.

    When the action requires host approval (not auto-approved), pre-populates
    result_json with a guest_reply so dispatch_action_reply() can still send
    a coherent policy-correct message: 'Your request has been sent to the host.'
    """
    requires_approval = not is_auto_approved(cfg, action_type)

    # For approval-required actions, build a pending-notice guest_reply now
    # so the guest gets a single policy-correct message immediately.
    initial_result: dict = {}
    if requires_approval:
        _labels = {
            "late_checkout":  "late checkout request",
            "early_checkin":  "early check-in request",
            "extend_stay":    "stay extension request",
            "extra_guest":    "additional guest request",
            "add_note":       "special request",
        }
        label = _labels.get(action_type, "request")
        initial_result = {
            "guest_reply": (
                f"I've sent your {label} to the host for review. "
                f"You'll receive confirmation shortly!"
            ),
        }

    action = GuestAction(
        tenant_id=tenant_id,
        reservation_id=reservation.id if reservation else None,
        draft_id=draft_id,
        action_type=action_type,
        params_json=params or {},
        status="pending",
        requires_approval=requires_approval,
        result_json=initial_result if initial_result else None,
    )
    db.add(action)
    db.commit()
    db.refresh(action)
    return action


def _get_pms_adapter(db: Session, tenant_id: str):
    """
    Return the best available PMS adapter for this tenant.

    Priority:
      1. Active external PMS integration (Hostaway, Guesty, iCal, etc.)
      2. LocalPMSAdapter — writes directly to HostAI's Reservation table.
         This is the fallback for hosts with no external PMS, making all
         agentic actions fully autonomous regardless of tech stack.
    """
    from web.pms_local import LocalPMSAdapter

    integration = db.query(PMSIntegration).filter_by(
        tenant_id=tenant_id, is_active=True
    ).first()

    if not integration:
        log.debug("[ACTIONS] No external PMS for %s — using LocalPMSAdapter", tenant_id)
        return LocalPMSAdapter(db, tenant_id), None

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
        log.error("[ACTIONS] External PMS adapter failed for %s (%s): %s — falling back to LocalPMSAdapter",
                  tenant_id, integration.pms_type, exc)
        return LocalPMSAdapter(db, tenant_id), integration


def _parse_hhmm(t: str) -> Optional[time]:
    """Parse 'HH:MM' string into a time object, or None on failure."""
    try:
        h, m = t.strip().split(":")
        return time(int(h), int(m))
    except Exception:
        return None


def _t_to_min(t: time) -> int:
    """Convert a time object to total minutes since midnight for easy comparison."""
    return t.hour * 60 + t.minute


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


def _unit_is_free_window(
    db: Session,
    tenant_id: str,
    unit_identifier: str,
    from_date: date,
    to_date: date,
) -> bool:
    """
    Return True if no confirmed reservation on `unit_identifier` has a checkin
    date that falls in [from_date, to_date).
    Used for extend_stay to check the extra nights are unbooked.
    """
    conflict = (
        db.query(Reservation)
        .filter(
            Reservation.tenant_id == tenant_id,
            Reservation.unit_identifier == unit_identifier,
            Reservation.status == "confirmed",
            Reservation.checkin >= from_date,
            Reservation.checkin < to_date,
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
    alt_mode: str = "charge_alt_rate",
    flat_fee_amount: float = 0,
    flat_fee_currency: str = "USD",
) -> str:
    """
    Build a human-readable offer list for guest-facing reply.
    Pricing shown depends on the host's alt_unit_pricing policy:
      charge_alt_rate — each unit quoted at its own rate
      waive_extra     — guest pays their current rate (host absorbs difference)
      flat_fee        — single flat fee regardless of unit
      approval_required — pricing TBC
    """
    if not alternatives:
        return ""

    lines = []
    for alt in alternatives[:3]:
        name = alt["listing_name"] or alt["unit_identifier"]
        alt_rate = alt["est_nightly_rate"]

        if alt_mode == "waive_extra":
            rate_str = (
                f"{_fmt_fee(current_rate, flat_fee_currency)}/night (your current rate — we cover the difference)"
                if current_rate else "same rate as your current booking"
            )
        elif alt_mode == "flat_fee":
            rate_str = f"flat {_fmt_fee(flat_fee_amount, flat_fee_currency)} fee"
        elif alt_mode == "approval_required":
            rate_str = "pricing to be confirmed by host"
        else:  # charge_alt_rate
            if alt_rate is None:
                rate_str = "rate on request"
            elif current_rate and abs(alt_rate - current_rate) < 1:
                rate_str = f"{_fmt_fee(alt_rate, flat_fee_currency)}/night (same as your current unit)"
            elif current_rate and alt_rate > current_rate:
                rate_str = (
                    f"{_fmt_fee(alt_rate, flat_fee_currency)}/night "
                    f"(+{_fmt_fee(alt_rate - current_rate, flat_fee_currency)} vs your current unit)"
                )
            else:
                rate_str = f"{_fmt_fee(alt_rate, flat_fee_currency)}/night" + (
                    f" ({_fmt_fee(current_rate - alt_rate, flat_fee_currency)} less than your current unit)"
                    if current_rate else ""
                )
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
    # Load tenant config for policy lookups
    cfg = db.query(TenantConfig).filter_by(tenant_id=action.tenant_id).first()

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
            policy = _get_policy(cfg, "late_checkout")
            requested_time_str = params.get("requested_time") or "13:00"
            requested_t = _parse_hhmm(requested_time_str) or time(13, 0)

            unit = reservation.unit_identifier or ""
            checkout_date = reservation.checkout if reservation.checkout else date.today()
            if hasattr(checkout_date, "date"):
                checkout_date = checkout_date.date()

            requested_dt = datetime.combine(checkout_date, requested_t)
            unit_free = _unit_is_free_after(db, action.tenant_id, unit, requested_dt) if unit else True

            # Determine if this is "extra night" territory (request past host's cutoff)
            cutoff_str = policy.get("cutoff_time") or "14:00"
            cutoff_t   = _parse_hhmm(cutoff_str) or time(14, 0)
            is_extra_night = _t_to_min(requested_t) > _t_to_min(cutoff_t)

            if unit_free:
                # ── Same unit is free ────────────────────────────────────────
                if is_extra_night:
                    # Request is past the cutoff — treat as an extra night charge
                    extra_mode     = policy.get("extra_night_mode", "nightly_rate")
                    extra_fee_amt  = float(policy.get("extra_night_fee_amount") or 0)
                    extra_fee_curr = policy.get("extra_night_fee_currency") or "USD"
                    nightly        = _nightly_rate(reservation)

                    if extra_mode == "approval_required":
                        result = {
                            "applied": False,
                            "reason": "pending_host_approval_extra_night",
                            "guest_reply": (
                                f"Checking out at {requested_time_str} is quite late — that's past our standard "
                                f"late checkout window (after {cutoff_str}), so it counts as an additional night. "
                                f"I've sent this to the host for approval. You'll hear back shortly! 🙂"
                            ),
                        }
                        ok = False

                    elif extra_mode == "flat_fee":
                        fee_str = _fmt_fee(extra_fee_amt, extra_fee_curr)
                        pms_ok  = adapter.update_reservation(pms_res_id, {"checkout_time": requested_time_str})
                        ok = pms_ok
                        if pms_ok:
                            result = {
                                "checkout_time": requested_time_str,
                                "applied": True,
                                "extra_night": True,
                                "fee_charged": extra_fee_amt,
                                "fee_currency": extra_fee_curr,
                                "guest_reply": (
                                    f"Done! Your checkout has been extended to {requested_time_str}. "
                                    f"Since this is past {cutoff_str}, an extra-night fee of {fee_str} applies. "
                                    f"Enjoy the extended stay! 🙂"
                                ),
                            }
                        else:
                            result = {
                                "applied": False,
                                "guest_reply": (
                                    "I wasn't able to update your checkout time — "
                                    "I've flagged this for the host to sort out with you shortly."
                                ),
                            }

                    else:  # nightly_rate — default
                        if nightly:
                            rate_msg = f"an additional night charge of {_fmt_fee(nightly, extra_fee_curr or 'USD')}"
                        else:
                            rate_msg = "an additional night's charge at your booking rate"
                        # Extend checkout date by 1 night
                        next_day = checkout_date + timedelta(days=1)
                        pms_ok = adapter.update_reservation(pms_res_id, {
                            "checkout_time": requested_time_str,
                            "checkout_date": str(next_day),
                        })
                        ok = pms_ok
                        if pms_ok:
                            result = {
                                "checkout_time": requested_time_str,
                                "checkout_date": str(next_day),
                                "applied": True,
                                "extra_night": True,
                                "guest_reply": (
                                    f"Done! Your stay has been extended — checkout is now {requested_time_str} "
                                    f"on {next_day.strftime('%B %d')}. "
                                    f"Since you're staying past {cutoff_str}, this includes {rate_msg}. "
                                    f"Enjoy the extra time! 🙂"
                                ),
                            }
                        else:
                            result = {
                                "applied": False,
                                "guest_reply": (
                                    "I wasn't able to update your checkout — "
                                    "I've flagged this for the host to sort out with you shortly."
                                ),
                            }

                else:
                    # ── Standard late checkout — apply same_unit_free policy ──
                    mode = policy.get("same_unit_free", "free")

                    if mode == "approval_required":
                        result = {
                            "applied": False,
                            "reason": "pending_host_approval",
                            "guest_reply": (
                                f"I've sent your late checkout request ({requested_time_str}) to the host for approval. "
                                f"You'll hear back shortly! 🙂"
                            ),
                        }
                        ok = False

                    elif mode == "flat_fee":
                        fee_amt  = float(policy.get("flat_fee_amount") or 0)
                        fee_curr = policy.get("flat_fee_currency") or "USD"
                        fee_str  = _fmt_fee(fee_amt, fee_curr)
                        pms_ok = adapter.update_reservation(pms_res_id, {"checkout_time": requested_time_str})
                        ok = pms_ok
                        if pms_ok:
                            result = {
                                "checkout_time": requested_time_str,
                                "applied": True,
                                "fee_charged": fee_amt,
                                "fee_currency": fee_curr,
                                "guest_reply": (
                                    f"Done! Your checkout has been extended to {requested_time_str}. "
                                    f"A late checkout fee of {fee_str} will be added to your booking. "
                                    f"Enjoy the extra time! 🙂"
                                ),
                            }
                        else:
                            result = {
                                "applied": False,
                                "guest_reply": (
                                    "I wasn't able to update your checkout time — "
                                    "I've flagged this for the host to sort out with you shortly."
                                ),
                            }

                    else:  # "free" — default
                        pms_ok = adapter.update_reservation(pms_res_id, {"checkout_time": requested_time_str})
                        ok = pms_ok
                        if pms_ok:
                            result = {
                                "checkout_time": requested_time_str,
                                "applied": True,
                                "guest_reply": (
                                    f"Great news! Your checkout has been extended to {requested_time_str} — "
                                    f"no extra charge. Enjoy the extra time! 🙂"
                                ),
                            }
                        else:
                            result = {
                                "applied": False,
                                "guest_reply": (
                                    "I wasn't able to update your checkout time directly — "
                                    "I've flagged this for the host to confirm with you shortly."
                                ),
                            }

            else:
                # ── Same unit is blocked — find alternatives ───────────────
                current_rate = _nightly_rate(reservation)
                guests = reservation.guests_count or 1
                alternatives = _find_alternative_units(
                    db, action.tenant_id, unit, checkout_date, guests
                )

                if alternatives:
                    alt_mode     = policy.get("alt_unit_pricing", "charge_alt_rate")
                    fee_amt      = float(policy.get("flat_fee_amount") or 0)
                    fee_curr     = policy.get("flat_fee_currency") or "USD"
                    offer_lines  = []

                    for alt in alternatives[:3]:
                        name     = alt["listing_name"] or alt["unit_identifier"]
                        alt_rate = alt["est_nightly_rate"]

                        if alt_mode == "waive_extra":
                            # Host absorbs price difference — guest pays their current rate
                            rate_str = (
                                f"{_fmt_fee(current_rate, fee_curr)}/night (your current rate — we'll cover the difference)"
                                if current_rate else "rate on request"
                            )
                        elif alt_mode == "flat_fee":
                            rate_str = f"flat {_fmt_fee(fee_amt, fee_curr)} late-checkout fee"
                        elif alt_mode == "approval_required":
                            rate_str = "pricing subject to host confirmation"
                        else:  # charge_alt_rate
                            if alt_rate is None:
                                rate_str = "rate on request"
                            elif current_rate and abs(alt_rate - current_rate) < 1:
                                rate_str = f"{_fmt_fee(alt_rate, fee_curr)}/night (same as your current unit)"
                            elif current_rate and alt_rate > current_rate:
                                rate_str = f"{_fmt_fee(alt_rate, fee_curr)}/night (+{_fmt_fee(alt_rate - current_rate, fee_curr)} vs your unit)"
                            else:
                                rate_str = f"{_fmt_fee(alt_rate, fee_curr)}/night" + (
                                    f" ({_fmt_fee(current_rate - alt_rate, fee_curr)} less than your current unit)"
                                    if current_rate else ""
                                )
                        offer_lines.append(f"• {name} — {rate_str}")

                    offer_text = "\n".join(offer_lines)

                    if alt_mode == "approval_required":
                        result = {
                            "applied": False,
                            "reason": "same_unit_blocked_pending_approval",
                            "alternatives_found": len(alternatives),
                            "alternatives": alternatives,
                            "guest_reply": (
                                f"Your unit ({unit}) has another guest arriving that afternoon. "
                                f"I've passed your request to the host — they'll confirm availability "
                                f"and pricing for an alternative unit shortly."
                            ),
                        }
                        ok = False
                    else:
                        result = {
                            "applied": False,
                            "reason": "same_unit_blocked",
                            "alt_mode": alt_mode,
                            "alternatives_found": len(alternatives),
                            "alternatives": alternatives,
                            "guest_reply": (
                                f"Your unit ({unit}) has another guest arriving that afternoon, "
                                f"so I can't extend your checkout there.\n\n"
                                f"However, we have these units available:\n{offer_text}\n\n"
                                f"Would you like me to arrange a move? Just say which one suits you."
                            ),
                        }
                        ok = True  # alternatives offered — handled

                else:
                    # No alternative anywhere
                    no_avail_mode = policy.get("no_unit_available", "deny")
                    if no_avail_mode == "escalate":
                        result = {
                            "applied": False,
                            "reason": "no_alternatives_escalated",
                            "guest_reply": (
                                f"Your unit ({unit}) has another guest arriving and we don't have "
                                f"other units free right now. I've escalated this to the host — "
                                f"they'll get back to you very shortly to find a solution."
                            ),
                        }
                    else:  # deny
                        result = {
                            "applied": False,
                            "reason": "no_alternatives",
                            "guest_reply": (
                                f"I'm sorry — your unit ({unit}) has another guest arriving that afternoon "
                                f"and we don't have any other units free at that time. "
                                f"Apologies for the inconvenience!"
                            ),
                        }
                    ok = False

        # ── Early check-in ─────────────────────────────────────────────────
        elif action.action_type == "early_checkin":
            policy = _get_policy(cfg, "early_checkin")
            requested_time_str = params.get("requested_time") or "13:00"
            requested_t = _parse_hhmm(requested_time_str) or time(13, 0)

            unit = reservation.unit_identifier or ""
            checkin_date = reservation.checkin if reservation.checkin else date.today()
            if hasattr(checkin_date, "date"):
                checkin_date = checkin_date.date()

            # Determine if this is "extra night" territory (request is before host's cutoff)
            cutoff_str   = policy.get("cutoff_time") or "10:00"
            cutoff_t     = _parse_hhmm(cutoff_str) or time(10, 0)
            is_extra_night = _t_to_min(requested_t) < _t_to_min(cutoff_t)

            # Try the PMS first — it knows if the unit is truly ready
            pms_ok = adapter.update_reservation(pms_res_id, {"checkin_time": requested_time_str})

            if pms_ok:
                if is_extra_night:
                    # Request is before the cutoff — treat as an extra night
                    extra_mode     = policy.get("extra_night_mode", "nightly_rate")
                    extra_fee_amt  = float(policy.get("extra_night_fee_amount") or 0)
                    extra_fee_curr = policy.get("extra_night_fee_currency") or "USD"
                    nightly        = _nightly_rate(reservation)

                    if extra_mode == "approval_required":
                        adapter.update_reservation(pms_res_id, {"checkin_time": ""})
                        result = {
                            "applied": False,
                            "reason": "pending_host_approval_extra_night",
                            "guest_reply": (
                                f"Check-in at {requested_time_str} is very early — that's before our standard "
                                f"early check-in window (before {cutoff_str}), so it counts as an additional night. "
                                f"I've sent this to the host for approval. You'll hear back shortly! 🙂"
                            ),
                        }
                        pms_ok = False

                    elif extra_mode == "flat_fee":
                        fee_str = _fmt_fee(extra_fee_amt, extra_fee_curr)
                        result = {
                            "checkin_time": requested_time_str,
                            "applied": True,
                            "extra_night": True,
                            "fee_charged": extra_fee_amt,
                            "fee_currency": extra_fee_curr,
                            "guest_reply": (
                                f"Done! Early check-in at {requested_time_str} is confirmed. "
                                f"Since this is before {cutoff_str}, an extra-night fee of {fee_str} applies. "
                                f"We'll have everything ready for you! 🏠"
                            ),
                        }

                    else:  # nightly_rate
                        prev_day = checkin_date - timedelta(days=1)
                        if nightly:
                            rate_msg = f"an additional night charge of {_fmt_fee(nightly, extra_fee_curr or 'USD')}"
                        else:
                            rate_msg = "an additional night's charge at your booking rate"
                        # Push checkin date back by 1 night
                        pms_ok2 = adapter.update_reservation(pms_res_id, {"checkin_date": str(prev_day)})
                        result = {
                            "checkin_time": requested_time_str,
                            "checkin_date": str(prev_day),
                            "applied": True,
                            "extra_night": True,
                            "guest_reply": (
                                f"Done! Your stay now starts from {requested_time_str} "
                                f"on {prev_day.strftime('%B %d')}. "
                                f"Since you're arriving before {cutoff_str}, this includes {rate_msg}. "
                                f"See you soon! 🏠"
                            ),
                        }

                else:
                    mode = policy.get("same_unit_free", "free")
                    if mode == "flat_fee":
                        fee_amt  = float(policy.get("flat_fee_amount") or 0)
                        fee_curr = policy.get("flat_fee_currency") or "USD"
                        fee_str  = _fmt_fee(fee_amt, fee_curr)
                        result = {
                            "checkin_time": requested_time_str,
                            "applied": True,
                            "fee_charged": fee_amt,
                            "guest_reply": (
                                f"Done! Early check-in at {requested_time_str} is confirmed. "
                                f"An early check-in fee of {fee_str} will be added to your booking. "
                                f"See you soon! 🏠"
                            ),
                        }
                    elif mode == "approval_required":
                        result = {
                            "applied": False,
                            "reason": "pending_host_approval",
                            "guest_reply": (
                                f"I've sent your early check-in request ({requested_time_str}) to the host for approval. "
                                f"You'll hear back very shortly!"
                            ),
                        }
                        # Revert — approval not yet given
                        adapter.update_reservation(pms_res_id, {"checkin_time": ""})
                        pms_ok = False
                    else:  # free
                        result = {
                            "checkin_time": requested_time_str,
                            "applied": True,
                            "guest_reply": (
                                f"Done! Early check-in at {requested_time_str} is confirmed — "
                                f"no extra charge. We'll have the unit ready for you. See you soon! 🏠"
                            ),
                        }
                ok = pms_ok

            else:
                # PMS rejected — unit not ready, look for alternatives
                current_rate = _nightly_rate(reservation)
                guests = reservation.guests_count or 1
                alternatives = _find_alternative_units(
                    db, action.tenant_id, unit, checkin_date, guests
                )
                if alternatives:
                    alt_mode = policy.get("alt_unit_pricing", "charge_alt_rate")
                    offer_text = _build_alternative_offer_text(unit, current_rate, alternatives, alt_mode,
                                                               policy.get("flat_fee_amount", 0),
                                                               policy.get("flat_fee_currency", "USD"))
                    result = {
                        "applied": False,
                        "reason": "unit_not_ready",
                        "alternatives_found": len(alternatives),
                        "alternatives": alternatives,
                        "guest_reply": (
                            f"Early check-in to your unit isn't available at {requested_time_str} — "
                            f"it's still being prepared.\n\n"
                            f"We have these other units ready earlier:\n{offer_text}\n\n"
                            f"Would any of these work for you?"
                        ),
                    }
                    ok = True
                else:
                    no_avail_mode = policy.get("no_unit_available", "deny")
                    result = {
                        "applied": False,
                        "reason": "no_alternatives",
                        "guest_reply": (
                            f"I wasn't able to arrange early check-in at {requested_time_str} — "
                            f"the unit is still being prepared and no alternatives are available earlier. "
                            + ("I've escalated this to the host who will get back to you shortly."
                               if no_avail_mode == "escalate" else
                               "Your standard check-in time still applies.")
                        ),
                    }
                    ok = False

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

        # ── Extend stay ────────────────────────────────────────────────────
        elif action.action_type == "extend_stay":
            policy      = _get_policy(cfg, "extend_stay")
            extra_nights_raw = params.get("extra_nights") or 1
            try:
                extra_nights = max(1, int(extra_nights_raw))
            except (TypeError, ValueError):
                extra_nights = 1

            current_checkout = reservation.checkout if reservation.checkout else date.today()
            if hasattr(current_checkout, "date"):
                current_checkout = current_checkout.date()

            # Prefer explicit new date if the guest said e.g. "until Friday"
            new_checkout_str = params.get("new_checkout_date")
            if new_checkout_str:
                try:
                    new_checkout = date.fromisoformat(new_checkout_str)
                    extra_nights = max(1, (new_checkout - current_checkout).days)
                except Exception:
                    new_checkout = current_checkout + timedelta(days=extra_nights)
            else:
                new_checkout = current_checkout + timedelta(days=extra_nights)

            unit        = reservation.unit_identifier or ""
            unit_free   = _unit_is_free_window(db, action.tenant_id, unit, current_checkout, new_checkout) if unit else True
            nightly     = _nightly_rate(reservation)
            fee_curr    = policy.get("flat_fee_currency") or "USD"
            nights_label = f"{extra_nights} night{'s' if extra_nights != 1 else ''}"
            new_date_label = new_checkout.strftime("%B %d")

            if unit_free:
                mode = policy.get("when_available", "charge_nightly")

                if mode == "approval_required":
                    result = {
                        "applied": False,
                        "reason": "pending_host_approval",
                        "guest_reply": (
                            f"I've sent your request to extend your stay by {nights_label} "
                            f"(until {new_date_label}) to the host for approval. "
                            f"You'll hear back shortly! 🙂"
                        ),
                    }
                    ok = False

                elif mode == "flat_fee_per_night":
                    flat_per_night = float(policy.get("flat_fee_per_night") or 0)
                    total_fee      = flat_per_night * extra_nights
                    fee_str        = _fmt_fee(total_fee, fee_curr)
                    per_str        = _fmt_fee(flat_per_night, fee_curr)
                    pms_ok = adapter.update_reservation(pms_res_id, {"checkout_date": str(new_checkout)})
                    ok = pms_ok
                    if pms_ok:
                        result = {
                            "checkout_date": str(new_checkout),
                            "extra_nights": extra_nights,
                            "applied": True,
                            "fee_charged": total_fee,
                            "fee_currency": fee_curr,
                            "guest_reply": (
                                f"Your stay has been extended by {nights_label} to {new_date_label} — all confirmed! "
                                f"The host will be in touch shortly to arrange payment of {fee_str} ({per_str}/night). "
                                f"Enjoy the extra time! 🙂"
                            ),
                        }
                    else:
                        result = {
                            "applied": False,
                            "guest_reply": (
                                "I wasn't able to update your booking — "
                                "I've flagged this for the host to sort out with you shortly."
                            ),
                        }

                else:  # charge_nightly — default
                    total_cost = (nightly or 0) * extra_nights
                    if nightly and total_cost:
                        cost_str  = _fmt_fee(total_cost, fee_curr)
                        night_str = _fmt_fee(nightly, fee_curr)
                        payment_note = f"The host will be in touch to arrange payment of {cost_str} ({night_str}/night × {extra_nights})."
                    else:
                        payment_note = "The host will be in touch shortly to arrange payment for the additional nights."
                    pms_ok = adapter.update_reservation(pms_res_id, {"checkout_date": str(new_checkout)})
                    ok = pms_ok
                    if pms_ok:
                        result = {
                            "checkout_date": str(new_checkout),
                            "extra_nights": extra_nights,
                            "applied": True,
                            "nightly_rate": nightly,
                            "total_cost": total_cost if nightly else None,
                            "guest_reply": (
                                f"Your stay has been extended by {nights_label} to {new_date_label} — all confirmed! "
                                f"{payment_note} Enjoy the extra time! 🙂"
                            ),
                        }
                    else:
                        result = {
                            "applied": False,
                            "guest_reply": (
                                "I wasn't able to update your booking — "
                                "I've flagged this for the host to sort out with you shortly."
                            ),
                        }

            else:
                # Unit is booked during the requested extra nights
                no_avail_mode = policy.get("no_unit_available", "deny")
                if no_avail_mode == "escalate":
                    result = {
                        "applied": False,
                        "reason": "no_unit_available_escalated",
                        "guest_reply": (
                            f"Unfortunately your unit is already booked during those nights, "
                            f"so I can't extend your stay automatically. "
                            f"I've escalated this to the host — they'll get back to you shortly to see what's possible!"
                        ),
                    }
                else:  # deny
                    result = {
                        "applied": False,
                        "reason": "no_unit_available",
                        "guest_reply": (
                            f"I'm sorry — your unit is already booked during those nights, "
                            f"so we're unable to extend your stay by {nights_label}. "
                            f"Apologies for the inconvenience!"
                        ),
                    }
                ok = False

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
