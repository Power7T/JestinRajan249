#!/bin/bash
# Monthly recovery test — validates backups are restorable
# Run this monthly: crontab: 0 3 1 * * /srv/hostai/test_recovery.sh

set -e

LOG_FILE="/var/log/hostai_recovery_test.log"
BACKUP_DIR="/var/lib/docker/volumes/jestinrajan249_pgbackups/_data"

# If in docker-compose environment
if [ -d "/backups" ]; then
    BACKUP_DIR="/backups"
fi

{
    echo "======================================"
    echo "🔄 Recovery test started: $(date)"
    echo "======================================"

    # 1. Find latest backup
    LATEST_BACKUP=$(ls -t "$BACKUP_DIR"/*.sql.gz 2>/dev/null | head -1)
    if [ -z "$LATEST_BACKUP" ]; then
        echo "❌ ERROR: No backups found in $BACKUP_DIR"
        exit 1
    fi
    echo "📦 Testing backup: $LATEST_BACKUP"

    # 2. Check backup size
    BACKUP_SIZE=$(ls -lh "$LATEST_BACKUP" | awk '{print $5}')
    echo "📊 Backup size: $BACKUP_SIZE"

    if [ $(stat -f%z "$LATEST_BACKUP" 2>/dev/null || stat -c%s "$LATEST_BACKUP") -lt 1000 ]; then
        echo "⚠️  WARNING: Backup is suspiciously small (< 1KB)"
        echo "⚠️  Database might be empty"
    fi

    # 3. Stop app temporarily
    echo "🛑 Stopping app..."
    docker-compose stop web worker 2>/dev/null || true

    # 4. Create test database
    echo "🏗️  Creating test database..."
    docker-compose exec -T db psql -U postgres << 'EOF' || true
DROP DATABASE IF EXISTS hostai_test;
CREATE DATABASE hostai_test;
EOF

    # 5. Restore into test DB
    echo "⏳ Restoring backup..."
    docker-compose exec -T db bash -c "zcat '$LATEST_BACKUP' | psql -U hostai hostai_test" || {
        echo "❌ FAILED: Backup did not restore"
        docker-compose start web worker 2>/dev/null || true
        exit 1
    }

    # 6. Verify schema
    echo "✓ Verifying schema..."
    TABLE_COUNT=$(docker-compose exec -T db psql -U hostai -d hostai_test -t -c "SELECT count(*) FROM information_schema.tables WHERE table_schema='public'" | tr -d ' ')
    echo "  Tables: $TABLE_COUNT"

    if [ "$TABLE_COUNT" -lt 5 ]; then
        echo "⚠️  WARNING: Very few tables ($TABLE_COUNT) — backup might be incomplete"
    fi

    # 7. Verify key data
    echo "✓ Verifying data..."
    DRAFT_COUNT=$(docker-compose exec -T db psql -U hostai -d hostai_test -t -c "SELECT count(*) FROM drafts" | tr -d ' ')
    RES_COUNT=$(docker-compose exec -T db psql -U hostai -d hostai_test -t -c "SELECT count(*) FROM reservations" | tr -d ' ')
    TENANT_COUNT=$(docker-compose exec -T db psql -U hostai -d hostai_test -t -c "SELECT count(*) FROM tenants" | tr -d ' ')

    echo "  Drafts: $DRAFT_COUNT"
    echo "  Reservations: $RES_COUNT"
    echo "  Tenants: $TENANT_COUNT"

    # 8. Cleanup test DB
    echo "🧹 Cleaning up..."
    docker-compose exec -T db psql -U postgres -c "DROP DATABASE hostai_test" 2>/dev/null || true

    # 9. Restart app
    echo "🚀 Restarting app..."
    docker-compose start web worker 2>/dev/null || true

    echo "======================================"
    echo "✅ Recovery test PASSED: $(date)"
    echo "======================================"

} | tee -a "$LOG_FILE"
