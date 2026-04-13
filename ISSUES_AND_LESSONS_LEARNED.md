# HostAI — Issues Log & Lessons Learned
> **Purpose:** Every bug, crash, 500 error, deployment failure, and design mistake encountered while building this project. Use this as a pre-flight checklist before every release.
>
> **Last updated:** 2026-04-13 | **Total issues documented:** 50+

---

## Table of Contents
1. [Database & Migration Issues](#1-database--migration-issues)
2. [Deployment Crashes Railway / Docker](#2-deployment-crashes-railway--docker)
3. [Backend 500 Errors](#3-backend-500-errors)
4. [SQLAlchemy / ORM Issues](#4-sqlalchemy--orm-issues)
5. [UI / Frontend Issues](#5-ui--frontend-issues)
6. [Voice AI Specific Issues](#6-voice-ai-specific-issues)
7. [Authentication & Security Issues](#7-authentication--security-issues)
8. [Architectural / Design Mistakes](#8-architectural--design-mistakes)
9. [Developer Workflow Issues](#9-developer-workflow-issues)
10. [Prevention Checklist](#10-prevention-checklist)

---

## 1. Database & Migration Issues

### 1.1 — Broken Alembic chain: `KeyError: '20260403_0100'`
- **Symptom:** `alembic upgrade head` crashes on every boot, blocking deployment
- **Root cause:** Migration `20260403_0200` had `down_revision = '20260403_0100'` but that file was **never created**. The migration was written referencing a planned parent that didn't exist.
- **Fix:** Changed `down_revision` to point to the actual previous migration (`20260402_0100`). Updated the merge head to use the corrected parent.
- **Prevention:**
  - After creating any migration, immediately run `alembic heads` locally to verify zero dangling heads
  - Never hardcode a `down_revision` to a future/planned revision ID
  - Run `alembic check` as part of CI before merging

---

### 1.2 — Multiple Alembic heads causing upgrade failure
- **Symptom:** `alembic upgrade head` fails with "Multiple heads" error
- **Root cause:** Features were built in parallel branches, each creating migrations with the same parent. When merged, two heads existed.
- **Fix:** Created merge migration (`20260405_0100_merge_voice_heads.py`) with `down_revision = (head1, head2)`
- **Prevention:**
  - Always check `alembic heads` after merging feature branches
  - Create a merge migration immediately after any branch merge that adds migrations

---

### 1.3 — Wrong table name in migration (`tenant` vs `tenants`)
- **Symptom:** `ProgrammingError: relation "tenant" does not exist` on startup migration
- **Root cause:** Migration used `ALTER TABLE tenant` but the actual table is `tenants` (plural)
- **Fix:** Corrected table name in migration and added idempotency checks
- **Prevention:**
  - Always verify table names against `models.py` (`__tablename__`) before writing raw SQL in migrations
  - Test migrations against a clean DB locally before deploying

---

### 1.4 — Missing ORM model for migration-created table
- **Symptom:** `NameError: name 'AutomatedMessage' is not defined` at runtime; `no such table` on query
- **Root cause:** Migration `20260403_0200` created `automated_messages` and `guest_feedback` tables but the corresponding **SQLAlchemy model classes were never added to `models.py`**. `Base.metadata.create_all()` also didn't know about these tables.
- **Fix:** Added `AutomatedMessage` and `GuestFeedback` ORM classes to `models.py`; added them to `db.py` import block.
- **Prevention:**
  - Rule: every `create_table()` in a migration must have a matching class in `models.py`
  - After writing a migration, search `models.py` for the table name to confirm the model exists

---

### 1.5 — Column added in migration but missing from ORM model
- **Symptom:** `AttributeError: 'TenantConfig' object has no attribute 'digest_enabled'`
- **Root cause:** Migration added `digest_enabled` column but `TenantConfig` model class was never updated.
- **Fix:** Added `digest_enabled: Mapped[bool]` field to `TenantConfig`
- **Prevention:**
  - Every `add_column()` in a migration = a corresponding `mapped_column()` added to the model
  - Run `grep -n "digest_enabled" models.py` before deploying any migration that adds a column

---

### 1.6 — Raw SQL in migration: broke on PostgreSQL
- **Symptom:** Migration worked on SQLite (dev) but crashed on PostgreSQL (prod)
- **Root cause:** Migration used `conn.execute(text("ALTER TABLE ..."))` raw SQL. PostgreSQL syntax for boolean defaults (`TRUE` vs `1`) differs from SQLite.
- **Fix:** Replaced raw SQL with proper Alembic `op.add_column()`, `op.create_table()` DDL operations
- **Prevention:**
  - Never use raw SQL in Alembic migrations — always use `op.*` DDL functions
  - If raw SQL is unavoidable, branch on `dialect = op.get_bind().dialect.name`

---

### 1.7 — Non-idempotent migrations crash on re-run
- **Symptom:** `DuplicateTable` or `DuplicateColumn` error when migration is re-applied
- **Root cause:** Migrations used bare `op.create_table()` without checking if table already exists
- **Fix:** Added `if 'table_name' not in existing_tables:` guards in all migrations
- **Prevention:**
  - All `create_table` calls must be wrapped in `if table not in existing_tables:`
  - All `add_column` calls must check `if column not in existing_columns:`

---

### 1.8 — `db_migrate()` not covering new columns from late migrations
- **Symptom:** Columns like `voice_forward_enabled`, `expertise_areas` missing on live DB
- **Root cause:** `db.py`'s `ensure_columns` list was not updated when new migrations were added
- **Fix:** Added all new columns to the `new_columns` list in `db_migrate()`
- **Prevention:**
  - Every new migration that adds a column should also add a corresponding entry to `db_migrate()` as a fallback

---

### 1.9 — Postgres boolean default error (`1` vs `TRUE`)
- **Symptom:** `psycopg2.errors.InvalidTextRepresentation: invalid input syntax for type boolean: "1"`
- **Root cause:** SQLite uses `0/1` for booleans; PostgreSQL requires `TRUE/FALSE`. Code used `server_default="1"`.
- **Fix:** Conditional default: `"0" if _is_sqlite else "FALSE"`
- **Prevention:**
  - Use `sa.true()` / `sa.false()` for boolean server_defaults in migrations
  - Never use `"0"/"1"` for boolean defaults in Alembic migrations

---

### 1.10 — Timezone-naive `datetime.min` crash
- **Symptom:** `TypeError: can't compare offset-naive and offset-aware datetimes`
- **Root cause:** Code used `datetime.min` (timezone-naive) when sorting against `DateTime(timezone=True)` columns
- **Fix:** Used `datetime.min.replace(tzinfo=timezone.utc)`
- **Prevention:**
  - Always use `datetime.now(timezone.utc)` and `datetime.min.replace(tzinfo=timezone.utc)`
  - All `DateTime` columns in models should have `timezone=True`

---

### 1.11 — Model columns not in database (out-of-sync schema)
- **Symptom:** `psycopg2.errors.UndefinedColumn: column system_config.primary_backup_model does not exist` on page load
- **Root cause:** Columns were added to SQLAlchemy models (`SystemConfig`, `TenantConfig`) but database migrations to create those columns were never run or were pending
- **Specific case:** Voice AI feature added 9 new columns to `TenantConfig` and 2 to `SystemConfig` but Railway database hadn't applied the migrations yet, causing 500 errors on every page trying to query those columns
- **Fix:** 
  - Created two Alembic migrations:
    - `20260413_0000_voice_ai_tenant_config.py` — adds 9 voice AI columns to `tenant_configs`
    - `20260413_0100_add_system_config_backup_models.py` — adds 2 backup model columns to `system_config`
  - Made all new columns `nullable=True` in models to prevent errors when migrations haven't run yet
  - Migrations run automatically on next deploy
- **Prevention:**
  - **Critical rule:** Never add a column to a SQLAlchemy model without immediately creating an Alembic migration for it
  - Before deploying to production, verify: `alembic history | tail -20` includes all recent migrations
  - Make new columns `nullable=True` to gracefully handle pre-migration state
  - Add this check to pre-commit: `grep -n "mapped_column" models.py` then `grep -n "add_column" versions/*.py` — they should match

---

## 2. Deployment Crashes Railway / Docker

### 2.1 — `python` vs `python3` command not found
- **Symptom:** Container exits immediately with `python: command not found`
- **Root cause:** Dockerfile/Procfile used `python` but the Docker image only had `python3` in PATH
- **Fix:** Changed all `python` references to `python3`
- **Prevention:** Always use `python3` explicitly in Dockerfiles and shell scripts

---

### 2.2 — Wrong Dockerfile path in `railway.toml`
- **Symptom:** Railway couldn't find Dockerfile; fell back to Nixpacks and built incorrectly
- **Root cause:** `railway.toml` had `dockerfilePath = "Dockerfile"` but file was at `web/Dockerfile`
- **Fix:** Added a root-level `Dockerfile` that forwards to the correct build context
- **Prevention:** Always confirm `dockerfilePath` in `railway.toml` matches actual file location before first deploy

---

### 2.3 — Port mismatch (`$PORT` not read from environment)
- **Symptom:** App starts but Railway health check fails; service shows as crashed
- **Root cause:** App bound to hardcoded port `8000` instead of `$PORT` env var
- **Fix:** Changed to `${PORT:-8000}` in Dockerfile CMD
- **Prevention:**
  - Always use `os.environ.get("PORT", 8080)` or `${PORT:-8080}` in entrypoints for PaaS deployments
  - Never hardcode a port number in production

---

### 2.4 — Migrations ran before database was ready
- **Symptom:** `alembic upgrade head` fails with `connection refused` — DB not up yet
- **Root cause:** `entrypoint.sh` ran Alembic immediately without waiting for DB readiness
- **Fix:** Added a DB wait loop with retry logic before running Alembic
- **Prevention:** Always include a `wait-for-db` step in `entrypoint.sh` before running migrations

---

### 2.5 — `AUTO_CREATE_TABLES` / `AUTO_MIGRATE` disabled in production
- **Symptom:** New tables and columns weren't created on first deploy; 500 errors on every page
- **Root cause:** `db.py` gated schema creation behind env flags that defaulted to `False` in production
- **Fix:** Added explicit env vars to Railway; added safe table-creation fallback in `db_migrate()`
- **Prevention:** Document all required env vars in `README.md` and `.env.example`

---

### 2.6 — `bot.js` not found in Docker build context
- **Symptom:** Docker build fails: `COPY airbnb-host/bot.js: no such file`
- **Root cause:** Dockerfile tried to copy from a sibling directory outside the build context
- **Fix:** Moved `bot.js` into `web/` and updated `COPY` path
- **Prevention:** All files referenced in `COPY` must be within the Docker build context directory

---

## 3. Backend 500 Errors

### 3.1 — `NameError: name 'PLAN_BAILEYS' is not defined`
- **Symptom:** Settings page and Voice Calls page threw 500 on every load
- **Root cause:** `PLAN_BAILEYS` constant was removed from `models.py` but code still referenced it
- **Fix:** Added `PLAN_BAILEYS = "baileys"` as a backward-compat stub
- **Prevention:**
  - Use `grep -rn "PLAN_BAILEYS"` before removing any constant
  - Mark deprecated constants with a comment rather than deleting immediately

---

### 3.2 — Admin pages 500: `admin` variable not passed to template
- **Symptom:** All admin panel pages threw `jinja2.exceptions.UndefinedError: 'admin' is undefined`
- **Root cause:** Routes returned data without including the `admin` user object in template context
- **Fix:** Added `admin=admin` to all `render_template()` calls in admin routes
- **Prevention:** Create a shared `admin_context(admin, **kwargs)` helper used by all admin routes

---

### 3.3 — `NameError: name 'VoiceKnowledgeGap' is not defined`
- **Symptom:** Voice calls route throws 500 on load
- **Root cause:** Added VoiceKnowledgeGap to a route but forgot to import it
- **Fix:** Added `VoiceKnowledgeGap` to the import block
- **Prevention:**
  - Always import new models explicitly; avoid `import *`
  - Run `pyflakes` or `ruff` before commit

---

### 3.4 — Jinja2 divide-by-zero on empty data
- **Symptom:** Analytics / Voice Calls page crashes when tenant has no data
- **Root cause:** Template used `{{ a / b }}` without guarding against `b == 0`
- **Fix:** Changed to `{{ (a / b) if b else 0 }}`
- **Prevention:** Never divide in Jinja2 without a zero-guard: `{{ (a / b) if b else 0 }}`

---

### 3.5 — `AttributeError: 'Tenant' object has no attribute 'name'`
- **Symptom:** Multiple templates throw 500
- **Root cause:** `Tenant` model uses `first_name` + `last_name`; there is no `name` field
- **Fix:** Replaced all `tenant.name` with `tenant.first_name` in templates and routes
- **Prevention:** Add `@property def name(self): return f"{self.first_name} {self.last_name}"` to Tenant model

---

### 3.6 — `current_user` undefined in templates
- **Symptom:** Templates threw `UndefinedError: 'current_user' is undefined`
- **Root cause:** Templates assumed Flask-Login's `current_user` global, but app uses custom `tenant` variable
- **Fix:** Replaced all `current_user` with `tenant` in templates and routes
- **Prevention:** Document which auth pattern the app uses and enforce with a grep check

---

### 3.7 — `ImportError: cannot import name 'logger' from 'web.logger'`
- **Symptom:** App fails to start with ImportError
- **Root cause:** Custom `web.logger` module referenced but didn't exist
- **Fix:** Replaced with `import logging; log = logging.getLogger(__name__)`
- **Prevention:** Don't create custom logger wrappers; use stdlib `logging` consistently

---

### 3.8 — PMS scheduler crashes on missing config
- **Symptom:** Background worker crashes with `AttributeError`; stops processing all tenants
- **Root cause:** Scheduler assumed every tenant had a PMS integration; crashed on `None`
- **Fix:** Added `if integration is None: continue` guards; wrapped per-tenant loops in try/except
- **Prevention:** All background workers must handle `None` gracefully for every optional tenant config

---

## 4. SQLAlchemy / ORM Issues

### 4.1 — Duplicate model class definition
- **Symptom:** `sqlalchemy.exc.InvalidRequestError: Table 'feature_flags' is already defined`
- **Root cause:** `FeatureFlag` model class was defined twice in `models.py`
- **Fix:** Removed the duplicate definition
- **Prevention:** Keep models alphabetically ordered; run `grep -n "class FeatureFlag" models.py` before adding

---

### 4.2 — `APIUsageLog` vs `ApiUsageLog` class name inconsistency
- **Symptom:** `NameError: name 'ApiUsageLog' is not defined` in one file
- **Root cause:** Class was named `ApiUsageLog` in one migration but `APIUsageLog` in `models.py`
- **Fix:** Standardized to `APIUsageLog` everywhere
- **Prevention:** Follow one naming convention (prefer all-caps acronyms: `APIUsageLog`, `PMSIntegration`)

---

### 4.3 — Missing `back_populates` on relationship
- **Symptom:** SQLAlchemy warning and potential query crashes on `RoutingRule.tenant`
- **Root cause:** `Tenant.routing_rules` declared `back_populates="tenant"` but `RoutingRule.tenant` didn't have matching `back_populates`
- **Fix:** Added `back_populates="routing_rules"` to `RoutingRule.tenant`
- **Prevention:** Every bidirectional relationship must have matching `back_populates` on both sides

---

### 4.4 — `Integer` vs `String` FK type mismatch
- **Symptom:** `sqlalchemy.exc.NoForeignKeysError` when joining `EscalationRule` to `TeamMember`
- **Root cause:** `assign_to_team_member` FK was declared as `String` but `TeamMember.id` is `Integer`
- **Fix:** Changed FK column type to `Integer`
- **Prevention:** Always check the PK type of the referenced table before declaring a FK column

---

### 4.5 — Defining columns in model without corresponding migration
- **Symptom:** `psycopg2.errors.UndefinedColumn: column X does not exist` on every page load
- **Root cause:** Model class has column definition (e.g., `voice_llm_model: Mapped[str]`) but database doesn't have the column yet (migration is pending or not created)
- **Example:** Added 11 voice AI columns to models but migrations only created after 500 errors on Railway
- **Fix:** 
  - Make all new columns `nullable=True` and `nullable=True` in their mapped_column definition
  - Ensure migration file exists for each new column
  - Verify migrations run before code that uses those columns
- **Prevention:**
  - **Golden rule:** For every `mapped_column(...)` added to a model, create a corresponding `op.add_column(...)` or `op.create_table(...)` in a migration **before** committing
  - Run migrations locally before committing: `alembic upgrade head`
  - Test that the app still starts if you temporarily comment out the new columns from models

---

## 5. UI / Frontend Issues

### 5.1 — White-flash bug on dark mode pages
- **Symptom:** KPI cards and chart backgrounds flash white on page load
- **Root cause:** Legacy CSS used `var(--surface, #fff)` with a light-mode fallback. Fallback `#fff` rendered briefly before CSS variable resolved.
- **Fix:** Replaced all `var(--surface, #fff)` with explicit Obsidian dark tokens
- **Prevention:** Never use light-mode fallbacks in CSS variables for a dark-mode-first design system

---

### 5.2 — Sidebar displacement: content hidden on mobile
- **Symptom:** On mobile, main content rendered under the sidebar
- **Root cause:** `<main>` had `ml-[260px]` without `md:` prefix — always applied the margin
- **Fix:** Changed `ml-[260px]` to `md:ml-[260px]` and added mobile hamburger toggle
- **Prevention:** Every layout class referencing sidebar width must have `md:` breakpoint prefix

---

### 5.3 — Missing mobile hamburger menu on 20+ pages
- **Symptom:** Sidebar inaccessible on mobile — no way to open it
- **Root cause:** Pages built desktop-first; mobile toggle added inconsistently
- **Fix:** Added hamburger button + dark overlay to all 30+ templates with a sidebar
- **Prevention:** Create `base_authenticated.html` with sidebar + hamburger once; all pages extend it

---

### 5.4 — Duplicate CDN `<link>` tags
- **Symptom:** Material Symbols font loaded twice; duplicate network requests
- **Root cause:** Template `<head>` sections assembled by copy-paste; CDN links duplicated
- **Fix:** Removed duplicate CDN links from 6 templates
- **Prevention:** Use a base template for `<head>` — never copy-paste CDN links between templates

---

### 5.5 — Inconsistent top padding (content hidden behind fixed header)
- **Symptom:** First lines of content hidden behind fixed header bar
- **Root cause:** Different pages used `pt-8`, `pt-16`, `pt-24`, `pt-[100px]` inconsistently
- **Fix:** Standardized to `pt-[80px]` (64px header + 16px gap) on all pages
- **Prevention:** Document the standard: `pt-[80px]` is always the main content top padding

---

### 5.6 — Admin sidebar gap: `md:relative` pushing content down
- **Symptom:** Huge gap at top of every admin page
- **Root cause:** `<aside>` had `md:relative` which broke fixed-position sidebar layout
- **Fix:** Removed `md:relative` from all admin sidebar `<aside>` elements
- **Prevention:** Fixed-position sidebars must never have `relative` or `static` positioning classes

---

### 5.7 — Non-responsive header bar on mobile
- **Symptom:** Header bar extended off-screen on mobile
- **Root cause:** Header used `w-[calc(100%-260px)]` without `md:` prefix
- **Fix:** Changed to `w-full md:w-[calc(100%-260px)]`
- **Prevention:** Any class that references a sidebar dimension must be prefixed with `md:`

---

## 6. Voice AI Specific Issues

### 6.1 — Voice columns missing from `tenants` table
- **Symptom:** `500: column "voice_enabled" does not exist`
- **Root cause:** Voice AI added columns to `tenant_configs` but missed `tenants` table
- **Fix:** Created new migration for missing voice columns on `tenants` table
- **Prevention:** When a feature spans multiple tables, list ALL tables needing changes before writing any migration

---

### 6.2 — Missing `voice_forward_enabled` / `voice_forward_number` columns
- **Symptom:** Settings page 500 when host tries to configure call forwarding
- **Root cause:** Voice forwarding feature added to `Tenant` model but migration only updated `tenant_configs`
- **Fix:** Added columns to both migration and `db_migrate()` ensure_columns list
- **Prevention:** `Tenant` and `TenantConfig` are separate tables — changes to one don't auto-apply to the other

---

### 6.3 — Voice analytics page crash on empty dataset
- **Symptom:** `ZeroDivisionError` on voice analytics for new tenants
- **Root cause:** Voice analytics computed averages without checking for empty dataset
- **Fix:** Added fail-soft guards: `if calls else []`, `len(calls) or 1`
- **Prevention:** Every analytics aggregation must handle the empty-dataset case explicitly

---

## 7. Authentication & Security Issues

### 7.1 — Team member login form hidden entirely
- **Symptom:** When `show_team_tab = False`, entire login page was hidden
- **Root cause:** Jinja2 template logic applied hide class to outer container instead of just the tab
- **Fix:** Fixed conditional to only hide the team tab button
- **Prevention:** Test login page with both `show_team_tab=True` and `show_team_tab=False`

---

### 7.2 — CSRF not enforced on all POST routes
- **Symptom:** API endpoints accepted POST requests without CSRF tokens
- **Root cause:** Some newer routes added without CSRF protection
- **Fix:** Enforced CSRF token validation on all state-changing routes
- **Prevention:** Use a middleware or decorator that auto-enforces CSRF on all POST/PUT/PATCH/DELETE routes

---

### 7.3 — API keys stored in plaintext
- **Symptom:** Security audit flagged API keys stored unencrypted in DB
- **Root cause:** `openrouter_api_key` was stored as plain varchar without encryption
- **Fix:** Added `_enc` suffix convention; keys stored AES-encrypted
- **Prevention:** Any column ending in `_key`, `_token`, `_secret`, or `_password` must always be encrypted

---

## 8. Architectural / Design Mistakes

### 8.1 — No base template: 30+ copies of sidebar HTML
- **Symptom:** Fixing a sidebar bug required editing 30+ files; extremely error-prone
- **Root cause:** All templates copy-pasted the sidebar HTML inline rather than using Jinja2 `extends`
- **Recommended fix:** Create `templates/base_user.html` and `templates/base_admin.html` with sidebar/header included once
- **Prevention:** This is the single biggest architectural debt. Create base templates before adding more pages.

---

### 8.2 — Tenant-level vs Property-level settings confusion
- **Symptom:** Voice settings saved to `tenant_configs`; inconsistent behavior with `property_configs`
- **Root cause:** When Property model was added late, it was unclear which settings live at tenant vs property level
- **Prevention:** Establish clear rules: tenant = billing/global/account; property = voice number/amenities/rules

---

### 8.3 — Feature deprecated mid-project without cleanup (Baileys)
- **Symptom:** `PLAN_BAILEYS` removed in one commit but referenced in 10+ places
- **Root cause:** Integration deprecated but code deleted rather than gradually phased out
- **Prevention:** Never hard-delete a plan/feature in one commit — use a deprecation flag first, then remove after a grace period

---

## 9. Developer Workflow Issues

### 9.1 — Migration committed without its referenced parent
- **Symptom:** `20260403_0200` committed with `down_revision = '20260403_0100'` but parent never committed
- **Root cause:** Developer planned to create `0100` first but committed `0200` without realizing the parent was absent
- **Prevention:**
  - Before committing any migration, run `alembic heads` and verify chain is valid
  - Add a pre-commit hook that validates the Alembic revision chain

---

### 9.2 — SQLite dev vs PostgreSQL prod differences not caught early
- **Symptom:** Bugs only appeared in production (Postgres) but not in dev (SQLite)
  - Boolean `1` vs `TRUE`
  - FK enforcement (SQLite doesn't enforce FKs by default)
- **Prevention:**
  - Run local PostgreSQL for development via Docker: `docker run -e POSTGRES_PASSWORD=dev -p 5432:5432 postgres`
  - Set `PRAGMA foreign_keys = ON` in SQLite dev mode

---

### 9.3 — No route-level smoke tests
- **Symptom:** Pages could be broken silently — no automatic check that each route returns 200
- **Fix:** Added authenticated page smoke test script
- **Prevention:** Add `test_routes.py` that logs in and hits every `/dashboard`, `/settings`, `/admin/*` and asserts 200

---

---

## 10. Prevention Checklist

> Run through this before every deployment.

### Before writing a migration
- [ ] Run `alembic heads` — should show exactly 1 head
- [ ] Verify `__tablename__` in `models.py` matches the table name in your migration
- [ ] Wrap all `create_table` in `if table not in existing_tables:` guards
- [ ] Wrap all `add_column` in `if column not in existing_columns:` guards
- [ ] Use `op.*` DDL functions, not raw SQL
- [ ] Add new columns to `db_migrate()` ensure_columns list in `db.py`
- [ ] Add new model classes to `init_db()` import block in `db.py`
- [ ] **If adding model columns:** Make them `nullable=True` in the model to handle pre-migration state
- [ ] Create migration file BEFORE or IMMEDIATELY AFTER committing model changes (never leave model columns without migrations)

### Before committing code
- [ ] `grep -rn "current_user"` — should return 0 (all replaced with `tenant`)
- [ ] `python3 -c "from web.models import *"` — should import cleanly with no errors
- [ ] All new template variables are passed in the route's `render_template()` call
- [ ] All new POST routes have CSRF protection

### Before deploying
- [ ] `alembic heads` shows exactly 1 head
- [ ] All migration `down_revision` values point to actually-existing revision files
- [ ] All new `mapped_column(...)` in models have corresponding `op.add_column(...)` in a migration
- [ ] All new columns in migrations are `nullable=True` (to handle timing of migrations vs. code deploy)
- [ ] Environment variables set: `DATABASE_URL`, `PORT`, `AUTO_MIGRATE`, `ENVIRONMENT`
- [ ] Test login page with brand-new tenant (empty data — no voice calls, no reservations)
- [ ] Test admin pages that load system_config or tenant_configs (make sure no 500 errors on undefined columns)
- [ ] Jinja2 divisions all use `{{ (a / b) if b else 0 }}` pattern

### Mobile UI checklist per new page
- [ ] `<aside>` has `-translate-x-full md:translate-x-0 transition-transform duration-300`
- [ ] `<aside>` has `id="sidebar"` (user) or `id="admin-sidebar"` (admin)
- [ ] Hamburger button exists: `class="... md:hidden"`
- [ ] `<main>` has `md:ml-[260px]` (not just `ml-[260px]`)
- [ ] `<header>` has `w-full md:w-[calc(100%-260px)]`
- [ ] Main content has `pt-[80px]` top padding
- [ ] Test at 375px width before merging

---

*Update this document every time a new issue is discovered and fixed.*
