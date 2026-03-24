# Implementation Guide: All 6 Sonnet Fixes

Complete code snippets and file changes needed for each fix. Execute in order: Fix 6 → Fix 1 → Fix 2 → Fix 3 → Fix 4 → Fix 5.

---

## Status: Schema Changes Complete ✅

- ✅ `PlanConfig` model added to models.py
- ✅ `TeamMember` password_hash, invite_token, invite_token_expires_at added
- ✅ `TenantConfig.num_units` and `extra_services` added
- ✅ `create_member_token()`, `get_current_member()`, `member_session_version()` added to auth.py

---

## Fix 6: Billing Plan Redesign

### Step 1: Update PLAN constants (models.py, done but needs review)

Already updated. New plans:
```python
PLAN_STARTER = "starter"   # 1-5 units, $20 base + $10/unit
PLAN_GROWTH = "growth"     # 6-10 units, $20 base + $9/unit
PLAN_PRO = "pro"           # 11-50 units, $20 base + $8/unit
```

### Step 2: Seed PlanConfig at startup (web/db.py)

Add to `db_migrate()` function after all schema migrations run:

```python
def seed_plan_configs(db):
    """Create default PlanConfig rows if they don't exist."""
    plans = [
        {"plan_key": "starter", "display_name": "Starter", "base_fee_usd": 20.0, "per_unit_fee_usd": 10.0, "min_units": 1, "max_units": 5},
        {"plan_key": "growth", "display_name": "Growth", "base_fee_usd": 20.0, "per_unit_fee_usd": 9.0, "min_units": 6, "max_units": 10},
        {"plan_key": "pro", "display_name": "Pro", "base_fee_usd": 20.0, "per_unit_fee_usd": 8.0, "min_units": 11, "max_units": 50},
    ]
    for plan_data in plans:
        existing = db.query(PlanConfig).filter_by(plan_key=plan_data["plan_key"]).first()
        if not existing:
            db.add(PlanConfig(**plan_data))
    db.commit()

# Call in db_migrate():
seed_plan_configs(db)
```

### Step 3: Update billing.py checkout (replace create_checkout_session)

```python
def create_checkout_session(tenant_id: str, plan_key: str, num_units: int,
                            success_url: str, cancel_url: str,
                            customer_id: str | None = None, db: Session = None) -> str:
    """Create Stripe checkout for unit-based plan."""
    if not db:
        from web.db import SessionLocal
        db = SessionLocal()
        owns_db = True
    else:
        owns_db = False

    try:
        plan = db.query(PlanConfig).filter_by(plan_key=plan_key, is_active=True).first()
        if not plan:
            raise HTTPException(status_code=400, detail=f"Plan {plan_key} not found")

        if not (plan.min_units <= num_units <= plan.max_units):
            raise HTTPException(status_code=400,
                detail=f"Plan {plan_key} requires {plan.min_units}-{plan.max_units} units")

        total_cents = int((plan.base_fee_usd + plan.per_unit_fee_usd * num_units) * 100)

        session = stripe.checkout.Session.create(
            mode="subscription",
            line_items=[{
                "price_data": {
                    "currency": "usd",
                    "unit_amount": total_cents,
                    "product_data": {
                        "name": f"HostAI {plan.display_name} ({num_units} unit{'s' if num_units > 1 else ''})"
                    },
                    "recurring": {"interval": "month"},
                },
                "quantity": 1,
            }],
            success_url=success_url,
            cancel_url=cancel_url,
            client_reference_id=tenant_id,
            metadata={"tenant_id": tenant_id, "plan": plan_key, "num_units": num_units},
            subscription_data={"metadata": {"tenant_id": tenant_id, "plan": plan_key, "num_units": num_units}},
            customer=customer_id,
        )
        return session.url
    finally:
        if owns_db:
            db.close()
```

### Step 4: Update /billing/subscribe route in app.py

