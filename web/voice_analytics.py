import logging
from datetime import datetime, timedelta
from typing import Any, Dict

import sqlalchemy as sa
from sqlalchemy.orm import Session

from web.tenant_config_store import load_tenant_config

logger = logging.getLogger(__name__)


def _existing_columns(db: Session, table_name: str) -> set[str]:
    inspector = sa.inspect(db.connection())
    return {col["name"] for col in inspector.get_columns(table_name)}


def _reflect_table(db: Session, table_name: str) -> sa.Table | None:
    try:
        return sa.Table(table_name, sa.MetaData(), autoload_with=db.connection())
    except Exception as exc:
        logger.warning("Voice analytics could not reflect %s: %s", table_name, exc)
        return None


def _voice_call_metrics(db: Session, tenant_id: str, cutoff_date: datetime) -> Dict[str, Any]:
    defaults = {
        "total_calls": 0,
        "avg_duration": 0.0,
        "completed_calls": 0,
        "abandoned_calls": 0,
        "completion_rate": 0.0,
        "sentiment_dist": {},
        "estimated_cost": 0.0,
    }
    try:
        existing_columns = _existing_columns(db, "voice_calls")
    except Exception as exc:
        logger.warning("Voice analytics could not inspect voice_calls: %s", exc)
        return defaults

    if not {"tenant_id", "created_at"}.issubset(existing_columns):
        logger.warning("voice_calls is missing required analytics columns; using empty analytics defaults")
        return defaults

    voice_calls = _reflect_table(db, "voice_calls")
    if voice_calls is None:
        return defaults

    filters = [
        voice_calls.c.tenant_id == tenant_id,
        voice_calls.c.created_at >= cutoff_date,
    ]

    total_calls = db.execute(
        sa.select(sa.func.count()).select_from(voice_calls).where(*filters)
    ).scalar_one()

    completed_calls = 0
    abandoned_calls = 0
    avg_duration = 0.0
    sentiment_dist: dict[str, int] = {}
    total_duration_minutes = 0.0

    if "status" in existing_columns:
        completed_calls = db.execute(
            sa.select(sa.func.count())
            .select_from(voice_calls)
            .where(*filters, voice_calls.c.status == "completed")
        ).scalar_one()
        abandoned_calls = db.execute(
            sa.select(sa.func.count())
            .select_from(voice_calls)
            .where(*filters, voice_calls.c.status.in_(["abandoned", "failed"]))
        ).scalar_one()

    if {"status", "duration_seconds"}.issubset(existing_columns):
        avg_duration_res = db.execute(
            sa.select(sa.func.avg(voice_calls.c.duration_seconds))
            .where(
                *filters,
                voice_calls.c.status == "completed",
                voice_calls.c.duration_seconds.is_not(None),
            )
        ).scalar()
        avg_duration = round(float(avg_duration_res), 1) if avg_duration_res else 0.0

    if "duration_seconds" in existing_columns:
        total_duration_seconds = db.execute(
            sa.select(sa.func.sum(voice_calls.c.duration_seconds))
            .where(*filters)
        ).scalar() or 0
        total_duration_minutes = float(total_duration_seconds) / 60.0

    if "sentiment" in existing_columns:
        sentiment_rows = db.execute(
            sa.select(voice_calls.c.sentiment, sa.func.count())
            .where(*filters, voice_calls.c.sentiment.is_not(None))
            .group_by(voice_calls.c.sentiment)
        ).all()
        sentiment_dist = {row[0]: row[1] for row in sentiment_rows}

    completion_rate = round((completed_calls / total_calls * 100), 1) if total_calls > 0 else 0.0
    estimated_cost = round(total_duration_minutes * 0.05, 2)

    return {
        "total_calls": total_calls,
        "avg_duration": avg_duration,
        "completed_calls": completed_calls,
        "abandoned_calls": abandoned_calls,
        "completion_rate": completion_rate,
        "sentiment_dist": sentiment_dist,
        "estimated_cost": estimated_cost,
    }


def _knowledge_gap_metrics(db: Session, tenant_id: str, cutoff_date: datetime) -> Dict[str, Any]:
    defaults = {
        "total": 0,
        "resolved": 0,
        "by_room": {},
    }
    try:
        existing_columns = _existing_columns(db, "voice_knowledge_gaps")
    except Exception as exc:
        logger.warning("Voice analytics could not inspect voice_knowledge_gaps: %s", exc)
        return defaults

    if not {"tenant_id", "created_at"}.issubset(existing_columns):
        logger.warning("voice_knowledge_gaps is missing required analytics columns; using empty analytics defaults")
        return defaults

    knowledge_gaps = _reflect_table(db, "voice_knowledge_gaps")
    if knowledge_gaps is None:
        return defaults

    filters = [
        knowledge_gaps.c.tenant_id == tenant_id,
        knowledge_gaps.c.created_at >= cutoff_date,
    ]

    total_gaps = db.execute(
        sa.select(sa.func.count()).select_from(knowledge_gaps).where(*filters)
    ).scalar_one()

    resolved_gaps = 0
    if "resolved" in existing_columns:
        resolved_gaps = db.execute(
            sa.select(sa.func.count())
            .select_from(knowledge_gaps)
            .where(*filters, knowledge_gaps.c.resolved.is_(True))
        ).scalar_one()

    gaps_by_room: dict[str, int] = {}
    if "guest_room" in existing_columns:
        room_rows = db.execute(
            sa.select(knowledge_gaps.c.guest_room, sa.func.count())
            .where(*filters, knowledge_gaps.c.guest_room.is_not(None))
            .group_by(knowledge_gaps.c.guest_room)
        ).all()
        gaps_by_room = {row[0]: row[1] for row in room_rows}

    return {
        "total": total_gaps,
        "resolved": resolved_gaps,
        "by_room": gaps_by_room,
    }


def get_voice_analytics(db: Session, tenant_id: str, days: int = 30) -> Dict[str, Any]:
    """Retrieve voice analytics for a given tenant."""
    cutoff_date = datetime.utcnow() - timedelta(days=days)

    voice_metrics = _voice_call_metrics(db, tenant_id, cutoff_date)
    knowledge_gap_metrics = _knowledge_gap_metrics(db, tenant_id, cutoff_date)

    tenant_config = load_tenant_config(db, tenant_id)
    plan = tenant_config.subscription_plan if tenant_config else "starter"

    return {
        "days": days,
        "total_calls": voice_metrics["total_calls"],
        "avg_duration": voice_metrics["avg_duration"],
        "completed_calls": voice_metrics["completed_calls"],
        "abandoned_calls": voice_metrics["abandoned_calls"],
        "completion_rate": voice_metrics["completion_rate"],
        "sentiment_dist": voice_metrics["sentiment_dist"],
        "knowledge_gaps": knowledge_gap_metrics,
        "estimated_cost": voice_metrics["estimated_cost"],
        "plan": plan,
    }
