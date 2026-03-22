# © 2024 Jestin Rajan. All rights reserved.
"""
Security layer:
  - CSRF protection (double-submit signed cookie)
  - In-memory sliding-window rate limiter
  - Security headers middleware (HSTS, CSP, X-Frame-Options, etc.)
"""

import hashlib
import hmac
import os
import secrets
import time
from collections import defaultdict
from threading import Lock
from typing import Callable

from fastapi import HTTPException, Request
from fastapi.responses import Response
from starlette.middleware.base import BaseHTTPMiddleware

_ENVIRONMENT = os.getenv("ENVIRONMENT", "production").lower()
_ALLOW_INSECURE_DEFAULTS = _ENVIRONMENT in {"development", "dev", "test"}


def _load_required_secret(name: str, placeholder: str) -> bytes:
    value = os.getenv(name, "").strip()
    if value and not value.startswith("change-me"):
        return value.encode()
    if _ALLOW_INSECURE_DEFAULTS:
        return (value or placeholder).encode()
    raise RuntimeError(f"{name} must be set in non-development environments")


_SECRET = _load_required_secret("SECRET_KEY", "change-me-long-random-string")
_TRUST_PROXY_HEADERS = os.getenv("TRUST_PROXY_HEADERS", "").strip().lower() in {"1", "true", "yes"}

# ---------------------------------------------------------------------------
# CSRF — double-submit signed cookie
# ---------------------------------------------------------------------------
CSRF_COOKIE = "csrf_token"

# Paths that must never undergo CSRF validation
_CSRF_EXEMPT_PREFIXES = (
    "/billing/stripe-webhook",
    "/wa/webhook/",
    "/sms/webhook/",
    "/health",
    "/metrics",
    "/ping",
    "/api/wa/",
    "/api/workers",
    "/api/drafts",
    "/api/download/",
    "/static/",
    "/pricing",
    "/verify-email",
    "/checkin/",         # Public guest portal — no session, no CSRF
)

_CSRF_SAFE_METHODS = frozenset(("GET", "HEAD", "OPTIONS"))


def _is_csrf_exempt(path: str) -> bool:
    return any(path.startswith(p) for p in _CSRF_EXEMPT_PREFIXES)


def _make_csrf_token() -> str:
    raw = secrets.token_urlsafe(24)
    sig = hmac.new(_SECRET, raw.encode(), hashlib.sha256).hexdigest()[:16]
    return f"{raw}.{sig}"


def _verify_csrf_token(token: str) -> bool:
    try:
        raw, sig = token.rsplit(".", 1)
        expected = hmac.new(_SECRET, raw.encode(), hashlib.sha256).hexdigest()[:16]
        return hmac.compare_digest(sig, expected)
    except Exception:
        return False


def validate_csrf(request: Request, form_token: str | None) -> None:
    """
    Call from POST handlers to validate the CSRF token.
    Raises HTTP 403 on failure.
    """
    cookie_token = request.cookies.get(CSRF_COOKIE, "")
    if not cookie_token or not _verify_csrf_token(cookie_token):
        raise HTTPException(status_code=403, detail="Session expired — please refresh and try again")
    if not form_token:
        raise HTTPException(status_code=403, detail="CSRF token missing from form")
    if not hmac.compare_digest(cookie_token, form_token):
        raise HTTPException(status_code=403, detail="CSRF validation failed — please refresh and try again")


# ---------------------------------------------------------------------------
# CSRF Middleware — sets cookie + request.state.csrf_token on every request
# ---------------------------------------------------------------------------

class CSRFMiddleware(BaseHTTPMiddleware):
    """
    Sets a signed CSRF cookie on every response.
    Templates access the token via {{ request.state.csrf_token }}.
    POST handlers call validate_csrf(request, form_token) to verify.
    """

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        existing = request.cookies.get(CSRF_COOKIE, "")
        if existing and _verify_csrf_token(existing):
            csrf = existing
        else:
            csrf = _make_csrf_token()

        request.state.csrf_token = csrf

        response = await call_next(request)

        is_secure = is_request_secure(request)
        response.set_cookie(
            key=CSRF_COOKIE,
            value=csrf,
            httponly=False,        # forms / JS need to read it
            samesite="strict",
            secure=is_secure,
            max_age=7 * 24 * 3600,
            path="/",
        )
        return response