```python
@app.post("/billing/subscribe/{plan_key}")
def billing_subscribe(plan_key: str, request: Request,
                     num_units: int = Form(1),
                     csrf_token: str = Form(None),
                     db: Session = Depends(get_db)):
    try:
        tenant_id = get_current_tenant_id(request)
    except HTTPException:
        return _redirect_login()
    validate_csrf(request, csrf_token)

    # Validate plan and units
    plan = db.query(PlanConfig).filter_by(plan_key=plan_key, is_active=True).first()
    if not plan:
        raise HTTPException(status_code=400, detail="Invalid plan")
    if not (plan.min_units <= num_units <= plan.max_units):
        raise HTTPException(status_code=400, detail=f"Plan requires {plan.min_units}-{plan.max_units} units")

    cfg = _get_or_create_config(tenant_id, db)
    try:
        url = create_checkout_session(
            tenant_id=tenant_id,
            plan_key=plan_key,
            num_units=num_units,
            success_url=f"{APP_BASE_URL}/billing/success?plan={plan_key}",
            cancel_url=f"{APP_BASE_URL}/billing/cancel",
            customer_id=cfg.stripe_customer_id,
            db=db,
        )
    except HTTPException:
        raise
    except Exception as exc:
        log.error("Stripe checkout error: %s", exc)
        raise HTTPException(status_code=500, detail="Payment provider error")

    return RedirectResponse(url, status_code=302)
```

### Step 5: Add /api/plan-pricing endpoint (app.py)

```python
@app.get("/api/plan-pricing")
def api_plan_pricing(db: Session = Depends(get_db)):
    """Public JSON endpoint: current plan pricing and tiers."""
    plans = db.query(PlanConfig).filter_by(is_active=True).order_by(PlanConfig.min_units).all()
    return [
        {
            "plan_key": p.plan_key,
            "display_name": p.display_name,
            "base_fee": p.base_fee_usd,
            "per_unit_fee": p.per_unit_fee_usd,
            "min_units": p.min_units,
            "max_units": p.max_units,
        }
        for p in plans
    ]
```

### Step 6: Add /admin/pricing routes (app.py)

```python
@app.get("/admin/pricing", response_class=HTMLResponse)
def admin_pricing_page(request: Request, db: Session = Depends(get_db)):
    _require_admin(request, db)
    plans = db.query(PlanConfig).order_by(PlanConfig.min_units).all()
    return templates.TemplateResponse("admin_pricing.html", {
        "request": request,
        "plans": plans,
    })


@app.post("/admin/pricing/{plan_key}")
def admin_update_pricing(plan_key: str, request: Request,
                         base_fee: float = Form(...),
                         per_unit_fee: float = Form(...),
                         min_units: int = Form(...),
                         max_units: int = Form(...),
                         display_name: str = Form(...),
                         csrf_token: str = Form(None),
                         db: Session = Depends(get_db)):
    _require_admin(request, db)
    validate_csrf(request, csrf_token)

    plan = db.query(PlanConfig).filter_by(plan_key=plan_key).first()
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")

    plan.base_fee_usd = base_fee
    plan.per_unit_fee_usd = per_unit_fee
    plan.min_units = min_units
    plan.max_units = max_units
    plan.display_name = display_name
    plan.updated_at = datetime.now(timezone.utc)

    db.add(plan)
    db.commit()

    return RedirectResponse(f"/admin/pricing?msg=updated", status_code=302)
```

### Step 7: Update pricing.html template

Replace entire pricing section with unit-based cards. See template file - needs complete rewrite to show:
- 3 plan cards with unit count input
- JS to calculate live price: `total = base_fee + per_unit_fee * units`
- Fetch from `/api/plan-pricing`

### Step 8: Create admin_pricing.html template

Admin dashboard to edit plan pricing - add/edit base_fee, per_unit_fee, min/max units for each plan.

### Step 9: Remove require_channel calls

Find and remove all `require_channel(cfg, PLAN_*)` checks throughout app.py, workers. Replace with:
```python
if cfg.subscription_status not in ("active", "trialing"):
    raise HTTPException(status_code=402, detail="Subscription required")
```

---

## Fix 1: Team Member Login

### Step 1: New routes in app.py

