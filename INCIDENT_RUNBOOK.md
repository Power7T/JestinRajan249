# Incident Runbook — BNB Voice AI Assistant

**Last Updated:** 2026-03-30
**On-Call Contact:** ops@bnbplatform.com
**Status Page:** https://status.bnbplatform.com

---

## Quick Decision Tree

```
Is the site down?
├─ YES → Go to: Outage (Section 1)
└─ NO, but calls are failing?
    ├─ Twilio API errors → Go to: Twilio Issues (Section 2.1)
    ├─ Speech recognition failing → Go to: Deepgram Issues (Section 2.2)
    ├─ AI responses broken → Go to: OpenAI Issues (Section 2.3)
    ├─ Speech synthesis failing → Go to: ElevenLabs Issues (Section 2.4)
    └─ Slow/timing out → Go to: Timeouts (Section 3)

Is one tenant affected?
└─ YES → Go to: Single Tenant Incident (Section 4)
```

---

## 1. COMPLETE OUTAGE

**Signs:**
- `/api/calls/incoming` returning 500
- Dashboard not loading
- Most customers reporting no calls working

**Immediate Actions (First 5 minutes):**

1. **Check status of critical services:**
   ```bash
   # Database
   psql -h $DB_HOST -U $DB_USER -d $DB_NAME -c "SELECT 1"

   # Redis
   redis-cli -h $REDIS_HOST PING

   # Twilio
   curl -X GET "https://api.twilio.com/2010-04-01/Accounts/$TWILIO_ACCOUNT_SID" \
     -u "$TWILIO_ACCOUNT_SID:$TWILIO_AUTH_TOKEN"
   ```

2. **Check logs:**
   ```bash
   # Recent errors
   tail -100 /var/log/bnb-voice/app.log | grep -i error

   # Database connection issues
   grep "connection" /var/log/bnb-voice/app.log | tail -20
   ```

3. **Post to status page:**
   - "We're investigating reports of voice calling issues."
   - Estimated time: 30 minutes
   - Link: https://status.bnbplatform.com/incidents

4. **Notify team:**
   - Post in #incidents Slack channel
   - @mention on-call engineer if not already

**Investigation (5-30 minutes):**

- [ ] Database connectivity
- [ ] Redis connectivity
- [ ] Recent deployments (did something just ship?)
- [ ] External API status (Twilio, OpenAI, etc.)
- [ ] Disk space on servers
- [ ] Memory usage / OOM killer

**Recovery:**

- **If database is down:** Use your database provider's recovery dashboard (AWS RDS, Heroku, etc.)
- **If code issue:** Rollback last deployment: `git revert HEAD && deploy.sh`
- **If API quota:** Check Twilio/OpenAI dashboards, contact sales if needed

**Post-Incident:**

- [ ] Root cause analysis doc
- [ ] Update log monitoring thresholds
- [ ] Update alerting rules

---

## 2. PARTIAL OUTAGES (Service-Specific)

### 2.1 Twilio API Failures

**Signs:**
- Incoming calls go unanswered (Twilio webhooks not responding)
- Error logs: `TwilioRestException`
- Some tenants affected, others working fine

**Investigation:**

```bash
# Check Twilio status
curl https://status.twilio.com/api/v2/components.json | jq

# Check our Twilio credentials
echo $TWILIO_ACCOUNT_SID | wc -c  # Should be ~34 chars
echo $TWILIO_AUTH_TOKEN | wc -c   # Should be ~32 chars

# Check recent Twilio calls
SELECT COUNT(*) FROM voice_calls
WHERE status = 'failed' AND created_at > NOW() - INTERVAL '1 hour';
```

**Fixes:**

- **Auth issue:** Verify `TWILIO_ACCOUNT_SID` and `TWILIO_AUTH_TOKEN` in .env
- **Rate limited:** Check Twilio dashboard for rate limit alerts
- **Quota exceeded:** Upgrade Twilio account, contact Twilio sales
- **Service degradation:** Wait for Twilio to recover (usually < 30 min)

