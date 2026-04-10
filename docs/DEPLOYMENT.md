# Production Deployment

Complete guide to deploying HostAI to production on Railway.

## Overview

HostAI is designed for deployment on Railway with these components:

- **Web Service**: FastAPI application
- **Worker Service**: Background job processor
- **PostgreSQL**: Managed database
- **Redis**: Managed cache/queue
- **Cloudflare R2**: Object storage for recordings
- **Nginx**: Reverse proxy and static file serving

## Pre-Deployment Checklist

Before deploying to production, ensure:

- [ ] All tests pass: `pytest web/tests/ -v`
- [ ] Code is formatted: `black web/ worker/`
- [ ] No linting errors: `pylint web/`
- [ ] All migrations created: `alembic upgrade head` (local)
- [ ] Environment variables documented
- [ ] Third-party service accounts created (Stripe, Twilio, OpenRouter, etc.)
- [ ] Database backup strategy in place
- [ ] Monitoring and alerting configured

## Railway Deployment

### 1. Create Railway Project

```bash
# Login to Railway
railway login

# Create new project
railway init

# Follow the prompts:
# - Project name: hostai
# - Template: None (blank project)
```

### 2. Add Services to Railway

**Add PostgreSQL:**
```bash
railway add
# Select PostgreSQL
# Railway will create the database and generate DATABASE_URL
```

**Add Redis:**
```bash
railway add
# Select Redis
# Railway will generate REDIS_URL
```

### 3. Configure Environment Variables

In Railway dashboard:

