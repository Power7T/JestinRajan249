# DIY Voice AI — Local Testing Guide

## Overview

The voice AI system is ready for local testing **without API keys**. It runs in **MOCK MODE** by default, simulating:
- Speech transcription (Deepgram STT)
- AI response generation (OpenAI LLM)
- Audio synthesis (ElevenLabs TTS)
- S3 storage

---

## 1. Install Dependencies

```bash
cd /Users/chandan/Desktop/BNB

# Install new voice-related packages
pip install -r web/requirements.txt

# Key new packages added:
# - deepgram-sdk (STT)
# - elevenlabs (TTS)
# - boto3 (AWS S3)
# - httpx (async HTTP)
```

---

## 2. Run Database Migration

```bash
# Apply the voice calling migration
alembic upgrade head

# This creates:
# - voice_calls table
# - Indexes on tenant_id, guest_phone, created_at
# - Adds voice_enabled, voice_phone_number to tenants table
```

---

## 3. Start Dev Server

```bash
# Enable MOCK MODE (default)
export VOICE_MOCK_MODE=true

# Start the server
uvicorn web.app:app --reload --port 8000

# You should see:
# INFO:     Application startup complete
# INFO:     Uvicorn running on http://127.0.0.1:8000
```

---

## 4. Test Voice Routes Locally (Mock Mode)

### A. Test Incoming Call Handler

```bash
# Simulate Twilio webhook for incoming call
curl -X POST http://localhost:8000/api/calls/incoming \
  -F "From=+1-415-555-1234" \
  -F "To=+1-650-555-6789" \
  -F "CallSid=CA1234567890abcdef1234567890abcdef"

# Expected response: TwiML XML with <Say> greeting and <Record> element
# Should see in logs: [VOICE] Incoming call from +1-415-555-1234 ...
```

### B. Test Speech Processing

```bash
# Simulate Twilio webhook after guest speaks
curl -X POST http://localhost:8000/api/calls/process-speech?call_id=YOUR_CALL_ID \
  -F "CallSid=CA1234567890abcdef" \
  -F "RecordingUrl=https://api.twilio.com/2010-04-01/Accounts/AC.../Recordings/RE123"

# Expected response: TwiML with:
# - <Play>https://mock-s3.example.com/voice_*.mp3</Play> (synthesized audio)
# - <Record> for next guest input
# Should see in logs:
# [MOCK] Transcribing audio from https://api.twilio.com/...
# [MOCK] Generating response for: What time can I check in?
# [MOCK] Synthesizing speech: Check-in is at 3 PM...
```

### C. Test Call Hangup

```bash
# Simulate call end webhook
curl -X POST http://localhost:8000/api/calls/hangup \
  -F "CallSid=CA1234567890abcdef" \
  -F "CallStatus=completed"

# Expected response: {"status": "logged"}
# Should see in logs:
# [VOICE] Call hangup: call_sid=CA1234567890abcdef, status=completed
# ActivityLog entry created with event_type="voice_call_completed"
```

### D. Test Outbound Call

```bash
# Initiate outbound call (admin feature)
curl -X POST http://localhost:8000/api/calls/send-voice \
  -d "tenant_id=YOUR_TENANT_ID" \
  -d "guest_phone=+1-415-555-1234" \
  -d "message=Hello! This is a test message from your host."

# Expected response: {"call_id": "CA...", "status": "initiated"}
# In logs:
# [MOCK] Synthesizing speech: Hello! This is a test message...
# [VOICE] Outbound call initiated: call_id=CA..., to=+1-415-555-1234
```

---

## 5. Verify Database Records

```bash
# Check VoiceCall records created during testing
sqlite3 web/bnb.db (or PostgreSQL if using that)

SELECT id, tenant_id, guest_phone_number, call_type, status, created_at
FROM voice_calls
ORDER BY created_at DESC
LIMIT 5;

# Should see your test calls:
# | 550e8400-e29b-41d4-a716-446655440000 | <tenant_id> | +1-415-555-1234 | incoming | completed | 2026-03-30 ... |
```

---

## 6. Verify Flow in Logs

You should see this flow in terminal logs:

