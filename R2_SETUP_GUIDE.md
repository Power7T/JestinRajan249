# Cloudflare R2 Setup Guide for Voice AI

## Why R2 Instead of AWS S3?

- **30% cheaper**: $0.015/GB vs $0.023/GB storage
- **No egress fees**: S3 charges $0.02/GB for downloads, R2 is free
- **S3-compatible**: Same API, easy to use
- **Built-in CDN**: Free global distribution
- **Simple pricing**: No hidden charges

**Cost Comparison (1000 calls/month):**
```
AWS S3:  $1.30/month
R2:      $0.45/month
Savings: 66% cheaper
```

---

## Step 1: Create Cloudflare Account

1. Go to **https://cloudflare.com**
2. Click "Sign up"
3. Enter email and password
4. Verify email
5. You're done!

No credit card required for account creation.

---

## Step 2: Create R2 Bucket

1. Go to **Cloudflare Dashboard**: https://dash.cloudflare.com/
2. Left sidebar → **R2**
3. Click **"Create bucket"**
4. Fill in:
   ```
   Bucket name: hostai-voice-calls
   Region: Auto (or pick closest to you)
   ```
5. Click **"Create bucket"**
6. Done!

---

## Step 3: Generate API Credentials

### A. Create API Token

1. Dashboard → **R2**
2. Click **"API Tokens"** (top right)
3. Click **"Create API Token"**
4. Fill in:
   ```
   Token name: hostai-voice
   Expiration: Recommended (90 days)
   TTL: Recommended
   ```
5. Under "Permissions":
   - Select "Object Read & Write"
   - Select "All buckets" or "hostai-voice-calls"
6. Click **"Create Token"**
7. Copy:
   - **Access Key ID** (looks like: 1234567890abcdef)
   - **Secret Access Key** (looks like: xxxxxxxxxxxxxxxxxxx)
8. Save these in a secure place!

⚠️ **You won't be able to view the secret key again, so copy it now!**

### B. Get Account ID

1. Dashboard → **R2**
2. Click **Settings** tab
3. Look for **"Account ID"** (32-character string)
4. Copy it

Example:
```
Account ID: 1234567890abcdef1234567890abcdef
```

---

## Step 4: Update .env File

Open `.env` in your project and add/update:

```bash
# Cloudflare R2 (replaces AWS S3)
CLOUDFLARE_ACCOUNT_ID=1234567890abcdef1234567890abcdef
CLOUDFLARE_ACCESS_KEY_ID=1234567890abcdef1234567890abcd
CLOUDFLARE_SECRET_ACCESS_KEY=xxxxxxxxxxxxxxxxxxxxxxxxxxx
CLOUDFLARE_R2_BUCKET=hostai-voice-calls

# Disable mock mode to use real APIs
VOICE_MOCK_MODE=false
```

**Where to get each value:**
| Variable | Where to Find |
|----------|---------------|
| `CLOUDFLARE_ACCOUNT_ID` | R2 Settings tab |
| `CLOUDFLARE_ACCESS_KEY_ID` | API Token creation response |
| `CLOUDFLARE_SECRET_ACCESS_KEY` | API Token creation response |
| `CLOUDFLARE_R2_BUCKET` | Bucket name you created |

---

## Step 5: Test Connection

```bash
# Restart your app
uvicorn web.app:app --reload --port 8000

# Test upload (curl command from VOICE_AI_LOCAL_TESTING.md)
curl -X POST http://localhost:8000/api/calls/process-speech?call_id=test-123 \
  -F "CallSid=CA1234567890abcdef" \
  -F "RecordingUrl=https://api.twilio.com/2010-04-01/Accounts/AC.../Recordings/RE123"
```

**Expected in logs:**
```
[R2] Uploaded: https://hostai-voice-calls.1234567890abcdef.r2.cloudflarestorage.com/calls/voice_xxx.mp3
```

---

## Step 6: Enable Public Access (Optional)

If you want guests to access recordings via browser:

