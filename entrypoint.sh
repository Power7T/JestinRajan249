#!/bin/bash
set -e

echo "=== HostAI Entrypoint Starting ==="
echo "Environment: PORT=$PORT, DATABASE_URL=$(echo $DATABASE_URL | cut -c1-30)..."

# Wait for database
echo "Waiting for database to be ready..."
for i in {1..30}; do
    if python3 -c "
import os
from urllib.parse import urlparse
import psycopg2

db_url = os.getenv('DATABASE_URL', '')
if not db_url:
    print('ERROR: DATABASE_URL not set')
    exit(1)

try:
    conn = psycopg2.connect(db_url, connect_timeout=2)
    conn.close()
    print('Database connection successful')
    exit(0)
except Exception as e:
    print(f'DB not ready: {e}')
    exit(1)
" 2>/dev/null; then
        echo "✓ Database is ready"
        break
    else
        if [ $i -lt 30 ]; then
            echo "Waiting for database... attempt $i/30"
            sleep 1
        else
            echo "✗ Database failed to become ready after 30 seconds"
            exit 1
        fi
    fi
done

# Run migrations
echo "Running Alembic migrations..."
cd /app
if PYTHONPATH=/app alembic upgrade head; then
    echo "✓ Migrations completed successfully"
else
    echo "✗ Migrations failed"
    exit 1
fi

# Start application
echo "Starting uvicorn on port ${PORT:-8000}..."
exec uvicorn web.app:app \
    --host 0.0.0.0 \
    --port ${PORT:-8000} \
    --workers 2 \
    --loop uvloop \
    --http h11 \
    --timeout-keep-alive 30
