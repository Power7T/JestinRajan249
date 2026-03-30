"""
API cost tracking and billing utilities.
Tracks usage and costs for all external API services.
"""

from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4
from sqlalchemy.orm import Session
from web.models import APIUsageLog, Tenant
import logging

log = logging.getLogger(__name__)

# Current API costs (as of 2024)
COSTS = {
    "deepgram": {
        "transcribe": 0.0043,  # per minute, assume 60 seconds
    },
    "openai": {
        "gpt-4": {
            "input": 0.00003,   # per token
            "output": 0.00006,  # per token
        },
        "gpt-3.5-turbo": {
            "input": 0.0000015,
            "output": 0.000002,
        },
    },
    "elevenlabs": {
        "synthesize": 0.0003,  # per character (roughly 15000 chars = $1)
    },
    "twilio": {
        "voice_call": 0.0150,  # per minute, assume worst case
        "recording": 0.0050,   # per minute for recording storage
    },
}


def log_api_usage(
    db: Session,
    tenant_id: str,
    service: str,
    operation: str,
    cost_usd: float = 0.0,
    input_tokens: Optional[int] = None,
    output_tokens: Optional[int] = None,
    duration_seconds: Optional[float] = None,
    characters: Optional[int] = None,
    call_id: Optional[str] = None,
    status: str = "success",
    error_message: Optional[str] = None,
):
    """Log an API usage event for cost tracking."""
    try:
        usage_log = APIUsageLog(
            id=str(uuid4()),
            tenant_id=tenant_id,
            call_id=call_id,
            service=service,
            operation=operation,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            duration_seconds=duration_seconds,
            characters=characters,
            cost_usd=cost_usd,
            status=status,
            error_message=error_message,
            created_at=datetime.now(timezone.utc),
        )
        db.add(usage_log)
        db.commit()

        log.info(
            f"[COST] {service}.{operation}: ${cost_usd:.6f} "
            f"(tokens={input_tokens}/{output_tokens}, duration={duration_seconds}s)"
        )
    except Exception as e:
        log.error(f"[COST] Error logging API usage: {e}")
        db.rollback()


def estimate_cost(
    service: str,
    operation: str,
    input_tokens: Optional[int] = None,
    output_tokens: Optional[int] = None,
    duration_seconds: Optional[float] = None,
    characters: Optional[int] = None,
    model: str = "gpt-4",
) -> float:
    """Estimate cost for an API operation before making the call."""
    try:
        if service == "deepgram" and operation == "transcribe":
            # $0.0043 per minute, round up to 1 min minimum
            minutes = max(1, (duration_seconds or 60) / 60)
            return COSTS["deepgram"]["transcribe"] * minutes

        elif service == "openai":
            costs = COSTS["openai"].get(model, COSTS["openai"]["gpt-4"])
            input_cost = (input_tokens or 0) * costs["input"]
            output_cost = (output_tokens or 0) * costs["output"]
            return input_cost + output_cost

        elif service == "elevenlabs" and operation == "synthesize":
            # $0.0003 per character
            return COSTS["elevenlabs"]["synthesize"] * (characters or 0)

        elif service == "twilio":
            if operation == "voice_call":
                minutes = max(1, (duration_seconds or 60) / 60)
                return COSTS["twilio"]["voice_call"] * minutes
            elif operation == "recording":
                minutes = (duration_seconds or 60) / 60
                return COSTS["twilio"]["recording"] * minutes

        return 0.0
    except Exception as e:
        log.error(f"[COST] Error estimating cost: {e}")
        return 0.0


def get_tenant_usage_summary(db: Session, tenant_id: str, days: int = 30) -> dict:
    """Get usage and cost summary for a tenant over the last N days."""
    from sqlalchemy import func
    from datetime import timedelta

    cutoff_date = datetime.now(timezone.utc) - timedelta(days=days)

    logs = db.query(APIUsageLog).filter(
        APIUsageLog.tenant_id == tenant_id,
        APIUsageLog.created_at >= cutoff_date,
        APIUsageLog.status == "success",
    ).all()

    summary = {
        "period_days": days,
        "total_cost_usd": 0.0,
        "total_calls": len(logs),
        "by_service": {},
        "by_operation": {},
        "daily_costs": {},
    }

    for log in logs:
        summary["total_cost_usd"] += log.cost_usd

        # By service
        if log.service not in summary["by_service"]:
            summary["by_service"][log.service] = {"count": 0, "cost": 0.0}
        summary["by_service"][log.service]["count"] += 1
        summary["by_service"][log.service]["cost"] += log.cost_usd

        # By operation
        key = f"{log.service}.{log.operation}"
        if key not in summary["by_operation"]:
            summary["by_operation"][key] = {"count": 0, "cost": 0.0}
        summary["by_operation"][key]["count"] += 1
        summary["by_operation"][key]["cost"] += log.cost_usd

        # Daily costs
        day_key = log.created_at.date().isoformat()
        if day_key not in summary["daily_costs"]:
            summary["daily_costs"][day_key] = 0.0
        summary["daily_costs"][day_key] += log.cost_usd

    summary["average_daily_cost"] = summary["total_cost_usd"] / max(days, 1)
    summary["average_cost_per_call"] = summary["total_cost_usd"] / max(summary["total_calls"], 1)

    return summary
