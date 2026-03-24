# HostAI — Comprehensive Product Audit
> Conducted 2026-03-24 | Covers 20 areas + security re-assessment + host gap analysis

---

## Area 1: Onboarding Wizard

**Files**: `onboarding.html`, `app.py:1676-1993`

**Flow**: 5-step wizard — (1) Property details, (2) House rules/FAQ, (3) Email setup, (4) Reservation CSV upload, (5) Calendar/iCal + demo.

| What works | What's missing / broken |
|---|---|
| Progress bar with step dots + lines | No back navigation — user can only skip forward |
| Skippable steps ("you can always update later") | No data validation on step transitions (empty forms silently proceed) |
| IMAP connection test inline (`/test/imap`) | No progress save on browser refresh — form data lost |
| iCal test endpoint (`/test/ical`) | No mobile optimization for wizard (fixed widths, small tap targets) |
| AI demo at step 5 (`/onboarding/demo`) | Step 4 (CSV upload) has no sample file download |
| Quick-start listing import from URL (`/onboarding/import-listing`) | No timeout/spinner on listing import — user waits on blank |
| Welcome email sent on completion | `onboarding_step` tracks progress but wizard re-renders full page |
| `dismiss-tour` endpoint to skip | No error recovery if IMAP test fails — just red text, no suggestions |

**Verdict**: Functional but fragile. No server-side form persistence between steps. A browser crash at step 4 loses all prior input.

---

## Area 2: Guest Messaging / Chat

**Files**: `app.py:2785-2890`, `classifier.py`, `email_worker.py`, `meta_sender.py`, `sms_sender.py`

**Flow**: Inbound message → `_handle_guest_inbound_message()` → classify → generate draft → save → host review → approve/edit/skip/schedule → send via channel.

| What works | What's missing |
|---|---|
| Multi-channel inbound (email, WhatsApp Meta, WhatsApp Baileys, SMS, PMS) | No real-time chat UI — host sees drafts on dashboard, not a conversation view |
| Thread tracking via `thread_key` + `parent_draft_id` | No typing indicators or read receipts |
| Conversation memory from timeline events injected into AI context | No guest-side chat widget (guests use their own channels) |
| Reservation context auto-linked by phone number match | No message search/filter across conversations |
| Guest sentiment analysis (LLM + regex) | No canned/quick replies library |
| Policy conflict detection in drafts | No media/image handling (WhatsApp images silently dropped) |
| Multilingual auto-detection and reply | No guest language preference persistence |
| Escalation detection (legal threats, emergencies) | Escalation email can silently fail (logged but not retried beyond 3 attempts) |

**Verdict**: Core flow is solid. Missing a conversation-centric UI — hosts manage drafts, not conversations.

---

## Area 3: Host Dashboard & UI

**Files**: `dashboard.html`, `app.py:1011-1255`

**Flow**: `/dashboard` shows pending drafts, KPIs, activation checklist, ops queue.

| What works | What's missing |
|---|---|
| HTMX polling for new drafts (`/api/drafts` every 15s) | No WebSocket/SSE — polling only, 15s latency |
| KPI cards (drafts total, approval rate, occupancy gaps, active stays) | No date range picker for KPIs |
| Activation checklist from `build_activation_checklist()` | No per-property dashboard view (all properties mixed) |
| Approve/Edit/Skip/Schedule actions per draft | No bulk approve with confirmation |
| Draft confidence score displayed | No dark mode toggle (base.html has CSS vars but no user toggle) |
| Guest sentiment badge on drafts | Dashboard loads ALL drafts for KPIs — no pagination on analytics |
| Approval streak counter | No notification sound/browser notification for new drafts |
| Exception queue (stale pending, missing phone, failed sends) | Mobile responsive but cramped on small screens |

**Verdict**: Good data density. Needs conversation-centric view and real-time updates.

---

## Area 4: Messaging Channels

