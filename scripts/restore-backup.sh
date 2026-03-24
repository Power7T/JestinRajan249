#!/bin/bash
# Restore HostAI database from backup
# Usage: ./restore-backup.sh <backup-file.sql.gz>
#        ./restore-backup.sh --latest
#        ./restore-backup.sh           (list available backups)

set -e

BACKUP_DIR="/var/lib/docker/volumes/jestinrajan249_pgbackups/_data"

# If running from docker-compose
if [ -d "/backups" ]; then
    BACKUP_DIR="/backups"
fi

if [ "$1" == "--latest" ]; then
    # Find most recent backup
    BACKUP_FILE=$(ls -t "$BACKUP_DIR"/*.sql.gz 2>/dev/null | head -1)
    if [ -z "$BACKUP_FILE" ]; then
        echo "❌ No backups found in $BACKUP_DIR"
        exit 1
    fi
elif [ -z "$1" ]; then
    # List available backups
    echo "📦 Available backups:"
    ls -lh "$BACKUP_DIR"/*.sql.gz 2>/dev/null | awk '{print $9, "(" $5 ")"}'
    echo ""
    echo "Usage:"
    echo "  ./restore-backup.sh --latest                    # Restore newest backup"
    echo "  ./restore-backup.sh hostai_20260324_020000.sql.gz  # Restore specific backup"
    exit 0
else
    BACKUP_FILE="$BACKUP_DIR/$1"
    # Support both full path and filename
    if [ ! -f "$BACKUP_FILE" ] && [ -f "$1" ]; then
        BACKUP_FILE="$1"
    fi
fi

if [ ! -f "$BACKUP_FILE" ]; then
    echo "❌ Backup file not found: $BACKUP_FILE"
    exit 1
fi

echo "🔄 Restoring from backup: $BACKUP_FILE"
echo "⚠️  This will REPLACE the current database"
echo "📋 Make sure you have:"
echo "   - Stopped the web app: docker-compose stop web worker"
echo "   - Created a manual backup: cp /backups/latest.sql.gz /tmp/"
echo ""
read -p "Continue? (yes/no): " -r
if [[ ! $REPLY =~ ^[Yy][Ee][Ss]$ ]]; then
    echo "Cancelled."
    exit 0
fi

echo ""
echo "⏳ Restoring database..."

# Get database credentials from docker-compose or environment
DB_USER="${DB_USER:-hostai}"
DB_HOST="${DB_HOST:-db}"
DB_NAME="${DB_NAME:-hostai}"

# Method 1: Using docker-compose (preferred)
if command -v docker-compose &> /dev/null; then
    echo "Using docker-compose..."
    docker-compose exec -T db bash -c "zcat '$BACKUP_FILE' | psql -U $DB_USER -h localhost $DB_NAME"
elif command -v docker &> /dev/null; then
    # Method 2: Using docker directly
    CONTAINER=$(docker ps --filter "name=postgres" -q | head -1)
    if [ -z "$CONTAINER" ]; then
        echo "❌ No PostgreSQL container found"
        exit 1
    fi
    echo "Using docker container: $CONTAINER"
    zcat "$BACKUP_FILE" | docker exec -i "$CONTAINER" psql -U $DB_USER $DB_NAME
else
    # Method 3: Direct psql connection
    echo "Using psql directly (requires local PostgreSQL)"
    psql -h $DB_HOST -U $DB_USER -d $DB_NAME < <(zcat "$BACKUP_FILE")
fi

if [ $? -ne 0 ]; then
    echo "❌ Restore FAILED"
    exit 1
fi

echo ""
echo "✅ Restore completed!"
echo ""
echo "📋 Next steps:"
echo "   1. Verify data: docker-compose exec db psql -U hostai -d hostai -c \"SELECT count(*) FROM drafts;\""
echo "   2. Restart app: docker-compose up -d web worker"
echo "   3. Check logs: docker-compose logs web | grep -i error"
echo ""