```python
@app.get("/team/login", response_class=HTMLResponse)
def team_login_page(request: Request):
    return templates.TemplateResponse("login.html", {
        "request": request,
        "show_team_tab": True,
    })


@app.post("/team/login")
def team_login(request: Request,
               email: str = Form(...),
               password: str = Form(...),
               csrf_token: str = Form(None),
               db: Session = Depends(get_db)):
    validate_csrf(request, csrf_token)
    rate_limit(f"team-login:{client_ip(request)}", 10, 900)  # 10/15min per IP

    member = db.query(TeamMember).filter(
        TeamMember.email == email.lower(),
        TeamMember.is_active == True,
    ).first()

    if not member or not member.password_hash:
        raise HTTPException(status_code=401, detail="Invalid email or password")

    if not verify_password(password, member.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    version = member_session_version(member)
    token = create_member_token(member.id, member.tenant_id, member.role, version)

    is_secure = is_request_secure(request)
    resp = RedirectResponse("/dashboard", status_code=302)
    resp.set_cookie("session", token, httponly=True, samesite="strict",
                    secure=is_secure, max_age=72*3600)

    member.last_login_at = datetime.now(timezone.utc)
    db.add(ActivityLog(
        tenant_id=member.tenant_id,
        event_type="team_member_login",
        message=f"Team member {member.display_name} ({member.role}) logged in",
    ))
    db.commit()

    return resp


@app.post("/api/team/{member_id}/invite")
def send_team_invite(member_id: int, request: Request,
                     csrf_token: str = Form(None),
                     db: Session = Depends(get_db)):
    try:
        tenant_id = get_current_tenant_id(request)
    except HTTPException:
        raise HTTPException(status_code=401, detail="Not authenticated")
    validate_csrf(request, csrf_token)

    member = db.query(TeamMember).filter_by(id=member_id, tenant_id=tenant_id).first()
    if not member:
        raise HTTPException(status_code=404, detail="Team member not found")

    # Generate invite token (48h TTL)
    invite_token = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(hours=48)

    member.invite_token = invite_token
    member.invite_token_expires_at = expires_at
    db.add(member)
    db.commit()

    # Send invite email (placeholder — use existing SMTP pattern)
    # send_team_invite(smtp_cfg, member.email, f"{APP_BASE_URL}/invite/{invite_token}", ...)

    return RedirectResponse("/settings?msg=invite_sent&tab=workflow", status_code=302)


@app.get("/invite/{token}", response_class=HTMLResponse)
def invite_accept_page(token: str, request: Request, db: Session = Depends(get_db)):
    member = db.query(TeamMember).filter_by(invite_token=token).first()
    if not member or not member.invite_token_expires_at:
        raise HTTPException(status_code=404, detail="Invite not found or expired")

    if datetime.now(timezone.utc) > member.invite_token_expires_at:
        raise HTTPException(status_code=404, detail="Invite link has expired")

    return templates.TemplateResponse("invite_accept.html", {
        "request": request,
        "token": token,
        "member_name": member.display_name,
    })


@app.post("/invite/{token}")
def accept_invite(token: str, request: Request,
                  password: str = Form(...),
                  password_confirm: str = Form(...),
                  csrf_token: str = Form(None),
                  db: Session = Depends(get_db)):
    validate_csrf(request, csrf_token)

    if password != password_confirm:
        raise HTTPException(status_code=400, detail="Passwords do not match")
    if len(password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")

    member = db.query(TeamMember).filter_by(invite_token=token).first()
    if not member or not member.invite_token_expires_at:
        raise HTTPException(status_code=404, detail="Invite not found or expired")

    if datetime.now(timezone.utc) > member.invite_token_expires_at:
        raise HTTPException(status_code=404, detail="Invite link has expired")

    # Set password and clear invite
    member.password_hash = hash_password(password)
    member.invite_token = None
    member.invite_token_expires_at = None

    # Create session and log in
    version = member_session_version(member)
    token_jwt = create_member_token(member.id, member.tenant_id, member.role, version)

    is_secure = is_request_secure(request)
    resp = RedirectResponse("/dashboard", status_code=302)
    resp.set_cookie("session", token_jwt, httponly=True, samesite="strict",
                    secure=is_secure, max_age=72*3600)

    member.last_login_at = datetime.now(timezone.utc)
    db.add(ActivityLog(
        tenant_id=member.tenant_id,
        event_type="team_member_invite_accepted",
        message=f"Team member {member.display_name} accepted invite and set password",
    ))
    db.commit()

    return resp
```

