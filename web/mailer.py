# © 2024 Jestin Rajan. All rights reserved.
"""
Transactional email sender for:
  - Email address verification
  - Password reset

Uses SMTP (works with any provider: Gmail, SendGrid, Mailgun, AWS SES, etc.)
Configure via environment variables:
  SMTP_HOST, SMTP_PORT (default 587), SMTP_USER, SMTP_PASS, SMTP_FROM
"""

import logging
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

log = logging.getLogger(__name__)

SMTP_HOST    = os.getenv("SMTP_HOST", "")
SMTP_PORT    = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER    = os.getenv("SMTP_USER", "")
SMTP_PASS    = os.getenv("SMTP_PASS", "")
SMTP_FROM    = os.getenv("SMTP_FROM", SMTP_USER) or "noreply@hostai.app"

_ENVIRONMENT = os.getenv("ENVIRONMENT", "production").lower()
_ALLOW_INSECURE_DEFAULTS = _ENVIRONMENT in {"development", "dev", "test"}
_APP_BASE_URL_RAW = os.getenv("APP_BASE_URL", "").strip().rstrip("/")
if not _APP_BASE_URL_RAW or _APP_BASE_URL_RAW == "https://your-domain.com":
    if not _ALLOW_INSECURE_DEFAULTS:
        raise RuntimeError(
            "APP_BASE_URL must be set to your actual public domain in production "
            "(e.g. APP_BASE_URL=https://hostai.fly.dev). "
            "Email verification and password-reset links will be broken without it."
        )
    _APP_BASE_URL_RAW = _APP_BASE_URL_RAW or "http://localhost:8000"
APP_BASE_URL = _APP_BASE_URL_RAW
APP_NAME     = "HostAI"


def validate_smtp_config() -> bool:
    """
    Test SMTP credentials at startup. Logs a clear error if misconfigured.
    Call this from app startup so you find out immediately, not on first email send.
    Returns True if SMTP works, False if not configured or credentials fail.
    """
    if not SMTP_HOST or not SMTP_USER:
        log.warning("SMTP not configured — email sending disabled (set SMTP_HOST, SMTP_USER, SMTP_PASS)")
        return False
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10) as server:
            server.ehlo()
            server.starttls()
            server.login(SMTP_USER, SMTP_PASS)
        log.info("SMTP connection verified: %s:%s as %s", SMTP_HOST, SMTP_PORT, SMTP_USER)
        return True
    except smtplib.SMTPAuthenticationError:
        log.error(
            "SMTP authentication FAILED for %s@%s — email will not work. "
            "Check SMTP_PASS (use an app password if 2FA is enabled).",
            SMTP_USER, SMTP_HOST,
        )
        return False
    except Exception as exc:
        log.warning("SMTP connection test failed (%s) — emails may not work: %s", SMTP_HOST, exc)
        return False


def _send(to: str, subject: str, html: str) -> bool:
    """Send an HTML email. Returns True on success, False on failure."""
    if not SMTP_HOST or not SMTP_USER:
        log.warning("SMTP not configured — cannot send email to %s (subject: %s)", to, subject)
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = f"{APP_NAME} <{SMTP_FROM}>"
    msg["To"]      = to
    msg.attach(MIMEText(html, "html"))

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as server:
            server.ehlo()
            server.starttls()
            server.login(SMTP_USER, SMTP_PASS)
            server.sendmail(SMTP_FROM, [to], msg.as_string())
        log.info("Email sent to %s: %s", to, subject)
        return True
    except Exception as exc:
        log.error("Failed to send email to %s: %s", to, exc)
        return False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def send_verification_email(to: str, token: str) -> bool:
    url = f"{APP_BASE_URL}/verify-email?token={token}"
    html = f"""
    <div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;max-width:520px;margin:0 auto;padding:2rem;color:#212529">
      <h2 style="color:#e00b27;margin-bottom:1rem">🏠 {APP_NAME} — Verify your email</h2>
      <p>Thanks for signing up! Click the button below to verify your email address and activate your account.</p>
      <p style="margin:2rem 0">
        <a href="{url}" style="background:#e00b27;color:#fff;padding:12px 28px;border-radius:6px;text-decoration:none;font-weight:600;display:inline-block">
          Verify Email Address
        </a>
      </p>
      <p style="color:#6c757d;font-size:0.85rem">
        This link expires in 24 hours. If you didn't create an account, you can safely ignore this email.
      </p>
      <p style="color:#adb5bd;font-size:0.75rem">
        Or copy this URL: {url}
      </p>
    </div>
    """
    return _send(to, f"{APP_NAME} — Verify your email address", html)


