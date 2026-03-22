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

import json
import logging
import threading
from datetime import datetime, timezone, timedelta
from typing import Optional

from web.db import SessionLocal
from web.models import (
    Draft, ActivityLog, Reservation, PMSIntegration, PMSProcessedMessage,
    AutomationRule, GuestTimelineEvent,
)
from web.classifier import (
    classify_message_with_confidence, extract_context_sources,
    detect_vendor_type, generate_draft,
    make_draft_id, needs_escalation, build_property_context,
)
from web.crypto import decrypt
from web.pms_base import make_adapter, PMSMessage
from web.workflow import (
    analyze_guest_sentiment,
    automation_rule_decision,
    build_conversation_memory,
    build_thread_key,
    compute_guest_history_score,
    compute_stay_stage,
    draft_policy_conflicts,
)

log = logging.getLogger(__name__)

_POLL_INTERVAL = 60   # seconds between polls
_MAX_BACKOFF   = 600  # 10 minutes max


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------

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
            "anthropic_api_key":   decrypt(cfg.anthropic_api_key_enc),
            "property_context":    build_property_context(cfg),
            "escalation_email":    cfg.escalation_email or cfg.email_address or "",
            # Policy fields passed through for conflict checks
            "pet_policy":          cfg.pet_policy or "",
            "refund_policy":       cfg.refund_policy or "",
            "early_checkin_policy": cfg.early_checkin_policy or "",
            "early_checkin_fee":   cfg.early_checkin_fee or "",
            "late_checkout_policy": cfg.late_checkout_policy or "",
            "late_checkout_fee":   cfg.late_checkout_fee or "",
            "parking_policy":      cfg.parking_policy or "",
            "smoking_policy":      cfg.smoking_policy or "",
            "integrations": [
                {
                    "id":          i.id,
                    "pms_type":    i.pms_type,
                    "api_key":     decrypt(i.api_key_enc),
                    "account_id":  i.account_id or "",
                    "base_url":    i.api_base_url or "",
                    "last_synced": i.last_synced_at,
                }
                for i in integrations
            ],
        }
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Deduplication helpers
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Reservation helpers
# ---------------------------------------------------------------------------

def _lookup_reservation_row(db, tenant_id: str, guest_name: str) -> Optional[Reservation]:
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
            return r
    return None


def _build_reservation_context(reservation: Reservation, cfg: dict) -> str:
    """Build a rich reservation context string including stay stage and policy fields."""
    lines = [f"Reservation: {reservation.confirmation_code}"]
    if reservation.guest_phone:
        lines.append(f"Guest phone: {reservation.guest_phone}")
    if reservation.listing_name:
        lines.append(f"Property: {reservation.listing_name}")
    if reservation.unit_identifier:
        lines.append(f"Room / unit / property #: {reservation.unit_identifier}")
    if reservation.checkin:
        lines.append(f"Check-in: {reservation.checkin.strftime('%A, %B %d, %Y')}")
    if reservation.checkout:
        lines.append(f"Check-out: {reservation.checkout.strftime('%A, %B %d, %Y')}")
    if reservation.nights:
        lines.append(f"Nights: {reservation.nights}")
    if reservation.guests_count:
        lines.append(f"Guests: {reservation.guests_count}")

    today = datetime.now(timezone.utc).date()
    stay_stage = compute_stay_stage(reservation, today=today)
    if stay_stage:
        lines.append(f"Stay stage: {stay_stage.replace('_', ' ')}")
    if (reservation.checkin and reservation.checkout and reservation.nights
            and reservation.checkin <= today <= reservation.checkout):
        day_of_stay = (today - reservation.checkin).days + 1
        lines.append(f"Guest is on day {day_of_stay} of {reservation.nights} nights.")

    if cfg.get("early_checkin_policy"):
        early = f"Early check-in: {cfg['early_checkin_policy']}"
        if cfg.get("early_checkin_fee"):
            early += f" (fee: {cfg['early_checkin_fee']})"
        lines.append(early)
    if cfg.get("late_checkout_policy"):
        late = f"Late checkout: {cfg['late_checkout_policy']}"
        if cfg.get("late_checkout_fee"):
            late += f" (fee: {cfg['late_checkout_fee']})"
        lines.append(late)
    if cfg.get("pet_policy"):
        lines.append(f"Pet policy: {cfg['pet_policy']}")
    if cfg.get("refund_policy"):
        lines.append(f"Refund policy: {cfg['refund_policy']}")
    return "\n".join(lines)