### Step 2: Update login.html template

Add "Team member" tab alongside existing sign-in. Tab switches form POST destination between `/login` (tenant) and `/team/login` (member).

### Step 3: Create invite_accept.html template

Simple form: password + confirm, submit button. Inherits base.html.

### Step 4: Update base.html navbar

When `request.state.member_id` is set, show member name + role badge. Add "Switch account" link to `/login`.

### Step 5: Property scoping helper in app.py

```python
def _get_tenant_and_member(request, db):
    """Returns (tenant_id, member_id, role) — tries tenant auth first, then member."""
    try:
        tid = get_current_tenant_id(request)
        return tid, None, "owner"  # tenant acts as owner
    except HTTPException:
        try:
            tid, mid, role = get_current_member(request, db)
            return tid, mid, role
        except HTTPException:
            raise HTTPException(status_code=401, detail="Not authenticated")

# Use in dashboard/reservations routes:
def _filter_by_property_scope(query, member_id, db):
    """Filter query results by member's property_scope if member is logged in."""
    if not member_id:
        return query  # Tenant sees all

    member = db.query(TeamMember).filter_by(id=member_id).first()
    if not member or not member.property_scope:
        return query  # No scope restriction

    properties = [p.strip() for p in member.property_scope.split(",")]
    return query.filter(Draft.property_name_snapshot.in_(properties))
```

---

## Fix 2: Conversation View

### Step 1: Update dashboard route in app.py

```python
# After existing drafts query, add grouping:
from collections import defaultdict

conversations_map = defaultdict(lambda: {
    "guest_name": "", "reply_to": "", "reservation": None,
    "thread_key": None, "drafts": [], "last_at": None
})

res_by_id = {r.id: r for r in reservations}

for draft in sorted(pending, key=lambda d: d.created_at):
    key = draft.thread_key or f"solo:{draft.id}"
    conv = conversations_map[key]
    conv["guest_name"] = draft.guest_name
    conv["reply_to"] = draft.reply_to
    conv["thread_key"] = draft.thread_key
    conv["last_at"] = draft.created_at
    if draft.reservation_id and not conv["reservation"]:
        conv["reservation"] = res_by_id.get(draft.reservation_id)
    conv["drafts"].append(draft)

conversations = sorted(conversations_map.values(),
                      key=lambda c: c["last_at"] or datetime.min,
                      reverse=True)

# Pass to template:
return templates.TemplateResponse("dashboard.html", {
    ...,
    "conversations": conversations,  # NEW
    "drafts": pending,  # Keep for backward compat with KPIs
})
```

### Step 2: Add /api/conversation/{thread_key} endpoint

```python
@app.get("/api/conversation/{thread_key}")
def api_conversation(thread_key: str, request: Request, db: Session = Depends(get_db)):
    """Get all drafts for a thread (last 10 sent + all pending)."""
    tenant_id = get_current_tenant_id(request)

    # Get last 10 sent drafts + all pending
    sent_drafts = db.query(Draft).filter(
        Draft.tenant_id == tenant_id,
        Draft.thread_key == thread_key,
        Draft.status.in_(["approved", "failed", "escalation"]),
    ).order_by(Draft.created_at.desc()).limit(10).all()

    pending_drafts = db.query(Draft).filter(
        Draft.tenant_id == tenant_id,
        Draft.thread_key == thread_key,
        Draft.status == "pending",
    ).order_by(Draft.created_at).all()

    all_drafts = sorted(sent_drafts + pending_drafts, key=lambda d: d.created_at)

    return [
        {
            "id": d.id,
            "guest_name": d.guest_name,
            "message": d.message,
            "draft": d.draft,
            "status": d.status,
            "created_at": d.created_at.isoformat(),
            "guest_message_index": d.guest_message_index,
        }
        for d in all_drafts
    ]
```

