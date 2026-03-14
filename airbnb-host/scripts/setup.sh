#!/usr/bin/env bash
# ============================================================
# setup.sh — Airbnb Host Assistant — First-time Setup Wizard
#
# © 2024 Jestin Rajan. All rights reserved.
# Licensed under the Airbnb Host AI License Agreement.
# Unauthorized copying, distribution or use is prohibited.
#
# Usage:
#   cd airbnb-host/scripts
#   chmod +x setup.sh && ./setup.sh
# ============================================================

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'
info()  { echo -e "${GREEN}[setup]${NC} $*"; }
warn()  { echo -e "${YELLOW}[setup]${NC} $*"; }
error() { echo -e "${RED}[setup]${NC} $*"; exit 1; }
step()  { echo -e "\n${CYAN}${BOLD}━━━ $* ━━━${NC}"; }
ask()   { printf "${CYAN}▶${NC}  %s: " "$1"; }

echo ""
echo -e "${CYAN}${BOLD}╔═══════════════════════════════════════════════════════╗${NC}"
echo -e "${CYAN}${BOLD}║       Airbnb Host AI Assistant — Setup Wizard         ║${NC}"
echo -e "${CYAN}${BOLD}╚═══════════════════════════════════════════════════════╝${NC}"
echo ""
# Windows users must use WSL2
if [[ "$(uname -s)" == *"_NT"* ]] || [[ "$(uname -r)" == *"microsoft"* ]]; then
  warn "Windows detected via WSL2 — this is supported. Continue normally."
elif [[ "$(uname -s)" == "MINGW"* ]] || [[ "$(uname -s)" == "CYGWIN"* ]]; then
  error "Git Bash / Cygwin are not supported. Please use WSL2 on Windows: https://learn.microsoft.com/en-us/windows/wsl/install"
fi

# ── Step 1: Check dependencies ──────────────────────────────────────────────
step "Step 1 / 6 — Checking dependencies"

PY_CMD=""
for cmd in python3 python; do
  if command -v "$cmd" >/dev/null 2>&1; then
    if "$cmd" -c "import sys; sys.exit(0 if sys.version_info >= (3,9) else 1)" 2>/dev/null; then
      PY_CMD="$cmd"
      info "Python: $($cmd --version)"
      break
    fi
  fi
done
[[ -z "$PY_CMD" ]] && error "Python 3.9+ is required. Install from https://python.org"

command -v node >/dev/null 2>&1 || error "Node.js 22+ is required. Install from https://nodejs.org"
if ! node -e "process.exit(parseInt(process.version.slice(1))<22?1:0)" 2>/dev/null; then
  error "Node.js 22+ required (found $(node --version)). Upgrade at https://nodejs.org"
fi
info "Node.js: $(node --version)"

command -v npm >/dev/null 2>&1 || error "npm not found"
info "npm: $(npm --version)"

# ── Step 2: Install dependencies ────────────────────────────────────────────
step "Step 2 / 6 — Installing dependencies"

info "Installing Python packages (this may take a minute)..."
$PY_CMD -m pip install -r requirements.txt -q 2>&1 \
  | grep -E "^(Collecting|Successfully installed|ERROR)" || true
info "Python packages ready."

info "Installing Node.js packages for WhatsApp bot..."
(cd whatsapp && npm install --silent)
info "Node.js packages ready."

# ── Step 3: Configure .env ───────────────────────────────────────────────────
step "Step 3 / 6 — Configuration"

# Load existing values if .env exists
if [[ -f .env ]]; then
  set +u
  # shellcheck source=/dev/null
  source .env 2>/dev/null || true
  set -u
  warn "Existing .env found — press Enter to keep any value shown in [brackets]."
fi

echo ""
info "Answer each prompt. Press Enter to keep the existing value."
echo ""

# Helper — prompt with optional default + optional secret mode
_prompt() {
  local varname="$1" label="$2" default="${3:-}" secret="${4:-no}"
  local current="${!varname:-$default}"
  local val=""
  if [[ "$secret" == "yes" ]]; then
    [[ -n "$current" ]] && ask "$label [***hidden***]" || ask "$label"
    read -rs val; echo ""
  else
    [[ -n "$current" ]] && ask "$label [$current]" || ask "$label"
    read -r val
  fi
  [[ -z "$val" ]] && val="$current"
  printf -v "$varname" '%s' "$val"
}

