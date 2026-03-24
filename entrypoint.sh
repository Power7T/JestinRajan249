#!/bin/bash
set -e

echo "=== Starting entrypoint.sh ==="
echo "PORT=${PORT:-8000}"
echo "DATABASE_URL set: ${DATABASE_URL:+yes}"
echo "PYTHONPATH=/app"

if [ -z "$DATABASE_URL" ]; then
    echo "FATAL: DATABASE_URL environment variable not set"
    exit 1
fi

echo ""
echo "Step 1: Waiting for database..."
python3 << 'EOF' 2>&1 || { echo "FATAL: DB wait script failed"; exit 1; }
import socket
import time
import os
from urllib.parse import urlparse

db_url = os.getenv('DATABASE_URL')
if not db_url:
    print("[DB] ERROR: DATABASE_URL env var is empty or not set")
    exit(1)

try:
    parsed = urlparse(db_url)
    host = parsed.hostname
    port = parsed.port or 5432
    print(f"[DB] Connecting to {host}:{port}...")
except Exception as e:
    print(f"[DB] ERROR: Failed to parse DATABASE_URL: {e}")
    exit(1)

max_retries = 30
for i in range(max_retries):
    try:
        sock = socket.create_connection((host, port), timeout=2)
        sock.close()
        print(f"[DB] ✓ Connected successfully on attempt {i+1}")
        exit(0)
    except Exception as e:
        if i < max_retries - 1:
            print(f"[DB] Attempt {i+1}/{max_retries} failed: {e}")
            time.sleep(1)
        else:
            print(f"[DB] FATAL: Could not connect after {max_retries} attempts")
            exit(1)
EOF

if [ $? -ne 0 ]; then
    echo "FATAL: Database wait failed"
    exit 1
fi

echo ""
echo "Step 2: Running Alembic migrations..."
cd /app || { echo "FATAL: Could not cd to /app"; exit 1; }

if PYTHONPATH=/app alembic upgrade head 2>&1; then
    echo "[Migrations] ✓ Completed successfully"
else
    echo "[Migrations] ✗ Had non-zero exit (this may be expected if DB was being initialized)"
fi

echo ""
echo "Step 3: Starting uvicorn..."
PORT=${PORT:-8000}
echo "[Uvicorn] Starting on 0.0.0.0:$PORT with 2 workers"
exec uvicorn web.app:app \
    --host 0.0.0.0 \
    --port $PORT \
    --workers 2 \
    --loop uvloop \
    --http h11 \
    --timeout-keep-alive 30 \
    2>&1

echo "FATAL: uvicorn exec failed (should not reach here)"
exit 1