def send_welcome_email(to: str, property_name: str) -> bool:
    url = f"{APP_BASE_URL}/dashboard"
    display_name = property_name.strip() if property_name and property_name.strip() else "your property"
    html = f"""
    <div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;max-width:520px;margin:0 auto;padding:2rem;color:#212529">
      <h2 style="color:#e00b27;margin-bottom:1rem">🏠 {APP_NAME} is live for {display_name}!</h2>
      <p style="margin-bottom:1rem">Your setup is complete. Here's what happens now:</p>
      <ul style="margin-bottom:1.5rem;padding-left:1.25rem;line-height:2">
        <li>When a guest emails you, {APP_NAME} drafts a reply automatically</li>
        <li>It appears on your dashboard within seconds for your approval</li>
        <li>You approve, edit, or skip — the reply sends instantly</li>
        <li>Guest messages in any language are handled and replied to in the same language</li>
      </ul>
      <p style="margin:2rem 0">
        <a href="{url}" style="background:#e00b27;color:#fff;padding:12px 28px;border-radius:6px;text-decoration:none;font-weight:600;display:inline-block">
          Go to Dashboard →
        </a>
      </p>
      <p style="color:#6c757d;font-size:0.85rem">
        Your first real draft will appear when your next guest message arrives.
        Until then, you can use the "Simulate a Guest" button on your dashboard to see a live preview.
      </p>
    </div>
    """
    return _send(to, f"{APP_NAME} is live for {display_name}!", html)


def send_escalation_alert(to: str, guest_name: str, guest_message: str) -> bool:
    html = f"""
    <div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;max-width:520px;margin:0 auto;padding:2rem;color:#212529">
      <h2 style="color:#dc3545;margin-bottom:1rem">⚠️ {APP_NAME} — Human attention needed</h2>
      <p>A guest message requires your immediate attention. {APP_NAME} has flagged it as needing a human response.</p>
      <div style="background:#f8d7da;border:1px solid #f5c6cb;border-radius:6px;padding:1rem;margin:1.5rem 0">
        <p style="font-size:0.85rem;font-weight:600;margin-bottom:0.5rem">Guest: {guest_name}</p>
        <p style="font-size:0.875rem;white-space:pre-wrap">{guest_message[:1000]}</p>
      </div>
      <p style="margin:1.5rem 0">
        <a href="{APP_BASE_URL}/dashboard" style="background:#dc3545;color:#fff;padding:12px 28px;border-radius:6px;text-decoration:none;font-weight:600;display:inline-block">
          View on Dashboard →
        </a>
      </p>
      <p style="color:#6c757d;font-size:0.85rem">
        The draft has been saved for your review. Please respond to the guest directly or edit the draft before sending.
      </p>
    </div>
    """
    return _send(to, f"{APP_NAME} — Urgent: {guest_name} needs immediate attention", html)


