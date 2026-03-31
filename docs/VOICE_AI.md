# Voice AI System

Complete documentation for HostAI's AI-powered voice calling system.

## Overview

HostAI Voice AI provides a complete phone system that:
- Answers incoming calls with AI (powered by OpenAI/Llama)
- Identifies guests automatically
- Provides property-specific information
- Transcribes conversations
- Escalates complex issues

## How It Works

```
1. Guest calls your Twilio number
   ↓
2. AI answers with personalized greeting
   (includes guest name, property info)
   ↓
3. Guest speaks their question
   ↓
4. Deepgram converts speech → text
   ↓
5. OpenAI/Llama analyzes with context
   ↓
6. ElevenLabs converts response → speech
   ↓
7. Guest hears answer
   ↓
8. Full transcript + recording saved
   ↓
9. Complex issues escalated to you
```

## Pricing Tiers

| Tier | Monthly | Minutes | Overage | Best For |
|------|---------|---------|---------|----------|
| Light | $39 | 100 | $0.049/min | Small properties |
| Standard | $79 | 300 | $0.049/min | Growing businesses |
| Professional | $129 | 750 | $0.049/min | Multi-property |
| Unlimited | $199 | Unlimited | Free | Enterprise |

## Features

### Guest Identification
- **Phone number**: Primary lookup (fastest)
- **Guest name**: Extracted from first message
- **Confirmation code**: From guest's email/booking
- **Fallback chain**: Phone → name → confirmation code

### Context Awareness
The AI knows:
- Guest name and history
- Room/unit number
- Property amenities
- House rules and policies
- Check-in/check-out times
- Previous conversations
- Reservation dates
- Special requests

### Multi-Language Support
- Auto-detects guest's language
- Responds in that language
- Supports 8+ languages

### Post-Call Features
- **Transcripts**: Full text with timestamps
- **Recordings**: Audio file stored securely
- **Summaries**: AI-generated call summary
- **Sentiment**: Positive/negative/neutral analysis
- **Tickets**: Auto-create support tickets for unanswered questions
- **Follow-up**: SMS/WhatsApp summary sent to guest

## Cost Optimization

HostAI uses OpenRouter for cost-efficient AI:

- **Complex decisions** (CE): Claude Sonnet ($3/1M tokens)
- **General tasks** (Llama 70B): $0.27/1M tokens
- **Simple checks** (Llama 8B): $0.05/1M tokens

Average cost per call: **$0.15-0.25** (including speech recognition and text-to-speech)

## Configuration

### Twilio Setup
1. Get Twilio account at https://twilio.com
2. Create phone number for incoming calls
3. Set webhook to: `https://yourapp.com/voice/incoming`
4. Add to `.env`: `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `VOICE_TWILIO_FROM_NUMBER`

### OpenRouter Setup
1. Get API key from https://openrouter.ai
2. Add to `.env`: `OPENROUTER_API_KEY`

### ElevenLabs Setup
1. Get voice ID from https://elevenlabs.io
2. Add to `.env`: `ELEVENLABS_API_KEY`, `ELEVENLABS_VOICE_ID`

### Deepgram Setup
1. Get API key from https://deepgram.com
2. Add to `.env`: `DEEPGRAM_API_KEY`

## API Reference

### Incoming Call Webhook
```
POST /voice/incoming

Twilio sends:
  From: caller phone number
  To: your Twilio number
  CallSid: unique call ID

Returns TwiML (voice instructions)
```

### Call Recording Webhook
```
POST /voice/recording

After call ends, Twilio sends:
  CallSid: call ID
  RecordingUrl: URL to recording file
  RecordingSid: recording ID
  Duration: length in seconds
