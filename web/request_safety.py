"""
Helpers for validating tenant-supplied outbound hosts and URLs.

These checks are intentionally conservative: they allow public internet targets
while rejecting loopback, private, link-local, and otherwise non-routable
addresses that would expose internal services in a hosted deployment.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse


def _is_public_ip(value: str) -> bool:
    try:
        ip = ipaddress.ip_address(value)
    except ValueError:
        return False
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def ensure_public_hostname(hostname: str) -> str:
    """
    Return the normalized hostname if it resolves only to public IPs.
    Raises ValueError when the hostname is empty, unresolvable, or points to a
    non-public address.
    """
    host = (hostname or "").strip().rstrip(".").lower()
    if not host:
        raise ValueError("Host is required")

    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValueError(f"Could not resolve host: {host}") from exc

    seen_ips: set[str] = set()
    for family, _, _, _, sockaddr in infos:
        if family == socket.AF_INET:
            seen_ips.add(sockaddr[0])
        elif family == socket.AF_INET6:
            seen_ips.add(sockaddr[0])

    if not seen_ips:
        raise ValueError(f"Could not resolve host: {host}")
    for ip in seen_ips:
        if not _is_public_ip(ip):
            raise ValueError(f"Host resolves to a non-public address: {host}")

    return host


def ensure_public_url(url: str, *, allowed_hosts: set[str] | None = None) -> str:
    """
    Validate a tenant-supplied URL before any outbound fetch.

    When `allowed_hosts` is provided, the hostname must exactly match one of the
    allowed values or be a subdomain of one of them.
    """
    raw = (url or "").strip()
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("URL must start with http:// or https://")
    if not parsed.hostname:
        raise ValueError("URL host is missing")

    host = ensure_public_hostname(parsed.hostname)
    if allowed_hosts:
        if not any(host == allowed or host.endswith(f".{allowed}") for allowed in allowed_hosts):
            raise ValueError("URL host is not allowed")

    return raw
