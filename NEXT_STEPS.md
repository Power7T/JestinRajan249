# Next Steps: Completing All 6 Sonnet Fixes

## What's Been Delivered

### ✅ Complete Planning & Documentation
- **PLAN file** (`/Users/chandan/.claude/plans/fizzy-churning-journal.md`): Full architectural design for all 6 fixes
- **IMPLEMENTATION_GUIDE.md**: Complete code snippets organized by fix (ready to copy/paste)
- **FIXES_STATUS.md**: Detailed checklist of what's done vs. what remains
- **Schema foundation**: All model changes committed to git

### ✅ Foundation Code (Committed)
1. **PlanConfig model** - unit-based billing (starter/growth/pro at $20 base + $10/$9/$8 per unit)
2. **TeamMember auth fields** - password_hash, invite_token, invite_token_expires_at
3. **Member JWT functions** - create_member_token(), get_current_member(), member_session_version()
4. **TenantConfig fields** - num_units (billing), extra_services (onboarding)
5. **Data retention scheduler** - _process_data_retention() runs hourly
6. **Updated imports** - app.py ready for new routes

### ✅ Haiku Fixes (Already Complete)
- Twilio SMS webhook signature verification
- Check-in token expiry (24h after checkout)
- Free tier AI call limits (10/day, 50/month)
- Data retention enforcement job (GDPR compliance)
- Legal pages (Terms of Service, Privacy Policy)
- Admin impersonation audit logging

---

## Remaining Work: 7 Routes + 6 Templates

All code is ready in IMPLEMENTATION_GUIDE.md. You can systematically work through these:

### **Priority 1: Billing Foundation (Fix 6)**
**Routes** (3 needed):
```
POST   /billing/subscribe/{plan_key}    - new route with num_units parameter
GET    /admin/pricing                   - admin dashboard to edit prices
POST   /admin/pricing/{plan_key}        - save plan config changes
GET    /api/plan-pricing                - public JSON pricing endpoint
```
**Why first**: All subscription logic depends on this. Blocking fixes 1-5.

### **Priority 2: Team Login (Fix 1)**
**Routes** (3 needed):
```
GET    /team/login                      - team member login page
POST   /team/login                      - authenticate member, set JWT
POST   /api/team/{member_id}/invite     - generate invite token, send email
GET    /invite/{token}                  - accept invite form
POST   /invite/{token}                  - set password, log in
```
**Templates** (3 needed):
- Update login.html: add "Team member" tab
- Create invite_accept.html: password entry form
- Update base.html: show member name + role when logged in as member

### **Priority 3: Features (Fixes 2, 3, 4)**
**Dashboard conversations (Fix 2)**:
- Modify GET /dashboard route to group drafts by thread_key
- Add GET /api/conversation/{thread_key} endpoint
- Update dashboard.html template

**Analytics (Fix 3)**:
- Add GET /analytics route
- Add _process_kpi_snapshots() scheduler to worker_manager
- Create analytics.html with 4 Chart.js charts
- Add Analytics link to base.html nav

**Real-time SSE (Fix 4)**:
- Add GET /api/sse/drafts endpoint (async, Redis pubsub)
- Add pubsub publish to _handle_guest_inbound_message()
- Update dashboard.html to use EventSource instead of polling

### **Priority 4: Polish (Fix 5)**
**Onboarding** (template only):
- Step 3: Pre-populate extra_services checkboxes from cfg.extra_services
- Step 1: Show import spinner during Airbnb listing fetch
- Step 5: Add IMAP troubleshooting tips in error area

---

## How to Execute

### Option A: Implement Everything Now
1. Copy routes from IMPLEMENTATION_GUIDE.md → app.py (Priority 1, then 2, then 3)
2. Create/update templates (6 files)
3. Test end-to-end using checklist in FIXES_STATUS.md
4. Create PR

**Estimated time**: 4-6 hours focused work

### Option B: Staged Implementation (Recommended)
**Session 1**: Billing (Fix 6) routes + seeding
- Implement billing routes and PlanConfig seeding
- Test: create account → /pricing → subscribe with units → verify checkout

**Session 2**: Team Login (Fix 1) routes + templates
- Implement 5 team login routes
- Create 3 templates
- Test: create member → send invite → accept → log in