def _timeline_memory(db, tenant_id: str, reservation: Optional[Reservation]) -> str:
    if not reservation:
        return ""
    events = (
        db.query(GuestTimelineEvent)
        .filter_by(tenant_id=tenant_id, reservation_id=reservation.id)
        .order_by(GuestTimelineEvent.created_at.desc())
        .limit(8)
        .all()
    )
    return build_conversation_memory(reversed(events), limit=8)


def _recent_reservation_drafts(db, tenant_id: str, reservation: Optional[Reservation]) -> list:
    if not reservation:
        return []
    return (
        db.query(Draft)
        .filter(Draft.tenant_id == tenant_id, Draft.reservation_id == reservation.id)
        .order_by(Draft.created_at.desc())
        .limit(12)
        .all()
    )


def _thread_metadata(db, tenant_id: str, reservation: Optional[Reservation],
                     msg: PMSMessage) -> tuple:
    thread_key = build_thread_key(
        tenant_id,
        reservation_id=reservation.id if reservation else None,
        reply_to=str(msg.reservation_id),
        guest_name=msg.guest_name,
        channel="pms",
    )
    parent = (
        db.query(Draft)
        .filter(Draft.tenant_id == tenant_id, Draft.thread_key == thread_key)
        .order_by(Draft.created_at.desc())
        .first()
    )
    return (
        thread_key,
        parent.id if parent else None,
        (parent.guest_message_index + 1) if parent else 1,
    )


# ---------------------------------------------------------------------------
# Draft / timeline persistence
# ---------------------------------------------------------------------------

def _save_draft(db, tenant_id: str, draft_id: str, msg: PMSMessage,
                msg_type: str, vendor_type: Optional[str], draft_text: str,
                integration_id: int,
                reservation: Optional[Reservation] = None,
                automation_rule_id: Optional[int] = None,
                confidence: Optional[float] = None,
                context_sources: Optional[list] = None,
                parent_draft_id: Optional[str] = None,
                thread_key: Optional[str] = None,
                guest_message_index: int = 1,
                property_name_snapshot: Optional[str] = None,
                unit_identifier_snapshot: Optional[str] = None,
                auto_send_eligible: bool = False,
                guest_history_score: Optional[float] = None,
                guest_sentiment: Optional[str] = None,
                sentiment_score: Optional[float] = None,
                stay_stage: Optional[str] = None,
                policy_conflicts: Optional[list] = None):
    db.add(Draft(
        id=draft_id,
        tenant_id=tenant_id,
        source="pms",
        reservation_id=reservation.id if reservation else None,
        automation_rule_id=automation_rule_id,
        parent_draft_id=parent_draft_id,
        thread_key=thread_key,
        guest_message_index=guest_message_index,
        guest_name=msg.guest_name,
        message=msg.text,
        reply_to=f"{integration_id}:{msg.reservation_id}",
        msg_type=msg_type,
        vendor_type=vendor_type,
        draft=draft_text,
        status="pending",
        created_at=datetime.now(timezone.utc),
        property_name_snapshot=property_name_snapshot,
        unit_identifier_snapshot=unit_identifier_snapshot,
        confidence=confidence,
        auto_send_eligible=auto_send_eligible,
        guest_history_score=guest_history_score,
        guest_sentiment=guest_sentiment,
        sentiment_score=sentiment_score,
        stay_stage=stay_stage,
        policy_conflicts_json=json.dumps(policy_conflicts) if policy_conflicts else None,
        context_sources=json.dumps(context_sources) if context_sources else None,
    ))
    db.add(ActivityLog(
        tenant_id=tenant_id,
        event_type="pms_message_received",
        message=f"PMS message from {msg.guest_name} — {msg_type} ({msg.channel})",
    ))


def _record_timeline_event(db, tenant_id: str, reservation: Optional[Reservation],
                           event_type: str, summary: str, *,
                           direction: str = "internal",
                           body: str = "",
                           draft_id: Optional[str] = None,
                           automation_rule_id: Optional[int] = None):
    db.add(GuestTimelineEvent(
        tenant_id=tenant_id,
        reservation_id=reservation.id if reservation else None,
        draft_id=draft_id,
        automation_rule_id=automation_rule_id,
        guest_name=reservation.guest_name if reservation else None,
        guest_phone=reservation.guest_phone if reservation else None,
        property_name=reservation.listing_name if reservation else None,
        unit_identifier=reservation.unit_identifier if reservation else None,
        channel="pms",
        direction=direction,
        event_type=event_type,
        summary=summary,
        body=body or None,
    ))


