FROM python:3.12-slim

# Install system deps (imapclient, psycopg2 need libssl; curl for healthcheck)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libssl-dev curl libpq5 && \
    rm -rf /var/lib/apt/lists/*

# Playwright system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 \
    libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 \
    libgbm1 libasound2 libpango-1.0-0 libpangocairo-1.0-0 \
    fonts-liberation && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first (cached layer when code changes but deps don't)
COPY web/requirements.txt /app/web/requirements.txt
RUN pip install --no-cache-dir -r /app/web/requirements.txt
RUN playwright install chromium --with-deps 2>/dev/null || true

# Copy application + migration config + entrypoint
COPY web/ /app/web/
COPY alembic.ini /app/alembic.ini
COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONPATH=/app

EXPOSE 8000

CMD ["/app/entrypoint.sh"]