**Session 3**: Remaining Features (Fixes 2, 3, 4)
- Dashboard conversation view
- Analytics page + scheduler
- SSE real-time notifications

**Session 4**: Polish (Fix 5) + Testing
- Onboarding template updates
- End-to-end testing
- PR creation

---

## Quick Copy-Paste Reference

**For each route**, find in IMPLEMENTATION_GUIDE.md:
- Function signature with all parameters
- Complete implementation with error handling
- Form validation, CSRF checks, rate limiting, logging

**For each template**, find:
- HTML structure with Bootstrap classes
- Jinja2 variable references
- HTMX or JavaScript integrations
- CSP nonce handling

---

## Files to Modify

| File | Changes |
|------|---------|
| web/app.py | +7 routes |
| web/worker_manager.py | + _process_kpi_snapshots() |
| web/templates/login.html | Add team member tab |
| web/templates/invite_accept.html | **NEW** |
| web/templates/admin_pricing.html | **NEW** |
| web/templates/analytics.html | **NEW** |
| web/templates/base.html | Add Analytics nav link, update member display |
| web/templates/dashboard.html | Conversation grouping + SSE |
| web/templates/onboarding.html | Step 3/5 improvements |
| web/templates/pricing.html | Rewrite for unit-based plans |

---

## Testing Checklist

After implementing, verify:

✅ **Billing**
- [ ] New account defaults to "requires_upgrade"
- [ ] /pricing shows 3 plans with unit inputs
- [ ] Live price calculation works (base + per_unit × units)
- [ ] Checkout creates Stripe session with correct amount
- [ ] Admin can edit prices and changes persist

✅ **Team Login**
- [ ] Create team member in settings
- [ ] /api/team/{id}/invite generates token, sends email
- [ ] /invite/{token} page loads
- [ ] Password submission creates account
- [ ] /team/login accepts email/password
- [ ] Logged-in member sees dashboard
- [ ] Property scope filtering works (member sees only scoped properties)

✅ **Conversations**
- [ ] Send 3 messages from same guest
- [ ] Dashboard shows 1 conversation card with message count
- [ ] Prior messages load when expanded

✅ **Analytics**
- [ ] /analytics loads
- [ ] 4 charts render without errors
- [ ] Date range selector works (7d/30d/90d)
- [ ] KPI data matches dashboard

✅ **Real-time**
- [ ] Open dashboard, trigger inbound message
- [ ] Notification fires within 2 seconds
- [ ] New draft appears on dashboard automatically
- [ ] No page reload needed

✅ **Onboarding**
- [ ] Step 3: check extra_services → submit → back → step 3 → checkboxes pre-checked
- [ ] Step 1: import button shows spinner while fetching
- [ ] Step 5: IMAP error shows troubleshooting tips

---

## Git Workflow

```bash
# Current state: foundation committed
git log --oneline | head

# To continue: create feature branch for remaining work
git checkout -b feat/sonnet-fixes-complete

# After implementing routes:
git add web/app.py
git commit -m "Implement billing and team login routes"

# After templates:
git add web/templates/
git commit -m "Add team member, analytics, and conversation templates"

# After everything:
git log --oneline | head
# Create PR with all commits
```

---

## Questions During Implementation?

- Reference **IMPLEMENTATION_GUIDE.md** for exact code
- Check **FIXES_STATUS.md** for what's completed vs. pending
- Review **PLAN file** for architectural context
- Cross-reference with **PRODUCT_AUDIT.md** for feature context

---

## Success Criteria

When complete, HostAI will have:

✅ **Unit-based billing** - All channels unlocked, pricing by property count
✅ **Team member login** - Staff can log in with credentials, property-scoped access
✅ **Conversation view** - Guests grouped by thread, not individual drafts
✅ **Analytics dashboard** - KPIs visualized with charts, date range filtering
✅ **Real-time notifications** - Browser notifications < 2s latency
✅ **Onboarding polish** - Step 3 checkbox re-population, helpful spinners & hints

**Overall score improvement**: 5.0/10 → ~7.0/10 (estimated)

---

## Status: READY FOR IMPLEMENTATION ✅

All schema, auth, and documentation complete. Ready to implement routes and templates.

Estimated completion time: **4-6 hours of focused work**

Start with Billing (Fix 6) → Team Login (Fix 1) → Features (2,3,4) → Polish (5).