### Email (IMAP + Forwarding)
- **IMAP polling**: `email_worker.py` polls every 2 min, parses emails, creates drafts
- **Forwarding mode**: `/email/inbound` webhook accepts Mailgun/SendGrid/Postmark payloads
- **Send**: SMTP via tenant's own credentials
- **Gap**: No OAuth support (app passwords only), no attachment handling

### WhatsApp — Meta Cloud API
- **Inbound**: `/wa/webhook/{tenant_id}` with signature verification (`X-Hub-Signature-256`)
- **Outbound**: `meta_sender.py` sends via Graph API v18.0
- **Gap**: Only text messages supported (no templates, no media, no buttons)

### WhatsApp — Baileys (Local Bot)
- **Inbound**: Bot on host's PC pushes via `/api/wa/inbound`
- **Outbound**: Bot polls `/api/wa/pending` for messages
- **Auth**: Bot API token (SHA256 hashed, `hmac.compare_digest`)
- **Gap**: Requires host's PC always on, no fallback if bot is offline

### SMS — Twilio
- **Inbound**: `/sms/webhook/{tenant_id}` parses TwiML form data
- **Outbound**: `sms_sender.py` via Twilio REST API
- **Gap**: No Twilio request signature validation on inbound webhook

**Cross-channel gaps**:
- No channel routing preference per guest (if guest has both WA and SMS, which to use?)
- No unified outbox view across channels
- No delivery status tracking for Meta WA or SMS sends

---

## Area 5: Plans & Billing

**Files**: `billing.py`, `billing.html`, `pricing.html`, `app.py:2510-2636`

I dont want plans to be social channel based as below user can choose any chhannel they seem right. Plan in based on no. of flat/rooms/units per host.
| Plan | Price | Channels |
|---|---|---|
| Free | $0/mo | Dashboard + email drafts + iCal |
| Baileys | $19/mo | + WhatsApp via host's PC |
| Meta Cloud | $29/mo | + WhatsApp Cloud API |
| SMS | $19/mo | + Twilio SMS |
| Pro | $49/mo | All channels |

| What works | What's missing |
|---|---|
| Stripe Checkout + Customer Portal | No free trial period |
| Webhook idempotency via Redis `SET NX` | No usage-based billing (flat fee regardless of volume) |
| Plan enforcement via `require_channel()` | No annual billing option |
| Webhook handles: created, updated, deleted, paused, payment_failed | No invoice/receipt page in-app |
| Admin can manually change tenant plan | No proration display when switching plans |
| `past_due` status tracked | No grace period logic — immediate lockout on failure |

**Verdict**: Clean Stripe integration. Missing trial, annual billing, and in-app receipts.

---

## Area 6: Admin Panel

**Files**: `admin_overview.html`, `admin_tenant.html`, `admin_ai.html`, `admin_api.html`, `admin_costs.html`, `admin_system.html`, `app.py:5278-5780`

**Routes**: `/admin`, `/admin/tenants/{tid}`, `/admin/ai`, `/admin/costs`, `/admin/system`, `/admin/health_api`

| What works | What's missing |
|---|---|
| Tenant list with plan, status, worker health | No admin audit log (who changed what) |
| Onboarding funnel visualization | No admin 2FA |
| Draft quality stats (30-day approval/skip/edit rates) | No bulk operations (mass email, mass plan change) |
| Churn signal detection (14-day inactive, dead workers) | No admin search/filter for tenants |
| MRR calculation + plan breakdown | No export of admin reports |
| Impersonation (`/admin/tenants/{tid}/impersonate`) | Impersonation has no audit trail |
| AI model config (primary, fallback, sentiment models) | No rate limit on admin actions |
| API usage cost tracking per tenant | No admin notification for new signups or churn |
| System health (DB, Redis, workers, threads) | No admin role system (single `ADMIN_EMAILS` allowlist) |

**Verdict**: Comprehensive for a single-admin SaaS. Needs audit logging and search.

---

## Area 7: Reservations

**Files**: `reservations.html`, `guest_timeline.html`, `app.py:3736-4375`