**Fallback:**

For now: No fallback. Document customer issue, offer callback when service recovers.

**Future:** Integrate with backup carrier (Bandwidth, Vonage).

---

### 2.2 Deepgram Speech-to-Text Failing

**Signs:**
- `Guest message = "[audio unclear]"` in all calls
- Error logs: `deepgram_error`, `timeout`
- Call transcripts empty

**Investigation:**

```bash
# Check Deepgram status
curl https://status.deepgram.com

# Check our API key
echo $DEEPGRAM_API_KEY | wc -c  # Should be ~40 chars

# Check usage/quota
curl -H "Authorization: Token $DEEPGRAM_API_KEY" \
  https://api.deepgram.com/v1/models
```

**Fixes:**

- **Auth issue:** Verify `DEEPGRAM_API_KEY` in .env
- **Rate limited:** Check Deepgram usage dashboard
- **Quota exceeded:** Upgrade Deepgram plan or add credits

**Fallback:**

The system already has one: If transcription fails, guest message defaults to `"[audio unclear]"` and AI responds with a fallback prompt. Monitor for patterns.

---

### 2.3 OpenAI API Failures

**Signs:**
- AI responses are generic/fallback: "Sorry, I'm having trouble understanding..."
- Error logs: `openai_error`, `timeout`
- Guest messages are transcribed but no AI response

**Investigation:**

```bash
# Check OpenAI status
curl https://status.openai.com

# Check our API key
echo $OPENAI_API_KEY | wc -c  # Should be ~48 chars

# Check recent OpenAI errors
SELECT COUNT(*) FROM api_usage_logs
WHERE service = 'openai' AND status = 'error'
AND created_at > NOW() - INTERVAL '1 hour';
```

**Fixes:**

- **Auth issue:** Verify `OPENAI_API_KEY` in .env
- **Rate limited:** Check OpenAI usage dashboard
- **Quota exceeded:** Add billing method or increase quota
- **Model deprecated:** Update model name in voice.py (gpt-4 → gpt-4-turbo)

**Fallback:**

System returns: "Sorry, I'm having trouble understanding right now. Could you please repeat that?"

---

### 2.4 ElevenLabs TTS Failing

**Signs:**
- Calls complete with transcript/AI response but no audio response
- Guest doesn't hear anything
- Error logs: `elevenlabs_error`

**Investigation:**

```bash
# Check ElevenLabs status
curl https://status.elevenlabs.io

# Check our API key
echo $ELEVENLABS_API_KEY | wc -c

# Check usage
curl -H "xi-api-key: $ELEVENLABS_API_KEY" \
  https://api.elevenlabs.io/v1/user
```

**Fixes:**

- **Auth issue:** Verify `ELEVENLABS_API_KEY` in .env
- **Character limit:** Check usage, may need upgrade
- **Quota exceeded:** Add credits or upgrade plan

**Fallback:**

Not implemented yet. Future: Fall back to simple text-to-speech (AWS Polly) or silent hang up.

---

## 3. TIMEOUTS & SLOWNESS

**Signs:**
- Calls drop after 30 seconds
- Error logs: `timeout`, `asyncio.TimeoutError`
- Guest says: "Call disconnected"

**Investigation:**

```bash
# Check API response times
SELECT service, AVG(duration_seconds) as avg_duration,
       MAX(duration_seconds) as max_duration
FROM api_usage_logs
WHERE created_at > NOW() - INTERVAL '1 hour'
GROUP BY service;

# Check slow database queries
# Enable query logging in PostgreSQL:
log_statement = 'all'
log_min_duration_statement = 1000  # Log queries > 1 second
```

**Fixes:**

- **Deepgram slow:** Usually < 2s. If > 5s, check network latency.
- **OpenAI slow:** Can be 3-8s during peak hours. Check OpenAI status.
- **Database slow:** Run `ANALYZE` on slow tables.

**Timeout Settings (in timeout_handler.py):**

