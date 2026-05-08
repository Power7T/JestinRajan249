import logging
from typing import Optional, Dict, Any
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import datetime

from web.models import VoiceCall, VoiceRoutingConfig, RoutingRule, Tenant

logger = logging.getLogger(__name__)

def evaluate_routing_rules(db: Session, tenant_id: str, caller_number: str) -> Dict[str, Any]:
    """
    Evaluate custom routing rules and return an action dictionary.
    Action format: {"action": "ai" | "voicemail" | "escalate", "target": str, "urgent": bool}
    """
    config = db.query(VoiceRoutingConfig).filter(VoiceRoutingConfig.tenant_id == tenant_id).first()
    
    # Defaults
    action = {"action": "ai", "target": None, "urgent": False}
    if config:
        action["action"] = config.default_route
        action["target"] = config.host_routing_number
        if config.queue_hold_music:
            action["hold_music"] = True

    # Get recent calls to determine things like call_count or sentiment history.
    # NOTE: These queries run inside a request context and have no statement timeout
    # configured at the DB level. A slow query can block the request thread indefinitely.
    # Wrap with try/except so a DB hiccup degrades gracefully to default routing.
    try:
        recent_calls = db.query(VoiceCall).filter(
            VoiceCall.tenant_id == tenant_id,
            VoiceCall.guest_phone_number == caller_number
        ).order_by(VoiceCall.created_at.desc()).all()
    except Exception as exc:
        logger.warning("[%s] Voice routing VoiceCall query failed: %s", tenant_id, exc)
        recent_calls = []

    call_count = len(recent_calls)
    negative_calls = sum(1 for c in recent_calls if c.sentiment == "negative")

    # Evaluate rules by priority
    try:
        rules = db.query(RoutingRule).filter(
            RoutingRule.tenant_id == tenant_id,
            RoutingRule.is_active == True
        ).order_by(RoutingRule.priority.asc()).all()
    except Exception as exc:
        logger.warning("[%s] Voice routing RoutingRule query failed: %s", tenant_id, exc)
        rules = []

    for rule in rules:
        matched = False
        
        # Condition logic mapping
        if rule.condition_type == "sentiment":
            if rule.condition_value == "negative" and negative_calls > 0:
                matched = True
        elif rule.condition_type == "repeat_caller":
            try:
                threshold = int(rule.condition_value)
                if call_count >= threshold:
                    matched = True
            except ValueError:
                pass
        
        # Add VIP check, blocked list, etc here

        if matched:
            action["action"] = rule.action
            action["target"] = rule.action_target or action["target"]
            if rule.action == "escalate":
                action["urgent"] = True
            break # Stop at first rule match (highest priority)

    return action