### Step 3: Update dashboard.html template

```html
{% for conv in conversations %}
<div class="conversation-card">
  <!-- Header -->
  <div class="card-header">
    <h3>{{ conv.guest_name }}</h3>
    {% if conv.reservation %}
      <span>{{ conv.reservation.unit_identifier or conv.reservation.listing_name }}</span>
      <span>Check-in: {{ conv.reservation.checkin }} – {{ conv.reservation.checkout }}</span>
    {% endif %}
    <span class="message-count">{{ conv.drafts|length }} message{{ conv.drafts|length != 1 and 's' }}</span>
    <span class="sentiment">{{ conv.drafts[-1].guest_sentiment or 'neutral' }}</span>
  </div>

  <!-- Prior messages (collapsed) -->
  {% if conv.drafts|length > 1 %}
  <details class="thread-context">
    <summary>↓ Show {{ conv.drafts|length - 1 }} prior message{{ (conv.drafts|length - 1) != 1 and 's' }}</summary>
    <div hx-get="/api/conversation/{{ conv.thread_key }}" hx-trigger="click" hx-swap="innerHTML">
      <p>Loading...</p>
    </div>
  </details>
  {% endif %}

  <!-- Latest draft (active) -->
  {% set draft = conv.drafts[-1] %}
  <div class="draft-content">
    <p class="guest-message">Guest: "{{ draft.message[:200] }}..."</p>
    <p class="ai-draft">Draft: "{{ draft.draft[:300] }}..."</p>
    <div class="draft-actions">
      <form method="post" action="/drafts/{{ draft.id }}/approve" style="display:inline;">
        <button>Approve</button>
      </form>
      <button hx-get="/drafts/{{ draft.id }}/edit-form" hx-target="#edit-area">Edit</button>
      <form method="post" action="/drafts/{{ draft.id }}/skip" style="display:inline;">
        <button>Skip</button>
      </form>
    </div>
  </div>
</div>
{% endfor %}
```

---

## Fix 3: Analytics Page

### Step 1: Add scheduler job to worker_manager.py

```python
_last_kpi_snapshot = 0.0

def _process_kpi_snapshots():
    """Compute and store KPI snapshots for all tenants (once per 24h)."""
    global _last_kpi_snapshot
    import time
    now_ts = time.time()
    if now_ts - _last_kpi_snapshot < 86400:  # 24h
        return

    _last_kpi_snapshot = now_ts

    from datetime import datetime, timezone
    from web.db import SessionLocal
    from web.models import TenantConfig
    from web.app import _upsert_tenant_kpi_snapshot

    db = SessionLocal()
    try:
        for cfg in db.query(TenantConfig).all():
            try:
                _upsert_tenant_kpi_snapshot(cfg.tenant_id, db)
            except Exception as exc:
                log.warning("[%s] KPI snapshot error: %s", cfg.tenant_id, exc)
    finally:
        db.close()

# Call in _watchdog_loop() after _process_data_retention():
_process_kpi_snapshots()
```

### Step 2: Add /analytics route in app.py

```python
@app.get("/analytics", response_class=HTMLResponse)
def analytics_page(request: Request,
                   range_days: int = 30,
                   property: str = "",
                   db: Session = Depends(get_db)):
    tenant_id = get_current_tenant_id(request)

    # Fetch KPI snapshots for date range
    cutoff = datetime.now(timezone.utc) - timedelta(days=range_days)
    snapshots = db.query(TenantKpiSnapshot).filter(
        TenantKpiSnapshot.tenant_id == tenant_id,
        TenantKpiSnapshot.period_start >= cutoff,
    ).order_by(TenantKpiSnapshot.period_start).all()

    # Get current KPIs
    kpis = derive_dashboard_kpis(pending_drafts, reservations)

    return templates.TemplateResponse("analytics.html", {
        "request": request,
        "kpis": kpis,
        "snapshots": snapshots,
        "range_days": range_days,
    })
```