def send_weekly_digest(to: str, stats: dict) -> bool:
    """
    Send a weekly performance digest email.

    stats keys (all optional with sensible defaults):
      property_name, week_label,
      drafts_total, approved, skipped, escalations,
      approval_rate, approval_streak,
      active_stays, upcoming_checkins,
      occupancy_gaps (list of dicts with gap_nights/gap_start/gap_end),
      review_velocity
    """
    property_name   = stats.get("property_name", "your property")
    week_label      = stats.get("week_label", "this week")
    drafts_total    = stats.get("drafts_total", 0)
    approved        = stats.get("approved", 0)
    skipped         = stats.get("skipped", 0)
    escalations     = stats.get("escalations", 0)
    approval_rate   = stats.get("approval_rate", 0.0)
    streak          = stats.get("approval_streak", 0)
    active_stays    = stats.get("active_stays", 0)
    upcoming        = stats.get("upcoming_checkins", 0)
    gaps            = stats.get("occupancy_gaps", [])
    review_velocity = stats.get("review_velocity")
    url             = f"{APP_BASE_URL}/dashboard"

    streak_badge = f"🔥 {streak}-draft approval streak!" if streak >= 3 else ""

    gap_rows = ""
    for g in gaps[:5]:
        gap_start = g.get("gap_start", "")
        gap_end   = g.get("gap_end", "")
        nights    = g.get("gap_nights", "?")
        if hasattr(gap_start, "strftime"):
            gap_start = gap_start.strftime("%b %d")
        if hasattr(gap_end, "strftime"):
            gap_end = gap_end.strftime("%b %d")
        gap_rows += (
            f"<tr><td style='padding:4px 8px;border-bottom:1px solid #dee2e6'>{gap_start} – {gap_end}</td>"
            f"<td style='padding:4px 8px;border-bottom:1px solid #dee2e6'>{nights} nights</td></tr>"
        )

    gap_section = ""
    if gap_rows:
        gap_section = f"""
      <p style="margin-top:1.5rem;font-weight:600">Occupancy gaps to fill:</p>
      <table style="width:100%;border-collapse:collapse;font-size:0.875rem">
        <thead><tr>
          <th style="text-align:left;padding:4px 8px;background:#f8f9fa">Gap window</th>
          <th style="text-align:left;padding:4px 8px;background:#f8f9fa">Duration</th>
        </tr></thead>
        <tbody>{gap_rows}</tbody>
      </table>"""

    review_line = ""
    if review_velocity is not None:
        review_line = f"<li>Review velocity: <strong>{review_velocity:.1f} reviews/30 days</strong></li>"

    html = f"""
    <div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;max-width:520px;margin:0 auto;padding:2rem;color:#212529">
      <h2 style="color:#e00b27;margin-bottom:0.5rem">🏠 {APP_NAME} — Weekly digest</h2>
      <p style="color:#6c757d;margin-top:0">{property_name} · {week_label}</p>
      {"<p style='background:#fff3cd;border:1px solid #ffc107;border-radius:6px;padding:0.75rem;font-weight:600'>" + streak_badge + "</p>" if streak_badge else ""}
      <ul style="line-height:2;padding-left:1.25rem">
        <li>Drafts generated: <strong>{drafts_total}</strong></li>
        <li>Approved: <strong>{approved}</strong> &nbsp;·&nbsp; Skipped: <strong>{skipped}</strong> &nbsp;·&nbsp; Escalations: <strong>{escalations}</strong></li>
        <li>Approval rate: <strong>{approval_rate:.0f}%</strong></li>
        <li>Active stays: <strong>{active_stays}</strong> &nbsp;·&nbsp; Upcoming check-ins: <strong>{upcoming}</strong></li>
        {review_line}
      </ul>
      {gap_section}
      <p style="margin:2rem 0">
        <a href="{url}" style="background:#e00b27;color:#fff;padding:12px 28px;border-radius:6px;text-decoration:none;font-weight:600;display:inline-block">
          View Dashboard →
        </a>
      </p>
      <p style="color:#adb5bd;font-size:0.75rem">You're receiving this because you're a {APP_NAME} host. Manage preferences in Settings.</p>
    </div>
    """
    return _send(to, f"{APP_NAME} — Weekly digest for {property_name}", html)


def send_password_reset_email(to: str, token: str) -> bool:
    url = f"{APP_BASE_URL}/reset-password?token={token}"
    html = f"""
    <div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;max-width:520px;margin:0 auto;padding:2rem;color:#212529">
      <h2 style="color:#e00b27;margin-bottom:1rem">🏠 {APP_NAME} — Reset your password</h2>
      <p>We received a request to reset the password for your {APP_NAME} account.</p>
      <p style="margin:2rem 0">
        <a href="{url}" style="background:#e00b27;color:#fff;padding:12px 28px;border-radius:6px;text-decoration:none;font-weight:600;display:inline-block">
          Reset Password
        </a>
      </p>
      <p style="color:#6c757d;font-size:0.85rem">
        This link expires in 1 hour. If you didn't request a password reset, you can safely ignore this email — your password will not change.
      </p>
      <p style="color:#adb5bd;font-size:0.75rem">
        Or copy this URL: {url}
      </p>
    </div>
    """
    return _send(to, f"{APP_NAME} — Reset your password", html)
