# HostAI Production Deployment Guide

**Last Updated:** 2026-03-24

## Table of Contents
1. [Pre-Deployment Checklist](#pre-deployment-checklist)
2. [Environment Setup](#environment-setup)
3. [Database Initialization](#database-initialization)
4. [Deployment Steps](#deployment-steps)
5. [Post-Deployment Verification](#post-deployment-verification)
6. [Monitoring & Alerts](#monitoring--alerts)
7. [Backup & Recovery](#backup--recovery)
8. [Troubleshooting](#troubleshooting)

---

## Pre-Deployment Checklist

**NEVER deploy without checking these:**

### Code Quality
- [ ] All tests pass locally
- [ ] No linting errors
- [ ] Database migrations are tested
- [ ] New environment variables documented

### Configuration
- [ ] All required env vars set (see below)
- [ ] Database connection string verified
- [ ] Redis connection working
- [ ] Stripe keys configured (if using payments)
- [ ] SMTP credentials set (if sending emails)

### Testing (BEFORE production)
- [ ] Test locally: `docker-compose up -d`
- [ ] Can create account: `http://localhost:8000`
- [ ] Can log in
- [ ] Dashboard loads without 500 errors
- [ ] Run migrations successfully

### Security
- [ ] SECRET_KEY is strong (32+ chars)
- [ ] FIELD_ENCRYPTION_KEY is valid Fernet key
- [ ] No secrets in git history
- [ ] APP_BASE_URL uses HTTPS in production

---

## Environment Setup

### Required Environment Variables

**Copy this `.env` template and fill in your values:**

```bash
# ============================================================
# CRITICAL — These MUST be set correctly
# ============================================================

# Database
DATABASE_URL=postgresql://hostai:PASSWORD@HOST:5432/hostai
DATABASE_DIRECT_URL=postgresql://hostai:PASSWORD@HOST:5432/hostai
POSTGRES_PASSWORD=STRONG_PASSWORD_HERE

# Secrets (generate if missing)
SECRET_KEY=<generate with: python -c "import secrets; print(secrets.token_hex(32))">
FIELD_ENCRYPTION_KEY=<generate with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())">

# App Config
ENVIRONMENT=production
APP_BASE_URL=https://your-domain.com  # EXACT domain, with https://
DOMAIN=your-domain.com

# ============================================================
# Database Migrations (PRODUCTION SETTINGS)
# ============================================================
AUTO_CREATE_TABLES=false      # Never auto-create in production
AUTO_MIGRATE=true             # Run migrations on startup
RUN_EMBEDDED_WORKERS=true     # Enable background jobs

# ============================================================
# Services
# ============================================================
REDIS_URL=redis://redis:6379/0
PORT=8000
WORKERS=4                     # Adjust for your server CPU count

# ============================================================
# Optional (but recommended)
# ============================================================

# Monitoring
SENTRY_DSN=<get from sentry.io if using error tracking>

# Email
SMTP_HOST=smtp.gmail.com  # or your provider
SMTP_PORT=587
SMTP_USER=your-email@gmail.com
SMTP_PASS=your-app-password
SMTP_FROM=noreply@your-domain.com

# Stripe (if using payments)
STRIPE_SECRET_KEY=sk_live_...
STRIPE_WEBHOOK_SECRET=whsec_...
STRIPE_PRICE_BAILEYS=price_...
STRIPE_PRICE_META_CLOUD=price_...
STRIPE_PRICE_SMS=price_...
STRIPE_PRICE_PRO=price_...

# SSL (for certbot auto-renewal)
CERTBOT_EMAIL=admin@your-domain.com
```

### Verify Environment Variables

```bash
# Check all required vars are set
python3 << 'EOF'
import os
required = [
    'SECRET_KEY', 'FIELD_ENCRYPTION_KEY', 'APP_BASE_URL',
    'DATABASE_URL', 'POSTGRES_PASSWORD', 'ENVIRONMENT'
]
missing = [v for v in required if not os.getenv(v)]
if missing:
    print(f"❌ Missing vars: {missing}")
    exit(1)
print("✓ All required vars set")
EOF
```

---

## Database Initialization

### First-Time Setup (Fresh Deployment)

```bash
# 1. Start database only
docker-compose up -d db redis

# 2. Wait for database to be ready
sleep 10

# 3. Start web service (migrations run automatically)
docker-compose up -d web

# 4. Wait for migrations to complete
sleep 20

# 5. Verify database is initialized
docker-compose exec web python3 << 'EOF'
from web.db import SessionLocal
from web.models import Tenant
db = SessionLocal()
count = db.query(Tenant).count()
print(f"✓ Database initialized. Tenants: {count}")
EOF
```

### Verify Migrations Ran

```bash
# Check alembic version
docker-compose exec web alembic current

# Should show the latest revision (head)
```

---

## Deployment Steps

### Option 1: Local Deployment (Development)

```bash
cd /path/to/hostai
git pull origin main
docker-compose down
docker volume rm hostai_pgdata  # Only on first deploy or to reset
docker-compose up -d
```

### Option 2: Server Deployment (Fly.io / Railway)

```bash
# Set environment variables in your platform's dashboard:
# - All vars from the Environment Setup section above

# Deploy
fly deploy          # Fly.io
# OR
railway up          # Railway

# Verify
fly logs -a app     # Fly.io
# OR
railway logs        # Railway
```

### Option 3: Custom Server (VPS)

```bash
# 1. SSH into server
ssh user@server

# 2. Pull latest code
cd /srv/hostai
git pull origin main

# 3. Update environment variables
nano .env

# 4. Redeploy
docker-compose down
docker-compose up -d

# 5. Wait for migrations
sleep 20

# 6. Verify
curl https://your-domain.com/ping
```

---

## Post-Deployment Verification

**Run this checklist immediately after deploying:**

```bash
# 1. Check app is running
curl https://your-domain.com/ping
# Expected: {"ok":true}

# 2. Check database is initialized
curl https://your-domain.com/login | grep "Sign in"
# Expected: Should return login HTML (no 500 error)

# 3. Check logs for errors
docker-compose logs web --tail 50 | grep -i error

# 4. Create test account
# Go to https://your-domain.com and create test account

# 5. Test login
# Log out and log back in

# 6. Check database migrations
docker-compose exec web alembic current
```

### Health Check Endpoints

```bash
# Ping (no DB required)
curl https://your-domain.com/ping
# Expected: {"ok": true}

# Metrics (if METRICS_TOKEN set)
curl https://your-domain.com/metrics -H "Authorization: Bearer $METRICS_TOKEN"
```

---

## Monitoring & Alerts

### Container Health

```bash
# Check container status
docker-compose ps

# Check logs in real-time
docker-compose logs -f web
docker-compose logs -f db

# Check resource usage
docker stats
```

### Database Health

```bash
# Check database connection
docker-compose exec db psql -U hostai -d hostai -c "SELECT now();"

# Check database size
docker-compose exec db psql -U hostai -d hostai -c "\l+ hostai"

# Check tables exist
docker-compose exec db psql -U hostai -d hostai -c "\dt"
```

### Set Up Monitoring (Recommended)

**1. Sentry (Error Tracking)**
```bash
# Sign up at sentry.io
# Create Python project
# Add SENTRY_DSN to .env
# All errors now logged to Sentry
```

**2. Health Check Service**
```bash
# Use Uptime Robot / Healthchecks.io
# Monitor: https://your-domain.com/ping
# Alert if down > 5 minutes
```

**3. Disk Space Alert**
```bash
# Monitor docker volume storage
docker system df

# Alert if > 80% full
# Plan: rotate old logs, clean backups
```

---

## Backup & Recovery

### Automated Daily Backups

```bash
# The docker-compose includes db-backup service
# Backups run daily at 2 AM UTC (configurable)
# Stored in pgbackups volume

# List backups
docker-compose run --rm db-backup ls -lh /backups/

# Restore from backup
./scripts/restore-backup.sh --latest
```

### Manual Backup

```bash
# Create backup now
docker-compose run --rm db-backup \
  bash -c "pg_dump -U hostai -h db hostai | gzip > /backups/backup-$(date +%Y%m%d-%H%M%S).sql.gz"

# Verify backup size
docker-compose run --rm db-backup ls -lh /backups/
```

### Recovery Procedure

```bash
# 1. List available backups
./scripts/restore-backup.sh

# 2. Restore from backup
./scripts/restore-backup.sh <backup-name.sql.gz>

# 3. Verify recovery
docker-compose logs web | grep "Application startup complete"
```

---

## Troubleshooting

### "502 Bad Gateway" or "Connection Refused"

**Check:**
```bash
# 1. Is app running?
docker-compose ps web
# Expected: Status "Up"

# 2. Check logs
docker-compose logs web --tail 100 | grep -i error

# 3. Check database is healthy
docker-compose exec db psql -U hostai -c "SELECT 1"

# 4. Restart app
docker-compose restart web
sleep 10
curl https://your-domain.com/ping
```

### "500 Internal Server Error"

**Check:**
```bash
# 1. Check logs for exact error
docker-compose logs web --tail 100

# 2. Common causes:
#    - Missing environment variable
#    - Database table missing (migrations didn't run)
#    - Stripe key invalid
#    - Email configuration wrong

# 3. Fix and restart
docker-compose restart web
```

### "UndefinedTable: relation X does not exist"

**This means migrations didn't run:**
```bash
# 1. Check if migrations ran
docker-compose exec web alembic current

# 2. Run migrations manually
docker-compose exec web alembic upgrade head

# 3. Restart app
docker-compose restart web
```

### Database Keeps Crashing

**Check disk space:**
```bash
docker system df

# If > 90% full:
# 1. Backup current data
docker-compose run --rm db-backup \
  bash -c "pg_dump -U hostai -h db hostai | gzip > /tmp/backup.sql.gz"

# 2. Remove old backups
docker-compose run --rm db-backup \
  bash -c "ls -t /backups/*.sql.gz | tail -n +4 | xargs rm -f"

# 3. Clean docker
docker system prune -a -f --volumes
```

---

## Incident Response

### If Production is Down

**1. Immediate (0-5 min)**
```bash
# SSH into server
ssh user@server

# Check status
docker-compose ps
docker-compose logs web

# Try restart
docker-compose restart web
sleep 10
curl https://your-domain.com/ping
```

**2. If restart doesn't work (5-15 min)**
```bash
# Check database
docker-compose logs db --tail 50

# If DB is down:
docker-compose restart db
sleep 20

# If DB won't start, restore from backup:
./scripts/restore-backup.sh --latest
```

**3. If still down (15+ min)**
```bash
# Rollback to previous version
git log --oneline -10
git revert <commit-that-broke-it>
docker-compose down
docker-compose up -d

# OR restore entire environment from backup
./scripts/restore-backup.sh --latest
```

---

## Key Principles to Remember

✅ **Do This:**
- Always test locally before deploying
- Keep daily backups
- Monitor error logs daily
- Update dependencies monthly
- Document any custom configurations

❌ **Never Do This:**
- Skip the pre-deployment checklist
- Deploy without testing
- Share secrets in git
- Modify database schema without migrations
- Ignore error logs
- Forget to set environment variables

---

## Support & Escalation

If production is down:
1. Check this guide's Troubleshooting section
2. Review recent commits: `git log --oneline -5`
3. Check cloud provider status page
4. Restore from backup if needed
5. Contact support if still stuck

**Never:** Force push, delete databases, or modify production without a backup.
