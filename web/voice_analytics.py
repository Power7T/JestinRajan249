import logging
from sqlalchemy.orm import Session
from sqlalchemy import func, text
from datetime import datetime, timedelta
from typing import Dict, Any

from web.models import VoiceCall, VoiceKnowledgeGap, TenantConfig

logger = logging.getLogger(__name__)

def get_voice_analytics(db: Session, tenant_id: str, days: int = 30) -> Dict[str, Any]:
    """Retrieve voice analytics for a given tenant."""
    cutoff_date = datetime.utcnow() - timedelta(days=days)

    # Base query for calls in this period
    base_query = db.query(VoiceCall).filter(
        VoiceCall.tenant_id == tenant_id,
        VoiceCall.created_at >= cutoff_date
    )

    total_calls = base_query.count()

    # Average duration (only completed calls with duration)
    avg_duration_res = base_query.with_entities(func.avg(VoiceCall.duration_seconds)).filter(
        VoiceCall.status == "completed",
        VoiceCall.duration_seconds.isnot(None)
    ).scalar()
    avg_duration = round(float(avg_duration_res), 1) if avg_duration_res else 0.0

    # Call completion rate
    completed_calls = base_query.filter(VoiceCall.status == "completed").count()
    abandoned_calls = base_query.filter(VoiceCall.status.in_(["abandoned", "failed"])).count()
    completion_rate = round((completed_calls / total_calls * 100), 1) if total_calls > 0 else 0.0

    # Sentiment distribution
    sentiment_dist = {}
    for row in base_query.with_entities(VoiceCall.sentiment, func.count(VoiceCall.id)).filter(VoiceCall.sentiment.isnot(None)).group_by(VoiceCall.sentiment).all():
        sentiment_dist[row[0]] = row[1]

    # Knowledge gaps
    knowledge_gaps_query = db.query(VoiceKnowledgeGap).filter(
        VoiceKnowledgeGap.tenant_id == tenant_id,
        VoiceKnowledgeGap.created_at >= cutoff_date
    )
    total_gaps = knowledge_gaps_query.count()
    resolved_gaps = knowledge_gaps_query.filter(VoiceKnowledgeGap.resolved == True).count()
    
    # Needs to be extracted: top issues / rooms (just basic for now)
    gaps_by_room = {}
    for row in knowledge_gaps_query.with_entities(VoiceKnowledgeGap.guest_room, func.count(VoiceKnowledgeGap.id)).filter(VoiceKnowledgeGap.guest_room.isnot(None)).group_by(VoiceKnowledgeGap.guest_room).all():
        gaps_by_room[row[0]] = row[1]

    # Quick cost metrics
    # From TenantConfig pricing tier
    tenant_config = db.query(TenantConfig).filter(TenantConfig.tenant_id == tenant_id).first()
    plan = tenant_config.subscription_plan if tenant_config else "starter"
    
    # We can refine costs in a more detailed query, but for now we aggregate VoiceCall duration
    total_duration_minutes = (base_query.with_entities(func.sum(VoiceCall.duration_seconds)).scalar() or 0) / 60.0
    # Assuming baseline twilio cost of ~$0.05 / min as a rough estimate
    estimated_cost = round(total_duration_minutes * 0.05, 2)

    return {
        "days": days,
        "total_calls": total_calls,
        "avg_duration": avg_duration,
        "completed_calls": completed_calls,
        "abandoned_calls": abandoned_calls,
        "completion_rate": completion_rate,
        "sentiment_dist": sentiment_dist,
        "knowledge_gaps": {
            "total": total_gaps,
            "resolved": resolved_gaps,
            "by_room": gaps_by_room
        },
        "estimated_cost": estimated_cost,
        "plan": plan
    }
