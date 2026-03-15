#!/usr/bin/env bash
# ============================================================
# HostAI — Restore a PostgreSQL backup
#
# Usage:
#   ./scripts/restore-backup.sh                       # list available backups
#   ./scripts/restore-backup.sh <backup.sql.gz>       # restore a specific backup
#   ./scripts/restore-backup.sh --latest              # restore the most recent backup
#
# WARNING: This DROPS and RECREATES the 'hostai' database.
#          Run against a staging environment to verify before using on production.
# ============================================================

set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
info()  { echo -e "${BLUE}[•]${NC} $*"; }
ok()    { echo -e "${GREEN}[✓]${NC} $*"; }
warn()  { echo -e "${YELLOW}[!]${NC} $*"; }
die()   { echo -e "${RED}[✗]${NC} $*" >&2; exit 1; }

if docker compose version >/dev/null 2>&1; then
  DC="docker compose"
else
  DC="docker-compose"
fi

# Load POSTGRES_PASSWORD from .env
if [ -f .env ]; then
  # shellcheck disable=SC1091
  export $(grep -E '^POSTGRES_PASSWORD=' .env | xargs) 2>/dev/null || true
fi
PGPASS="${POSTGRES_PASSWORD:-}"

# ── List available backups ────────────────────────────────────
list_backups() {
  info "Available backups in pgbackups volume:"
  $DC run --rm db-backup /bin/sh -c \
    "ls -lh /backups/*.sql.gz 2>/dev/null | awk '{print \$5, \$9}' || echo '  (no backups found)'"
}

# ── Select backup file ────────────────────────────────────────
BACKUP_FILE=""
if [ "${1:-}" = "--latest" ]; then
  BACKUP_FILE=$($DC run --rm db-backup /bin/sh -c \
    "ls -t /backups/*.sql.gz 2>/dev/null | head -1" 2>/dev/null | tr -d '\r\n')
  [ -z "$BACKUP_FILE" ] && die "No backups found in pgbackups volume"
  info "Latest backup: $BACKUP_FILE"
elif [ -n "${1:-}" ]; then
  # User passed a filename — check if it's a volume path or local path
  if [[ "$1" == /backups/* ]]; then
    BACKUP_FILE="$1"
  else
    BACKUP_FILE="/backups/$(basename "$1")"
  fi
else
  list_backups
  echo ""
  read -rp "  Enter backup filename to restore (or 'latest'): " INPUT
  [ "$INPUT" = "latest" ] && exec "$0" --latest
  BACKUP_FILE="/backups/$(basename "$INPUT")"
fi

echo ""
warn "⚠️  This will DROP and recreate the 'hostai' database using:"
warn "    ${BACKUP_FILE}"
echo ""
read -rp "  Type 'yes' to continue: " CONFIRM
[ "$CONFIRM" != "yes" ] && { echo "Aborted."; exit 0; }

# ── Stop web app to prevent connections during restore ────────
info "Stopping web app..."
$DC stop web 2>/dev/null || true

# ── Restore ───────────────────────────────────────────────────
info "Restoring from ${BACKUP_FILE}..."
$DC run --rm \
  -e PGPASSWORD="${PGPASS}" \
  db-backup \
  /bin/sh -c "
    set -e
    # Verify backup exists
    [ -f '${BACKUP_FILE}' ] || { echo 'ERROR: backup file not found: ${BACKUP_FILE}'; exit 1; }

    # Drop existing connections and database
    psql -h db -U hostai postgres -c \"
      SELECT pg_terminate_backend(pid)
      FROM pg_stat_activity
      WHERE datname = 'hostai' AND pid <> pg_backend_pid();\"  2>/dev/null || true
    psql -h db -U hostai postgres -c 'DROP DATABASE IF EXISTS hostai;'
    psql -h db -U hostai postgres -c 'CREATE DATABASE hostai OWNER hostai;'

    # Restore
    gunzip -c '${BACKUP_FILE}' | psql -h db -U hostai hostai
    echo 'Restore complete'
  "

# ── Restart web app ───────────────────────────────────────────
info "Restarting web app..."
$DC start web

# Wait for healthy
MAX_WAIT=60; WAITED=0
until $DC exec -T web curl -sf http://localhost:8000/health >/dev/null 2>&1; do
  sleep 2; WAITED=$((WAITED + 2))
  [ "$WAITED" -ge "$MAX_WAIT" ] && die "App did not recover after ${MAX_WAIT}s. Check: ${DC} logs web"
done

ok "Restore complete and app is healthy"
echo ""
echo "  Verify: ${DC} exec web python -c \"from web.db import SessionLocal; db=SessionLocal(); print('DB OK:', db.execute(__import__('sqlalchemy').text('SELECT count(*) FROM tenants')).scalar())\""