### Step 3: Create analytics.html template

```html
{% extends "base.html" %}

{% block content %}
<div class="analytics-page">
  <h1>Analytics</h1>

  <!-- Controls -->
  <div class="controls">
    <form method="get" class="filters">
      <label>Date Range:
        {% for days, label in [(7, '7 days'), (30, '30 days'), (90, '90 days')] %}
          <a href="?range_days={{ days }}" {% if range_days == days %}class="active"{% endif %}>{{ label }}</a>
        {% endfor %}
      </label>
    </form>
  </div>

  <!-- Summary cards -->
  <div class="kpi-cards">
    <div class="card">
      <h3>{{ kpis.drafts.total }}</h3>
      <p>Total Drafts</p>
    </div>
    <div class="card">
      <h3>{{ '%.1f'|format(kpis.drafts.approval_rate * 100) }}%</h3>
      <p>Approval Rate</p>
    </div>
    <div class="card">
      <h3>{{ '%.0f'|format(kpis.drafts.avg_response_seconds / 60) }}m</h3>
      <p>Avg Response Time</p>
    </div>
    <div class="card">
      <h3>{{ kpis.reservations.active_stays }}</h3>
      <p>Active Stays</p>
    </div>
  </div>

  <!-- Charts (Chart.js) -->
  <script src="https://unpkg.com/chart.js@4/dist/chart.umd.min.js" nonce="{{ request.state.csp_nonce }}"></script>

  <div class="charts">
    <div class="chart-container">
      <canvas id="dailyVolumeChart"></canvas>
    </div>
    <div class="chart-container">
      <canvas id="approvalRateChart"></canvas>
    </div>
    <div class="chart-container">
      <canvas id="channelBreakdownChart"></canvas>
    </div>
    <div class="chart-container">
      <canvas id="sentimentChart"></canvas>
    </div>
  </div>

  <script nonce="{{ request.state.csp_nonce }}">
    // Chart.js initialization here
    // Daily volume bar chart
    const dailyCtx = document.getElementById('dailyVolumeChart').getContext('2d');
    new Chart(dailyCtx, {
        type: 'bar',
        data: {
            labels: [/* dates from snapshots */],
            datasets: [{label: 'Drafts', data: [/* counts */]}],
        },
    });
    // ... other charts
  </script>
</div>
{% endblock %}
```

### Step 4: Add Analytics link to base.html

Add to sidebar nav between Reservations and Workflow:
```html
<a href="/analytics">Analytics</a>
```

---

## Fix 4: Real-Time SSE Notifications

### Step 1: Add Redis pubsub publish in _handle_guest_inbound_message()

```python
# After db.commit() of new draft:
r = get_redis()
if r:
    try:
        r.publish(f"hostai:notify:{tenant_id}", json.dumps({
            "guest_name": draft.guest_name,
            "source": draft.source,
            "msg_type": draft.msg_type,
            "draft_id": draft.id,
        }))
    except Exception:
        pass  # non-critical
```

### Step 2: Add SSE endpoint in app.py

```python
@app.get("/api/sse/drafts")
async def sse_drafts(request: Request, db: Session = Depends(get_db)):
    """Server-Sent Events stream for real-time draft notifications."""
    tenant_id = get_current_tenant_id(request)

    async def event_generator():
        import asyncio
        r = get_redis()

        if r:
            # Redis pubsub path
            pubsub = r.pubsub()
            pubsub.subscribe(f"hostai:notify:{tenant_id}")
            try:
                while not await request.is_disconnected():
                    msg = pubsub.get_message(timeout=25)
                    if msg and msg["type"] == "message":
                        yield f"data: {msg['data'].decode()}\n\n"
                    else:
                        yield ": keepalive\n\n"
                    await asyncio.sleep(0.5)
            finally:
                pubsub.unsubscribe()
        else:
            # Fallback: DB polling every 8s
            last_count = db.query(Draft).filter_by(tenant_id=tenant_id, status="pending").count()
            while not await request.is_disconnected():
                await asyncio.sleep(8)
                count = db.query(Draft).filter_by(tenant_id=tenant_id, status="pending").count()
                if count != last_count:
                    yield f"data: {json.dumps({'type': 'refresh'})}\n\n"
                    last_count = count
                else:
                    yield ": keepalive\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream",
                            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
```

