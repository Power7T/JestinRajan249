# Local Development Setup

Complete guide to getting HostAI running locally for development.

## Requirements

- **Python**: 3.12+ (check with `python3 --version`)
- **PostgreSQL**: 15+ (for database)
- **Redis**: 7+ (for caching and job queue)
- **Docker**: Latest version (for containerized services)
- **Docker Compose**: Latest version
- **Node.js**: 18+ (optional, if modifying frontend)

## Quick Start (Docker Compose)

The fastest way to get everything running:

```bash
# Clone the repository
git clone https://github.com/your-org/hostai.git
cd hostai

# Create environment file from template
cp .env.example .env

# Edit .env with your local configuration (see Environment Variables section below)
# For local dev, most defaults work fine

# Start all services (database, redis, app)
docker compose -f docker-compose.dev.yml up -d --build

# Run database migrations
docker compose -f docker-compose.dev.yml exec web alembic upgrade head

# Check logs
docker compose -f docker-compose.dev.yml logs -f web
```

**Access the application:**
- App: http://localhost:8000
- Admin panel: http://localhost:8000/admin
- API docs: http://localhost:8000/docs

**Default admin credentials:**
```
Email: admin@hostai.local
Password: (set in .env file - ADMIN_PASSWORD)
```

## Docker Compose Services

The dev setup includes:

| Service | Port | Purpose |
|---------|------|---------|
| `web` | 8000 | FastAPI application |
| `postgres` | 5432 | Database |
| `redis` | 6379 | Cache and job queue |

**Useful commands:**
```bash
# View all services and their status
docker compose -f docker-compose.dev.yml ps

# View logs from a specific service
docker compose -f docker-compose.dev.yml logs -f web
docker compose -f docker-compose.dev.yml logs -f postgres

# Stop all services
docker compose -f docker-compose.dev.yml down

# Stop all services and remove data
docker compose -f docker-compose.dev.yml down -v

# Access database shell
docker compose -f docker-compose.dev.yml exec postgres psql -U hostai -d hostai_db

# Run a command in the app container
docker compose -f docker-compose.dev.yml exec web python -c "import web; print(web.__version__)"
```

## Manual Setup (Without Docker)

If you prefer to run services directly on your machine:

### 1. Install Dependencies

```bash
# Create Python virtual environment
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install Python dependencies
pip install -r requirements.txt
```

### 2. Set Up PostgreSQL

```bash
# Create database and user (adjust as needed)
psql -U postgres

CREATE DATABASE hostai_db;
CREATE USER hostai WITH PASSWORD 'hostai_password';
ALTER ROLE hostai SET client_encoding TO 'utf8';
ALTER ROLE hostai SET default_transaction_isolation TO 'read committed';
ALTER ROLE hostai SET default_transaction_deferrable TO off;
ALTER ROLE hostai SET default_transaction_read_only TO off;
ALTER ROLE hostai SET timezone TO 'UTC';
GRANT ALL PRIVILEGES ON DATABASE hostai_db TO hostai;
\q
```

### 3. Set Up Redis

```bash
# Start Redis (macOS with Homebrew)
brew services start redis

# Or run with Docker
docker run -d -p 6379:6379 redis:7-alpine
```

### 4. Run Migrations

```bash
# From project root
alembic upgrade head
```

### 5. Start the Application

```bash
# Terminal 1: Start FastAPI backend
python -m uvicorn web.app:app --host 0.0.0.0 --port 8000 --reload

# Terminal 2: Start background worker (for email polling, task scheduling)
python web/worker.py
```

**Access the application:**
- App: http://localhost:8000
- API docs: http://localhost:8000/docs

## Environment Variables

Create a `.env` file in the project root with these variables. See `.env.example` for the complete template.

### Required Variables

```env
# Database
DATABASE_URL=postgresql://hostai:hostai_password@localhost:5432/hostai_db

# Redis
REDIS_URL=redis://localhost:6379/0

# Security
SECRET_KEY=your-secret-key-here-min-32-chars
ADMIN_PASSWORD=secure_password_for_admin

# API Keys (for 3rd party services)
# Get from https://openrouter.ai
OPENROUTER_API_KEY=sk-or-...

# Get from https://twilio.com
TWILIO_ACCOUNT_SID=AC...
TWILIO_AUTH_TOKEN=...
VOICE_TWILIO_FROM_NUMBER=+1555...

# Get from https://elevenlabs.io
ELEVENLABS_API_KEY=...
ELEVENLABS_VOICE_ID=...

# Get from https://deepgram.com
DEEPGRAM_API_KEY=...

# Get from https://dashboard.stripe.com
STRIPE_API_KEY=sk_...
STRIPE_WEBHOOK_SECRET=whsec_...

# Email (for sending notifications)
SMTP_SERVER=smtp.gmail.com
SMTP_PORT=587
SMTP_USERNAME=your-email@gmail.com
SMTP_PASSWORD=app-specific-password
```

### Optional Variables

```env
# Email polling (for IMAP mode)
IMAP_SERVER=imap.gmail.com
IMAP_USERNAME=your-email@gmail.com
IMAP_PASSWORD=app-specific-password
IMAP_FOLDER=INBOX

# WhatsApp (Meta Cloud API)
WHATSAPP_BUSINESS_ACCOUNT_ID=...
WHATSAPP_API_TOKEN=...
WHATSAPP_WEBHOOK_TOKEN=...

# Mailgun (for email webhooks)
MAILGUN_DOMAIN=...
MAILGUN_API_KEY=...

# Feature flags
ENABLE_VOICE_AI=true
ENABLE_WHATSAPP=true
ENABLE_BILLING=true

# Development
DEBUG=true
LOG_LEVEL=DEBUG
```

