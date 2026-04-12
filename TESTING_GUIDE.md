# Conversation Threading — Testing Guide

All code changes have been verified. Here's how to test the implementation live.

## ✅ Code Verification (Complete)

Run this command to verify all code is in place:

```bash
bash /Users/chandan/Desktop/BNB/verify_threading_implementation.sh
```

**Status:** All 34 checks passed ✅

---

## 🚀 Setup for Live Testing

### Step 1: Create .env Configuration

```bash
cd /Users/chandan/Desktop/BNB/airbnb-host/scripts
cp .env.example .env
```

Edit `.env` and fill in required values:

```bash
# Required: Your Anthropic API key
ANTHROPIC_API_KEY=sk-ant-...

# Required: Your WhatsApp number (E.164 format)
HOST_WHATSAPP_NUMBER=+1234567890

# Optional: Email configuration (if using email channel)
EMAIL_ADDRESS=your@email.com
EMAIL_PASSWORD=your-app-password

# Other settings (keep defaults or adjust as needed)
ROUTER_PORT=7771
WA_BOT_PORT=7772
```

### Step 2: Start All Services

```bash
cd /Users/chandan/Desktop/BNB/airbnb-host/scripts
./start.sh
```

You should see:
```
✅ response_router.py running on port 7771
✅ whatsapp/bot.js running on port 7772  
✅ email_watcher.py polling every 30s
```

If it's the first time with WhatsApp, you'll get a QR code in the terminal — scan it with WhatsApp.

### Step 3: Test API Endpoints (No Real Messages Needed)

In a separate terminal, run:

```bash
bash /Users/chandan/Desktop/BNB/test_conversation_threading.sh
```

This runs 7 automated tests that:
- Verify the `/classify` endpoint works
- Test context passing (thread_context parameter)
- Verify message history is stored
- Test multi-turn conversations (3+ messages)

---

## 👥 Manual Testing with Real Guest Messages

### Quick Test (2-3 minutes)

1. **Register a guest with their WhatsApp number:**
   ```
   Send to your host number: GUEST_WA +1234567890 John Doe
   ```

2. **Guest sends first message:**
   ```
   Guest: How do I adjust the air conditioning?
   ```

3. **Bot auto-replies** (routine message):
   ```
   Bot: The AC is controlled via the smart thermostat in the living room.
        You can set it between 68-76°F. 
        Feel free to ask if you need help with anything else!
   ```

4. **Guest sends follow-up:**
   ```
   Guest: What temperature should I set it to?
   ```

5. **Check the response:**
   - ✅ **Threading works if:** Bot acknowledges prior AC context
     ```
     The thermostat usually works best at 72°F. 
     As you can see on the display, you can adjust it...
     ```
   - ❌ **Threading broken if:** Bot treats it as fresh question
     ```
     The AC is controlled via the thermostat...
     ```

### Detailed Test (10-15 minutes)

Test both routine and complex messages:

**Test A: Routine Messages (Auto-Reply)**
```
Guest: What's the WiFi password?
→ Bot auto-replies with context
→ Guest asks: How long is the WiFi code?
→ Bot references prior WiFi question ✅
```

**Test B: Complex Messages (Host Approval)**
```
Guest: The shower isn't getting hot water
→ Bot flags as COMPLEX, sends draft to host
→ Host approves/edits via WhatsApp
→ Guest asks: How long will it take to fix?
→ Bot draft acknowledges shower issue ✅
```

**Test C: Multi-Turn Conversation**
```
Guest: Is there a gym nearby?
→ Bot responds with location
→ Guest: How far is it?
→ Bot responds, shows awareness of prior question
→ Guest: Does it have a pool?
→ Bot answers about pool, references earlier gym question ✅
```

---

## 📊 What to Check

### 1. Message History File

```bash
cat /Users/chandan/Desktop/BNB/airbnb-host/scripts/whatsapp/message_history.json | jq .
```

