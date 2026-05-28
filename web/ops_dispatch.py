# © 2024 Jestin Rajan. All rights reserved.
"""
Operations Task Dispatch — turn guest complaints/maintenance reports into
tracked tasks assigned to the right team member, with SLA escalation and
auto-notify guest on resolution.

Flow:
  1. maybe_dispatch_from_message(...) called from inbound handler
  2. Detects maintenance / cleaning / supply / security task type
  3. Routes to the right team member (matches task_types column)
  4. Sends them a dispatch notification via SMS/WhatsApp
  5. Replies to guest that someone's on it
  6. resolve_task(team_member_phone, reply_text) parses "done"/"fixed" replies
  7. sla_escalation_job() escalates overdue tasks to host
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy.orm import Session

from web.db import SessionLocal
from web.models import (
    ActivityLog,
    Draft,
    OperationsTask,
    Reservation,
    TeamMember,
    TenantConfig,
)

log = logging.getLogger(__name__)

# Default SLA: how long before escalation to host (minutes)
_SLA_BY_PRIORITY = {
    "urgent": 30,
    "high":   60,
    "normal": 240,   # 4 hours
    "low":    1440,  # 24 hours
}

# Keywords that, on their own, indicate an operations task
_MAINTENANCE_KEYWORDS = (
    "broken", "not working", "doesn't work", "doesnt work", "won't work",
    "leaking", "leak", "no hot water", "no water", "no power", "no electricity",
    "no wifi", "no internet", "no ac", "no heat", "smoke", "smoke alarm",
    "burning smell", "stuck", "jammed", "blocked", "won't open", "won't close",
    "noisy", "noise", "smell", "dirty", "stained",
)
_CLEANING_KEYWORDS = ("dirty", "stained", "smell", "trash", "garbage", "mess", "vomit")
_SUPPLY_KEYWORDS = (
    "out of", "ran out", "no more", "need more", "no toilet paper", "no towels",
    "no soap", "no shampoo", "no coffee",
)
_SECURITY_KEYWORDS = ("intruder", "stolen", "thief", "break in", "broke in")
_URGENT_KEYWORDS = ("urgent", "emergency", "immediately", "right now", "asap", "burning", "fire", "smoke", "flood")

_TASK_DONE_KEYWORDS = ("done", "complete", "completed", "fixed", "resolved", "sorted", "finished")


def _classify_task(text: str) -> Optional[tuple[str, str]]:
    """
    Returns (task_type, priority) or None if not an operations task.
    """
    if not text:
        return None
    t = text.lower()
    priority = "urgent" if any(k in t for k in _URGENT_KEYWORDS) else "normal"

    if any(k in t for k in _SECURITY_KEYWORDS):
        return ("security", "urgent")
    if any(k in t for k in _MAINTENANCE_KEYWORDS):
        return ("maintenance", priority if priority != "normal" else "high")
    if any(k in t for k in _SUPPLY_KEYWORDS):
        return ("supply", "normal")
    if any(k in t for k in _CLEANING_KEYWORDS):
        return ("cleaning", priority if priority != "normal" else "high")
    return None


def _route(db: Session, tenant_id: str, task_type: str) -> Optional[TeamMember]:
    """Pick a team member to handle this task type."""
    members = db.query(TeamMember).filter(
        TeamMember.tenant_id == tenant_id,
        TeamMember.is_active.is_(True),
        TeamMember.is_available_for_assignment.is_(True),
    ).all()
    if not members:
        return None
    # Prefer members whose task_types includes this type
    for m in members:
        if task_type in {t.strip() for t in (m.task_types or "").split(",") if t.strip()}:
            return m
    # Fallback: role-based pick
    role_priority = {
        "maintenance": ("maintenance",),
        "cleaning":    ("cleaner",),
        "supply":      ("manager", "front_desk"),
        "security":    ("manager", "owner"),
    }.get(task_type, ("manager",))
    for role in role_priority:
        for m in members:
            if m.role == role:
                return m
    # Fallback: any member
    return members[0] if members else None


def _send(cfg: TenantConfig, phone: str, content: str, channel: str = "sms") -> bool:
    """Reuse the existing _send_voice_message helper from app.py."""
    if not phone or not content:
        return False
    try:
        from web.app import _send_voice_message
        return _send_voice_message(cfg, phone, content, channel)
    except Exception as exc:
        log.error("[OPS] _send_voice_message failed: %s", exc)
        return False


def maybe_dispatch_from_message(db: Session, tenant_id: str, cfg: TenantConfig,
                                 draft: Draft, reservation: Optional[Reservation],
                                 guest_name: str, guest_phone: str, channel: str) -> Optional[OperationsTask]:
    """
    Entry point from the inbound handler. Detects ops requests, creates a task,
    notifies the assigned team member, and returns the task (or None).
    """
    guest_text = (draft.message if draft else "") or ""
    classified = _classify_task(guest_text)
    if not classified:
        return None
    task_type, priority = classified

    assignee = _route(db, tenant_id, task_type)
    sla = _SLA_BY_PRIORITY.get(priority, 240)
    title = f"{task_type.title()}: {guest_text[:60]}"

    task = OperationsTask(
        tenant_id=tenant_id,
        reservation_id=reservation.id if reservation else None,
        draft_id=draft.id if draft else None,
        assigned_to=assignee.id if assignee else None,
        task_type=task_type,
        priority=priority,
        title=title,
        description=guest_text[:1000],
        status="assigned" if assignee else "open",
        sla_minutes=sla,
        guest_phone=guest_phone,
        guest_name=guest_name,
        guest_channel=channel,
        assigned_at=datetime.now(timezone.utc) if assignee else None,
    )
    db.add(task)
    db.commit()
    db.refresh(task)

    # Notify the team member
    if assignee:
        notify_phone = assignee.notify_phone or assignee.phone
        notify_channel = assignee.notify_channel or "sms"
        if notify_phone:
            res_label = ""
            if reservation:
                room = reservation.unit_identifier or reservation.listing_name or ""
                res_label = f"\nUnit: {room}\nGuest: {guest_name}" if room else f"\nGuest: {guest_name}"
            msg = (
                f"🔔 New {priority.upper()} {task_type} task #{task.id}{res_label}\n"
                f"Issue: {guest_text[:160]}\n\n"
                f"Reply DONE {task.id} when fixed."
            )
            sent = _send(cfg, notify_phone, msg, notify_channel)
            if sent:
                db.add(ActivityLog(
                    tenant_id=tenant_id, event_type="ops_dispatched",
                    message=f"Task #{task.id} ({task_type}, {priority}) → {assignee.display_name}",
                ))
                db.commit()

    # Reply to the guest
    if guest_phone and channel in ("sms", "whatsapp"):
        guest_reply = {
            "security":    "We've been notified and are looking into this immediately. Please get to a safe location.",
            "maintenance": "Sorry about that — I've alerted our maintenance team. They'll be in touch shortly.",
            "cleaning":    "Apologies — I'm sending someone from housekeeping to take care of this.",
            "supply":      "Got it — I'll arrange a delivery / restock right away.",
        }.get(task_type, "Got it — I've passed this to the right person and they'll be in touch soon.")
        _send(cfg, guest_phone, guest_reply, channel)
        # Mark the draft as auto-handled so it doesn't sit in the pending queue
        try:
            if draft and draft.status == "pending":
                draft.status = "auto_sent"
                draft.final_text = guest_reply
                draft.approved_at = datetime.now(timezone.utc)
                db.commit()
        except Exception:
            db.rollback()

    return task


def resolve_task_by_reply(db: Session, tenant_id: str, from_phone: str, reply_text: str) -> bool:
    """
    Parse a team-member reply like 'DONE 42' or 'fixed' (replying to dispatch).
    Resolves the task and notifies the guest. Returns True if a task was resolved.
    """
    if not from_phone or not reply_text:
        return False
    text = reply_text.strip().lower()

    # Find the team member by phone
    member = db.query(TeamMember).filter(
        TeamMember.tenant_id == tenant_id,
        ((TeamMember.notify_phone == from_phone) | (TeamMember.phone == from_phone)),
    ).first()
    if not member:
        return False

    # Look for "DONE 42" pattern
    task_id = None
    m = re.search(r"\b(?:done|fixed|complete|resolved|finished)\s*#?(\d+)\b", text)
    if m:
        task_id = int(m.group(1))
    elif any(kw in text for kw in _TASK_DONE_KEYWORDS):
        # If no number, find their most recent assigned-but-not-resolved task
        open_task = (
            db.query(OperationsTask)
            .filter(
                OperationsTask.tenant_id == tenant_id,
                OperationsTask.assigned_to == member.id,
                OperationsTask.status.in_(("open", "assigned", "in_progress")),
            )
            .order_by(OperationsTask.created_at.desc())
            .first()
        )
        if open_task:
            task_id = open_task.id

    if not task_id:
        return False

    task = db.query(OperationsTask).filter_by(id=task_id, tenant_id=tenant_id).first()
    if not task or task.status == "resolved":
        return False
    task.status = "resolved"
    task.resolved_at = datetime.now(timezone.utc)
    task.resolution_note = reply_text[:500]
    db.add(ActivityLog(
        tenant_id=tenant_id, event_type="ops_resolved",
        message=f"Task #{task.id} resolved by {member.display_name}",
    ))
    db.commit()

    # Notify guest
    if task.guest_phone and task.guest_channel and not task.guest_notified:
        cfg = db.query(TenantConfig).filter_by(tenant_id=tenant_id).first()
        if cfg:
            done_msg = {
                "maintenance": "Great news — the maintenance issue has been taken care of! 🔧 Anything else?",
                "cleaning":    "All sorted — our team has cleaned that up. Thanks for your patience!",
                "supply":      "Done — fresh supplies are on the way / have been delivered.",
                "security":    "We've handled the security concern. Please let us know if you have any further worries.",
            }.get(task.task_type, "All taken care of. Thanks for your patience!")
            sent = _send(cfg, task.guest_phone, done_msg, task.guest_channel)
            if sent:
                task.guest_notified = True
                db.commit()
    return True


def sla_escalation_job() -> None:
    """
    Cron job: find tasks past their SLA without resolution → notify the host.
    """
    now = datetime.now(timezone.utc)
    try:
        with SessionLocal() as db:
            overdue = (
                db.query(OperationsTask)
                .filter(
                    OperationsTask.status.in_(("open", "assigned", "in_progress")),
                    OperationsTask.escalated.is_(False),
                )
                .limit(100)
                .all()
            )
            for task in overdue:
                if not task.sla_minutes or not task.created_at:
                    continue
                created = task.created_at if task.created_at.tzinfo else task.created_at.replace(tzinfo=timezone.utc)
                age_min = (now - created).total_seconds() / 60
                if age_min < task.sla_minutes:
                    continue

                # Escalate to host
                cfg = db.query(TenantConfig).filter_by(tenant_id=task.tenant_id).first()
                if not cfg:
                    continue
                host_phone = cfg.sms_notify_number or cfg.host_notify_phone
                if not host_phone:
                    task.escalated = True
                    continue
                msg = (
                    f"⚠️ SLA breach: task #{task.id} ({task.task_type}, {task.priority}) "
                    f"open for {int(age_min)} min.\n"
                    f"Guest: {task.guest_name or 'unknown'}\n"
                    f"{task.title[:140]}"
                )
                _send(cfg, host_phone, msg, "sms")
                task.escalated = True
                db.add(ActivityLog(
                    tenant_id=task.tenant_id, event_type="ops_escalated",
                    message=f"Task #{task.id} escalated to host ({int(age_min)} min)",
                ))
            db.commit()
    except Exception as exc:
        log.error("[OPS] sla_escalation_job error: %s", exc, exc_info=True)
