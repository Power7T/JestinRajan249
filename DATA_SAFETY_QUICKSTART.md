# Data Safety Quick Start

**TL;DR: You have 3 layers of protection against data loss. Here's how to use them.**

---

## 🚨 If You're About to Deploy

```bash
# 1. CREATE BACKUP
./scripts/create-backup.sh

# 2. DEPLOY
docker-compose pull && docker-compose up -d

# 3. VERIFY
curl http://localhost:8000/ping
docker-compose logs web | tail

# ✅ If something breaks, you can restore:
./scripts/restore-backup.sh --latest
```

---

## 🔍 Daily: Check Backups Are Working

```bash
# Run every morning
./scripts/check-backup-health.sh

# Expected output:
# ✅ Backup health check PASSED
# 📊 Backup count: 7 backups
# 📅 Latest backup age: 3 hours
```

**If it fails:**
```bash
docker-compose logs db-backup | tail -20  # See what went wrong
docker-compose exec db-backup ls -lh /backups/  # List backups
```

---

## 🚨 If Data is Lost/Corrupted

**Immediately:**
```bash
# STOP — don't write more data
docker-compose stop web worker

# Restore from backup
./scripts/restore-backup.sh --latest

# Verify it worked
docker-compose exec db psql -U hostai -c "SELECT count(*) FROM drafts;"

# Restart
docker-compose start web worker
```

**Recovery time: 5-10 minutes**

---

## 🗓️ Monthly: Test Recovery Procedure

```bash
# First of each month
./scripts/test-recovery.sh

# This:
# 1. Takes latest backup
# 2. Restores it to a test database
# 3. Verifies data is intact
# 4. Cleans up
# 5. Reports success/failure

# Expected: ✅ Recovery test PASSED
```

---

## 📋 Backup Lifecycle

| Day | Status | What To Do |
|---|---|---|
| Daily | ✅ Automated backup runs at 2 AM UTC | Nothing (automatic) |
| Before deploy | 🟡 Create manual backup | `./scripts/create-backup.sh` |
| Monthly (1st) | 🔍 Test recovery | `./scripts/test-recovery.sh` |
| After 7 days | 🗑️ Auto-deleted | Nothing (automatic) |

---

## 🛡️ The 3 Layers (Simple Version)

| Layer | What | How | Recovery Time |
|---|---|---|---|
| **Layer 1** | Daily automatic backups | Stored in Docker volume | 5-10 min |
| **Layer 2** | Database replica | Streaming copy of database | 2-5 min |
| **Layer 3** | Manual backups | You create before risky changes | 5-10 min |

---

## ❌ What Can Go Wrong & How to Fix It

### "Backup service not running"
```bash
docker-compose ps db-backup
# If status is not "Up", restart it:
docker-compose up -d db-backup
```

### "No backups in /backups/"
```bash
# Create one manually
./scripts/create-backup.sh

# Check the backup service logs
docker-compose logs db-backup --tail 50
```

### "Backup size is 0 bytes"
```bash
# Database connection failed during backup
# Check if database is running:
docker-compose exec db psql -U hostai -c "SELECT 1"

# Restart everything
docker-compose restart db
sleep 10
./scripts/create-backup.sh
```

### "Recovery test failed"
```bash
# Backup file is corrupted
# Try an older backup:
./scripts/restore-backup.sh  # List all backups
./scripts/restore-backup.sh hostai_20260323_020000.sql.gz  # Try older one
```

### "Volume is 90% full"
```bash
# Too many old backups hogging space
# They auto-delete after 7 days, but you can clean manually:
docker-compose exec db-backup bash -c "find /backups -mtime +7 -delete"
```

---

## 📞 Emergency Contacts

**If you're stuck:**

1. **Database is down:** `docker-compose restart db`
2. **Can't connect after restart:** Check `docker-compose logs db --tail 50`
3. **Nothing works:** Restore from backup with `./scripts/restore-backup.sh --latest`
4. **Still stuck:** Check DATA_SAFETY.md for detailed troubleshooting

---

## ✅ Your Checklist

- [ ] Backups running automatically (`./scripts/check-backup-health.sh`)
- [ ] Manual backup script tested (`./scripts/create-backup.sh`)
- [ ] Recovery script tested (`./scripts/restore-backup.sh`)
- [ ] Monthly recovery test scheduled (`./scripts/test-recovery.sh`)
- [ ] Team knows how to restore (`./scripts/restore-backup.sh --latest`)

---

**Bottom line:** You have automated daily backups + manual backup capability + recovery testing. Data loss requires multiple failures to happen simultaneously. You're protected. 🛡️
