#!/usr/bin/env python3
"""
Synthetic monitoring script — runs key health checks against the live app.

Run manually:
    python scripts/synthetic_monitor.py --url https://your-app.com

Run as a cron (every 5 minutes):
    */5 * * * * python /app/scripts/synthetic_monitor.py --url https://your-app.com

Exit codes:
    0 — all checks passed
    1 — one or more checks failed (triggers PagerDuty/alerting if wired up)

Environment variables (optional):
    MONITOR_URL          — base URL (overrides --url)
    MONITOR_TIMEOUT      — request timeout seconds (default 10)
    MONITOR_ALERT_EMAIL  — if set, sends email on failure (requires SMTP_* vars)
    METRICS_TOKEN        — bearer token for /metrics endpoints
    MONITOR_SLACK_WEBHOOK — Slack webhook URL for failure alerts
"""

import argparse
import json
import os
import sys
import time
import urllib.request
import urllib.error
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


# ---------------------------------------------------------------------------
# Check result
# ---------------------------------------------------------------------------

@dataclass
class CheckResult:
    name: str
    passed: bool
    duration_ms: float
    detail: str = ""
    status_code: Optional[int] = None


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------

def _get(url: str, timeout: int, headers: dict | None = None) -> tuple[int, bytes, float]:
    """Returns (status_code, body_bytes, duration_ms)."""
    req = urllib.request.Request(url, headers=headers or {})
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read()
        duration_ms = (time.perf_counter() - t0) * 1000
        return resp.status, body, duration_ms


def check_ping(base_url: str, timeout: int) -> CheckResult:
    """GET /ping — must return {"ok": true} in < 2s."""
    url = f"{base_url}/ping"
    try:
        status, body, ms = _get(url, timeout)
        data = json.loads(body)
        passed = status == 200 and data.get("ok") is True
        return CheckResult("ping", passed, ms, f"status={status}", status)
    except Exception as exc:
        return CheckResult("ping", False, 0, str(exc))


def check_health(base_url: str, timeout: int) -> CheckResult:
    """GET /health — must return 200 with db=ok."""
    url = f"{base_url}/health"
    try:
        status, body, ms = _get(url, timeout)
        data = json.loads(body)
        db_ok = data.get("db") == "ok"
        passed = status == 200 and db_ok
        detail = f"db={data.get('db')} redis={data.get('redis')}"
        return CheckResult("health", passed, ms, detail, status)
    except Exception as exc:
        return CheckResult("health", False, 0, str(exc))


def check_login_page(base_url: str, timeout: int) -> CheckResult:
    """GET /login — must return HTML with login form."""
    url = f"{base_url}/login"
    try:
        status, body, ms = _get(url, timeout)
        html = body.decode(errors="replace")
        has_form = 'action="/login"' in html or "Sign in" in html or "Log in" in html
        passed = status == 200 and has_form
        detail = f"status={status} has_form={has_form}"
        return CheckResult("login_page", passed, ms, detail, status)
    except Exception as exc:
        return CheckResult("login_page", False, 0, str(exc))


def check_pricing_page(base_url: str, timeout: int) -> CheckResult:
    """GET /pricing — must return 200."""
    url = f"{base_url}/pricing"
    try:
        status, body, ms = _get(url, timeout)
        passed = status == 200 and len(body) > 1000
        return CheckResult("pricing_page", passed, ms, f"status={status} size={len(body)}b", status)
    except Exception as exc:
        return CheckResult("pricing_page", False, 0, str(exc))


def check_metrics(base_url: str, timeout: int, metrics_token: str) -> CheckResult:
    """GET /metrics — must return JSON with db=ok."""
    url = f"{base_url}/metrics"
    headers = {"Authorization": f"Bearer {metrics_token}"} if metrics_token else {}
    try:
        status, body, ms = _get(url, timeout, headers)
        if status == 404:
            # No token configured — skip rather than fail
            return CheckResult("metrics", True, ms, "skipped (no METRICS_TOKEN)", status)
        data = json.loads(body)
        passed = status == 200 and data.get("db") == "ok"
        detail = f"db={data.get('db')} redis={data.get('redis')} tenants={data.get('total_tenants')}"
        return CheckResult("metrics", passed, ms, detail, status)
    except Exception as exc:
        return CheckResult("metrics", False, 0, str(exc))