# — Claude API —
echo ""
info "Claude AI (required for smart message drafts)"
info "  Get your key at: https://console.anthropic.com"
_prompt ANTHROPIC_API_KEY "Anthropic API key" "" yes

# — License —
echo ""
info "License"
info "  Leave blank to run in trial mode (full features, no time limit during beta)"
_prompt LICENSE_KEY "License key" ""

# — Email —
echo ""
info "Email — the inbox that receives Airbnb notification emails"
echo ""
echo -e "  ${YELLOW}IMPORTANT: Use an App Password, NOT your regular account password${NC}"
echo "  Gmail:   myaccount.google.com/apppasswords"
echo "  Outlook: account.live.com → Security → App passwords"
echo "  Yahoo:   login.yahoo.com/account/security → App passwords"
echo ""
_prompt EMAIL_ADDRESS "Your email address" ""

# Auto-detect IMAP/SMTP from email domain
_domain="${EMAIL_ADDRESS##*@}"
case "${_domain,,}" in
  gmail.com)
    EMAIL_IMAP_HOST="${EMAIL_IMAP_HOST:-imap.gmail.com}";   EMAIL_IMAP_PORT="${EMAIL_IMAP_PORT:-993}"
    EMAIL_SMTP_HOST="${EMAIL_SMTP_HOST:-smtp.gmail.com}";   EMAIL_SMTP_PORT="${EMAIL_SMTP_PORT:-587}"
    info "  Gmail detected — server settings pre-filled." ;;
  outlook.com|hotmail.com|live.com)
    EMAIL_IMAP_HOST="${EMAIL_IMAP_HOST:-outlook.office365.com}";  EMAIL_IMAP_PORT="${EMAIL_IMAP_PORT:-993}"
    EMAIL_SMTP_HOST="${EMAIL_SMTP_HOST:-smtp-mail.outlook.com}";  EMAIL_SMTP_PORT="${EMAIL_SMTP_PORT:-587}"
    info "  Outlook detected — server settings pre-filled." ;;
  yahoo.com|yahoo.co.*)
    EMAIL_IMAP_HOST="${EMAIL_IMAP_HOST:-imap.mail.yahoo.com}";  EMAIL_IMAP_PORT="${EMAIL_IMAP_PORT:-993}"
    EMAIL_SMTP_HOST="${EMAIL_SMTP_HOST:-smtp.mail.yahoo.com}";  EMAIL_SMTP_PORT="${EMAIL_SMTP_PORT:-587}"
    info "  Yahoo detected — server settings pre-filled." ;;
  *)
    warn "  Non-standard email provider — please fill in IMAP/SMTP manually."
    _prompt EMAIL_IMAP_HOST "IMAP server hostname" ""
    _prompt EMAIL_IMAP_PORT "IMAP port" "993"
    _prompt EMAIL_SMTP_HOST "SMTP server hostname" ""
    _prompt EMAIL_SMTP_PORT "SMTP port" "587"
    ;;
esac

_prompt EMAIL_PASSWORD "Email App Password" "" yes

# — WhatsApp mode —
echo ""
info "WhatsApp — choose how to connect"
echo ""
echo "  companion    — Personal/dedicated number, scan QR code, 1–20 units (recommended)"
echo "  business_api — WhatsApp Business Cloud API (Meta-official), 20+ units, no ban risk"
echo ""
_prompt WA_MODE "WhatsApp mode (companion or business_api)" "companion"

if [[ "${WA_MODE}" == "business_api" ]]; then
  echo ""
  info "WhatsApp Business Cloud API credentials"
  info "  Meta dashboard: business.facebook.com → WhatsApp → API Setup"
  _prompt HOST_WHATSAPP_NUMBER  "Your WhatsApp Business number (E.164, e.g. +14155550123)" ""
  _prompt WHATSAPP_TOKEN        "Permanent access token (from Meta dashboard)" "" yes
  _prompt WHATSAPP_PHONE_ID     "Phone number ID (numeric, from Meta dashboard)" ""
  _prompt WHATSAPP_VERIFY_TOKEN "Webhook verify token (choose any secret string)" ""
  echo ""
  warn "After setup, configure your webhook URL in the Meta dashboard:"
  warn "  URL: https://your-server.com:${WA_BOT_PORT:-7772}/webhook"
  warn "  Verify token: the WHATSAPP_VERIFY_TOKEN you just entered"
  warn "  Subscribe to: messages"
