FROM python:3.12-slim

# Install system deps (imapclient, psycopg2 need libssl; curl for healthcheck)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libssl-dev curl libpq5 && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first (cached layer when code changes but deps don't)
COPY web/requirements.txt /app/web/requirements.txt
RUN pip install --no-cache-dir -r /app/web/requirements.txt

# Copy application
COPY web/ /app/web/
COPY airbnb-host/ /app/airbnb-host/
# SKILL.md mounted at runtime via docker-compose volume

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONPATH=/app

EXPOSE 8000

# 2 workers × (2 × CPU + 1) — good default for I/O-bound FastAPI
# Use WORKERS env var to override (e.g. WORKERS=4 for a 2-core server)
CMD ["sh", "-c", "uvicorn web.app:app --host 0.0.0.0 --port 8000 --workers ${WORKERS:-2} --loop uvloop --http h11 --timeout-keep-alive 30"]