```
[VOICE] Incoming call from +1-415-555-1234 to +1-650-555-6789, CallSid=CA1234567890abcdef
[VOICE] Call record created: id=550e8400-...
[VOICE] Processing speech for call_id=550e8400-..., recording_url=https://api.twilio.com/...
[MOCK] Transcribing audio from https://api.twilio.com/...
[VOICE] Transcribed: 'What time can I check in?' (confidence=0.95)
[MOCK] Generating response for: What time can I check in?
[VOICE] Generated response: 'Check-in is at 3 PM. You can enter using code 1234 at the main door.'
[MOCK] Synthesizing speech: Check-in is at 3 PM...
[MOCK] Uploading to R2: voice_550e8400-e29b-41d4-a716-446655440000.mp3
[R2] Uploaded: https://hostai-voice-calls.account-id.r2.cloudflarestorage.com/calls/voice_550e8400-e29b-41d4-a716-446655440000.mp3
[VOICE] Call hangup: call_sid=CA1234567890abcdef, status=completed
```

---

## 7. Test Via Browser/Dashboard (Future)

Once integrated into the dashboard UI:

```
1. Admin enables voice for a tenant:
   - Settings → Voice Calling → Enable
   - Enter Twilio phone number (+1-650-555-6789)

2. Guest calls the number
   - Receives greeting (mock: "Hello, welcome to our property...")
   - Speaks: "What time is checkout?"
   - Hears AI response (mock audio)
   - Can ask another question or hang up

3. Host sees call record in dashboard:
   - Voice Calls → Call logs
   - Duration: 45 seconds
   - Transcript: "What time is checkout? → Checkout is at 11 AM"
```

---

## 8. Switching to Real APIs (When You Get Keys)

### A. Set Up Cloudflare R2 (5 minutes)

**Why R2 instead of AWS S3?**
- 30% cheaper ($0.015/GB vs $0.023/GB)
- No egress fees (S3 charges $0.02/GB for downloads!)
- Same S3 API (easy to use)
- Free data transfer to guest devices

**Steps:**

```bash
# 1. Create Cloudflare account
#    Go to: cloudflare.com → Sign up

# 2. Create R2 bucket
#    Dashboard → R2 → Create bucket
#    Name: hostai-voice-calls
#    Region: Auto

# 3. Generate API credentials
#    R2 Dashboard → API Tokens → Create API Token
#    Copy:
#    - Access Key ID
#    - Secret Access Key
#    - Account ID (from Settings)

# 4. Update .env
CLOUDFLARE_ACCOUNT_ID=1234567890abcdef1234567890abcdef
CLOUDFLARE_ACCESS_KEY_ID=1234567890abcdef1234567890abcd
CLOUDFLARE_SECRET_ACCESS_KEY=xxxxxxxxxxxxxxxxxxxxxxxxxxx
CLOUDFLARE_R2_BUCKET=hostai-voice-calls
```

### B. Update .env with All API Keys

```bash
# .env (currently all disabled in MOCK MODE)
DEEPGRAM_API_KEY=dg_xxx...
OPENAI_API_KEY=sk-...
ELEVENLABS_API_KEY=sk_xxx...
TWILIO_ACCOUNT_SID=AC...
TWILIO_AUTH_TOKEN=xxxx...
TWILIO_PHONE_NUMBER=+1-650-555-6789

# Cloudflare R2 (instead of AWS S3!)
CLOUDFLARE_ACCOUNT_ID=your_account_id
CLOUDFLARE_ACCESS_KEY_ID=your_access_key
CLOUDFLARE_SECRET_ACCESS_KEY=your_secret_key
CLOUDFLARE_R2_BUCKET=hostai-voice-calls
```

### C. Disable Mock Mode

```bash
# In terminal, before running server:
export VOICE_MOCK_MODE=false

# Or in .env:
VOICE_MOCK_MODE=false

# Then restart server:
uvicorn web.app:app --reload --port 8000
```

### D. Use ngrok for Local Twilio Testing

```bash
# Install ngrok
brew install ngrok

# Start ngrok tunnel to your local server
ngrok http 8000

# Output:
# Forwarding                    https://xxxxx.ngrok.io -> http://localhost:8000

# Configure Twilio:
# 1. Go to Twilio Console → Phone Numbers
# 2. Set Webhook URL for Incoming Calls:
#    https://xxxxx.ngrok.io/api/calls/incoming
# 3. Set Webhook URL for Call Recording:
#    https://xxxxx.ngrok.io/api/calls/process-speech
# 4. Set Webhook URL for Call Hangup:
#    https://xxxxx.ngrok.io/api/calls/hangup

# Now test with real phone calls!
```