| What works | What's missing |
|---|---|
| CSV upload from Airbnb exports | No direct Airbnb API integration (CSV-only for Airbnb) |
| PMS sync (Guesty, Hostaway, Lodgify, Generic) | PMS sync is poll-based only, no webhooks |
| Manual reservation creation | No calendar view of reservations |
| Guest context mapping (phone + unit/room) | No bulk context mapping |
| Guest timeline with all events | Timeline is read-only — no manual notes |
| Check-in portal with token-based link | Check-in portal is view-only (no guest forms) |
| Issue ticket creation per reservation | No revenue/payout tracking in UI |
| Reservation export to CSV | No occupancy rate chart |
| Review tracking (rating, sentiment) | Review data is import-only, no push |
| Repeat guest detection | No guest profile/CRM beyond reservation fields |

**Verdict**: Strong reservation pipeline. Missing calendar visualization and direct booking platform APIs.

---

## Area 8: AI / LLM Quality

**Files**: `classifier.py`, `workflow.py`

**Architecture**: OpenRouter → routes to configurable models (Claude 3.5 Sonnet default, Llama 70B fallback, GPT-4o-mini for sentiment).

| What works | What's missing |
|---|---|
| SKILL.md system prompt loaded from file | No A/B testing of models |
| Property context injected (rules, FAQ, amenities, menu) | No prompt versioning/rollback |
| Reservation + timeline memory injected | No per-property system prompt customization |
| Classification: routine vs complex (regex patterns) | Classification is regex-only — no LLM classification |
| Confidence score from pattern match count | Confidence is heuristic, not calibrated |
| PII redaction before sending to OpenRouter | PII redaction is regex-only (phone/email) — misses names in context |
| Policy conflict detection (pet, refund, checkin, checkout, parking, smoking) | Policy conflict is keyword-matching, not semantic |
| Multilingual auto-reply | No language detection confidence or fallback |
| Host feedback score per draft | No feedback loop into model fine-tuning |
| Guest sentiment analysis (LLM + regex fallback) | No intent classification beyond routine/complex |
| 3 retries with exponential backoff on OpenRouter | No fallback draft when all retries fail |
| API usage logging with token counts and cost | No cost alerting when spend exceeds threshold |

**Verdict**: Good foundational AI. Classification needs LLM upgrade. No closed-loop learning from host edits.

---

## Area 9: Ops / Workflow

**Files**: `workflow.py`, `workflow_center.html`, `ops_queue.html`, `vendor_workflow.html`, `automation_rules.html`, `app.py:4438-4760`

| What works | What's missing |
|---|---|
| Automation rules engine (confidence threshold, channel filter, day/hour window, keyword block, stay stage, sentiment gate) | No visual rule builder — JSON config in DB |
| Exception queue (failed sends, stale pending, missing phone, missing unit) | No SLA tracking (time to respond) |
| Issue tickets (create, assign to team member, resolve) | No recurring task scheduling (daily cleaning checklist) |
| Vendor directory with categories | No vendor availability/scheduling |
| Ops queue view combining exceptions + issues | No Kanban/board view for issues |
| Vendor workflow page | No vendor notification when assigned |
| Policy conflict check before auto-send | No approval workflow (multi-person review) |

**Verdict**: Automation rules engine is sophisticated. Missing visual builder and SLA tracking.

---

## Area 10: Multi-Property & Teams

**Files**: `models.py` (TeamMember, property_scope), `app.py:5092-5152`

| What works | What's missing |
|---|---|
| Team members with roles (owner, manager, front_desk, maintenance, cleaner) | Team members can't log in — no separate auth |
| Property scope per team member | No per-property permission enforcement on routes |
| JSON permissions field | Permissions field exists but is never checked |
| Issue assignment to team members | No team member notification system |
| `unit_identifier` on reservations for multi-unit | No property/unit management UI (just comma-separated names) |

**Verdict**: Data model supports multi-property teams. No enforcement or separate login for team members. This is a shell — the feature doesn't work.

---

## Area 11: Notifications

