#!/bin/bash
# Check backup health — alerts if backups are failing or missing
# Run daily: crontab: 0 9 * * * /srv/hostai/check-backup-health.sh

BACKUP_DIR="/var/lib/docker/volumes/jestinrajan249_pgbackups/_data"

# If in docker-compose environment
if [ -d "/backups" ]; then
    BACKUP_DIR="/backups"
fi

echo "🔍 Checking backup health..."
echo ""

# 1. Check if backups exist
BACKUP_COUNT=$(find "$BACKUP_DIR" -name "*.sql.gz" -type f 2>/dev/null | wc -l)
echo "📊 Backup count: $BACKUP_COUNT backups"

if [ "$BACKUP_COUNT" -eq 0 ]; then
    echo "❌ CRITICAL: No backups found!"
    exit 1
fi

# 2. Check latest backup age
LATEST_BACKUP=$(ls -t "$BACKUP_DIR"/*.sql.gz 2>/dev/null | head -1)
LATEST_AGE=$(($(date +%s) - $(stat -f%m "$LATEST_BACKUP" 2>/dev/null || stat -c%Y "$LATEST_BACKUP")))
LATEST_AGE_HOURS=$((LATEST_AGE / 3600))

echo "📅 Latest backup age: $LATEST_AGE_HOURS hours"

if [ "$LATEST_AGE_HOURS" -gt 26 ]; then
    echo "❌ WARNING: No backup in 26+ hours!"
    echo "   This suggests backup service is failing"
    exit 1
fi

if [ "$LATEST_AGE_HOURS" -gt 24 ]; then
    echo "⚠️  WARNING: Last backup is 24+ hours old"
fi

# 3. Check backup size
LATEST_SIZE=$(stat -f%z "$LATEST_BACKUP" 2>/dev/null || stat -c%s "$LATEST_BACKUP")
LATEST_SIZE_MB=$((LATEST_SIZE / 1024 / 1024))

echo "💾 Latest backup size: ${LATEST_SIZE_MB}MB"

if [ "$LATEST_SIZE_MB" -lt 1 ]; then
    echo "❌ ERROR: Backup is too small (< 1MB)"
    echo "   Database might be empty or backup is corrupted"
    exit 1
fi

# 4. Check volume space
VOLUME_PATH="/var/lib/docker/volumes/jestinrajan249_pgbackups"

if [ -d "$VOLUME_PATH/_data" ]; then
    USED_SPACE=$(du -sh "$VOLUME_PATH/_data" 2>/dev/null | cut -f1)
    TOTAL_SPACE=$(df -h "$VOLUME_PATH" | tail -1 | awk '{print $2}')
    USED_PERCENT=$(df "$VOLUME_PATH" | tail -1 | awk '{print $5}' | sed 's/%//')
    
    echo "💿 Volume usage: $USED_SPACE / $TOTAL_SPACE ($USED_PERCENT%)"
    
    if [ "$USED_PERCENT" -gt 90 ]; then
        echo "❌ CRITICAL: Backup volume is 90%+ full!"
        echo "   Consider archiving old backups to cloud storage"
        exit 1
    fi
    
    if [ "$USED_PERCENT" -gt 80 ]; then
        echo "⚠️  WARNING: Backup volume is 80%+ full"
    fi
fi

# 5. Check retention (7 days)
OLD_COUNT=$(find "$BACKUP_DIR" -name "*.sql.gz" -mtime +7 -type f 2>/dev/null | wc -l)
if [ "$OLD_COUNT" -gt 0 ]; then
    echo "⚠️  Found $OLD_COUNT backups older than 7 days"
    echo "   These should have been auto-deleted"
fi

# 6. Summary
echo ""
echo "✅ Backup health check PASSED"
echo ""
echo "Recommended actions:"
echo "  - Review: docker-compose logs db-backup | tail"
echo "  - Manual backup: ./scripts/create-backup.sh"
echo "  - Recovery test: ./scripts/test-recovery.sh"
