# © 2024 Jestin Rajan. All rights reserved.
"""
Workflow helpers for HostAI.

This module is intentionally pure or mostly pure so the app layer can reuse it
for dashboard metrics, onboarding readiness, guest timeline formatting, rule
evaluation, and exception surfacing without pulling in request or DB context.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping, Optional, Sequence


_AUTO_SEND_STATUSES = {"active", "enabled", "on", "ready"}
_FAILURE_STATUSES = {"failed", "error", "errored", "bounced", "rejected"}
_PENDING_STATUSES = {"pending", "queued", "waiting", "needs_review"}
_ESCALATION_TYPES = {"escalation", "complex"}
_NEGATIVE_SENTIMENT_PATTERNS = (
    r"\bangry\b",
    r"\bawful\b",
    r"\bbad\b",
    r"\bbroken\b",
    r"\bcomplain\b",
    r"\bdirty\b",
    r"\bdisappointed\b",
    r"\bfrustrat",
    r"\bhorrible\b",
    r"\bissue\b",
    r"\blate\b",
    r"\bleak\b",
    r"\bmissing\b",
    r"\bnot working\b",
    r"\bproblem\b",
    r"\brefund\b",
    r"\bterrible\b",
    r"\bunacceptable\b",
)
_POSITIVE_SENTIMENT_PATTERNS = (
    r"\bawesome\b",
    r"\bexcellent\b",
    r"\bgreat\b",
    r"\bhelpful\b",
    r"\blove\b",
    r"\bperfect\b",
    r"\bthank(s| you)\b",
    r"\bwonderful\b",
)


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


def _normalize_score(value: Any, default: float = 0.0) -> float:
    if value is None or value == "":
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return max(-1.0, min(number, 1.0))


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


def analyze_guest_sentiment(text: str) -> dict[str, Any]:
    body = (text or "").strip().lower()
    if not body:
        return {"label": "neutral", "score": 0.0}
    positive_hits = sum(1 for pattern in _POSITIVE_SENTIMENT_PATTERNS if re.search(pattern, body))
    negative_hits = sum(1 for pattern in _NEGATIVE_SENTIMENT_PATTERNS if re.search(pattern, body))
    total = positive_hits + negative_hits
    if total == 0:
        return {"label": "neutral", "score": 0.0}
    score = (positive_hits - negative_hits) / max(total, 1)
    if score >= 0.2:
        label = "positive"
    elif score <= -0.2:
        label = "negative"
    else:
        label = "neutral"
    return {"label": label, "score": round(max(-1.0, min(score, 1.0)), 2)}


def build_thread_key(
    tenant_id: str,
    *,
    reservation_id: Any = None,
    reply_to: str = "",
    guest_name: str = "",
    channel: str = "",
) -> str:
    if reservation_id:
        return f"{tenant_id}:reservation:{reservation_id}"
    identity = (reply_to or guest_name or "").strip().lower()
    normalized = re.sub(r"\s+", "", identity)
    digest = hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:16] if normalized else "unknown"
    return f"{tenant_id}:{(channel or 'guest').lower()}:{digest}"


def compute_stay_stage(reservation: Any, today: Optional[Any] = None) -> str:
    if today is None:
        today = datetime.now(timezone.utc).date()
    elif isinstance(today, datetime):
        today = today.date()
    checkin = _date_from_any(_value(reservation, "checkin"))
    checkout = _date_from_any(_value(reservation, "checkout"))
    if not checkin or not checkout:
        return ""
    if today < checkin:
        return "pre_arrival"
    if today > checkout:
        return "post_checkout"
    if today == checkin:
        return "arrival_day"
    if today == checkout:
        return "checkout_day"
    return "in_stay"


def build_structured_policy_context(config: Any) -> str:
    if not config:
        return ""
    policy_fields = [
        ("pet_policy", "Pet policy"),
        ("refund_policy", "Refund policy"),
        ("early_checkin_policy", "Early check-in"),
        ("early_checkin_fee", "Early check-in fee"),
        ("late_checkout_policy", "Late checkout"),
        ("late_checkout_fee", "Late checkout fee"),
        ("parking_policy", "Parking"),
        ("smoking_policy", "Smoking"),
        ("quiet_hours", "Quiet hours"),
    ]
    lines: list[str] = []
    for attr, label in policy_fields:
        value = _value(config, attr)
        if value is None or value == "":
            continue
        lines.append(f"{label}: {value}")
    if not lines:
        return ""
    return "<structured_policies>\n" + "\n".join(lines) + "\n</structured_policies>"


def draft_policy_conflicts(message: str, draft_text: str, config: Any) -> list[str]:
    guest_text = (message or "").lower()
    reply_text = (draft_text or "").lower()
    conflicts: list[str] = []
    pet_policy = _text(config, "pet_policy").lower()
    refund_policy = _text(config, "refund_policy").lower()
    early_policy = _text(config, "early_checkin_policy").lower()
    early_fee = _text(config, "early_checkin_fee").lower()
    late_policy = _text(config, "late_checkout_policy").lower()
    late_fee = _text(config, "late_checkout_fee").lower()
    parking_policy = _text(config, "parking_policy").lower()
    smoking_policy = _text(config, "smoking_policy").lower()

    if "pet" in guest_text and pet_policy:
        if any(term in pet_policy for term in {"no", "not allowed", "prohibit"}) and any(term in reply_text for term in {"yes", "allowed", "welcome"}):
            conflicts.append("Draft appears to allow pets against the configured pet policy.")
    if "refund" in guest_text and refund_policy:
        if any(term in refund_policy for term in {"no refund", "non-refundable", "48 hours"}) and any(term in reply_text for term in {"refund approved", "full refund", "we can refund"}):
            conflicts.append("Draft appears to promise a refund that conflicts with the configured refund policy.")
    if "early check" in guest_text and (early_policy or early_fee):
        if any(term in early_policy for term in {"subject to availability", "not guaranteed", "fee"}) and any(term in reply_text for term in {"any time", "whenever", "free early"}):
            conflicts.append("Draft appears to over-promise early check-in against policy.")
    if "late check" in guest_text and (late_policy or late_fee):
        if any(term in late_policy for term in {"subject to availability", "not guaranteed", "fee"}) and any(term in reply_text for term in {"free late", "any time", "whenever"}):
            conflicts.append("Draft appears to over-promise late checkout against policy.")
    if "parking" in guest_text and parking_policy:
        if any(term in parking_policy for term in {"no parking", "street only", "not included"}) and any(term in reply_text for term in {"private parking", "reserved parking", "garage"}):
            conflicts.append("Draft parking instructions appear to conflict with the configured parking policy.")
    if "smok" in guest_text and smoking_policy:
        if any(term in smoking_policy for term in {"no", "not allowed", "prohibit"}) and any(term in reply_text for term in {"yes", "allowed", "fine"}):
            conflicts.append("Draft appears to allow smoking against the configured smoking policy.")
    return conflicts


def compute_guest_history_score(
    reservation: Any = None,
    drafts: Iterable[Any] = (),
) -> float:
    score = 0.5
    if reservation is not None:
        rating = _number(reservation, "review_rating", None)
        if rating is not None:
            score += ((rating - 3.0) / 2.0) * 0.2
        repeat_guest_count = _number(reservation, "repeat_guest_count", 0.0) or 0.0
        score += min(repeat_guest_count, 3.0) * 0.05
        positive_feedback = _number(reservation, "guest_feedback_positive", 0.0) or 0.0
        negative_feedback = _number(reservation, "guest_feedback_negative", 0.0) or 0.0
        score += min(positive_feedback, 5.0) * 0.03
        score -= min(negative_feedback, 5.0) * 0.06
        review_sentiment_score = _normalize_score(_value(reservation, "review_sentiment_score"), 0.0)
        score += review_sentiment_score * 0.08
    recent_drafts = sorted(
        list(drafts or []),
        key=lambda row: _dt(_value(row, "approved_at") or _value(row, "created_at")) or datetime.now(timezone.utc),
        reverse=True,
    )[:10]
    for draft in recent_drafts:
        feedback_score = _number(draft, "host_feedback_score", None)
        if feedback_score is not None:
            score += max(-1.0, min(feedback_score, 1.0)) * 0.04
        score += _normalize_score(_value(draft, "sentiment_score"), 0.0) * 0.02
    return round(max(0.0, min(score, 1.0)), 2)


def compute_portfolio_benchmark(
    current_value: Optional[float],
    peer_values: Iterable[Any],
    *,
    lower_is_better: bool = True,
) -> dict[str, Any]:
    if current_value is None:
        return {"percentile": None, "summary": ""}
    peers = [float(value) for value in peer_values if value not in {None, ""}]
    if not peers:
        return {"percentile": None, "summary": ""}
    if lower_is_better:
        better_or_equal = sum(1 for value in peers if current_value <= value)
    else:
        better_or_equal = sum(1 for value in peers if current_value >= value)
    percentile = round((better_or_equal / len(peers)) * 100.0, 1)
    if percentile >= 85:
        band = "top 15%"
    elif percentile >= 70:
        band = "top 30%"
    elif percentile >= 50:
        band = "around median"
    else:
        band = "below portfolio median"
    return {
        "percentile": percentile,
        "summary": band,
    }


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
    auto_sent = 0
    positive_feedback = 0
    negative_feedback = 0
    sentiment_total = 0.0
    sentiment_count = 0
    thread_depth_max = 0
    approval_seconds: list[float] = []

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
        if _bool(draft, "auto_send_eligible") or _bool(draft, "auto_sent"):
            auto_sent += 1
        feedback_score = _number(draft, "host_feedback_score", None)
        if feedback_score is not None:
            if feedback_score > 0:
                positive_feedback += 1
            elif feedback_score < 0:
                negative_feedback += 1
        sentiment_score = _number(draft, "sentiment_score", None)
        if sentiment_score is not None:
            sentiment_total += sentiment_score
            sentiment_count += 1
        guest_message_index = int(_number(draft, "guest_message_index", 1) or 1)
        thread_depth_max = max(thread_depth_max, guest_message_index)
        approved_at = _dt(_value(draft, "approved_at"))
        if created_at and approved_at and approved_at >= created_at:
            approval_seconds.append((approved_at - created_at).total_seconds())
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
    reviews_count = 0
    review_rating_total = 0.0
    review_sentiment_total = 0.0
    review_sentiment_count = 0

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
        review_rating = _number(reservation, "review_rating", None)
        if review_rating is not None:
            reviews_count += 1
            review_rating_total += review_rating
        review_sentiment_score = _number(reservation, "review_sentiment_score", None)
        if review_sentiment_score is not None:
            review_sentiment_total += review_sentiment_score
            review_sentiment_count += 1

    total_drafts = len(draft_rows)
    total_reservations = len(reservation_rows)
    approval_rate = round((approved / total_drafts) * 100, 1) if total_drafts else 0.0
    context_completion_rate = round((context_complete / total_reservations) * 100, 1) if total_reservations else 0.0
    automation_ready_ratio = round(((routine + approved) / total_drafts) * 100, 1) if total_drafts else 0.0
    avg_sentiment = round(sentiment_total / sentiment_count, 2) if sentiment_count else 0.0
    avg_review_rating = round(review_rating_total / reviews_count, 2) if reviews_count else None
    avg_review_sentiment = round(review_sentiment_total / review_sentiment_count, 2) if review_sentiment_count else 0.0
    avg_response_seconds = round(sum(approval_seconds) / len(approval_seconds), 1) if approval_seconds else None

    streak = compute_approval_streak(draft_rows)
    gaps   = find_occupancy_gaps(reservation_rows, today=today)

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
            "approval_streak": streak,
            "auto_sent": auto_sent,
            "positive_feedback": positive_feedback,
            "negative_feedback": negative_feedback,
            "max_thread_depth": thread_depth_max,
            "avg_response_seconds": avg_response_seconds,
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
            "occupancy_gaps": gaps,
            "review_count": reviews_count,
            "avg_review_rating": avg_review_rating,
        },
        "ops": {
            "automation_ready_ratio": automation_ready_ratio,
            "needs_attention": pending + escalations + missing_guest_phone + missing_unit,
            "avg_guest_sentiment": avg_sentiment,
            "avg_review_sentiment": avg_review_sentiment,
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

    policy_conflicts = _listify(_value(draft, "policy_conflicts"))
    if policy_conflicts or _bool(draft, "policy_conflict", False):
        return {"should_send": False, "reason": "draft conflicts with structured property policy"}

    min_confidence = _number(rule, "min_confidence", None)
    if min_confidence is None:
        min_confidence = _number(rule, "confidence_threshold", None)
    draft_confidence = _normalize_confidence(_value(draft, "confidence"))
    if min_confidence is not None:
        if draft_confidence < min_confidence:
            return {
                "should_send": False,
                "reason": f"confidence {draft_confidence:.2f} below threshold {min_confidence:.2f}",
            }
    elif draft_type == "complex" and draft_confidence < 0.92:
        return {"should_send": False, "reason": "complex auto-send requires very high confidence"}

    min_guest_history_score = _number(rule, "min_guest_history_score", None)
    guest_history_score = _number(draft, "guest_history_score", None)
    if min_guest_history_score is not None and guest_history_score is not None and guest_history_score < min_guest_history_score:
        return {
            "should_send": False,
            "reason": f"guest history score {guest_history_score:.2f} below threshold {min_guest_history_score:.2f}",
        }
    if draft_type == "complex" and guest_history_score is not None and guest_history_score < 0.6:
        return {"should_send": False, "reason": "complex auto-send requires a stronger guest history"}

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

    guest_sentiment = _text(draft, "guest_sentiment").lower()
    sentiment_score = _normalize_score(_value(draft, "guest_sentiment_score") or _value(draft, "sentiment_score"), 0.0)
    if guest_sentiment == "negative" and not _bool(rule, "allow_negative_sentiment", False):
        return {"should_send": False, "reason": "negative guest sentiment requires review"}
    if draft_type == "complex" and sentiment_score < -0.2:
        return {"should_send": False, "reason": "guest sentiment is too negative for safe complex auto-send"}

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

    allowed_stay_stages = _listify(_value(rule, "stay_stages"))
    stay_stage = _text(draft, "stay_stage").lower()
    if allowed_stay_stages and stay_stage and stay_stage not in {item.lower() for item in allowed_stay_stages}:
        return {"should_send": False, "reason": f"stay stage {stay_stage} is not allowed"}

    if _bool(rule, "requires_approval", False):
        return {"should_send": False, "reason": "rule requires manual approval"}

    return {"should_send": True, "reason": "rule matched"}


def should_auto_send(rule: Any, draft: Any, when: Optional[datetime] = None) -> bool:
    """Boolean convenience wrapper around automation_rule_decision()."""
    return bool(automation_rule_decision(rule, draft, when=when)["should_send"])


def compute_approval_streak(drafts: Iterable[Any]) -> int:
    """
    Return the number of consecutive most-recent drafts the host approved
    without skipping or escalating.

    A streak resets on any non-approved terminal status (skipped, failed, etc.).
    Pending drafts are ignored (streak looks at resolved ones only).
    """
    terminal_statuses = {"approved", "skipped", "failed", "error", "bounced", "escalation"}
    resolved: list[Any] = []
    for d in drafts or []:
        if _text(d, "status").lower() in terminal_statuses:
            resolved.append(d)

    # Sort newest first
    resolved.sort(key=lambda d: _dt(_value(d, "approved_at") or _value(d, "created_at")) or datetime.min.replace(tzinfo=timezone.utc), reverse=True)

    streak = 0
    for d in resolved:
        if _text(d, "status").lower() == "approved":
            streak += 1
        else:
            break
    return streak


def find_occupancy_gaps(
    reservations: Iterable[Any],
    today: Optional[Any] = None,
    window_days: int = 60,
) -> list[dict[str, Any]]:
    """
    Find gap nights between confirmed reservations within the next window_days.

    Returns a list of dicts: {gap_start, gap_end, gap_nights, before_guest, after_guest}.
    Only gaps ≥ 1 night are returned, sorted by gap_start ascending.
    """
    if today is None:
        today = datetime.now(timezone.utc).date()
    elif isinstance(today, datetime):
        today = today.date()

    window_end = today + timedelta(days=window_days)

    # Collect confirmed reservations with valid dates
    confirmed: list[tuple] = []
    for res in reservations or []:
        status = _text(res, "status").lower() or "confirmed"
        if status not in {"confirmed", "active"}:
            continue
        checkin  = _date_from_any(_value(res, "checkin"))
        checkout = _date_from_any(_value(res, "checkout"))
        if not checkin or not checkout or checkout <= checkin:
            continue
        if checkout < today or checkin > window_end:
            continue
        guest_name = _text(res, "guest_name") or "Guest"
        confirmed.append((checkin, checkout, guest_name))

    if len(confirmed) < 2:
        return []

    confirmed.sort(key=lambda t: t[0])

    gaps: list[dict[str, Any]] = []
    for i in range(len(confirmed) - 1):
        _, prev_checkout, prev_guest = confirmed[i]
        next_checkin, _, next_guest = confirmed[i + 1]
        gap_nights = (next_checkin - prev_checkout).days
        if gap_nights >= 1:
            gaps.append({
                "gap_start":    prev_checkout,
                "gap_end":      next_checkin,
                "gap_nights":   gap_nights,
                "before_guest": prev_guest,
                "after_guest":  next_guest,
            })
    return gaps


def compute_review_velocity(reservations: Iterable[Any]) -> Optional[float]:
    """
    Estimate reviews per 30 days based on reservations with review data.

    Returns None if no review data is available.
    Looks for a `review_count` or `rating` field to infer a review was left,
    combined with `checkout` date to compute velocity over time.
    """
    reviewed: list[Any] = []
    for res in reservations or []:
        has_review = (
            _number(res, "review_count") is not None
            or _number(res, "rating") is not None
            or _bool(res, "has_review")
            or _text(res, "review_text").strip()
        )
        checkout = _date_from_any(_value(res, "checkout"))
        if has_review and checkout:
            reviewed.append(checkout)

    if not reviewed:
        return None

    if len(reviewed) == 1:
        return 1.0

    reviewed.sort()
    earliest = reviewed[0]
    latest   = reviewed[-1]
    span_days = (latest - earliest).days
    if span_days < 1:
        return float(len(reviewed))

    return round(len(reviewed) / (span_days / 30.0), 2)


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
