#!/usr/bin/env bash
# ============================================================
# HostAI — One-Command Production Deploy
#
# Usage:
#   First time:  chmod +x deploy.sh && ./deploy.sh
#   Update:      git pull && ./deploy.sh
#
# What it does:
#   1. Checks dependencies (Docker, Docker Compose)
#   2. Creates .env from .env.example if missing
#   3. Auto-generates SECRET_KEY and FIELD_ENCRYPTION_KEY if empty
#   4. Asks for domain name and sets it in nginx.conf
#   5. Optionally obtains a Let's Encrypt SSL certificate
#   6. Builds and starts all containers
#   7. Runs DB migrations (automatic at startup)
# ============================================================

set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
info()  { echo -e "${BLUE}[•]${NC} $*"; }
ok()    { echo -e "${GREEN}[✓]${NC} $*"; }
warn()  { echo -e "${YELLOW}[!]${NC} $*"; }
die()   { echo -e "${RED}[✗]${NC} $*" >&2; exit 1; }

echo ""
echo "  🏠  HostAI — Production Deploy"
echo "  ================================"
echo ""

# ── 1. Check dependencies ────────────────────────────────────
info "Checking dependencies..."
command -v docker        >/dev/null || die "Docker not found. Install from https://docs.docker.com/get-docker/"
command -v docker-compose >/dev/null 2>&1 || \
  docker compose version >/dev/null 2>&1   || die "Docker Compose not found."

# Prefer 'docker compose' (V2) over 'docker-compose' (V1)
if docker compose version >/dev/null 2>&1; then
  DC="docker compose"
else
  DC="docker-compose"
fi
ok "Docker $(docker --version | awk '{print $3}' | tr -d ',')"

# ── 2. Set up .env ───────────────────────────────────────────
if [ ! -f .env ]; then
  cp .env.example .env
  warn ".env created from .env.example — filling in auto-generated secrets..."
fi

# Auto-generate SECRET_KEY if placeholder
if grep -qE '^SECRET_KEY=change-me|^SECRET_KEY=$' .env 2>/dev/null; then
  NEW_SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))" 2>/dev/null || \
               openssl rand -hex 32)
  sed -i "s|^SECRET_KEY=.*|SECRET_KEY=${NEW_SECRET}|" .env
  ok "Generated SECRET_KEY"
fi

# Auto-generate FIELD_ENCRYPTION_KEY if empty
if grep -qE '^FIELD_ENCRYPTION_KEY=$' .env 2>/dev/null; then
  NEW_ENC=$(python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())" 2>/dev/null || true)
  if [ -n "$NEW_ENC" ]; then
    sed -i "s|^FIELD_ENCRYPTION_KEY=.*|FIELD_ENCRYPTION_KEY=${NEW_ENC}|" .env
    ok "Generated FIELD_ENCRYPTION_KEY"
  else
    warn "cryptography not installed locally — set FIELD_ENCRYPTION_KEY manually in .env"
  fi
fi

# Auto-generate POSTGRES_PASSWORD if placeholder
if grep -qE '^POSTGRES_PASSWORD=changeme|^POSTGRES_PASSWORD=$' .env 2>/dev/null; then
  NEW_PG=$(python3 -c "import secrets; print(secrets.token_urlsafe(20))" 2>/dev/null || \
           openssl rand -base64 15)
  sed -i "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=${NEW_PG}|" .env
  ok "Generated POSTGRES_PASSWORD"
fi

# ── 3. Domain & Nginx config ─────────────────────────────────
CURRENT_DOMAIN=$(grep -oP '(?<=APP_BASE_URL=https://)[\w.\-]+' .env 2>/dev/null || echo "")
if [ -z "$CURRENT_DOMAIN" ] || [ "$CURRENT_DOMAIN" = "your-domain.com" ]; then
  echo ""
  read -rp "  Enter your domain name (e.g. hostai.example.com): " DOMAIN
  DOMAIN="${DOMAIN:-localhost}"
  sed -i "s|APP_BASE_URL=https://your-domain.com|APP_BASE_URL=https://${DOMAIN}|" .env
  sed -i "s|APP_BASE_URL=.*|APP_BASE_URL=https://${DOMAIN}|" .env