```

## Database

### VoiceCall Table
```sql
id (UUID) - primary key
tenant_id - which customer
guest_contact_id - identified guest
reservation_id - associated booking
twilio_call_id - Twilio's call ID
guest_phone_number - caller phone
twilio_phone_number - your number
call_type - "incoming" or "outbound"
status - "ringing", "answered", "completed"
guest_messages - JSON array of guest messages
ai_responses - JSON array of AI responses
full_transcript - complete text
recording_url - link to audio file
sentiment - positive, neutral, negative
duration_seconds - call length
guest_name - identified guest name
created_at, started_at, ended_at - timestamps
```

### VoiceKnowledgeGap Table
```sql
id (UUID)
call_id - which call
tenant_id - which customer
question - what guest asked
status - "new", "resolved"
created_at
```

## Metrics & Analytics

**Tracked automatically:**
- Call volume (daily, weekly, monthly)
- Average call duration
- Call completion rate
- Sentiment distribution
- Cost per call
- Margin analysis by tier
- Response time metrics
- Escalation rate

**Available in admin panel** with:
- Date range filtering
- Property/tenant filtering
- CSV export
- PDF reports

## Example Interaction

```
Guest calls +1-555-0123 (your Twilio number)

SYSTEM: *Ring ring*
AI: Hi there! Thanks for calling The Beachside Property.
    This is Alex, your AI assistant.
    How can I help you today?

GUEST: "What's the WiFi password?"

AI: The WiFi network is 'BeachsideGuest' and the password
    is on the welcome card in your room. If you can't find
    it, let me know and I can resend it to you via text.

GUEST: "Thanks!"

AI: You're welcome! Is there anything else I can help
    with today?

GUEST: "No, that's all."

AI: Great! Have a wonderful stay at The Beachside Property!

[Call ends]

SYSTEM: Call recorded and transcribed
[SMS to guest: "Thanks for calling! Here's a summary..."]
[Admin notified: One call today, sentiment: positive]
```

## Troubleshooting

**No calls coming in?**
- Verify Twilio webhook URL in console
- Check firewall/networking
- Ensure HTTPS is working

**Poor transcription?**
- Guest may have accent or noise
- Deepgram is accurate 95%+ of time
- Manual transcript review in admin

**AI responses are generic?**
- Check property context is filled in
- Verify guest identification is working
- Test with known guest phone number

**High costs?**
- Switch to cheaper model (Llama 70B vs Claude)
- Check for long silence periods
- Review call duration patterns

## Best Practices

1. **Keep property info updated** - More context = better responses
2. **Test with your own phone** - Verify guest identification works
3. **Monitor sentiment** - Identify unhappy customers early
4. **Review knowledge gaps** - Unanswered questions = improvement opportunities
5. **Adjust pricing** - Use admin panel to optimize for your market

## Advanced Configuration

### Custom AI Instructions
Edit system prompt in `web/integrations/voice.py` to customize AI personality:
```python
system_prompt = f"""You are a helpful AI concierge for {property_name}.
Your name is {assistant_name}.
Be friendly, professional, and concise.
Always offer to escalate to a human if guest needs more help.
"""
```

### Fallback to Human
If AI can't handle question:
```
AI: "I'm not sure about that. Let me connect you with
    someone who can help. One moment please..."

[Transfers to on-call staff]
```

### Recording Consent
Before first use, get consent:
```
AI: "Thank you for calling! For quality purposes, this call
    will be recorded. By continuing, you consent to recording.
    Press 1 to continue or hang up."
```

## Security & Compliance

- **GDPR ready**: Consent tracking, data retention policies
- **CCPA ready**: Data export, deletion capability
- **PCI DSS**: No credit cards stored in calls
- **SOC 2**: Encryption, access controls, audit logging
- **HIPAA**: Not yet (but architecture supports it)

## Support

- **Twilio docs**: https://twilio.com/docs
- **ElevenLabs docs**: https://elevenlabs.io/docs
- **Deepgram docs**: https://developers.deepgram.com
- **OpenRouter docs**: https://openrouter.ai/docs
- **HostAI support**: GitHub Issues
