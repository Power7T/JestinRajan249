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
COPY airbnb-host/SKILL.md /app/airbnb-host/SKILL.md
COPY alembic.ini /app/alembic.ini
COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONPATH=/app
ENV DATABASE_HOST=db

EXPOSE 8000

# Run entrypoint script which runs migrations then starts app
ENTRYPOINT ["/app/entrypoint.sh"]