### Step 3: Update dashboard.html to use SSE

Replace HTMX polling div with:

```html
<script nonce="{{ request.state.csp_nonce }}">
// Request notification permission once
if ('Notification' in window && Notification.permission === 'default') {
    Notification.requestPermission();
}

const sse = new EventSource('/api/sse/drafts');
sse.onmessage = (e) => {
    const data = JSON.parse(e.data);
    if (data.type === 'refresh' || data.draft_id) {
        if (Notification.permission === 'granted' && data.guest_name) {
            new Notification('New message — ' + data.guest_name, {
                body: (data.source || '') + ' • ' + (data.msg_type || ''),
            });
        }
        location.reload();
    }
};
sse.onerror = () => {
    sse.close();
    // Fallback: re-add 30s HTMX polling
    const poll = document.createElement('div');
    poll.setAttribute('hx-get', '/api/drafts');
    poll.setAttribute('hx-trigger', 'every 30s');
    poll.setAttribute('hx-swap', 'none');
    document.body.appendChild(poll);
    htmx.process(poll);
};
</script>
```

---

## Fix 5: Onboarding UX Polish

### Step 1: Update step 3 POST handler in app.py

```python
# In the step 3 POST handler:
if form.get("extra_services"):
    services = [s.strip() for s in form.getlist("extra_services")]
    cfg.extra_services = ",".join(services)
else:
    cfg.extra_services = ""

# Also append to food_menu for AI context (backward compat):
if services:
    cfg.food_menu = (cfg.food_menu or "") + "\nExtra services: " + ", ".join(services)
```

### Step 2: Update onboarding.html step 3

```html
<!-- Extra services checkboxes (pre-populated from cfg.extra_services) -->
{% set extra_svc = cfg.extra_services.split(',') if cfg.extra_services else [] %}

<fieldset>
  <legend>Extra Services</legend>
  {% for service in ['Airport transfer', 'Bike rental', 'Car rental', 'Laundry service', ...] %}
    <label>
      <input type="checkbox" name="extra_services" value="{{ service }}"
             {% if service in extra_svc %}checked{% endif %}>
      {{ service }}
    </label>
  {% endfor %}
</fieldset>

<!-- Import spinner (step 1) -->
<div id="import-spinner" style="display: none;">
  <p>Loading property details...</p>
  <img src="/static/spinner.gif" alt="Loading">
</div>

<script nonce="{{ request.state.csp_nonce }}">
// Show spinner during import
document.getElementById('import-btn')?.addEventListener('click', () => {
    document.getElementById('import-spinner').style.display = 'block';
});
</script>

<!-- IMAP error hints (step 5) -->
<div id="imap-error-area">
  <!-- Existing error text -->
  <details>
    <summary>Troubleshooting</summary>
    <ul>
      <li><strong>Gmail:</strong> Enable IMAP in Settings → Forwarding and POP/IMAP</li>
      <li><strong>2FA enabled:</strong> Use an App Password instead of your regular password</li>
      <li><strong>Other providers:</strong> Check support docs for IMAP settings</li>
    </ul>
  </details>
</div>
```

---

## Summary

All code is organized by fix and step. Review the [PLAN file](/Users/chandan/.claude/plans/fizzy-churning-journal.md) for architecture details.

**Next steps:**
1. Commit model/auth schema changes
2. Implement Fix 6 (billing) - foundation for all routes
3. Implement Fix 1 (team login) - enables member-scoped views
4. Implement Fix 2 (conversation view) - dashboard redesign
5. Implement Fix 3 (analytics) - KPI dashboard
6. Implement Fix 4 (SSE) - real-time notifications
7. Implement Fix 5 (onboarding) - polish
8. Test all changes end-to-end
9. Create PR
