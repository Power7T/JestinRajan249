#!/usr/bin/env bash
# ============================================================
# HostAI — Zero-Downtime Production Update
#
# Usage:
#   ./update.sh                  # Pull latest code + redeploy
#   ./update.sh --skip-pull      # Redeploy current code without git pull
#   ./update.sh --service web    # Redeploy only one service (web / nginx / etc.)
#
# How it achieves zero downtime:
#   1. Pulls latest code
#   2. Builds the new Docker image while the old container is still running
#   3. Scales web to 2 — nginx starts round-robining to both old & new
#   4. Waits for the new container to pass its health check
#   5. Scales back to 1 — Docker stops the old container
#   Nginx serves all in-flight requests from the old container to completion.
#   Typical user-visible gap: 0 seconds.
#
# Requirements:
#   - Docker Compose V2  (docker compose version)
#   - nginx.conf uses upstream block "web" (already configured)
#   - web service has a healthcheck configured (already configured)
# ============================================================

set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
info()  { echo -e "${BLUE}[•]${NC} $*"; }
ok()    { echo -e "${GREEN}[✓]${NC} $*"; }
warn()  { echo -e "${YELLOW}[!]${NC} $*"; }
die()   { echo -e "${RED}[✗]${NC} $*" >&2; exit 1; }

echo ""
echo "  🏠  HostAI — Zero-Downtime Update"
echo "  ==================================="
echo ""

# ── Parse args ───────────────────────────────────────────────
SKIP_PULL=0
TARGET_SERVICE=""
while [[ $# -gt 0 ]]; do
  case $1 in
    --skip-pull) SKIP_PULL=1; shift ;;
    --service)   TARGET_SERVICE="$2"; shift 2 ;;
    *) die "Unknown option: $1" ;;
  esac
done

# ── Compose command ──────────────────────────────────────────
if docker compose version >/dev/null 2>&1; then
  DC="docker compose"
else
  die "Docker Compose V2 required (docker compose version). Install: https://docs.docker.com/compose/install/"
fi

# ── 1. Git pull ───────────────────────────────────────────────
if [ "$SKIP_PULL" -eq 0 ]; then
  info "Pulling latest code..."
  git fetch origin
  LOCAL=$(git rev-parse HEAD)
  REMOTE=$(git rev-parse @{u} 2>/dev/null || echo "")
  if [ -n "$REMOTE" ] && [ "$LOCAL" = "$REMOTE" ]; then
    ok "Already up to date ($(git rev-parse --short HEAD))"
    read -rp "  Deploy anyway? [y/N]: " FORCE
    [[ "$FORCE" =~ ^[Yy]$ ]] || { echo "  Aborted."; exit 0; }
  else
    git pull --ff-only
    ok "Updated to $(git rev-parse --short HEAD)"
  fi
fi

# ── 2. Run DB migrations check ───────────────────────────────
# Our migrations run automatically at app startup (db_migrate()), so
# they are backward-compatible (only ADD COLUMN, never DROP/RENAME).
# No action needed here — just a reminder.
info "DB migrations run automatically at startup (backward-compatible ALTER TABLE only)"

# ── 3. Determine what to update ──────────────────────────────
if [ -n "$TARGET_SERVICE" ] && [ "$TARGET_SERVICE" != "web" ]; then
  # Non-web services (nginx, redis, db) don't need zero-downtime tricks
  info "Rebuilding & restarting service: ${TARGET_SERVICE}..."
  $DC build "$TARGET_SERVICE" 2>&1 | tail -5
  $DC up -d --no-deps "$TARGET_SERVICE"
  ok "Service '${TARGET_SERVICE}' updated"
  exit 0
fi

# ── 4. Build new web image (old container still running) ──────
info "Building new web image (old container still running)..."
$DC build web 2>&1 | tail -10
ok "Build complete"

# ── 5. Scale to 2 — old + new run in parallel ────────────────
info "Starting new container alongside old one..."
$DC up -d --no-deps --scale web=2 web
ok "Two web containers now running — nginx is load-balancing between them"

# ── 6. Wait for new container to pass health check ───────────
info "Waiting for new container to be healthy..."
MAX_WAIT=90
WAITED=0
while true; do
  # Count healthy web containers
  HEALTHY=$($DC ps web --format json 2>/dev/null \
    | python3 -c "import sys,json; data=sys.stdin.read().strip(); \
      rows=[json.loads(l) for l in data.splitlines() if l]; \
      print(sum(1 for r in rows if r.get('Health','') in ('healthy','') and r.get('State','')=='running'))" \
    2>/dev/null || echo "0")

  if [ "$HEALTHY" -ge 2 ]; then
    ok "Both containers healthy"
    break
  fi

  # Fallback: direct health check on the new container
  NEW_CONTAINER=$($DC ps -q web 2>/dev/null | tail -1)
  if [ -n "$NEW_CONTAINER" ]; then
    if docker exec "$NEW_CONTAINER" curl -sf http://localhost:8000/health >/dev/null 2>&1; then
      ok "New container passed health check"
      break
    fi
  fi

  sleep 2; WAITED=$((WAITED + 2))
  if [ "$WAITED" -ge "$MAX_WAIT" ]; then
    warn "Health check timed out after ${MAX_WAIT}s — rolling back to 1 container"
    $DC up -d --no-deps --scale web=1 web
    die "Deploy failed. Check logs: ${DC} logs web"
  fi
  echo -n "."
done
echo ""

# ── 7. Scale back to 1 — Docker stops the old container ──────
info "Removing old container..."
$DC up -d --no-deps --scale web=1 web
ok "Old container stopped. Zero-downtime update complete."

# ── 8. Also update other non-disruptive services ─────────────
info "Ensuring all other services are current..."
$DC up -d certbot-renew db-backup
ok "All services running"

# ── 9. Summary ───────────────────────────────────────────────
echo ""
echo "  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
ok " Update complete — $(git rev-parse --short HEAD 2>/dev/null || echo 'done')"
echo ""
echo "  Health:  curl https://$(grep -oP '(?<=APP_BASE_URL=https://)[\w.\-]+' .env 2>/dev/null || echo 'your-domain')/health"
echo "  Metrics: curl https://$(grep -oP '(?<=APP_BASE_URL=https://)[\w.\-]+' .env 2>/dev/null || echo 'your-domain')/metrics"
echo "  Logs:    ${DC} logs -f web"
echo "  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
