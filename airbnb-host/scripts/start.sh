#!/usr/bin/env bash
# ============================================================
# start.sh — Launch the Airbnb Host automated pipeline
#
# © 2024 Jestin Rajan. All rights reserved.
# Licensed under the Airbnb Host AI License Agreement.
# Unauthorized copying, distribution or use is prohibited.
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
command -v node    >/dev/null 2>&1 || error "node not found — install Node.js ≥ 22 from https://nodejs.org"
command -v npm     >/dev/null 2>&1 || error "npm not found"
command -v curl    >/dev/null 2>&1 || error "curl not found — needed for health checks"

# Node.js version check (OpenClaw requires ≥22)
if ! node -e "process.exit(parseInt(process.version.slice(1))<22?1:0)" 2>/dev/null; then
  error "Node.js 22+ required (found $(node --version)). Upgrade at https://nodejs.org"
fi

[[ -n "${ANTHROPIC_API_KEY:-}" ]]    || error "ANTHROPIC_API_KEY is not set in .env"
[[ -n "${EMAIL_IMAP_HOST:-}" ]]      || error "EMAIL_IMAP_HOST is not set in .env"
[[ -n "${EMAIL_ADDRESS:-}" ]]        || error "EMAIL_ADDRESS is not set in .env"
[[ -n "${HOST_WHATSAPP_NUMBER:-}" ]] || error "HOST_WHATSAPP_NUMBER is not set in .env"

# ── Check for port conflicts ────────────────────────────────
_port_in_use() {
  # Works on Linux, macOS, and WSL2 (falls back gracefully if ss/lsof missing)
  if command -v ss >/dev/null 2>&1; then
    ss -tlnp 2>/dev/null | grep -q ":$1 "
  elif command -v lsof >/dev/null 2>&1; then
    lsof -iTCP:"$1" -sTCP:LISTEN -t >/dev/null 2>&1
  else
    # Fallback: try to bind the port via node
    ! node -e "const n=require('net');const s=n.createServer();s.once('error',()=>{process.exit(1)});s.listen($1,'127.0.0.1',()=>{s.close();process.exit(0)})" 2>/dev/null
  fi
}
for port in "$ROUTER_PORT" "$WA_BOT_PORT"; do
  if _port_in_use "$port"; then
    warn "Port $port is already in use. Stop the existing process first."
    error "Port conflict on $port"
  fi
done

# ── License check ──────────────────────────────────────────
info "Checking license..."
python3 license.py || exit 1

# ── Install Python dependencies ────────────────────────────
info "Installing Python dependencies..."
pip install -r requirements.txt 2>&1 | grep -E "^(Collecting|Successfully|ERROR)" || true

# ── Install Node dependencies ──────────────────────────────
info "Installing Node.js dependencies for WhatsApp bot..."
(cd whatsapp && npm install --silent)

# ── PID tracking ──────────────────────────────────────────
PIDS=()
cleanup() {
  echo ""
  info "Shutting down all services..."
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

# Health-check: poll /health until router responds (up to 15s)
info "Waiting for router to be ready..."
READY=0
for i in $(seq 1 15); do
  if curl -sf "http://127.0.0.1:${ROUTER_PORT}/health" >/dev/null 2>&1; then
    READY=1
    break
  fi
  sleep 1
done
if [[ $READY -eq 0 ]]; then
  error "Router did not start within 15 seconds. Check logs above."
fi
info "Router is up."

# ── 2. Start WhatsApp bot ─────────────────────────────────
info "Starting WhatsApp companion bot on port ${WA_BOT_PORT}..."
info "(First run: scan the QR code printed below to link your phone)"
(cd whatsapp && node bot.js) &
PIDS+=($!)

# Give the HTTP server a moment to bind
sleep 2

# ── 3. Start calendar_watcher.py (if iCal URL is configured) ─────────────
if [[ -n "${AIRBNB_ICAL_URL:-}${AIRBNB_ICAL_URLS:-}" ]]; then
  info "Starting calendar watcher (iCal → check-in + cleaner brief)..."
  python3 calendar_watcher.py &
  PIDS+=($!)
  sleep 1
else
  warn "AIRBNB_ICAL_URL not set — calendar watcher skipped."
  warn "Set it in .env to enable auto check-in + cleaner brief drafts."
fi

# ── 4. Start email_watcher.py (foreground — shows live log) ─
info "Starting email watcher (${EMAIL_ADDRESS})..."
info "Press Ctrl+C to stop all services."
echo ""
python3 email_watcher.py