| What exists | What's missing |
|---|---|
| Escalation email alert | No push notifications (browser or mobile) |
| Welcome email | No in-app notification center |
| Weekly digest email | No SMS/WhatsApp notification to host |
| Password reset email | No notification preferences per host |
| Email verification | No webhook notifications (for integrations) |
| Activity log (viewable in `/activity`) | No real-time alerts for urgent guest messages |

**Verdict**: Email-only notifications. No push, no in-app, no real-time alerts.

---

## Area 12: Analytics

**Files**: `workflow.py` (derive_dashboard_kpis, compute_approval_streak, find_occupancy_gaps, compute_review_velocity), `TenantKpiSnapshot` model

| What works | What's missing |
|---|---|
| Dashboard KPIs (drafts, approval rate, response time, occupancy gaps) | No analytics page — KPIs only on dashboard |
| KPI snapshot model for historical tracking | KPI snapshots never actually computed/stored (model exists, no scheduler) |
| Occupancy gap detection | No revenue analytics |
| Review velocity computation | No channel performance comparison |
| Guest sentiment aggregation | No export of analytics data |
| Portfolio benchmark computation | No charts/graphs — numbers only |
| Approval streak tracking | No date range filtering |
| Admin-level draft quality stats (30-day) | No per-property analytics breakdown |

**Verdict**: Rich KPI computation logic exists. Never displayed in a dedicated analytics page. No historical tracking despite the snapshot model existing.

---

## Area 13: Templates & Automation

**Files**: `automation_rules.html`, `AutomationRule` model, `workflow.py:638-748`

| What works | What's missing |
|---|---|
| Auto-send rules with 15+ conditions (confidence, sentiment, channel, day, hour, property, keywords, stay stage, guest history) | No template library for common responses |
| Policy conflict blocks auto-send | No scheduled/recurring message templates |
| Complex message review requirement | No template variables/placeholders |
| Rule priority ordering | No A/B testing of auto-send rules |
| Calendar-triggered proactive messages (pre-arrival, check-in, cleaner brief, extension offer) | No drag-and-drop rule builder |

**Verdict**: Automation rules engine is the most mature feature. Missing user-friendly template system.

---

## Area 14: Localization / i18n

| What exists | What's missing |
|---|---|
| AI auto-detects guest language and replies in same language | ALL UI text is hardcoded English |
| Multilingual system prompt rule in classifier | No i18n framework (no `gettext`, no translation files) |
| | No locale-aware date/time formatting |
| | No currency localization |
| | No RTL support |

**Verdict**: Guest-facing AI is multilingual. Host-facing UI is English-only with no i18n infrastructure.

---

## Area 15: Deployment & Infrastructure

**Files**: `docker-compose.yml`, `Dockerfile`, `fly.toml`, `railway.toml`

| What works | What's missing |
|---|---|
| Docker Compose with PostgreSQL, PgBouncer, Redis, Nginx, certbot | No CI/CD pipeline defined |
| Connection pooling via PgBouncer | No staging environment config |
| Daily backup container (pg_dump + gzip) | Backup uses pipe without `pipefail` (fixed per threat analysis) |
| SSL via certbot auto-renewal | No blue-green or rolling deploy strategy |
| Fly.io and Railway deployment configs | No infrastructure-as-code (Terraform/Pulumi) |
| Read replica support (`db_read.py`) | Read replica configured but usage is minimal |
| Prometheus metrics endpoint | No Grafana dashboards defined |
| Sentry integration (optional) | No structured alerting rules |
| JSON structured logging in production | No log aggregation config (ELK/Datadog) |
| Leader election for workers via Redis | Leader election only for embedded workers, not for the web process |

**Verdict**: Good production stack. Missing CI/CD, staging, and observability dashboards.

---

## Area 16: Guest-Side Experience

**Files**: `checkin.html`, `app.py:6048-6110`

