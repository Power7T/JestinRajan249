# © 2024 Jestin Rajan. All rights reserved.
"""
Workflow helpers for HostAI.

This module is intentionally pure or mostly pure so the app layer can reuse it
for dashboard metrics, onboarding readiness, guest timeline formatting, rule
evaluation, and exception surfacing without pulling in request or DB context.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping, Optional, Sequence


_AUTO_SEND_STATUSES = {"active", "enabled", "on", "ready"}
_FAILURE_STATUSES = {"failed", "error", "errored", "bounced", "rejected"}
_PENDING_STATUSES = {"pending", "queued", "waiting", "needs_review"}
_ESCALATION_TYPES = {"escalation", "complex"}


def _value(item: Any, key: str, default: Any = None) -> Any:
    if isinstance(item, Mapping):
        return item.get(key, default)
    return getattr(item, key, default)


def _text(item: Any, key: str, default: str = "") -> str:
    value = _value(item, key, default)
    if value is None:
        return default
    return str(value)


def _bool(item: Any, key: str, default: bool = False) -> bool:
    value = _value(item, key, default)
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on", "active", "enabled"}


def _number(item: Any, key: str, default: Optional[float] = None) -> Optional[float]:
    value = _value(item, key, default)
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _dt(value: Any) -> Optional[datetime]:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _date_from_any(value: Any) -> Optional[datetime.date]:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if hasattr(value, "isoformat") and not isinstance(value, str):
        return value
    text = str(value).strip()
    if not text:
        return None
    if "T" in text or " " in text:
        dt = _dt(text)
        return dt.date() if dt else None
    try:
        return datetime.fromisoformat(text).date()
    except ValueError:
        return None


def _listify(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        parts = value.replace("\n", ",").replace(";", ",").split(",")
        return [part.strip() for part in parts if part.strip()]
    if isinstance(value, Sequence):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()] if str(value).strip() else []


def _matches_any(value: str, candidates: Sequence[str]) -> bool:
    target = value.strip().lower()
    return any(candidate.strip().lower() in target for candidate in candidates if candidate)


def _normalize_confidence(value: Any) -> float:
    if value is None or value == "":
        return 0.0
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if number > 1:
        return min(number / 100.0, 1.0)
    return max(0.0, min(number, 1.0))


def _sort_key(item: Any) -> tuple[datetime, str]:
    stamp = _dt(_value(item, "created_at") or _value(item, "timestamp") or _value(item, "time"))
    if stamp is None:
        stamp = datetime.now(timezone.utc)
    label = _text(item, "event_type") or _text(item, "type") or _text(item, "channel")
    return stamp, label


def build_guest_timeline(events: Iterable[Any], limit: int = 50) -> list[dict[str, Any]]:
    """
    Normalize raw timeline objects/dicts into a consistent chronological list.
    """
    normalized: list[dict[str, Any]] = []
    for event in events or []:
        stamp = _dt(_value(event, "created_at") or _value(event, "timestamp") or _value(event, "time"))
        normalized.append(
            {
                "created_at": stamp,
                "channel": _text(event, "channel") or _text(event, "source"),
                "actor": _text(event, "actor") or _text(event, "direction"),
                "event_type": _text(event, "event_type") or _text(event, "type"),
                "message": _text(event, "summary") or _text(event, "message") or _text(event, "text"),
                "summary": _text(event, "summary") or _text(event, "message") or _text(event, "text"),
                "detail": _text(event, "detail") or _text(event, "body") or _text(event, "status"),
                "status": _text(event, "body") or _text(event, "detail") or _text(event, "status"),
                "guest_name": _text(event, "guest_name"),
                "reservation_code": _text(event, "reservation_code") or _text(event, "confirmation_code"),
                "room": _text(event, "room") or _text(event, "unit_identifier"),
                "metadata": _value(event, "payload_json") or _value(event, "metadata", {}) or {},
            }
        )
    normalized.sort(key=_sort_key)
    if limit > 0:
        normalized = normalized[-limit:]
    return normalized


def build_conversation_memory(events: Iterable[Any], limit: int = 12, max_chars: int = 3000) -> str:
    """
    Convert timeline items into a concise prompt-ready memory block.
    """
    timeline = build_guest_timeline(events, limit=limit)
    if not timeline:
        return ""

    lines: list[str] = []
    for item in timeline:
        stamp = item["created_at"]
        stamp_text = stamp.strftime("%Y-%m-%d %H:%M UTC") if stamp else "unknown time"
        label_bits = [bit for bit in [item["actor"], item["channel"], item["event_type"]] if bit]
        label = " / ".join(label_bits) if label_bits else "event"
        body = item["detail"] or item["message"] or item["status"] or ""
        extras = []
        if item["reservation_code"]:
            extras.append(f"booking={item['reservation_code']}")
        if item["room"]:
            extras.append(f"room={item['room']}")
        if extras:
            body = f"{body} ({', '.join(extras)})" if body else ", ".join(extras)
        lines.append(f"[{stamp_text}] {label}: {body}".strip())

    memory = "\n".join(lines).strip()
    if len(memory) <= max_chars:
        return memory
    return memory[-max_chars:]


def derive_dashboard_kpis(
    drafts: Iterable[Any],
    reservations: Iterable[Any],
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    """
    Derive simple operational KPIs from current drafts and reservations.
    """
    now = now or datetime.now(timezone.utc)
    today = now.date()

    draft_rows = list(drafts or [])
    reservation_rows = list(reservations or [])

    pending = 0
    approved = 0
    skipped = 0
    routine = 0
    complex_ = 0
    escalations = 0
    overdue_pending = 0

    for draft in draft_rows:
        status = _text(draft, "status").lower()
        msg_type = _text(draft, "msg_type").lower()
        created_at = _dt(_value(draft, "created_at"))
        if status == "pending":
            pending += 1
            if created_at and (now - created_at) > timedelta(minutes=60):
                overdue_pending += 1
        elif status == "approved":
            approved += 1
        elif status == "skipped":
            skipped += 1
        if msg_type == "routine":
            routine += 1
        elif msg_type == "complex":
            complex_ += 1
        if msg_type in _ESCALATION_TYPES or status in {"escalation"}:
            escalations += 1

    confirmed = 0
    upcoming = 0
    active_stays = 0
    missing_guest_phone = 0
    missing_unit = 0
    context_complete = 0

    for reservation in reservation_rows:
        status = _text(reservation, "status").lower() or "confirmed"
        checkin = _date_from_any(_value(reservation, "checkin"))
        checkout = _date_from_any(_value(reservation, "checkout"))
        if status == "confirmed":
            confirmed += 1
        if checkin and checkin >= today and checkin <= today + timedelta(days=30):
            upcoming += 1
        if checkin and checkout and checkin <= today <= checkout:
            active_stays += 1
        guest_phone = _text(reservation, "guest_phone").strip()
        unit_identifier = _text(reservation, "unit_identifier").strip()
        if not guest_phone:
            missing_guest_phone += 1
        if not unit_identifier:
            missing_unit += 1
        if guest_phone and unit_identifier:
            context_complete += 1

    total_drafts = len(draft_rows)
    total_reservations = len(reservation_rows)
    approval_rate = round((approved / total_drafts) * 100, 1) if total_drafts else 0.0
    context_completion_rate = round((context_complete / total_reservations) * 100, 1) if total_reservations else 0.0
    automation_ready_ratio = round(((routine + approved) / total_drafts) * 100, 1) if total_drafts else 0.0

    return {
        "drafts": {
            "total": total_drafts,
            "pending": pending,
            "approved": approved,
            "skipped": skipped,
            "routine": routine,
            "complex": complex_,
            "escalations": escalations,
            "overdue_pending": overdue_pending,
            "approval_rate": approval_rate,
        },
        "reservations": {
            "total": total_reservations,
            "confirmed": confirmed,
            "upcoming_checkins": upcoming,
            "active_stays": active_stays,
            "missing_guest_phone": missing_guest_phone,
            "missing_unit_identifier": missing_unit,
            "context_complete": context_complete,
            "context_completion_rate": context_completion_rate,
        },
        "ops": {
            "automation_ready_ratio": automation_ready_ratio,
            "needs_attention": pending + escalations + missing_guest_phone + missing_unit,
        },
    }


def build_activation_checklist(
    config: Any,
    reservations: Iterable[Any] = (),
    inbound_email_address: str = "",
    inbound_webhook_url: str = "",
) -> list[dict[str, Any]]:
    """
    Build a step-by-step activation checklist for the onboarding/dashboard UX.
    """
    reservation_rows = list(reservations or [])
    property_names = _text(config, "property_names").strip()
    house_rules = _text(config, "house_rules").strip()
    faq = _text(config, "faq").strip()
    custom_instructions = _text(config, "custom_instructions").strip()
    email_ingest_mode = _text(config, "email_ingest_mode").strip().lower()
    anthropic_key = _text(config, "anthropic_api_key_enc").strip()
    ical_urls = _text(config, "ical_urls").strip()
    escalation_email = _text(config, "escalation_email").strip()
    wa_mode = _text(config, "wa_mode").strip().lower()
    sms_mode = _text(config, "sms_mode").strip().lower()

    reservations_with_context = [
        res for res in reservation_rows
        if _text(res, "guest_phone").strip() and _text(res, "unit_identifier").strip()
    ]

    items = [
        {
            "key": "property_profile",
            "label": "Property details added",
            "complete": bool(property_names),
            "detail": "Property name and basic stay information are configured.",
            "cta": "/onboarding?step=1",
        },
        {
            "key": "house_context",
            "label": "House rules and guest info loaded",
            "complete": bool(house_rules or faq or custom_instructions),
            "detail": "Host rules, FAQ, or special instructions are available for replies.",
            "cta": "/onboarding?step=2",
        },
        {
            "key": "calendar",
            "label": "Calendar connected",
            "complete": bool(ical_urls),
            "detail": "Airbnb iCal provides stay timing and upcoming arrivals.",
            "cta": "/onboarding?step=5",
        },
        {
            "key": "guest_context",
            "label": "Guest phone and room mapped",
            "complete": bool(reservations_with_context),
            "detail": "A reservation has both a guest phone and room/unit value.",
            "cta": "/reservations",
        },
        {
            "key": "email_intake",
            "label": "Email forwarding ready",
            "complete": email_ingest_mode == "forwarding" and bool(inbound_email_address) and bool(inbound_webhook_url),
            "detail": "Inbound email webhook and forwarding alias are ready.",
            "cta": "/settings",
        },
        {
            "key": "claude",
            "label": "Claude API key added",
            "complete": bool(anthropic_key),
            "detail": "Draft generation can run for guest replies.",
            "cta": "/settings",
        },
        {
            "key": "escalation",
            "label": "Escalation contact set",
            "complete": bool(escalation_email),
            "detail": "Serious guest issues have a human handoff path.",
            "cta": "/settings",
        },
        {
            "key": "channels",
            "label": "Guest chat channels connected",
            "complete": bool(wa_mode != "none" or sms_mode != "none"),
            "detail": "At least one outbound guest channel is configured.",
            "cta": "/settings",
        },
    ]
    return items


def automation_rule_decision(
    rule: Any,
    draft: Any,
    when: Optional[datetime] = None,
) -> dict[str, Any]:
    """
    Decide whether an automation rule should auto-send a draft.
    Returns a structured decision with a reason string.
    """
    when = when or datetime.now(timezone.utc)
    active = _bool(rule, "enabled", True) and _text(rule, "status").lower() in _AUTO_SEND_STATUSES | {""}
    if not active:
        return {"should_send": False, "reason": "rule disabled"}

    draft_status = _text(draft, "status").lower() or "pending"
    if draft_status != "pending":
        return {"should_send": False, "reason": f"draft status is {draft_status}"}

    if _bool(draft, "needs_escalation") or _text(draft, "msg_type").lower() in {"escalation"}:
        return {"should_send": False, "reason": "draft requires escalation"}

    allowed_channels = _listify(_value(rule, "channels") or _value(rule, "channel"))
    draft_channel = _text(draft, "channel") or _text(draft, "source")
    if allowed_channels and draft_channel and draft_channel.lower() not in {item.lower() for item in allowed_channels}:
        return {"should_send": False, "reason": f"channel {draft_channel} not allowed"}

    allowed_types = _listify(_value(rule, "msg_types") or _value(rule, "allowed_msg_types"))
    draft_type = _text(draft, "msg_type").lower()
    if allowed_types and draft_type and draft_type not in {item.lower() for item in allowed_types}:
        return {"should_send": False, "reason": f"message type {draft_type} not allowed"}

    if draft_type == "complex" and not _bool(rule, "allow_complex", False):
        return {"should_send": False, "reason": "complex drafts require review"}

    min_confidence = _number(rule, "min_confidence", None)
    if min_confidence is None:
        min_confidence = _number(rule, "confidence_threshold", None)
    if min_confidence is not None:
        draft_confidence = _normalize_confidence(_value(draft, "confidence"))
        if draft_confidence < min_confidence:
            return {
                "should_send": False,
                "reason": f"confidence {draft_confidence:.2f} below threshold {min_confidence:.2f}",
            }

    allowed_properties = _listify(_value(rule, "properties") or _value(rule, "property_names"))
    draft_property = _text(draft, "property_name") or _text(draft, "listing_name")
    if allowed_properties and draft_property and not _matches_any(draft_property, allowed_properties):
        return {"should_send": False, "reason": f"property {draft_property} not allowed"}

    block_keywords = _listify(_value(rule, "block_keywords"))
    draft_text = f"{_text(draft, 'message')} {_text(draft, 'draft')}".strip().lower()
    if block_keywords and any(keyword.lower() in draft_text for keyword in block_keywords):
        return {"should_send": False, "reason": "blocked keyword matched"}

    allow_keywords = _listify(_value(rule, "allow_keywords"))
    if allow_keywords and not any(keyword.lower() in draft_text for keyword in allow_keywords):
        return {"should_send": False, "reason": "required keyword not present"}

    allowed_days = _listify(_value(rule, "days_of_week") or _value(rule, "days"))
    if allowed_days:
        weekday = when.strftime("%a").lower()
        weekday_num = str(when.weekday())
        normalized_days = {item.strip().lower() for item in allowed_days}
        if weekday not in normalized_days and weekday_num not in normalized_days:
            return {"should_send": False, "reason": f"day {weekday} is not allowed"}

    start_hour = _number(rule, "start_hour", None)
    end_hour = _number(rule, "end_hour", None)
    if start_hour is not None and end_hour is not None:
        current_hour = when.hour + (when.minute / 60.0)
        if not (start_hour <= current_hour < end_hour):
            return {"should_send": False, "reason": "outside allowed hours"}

    if _bool(rule, "requires_approval", False):
        return {"should_send": False, "reason": "rule requires manual approval"}

    return {"should_send": True, "reason": "rule matched"}


def should_auto_send(rule: Any, draft: Any, when: Optional[datetime] = None) -> bool:
    """Boolean convenience wrapper around automation_rule_decision()."""
    return bool(automation_rule_decision(rule, draft, when=when)["should_send"])


def surface_exception_queue(
    drafts: Iterable[Any],
    reservations: Iterable[Any] = (),
    now: Optional[datetime] = None,
    stale_minutes: int = 60,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """
    Turn drafts/reservations into a small queue of operational exceptions.
    """
    now = now or datetime.now(timezone.utc)
    today = now.date()
    issues: list[dict[str, Any]] = []

    for draft in drafts or []:
        status = _text(draft, "status").lower()
        msg_type = _text(draft, "msg_type").lower()
        created_at = _dt(_value(draft, "created_at"))
        draft_id = _text(draft, "id")
        guest_name = _text(draft, "guest_name") or "Guest"

        if status in _FAILURE_STATUSES:
            issues.append({
                "kind": "failed_send",
                "severity": "high",
                "entity_type": "draft",
                "entity_id": draft_id,
                "title": f"Failed draft for {guest_name}",
                "detail": f"Draft status is {status or 'unknown'}",
            })
            continue

        if msg_type in _ESCALATION_TYPES or status == "escalation":
            issues.append({
                "kind": "escalation",
                "severity": "high",
                "entity_type": "draft",
                "entity_id": draft_id,
                "title": f"Escalation needed for {guest_name}",
                "detail": _text(draft, "message")[:200],
            })
            continue

        if status in _PENDING_STATUSES:
            age_minutes = None
            if created_at:
                age_minutes = (now - created_at).total_seconds() / 60.0
            if age_minutes is not None and age_minutes >= stale_minutes:
                issues.append({
                    "kind": "stale_pending",
                    "severity": "medium",
                    "entity_type": "draft",
                    "entity_id": draft_id,
                    "title": f"Pending draft waiting too long for {guest_name}",
                    "detail": f"Pending for {int(age_minutes)} minutes",
                })

        if status == "pending" and not _text(draft, "reply_to").strip():
            issues.append({
                "kind": "missing_reply_route",
                "severity": "medium",
                "entity_type": "draft",
                "entity_id": draft_id,
                "title": f"No reply route for {guest_name}",
                "detail": "Draft has no reply_to address or phone number.",
            })

    for reservation in reservations or []:
        status = _text(reservation, "status").lower() or "confirmed"
        checkin = _date_from_any(_value(reservation, "checkin"))
        guest_name = _text(reservation, "guest_name") or "Reservation"
        reservation_id = _text(reservation, "confirmation_code") or _text(reservation, "id")

        if status != "confirmed" or not checkin:
            continue

        days_to_checkin = (checkin - today).days
        if 0 <= days_to_checkin <= 7:
            if not _text(reservation, "guest_phone").strip():
                issues.append({
                    "kind": "missing_guest_phone",
                    "severity": "medium",
                    "entity_type": "reservation",
                    "entity_id": reservation_id,
                    "title": f"Guest phone missing for {guest_name}",
                    "detail": "HostAI cannot match inbound messages to this stay by phone.",
                })
            if not _text(reservation, "unit_identifier").strip():
                issues.append({
                    "kind": "missing_unit_mapping",
                    "severity": "medium",
                    "entity_type": "reservation",
                    "entity_id": reservation_id,
                    "title": f"Room / unit missing for {guest_name}",
                    "detail": "Add a room, unit, or property number for better guest replies.",
                })

    severity_order = {"high": 0, "medium": 1, "low": 2}
    issues.sort(
        key=lambda item: (
            severity_order.get(str(item.get("severity", "low")).lower(), 3),
            str(item.get("entity_type", "")),
            str(item.get("entity_id", "")),
        )
    )
    if limit > 0:
        issues = issues[:limit]
    return issues