Should show conversation threads like:
```json
{
  "guest_booking_uid_123": [
    { "direction": "inbound", "text": "How do I adjust the AC?", "timestamp": "..." },
    { "direction": "outbound", "text": "The AC is controlled via...", "timestamp": "..." },
    { "direction": "inbound", "text": "What temperature should I set it to?", "timestamp": "..." }
  ]
}
```

### 2. Router Logs

Monitor in the terminal running `./start.sh`:

```
[router] Classified [routine] from John Doe...
  thread_context: [INBOUND] How do I adjust the AC?
                  [OUTBOUND] The AC is controlled via...
```

✅ **If you see thread_context being logged** = context is being passed

### 3. Bot Responses

Check if subsequent messages reference prior context:
- ✅ "As you mentioned earlier..."
- ✅ "You asked about the thermostat..."
- ✅ References specific details from first message
- ❌ Generic response that could be first message too

---

## 🐛 Debugging

### Services Not Starting

```bash
# Check if Python and Node are installed
python3 --version  # should be 3.8+
node --version     # should be 22+

# Check port conflicts
lsof -i :7771  # router port
lsof -i :7772  # bot port
```

### Messages Not Being Recorded

```bash
# Check if message_history.json exists
ls -lh /Users/chandan/Desktop/BNB/airbnb-host/scripts/whatsapp/message_history.json

# Check permissions
chmod 644 /Users/chandan/Desktop/BNB/airbnb-host/scripts/whatsapp/*.json
```

### Bot Not Responding to Messages

```bash
# Check guest is registered
cat /Users/chandan/Desktop/BNB/airbnb-host/scripts/whatsapp/guests.json | jq .

# Verify API key is set
echo $ANTHROPIC_API_KEY  # should show your key
```

### Context Not Being Used

Check logs for:
```
thread_context: [INBOUND]...  # if present, context is passed
                [OUTBOUND]...
```

If not present, context retrieval might be failing. Check:
1. `getConversationContext()` is being called
2. Message history file has prior messages
3. `booking_uid` is being tracked

---

## 📈 Success Metrics

| Metric | Expected | Check |
|--------|----------|-------|
| Message history records messages | ✅ | Check message_history.json grows |
| Context passed to Claude | ✅ | See thread_context in logs |
| Bot references prior messages | ✅ | "As you mentioned..." in replies |
| Follow-ups are contextual | ✅ | Avoid repeating prior answers |
| Multi-turn conversations work | ✅ | 3+ messages feel natural |

---

## 🎯 Expected Behavior After Update

### Before
```
Guest: How do I control the AC?
Bot:   The AC is controlled via the smart thermostat...

Guest: What temperature should I set it to?
Bot:   The AC is controlled via the smart thermostat...  ← REPEATS
```

### After
```
Guest: How do I control the AC?
Bot:   The AC is controlled via the smart thermostat...

Guest: What temperature should I set it to?
Bot:   A good setting is 72°F. As mentioned, the thermostat 
       is in the living room. ← REFERENCES PRIOR QUESTION
```

---

## 📝 Quick Checklist

- [ ] Code verification passed (34/34 tests)
- [ ] .env file created in airbnb-host/scripts/
- [ ] ANTHROPIC_API_KEY and HOST_WHATSAPP_NUMBER filled in
- [ ] Services started with `./start.sh`
- [ ] Test API endpoints with `test_conversation_threading.sh`
- [ ] Guest registered with WhatsApp number
- [ ] First message sent and bot replied
- [ ] Follow-up sent and bot referenced prior context
- [ ] Message history file has 2+ messages
- [ ] Router logs show thread_context parameter

---

## 🆘 Getting Help

If something doesn't work:

1. Check the troubleshooting section above
2. Review logs: `tail -f /tmp/router.log`
3. Verify all services are running: `curl http://127.0.0.1:7771/health`
4. Check message_history.json has expected data
5. Ensure .env has all required variables

---

## 📚 Reference

- **Code Changes:** `git show 441bc60`
- **Main Files Modified:**
  - `/airbnb-host/scripts/whatsapp/bot.js` — threading logic
  - `/airbnb-host/scripts/response_router.py` — context handling
  - `/web/models.py` — database schema update
  - `/airbnb-host/SKILL.md` — prompt instructions