# ---------------------------------------------------------------------------
# Core message handler
# ---------------------------------------------------------------------------

def _process_message(tenant_id: str, cfg: dict, msg: PMSMessage,
                     integration_id: int, adapter):
    db = SessionLocal()
    try:
        guest_msg = msg.text
        if not guest_msg.strip():
            return

        # ── 1. Escalation check ──────────────────────────────────────────
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

        # ── 2. Classify + sentiment ──────────────────────────────────────
        msg_type, confidence, matched_patterns = classify_message_with_confidence(guest_msg)
        vendor_type = detect_vendor_type(guest_msg) if msg_type == "complex" else None
        
        from web import classifier as classifier_mod
        sentiment = classifier_mod.analyze_sentiment_and_intent_llm(tenant_id, guest_msg)

        # ── 3. Reservation lookup + context ─────────────────────────────
        reservation = _lookup_reservation_row(db, tenant_id, msg.guest_name)
        full_ctx = cfg["property_context"]
        if reservation:
            full_ctx = (
                full_ctx
                + "\n\n<reservation>\n"
                + _build_reservation_context(reservation, cfg)
                + "\n</reservation>"
            ).strip()
            log.info("[%s] PMS reservation match for %s", tenant_id, msg.guest_name)
        memory_context = _timeline_memory(db, tenant_id, reservation)
        if memory_context:
            full_ctx = (
                full_ctx
                + "\n\n<recent_guest_history>\n"
                + memory_context
                + "\n</recent_guest_history>"
            ).strip()

        # ── 4. Generate draft ────────────────────────────────────────────
        try:
            draft_text = generate_draft(
                cfg["anthropic_api_key"], msg.guest_name, guest_msg, msg_type,
                property_context=full_ctx,
            )
        except RuntimeError as exc:
            log.error("[%s] PMS draft generation failed: %s", tenant_id, exc)
            return

        # ── 5. Thread metadata ───────────────────────────────────────────
        thread_key, parent_draft_id, guest_message_index = _thread_metadata(
            db, tenant_id, reservation, msg
        )

        # ── 6. Guest intelligence signals ────────────────────────────────
        recent_drafts     = _recent_reservation_drafts(db, tenant_id, reservation)
        guest_history_score = compute_guest_history_score(reservation, recent_drafts)
        stay_stage        = compute_stay_stage(reservation)
        policy_conflicts  = draft_policy_conflicts(guest_msg, draft_text, cfg)

        # ── 7. 4-guard auto-send eligibility ─────────────────────────────
        auto_send_eligible = (
            msg_type == "routine"
            and confidence >= 0.7
            and sentiment["label"] != "negative"
            and not policy_conflicts
            and guest_history_score >= 0.4
        )

        # ── 8. Automation rules override ─────────────────────────────────
        automation_rule_id = None
        if reservation:
            rules = (
                db.query(AutomationRule)
                .filter_by(tenant_id=tenant_id, is_active=True)
                .order_by(AutomationRule.priority.asc(), AutomationRule.created_at.asc())
                .all()
            )
            draft_view = {
                "status":             "pending",
                "source":             "pms",
                "channel":            "pms",
                "msg_type":           msg_type,
                "message":            guest_msg,
                "draft":              draft_text,
                "listing_name":       reservation.listing_name or "",
                "property_name":      reservation.listing_name or "",
                "reply_to":           f"{integration_id}:{msg.reservation_id}",
                "confidence":         confidence,
                "guest_history_score": guest_history_score,
                "guest_sentiment":    sentiment["label"],
                "sentiment_score":    sentiment["score"],
                "stay_stage":         stay_stage,
                "policy_conflicts":   policy_conflicts,
            }
            for rule in rules:
                conditions = rule.conditions_json or {}
                decision = automation_rule_decision(
                    {
                        "enabled":                  rule.is_active,
                        "status":                   "active" if rule.is_active else "disabled",
                        "channels":                 [rule.channel] if rule.channel != "any" else [],
                        "msg_types":                conditions.get("msg_types") or [],
                        "min_confidence":           rule.confidence_threshold,
                        "properties":               conditions.get("properties") or [],
                        "allow_complex":            conditions.get("allow_complex", False),
                        "allow_negative_sentiment": conditions.get("allow_negative_sentiment", False),
                        "min_guest_history_score":  conditions.get("min_guest_history_score"),
                        "stay_stages":              conditions.get("stay_stages") or [],
                        "requires_approval":        (rule.actions_json or {}).get("mode") == "review",
                    },
                    draft_view,
                )
                if decision["should_send"]:
                    automation_rule_id = rule.id
                    auto_send_eligible = True
                    break

        # ── 9. Persist draft ─────────────────────────────────────────────
        ctx_sources = matched_patterns[:]
        from web.models import TenantConfig as _TC
        _tenant_cfg = db.query(_TC).filter_by(tenant_id=tenant_id).first()
        if _tenant_cfg:
            ctx_sources += extract_context_sources(_tenant_cfg)

        draft_id = make_draft_id("pms")
        _save_draft(
            db, tenant_id, draft_id, msg, msg_type, vendor_type, draft_text, integration_id,
            reservation=reservation,
            automation_rule_id=automation_rule_id,
            confidence=confidence,
            context_sources=ctx_sources,
            parent_draft_id=parent_draft_id,
            thread_key=thread_key,
            guest_message_index=guest_message_index,
            property_name_snapshot=reservation.listing_name if reservation else None,
            unit_identifier_snapshot=reservation.unit_identifier if reservation else None,
            auto_send_eligible=auto_send_eligible,
            guest_history_score=guest_history_score,
            guest_sentiment=sentiment["label"],
            sentiment_score=sentiment["score"],
            stay_stage=stay_stage,
            policy_conflicts=policy_conflicts,
        )
        if reservation:
            _record_timeline_event(
                db, tenant_id, reservation,
                "guest_message_received",
                f"PMS message from {msg.guest_name}",
                direction="inbound",
                body=guest_msg,
                draft_id=draft_id,
                automation_rule_id=automation_rule_id,
            )
        _mark_processed(db, tenant_id, integration_id, msg.message_id)
        db.commit()

        # ── 10. Update reservation counters + sentiment ──────────────────
        if reservation:
            live_res = db.query(Reservation).filter_by(
                id=reservation.id, tenant_id=tenant_id
            ).first()
            if live_res:
                live_res.last_guest_message_at = datetime.now(timezone.utc)
                live_res.message_count = (live_res.message_count or 0) + 1
                live_res.latest_guest_sentiment = sentiment["label"]
                live_res.latest_guest_sentiment_score = sentiment["score"]
                db.commit()

        # ── 11. Auto-send via PMS ────────────────────────────────────────
        if auto_send_eligible:
            ok = adapter.send_message(msg.reservation_id, draft_text)
            if ok:
                draft = db.query(Draft).filter_by(id=draft_id).first()
                if draft:
                    draft.status      = "approved"
                    draft.final_text  = draft_text
                    draft.approved_at = datetime.now(timezone.utc)
                    if reservation:
                        live_res = db.query(Reservation).filter_by(
                            id=reservation.id, tenant_id=tenant_id
                        ).first()
                        if live_res:
                            live_res.last_host_reply_at = draft.approved_at
                    db.commit()
                if reservation:
                    _record_timeline_event(
                        db, tenant_id, reservation,
                        "draft_approved",
                        f"PMS routine reply auto-sent to {msg.guest_name}",
                        direction="outbound",
                        body=draft_text,
                        draft_id=draft_id,
                        automation_rule_id=automation_rule_id,
                    )
                    db.commit()
                log.info("[%s] PMS routine reply auto-sent to %s", tenant_id, msg.guest_name)
            else:
                log.error("[%s] PMS auto-send failed for %s — draft kept pending",
                          tenant_id, msg.guest_name)
        elif msg_type == "routine":
            if policy_conflicts:
                log.info("[%s] PMS draft %s — policy conflict, needs review", tenant_id, draft_id)
            elif sentiment["label"] == "negative":
                log.info("[%s] PMS draft %s — negative sentiment, needs review", tenant_id, draft_id)
            else:
                log.info("[%s] PMS draft %s — auto-send threshold not met", tenant_id, draft_id)
        else:
            log.info("[%s] PMS complex draft %s — awaiting host approval", tenant_id, draft_id)
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Poll loop
# ---------------------------------------------------------------------------

def _poll_integration(tenant_id: str, cfg: dict, int_cfg: dict):
    """Poll one PMS integration for new messages."""
    adapter = make_adapter(
        int_cfg["pms_type"],
        int_cfg["api_key"],
        int_cfg["account_id"],
        int_cfg["base_url"],
    )
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
