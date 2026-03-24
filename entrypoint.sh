#!/bin/bash
set -e

echo "Waiting for database to be ready..."
python3 << 'EOF'
import socket
import time
import os
from urllib.parse import urlparse

# Support both Railway (DATABASE_URL) and local (DATABASE_HOST) setups
db_url = os.getenv('DATABASE_URL', '')
if db_url:
    # Railway Postgres: extract host from postgresql://user:pass@host:port/db
    parsed = urlparse(db_url)
    host = parsed.hostname or 'localhost'
else:
    host = os.getenv('DATABASE_HOST', 'db')

port = 5432
max_retries = 30

for i in range(max_retries):
    try:
        sock = socket.create_connection((host, port), timeout=1)
        sock.close()
        print(f"Database at {host}:5432 is ready!")
        break
    except (socket.timeout, socket.error, OSError):
        if i < max_retries - 1:
            print(f"Waiting for DB at {host}... ({i+1}/{max_retries})")
            time.sleep(1)
        else:
            print(f"Database at {host}:5432 did not become ready in time")
            exit(1)
EOF

echo "Running Alembic migrations..."
cd /app
PYTHONPATH=/app alembic upgrade head || echo "Alembic migration failed (might be normal on first run)"

echo "Starting application..."
exec uvicorn web.app:app --host 0.0.0.0 --port ${PORT:-8000} --workers ${WORKERS:-2} --loop uvloop --http h11 --timeout-keep-alive 30