```python
DEEPGRAM_TRANSCRIBE = 8.0  # seconds
OPENAI_GENERATE = 6.0      # seconds
ELEVENLABS_SYNTHESIZE = 5.0  # seconds
```

**Adjust if needed:**

```python
# If timeouts are too aggressive, increase them
TimeoutConfig.OPENAI_GENERATE = 10.0  # More lenient
```

---

## 4. SINGLE TENANT INCIDENT

**Signs:**
- One customer reports voice calls not working
- Other customers' calls working fine
- Guest contact lookups failing for that tenant

**Investigation:**

```bash
# Check tenant config
SELECT id, tenant_id, voice_enabled, voice_twilio_from_number
FROM tenant_configs
WHERE tenant_id = 'TENANT_ID';

# Check recent calls for tenant
SELECT id, status, created_at FROM voice_calls
WHERE tenant_id = 'TENANT_ID'
ORDER BY created_at DESC LIMIT 10;

# Check rate limit status
SELECT * FROM rate_limit_counters
WHERE tenant_id = 'TENANT_ID' AND expires_at > NOW();

# Check for cost overruns
SELECT SUM(cost_usd) as total_cost FROM api_usage_logs
WHERE tenant_id = 'TENANT_ID'
AND created_at > NOW() - INTERVAL '1 day';
```

**Possible Causes:**

1. **Tenant voice disabled:** Check `voice_enabled = FALSE` → enable in admin
2. **Wrong Twilio number:** Incoming call `To` number not matching `voice_twilio_from_number`
3. **Rate limited:** Check if tenant exceeded hourly/daily limits
4. **Cost cap hit:** Check if daily cost exceeded
5. **No guest contact:** Customer never set up GuestContact → guest matches as anonymous
6. **Bad iCal URL:** Check if Reservation sync failing

**Fixes:**

1. **Enable voice:**
   ```python
   cfg = db.query(TenantConfig).filter_by(tenant_id='TENANT_ID').first()
   cfg.voice_enabled = True
   db.commit()
   ```

2. **Fix Twilio number:**
   ```python
   cfg.voice_twilio_from_number = "+1-415-555-1234"
   db.commit()
   ```

3. **Reset rate limits:**
   ```python
   db.query(RateLimitCounter).filter_by(tenant_id='TENANT_ID').delete()
   db.commit()
   ```

4. **Increase cost cap:**
   ```python
   limit = db.query(TenantRateLimit).filter_by(tenant_id='TENANT_ID').first()
   limit.max_daily_cost_usd = 100
   db.commit()
   ```

---

## 5. DATA LOSS / CORRUPTION

**Signs:**
- Guest calls not appearing in history
- VoiceCall records missing
- Database integrity errors

**Recovery Steps:**

1. **Check database health:**
   ```bash
   VACUUM ANALYZE;
   REINDEX DATABASE;
   ```

2. **Restore from backup:**
   - Stop application: `systemctl stop bnb-voice-api`
   - Restore database: Use your provider's restore dialog (AWS, Heroku, etc.)
   - Verify backup: Check row counts match
   - Start application: `systemctl start bnb-voice-api`

3. **Notify affected customers:**
   - "We identified a data sync issue and restored from backup."
   - "Calls from [TIME] to [TIME] may not appear in history."
   - "We're investigating root cause."

---

## 6. SECURITY INCIDENT

**Signs:**
- Unauthorized API calls
- Rate limiting triggered from unknown source
- Suspicious SQL errors (injection attempts)

**Immediate Actions:**

1. **Isolate:** If confirmed compromise, take affected tenant offline
   ```python
   tenant = db.query(Tenant).filter_by(id='TENANT_ID').first()
   tenant.disabled_at = datetime.now()
   db.commit()
   ```

2. **Audit logs:**
   ```bash
   # Check for suspicious API patterns
   tail -1000 /var/log/bnb-voice/app.log | grep -i "error\|warn\|auth\|fail"
   ```

3. **Reset credentials:**
   - Regenerate API keys for affected tenant
   - Force password reset if applicable

4. **Notify:** Contact customer immediately

