FROM python:3.11-slim

WORKDIR /app

# Install system deps for imapclient + BeautifulSoup
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl libssl-dev && rm -rf /var/lib/apt/lists/*

COPY web/requirements.txt /app/web/requirements.txt
RUN pip install --no-cache-dir -r /app/web/requirements.txt

# Copy web package
COPY web/ /app/web/

# SKILL.md is mounted at runtime (see docker-compose.yml)
# Copy airbnb-host dir for SKILL.md fallback
COPY airbnb-host/ /app/airbnb-host/

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app

CMD ["python", "-m", "web.app"]