| What exists | What's missing |
|---|---|
| Public check-in portal at `/checkin/{token}` | No guest login or account | I think guest should only enter phone no. and select room and then same phone no. is set by host after guest arrival so no need of password or anything as guests are for temporary stays.
| Shows property info, FAQ, house rules | No guest feedback form |
| Token-based access (no auth required) | No check-in form (ID upload, arrival time) |
| FAQ parsed into Q&A accordion | No digital key/access code delivery |
| Property amenities listed | No local area guide with maps |
| Check-in/checkout times displayed | No link to message the host from portal |
| | No multi-language portal (follows UI language, not guest language) |
| | Token never expires — permanent access to property details |

**Verdict**: Minimal guest portal. View-only information page, no interactive features.

---

## Area 17: Legal & Compliance

| What exists | What's missing |
|---|---|
| PII redaction (phone/email regex) before OpenRouter | No Terms of Service page |
| `data_retention_days` field on TenantConfig | No Privacy Policy page |
| GDPR delete endpoint (`/api/tenant/delete`) | No cookie consent banner |
| `send_default_pii=False` in Sentry config | No DPA with OpenRouter |
| | No data export endpoint (GDPR Art. 20 portability) |
| | No consent tracking for AI processing |
| | Retention policy field exists but no automated purge job |
| | No geographic data residency controls |
| | No Terms acceptance checkbox on signup |
| | Activity logs stored indefinitely despite retention field |

**Verdict**: GDPR compliance is surface-level. Retention field exists but isn't enforced. No legal pages.

---

## Area 18: Cost Economics

**Files**: `admin_costs.html`, `ApiUsageLog` model, `app.py:5674-5726`

| What works | What's missing |
|---|---|
| API usage logging (model, tokens, cost per call) | No per-tenant cost cap or alerting |
| Admin cost dashboard | No cost optimization (model routing by message complexity) |
| Cost breakdown by tenant | No margin calculation (cost vs subscription revenue) |
| Model + provider tracked per call | No cost forecasting |
| | Free tier has no usage limit — unlimited AI calls at admin's expense |there is no free tier required remove it competely

**Verdict**: Cost tracking exists. No usage limits on free tier — a single free user could run up significant OpenRouter bills.

---

## Area 19: Host Offboarding

| What exists | What's missing |
|---|---|
| Tenant deactivation by admin (`/admin/tenants/{tid}/deactivate`) | No self-service account deletion |
| GDPR delete endpoint (marks inactive) | No data export before deletion |
| Stripe subscription cancellation via webhooks | No cancellation reason collection |
| | No offboarding email/confirmation |
| | No grace period with data preservation |
| | Deactivation doesn't stop workers immediately |
| | No re-activation flow for hosts |

**Verdict**: No self-service offboarding. Admin-only deactivation with no data export.

---

## Area 20: Support & Feedback

| What exists | What's missing |
|---|---|
| Host feedback score per draft (thumbs up/down + note) | No support ticket system |
| Escalation email for urgent issues | No in-app help/chat |
| Activity log as basic audit trail | No knowledge base / help center |
| | No feature request system |
| | No status page |
| | No onboarding tutorial / guided tour |
| | No changelog / what's new |
| | No NPS survey |

**Verdict**: No support infrastructure. Host feedback exists only as draft quality signal.

---

## Security & Concurrency Re-Assessment (Post-Threat Analysis)

### Fixes Confirmed in Code

