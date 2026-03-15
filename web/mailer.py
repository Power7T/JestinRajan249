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
APP_BASE_URL = os.getenv("APP_BASE_URL", "https://your-domain.com")
APP_NAME     = "HostAI"


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