---

## 7. COST EXPLOSION

**Signs:**
- Daily API costs > $100
- OpenAI token usage spike
- Rate limit warnings

**Investigation:**

```bash
# Last 24 hours of costs
SELECT service, COUNT(*) as count, SUM(cost_usd) as total_cost
FROM api_usage_logs
WHERE tenant_id = 'TENANT_ID' AND created_at > NOW() - INTERVAL '1 day'
GROUP BY service
ORDER BY total_cost DESC;

# Per-call costs
SELECT call_id, SUM(cost_usd) as call_cost
FROM api_usage_logs
WHERE tenant_id = 'TENANT_ID' AND created_at > NOW() - INTERVAL '1 day'
GROUP BY call_id
ORDER BY call_cost DESC LIMIT 10;
```

**Fixes:**

1. **Rate limit the tenant:**
   ```python
   limit = db.query(TenantRateLimit).filter_by(tenant_id='TENANT_ID').first()
   limit.max_daily_cost_usd = 25  # Lower cap
   limit.voice_calls_per_hour = 10  # Lower limit
   db.commit()
   ```

2. **Investigate root cause:**
   - Is guest stuck in loop asking same question?
   - Is iCal feed broken, creating duplicate Reservations?
   - Is there a bot calling in repeatedly?

3. **Monitor going forward:**
   - Add Sentry alert for daily costs > $50
   - Dashboard widget showing costs by tenant

---

## 8. MONITORING & ALERTING

**What to Monitor:**

```
Critical (alert if down for > 5 min):
- Twilio API /Accounts/{SID} endpoint
- OpenAI /chat/completions endpoint
- PostgreSQL database connectivity
- Redis connectivity

High Priority (alert if > threshold):
- Failed calls (> 10% failure rate)
- API response time (> 5 seconds)
- Cost per day (> $50)
- Rate limit violations (> 2 per hour)

Medium Priority (alert if > threshold):
- Deepgram transcription accuracy (< 0.7 confidence)
- AI timeout rate (> 5%)
- Database query time (p95 > 1 second)
```

**Setup Sentry Alerts:**

```python
from sentry_sdk import capture_exception

try:
    response = await VoiceAIService.generate_response(...)
except asyncio.TimeoutError as e:
    capture_exception(e)
    alert("OpenAI timeout", severity="high")
```

**Setup Prometheus Metrics:**

```python
from prometheus_client import Counter, Histogram

voice_calls_total = Counter('voice_calls_total', 'Total calls', ['status'])
voice_call_duration = Histogram('voice_call_duration_seconds', 'Call duration')
api_cost_total = Counter('api_cost_total', 'Total API cost', ['service'])

voice_calls_total.labels(status='success').inc()
voice_call_duration.observe(duration_seconds)
api_cost_total.labels(service='openai').inc(cost_amount)
```

---

## 9. ESCALATION PATH

| Severity | Time | Owner | Action |
|----------|------|-------|--------|
| Critical (outage) | < 5 min | On-call | Page ops, CTO |
| High (partial) | < 15 min | On-call | Notify #incidents Slack |
| Medium (single tenant) | < 30 min | Support | Reach out to customer |
| Low (monitoring) | < 1 day | Eng | Log and plan fix |

---

## 10. POST-INCIDENT PROCESS

After any incident > 15 minutes:

1. **Document:** Create incident report with:
   - Timeline
   - Root cause
   - What alerted vs didn't alert
   - What worked vs didn't

2. **Fix:** Implement permanent fix (not just workaround)

3. **Improve:** Update monitoring/alerting to catch earlier

4. **Share:** Post-mortem meeting within 24 hours

---

## Contact & Resources

- **On-Call Slack:** #incidents
- **Status Page:** https://status.bnbplatform.com
- **Monitoring:** https://monitoring.bnbplatform.com
- **Logs:** https://logs.bnbplatform.com
- **Sentry:** https://sentry.bnbplatform.com

---

*Last Update: 2026-03-30*
*Next Review: 2026-04-30*