### Getting API Keys

**OpenRouter:**
1. Go to https://openrouter.ai
2. Create account
3. Click "Keys" in sidebar
4. Create new API key
5. Copy to `OPENROUTER_API_KEY`

**Twilio:**
1. Go to https://console.twilio.com
2. Note your Account SID and Auth Token
3. Create a phone number under "Manage Numbers"
4. Copy to environment variables

**Stripe:**
1. Go to https://dashboard.stripe.com
2. Click "Developers" → "API keys"
3. Copy your Secret key to `STRIPE_API_KEY`
4. Get webhook signing secret from "Webhooks" section

**Other services:**
- ElevenLabs: https://elevenlabs.io/app/settings/api-keys
- Deepgram: https://console.deepgram.com/project/keys
- Meta Cloud API: https://developers.facebook.com/docs/whatsapp/cloud-api/

## Database Migrations

### Run Migrations

```bash
# Upgrade to latest version
alembic upgrade head

# Upgrade specific number of versions
alembic upgrade +2

# Downgrade one version
alembic downgrade -1

# View migration history
alembic current
alembic history
```

### Create New Migration

```bash
# Auto-generate migration from model changes
alembic revision --autogenerate -m "Add phone_number to guests"

# Review the generated file in alembic/versions/
# Run it
alembic upgrade head
```

## Running Tests

```bash
# Run all tests
pytest web/tests/ -v

# Run specific test file
pytest web/tests/test_voice.py -v

# Run tests with coverage
pytest web/tests/ --cov=web --cov-report=html

# Run tests matching pattern
pytest web/tests/ -k "voice" -v

# Run tests in parallel
pytest web/tests/ -n auto
```

## Code Formatting & Linting

```bash
# Format code with Black
black web/ worker/

# Check formatting
black --check web/ worker/

# Lint with Pylint
pylint web/

# Type checking with mypy
mypy web/ --ignore-missing-imports

# All checks at once
bash scripts/lint.sh
```

## Common Development Tasks

### Clear Redis Cache

```bash
docker compose -f docker-compose.dev.yml exec redis redis-cli FLUSHALL
# Or without Docker:
redis-cli FLUSHALL
```

### Reset Database

**Warning: This deletes all data.**

```bash
docker compose -f docker-compose.dev.yml down -v
docker compose -f docker-compose.dev.yml up -d
docker compose -f docker-compose.dev.yml exec web alembic upgrade head
```

### Create Test Tenant

```bash
# Access app shell
docker compose -f docker-compose.dev.yml exec web python

# Then in Python shell
from web.db import get_db
from web.models import Tenant
from sqlalchemy.orm import Session

db = Session()
tenant = Tenant(name="Test Tenant", domain="test.local")
db.add(tenant)
db.commit()
print(f"Created tenant: {tenant.id}")
```

### Access Database Shell

```bash
# With Docker
docker compose -f docker-compose.dev.yml exec postgres psql -U hostai -d hostai_db

# Without Docker
psql -U hostai -d hostai_db
```

### Check Service Health

```bash
# Check app health
curl http://localhost:8000/health

# Check database
docker compose -f docker-compose.dev.yml exec postgres pg_isready

# Check Redis
docker compose -f docker-compose.dev.yml exec redis redis-cli ping
```

## Troubleshooting

### Port Already in Use

```bash
# Find what's using port 8000
lsof -i :8000

# Kill process
kill -9 <PID>

# Or change port in docker-compose.dev.yml
# Change "8000:8000" to "8001:8000"
```

### Database Connection Error

```
psycopg2.OperationalError: could not translate host name "postgres" to address
```

This means the app can't reach the database. Make sure:
1. Docker services are running: `docker compose -f docker-compose.dev.yml ps`
2. Database is healthy: `docker compose -f docker-compose.dev.yml logs postgres`
3. `DATABASE_URL` in `.env` is correct

### Redis Connection Error

```
ConnectionError: Error 111 connecting to localhost:6379
```

Make sure Redis is running:
```bash
docker compose -f docker-compose.dev.yml exec redis redis-cli ping
# Should respond: PONG
```

### Migration Fails

If you get a migration error:

```bash
# View current migration state
alembic current

# Downgrade and try again
alembic downgrade -1
alembic upgrade head
```

### API Returns 500 Error

Check the application logs:
```bash
docker compose -f docker-compose.dev.yml logs -f web

# Or without Docker
# Look at app console output
```

### Tests Fail

```bash
# Run with verbose output to see errors
pytest web/tests/ -v -s

# Run specific test
pytest web/tests/test_voice.py::test_incoming_call -v -s
```

## Development Workflow

1. **Create a feature branch:**
   ```bash
   git checkout -b feature/my-feature
   ```

2. **Make changes and test:**
   ```bash
   # Code changes...
   pytest web/tests/ -v
   black web/
   ```

3. **Create migration if needed:**
   ```bash
   alembic revision --autogenerate -m "Add feature X"
   ```

4. **Commit and push:**
   ```bash
   git add .
   git commit -m "Add feature X"
   git push origin feature/my-feature
   ```

5. **Create pull request** and wait for review

## Next Steps

- Read **[DEVELOPMENT.md](DEVELOPMENT.md)** for code structure and conventions
- Read **[DEPLOYMENT.md](DEPLOYMENT.md)** to deploy to production
- Read **[API.md](API.md)** for REST API endpoints
- See **[README.md](../README.md)** for overall project overview

## Support

- **Documentation**: See `/docs` directory
- **Issues**: GitHub Issues
- **Tests**: Run `pytest` to verify setup works
