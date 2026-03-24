#!/bin/bash
# Create manual backup before deployment
# Usage: ./scripts/create-backup.sh [backup-name]

BACKUP_DIR="/var/lib/docker/volumes/jestinrajan249_pgbackups/_data"

# If in docker-compose environment
if [ -d "/backups" ]; then
    BACKUP_DIR="/backups"
fi

# Create directory if it doesn't exist
mkdir -p "$BACKUP_DIR"

# Generate backup filename
if [ -z "$1" ]; then
    BACKUP_FILE="hostai_$(date +%Y%m%d_%H%M%S).sql.gz"
else
    BACKUP_FILE="hostai_$1.sql.gz"
fi

BACKUP_PATH="$BACKUP_DIR/$BACKUP_FILE"

echo "💾 Creating backup: $BACKUP_FILE"
echo ""

# Check if we can reach docker-compose
if ! command -v docker-compose &> /dev/null; then
    echo "❌ docker-compose not found"
    exit 1
fi

# Create backup
docker-compose exec -T db bash -c "pg_dump -U hostai -h localhost hostai | gzip" > "$BACKUP_PATH"

if [ $? -ne 0 ]; then
    echo "❌ Backup FAILED"
    rm -f "$BACKUP_PATH"
    exit 1
fi

# Verify
SIZE=$(stat -f%z "$BACKUP_PATH" 2>/dev/null || stat -c%s "$BACKUP_PATH")
SIZE_MB=$((SIZE / 1024 / 1024))

if [ "$SIZE" -lt 1000 ]; then
    echo "⚠️  WARNING: Backup is very small ($SIZE bytes)"
    echo "    Database might be empty or backup is corrupted"
fi

echo "✅ Backup created: $BACKUP_FILE"
echo "📊 Size: ${SIZE_MB}MB"
echo ""
echo "💡 Safe to deploy now"