| # | Finding | Status | Evidence |
|---|---|---|---|
| 1 | Scheduled draft double-send | **FIXED** | `with_for_update(skip_locked=True)` in `worker_manager.py:272-281` |
| 5 | Calendar state race | **FIXED** | `UniqueConstraint` on `calendar_states(tenant_id, state_key)` in `models.py:308-309` |
| 8 | Bot token timing attack | **FIXED** | `hmac.compare_digest()` in `billing.py:328` |
| 4 | Rate limit atomicity | **FIXED** | Lua script for INCR+EXPIRE in `security.py:147-153` |
| 10 | CSP unsafe-inline | **FIXED** | Nonce-based CSP in `security.py:238-248` |
| 14 | Dev Fernet key loss | **FIXED** | Persists to `.dev_fernet_key` file in `crypto.py:20-33` |
| 19 | No dead-letter queue | **FIXED** | `FailedDraftLog` model + dead-letter writes in `worker_manager.py:302-314` |
| 23 | python-jose CVE | **FIXED** | Replaced with PyJWT (`import jwt` in `auth.py:13-14`) |
| 28 | Memory leak in rate limiter | **FIXED** | Periodic prune in `security.py:178-181` |
| 30 | No leader election | **FIXED** | Redis-based leader lock in `worker_manager.py:52-77` |
| 12 | Timezone off-by-one | **FIXED** | `timezone` field on TenantConfig, `ZoneInfo(cfg.timezone)` in `calendar_worker.py` |
| 9 | PII sent to OpenRouter | **PARTIAL** | Phone/email regex redaction added in classifier, but guest names and property context still sent |

### Remaining Risks

| # | Finding | Status | Risk |
|---|---|---|---|
| 2 | PMS auto-send status race | **NOT VERIFIED** | Need to check `_execute_draft` guard |
| 3 | SMTP send before DB commit | **NOT VERIFIED** | Email worker order may still be wrong |
| 6 | OpenRouter outage = draft loss | **NOT FIXED** | No queuing or retry-later mechanism |
| 7 | Escalation alert silently dropped | **PARTIAL** | 3-attempt retry added, but still no persistent queue |
| 11 | Phone normalization | **NOT VERIFIED** | No E.164 normalization visible in models or app.py |
| 13/26 | Upload size limits | **NOT VERIFIED** | Need to check `app.py:3785` for size check |
| 15 | SECRET_KEY fallback | **STILL PRESENT** | Fallback string in dev mode (`auth.py:31`) |
| 16 | Unsalted SHA256 bot token | **STILL PRESENT** | `billing.py:317` — mitigated by high entropy |
| 17 | CSRF 64-bit truncation | **STILL PRESENT** | `security.py:70` — low risk |
| 20 | GDPR third-party processing | **NOT FIXED** | No DPA, no consent mechanism |
| 21 | No data retention enforcement | **NOT FIXED** | Field exists but no purge job |
| 22 | Phone in logs | **NOT VERIFIED** | Need to check log statements |
| 27 | No HTTP session pooling in PMS | **NOT FIXED** | Still creates new connections per call |
| 29 | Backup pipe failure | **NOT VERIFIED** | Need to check docker-compose |

### New Security Observations

1. **Twilio inbound webhook has no signature verification** — `sms_sender.py:53-63` and the `/sms/webhook/{tenant_id}` handler don't validate Twilio's `X-Twilio-Signature`. Anyone knowing the tenant_id can forge inbound SMS.

2. **Check-in token never expires** — `checkin_token` is a permanent URL with no TTL. Guest can access property details indefinitely after checkout.

3. **Admin impersonation has no audit trail** — `app.py:5514` sets a session cookie as the target tenant with no log entry.

4. **CSRF exemptions are broad** — `/api/wa/`, `/api/workers`, `/api/drafts`, `/api/download/` are all CSRF-exempt. The bot API endpoints are token-authenticated, but `/api/drafts` is session-authenticated and CSRF-exempt.

5. **No rate limit on signup** — `/signup` has rate limiting, but the limit isn't visible in the code excerpt. Password reset has no rate limit beyond IP-level.

6. **Metrics endpoint auth is unclear** — `/metrics/prometheus` calls `_require_metrics_auth()` but implementation wasn't reviewed.

---

## Gap Analysis — Host Experience

### Onboarding Gaps
1. **No progress persistence** — Multi-step wizard doesn't save between pages
2. **No sample data** — New hosts see empty dashboard, no sample draft to understand flow
3. **No video walkthrough** — Complex product with no visual guide
4. **No "first draft" celebration** — No gamification or milestone acknowledgment

