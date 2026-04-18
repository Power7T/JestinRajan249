# Daily-Cost Rate Limit Enforcement Implementation

## Summary
Implemented per-tenant daily-cost rate limit enforcement across all API-consuming hot paths. When a tenant exceeds their configured `max_daily_cost_usd` cap, services gracefully degrade instead of running unbounded.

## Changes Made

### 1. **web/app.py** - Voice Path Enforcement (3 checks)

**Line ~12617: Deepgram Transcription Check**
- Checks rate limit BEFORE calling `VoiceAIService.transcribe_audio()`
- Estimated cost: `estimate_cost("deepgram", "transcribe", duration_seconds=60)` (~$0.0043)
- On deny: Returns TwiML error: "Sorry, this service is temporarily unavailable. Your host has been notified."

**Line ~12843: OpenRouter LLM Check**
- Checks rate limit BEFORE calling `VoiceAIService.generate_response()`
- Estimated cost: ~400 input + 150 output tokens with gpt-4o-mini
- On deny: Returns TwiML error with graceful hangup

**Line ~12916: TTS Synthesis Check**
- Checks rate limit BEFORE calling `VoiceAIService.synthesize_speech()`
- Estimated cost: Detects provider (Google: $16/1M chars, ElevenLabs: $0.0003/char)
- On deny: Returns TwiML error with graceful hangup

### 2. **web/classifier.py** - Chat/SMS Path Enforcement (2 checks)

**Line ~328: Sentiment Analysis Check** (`analyze_sentiment_and_intent_llm`)
- Checks rate limit BEFORE calling OpenRouter sentiment model
- Estimated cost: ~200 input + 50 output tokens with gpt-4o-mini
- On deny: Falls back to regex-based sentiment analysis, logs rate_limiter event

**Line ~468: Draft Generation Check** (`generate_draft`)
- Checks rate limit BEFORE calling OpenRouter for draft generation
- Estimated cost: ~800 input + 300 output tokens with claude-3.7-sonnet
- On deny: Returns error message, logs rate_limiter event

### 3. **web/cost_tracker.py** - New Logging Function

**New Function: `log_rate_limit_blocked()`**
- Logs when a rate limit blocks an API call
- Creates APIUsageLog entry with:
  - `service="rate_limiter"`
  - `status="blocked"`
  - `cost_usd=0.0`
  - `error_message=<reason>`
- Visible on admin cost dashboard for audit trail

## Technical Details

### Rate Limit Check Function
```python
check_rate_limit(db, tenant_id, "daily_cost", cost_increment=estimated_cost)
# Returns: {
#   "allowed": bool,
#   "remaining": float,
#   "limit": float,
#   "resets_at": datetime,
#   "reason": str  # if not allowed
# }
```

### Behavior on Deny

**Voice Calls:**
- Returns TwiML with user-friendly error message
- Logs at WARN level: `[VOICE] Daily cost limit exceeded for {tenant_id}: {reason}`
- Hangup prevents further API calls
- No charges incurred for failed attempt

**Chat/SMS/WhatsApp:**
- Sentiment analysis falls back to regex-based analysis (neutral by default)
- Draft generation returns error message instead of AI draft
- Logs at WARN level: `[SENTIMENT]` or `[DRAFT]`
- Creates `APIUsageLog` row with status="blocked" for audit trail
- Host can still see that a message was received

## Database Tables Used

- `rate_limit_counters` (tracks cumulative daily_cost per tenant)
- `tenant_rate_limits` (stores max_daily_cost_usd cap per tenant)
- `api_usage_logs` (now includes rate_limiter events with status="blocked")

## Testing Checklist

1. ✅ Unit-level: Insert test tenant with $1 daily cap, verify check_rate_limit blocks $2 cost
2. ⏳ Voice path: Place Twilio test call, second request after cap exceeded gets TwiML error
3. ⏳ Chat/SMS: Send test message, verify rate_limiter event appears in `/admin/costs` dashboard
4. ⏳ Dashboard sanity: `/admin/saas-dashboard` shows tenant flipping from WARNING → BLOCKED
5. ⏳ Reset: Increase tenant's max_daily_cost_usd via admin form, verify next call succeeds

## Deployment Notes

- **No schema changes**: RateLimitCounter and TenantRateLimit already exist in models
- **No migrations required**: Daily-cost metric already supported in rate_limiter.py
- **Backward compatible**: Doesn't break existing voice_calls or external_api limits
- **Graceful degradation**: Services continue running with fallbacks, no crashes
- **Cost estimation**: Conservative estimates prevent false positives

## Future Enhancements (Out of Scope)

- Per-metric cost caps (e.g., separate cap for LLM vs. TTS)
- Alerting/email when tenant hits cap
- De-duplicate _normalize_phone helpers