---

## 9. Troubleshooting

### Problem: "VoiceCall model not found"
```
Solution: Run alembic upgrade head
```

### Problem: "Module 'web.integrations.voice' not found"
```
Solution: Make sure web/integrations/__init__.py exists (create empty file if needed)
```

### Problem: "Recording URL is None"
```
Solution: In MOCK MODE, use any string as RecordingUrl. Real Twilio provides actual URLs.
```

### Problem: "R2 upload failed"
```
Solution: In MOCK MODE, R2 is mocked. When real Cloudflare keys added, verify:
- CLOUDFLARE_R2_BUCKET exists
- API credentials have Object Read/Write permissions
- CLOUDFLARE_ACCOUNT_ID is correct format (32 chars)
```

### Problem: "Twilio voice_response import error"
```
Solution: Twilio SDK is already in requirements.txt, just run:
pip install -r web/requirements.txt
```

---

## 10. API Endpoints Reference

| Endpoint | Method | Purpose | Test Command |
|----------|--------|---------|--------------|
| `/api/calls/incoming` | POST | Receive call from Twilio | curl (see step 4A) |
| `/api/calls/process-speech` | POST | Process guest speech | curl (see step 4B) |
| `/api/calls/hangup` | POST | Log call end | curl (see step 4C) |
| `/api/calls/send-voice` | POST | Initiate outbound call | curl (see step 4D) |
| `/api/calls/outbound-twiml` | GET | Generate TwiML for outbound | (auto-called) |

---

## 11. Database Schema

```sql
-- Voice calls table
CREATE TABLE voice_calls (
  id VARCHAR(36) PRIMARY KEY,
  tenant_id VARCHAR(36) NOT NULL,
  guest_contact_id VARCHAR(36),
  twilio_call_id VARCHAR(64) UNIQUE NOT NULL,
  twilio_phone_number VARCHAR(32),
  guest_phone_number VARCHAR(32),
  call_type VARCHAR(16),          -- incoming/outbound
  status VARCHAR(32),              -- ringing/answered/completed/failed
  guest_messages JSON,             -- [{"text": "...", "timestamp": "...", "confidence": 0.95}]
  ai_responses JSON,               -- [{"text": "...", "timestamp": "..."}]
  full_transcript TEXT,            -- Complete call transcript
  confidence_avg FLOAT,            -- Average confidence score
  sentiment VARCHAR(16),           -- positive/neutral/negative (for future)
  duration_seconds INTEGER,        -- Call length
  recording_url VARCHAR(512),      -- S3 URL to audio
  created_at TIMESTAMP WITH TIME ZONE,
  started_at TIMESTAMP WITH TIME ZONE,
  ended_at TIMESTAMP WITH TIME ZONE,
  FOREIGN KEY (tenant_id) REFERENCES tenants(id),
  FOREIGN KEY (guest_contact_id) REFERENCES guest_contacts(id)
);

-- Indexes
CREATE INDEX ix_voice_calls_tenant_id ON voice_calls(tenant_id);
CREATE INDEX ix_voice_calls_guest_phone ON voice_calls(guest_phone_number);
CREATE INDEX ix_voice_calls_created_at ON voice_calls(created_at);
```

---

## 12. Next Steps

1. ✅ Run migrations (`alembic upgrade head`)
2. ✅ Test all endpoints locally with curl
3. ✅ Verify database records
4. Get API keys:
   - Deepgram (deepgram.com)
   - OpenAI (platform.openai.com)
   - ElevenLabs (elevenlabs.io)
   - Twilio (twilio.com)
   - Cloudflare R2 (cloudflare.com → R2)
5. Set up Cloudflare R2:
   - Create account → Create bucket
   - Generate API credentials
   - Update .env with R2 keys
6. Update .env with all real API keys
7. Set `VOICE_MOCK_MODE=false` in .env
8. Test with ngrok + real Twilio phone number
9. Deploy to Railway
10. Configure Twilio webhooks to Railway URL
11. Test in production

---

## Questions?

Check the logs in `web/integrations/voice.py` - all functions log their steps with `[VOICE]` or `[MOCK]` prefixes for easy debugging.