1. Go to your project
2. Click "Variables" (or each service's settings)
3. Add all required variables from `.env.example`:

```env
# Security
SECRET_KEY=<generate-strong-key>
ADMIN_PASSWORD=<secure-password>

# Third-party APIs
OPENROUTER_API_KEY=<get-from-openrouter>
TWILIO_ACCOUNT_SID=<get-from-twilio>
TWILIO_AUTH_TOKEN=<get-from-twilio>
VOICE_TWILIO_FROM_NUMBER=+1...
ELEVENLABS_API_KEY=<get-from-elevenlabs>
ELEVENLABS_VOICE_ID=<get-from-elevenlabs>
DEEPGRAM_API_KEY=<get-from-deepgram>
STRIPE_API_KEY=<get-from-stripe>
STRIPE_WEBHOOK_SECRET=<get-from-stripe>

# Email (set based on your provider)
SMTP_SERVER=smtp.gmail.com
SMTP_PORT=587
SMTP_USERNAME=your-email@example.com
SMTP_PASSWORD=<app-password>

# Cloudflare R2 (for storage)
R2_ACCOUNT_ID=<get-from-cloudflare>
R2_ACCESS_KEY_ID=<create-in-cloudflare>
R2_SECRET_ACCESS_KEY=<create-in-cloudflare>
R2_BUCKET_NAME=hostai-prod

# Environment
ENVIRONMENT=production
DEBUG=false
LOG_LEVEL=INFO
```

### 4. Deploy Web Service

Create `Dockerfile` (if not exists) in project root:

```dockerfile
FROM python:3.12-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    postgresql-client \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY . .

# Run migrations and start app
CMD ["sh", "-c", "alembic upgrade head && uvicorn web.app:app --host 0.0.0.0 --port $PORT"]
```

Add to Railway:

```bash
# In Railway dashboard, create new service
# Select "GitHub" and connect your repository
# Configure:
# - Root Directory: . (or /web if using subdirectory)
# - Dockerfile: Dockerfile
# - Port: 8000 (Railway will use $PORT env var)
```

### 5. Deploy Worker Service

Use the repo's dedicated [`Dockerfile.worker`](../../Dockerfile.worker):

```dockerfile
FROM python:3.12-slim
...
CMD ["python", "-m", "web.worker_runner"]
```

Add to Railway:

```bash
# Create new service in Railway dashboard
# Select "GitHub" with same repository
# Configure:
# - Root Directory: .
# - Dockerfile: Dockerfile.worker
# - Start Command: leave blank (the Dockerfile already starts web.worker_runner)
# - Public Domain: do not generate one for the worker service
```

Recommended environment split:

```env
# Web service
RUN_EMBEDDED_WORKERS=false

# Worker service
RUN_EMBEDDED_WORKERS=true
```

### 6. Set Up Custom Domain

In Railway dashboard:

1. Go to Web service settings
2. Click "Domains"
3. Add custom domain (e.g., `app.yourdomain.com`)
4. Update DNS records in your domain registrar:
   ```
   CNAME app.yourdomain.com → railway-domain.up.railway.app
   ```

### 7. Configure Webhooks

Update webhook URLs in third-party services:

**Twilio Webhooks:**
1. Go to https://console.twilio.com/phone-numbers/incoming
2. For your phone number, set webhook:
   ```
   URL: https://app.yourdomain.com/voice/incoming
   Method: POST
   ```

**Stripe Webhooks:**
1. Go to https://dashboard.stripe.com/webhooks
2. Create endpoint:
   ```
   URL: https://app.yourdomain.com/webhooks/stripe
   Events: charge.updated, customer.subscription.*
   ```

**Meta (WhatsApp) Webhooks:**
1. Go to Facebook Developer Console
2. Set webhook:
   ```
   URL: https://app.yourdomain.com/webhooks/whatsapp
   Verify Token: <set in environment>
   ```

**Mailgun Webhooks:**
1. Go to https://app.mailgun.com/app/webhooks
2. Create webhooks for events:
   ```
   URL: https://app.yourdomain.com/webhooks/mailgun
   ```

## Database Management

### Connect to Production Database

```bash
# Using Railway CLI
railway connect postgres

# Or using psql directly (get DATABASE_URL from Railway)
psql $DATABASE_URL
```

### Backup Database

Railway provides automatic daily backups. To manually backup:

```bash
# Download backup
pg_dump $DATABASE_URL > hostai_backup_$(date +%Y%m%d).sql

# Restore from backup
psql $DATABASE_URL < hostai_backup_20260331.sql
```

### Run Migrations in Production

Railway runs migrations automatically during deployment (see Dockerfile CMD).

To run manually if needed:

```bash
# Using Railway
railway run alembic upgrade head

# Or SSH into service
railway shell web
alembic upgrade head
```

## Monitoring & Logging

### View Logs

In Railway dashboard:

1. Click service
2. Click "Deployments"
3. Click recent deployment
4. View logs in real-time

Or via CLI:

```bash
railway logs -f web
railway logs -f worker
```

### Health Check

Railway monitors your service health. Configure endpoint in service settings:

```
Health Check URL: /health
```

Ensure your app returns 200 status on this endpoint.

### Error Tracking

Configure error tracking in `.env`:

```env
# For Sentry (optional)
SENTRY_DSN=https://...@sentry.io/...
```

Then add to your app startup in `web/app.py`:

```python
import sentry_sdk
if SENTRY_DSN:
    sentry_sdk.init(SENTRY_DSN, ...)
```

## Performance Optimization

### Database Optimization

```sql
-- Connect to production database
-- Create indexes for common queries
CREATE INDEX idx_voice_calls_tenant_created
  ON voice_calls(tenant_id, created_at DESC);

CREATE INDEX idx_reservations_tenant_checkin
  ON reservations(tenant_id, checkin DESC);

CREATE INDEX idx_messages_tenant_created
  ON messages(tenant_id, created_at DESC);
```

### Caching Strategy

Configure Redis caching in `web/app.py`:

```python
# Cache settings
CACHE_TTL = 3600  # 1 hour for most data
CACHE_USER_TTL = 300  # 5 minutes for user-specific data
```

### Rate Limiting

Configured in `web/rate_limiter.py`. In production:

```python
# More strict limits
RATE_LIMIT_REQUESTS = 100
RATE_LIMIT_PERIOD = 60  # per minute
```

## Scaling

### Horizontal Scaling

In Railway dashboard:

1. Go to Web service settings
2. Click "Deploy"
3. Increase "Max Instances"
4. Railway will load-balance across instances

### Database Connection Pooling

Configure in `.env`:

```env
# PgBouncer settings (if using Railway Postgres)
DB_POOL_SIZE=20
DB_POOL_MAX_OVERFLOW=40
DB_POOL_TIMEOUT=30
```

### Redis Scaling

For high-traffic apps, switch to Premium Redis:

1. In Railway, delete current Redis
2. Click "Add"
3. Select "Redis (Premium)"
4. Railway will update REDIS_URL automatically

## Troubleshooting Production Issues

### Service Won't Start

Check logs:
```bash
railway logs -f web
```

Common issues:
- Missing environment variable → Add to Railway
- Migration failed → Run manually: `railway run alembic upgrade head`
- Database unreachable → Check DATABASE_URL is correct

### High Database Usage

```bash
# Check slow queries
railway shell postgres
SELECT query, mean_time FROM pg_stat_statements
ORDER BY mean_time DESC LIMIT 10;

# Add indexes for slow queries
CREATE INDEX idx_column ON table(column);
```

### Redis Out of Memory

```bash
# Check Redis memory
railway shell redis
INFO memory

# Clear old keys if needed
FLUSHDB  # Warning: deletes all cache data
```

### WebSocket Connection Issues

If using WebSocket for real-time updates:

1. Ensure Railway allows WebSocket connections (usually enabled by default)
2. Test: `wscat -c wss://app.yourdomain.com/ws`

### Webhook Delivery Failures

Check webhook logs in Railway:
```bash
railway logs -f web | grep webhook
```

Common issues:
- URL incorrect → Update in third-party service
- Signature validation failing → Check webhook secret in `.env`
- Timeout → Response taking too long, optimize handler

## Zero-Downtime Deployment

Railway supports rolling deployments:

1. Ensure Dockerfile has health check
2. Set deployment strategy to "Rolling"
3. Each new instance starts before old ones stop

To deploy:

```bash
git push origin main
# Railway automatically deploys
```

## Disaster Recovery

### Database Restore

If database corrupts:

```bash
# Download backup from Railway
# Create new postgres service in Railway
# Restore:
psql $NEW_DATABASE_URL < backup.sql
# Update DATABASE_URL in environment
# Redeploy app
```

### Service Restoration

If service completely fails:

1. Check Railway dashboard for error
2. Roll back to previous deployment:
   - Click service
   - Click "Deployments"
   - Select previous deployment
   - Click "Redeploy"

## Cost Optimization

### Estimate Monthly Cost

Railway charges by resource usage:

```
Web Service (2vCPU, 1GB RAM, shared):  ~$5-10/month
PostgreSQL (5GB):                      ~$15/month
Redis (500MB):                         ~$5/month
─────────────────────────────────
Total Infrastructure:                  ~$25-30/month
(+ third-party API costs: Twilio, OpenAI, etc.)
```

### Reduce Costs

- Use Railway's shared database (cheaper than dedicated)
- Scale down during off-hours
- Compress log retention
- Optimize API calls (cache responses)
- Use batch processing for background jobs

## Security Best Practices

### SSL/TLS

Railway provides free SSL certificates. Ensure:

```env
REQUIRE_HTTPS=true
HSTS_MAX_AGE=31536000
```

In your app, redirect HTTP → HTTPS:

```python
@app.middleware("http")
async def redirect_https(request, call_next):
    if request.url.scheme != "https" and REQUIRE_HTTPS:
        return RedirectResponse(
            url=request.url.replace(scheme="https"),
            status_code=301
        )
    return await call_next(request)
```

### Secrets Management

**Never commit secrets.** Use Railway's environment variables.

To rotate a secret:

1. Go to Railway Variables
2. Update the value
3. Services restart automatically
4. Git and code don't need changes

### Firewall/DDoS Protection

Configure in Cloudflare (if using):

1. Add domain to Cloudflare
2. Update DNS to Cloudflare
3. Enable DDoS protection in Cloudflare dashboard

## Monitoring Checklist

- [ ] Set up error tracking (Sentry, Rollbar, etc.)
- [ ] Configure log aggregation (Datadog, Papertrail, etc.)
- [ ] Set up alerts for high error rates
- [ ] Monitor database performance
- [ ] Track API response times
- [ ] Alert on service unavailability
- [ ] Regular backups tested

## Next Steps

1. Complete all pre-deployment checks
2. Deploy to staging first to test
3. Run smoke tests in staging
4. Deploy to production during low-traffic hours
5. Monitor closely for 24 hours after deployment
6. Document any issues and solutions

See **[SECURITY.md](SECURITY.md)** for security considerations.

See **[README.md](../README.md)** for quick start commands.