else
  echo ""
  info "WhatsApp — your personal number (you will scan a QR code with this phone)"
  _prompt HOST_WHATSAPP_NUMBER "Your WhatsApp number (E.164 format, e.g. +14155550123)" ""
  # Cloud API vars not needed in companion mode
  WHATSAPP_TOKEN="${WHATSAPP_TOKEN:-}"
  WHATSAPP_PHONE_ID="${WHATSAPP_PHONE_ID:-}"
  WHATSAPP_VERIFY_TOKEN="${WHATSAPP_VERIFY_TOKEN:-}"
fi

# — Property —
echo ""
info "Property details"
_prompt PROPERTY_NAME   "Property name (shown in all messages)" "My Airbnb"
_prompt AIRBNB_LISTING_URL "Airbnb listing URL (for review request links, optional)" ""

# — iCal —
echo ""
info "iCal Calendar (for automated check-in / checkout triggers)"
info "  Airbnb app → Calendar → ⚙️ Availability settings → Export Calendar → copy .ics link"
_prompt AIRBNB_ICAL_URL "Airbnb iCal URL (leave blank to configure later)" ""

# — Internal token (auto-generate if missing) —
if [[ -z "${INTERNAL_TOKEN:-}" ]]; then
  INTERNAL_TOKEN=$($PY_CMD -c "import secrets; print(secrets.token_hex(32))")
  info "Generated a secure INTERNAL_TOKEN automatically."
fi

# — Optional —
echo ""
info "Optional integrations"
_prompt SERPAPI_KEY  "SerpAPI key (for /price-tip with local event data, optional)" ""
_prompt BRAVE_API_KEY "Brave API key (alternative to SerpAPI, optional)" ""

# Write .env
cat > .env <<ENVEOF
# ============================================================
# Airbnb Host Assistant — Configuration
# Generated by setup.sh on $(date)
# ============================================================

# Claude AI
ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}

# License (leave blank for trial mode)
LICENSE_KEY=${LICENSE_KEY}

# Email
EMAIL_IMAP_HOST=${EMAIL_IMAP_HOST:-imap.gmail.com}
EMAIL_IMAP_PORT=${EMAIL_IMAP_PORT:-993}
EMAIL_SMTP_HOST=${EMAIL_SMTP_HOST:-smtp.gmail.com}
EMAIL_SMTP_PORT=${EMAIL_SMTP_PORT:-587}
EMAIL_ADDRESS=${EMAIL_ADDRESS}
EMAIL_PASSWORD=${EMAIL_PASSWORD}
EMAIL_POLL_SECONDS=30
EMAIL_IMAP_TIMEOUT=20

# WhatsApp
# companion  = Baileys (personal/dedicated number, QR scan) — default, 1–20 units
# business_api = Meta WhatsApp Business Cloud API — no ban risk, 20+ units
WA_MODE=${WA_MODE}
HOST_WHATSAPP_NUMBER=${HOST_WHATSAPP_NUMBER}
# Cloud API credentials (only used when WA_MODE=business_api)
WHATSAPP_TOKEN=${WHATSAPP_TOKEN}
WHATSAPP_PHONE_ID=${WHATSAPP_PHONE_ID}
WHATSAPP_VERIFY_TOKEN=${WHATSAPP_VERIFY_TOKEN}

# Security (shared secret between internal services — do not change after setup)
INTERNAL_TOKEN=${INTERNAL_TOKEN}

# Internal service ports (change only if conflicting with something else)
ROUTER_PORT=7771
WA_BOT_PORT=7772
DRAFT_TTL_DAYS=7

