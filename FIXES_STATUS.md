# Status: All 6 Sonnet Fixes - Implementation Progress

## ✅ COMPLETED: Foundation & Schema

### Fix 6: Billing Plan Redesign
- ✅ New `PlanConfig` model (plan_key, display_name, base_fee_usd, per_unit_fee_usd, min_units, max_units)
- ✅ `TenantConfig.num_units` field for unit count tracking
- ✅ Default plan changed to "starter" with status "requires_upgrade" (removes free tier)
- 🔲 **TODO**: Routes `/billing/subscribe/{plan_key}`, `/admin/pricing`, `/api/plan-pricing`
- 🔲 **TODO**: `PlanConfig` seeding at startup
- 🔲 **TODO**: Dynamic Stripe checkout with `price_data` formula

### Fix 1: Team Member Login
- ✅ `TeamMember` password_hash, invite_token, invite_token_expires_at fields
- ✅ `create_member_token()` JWT factory (includes `mid` claim)
- ✅ `get_current_member()` validator (checks status, version hash)
- ✅ `member_session_version()` for session revocation
- 🔲 **TODO**: Routes `/team/login` (GET/POST), `/invite/{token}` (GET/POST), `/api/team/{member_id}/invite`
- 🔲 **TODO**: Property scope filtering helper in dashboard/reservations routes
- 🔲 **TODO**: Templates: login.html team member tab, invite_accept.html, base.html nav update

### Fix 5: Onboarding UX Polish
- ✅ `TenantConfig.extra_services` field added
- 🔲 **TODO**: Step 3 POST handler to save extra_services separately
- 🔲 **TODO**: Template: pre-populate extra_services checkboxes from `cfg.extra_services`
- 🔲 **TODO**: Template: import spinner on step 1 (show during listing fetch)
- 🔲 **TODO**: Template: IMAP error hints on step 5 (troubleshooting details)

### Fix 4: Data Retention (already deployed in Haiku fixes)
- ✅ `_process_data_retention()` scheduler in worker_manager.py
- ✅ Runs hourly via watchdog
- ✅ Purges activity logs, drafts, timeline events past `data_retention_days`

---

## 🔲 PENDING: Route & Template Implementation

### Fix 3: Analytics Page
Routes needed:
- `GET /analytics` - render analytics.html with KPIs, charts
- Add to worker_manager: `_process_kpi_snapshots()` daily scheduler

Templates needed:
- Create `analytics.html` with Chart.js (4 charts: volume, rate, channels, sentiment)
- Update `base.html` sidebar: add Analytics nav link

### Fix 2: Conversation View
Routes needed:
- Modify `GET /dashboard` - group drafts by `thread_key` before passing to template
- `GET /api/conversation/{thread_key}` - return full thread history as JSON

Templates needed:
- Update `dashboard.html` - replace `{% for draft %}` loop with `{% for conversation %}` grouped view
- Show prior messages in collapsible section (loaded via HTMX)

### Fix 4: Real-Time SSE Notifications
Routes needed:
- `GET /api/sse/drafts` - async generator, StreamingResponse, Redis pubsub with DB fallback
- Add publish in `_handle_guest_inbound_message()` after draft commit

Templates needed:
- Update `dashboard.html` - replace HTMX poll div with EventSource + Notification API

---

## Implementation Checklist

### Priority 1 (Blocking Other Fixes)
- [ ] Fix 6.A: PlanConfig seeding + `/billing/subscribe/{plan_key}` route
- [ ] Fix 6.B: `/admin/pricing` admin dashboard
- [ ] Fix 1.A: `/team/login`, `/invite/{token}` routes + property scoping
- [ ] Fix 1.B: Templates (login.html team tab, invite_accept.html)

### Priority 2 (Core Features)
- [ ] Fix 2: Dashboard conversation grouping + `/api/conversation/{key}`
- [ ] Fix 3: Analytics routes + `_process_kpi_snapshots()` scheduler
- [ ] Fix 4: SSE endpoint + pubsub integration

### Priority 3 (Polish)
- [ ] Fix 5: Onboarding step 3/5 template updates

---

## Code References

All implementation snippets are in: `/Users/chandan/Desktop/BNB/JestinRajan249/IMPLEMENTATION_GUIDE.md`

Organized by fix with exact function signatures, route handlers, and template sections ready to copy/paste.

---

## How to Continue

1. **Quick start**: Copy route implementations from IMPLEMENTATION_GUIDE.md → app.py
2. **Templates**: Create/update HTML files following guide structure
3. **Testing**:
   - Billing: Create new account → /pricing → enter units → checkout
   - Team login: Create member → send invite → /invite/{token} → create password → /team/login
   - Conversations: Send 3 messages same guest → dashboard shows 1 grouped card
   - Analytics: /analytics → verify 4 charts render
   - SSE: Open dashboard → trigger message → notification fires < 2s
   - Onboarding: Step 3 → check services → back → step 3 → verify re-checked

---

## Schema Migrations

Already picked up by `db_migrate()`:
```python
# In db.migrate():
if not has_column('team_members', 'password_hash'):
    add_column('team_members', 'password_hash', String(128), nullable=True)
if not has_column('team_members', 'invite_token'):
    add_column('team_members', 'invite_token', String(64), nullable=True)
# ... etc for all new columns
```

Seeding needed (add to startup):
```python
def seed_plan_configs(db):
    plans = [
        {"plan_key": "starter", "display_name": "Starter", "base_fee": 20.0, "per_unit": 10.0, "min": 1, "max": 5},
        {"plan_key": "growth", "display_name": "Growth", "base_fee": 20.0, "per_unit": 9.0, "min": 6, "max": 10},
        {"plan_key": "pro", "display_name": "Pro", "base_fee": 20.0, "per_unit": 8.0, "min": 11, "max": 50},
    ]
    for plan in plans:
        if not db.query(PlanConfig).filter_by(plan_key=plan["plan_key"]).first():
            db.add(PlanConfig(**plan))
    db.commit()
```

---

## Next Session

Start with:
1. Implement Fix 6 routes (billing is foundation)
2. Implement Fix 1 routes (team login)
3. Implement remaining features
4. Run end-to-end tests
5. Create PR

Total estimated remaining effort: 4-6 hours of focused implementation.
