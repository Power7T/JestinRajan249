# © 2024 Jestin Rajan. All rights reserved.
"""
Module-level Prometheus metric definitions.

All metrics use the default prometheus_client registry so they accumulate
across requests (counters/histograms need a singleton — do NOT create them
inside a request handler or they reset on every scrape).

Usage:
    from web.metrics_prom import REQUEST_COUNT, REQUEST_DURATION, record_message_sent
    REQUEST_COUNT.labels(method="GET", path="/dashboard", status=200).inc()
    REQUEST_DURATION.labels(method="GET", path="/dashboard").observe(0.043)
    record_message_sent("whatsapp", "ok")
"""

import re
from prometheus_client import Counter, Histogram, Gauge

# ---------------------------------------------------------------------------
# HTTP request metrics
# ---------------------------------------------------------------------------

REQUEST_COUNT = Counter(
    "hostai_http_requests_total",
    "Total HTTP requests handled",
    ["method", "path", "status"],
)

REQUEST_DURATION = Histogram(
    "hostai_http_request_duration_seconds",
    "HTTP request duration in seconds",
    ["method", "path"],
    buckets=[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0],
)

# ---------------------------------------------------------------------------
# Business metrics
# ---------------------------------------------------------------------------

MESSAGES_SENT = Counter(
    "hostai_messages_sent_total",
    "Outbound messages attempted",
    ["channel", "result"],  # channel: whatsapp/sms/email  result: ok/error
)

DRAFTS_ACTIONED = Counter(
    "hostai_drafts_actioned_total",
    "Drafts approved, edited, or skipped",
    ["action"],  # approve / edit / skip
)

STRIPE_WEBHOOKS = Counter(
    "hostai_stripe_webhooks_total",
    "Stripe webhook events received",
    ["event_type"],
)

WORKER_ERRORS = Counter(
    "hostai_worker_errors_total",
    "Background worker errors",
    ["worker_type"],  # email / watchdog / kpi / retention
)

INBOUND_MESSAGES = Counter(
    "hostai_inbound_messages_total",
    "Inbound guest/vendor messages received",
    ["channel"],  # email / whatsapp / sms / baileys
)

# ---------------------------------------------------------------------------
# Path normalisation — prevents cardinality explosion from IDs in URLs
# ---------------------------------------------------------------------------
# Matches: UUIDs, long opaque tokens (30+ chars), and plain integers
_PARAM_RE = re.compile(
    r"(?<=/)"
    r"(?:"
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"  # UUID
    r"|[A-Za-z0-9_\-]{32,}"    # long token / hash
    r"|\d+"                    # integer ID
    r")"
)


def normalize_path(path: str) -> str:
    """Replace dynamic path segments with `{id}` to limit label cardinality."""
    return _PARAM_RE.sub("{id}", path)


# ---------------------------------------------------------------------------
# Convenience helpers (keeps call sites clean)
# ---------------------------------------------------------------------------

def record_message_sent(channel: str, result: str = "ok") -> None:
    MESSAGES_SENT.labels(channel=channel, result=result).inc()


def record_draft_action(action: str) -> None:
    DRAFTS_ACTIONED.labels(action=action).inc()


def record_stripe_event(event_type: str) -> None:
    STRIPE_WEBHOOKS.labels(event_type=event_type).inc()


def record_worker_error(worker_type: str) -> None:
    WORKER_ERRORS.labels(worker_type=worker_type).inc()


def record_inbound(channel: str) -> None:
    INBOUND_MESSAGES.labels(channel=channel).inc()
