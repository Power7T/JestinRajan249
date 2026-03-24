#!/bin/bash
set -e

echo "=== Starting entrypoint.sh ==="
echo "PORT=${PORT:-8000}"
echo "DATABASE_URL=${DATABASE_URL:0:50}..."
echo "PYTHONPATH=/app"

echo ""
echo "Step 1: Waiting for database..."
python3 << 'EOF' 2>&1 || { echo "FATAL: DB wait script failed"; exit 1; }
import socket
import time
import os
from urllib.parse import urlparse

db_url = os.getenv('DATABASE_URL', '')
if db_url:
    try:
        parsed = urlparse(db_url)
        host = parsed.hostname or 'localhost'
        print(f"[DB] Extracted host from DATABASE_URL: {host}")
    except Exception as e:
        print(f"[DB] Failed to parse DATABASE_URL: {e}")
        host = 'localhost'
else:
    host = os.getenv('DATABASE_HOST', 'db')
    print(f"[DB] Using DATABASE_HOST: {host}")

port = 5432
max_retries = 30

print(f"[DB] Connecting to {host}:{port}...")
for i in range(max_retries):
    try:
        sock = socket.create_connection((host, port), timeout=2)
        sock.close()
        print(f"[DB] ✓ Connected successfully on attempt {i+1}")
        break
    except Exception as e:
        if i < max_retries - 1:
            print(f"[DB] Attempt {i+1}/{max_retries} failed: {e}")
            time.sleep(1)
        else:
            print(f"[DB] FATAL: Could not connect after {max_retries} attempts")
            exit(1)

print("[DB] Database is ready!")
EOF

if [ $? -ne 0 ]; then
    echo "FATAL: Database wait failed"
    exit 1
fi

echo ""
echo "Step 2: Running Alembic migrations..."
cd /app || { echo "FATAL: Could not cd to /app"; exit 1; }

if ! PYTHONPATH=/app alembic upgrade head 2>&1; then
    echo "WARNING: Alembic migrations had non-zero exit, but continuing..."
fi

echo "[Migrations] Done"

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
