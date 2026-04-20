# HostAI Backend — Project Context

## Quick Facts

- **Project**: HostAI — AI guest messaging + operations management for short-term rentals
- **Stack**: FastAPI + Jinja2 templates, PostgreSQL, Redis, Tailwind/dark obsidian theme
- **Deployment**: Railway auto-deploys on `git push origin main` to `https://github.com/Power7T/JestinRajan249`
- **Prod URL**: `https://meticulous-vibrancy-production.up.railway.app/`
- **Key Accounts**:
  - Admin: `chandango12@gmail.com` / `Supergo12@`
  - Host test: `chandango12k@gmail.com` / `Supergo12@@@`

## Architecture Essentials

### Auth & Sessions
- Cookie-based sessions with JWT tokens
- CSRF tokens on every form (`request.state.csrf_token`)
- Admin check: `is_admin()` reads `ADMIN_EMAILS` env var (currently both accounts)
- Login rate limiting: 10 attempts per 15 min per IP

### Database
- 36+ tables (models in `web/models.py`)
- Alembic migrations in `migrations/versions/`
- SQLAlchemy ORM with proper relationships
- Common models: `Tenant`, `TenantConfig`, `AIResponse`, `Reservation`, `Vendor`, `TeamMember`, `AutomationRule`, `PMSIntegration`

### Templates
- **Location**: `web/templates/*.html`
- **Pre-commit check**: `scripts/check_templates.py` runs before every commit (checks syntax, no hardcoded secrets, etc.)
- **Rendering**: All extend `base.html` or `workspace_shell.html`
- **Dark theme**: Inline Tailwind via CDN, Material Icons via Google Fonts
- **Total**: 61 templates, all responsive for mobile

### Key Routes
- Host dashboard: `/dashboard` (shows AI drafts, activity, setup alerts)
- New workspace pages: `/properties`, `/communications`, `/automations`, `/integrations`, `/team`, `/settings`
- Admin: `/admin*` (overview, tenants, AI engine, voice routing, pricing)
- Onboarding: `/onboarding` — **CRITICAL**: 2-step flow (step 1 = property name, step 5 = iCal URL). Flag: `_ONBOARDING_STEPS = 5` in `app.py`

### Error Handling
- **404/403/429/500**: Handled by exception handlers in `app.py`; templates get `is_authed`, `cta_url`, `cta_label` context for smart buttons
- **Rate limiter**: `web/rate_limiter.py` — keyed on IP, stores in Redis
- **Audit logs**: `ActivityLog` model logs sensitive actions

## Common Patterns

### Tenant Context
Most endpoints follow this pattern:
```python
tenant_id = get_current_tenant_id(request)  # raises 401 if not authenticated
cfg = _get_or_create_config(tenant_id, db)  # TenantConfig
# cfg has: onboarding_step, onboarding_complete, subscription_plan, wa_mode, sms_mode, etc.
```

### Workspace Pages
All follow `workspace_shell.html` with sidebar + top nav. Context usually includes:
```python
_workspace_context(request, db, tenant_id, page_key="...", saved_message="...")
# Returns: request, tenant, cfg, vendors, pms_integrations, automation_rules, team_members, plan_info, activation_checklist, etc.
```

### Setup Alerts
`_get_setup_alerts(cfg, tenant, reservations)` returns list of contextual CTA cards on dashboard — links point to relevant workspace pages now (not `/settings` tabs).

## Gotchas

1. **Onboarding logic**: Step 1 jumps to step 5 (line 3314 in app.py). Check `onboarding_step` DB value before modifying.
2. **Template variables**: All rendered vars are escaped by default. Use `|safe` only for HTML you control.
3. **CSRF required**: Every `<form method="post">` must include `<input type="hidden" name="csrf_token" value="{{ request.state.csrf_token }}">`
4. **Rate limiter waits**: Login/signup have strict limits — use `railway logs` to check, don't hammer the endpoint in tight loops.
5. **Admin check at request time**: `is_admin()` is a lambda checking session email against `ADMIN_EMAILS` — happens per-request, env var is read at startup only.
6. **Railway env vars**: Changes trigger auto-redeploy. Useful vars: `ADMIN_EMAILS`, `DATABASE_URL`, `REDIS_URL`, `APP_BASE_URL`.

## Workflow

1. **Edit code** → files in `web/` (templates, app.py, models.py, etc.)
2. **Pre-commit runs automatically** — checks syntax, imports, migrations, templates, security
3. **Commit with message** — include what changed and why (e.g., "fix: Smart 404 error pages")
4. **`git push origin main`** — Railway picks it up and deploys (2–3 min)
5. **Verify** — check Railway logs or hit `/health` endpoint

## User Preferences

- **Always `git push origin main` immediately after every commit** — no staged-but-unpushed changes
- Use Haiku or Sonnet for routine audits/fixes; Opus only for complex architecture/debugging
- Memory system (`~/.claude/projects/*/memory/MEMORY.md`) tracks decisions and gotchas
- Prefer explicit over clever — name variables clearly, skip comments for obvious code

## Quick Links

- **GitHub**: https://github.com/Power7T/JestinRajan249
- **Railway Project**: meticulous-vibrancy (production)
- **Main app file**: `web/app.py` (~10K+ lines, organized by function)
- **Models**: `web/models.py`
- **Templates**: `web/templates/` (61 files)
