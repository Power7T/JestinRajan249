#!/usr/bin/env bash
# ============================================================
# start.sh — Launch the Airbnb Host automated pipeline
#
# Starts three processes:
#   1. response_router.py  (FastAPI, port ROUTER_PORT)
#   2. whatsapp/bot.js     (WhatsApp companion + HTTP server)
#   3. email_watcher.py    (IMAP poll daemon)  ← foreground
#
# Usage:
#   cd airbnb-host/scripts
#   ./start.sh
# ============================================================

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ── Colour helpers ──────────────────────────────────────────
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
info()  { echo -e "${GREEN}[start.sh]${NC} $*"; }
warn()  { echo -e "${YELLOW}[start.sh]${NC} $*"; }
error() { echo -e "${RED}[start.sh]${NC} $*"; exit 1; }

# ── Load .env ──────────────────────────────────────────────
if [[ ! -f .env ]]; then
  error ".env not found. Copy .env.example to .env and fill in your values."
fi
set -o allexport
source .env
set +o allexport

ROUTER_PORT="${ROUTER_PORT:-7771}"
WA_BOT_PORT="${WA_BOT_PORT:-7772}"

# ── Dependency checks ──────────────────────────────────────
command -v python3 >/dev/null 2>&1 || error "python3 not found"
command -v node    >/dev/null 2>&1 || error "node not found — install Node.js ≥ 18"
command -v npm     >/dev/null 2>&1 || error "npm not found"

[[ -n "${ANTHROPIC_API_KEY:-}" ]]   || error "ANTHROPIC_API_KEY is not set in .env"
[[ -n "${EMAIL_IMAP_HOST:-}" ]]     || error "EMAIL_IMAP_HOST is not set in .env"
[[ -n "${EMAIL_ADDRESS:-}" ]]       || error "EMAIL_ADDRESS is not set in .env"
[[ -n "${HOST_WHATSAPP_NUMBER:-}" ]] || error "HOST_WHATSAPP_NUMBER is not set in .env"

# ── Install Python dependencies ────────────────────────────
info "Installing Python dependencies..."
pip install -q -r requirements.txt

# ── Install Node dependencies ──────────────────────────────
info "Installing Node.js dependencies for WhatsApp bot..."
(cd whatsapp && npm install --silent)

# ── PID tracking ──────────────────────────────────────────
PIDS=()
cleanup() {
  info "Shutting down..."
  for pid in "${PIDS[@]}"; do
    kill "$pid" 2>/dev/null || true
  done
  exit 0
}
trap cleanup SIGINT SIGTERM

# ── 1. Start response_router.py ───────────────────────────
info "Starting response_router.py on port ${ROUTER_PORT}..."
python3 response_router.py &
PIDS+=($!)
sleep 2   # give FastAPI a moment to bind

# ── 2. Start WhatsApp bot ─────────────────────────────────
info "Starting WhatsApp companion bot on port ${WA_BOT_PORT}..."
info "(First run: scan the QR code printed below to link your phone)"
(cd whatsapp && node bot.js) &
PIDS+=($!)
sleep 3

# ── 3. Start email_watcher.py (foreground — shows live log) ─
info "Starting email watcher (${EMAIL_ADDRESS})..."
info "Press Ctrl+C to stop all services."
echo ""
python3 email_watcher.py