1. Dashboard → **R2** → **hostai-voice-calls**
2. Click **Settings**
3. Under "Public Access":
   - Toggle **"Allow public access"**
   - This lets anyone access files via URL
4. Save

Public URL format:
```
https://hostai-voice-calls.{account-id}.r2.cloudflarestorage.com/calls/voice_xxx.mp3
```

⚠️ **Keep private if guests shouldn't access recordings directly**

---

## Step 7: Set Up Billing Alerts (Optional but Recommended)

1. Dashboard → **Billing** → **Notifications**
2. Click **"Add a notification"**
3. Set threshold: **$1.00**
4. You'll get email if charges exceed $1/month
5. This prevents surprise bills

For voice testing, your costs will be ~$0.01-0.04/month (basically free).

---

## File Organization in R2

Your audio files will be organized like:

```
hostai-voice-calls bucket:
├── calls/
│   ├── voice_550e8400-e29b-41d4-a716-446655440000.mp3
│   ├── voice_a1f9c8d2-3b5e-4c7a-9f2e-1d8c6e4f3a2b.mp3
│   ├── voice_xyz...mp3
│   └── ...more audio files
```

Files automatically stay organized by date/call.

---

## Troubleshooting

### Problem: "Access Key Invalid"
```
Solution:
1. Check you copied entire access key (no spaces)
2. Verify you selected "Object Read & Write" permissions
3. Check bucket name matches (case-sensitive)
4. Regenerate API token if still failing
```

### Problem: "Bucket Not Found"
```
Solution:
1. Verify bucket name in .env matches exactly: hostai-voice-calls
2. Bucket names are case-sensitive
3. Bucket must be in same account
```

### Problem: "Quota Exceeded"
```
Solution:
1. R2 has no quota limits for testing
2. You may have AWS S3 limits if switching from S3
3. Just means your testing is going great!
```

### Problem: "403 Forbidden"
```
Solution:
1. API credentials don't have correct permissions
2. Regenerate API token with "Object Read & Write"
3. Make sure permissions include your bucket
```

---

## Cost Tracking

Monitor your R2 usage:

1. Dashboard → **Billing** → **Overview**
2. Look for **"R2"** line item
3. Shows:
   - Storage used
   - Requests made
   - Current month's charges

**Expected costs for testing:**
```
100 test calls (0.5GB):   ~$0.008
1000 calls/month (5GB):   ~$0.08
10000 calls/month (50GB): ~$0.75
```

All well under $1/month for testing!

---

## Migration from AWS S3 (If You Had It)

If you were using AWS S3 before:

```python
# Old code:
s3_client = boto3.client('s3',
    region_name='us-east-1',
    aws_access_key_id=AWS_KEY,
    aws_secret_access_key=AWS_SECRET
)

# New code (R2):
r2_client = boto3.client('s3',
    region_name='auto',
    endpoint_url='https://account-id.r2.cloudflarestorage.com',
    aws_access_key_id=CF_KEY,
    aws_secret_access_key=CF_SECRET
)
```

**The rest of the code stays the same!** R2 is S3-compatible.

---

## Advanced: Using Custom Domain

Want to use your own domain for audio files?

1. Dashboard → **R2** → **hostai-voice-calls**
2. Settings → **Custom Domains**
3. Click **"Add domain"**
4. Enter: **voice.yourdomain.com**
5. Follow DNS setup instructions
6. Audio URLs will be: `https://voice.yourdomain.com/calls/voice_xxx.mp3`

(This is optional, the default R2 URL works fine)

---

## Support

For R2 issues:
- Cloudflare Docs: https://developers.cloudflare.com/r2/
- Pricing: https://www.cloudflare.com/en-gb/products/r2/pricing/
- Status: https://www.cloudflarestatus.com/

---

## Summary

✅ Created Cloudflare account
✅ Created R2 bucket (hostai-voice-calls)
✅ Generated API credentials
✅ Updated .env file
✅ Ready to test voice calling!

Your voice AI system now uses R2 for 30% cheaper audio storage with no egress fees.
