"""Tests for SSRF protection (web.request_safety).

Validates that tenant-supplied hostnames and URLs pointing at loopback,
private, link-local, or multicast addresses are rejected, while real
public addresses are accepted.
"""

import pytest
from unittest.mock import patch

from web.request_safety import ensure_public_hostname, ensure_public_url


# ---------------------------------------------------------------------------
# Helpers — patch socket.getaddrinfo to avoid real DNS in unit tests
# ---------------------------------------------------------------------------

def _mock_resolve(ip: str):
    """Return a getaddrinfo patch that resolves to the given IP."""
    import socket
    return patch(
        "web.request_safety.socket.getaddrinfo",
        return_value=[(socket.AF_INET, None, None, None, (ip, 0))],
    )


# ---------------------------------------------------------------------------
# ensure_public_hostname — direct IP inputs (no DNS)
# ---------------------------------------------------------------------------

class TestEnsurePublicHostnameBlocksPrivateIPs:
    def test_loopback_ipv4_rejected(self):
        with _mock_resolve("127.0.0.1"):
            with pytest.raises(ValueError, match="non-public"):
                ensure_public_hostname("localhost")

    def test_loopback_127_x_rejected(self):
        with _mock_resolve("127.0.0.2"):
            with pytest.raises(ValueError, match="non-public"):
                ensure_public_hostname("anything")

    def test_private_10_block_rejected(self):
        with _mock_resolve("10.0.0.1"):
            with pytest.raises(ValueError, match="non-public"):
                ensure_public_hostname("internal.corp")

    def test_private_172_16_block_rejected(self):
        with _mock_resolve("172.16.0.1"):
            with pytest.raises(ValueError, match="non-public"):
                ensure_public_hostname("internal.corp")

    def test_private_192_168_block_rejected(self):
        with _mock_resolve("192.168.1.1"):
            with pytest.raises(ValueError, match="non-public"):
                ensure_public_hostname("router.local")

    def test_link_local_169_254_rejected(self):
        with _mock_resolve("169.254.169.254"):
            # Common cloud metadata endpoint — must be blocked
            with pytest.raises(ValueError, match="non-public"):
                ensure_public_hostname("169.254.169.254")

    def test_multicast_rejected(self):
        with _mock_resolve("224.0.0.1"):
            with pytest.raises(ValueError, match="non-public"):
                ensure_public_hostname("multicast.example")


class TestEnsurePublicHostnameAllowsPublicIPs:
    def test_public_ip_accepted(self):
        with _mock_resolve("93.184.216.34"):  # example.com
            result = ensure_public_hostname("example.com")
            assert result == "example.com"

    def test_leading_trailing_whitespace_stripped(self):
        with _mock_resolve("93.184.216.34"):
            result = ensure_public_hostname("  example.com  ")
            assert result == "example.com"

    def test_trailing_dot_stripped(self):
        with _mock_resolve("93.184.216.34"):
            result = ensure_public_hostname("example.com.")
            assert result == "example.com"


class TestEnsurePublicHostnameEdgeCases:
    def test_empty_hostname_rejected(self):
        with pytest.raises(ValueError, match="required"):
            ensure_public_hostname("")

    def test_whitespace_only_rejected(self):
        with pytest.raises(ValueError, match="required"):
            ensure_public_hostname("   ")

    def test_unresolvable_hostname_rejected(self):
        import socket
        with patch("web.request_safety.socket.getaddrinfo", side_effect=socket.gaierror):
            with pytest.raises(ValueError, match="resolve"):
                ensure_public_hostname("this-does-not-exist.invalid")


# ---------------------------------------------------------------------------
# ensure_public_url
# ---------------------------------------------------------------------------

class TestEnsurePublicUrl:
    def test_non_http_scheme_rejected(self):
        with pytest.raises(ValueError, match="http"):
            ensure_public_url("ftp://example.com/feed")

    def test_file_scheme_rejected(self):
        with pytest.raises(ValueError, match="http"):
            ensure_public_url("file:///etc/passwd")

    def test_missing_host_rejected(self):
        with pytest.raises(ValueError):
            ensure_public_url("https://")

    def test_private_ip_in_url_rejected(self):
        with _mock_resolve("192.168.1.1"):
            with pytest.raises(ValueError, match="non-public"):
                ensure_public_url("https://192.168.1.1/feed")

    def test_metadata_endpoint_rejected(self):
        """AWS/GCP instance metadata endpoint must be blocked."""
        with _mock_resolve("169.254.169.254"):
            with pytest.raises(ValueError, match="non-public"):
                ensure_public_url("http://169.254.169.254/latest/meta-data/")

    def test_public_https_url_accepted(self):
        with _mock_resolve("93.184.216.34"):
            result = ensure_public_url("https://example.com/calendar.ics")
            assert result == "https://example.com/calendar.ics"

    def test_public_http_url_accepted(self):
        with _mock_resolve("93.184.216.34"):
            result = ensure_public_url("http://example.com/feed")
            assert result == "http://example.com/feed"

    def test_allowed_hosts_enforced(self):
        with _mock_resolve("93.184.216.34"):
            with pytest.raises(ValueError, match="not allowed"):
                ensure_public_url(
                    "https://example.com/feed",
                    allowed_hosts={"airbnb.com"},
                )

    def test_allowed_hosts_subdomain_passes(self):
        with _mock_resolve("93.184.216.34"):
            result = ensure_public_url(
                "https://www.airbnb.com/calendar.ics",
                allowed_hosts={"airbnb.com"},
            )
            assert "airbnb.com" in result

    def test_allowed_hosts_exact_match_passes(self):
        with _mock_resolve("93.184.216.34"):
            result = ensure_public_url(
                "https://airbnb.com/calendar.ics",
                allowed_hosts={"airbnb.com"},
            )
            assert result == "https://airbnb.com/calendar.ics"
