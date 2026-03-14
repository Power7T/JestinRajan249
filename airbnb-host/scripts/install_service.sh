#!/usr/bin/env bash
# ============================================================
# install_service.sh — Install Airbnb Host Assistant as a
# system service that auto-starts on boot and restarts on crash.
#
# Supports:
#   systemd  — Linux (Ubuntu, Debian, Raspberry Pi, etc.)
#   launchd  — macOS
#
# © 2024 Jestin Rajan. All rights reserved.
# Licensed under the Airbnb Host AI License Agreement.
# Unauthorized copying, distribution or use is prohibited.
#
# Usage:
#   cd airbnb-host/scripts
#   chmod +x install_service.sh && ./install_service.sh
# ============================================================

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; CYAN='\033[0;36m'; NC='\033[0m'
info()  { echo -e "${GREEN}[service]${NC} $*"; }
warn()  { echo -e "${YELLOW}[service]${NC} $*"; }
error() { echo -e "${RED}[service]${NC} $*"; exit 1; }

[[ -f "${SCRIPT_DIR}/.env" ]] || error ".env not found. Run setup.sh first."

CURRENT_USER="${USER:-$(whoami)}"
OS="$(uname -s)"

# ── Detect platform ──────────────────────────────────────────────────────────
if [[ "$OS" == "Linux" ]]; then
  command -v systemctl >/dev/null 2>&1 || error "systemctl not found — systemd required on Linux."
  _install_systemd
elif [[ "$OS" == "Darwin" ]]; then
  _install_launchd
else
  error "Unsupported platform: $OS. Manually set up a process manager (PM2, supervisor, etc.)"
fi

# ── systemd installer ────────────────────────────────────────────────────────
_install_systemd() {
  local SERVICE_NAME="airbnb-host-assistant"
  local SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"

  info "Installing systemd service: ${SERVICE_NAME}"

  # Require sudo
  if [[ $EUID -ne 0 ]]; then
    warn "systemd installation requires sudo. Re-running with sudo..."
    exec sudo "$0" "$@"
  fi

  # Find python3
  PY_CMD="$(command -v python3 || command -v python || true)"
  [[ -z "$PY_CMD" ]] && error "python3 not found"

  cat > "$SERVICE_FILE" <<UNIT
[Unit]
Description=Airbnb Host AI Assistant
Documentation=https://yourdomain.com/docs
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${CURRENT_USER}
WorkingDirectory=${SCRIPT_DIR}
ExecStart=${SCRIPT_DIR}/start.sh
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal
SyslogIdentifier=airbnb-host

# Environment — inherit from .env via start.sh
EnvironmentFile=${SCRIPT_DIR}/.env

# Increase open file limit for WhatsApp (Puppeteer) + IMAP
LimitNOFILE=65536

[Install]
WantedBy=multi-user.target
UNIT

  systemctl daemon-reload
  systemctl enable "${SERVICE_NAME}"
  systemctl start  "${SERVICE_NAME}"

  info "Service installed and started."
  info ""
  info "Useful commands:"
  echo "  systemctl status  ${SERVICE_NAME}    # check status"
  echo "  systemctl stop    ${SERVICE_NAME}    # stop"
  echo "  systemctl restart ${SERVICE_NAME}    # restart"
  echo "  journalctl -u ${SERVICE_NAME} -f     # live logs"
  echo ""
  warn "First run: the WhatsApp QR code appears in the logs."
  warn "View it with:  journalctl -u ${SERVICE_NAME} -f"
}

# ── launchd installer (macOS) ────────────────────────────────────────────────
_install_launchd() {
  local LABEL="com.airbnb-host-assistant"
  local PLIST_DIR="${HOME}/Library/LaunchAgents"
  local PLIST="${PLIST_DIR}/${LABEL}.plist"
  local LOG_DIR="${HOME}/Library/Logs/airbnb-host-assistant"

  info "Installing launchd agent: ${LABEL}"

  mkdir -p "$PLIST_DIR" "$LOG_DIR"

  # Find python3
  PY_CMD="$(command -v python3 || command -v python || true)"
  [[ -z "$PY_CMD" ]] && error "python3 not found"

  cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>${LABEL}</string>

    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>${SCRIPT_DIR}/start.sh</string>
    </array>

    <key>WorkingDirectory</key>
    <string>${SCRIPT_DIR}</string>

    <key>RunAtLoad</key>
    <true/>

    <key>KeepAlive</key>
    <dict>
        <key>SuccessfulExit</key>
        <false/>
    </dict>

    <key>StandardOutPath</key>
    <string>${LOG_DIR}/stdout.log</string>

    <key>StandardErrorPath</key>
    <string>${LOG_DIR}/stderr.log</string>

    <key>ThrottleInterval</key>
    <integer>10</integer>
</dict>
</plist>
PLIST

  # Unload if already loaded, then load
  launchctl unload "$PLIST" 2>/dev/null || true
  launchctl load   "$PLIST"

  info "LaunchAgent installed and started."
  info ""
  info "Useful commands:"
  echo "  launchctl list | grep airbnb      # check status"
  echo "  launchctl stop  ${LABEL}           # stop"
  echo "  launchctl start ${LABEL}           # start"
  echo "  tail -f ${LOG_DIR}/stdout.log      # live logs"
  echo ""
  warn "First run: the WhatsApp QR code appears in the log file."
  warn "View it with:  tail -f ${LOG_DIR}/stdout.log"
}

# ── Uninstall helper ─────────────────────────────────────────────────────────
if [[ "${1:-}" == "--uninstall" ]]; then
  if [[ "$OS" == "Linux" ]]; then
    systemctl stop    airbnb-host-assistant 2>/dev/null || true
    systemctl disable airbnb-host-assistant 2>/dev/null || true
    rm -f /etc/systemd/system/airbnb-host-assistant.service
    systemctl daemon-reload
    info "systemd service removed."
  elif [[ "$OS" == "Darwin" ]]; then
    launchctl unload "${HOME}/Library/LaunchAgents/com.airbnb-host-assistant.plist" 2>/dev/null || true
    rm -f "${HOME}/Library/LaunchAgents/com.airbnb-host-assistant.plist"
    info "LaunchAgent removed."
  fi
  exit 0
fi
