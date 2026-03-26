#!/usr/bin/env python3
"""
License Check — Airbnb Host Assistant
======================================
Called by start.sh on every startup. Validates the LICENSE_KEY against the
license server. Exits 1 if the key is invalid or expired.

In trial / dev mode (no LICENSE_KEY set), prints a reminder and continues.
Includes a 24-hour grace cache so the system keeps running if the license
server is temporarily unreachable.

© 2024 Jestin Rajan. All rights reserved.
Licensed under the Airbnb Host AI License Agreement.
Unauthorized copying, distribution or use is prohibited.
"""

import os
import sys
import json
import hashlib
import socket
import time
import urllib.request
import urllib.error
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass

LICENSE_KEY    = os.getenv("LICENSE_KEY", "").strip()
LICENSE_SERVER = os.getenv("LICENSE_SERVER", "https://license.yourdomain.com/verify")
LICENSE_BUY_URL = os.getenv("LICENSE_BUY_URL", "https://yourdomain.com/buy")
LICENSE_RENEW_URL = os.getenv("LICENSE_RENEW_URL", "https://yourdomain.com/renew")
_CACHE_FILE    = Path(__file__).parent / ".license_cache"
_GRACE_SECONDS = 24 * 3600   # 24 hours of offline grace

YELLOW = "\033[1;33m"
RED    = "\033[0;31m"
GREEN  = "\033[0;32m"
NC     = "\033[0m"


# ---------------------------------------------------------------------------
# Machine fingerprint — stable across reboots, unique per machine
# ---------------------------------------------------------------------------

def _machine_id() -> str:
    parts = [socket.gethostname()]
    try:
        import uuid
        parts.append(str(uuid.getnode()))   # hardware MAC address
    except Exception:
        pass
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:32]


# ---------------------------------------------------------------------------
# Grace cache — lets the system work offline for GRACE_SECONDS
# ---------------------------------------------------------------------------

def _cache_valid() -> bool:
    try:
        if not _CACHE_FILE.exists():
            return False
        data = json.loads(_CACHE_FILE.read_text())
        return (time.time() - float(data.get("ts", 0))) < _GRACE_SECONDS
    except Exception:
        return False


def _write_cache():
    try:
        _CACHE_FILE.write_text(json.dumps({"ts": time.time(), "key": LICENSE_KEY}))
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Online verification
# ---------------------------------------------------------------------------

def _verify_online() -> bool:
    """POST to license server. Returns True if valid."""
    payload = json.dumps({
        "key":        LICENSE_KEY,
        "machine_id": _machine_id(),
    }).encode()
    req = urllib.request.Request(
        LICENSE_SERVER,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read().decode())
        return bool(data.get("valid", False))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    # Trial / dev mode: no key set
    if not LICENSE_KEY:
        print(
            f"{YELLOW}[license]{NC} ⚠️  Running in trial mode (no LICENSE_KEY set).\n"
            f"{YELLOW}[license]{NC}    Get a license at: {LICENSE_BUY_URL}"
        )
        return   # don't block startup

    print(f"[license] Verifying license key ... ", end="", flush=True)

    # Try online
    try:
        if _verify_online():
            print(f"{GREEN}✓ valid{NC}")
            _write_cache()
            return
        else:
            print(f"{RED}✗ invalid{NC}")
            print(
                f"{RED}[license]{NC} ❌  Invalid or expired license key.\n"
                f"{RED}[license]{NC}    Renew at: {LICENSE_RENEW_URL}"
            )
            sys.exit(1)

    except urllib.error.HTTPError as exc:
        if exc.code == 403:
            print(f"{RED}✗ rejected{NC}")
            print(
                f"{RED}[license]{NC} ❌  License rejected by server (key={LICENSE_KEY[:8]}...).\n"
                f"{RED}[license]{NC}    Purchase or renew at: {LICENSE_BUY_URL}"
            )
            sys.exit(1)
        # Other HTTP error — fall through to grace-cache check
        exc_msg = str(exc)

    except Exception as exc:
        exc_msg = str(exc)

    # Network / server error — use grace cache
    if _cache_valid():
        print(f"{YELLOW}⚠ offline (using cached verification){NC}")
        print(
            f"{YELLOW}[license]{NC} Could not reach license server: {exc_msg}\n"
            f"{YELLOW}[license]{NC} Cached verification is still valid — continuing."
        )
        return

    # No cache and can't reach server
    print(f"{RED}✗ unreachable{NC}")
    print(
        f"{RED}[license]{NC} ❌  License server unreachable and no cached verification found.\n"
        f"{RED}[license]{NC}    Error: {exc_msg}\n"
        f"{RED}[license]{NC}    Check your internet connection, or contact support."
    )
    sys.exit(1)


if __name__ == "__main__":
    main()