### Daily Use Gaps
1. **No mobile app** — Web-only, responsive but not optimized for phone-as-primary can user install it as shortcut on his android or iphone and use it as app?
2. **15-second polling delay** — Urgent guest messages wait up to 15s to appear
3. **No conversation view** — Hosts manage individual drafts, can't see full guest conversation
4. **No quick reply shortcuts** — Every response requires AI generation or full text entry
5. **No notification when AI generates a draft** — Must keep dashboard open
6. **No "away mode"** — Can't set auto-approve with guard rails for vacations
7. **No guest satisfaction dashboard** — Sentiment data collected but not surfaced well

### Trust Gaps
1. **No explanation of AI reasoning** — Host sees draft but not why it chose that response
2. **No "similar past drafts" comparison** — Can't see how AI handled the same question before
3. **No approval before first auto-send** — New automation rules take effect immediately
4. **No draft diff view** — When editing, no before/after comparison
5. **Policy conflicts shown but no specific citation** — Says "conflicts with pet policy" but doesn't quote the policy
6. **No confidence explanation** — Confidence score shown but not what drives it

### Scale Gaps
1. **10+ property hosts** — Comma-separated property names, no per-property config
2. **Team collaboration** — Team members can be created but can't log in or act
3. **Multi-timezone** — Single timezone per tenant, not per property
4. **High message volume** — Dashboard doesn't paginate drafts, all loaded at once

---

## Summary Scorecard

| Area | Score | One-line |
|---|---|---|
| 1. Onboarding | 6/10 | Works but fragile, no persistence |
| 2. Guest Messaging | 7/10 | Strong multi-channel, no conversation view |
| 3. Host Dashboard | 7/10 | Good data density, needs real-time |
| 4. Channels | 7/10 | 4 channels working, missing media + delivery tracking |
| 5. Billing | 7/10 | Clean Stripe, missing trial + annual |
| 6. Admin | 8/10 | Comprehensive, needs audit log + search |
| 7. Reservations | 7/10 | Good pipeline, needs calendar view |
| 8. AI Quality | 6/10 | Functional, regex classification is limiting |
| 9. Ops/Workflow | 7/10 | Sophisticated rules, no visual builder |
| 10. Multi-Property | 3/10 | Data model ready, feature doesn't work |
| 11. Notifications | 3/10 | Email-only, no push/real-time |
| 12. Analytics | 4/10 | Logic exists, no dedicated page or charts |
| 13. Templates | 5/10 | Auto-send rules good, no template library |
| 14. Localization | 2/10 | AI multilingual, UI English-only |
| 15. Deployment | 7/10 | Good stack, no CI/CD |
| 16. Guest Portal | 3/10 | View-only info page |
| 17. Legal/Compliance | 2/10 | Surface-level, no enforcement |
| 18. Cost Economics | 5/10 | Tracking exists, no limits on free tier |
| 19. Offboarding | 2/10 | Admin-only, no self-service |
| 20. Support | 1/10 | No support infrastructure |
| **Security** | **6/10** | Major fixes applied, residual risks remain |
| **OVERALL** | **5.0/10** | |

---

## Top 10 Priority Fixes

1. **Free tier usage limits** — Add daily/monthly AI call cap per tenant (Area 18)
2. **Multi-property team login** — Let team members sign in with their own credentials (Area 10)
3. **Conversation view** — Show threaded guest conversations, not isolated drafts (Area 2/3)
4. **Twilio webhook verification** — Validate `X-Twilio-Signature` on inbound SMS (Area 4/Security)
5. **Check-in token expiry** — Add TTL (checkout date + 24h) to guest portal tokens (Area 16)
6. **Analytics page** — Surface the rich KPI data that's already computed (Area 12)
7. **Push notifications** — Browser notifications for new drafts and escalations (Area 11)
8. **Legal pages** — Add Terms of Service, Privacy Policy, cookie consent (Area 17)
9. **Data retention enforcement** — Scheduled job to purge data past `data_retention_days` (Area 17)
10. **Onboarding persistence** — Save wizard progress server-side between steps (Area 1)