def check_latency_slo(results: list[CheckResult], threshold_ms: float = 2000) -> CheckResult:
    """Verify all successful checks completed within SLO threshold."""
    slow = [r for r in results if r.passed and r.duration_ms > threshold_ms]
    passed = len(slow) == 0
    detail = f"slow checks: {[r.name for r in slow]}" if slow else f"all under {threshold_ms}ms"
    max_ms = max((r.duration_ms for r in results if r.passed), default=0)
    return CheckResult("latency_slo", passed, max_ms, detail)


# ---------------------------------------------------------------------------
# Alerting
# ---------------------------------------------------------------------------

def _send_slack(webhook_url: str, message: str) -> None:
    payload = json.dumps({"text": message}).encode()
    req = urllib.request.Request(
        webhook_url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5):
            pass
    except Exception as exc:
        print(f"  [warn] Slack alert failed: {exc}", file=sys.stderr)


def _send_email_alert(failed: list[CheckResult], base_url: str) -> None:
    """Send failure alert via SMTP if configured."""
    to = os.getenv("MONITOR_ALERT_EMAIL", "")
    if not to:
        return
    smtp_host = os.getenv("SMTP_HOST", "")
    smtp_user = os.getenv("SMTP_USER", "")
    smtp_pass = os.getenv("SMTP_PASS", "")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    if not all([smtp_host, smtp_user, smtp_pass]):
        return

    import smtplib
    from email.message import EmailMessage

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    body_lines = [f"Synthetic monitor FAILED at {now}", f"URL: {base_url}", ""]
    for r in failed:
        body_lines.append(f"  FAIL  {r.name}: {r.detail}")

    msg = EmailMessage()
    msg["Subject"] = f"[HostAI] Monitor alert — {len(failed)} check(s) failed"
    msg["From"] = smtp_user
    msg["To"] = to
    msg.set_content("\n".join(body_lines))

    try:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=10) as s:
            s.ehlo()
            s.starttls()
            s.login(smtp_user, smtp_pass)
            s.send_message(msg)
        print(f"  Alert email sent to {to}")
    except Exception as exc:
        print(f"  [warn] Alert email failed: {exc}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run_checks(base_url: str, timeout: int, metrics_token: str) -> list[CheckResult]:
    base_url = base_url.rstrip("/")
    checks = [
        check_ping(base_url, timeout),
        check_health(base_url, timeout),
        check_login_page(base_url, timeout),
        check_pricing_page(base_url, timeout),
        check_metrics(base_url, timeout, metrics_token),
    ]
    checks.append(check_latency_slo(checks))
    return checks


def main() -> int:
    parser = argparse.ArgumentParser(description="HostAI synthetic monitor")
    parser.add_argument("--url", default=os.getenv("MONITOR_URL", "http://localhost:8000"))
    parser.add_argument("--timeout", type=int, default=int(os.getenv("MONITOR_TIMEOUT", "10")))
    parser.add_argument("--json", action="store_true", help="Output JSON instead of text")
    args = parser.parse_args()

    metrics_token = os.getenv("METRICS_TOKEN", "")
    slack_webhook = os.getenv("MONITOR_SLACK_WEBHOOK", "")

    results = run_checks(args.url, args.timeout, metrics_token)
    failed = [r for r in results if not r.passed]
    all_passed = len(failed) == 0

    if args.json:
        output = {
            "ok": all_passed,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "url": args.url,
            "checks": [
                {"name": r.name, "passed": r.passed,
                 "duration_ms": round(r.duration_ms, 1), "detail": r.detail}
                for r in results
            ],
        }
        print(json.dumps(output, indent=2))
    else:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        print(f"\nHostAI Synthetic Monitor  —  {args.url}  —  {now}\n")
        for r in results:
            icon = "PASS" if r.passed else "FAIL"
            ms_str = f"{r.duration_ms:6.0f}ms" if r.duration_ms else "      -"
            print(f"  [{icon}]  {r.name:<20}  {ms_str}  {r.detail}")
        print()
        if all_passed:
            print("  All checks passed.")
        else:
            print(f"  {len(failed)} check(s) FAILED: {[r.name for r in failed]}")
        print()

    if not all_passed:
        if slack_webhook:
            lines = [f"*HostAI monitor FAILED* — {args.url}"]
            for r in failed:
                lines.append(f"  :x: `{r.name}`: {r.detail}")
            _send_slack(slack_webhook, "\n".join(lines))
        _send_email_alert(failed, args.url)

    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
