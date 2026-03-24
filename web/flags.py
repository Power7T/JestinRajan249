# © 2024 Jestin Rajan. All rights reserved.
"""
Feature flag registry.

Flags are controlled by environment variables with the prefix FLAG_:
    FLAG_SSE_NOTIFICATIONS=true
    FLAG_ANALYTICS_PAGE=false

This lets you deploy code that's off by default and turn it on without a
redeploy — kill a broken feature in seconds by setting the env var to false.

Usage:
    from web.flags import flags
    if flags.SSE_NOTIFICATIONS:
        ...

Or as a FastAPI dependency:
    from web.flags import require_flag
    @app.get("/analytics")
    def analytics(flag=Depends(require_flag("ANALYTICS_PAGE"))):
        ...
"""

import os
import logging
from dataclasses import dataclass, field
from typing import Optional

log = logging.getLogger(__name__)


@dataclass
class Flag:
    name: str
    default: bool
    description: str

    @property
    def is_enabled(self) -> bool:
        raw = os.getenv(f"FLAG_{self.name}", "").strip().lower()
        if not raw:
            return self.default
        return raw in {"1", "true", "yes", "on"}

    def __bool__(self) -> bool:
        return self.is_enabled


class FlagRegistry:
    """Central registry — iterate with flags.all() to log the current state."""

    def __init__(self):
        self._flags: dict[str, Flag] = {}

    def _register(self, name: str, default: bool, description: str) -> Flag:
        f = Flag(name=name, default=default, description=description)
        self._flags[name] = f
        return f

    def all(self) -> list[Flag]:
        return list(self._flags.values())

    def log_state(self) -> None:
        for f in self._flags.values():
            log.info("Feature flag %-30s = %s (default=%s)", f.name, f.is_enabled, f.default)

    # ------------------------------------------------------------------
    # Flag definitions — add new flags here
    # ------------------------------------------------------------------

    @property
    def SSE_NOTIFICATIONS(self) -> Flag:
        """Real-time SSE draft notifications (replaces 30s HTMX poll)."""
        return self._flags.get("SSE_NOTIFICATIONS") or self._register(
            "SSE_NOTIFICATIONS", default=True,
            description="Real-time SSE draft notifications"
        )

    @property
    def ANALYTICS_PAGE(self) -> Flag:
        """Analytics dashboard with Chart.js charts."""
        return self._flags.get("ANALYTICS_PAGE") or self._register(
            "ANALYTICS_PAGE", default=True,
            description="Analytics dashboard page"
        )

    @property
    def TEAM_MEMBERS(self) -> Flag:
        """Team member invite + login flow."""
        return self._flags.get("TEAM_MEMBERS") or self._register(
            "TEAM_MEMBERS", default=True,
            description="Team member login and invite flow"
        )

    @property
    def CONVERSATION_VIEW(self) -> Flag:
        """Group dashboard drafts into conversation threads."""
        return self._flags.get("CONVERSATION_VIEW") or self._register(
            "CONVERSATION_VIEW", default=False,
            description="Conversation-grouped draft view on dashboard"
        )

    @property
    def UNIT_BASED_BILLING(self) -> Flag:
        """Unit-based pricing (Starter/Growth/Pro) instead of channel plans."""
        return self._flags.get("UNIT_BASED_BILLING") or self._register(
            "UNIT_BASED_BILLING", default=True,
            description="Unit-based billing with PlanConfig table"
        )

    @property
    def PROMETHEUS_METRICS(self) -> Flag:
        """Expose /metrics/prometheus endpoint."""
        return self._flags.get("PROMETHEUS_METRICS") or self._register(
            "PROMETHEUS_METRICS", default=True,
            description="Prometheus metrics scrape endpoint"
        )


# Singleton — import this everywhere
flags = FlagRegistry()
# Touch all properties once at import time so they register
_ = [flags.SSE_NOTIFICATIONS, flags.ANALYTICS_PAGE, flags.TEAM_MEMBERS,
     flags.CONVERSATION_VIEW, flags.UNIT_BASED_BILLING, flags.PROMETHEUS_METRICS]


def require_flag(flag_name: str):
    """FastAPI dependency — raises 404 if flag is disabled."""
    from fastapi import HTTPException

    def _check():
        f = flags._flags.get(flag_name)
        if f is None or not f.is_enabled:
            raise HTTPException(status_code=404, detail="Not found")

    return _check
