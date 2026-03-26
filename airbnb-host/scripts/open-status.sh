#!/usr/bin/env bash
# =============================================================================
# open-status.sh — Open the service status page in your default browser
#
# Usage: ./open-status.sh
# =============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Load .env for router port
if [[ -f "$SCRIPT_DIR/.env" ]]; then
  ROUTER_PORT=$(grep '^ROUTER_PORT=' "$SCRIPT_DIR/.env" | cut -d'=' -f2 || echo "7771")
else
  ROUTER_PORT="7771"
fi

URL="http://localhost:${ROUTER_PORT}/status?fmt=html"

echo "Opening service status page: $URL"
echo ""

# Try to open in browser (cross-platform)
if command -v xdg-open >/dev/null 2>&1; then
  # Linux
  xdg-open "$URL"
elif command -v open >/dev/null 2>&1; then
  # macOS
  open "$URL"
elif command -v start >/dev/null 2>&1; then
  # Windows (Git Bash, WSL2)
  start "$URL"
else
  echo "Could not open browser automatically. Please manually visit:"
  echo "  $URL"
fi