# iCal / Calendar
AIRBNB_ICAL_URL=${AIRBNB_ICAL_URL}
PROPERTY_NAME=${PROPERTY_NAME}
CHECKIN_NOTICE_HOURS=24
CHECKOUT_BRIEF_HOUR=11
DEFAULT_CHECKIN_HOUR=15
EXTENSION_OFFER_HOUR=9
CALENDAR_POLL_MINUTES=30
PRE_ARRIVAL_DAYS=7

# Airbnb listing URL (included in post-checkout review requests)
AIRBNB_LISTING_URL=${AIRBNB_LISTING_URL}

# Optional — event search for /price-tip
SERPAPI_KEY=${SERPAPI_KEY}
BRAVE_API_KEY=${BRAVE_API_KEY}
ENVEOF

info ".env written successfully."

# ── Step 4: Configure vendors.json ──────────────────────────────────────────
step "Step 4 / 6 — Service providers (vendors.json)"
echo ""
info "vendors.json holds contacts for cleaners, AC techs, plumbers, etc."
info "WhatsApp numbers must be in E.164 format (e.g. +14155550123)."
echo ""

if [[ ! -f vendors.json ]]; then
  cat > vendors.json <<'VENDOREOF'
{
  "_comment": "Primary is contacted first. Backups used if unavailable. Numbers in E.164 format.",
  "cleaners": [
    { "name": "Primary Cleaner",      "whatsapp": "+1234567890" },
    { "name": "Backup Cleaner",       "whatsapp": "+0987654321" }
  ],
  "ac_technicians": [
    { "name": "Primary AC Tech",      "whatsapp": "+1112223333" },
    { "name": "Backup AC Tech",       "whatsapp": "+4445556666" }
  ],
  "plumbers": [
    { "name": "Primary Plumber",      "whatsapp": "+2223334444" }
  ],
  "electricians": [
    { "name": "Primary Electrician",  "whatsapp": "+5556667777" }
  ],
  "locksmiths": [
    { "name": "Primary Locksmith",    "whatsapp": "+8889990000" }
  ]
}
VENDOREOF
  info "vendors.json created with example entries."
else
  info "vendors.json already exists — skipping."
fi

warn "Edit vendors.json with your real contacts before starting:"
warn "  ${SCRIPT_DIR}/vendors.json"
echo ""

# Offer to open in editor
if [[ -n "${EDITOR:-}" ]] && command -v "${EDITOR}" >/dev/null 2>&1; then
  read -rp "  Press Enter to open vendors.json in ${EDITOR}, or Ctrl+C to skip: "
  "${EDITOR}" vendors.json
elif command -v nano >/dev/null 2>&1; then
  read -rp "  Press Enter to open vendors.json in nano, or Ctrl+C to skip: "
  nano vendors.json
else
  info "  Open vendors.json in any text editor to fill in your real contacts."
fi

# ── Step 5: License check ────────────────────────────────────────────────────
step "Step 5 / 6 — License validation"
$PY_CMD license.py || true   # don't block setup even if license check fails

# ── Step 6: Instructions ─────────────────────────────────────────────────────
step "Step 6 / 6 — All done!"
echo ""
echo -e "  ${GREEN}${BOLD}Your Airbnb Host Assistant is configured.${NC}"
echo ""
echo "  ── To start all services ──────────────────────────────────────"
echo -e "  ${CYAN}cd ${SCRIPT_DIR} && ./start.sh${NC}"
echo ""
echo "  ── First run: WhatsApp pairing ─────────────────────────────────"
echo "  When you run start.sh for the first time, a QR code will appear."
echo "  Open WhatsApp on your phone:"
echo "    → ⋮ Menu (Android) or Settings (iPhone)"
echo "    → Linked Devices → Link a Device"
echo "    → Scan the QR code"
echo ""
echo "  ── To install as a system service (auto-start on boot) ─────────"
echo -e "  ${CYAN}./install_service.sh${NC}"
echo ""
echo "  ── Key files ───────────────────────────────────────────────────"
echo "    .env          — configuration (keep private)"
echo "    vendors.json  — service provider contacts"
echo "    SKILL.md      — AI behaviour (advanced customisation)"
echo ""
info "Setup complete. Run ./start.sh to launch."
echo ""
