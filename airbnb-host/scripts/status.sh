#!/usr/bin/env bash
# =============================================================================
# status.sh — One-shot health check for the Airbnb Host pipeline
#
# Usage:  cd airbnb-host/scripts && ./status.sh
# =============================================================================
set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'; BOLD='\033[1m'
ok()    { echo -e "  ${GREEN}[OK]${NC}    $*"; }
warn()  { echo -e "  ${YELLOW}[WARN]${NC}  $*"; }
bad()   { echo -e "  ${RED}[DOWN]${NC}  $*"; }
header(){ echo -e "\n${BOLD}$*${NC}"; }

# Load .env for port numbers
if [[ -f "$SCRIPT_DIR/.env" ]]; then
  # shellcheck disable=SC2046
  export $(grep -E '^(ROUTER_PORT|WA_BOT_PORT)=' "$SCRIPT_DIR/.env" | xargs) 2>/dev/null || true
fi
ROUTER_PORT="${ROUTER_PORT:-7771}"
WA_BOT_PORT="${WA_BOT_PORT:-7772}"

# ── Helpers ──────────────────────────────────────────────────────────────────
_ago() {
  # $1 = unix timestamp (float ok), prints "Xs ago" or "Xm ago" or "Xh ago"
  local now ts age
  now=$(date +%s)
  ts=$(printf "%.0f" "${1:-0}")
  age=$(( now - ts ))
  if   (( age < 60  )); then echo "${age}s ago"
  elif (( age < 3600)); then echo "$(( age/60 ))m ago"
  else                        echo "$(( age/3600 ))h ago"
  fi
}

_read_hb() {
  # $1=path $2=stale_seconds
  local path="$1" stale="$2"
  if [[ ! -f "$path" ]]; then echo "UNKNOWN (file missing)"; return 1; fi
  local ts polls
  # Use python3 for JSON parsing (already a dependency)
  ts=$(python3 -c "import json,sys; d=json.load(open('$path')); print(d.get('ts',0))" 2>/dev/null)
  polls=$(python3 -c "import json,sys; d=json.load(open('$path')); print(d.get('polls',0))" 2>/dev/null)
  local now age
  now=$(date +%s)
  age=$(( now - ${ts%.*} ))
  if (( age < stale )); then
    echo "OK  (last poll $(_ago "$ts"), total polls=$polls)"
    return 0
  else
    echo "STALE  (last poll $(_ago "$ts") — older than ${stale}s threshold)"
    return 2
  fi
}

# ── 1. HTTP service checks ────────────────────────────────────────────────────
header "1. HTTP Services"

if curl -sf --max-time 2 "http://127.0.0.1:${ROUTER_PORT}/health" >/dev/null 2>&1; then
  ok "Response Router   http://127.0.0.1:${ROUTER_PORT}/health"
  # Also print full status
  STATUS_JSON=$(curl -sf --max-time 3 "http://127.0.0.1:${ROUTER_PORT}/status" 2>/dev/null || echo "")
  [[ -n "$STATUS_JSON" ]] && echo "       status → $(echo "$STATUS_JSON" | python3 -c \
    "import json,sys; d=json.load(sys.stdin); \
     print('uptime=' + str(d.get('uptime_s','?')) + 's')" 2>/dev/null)"
else
  bad "Response Router   NOT RESPONDING on port ${ROUTER_PORT}"
fi

if curl -sf --max-time 2 "http://127.0.0.1:${WA_BOT_PORT}/health" >/dev/null 2>&1; then
  WA_JSON=$(curl -sf --max-time 2 "http://127.0.0.1:${WA_BOT_PORT}/health" 2>/dev/null || echo "{}")
  WA_CONN=$(echo "$WA_JSON" | python3 -c \
    "import json,sys; d=json.load(sys.stdin); print(d.get('connected','?'))" 2>/dev/null)
  WA_UP=$(echo "$WA_JSON" | python3 -c \
    "import json,sys; d=json.load(sys.stdin); print(d.get('uptime_s','?'))" 2>/dev/null)
  ok "WhatsApp Bot      http://127.0.0.1:${WA_BOT_PORT}/health  (connected=${WA_CONN} uptime=${WA_UP}s)"
else
  bad "WhatsApp Bot      NOT RESPONDING on port ${WA_BOT_PORT}"
fi

# ── 2. Daemon heartbeat files ────────────────────────────────────────────────
header "2. Daemon Heartbeats"

HB_EMAIL="$SCRIPT_DIR/heartbeat_email.json"
HB_CAL="$SCRIPT_DIR/heartbeat_calendar.json"

msg=$(_read_hb "$HB_EMAIL" 90); rc=$?
if   (( rc == 0 )); then ok "Email Watcher     $msg"
elif (( rc == 2 )); then warn "Email Watcher     $msg"
else                     bad "Email Watcher     $msg"
fi

msg=$(_read_hb "$HB_CAL" 300); rc=$?
if   (( rc == 0 )); then ok "Calendar Watcher  $msg"
elif (( rc == 2 )); then warn "Calendar Watcher  $msg"
else
  # Missing heartbeat for calendar is ok if not configured
  if grep -q 'AIRBNB_ICAL_URL' "$SCRIPT_DIR/.env" 2>/dev/null; then
    bad "Calendar Watcher  $msg"
  else
    warn "Calendar Watcher  not configured (AIRBNB_ICAL_URL not set)"
  fi
fi

# ── 3. System service status ─────────────────────────────────────────────────
header "3. System Service Status"
SERVICE_NAME="${SERVICE_NAME:-airbnb-host-assistant}"

if command -v systemctl >/dev/null 2>&1 && systemctl is-active "$SERVICE_NAME" >/dev/null 2>&1; then
  ok "systemd: $SERVICE_NAME is active"
  systemctl status "$SERVICE_NAME" --no-pager -l 2>/dev/null | tail -5 | sed 's/^/       /'
elif [[ "$(uname)" == "Darwin" ]]; then
  PLIST_LABEL="${PLIST_LABEL:-com.airbnb-host-assistant}"
  if launchctl list 2>/dev/null | grep -q "$PLIST_LABEL"; then
    ok "launchd: $PLIST_LABEL loaded"
  else
    warn "launchd: $PLIST_LABEL not found — running manually or not installed"
  fi
else
  warn "System service check skipped (systemctl/launchctl not found)"
fi

# ── 4. Recent log lines ──────────────────────────────────────────────────────
header "4. Recent Logs (last 20 lines)"
LOG_FILE="${LOG_FILE:-}"
if [[ -n "$LOG_FILE" && -f "$LOG_FILE" ]]; then
  tail -20 "$LOG_FILE" | sed 's/^/  /'
elif command -v journalctl >/dev/null 2>&1 && \
     systemctl is-active "$SERVICE_NAME" >/dev/null 2>&1; then
  journalctl -u "$SERVICE_NAME" -n 20 --no-pager 2>/dev/null | sed 's/^/  /'
else
  warn "No log source found. Set LOG_FILE=/path/to/logfile or run under systemd."
fi

echo ""
