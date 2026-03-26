# 📊 Checking Service Status

This guide helps you monitor if your Airbnb Host pipeline is running correctly.

## Quick Status Check

### Method 1: Browser (Easiest) 🌐
**One-click status page:**
```bash
./open-status.sh
```
This opens your browser automatically to see all 4 services.

**Or manually:**
- Open your browser
- Go to: `http://localhost:7771/status?fmt=html`

You'll see:
- ✅ **Green** = running OK
- ⚠️  **Yellow** = running but disconnected
- ❌ **Red** = stopped/down

### Method 2: Terminal (For Developers) 💻
```bash
./status.sh
```

Shows color-coded output:
```
[OK]    Response Router
[OK]    WhatsApp Bot      (connected=true uptime=3600s)
[OK]    Email Watcher     (last poll 2m ago, total polls=240)
[OK]    Calendar Watcher  (last poll 30m ago, total polls=4)
```

---

## Understanding the Status Page

### The 4 Services

| Service | What it does | Status means |
|---------|-------------|-------------|
| **Router** | Central hub for AI drafts | ✅ Always OK if page loads |
| **WhatsApp Bot** | Sends messages to host phone | ✅ OK = connected, ⚠️ = disconnected, ❌ = offline |
| **Email Watcher** | Polls Airbnb emails | ✅ OK = polling, ⚠️ = stale (not polling) |
| **Calendar Watcher** | Monitors check-in/checkout | ✅ OK = watching, ⚠️ = not configured |

---

## Troubleshooting

### "Connection refused" when opening status page
**The router is not running.** Check the terminal where you ran `./start.sh` — you should see error messages.

**Fix:** Restart services
```bash
# Kill the running start.sh (Ctrl+C in the terminal)
# Then run again:
./start.sh
```

### Email Watcher shows "STALE"
**Email polling has stopped.** This could mean:
- IMAP server connection failed
- Email credentials are wrong
- Too many connection attempts (Gmail/Outlook rate limiting)

**Fix:** Check `.env` file for:
```
EMAIL_IMAP_HOST=imap.gmail.com       # correct host?
EMAIL_PASSWORD=your-app-password     # app password, not regular password?
```

### WhatsApp shows "Disconnected" but "running"
**Bot is online but WhatsApp connection dropped.**

**Fix:**
- Wait 30 seconds (auto-reconnect)
- If still disconnected, restart services: `./start.sh`
- On first run, scan the QR code shown in the terminal

### Calendar Watcher shows "not configured"
**This is OK!** It just means `AIRBNB_ICAL_URL` is not set in `.env`. If you want auto check-in reminders, add your Airbnb calendar URL.

---

## Auto-Recovery

The system **automatically restarts** any crashed service within **15 seconds**. You'll see messages like:

```
[watchdog] whatsapp bot died (PID 12345), restarting...
[watchdog] whatsapp bot restarted as PID 12346
```

No action needed — the watchdog handles it automatically.

---

## Need Help?

Check the logs in the terminal where you ran `./start.sh` for detailed error messages.
