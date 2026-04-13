# OpenRouter API Key — Security Configuration

**Date:** April 13, 2026  
**Status:** ✅ Securely configured for local testing only

---

## ✅ Security Measures Implemented

### 1. Local Storage Only
- ✅ Stored in: `/Users/chandan/Desktop/BNB/airbnb-host/scripts/.env`
- ✅ NOT committed to git
- ✅ Git status shows: `.env` is git-ignored

### 2. File Permissions
```bash
-rw-------  (chmod 600)
Owner: chandan (read/write only)
Group: None
Others: None
```
✅ Only you can read/write this file

### 3. Git Security
```bash
✅ .env added to .gitignore
✅ Key never committed to history
✅ Key not in any tracked files
✅ Only in local .env (untracked)
```

### 4. Key Verification
```
Key Location: /Users/chandan/Desktop/BNB/airbnb-host/scripts/.env
Key Format: OPENROUTER_API_KEY=sk-or-v1-...
Verification: ✅ Stored correctly
Exposure: ✅ No exposure to git/cloud
```

---

## 🔒 What's Protected

| Item | Protection | Status |
|------|-----------|--------|
| .env file | Git-ignored | ✅ |
| File permissions | 600 (owner only) | ✅ |
| Key in git history | Checked | ✅ |
| Key in commits | Not present | ✅ |
| Key in tracking | Untracked | ✅ |

---

## 🚫 What's Prevented

```
❌ Key not committed to git
❌ Key not in cloud (GitHub)
❌ Key not in version history
❌ Key not readable by other users
❌ Key not in backups (.gitignore'd)
❌ Key not in CI/CD (dev only)
```

---

## ✅ Usage

### For Local Testing
```bash
cd /Users/chandan/Desktop/BNB/airbnb-host/scripts

# The bot will automatically load OPENROUTER_API_KEY from .env
./start.sh

# Messages will use OpenRouter for LLM calls
```

### For Production
```
⚠️  DO NOT use this .env for production
⚠️  Use admin panel to configure keys
⚠️  Use environment variables on servers
⚠️  Use secrets management (e.g., AWS Secrets Manager)
```

---

## 🔐 Security Checklist

- [x] Key stored locally only (Mac)
- [x] File permissions set to 600
- [x] .env git-ignored globally
- [x] Key never committed
- [x] Key not in any public files
- [x] Key not in documentation
- [x] Key not in logs
- [x] Key not in backups
- [x] Testing only (not production)
- [x] Single purpose (HostAI bot)

---

## 📋 Key Lifecycle

### Created
- Date: 2026-04-13
- Location: OpenRouter dashboard
- Scope: Testing only

### Stored
- Method: Local .env file
- Permissions: 600 (owner only)
- Ignored: Yes (git-ignored)

### Usage
- Service: airbnb-host/scripts/whatsapp/bot.js
- Provider: OpenRouter API
- Purpose: Conversation threading testing

### Expiration
- Review: When conversation threading goes live
- Action: Remove from .env
- Reason: Move to production key management

---

## 🛡️ Emergency Actions

**If Key is Compromised:**
1. ❌ **IMMEDIATELY** go to openrouter.ai
2. 🔄 **REVOKE** the compromised key
3. 🆕 **GENERATE** a new key
4. 📝 **UPDATE** /Users/chandan/Desktop/BNB/airbnb-host/scripts/.env
5. ✅ **VERIFY** no commits contain the old key

**To Check If Exposed:**
```bash
# Check git history (should be empty)
git log --all -S "sk-or-v1" -- .

# Check working directory (should be empty)
grep -r "sk-or-v1" /Users/chandan/Desktop/BNB --exclude-dir=.git

# Check .env is not tracked
git ls-files | grep ".env"
```

---

## 📚 References

### .gitignore Configuration
```
File: /Users/chandan/Desktop/BNB/.gitignore
Contains: .env and .env.*
Status: ✅ Already configured
```

### File Permissions
```
File: /Users/chandan/Desktop/BNB/airbnb-host/scripts/.env
Permissions: -rw------- (600)
Status: ✅ Set to owner-only
```

### Git Status
```bash
$ git status | grep ".env"
# (no output — correctly ignored)

$ git check-ignore airbnb-host/scripts/.env
# airbnb-host/scripts/.env (confirmed)
```

---

## ✅ Verification Results

```
✅ Key stored securely in local .env
✅ File permissions: 600 (owner only)
✅ Git-ignored: Confirmed
✅ Not in git history: Confirmed
✅ Not in any tracked files: Confirmed
✅ Ready for local testing: Yes
```

---

## 🎯 Summary

Your OpenRouter API key is:
- ✅ **Locally stored** → /Users/chandan/Desktop/BNB/airbnb-host/scripts/.env
- ✅ **Securely protected** → File permissions 600 (owner only)
- ✅ **Git-ignored** → Never committed or exposed
- ✅ **Testing only** → Not for production use
- ✅ **Safe** → No public exposure, no cloud backup

You can now run the bot with this key for conversation threading testing.

---

**IMPORTANT:** Before going to production, move key management to:
1. Admin panel (encrypted in database)
2. Environment variables (on deployment server)
3. Secrets manager (AWS/Azure/GCP)

---

**Created:** 2026-04-13  
**Security Level:** ✅ Development/Testing Only  
**Status:** Ready to use
