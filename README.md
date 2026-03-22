# HostAI

Fast, operator-grade guest messaging and workflow automation for short-term rental hosts.

## Overview
HostAI centralizes guest communication, reservations, and ops workflows so hosts can respond quickly and consistently without losing context.

## Core Features
- Multi-channel messaging: email, WhatsApp (Meta Cloud or Baileys), SMS (Twilio)
- CSV reservation intake (no PMS required)
- Optional PMS sync integrations
- AI-generated draft replies with approval, edit, schedule, and auto-send
- Workflow center with guest timeline, ops queue, vendor routing, and issue tracking
- Onboarding wizard with property knowledge capture
- Audit trail, metrics, and admin tools

## How It Works
- Inbound guest messages are normalized into drafts.
- Context includes property settings and house rules.
- Context includes reservation details and guest identifiers.
- Context includes past conversation timeline.
- The host reviews, edits, schedules, or auto-sends drafts.
- Ops workflows route maintenance or vendor tasks when needed.

## Architecture
- `web`: FastAPI app serving UI + API
- `worker`: background process for IMAP polling, scheduling, and PMS sync
- `postgres`: primary data store
- `redis`: rate limiting, queue, worker coordination
- `nginx`: TLS termination and reverse proxy (production)
- optional: PgBouncer, read replica, certbot, daily DB backups

## Local Development
```bash
cd /Users/chandan/Desktop/BNB/JestinRajan249
docker compose -f docker-compose.dev.yml up -d --build
```
Open:
- http://localhost:8000

Default dev admin email:
- `admin@hostai.local`

## Production Deploy
```bash
cd /Users/chandan/Desktop/BNB/JestinRajan249
cp .env.example .env
# edit .env with real secrets
./deploy.sh
```

Key notes:
- Production runs Alembic migrations during deploy.
- Web and background workers are separate services.

## CSV Reservation Workflow (Kept Intact)
CSV intake is a first-class path for hosts without a PMS.

Typical flow:
1. Host exports CSV from Airbnb or manual system.
2. Upload in the Reservations page.
3. Each reservation becomes a guest context entity.
4. Inbound messages are matched by name/phone/unit and routed accordingly.

## Inbound Email Options
Two supported modes:
- Forwarding / hosted inbound parse webhook. Provider receives email and POSTs structured payload to `/email/inbound`. Recommended for simpler operations.
- IMAP polling. Connect mailbox directly; worker polls IMAP. More setup but no provider changes needed.

## Security Highlights
- CSRF protection on all state-changing browser routes
- Webhook signature validation for Meta/Twilio
- Inbound email webhook support for Mailgun/Postmark signatures
- Metrics endpoints protected by token in production
- Secure cookies only when HTTPS is trusted

## Migrations
```bash
alembic upgrade head
```

Use `DATABASE_DIRECT_URL` in production to bypass PgBouncer for migrations.

## Tests
```bash
pytest -q
```

## Key Environment Variables
See `.env.example` for the full list. Highlights:
- `SECRET_KEY`, `FIELD_ENCRYPTION_KEY`
- `DATABASE_URL`, `DATABASE_DIRECT_URL`
- `REDIS_URL`
- `ADMIN_EMAILS`
- `METRICS_TOKEN`
- `INBOUND_PARSE_WEBHOOK_SECRET`

## Notes
- Keep `RUN_EMBEDDED_WORKERS=false` in production; use the worker service.
- `AUTO_CREATE_TABLES` and `AUTO_MIGRATE` should be false in production.