else
  DOMAIN="$CURRENT_DOMAIN"
fi

# Patch nginx.conf with domain
if grep -q "YOUR_DOMAIN" nginx.conf; then
  sed -i "s/YOUR_DOMAIN/${DOMAIN}/g" nginx.conf
  ok "Nginx configured for: ${DOMAIN}"
fi

# ── 4. Optional SSL via Let's Encrypt ────────────────────────
if [ "$DOMAIN" != "localhost" ] && grep -q "letsencrypt/live/YOUR_DOMAIN" nginx.conf 2>/dev/null || \
   grep -q "letsencrypt/live/${DOMAIN}" nginx.conf 2>/dev/null; then
  echo ""
  read -rp "  Set up free SSL certificate for ${DOMAIN}? [Y/n]: " GET_SSL
  GET_SSL="${GET_SSL:-Y}"
  if [[ "$GET_SSL" =~ ^[Yy]$ ]]; then
    read -rp "  Email for Let's Encrypt notifications: " LE_EMAIL
    sed -i "s|CERTBOT_EMAIL=.*|CERTBOT_EMAIL=${LE_EMAIL}|" .env
    echo "DOMAIN=${DOMAIN}" >> .env

    info "Obtaining SSL certificate (port 80 must be reachable)..."
    # Start nginx in HTTP-only mode temporarily
    $DC up -d nginx --no-deps 2>/dev/null || true
    sleep 3
    $DC --profile ssl run --rm certbot-init && ok "SSL certificate obtained" || \
      warn "Certbot failed — check that ${DOMAIN} points to this server's IP and port 80 is open"
    $DC restart nginx 2>/dev/null || true
  else
    # Use self-signed cert for local/dev
    warn "Skipping SSL. For local testing, the HTTPS block in nginx.conf may be disabled."
  fi
fi

# ── 5. Build & start ─────────────────────────────────────────
echo ""
info "Building containers (this may take a minute on first run)..."
$DC build --pull

info "Starting services..."
$DC up -d

# ── 6. Wait for healthy ──────────────────────────────────────
info "Waiting for app to be healthy..."
MAX_WAIT=60; WAITED=0
until $DC exec -T web curl -sf http://localhost:8000/health >/dev/null 2>&1; do
  sleep 2; WAITED=$((WAITED + 2))
  [ "$WAITED" -ge "$MAX_WAIT" ] && die "App did not become healthy after ${MAX_WAIT}s. Run: $DC logs web"
done
ok "App is healthy"

# ── 7. Done ──────────────────────────────────────────────────
echo ""
echo "  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
ok " HostAI is running!"
echo ""
echo "  Dashboard:  https://${DOMAIN}/dashboard"
echo "  Logs:       ${DC} logs -f web"
echo "  Stop:       ${DC} down"
echo "  Update:     git pull && ./deploy.sh"
echo ""
warn "Remember to:"
echo "  • Set STRIPE_SECRET_KEY + STRIPE_WEBHOOK_SECRET in .env"
echo "  • Set SMTP_HOST + SMTP_USER + SMTP_PASS in .env (for verification emails)"
echo "  • Point your Stripe webhook to: https://${DOMAIN}/billing/stripe-webhook"
echo "  • (Optional) Set SENTRY_DSN in .env for error tracking"
echo "  • (Optional) Proxy via Cloudflare for CDN + DDoS protection"
echo "  • Restart after .env changes: ${DC} restart web"
echo ""
echo "  Services running:"
echo "  • web + nginx: app + reverse proxy"
echo "  • db:          PostgreSQL"
echo "  • redis:       Rate limiting + message queue"
echo "  • certbot-renew: Auto SSL renewal (every 12h)"
echo "  • db-backup:   Daily PostgreSQL backups → pgbackups volume"
echo "  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