# ---------------------------------------------------------------------------
# Rate limiter — Redis-backed with in-memory fallback
# Uses Redis sliding window (INCR + EXPIRE) when Redis is available.
# Falls back to in-process sliding window when Redis is not configured.
# ---------------------------------------------------------------------------

_windows: dict[str, list[float]] = defaultdict(list)
_lock = Lock()


def _rate_limit_redis(r, key: str, max_requests: int, window_seconds: int) -> None:
    """Redis-backed counter rate limit (fixed window per Redis key TTL)."""
    rkey = f"rl:{key}"
    try:
        current = r.incr(rkey)
        if current == 1:
            r.expire(rkey, window_seconds)
        if current > max_requests:
            raise HTTPException(
                status_code=429,
                detail="Too many requests — please wait and try again",
            )
    except HTTPException:
        raise
    except Exception:
        # Redis error — fall through to in-memory
        _rate_limit_memory(key, max_requests, window_seconds)


def _rate_limit_memory(key: str, max_requests: int, window_seconds: int) -> None:
    """In-process sliding window rate limiter (single-worker only)."""
    now = time.monotonic()
    cutoff = now - window_seconds
    with _lock:
        hits = _windows[key]
        while hits and hits[0] < cutoff:
            hits.pop(0)
        if len(hits) >= max_requests:
            raise HTTPException(
                status_code=429,
                detail="Too many requests — please wait and try again",
            )
        hits.append(now)


def rate_limit(key: str, max_requests: int, window_seconds: int) -> None:
    """
    Enforce a rate limit. Uses Redis when available, in-memory as fallback.
    Raises HTTP 429 if the key has exceeded max_requests within window_seconds.
    """
    from web.redis_client import get_redis
    r = get_redis()
    if r is not None:
        _rate_limit_redis(r, key, max_requests, window_seconds)
    else:
        _rate_limit_memory(key, max_requests, window_seconds)


def client_ip(request: Request) -> str:
    """Best-effort client IP; only trusts proxy headers when explicitly enabled."""
    forwarded = request.headers.get("X-Forwarded-For", "") if _TRUST_PROXY_HEADERS else ""
    if forwarded:
        return forwarded.split(",")[0].strip()
    return (request.client.host if request.client else "unknown")


def is_request_secure(request: Request) -> bool:
    """Return True when the request is HTTPS (optionally via trusted proxy headers)."""
    if request.url.scheme == "https":
        return True
    if _TRUST_PROXY_HEADERS:
        forwarded = request.headers.get("X-Forwarded-Proto", "")
        proto = forwarded.split(",")[0].strip().lower()
        return proto == "https"
    return False


# ---------------------------------------------------------------------------
# Security headers middleware
# ---------------------------------------------------------------------------

class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Adds security headers to every response."""

    # NOTE: 'unsafe-inline' in script-src exists because the Jinja2 templates
    # embed inline <script> blocks (HTMX event wiring, dashboard polling).
    # To remove it: extract all inline JS to /static/*.js files and add a
    # per-request nonce via request.state, then pass that nonce into both the
    # CSP header and each <script nonce="..."> tag.
    # 'unsafe-inline' in style-src is required by per-page <style> blocks in
    # templates and is lower-risk than script-src.
    _CSP = (
        "default-src 'self'; "
        "script-src 'self' https://unpkg.com 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; "
        "font-src 'self'; "
        "connect-src 'self'; "
        "object-src 'none'; "
        "base-uri 'self'; "
        "frame-ancestors 'none';"
    )

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        response = await call_next(request)
        h = response.headers
        h["X-Content-Type-Options"]  = "nosniff"
        h["X-Frame-Options"]          = "DENY"
        h["X-XSS-Protection"]         = "0"        # Deprecated; CSP handles this
        h["Referrer-Policy"]          = "strict-origin-when-cross-origin"
        h["Permissions-Policy"]       = "geolocation=(), microphone=(), camera=()"
        h["Content-Security-Policy"]  = self._CSP
        if is_request_secure(request):
            h["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains; preload"
        return response
