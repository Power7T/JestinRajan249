# Testing Daily-Cost Rate Limit Enforcement

## Deployment Status
- ✅ Code committed and pushed to main
- ⏳ GitHub Actions deploying to Railway
- Expected deployment time: 5-10 minutes
- Monitor at: https://meticulous-vibrancy-production.up.railway.app/

## Test 1: Verify Rate Limit Check Function (Unit Test)

**Location:** Test via Python REPL against the dev/test database

```python
from web.db import SessionLocal
from web.models import Tenant, TenantRateLimit
from web.rate_limiter import check_rate_limit

db = SessionLocal()

# Find or create a test tenant
tenant = db.query(Tenant).filter(Tenant.id == "test-tenant").first()
if not tenant:
    from web.auth import hash_password
    tenant = Tenant(
        id="test-tenant",
        email="test@example.com",
        password_hash=hash_password("test"),
        first_name="Test"
    )
    db.add(tenant)
    db.commit()

# Set a very low daily cost limit ($1)
rate_limit = db.query(TenantRateLimit).filter(
    TenantRateLimit.tenant_id == "test-tenant"
).first()
if not rate_limit:
    rate_limit = TenantRateLimit(
        tenant_id="test-tenant",
        max_daily_cost_usd=1.0
    )
    db.add(rate_limit)
    db.commit()

# Test 1a: Check should allow $0.50
result = check_rate_limit(db, "test-tenant", "daily_cost", cost_increment=0.50)
assert result["allowed"] == True, f"Should allow $0.50: {result}"
print("✓ Rate limit check allows $0.50")

# Test 1b: Check should block $1.50
result = check_rate_limit(db, "test-tenant", "daily_cost", cost_increment=1.50)
assert result["allowed"] == False, f"Should block $1.50: {result}"
assert "reason" in result, "Should include reason"
print(f"✓ Rate limit check blocks $1.50: {result['reason']}")

db.close()
```

## Test 2: Voice Path - Deepgram Transcription Limit

**Setup:**
1. Set a tenant's daily cost limit to $2.00
2. Prepare a Twilio test call

**Steps:**
1. Place a call to the tenant's voice number
2. Make 5 calls in succession
3. After ~4-5 calls (when cumulative cost exceeds $2.00), the next call should get the error

**Expected Result:**
```
Twilio TwiML Response:
<Say>Sorry, this service is temporarily unavailable. Your host has been notified.</Say>
<Hangup/>
```

**Verification:**
- ✓ Call is rejected with friendly error message
- ✓ Check `/admin/costs` dashboard - should see rate_limiter blocked event
- ✓ Check application logs for: `[VOICE] Daily cost limit exceeded for {tenant_id}`

## Test 3: Voice Path - LLM Response Generation Limit

**Setup:**
1. Same as Test 2, but with even lower limit ($0.50)

**Steps:**
1. Place first call - should get through Deepgram transcription
2. When guest says something, the LLM response generation should hit the limit

**Expected Result:**
```
Call gets through transcription but fails at LLM stage with same error message
```

**Verification:**
- ✓ First call completes (Deepgram OK)
- ✓ Second call fails at LLM stage (TwiML error)
- ✓ Logs show: `[VOICE] Daily cost limit exceeded... during generate_response`

## Test 4: Chat/SMS Path - Sentiment Analysis Limit

**Setup:**
1. Set tenant's daily cost limit to $0.10
2. Enable AI draft generation for this tenant

**Steps:**
1. Send SMS or WhatsApp message: "I'm very angry with this place!"
2. Watch admin cost dashboard

**Expected Result:**
- ✓ Draft is still created (with regex-based sentiment, not LLM)
- ✓ Sentiment defaults to "neutral" (fallback analysis)
- ✓ A rate_limiter event appears in `/admin/costs` with status="blocked"

**Verification:**
```sql
SELECT * FROM api_usage_logs 
WHERE tenant_id = 'your-tenant' 
  AND service = 'rate_limiter' 
  AND status = 'blocked'
ORDER BY created_at DESC
LIMIT 5;
```

## Test 5: Chat/SMS Path - Draft Generation Limit

**Setup:**
1. Lower tenant's daily cost limit further ($0.01)

**Steps:**
1. Send SMS: "Can I get a refund?"
2. Watch the draft that's created

**Expected Result:**
```
Draft text: "[Message couldn't be processed due to service limits. Please try again later.]"
```

**Verification:**
- ✓ Draft is created but with error message
- ✓ rate_limiter event logged
- ✓ Host sees that AI draft generation failed gracefully

## Test 6: Dashboard Display

**Location:** `/admin/saas-dashboard`

**Steps:**
1. Find your test tenant in the list
2. Scroll to the "Daily Cost" column

**Expected Results:**
- ✓ Shows current spend vs. limit (e.g., "$0.50 / $1.00")
- ✓ Shows status indicator:
  - GREEN if under 80%
  - YELLOW if 80-100%
  - RED/BLOCKED if exceeds 100%

## Test 7: Reset and Recovery

**Steps:**
1. In `/admin/system-config` or `/admin/saas-dashboard`, increase the test tenant's `max_daily_cost_usd` to $100
2. Try calling or sending a message again

**Expected Result:**
- ✓ Service immediately works again
- ✓ No restart required
- ✓ Next API call goes through successfully

## Monitoring During Testing

### Application Logs
```bash
railway logs --service meticulous-vibrancy | grep -i "rate_limit\|daily_cost\|VOICE\|SENTIMENT\|DRAFT"
```

### Cost Dashboard
- Visit: `https://meticulous-vibrancy-production.up.railway.app/admin/costs`
- Filter by service = "rate_limiter"
- Look for status = "blocked" events

### Database Query
```sql
-- Find all rate limit blocks in last hour
SELECT 
  tenant_id,
  service,
  operation,
  status,
  error_message,
  created_at
FROM api_usage_logs 
WHERE service = 'rate_limiter'
  AND created_at > NOW() - INTERVAL '1 hour'
ORDER BY created_at DESC;
```

## Troubleshooting

**Issue:** Rate limit isn't blocking calls
- [ ] Check that tenant's TenantRateLimit.max_daily_cost_usd is set (not null)
- [ ] Verify daily cost counter is incrementing (check RateLimitCounter table)
- [ ] Check logs for "check_rate_limit" calls

**Issue:** Calls blocked even with low spend
- [ ] Check if RateLimitCounter counter_id is correctly formatted: `{tenant_id}:daily_cost:{date}`
- [ ] Verify the cost estimates are reasonable (Deepgram: ~$0.004, LLM: ~$0.01)
- [ ] Check for timezone issues in window_start calculation

**Issue:** rate_limiter events not appearing in dashboard
- [ ] Verify log_rate_limit_blocked() is being called
- [ ] Check that APIUsageLog entries are being committed to database
- [ ] Ensure admin cost dashboard is querying the right table/filters

## Success Criteria

✅ All 7 tests pass
✅ Logs show rate_limiter blocks with clear reasons
✅ Dashboard displays correct status indicators
✅ Graceful fallback behavior (no crashes)
✅ Reset works immediately without restarts
