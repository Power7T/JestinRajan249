# HostAI — Adversarial Production Threat Analysis
> Conducted via Claude Opus 4.6 — deep adversarial audit across 10 dimensions
> Date: 2026-03-24

---

## FINDINGS

```
#  | DIM | FILE                          | ATTACK / FAILURE VECTOR                                          | LIKELIHOOD | SEVERITY
---|-----|-------------------------------|------------------------------------------------------------------|------------|--------
 1 | D1  | worker_manager.py:272-289     | Scheduled draft auto-send has no DB lock — double-send           | HIGH       | CRITICAL
 2 | D1  | pms_worker.py:495-524         | PMS auto-send doesn't recheck draft.status=="pending"            | HIGH       | CRITICAL
 3 | D1  | email_worker.py:644-661       | Email auto-send: SMTP fires, then mark approved — crash = resend | MEDIUM     | CRITICAL
 4 | D1  | security.py:146-148           | Redis INCR + EXPIRE not atomic — rate limit bypass under load    | MEDIUM     | HIGH
 5 | D1  | calendar_worker.py:131-237    | _state_fired/_fire_state read+write not atomic — dup drafts      | MEDIUM     | MEDIUM
 6 | D2  | classifier.py:340-396         | If OpenRouter down, generate_draft raises RuntimeError — no graceful degradation, draft lost | MEDIUM | HIGH
 7 | D2  | email_worker.py:487-493       | Escalation alert send fails → logged but not retried, host never knows | MEDIUM | HIGH
 8 | D3  | billing.py:327                | Bot token verification uses == not hmac.compare_digest           | MEDIUM     | HIGH
 9 | D3  | classifier.py:269-331         | Guest PII (name, phone, message) sent raw to OpenRouter          | HIGH       | HIGH
10 | D3  | security.py:223-225           | CSP has unsafe-inline for script-src — XSS bypasses CSP          | MEDIUM     | HIGH
11 | D4  | models.py:366 + app.py        | No phone normalization — +91/091/91 creates duplicate guests     | HIGH       | MEDIUM
12 | D4  | calendar_worker.py:154-156    | Checkin date (no TZ) + UTC now() — off-by-one day for non-UTC properties | MEDIUM | MEDIUM
13 | D4  | app.py:3785                   | CSV upload: no file size limit — unbounded read() → OOM          | MEDIUM     | HIGH
14 | D5  | crypto.py:19-21               | Dev mode: Fernet key regenerated per restart — encrypted data lost | LOW      | HIGH
15 | D5  | auth.py:34                    | SECRET_KEY fallback is static string "change-me…" in dev         | LOW        | MEDIUM
16 | D5  | billing.py:317                | Bot token stored as unsalted SHA256 — rainbow table vulnerable   | LOW        | MEDIUM
17 | D5  | security.py:70                | CSRF HMAC signature truncated to 16 hex chars (64 bits)          | LOW        | LOW
18 | D6  | All workers                   | No per-request correlation ID across web ↔ worker                | HIGH       | MEDIUM
19 | D6  | worker_manager.py             | No dead-letter queue — failed drafts silently lost               | MEDIUM     | HIGH
20 | D7  | classifier.py:269-396         | Guest messages sent to OpenRouter without consent/DPA — GDPR risk | HIGH      | HIGH
21 | D7  | models.py                     | No conversation TTL, no data deletion path for GDPR              | HIGH       | HIGH
22 | D7  | app.py:1335, email_worker     | Phone numbers logged in plaintext in application logs            | HIGH       | MEDIUM
23 | D8  | requirements.txt:15           | python-jose==3.3.0 — unmaintained, known CVE on alg confusion    | MEDIUM     | HIGH
24 | D8  | requirements.txt:21           | anthropic>=0.28.0 — unpinned upper bound                         | MEDIUM     | MEDIUM
25 | D8  | docker-compose.yml:51         | pgbouncer image bitnami/pgbouncer:1 — tag, not digest            | LOW        | MEDIUM
26 | D9  | app.py:3785, 5870             | No upload size limit on CSV/PDF — DoS via memory exhaustion      | MEDIUM     | HIGH
27 | D9  | pms_guesty/hostaway/lodgify   | requests library: no session pooling, new connection per call    | MEDIUM     | MEDIUM
28 | D9  | security.py:138               | In-memory rate limiter dict never pruned — slow memory leak      | LOW        | LOW
29 | D10 | docker-compose.yml:307-308    | DB backup uses pg_dump | gzip piped — silent corruption if pipe breaks | LOW  | HIGH
30 | D10 | All workers                   | No leader election — multiple worker containers = all race on same data | MEDIUM | CRITICAL
```

---

## RISK MATRIX — Top 10

```
                         S E V E R I T Y
                    LOW        MEDIUM       HIGH        CRITICAL
               ┌──────────┬───────────┬───────────┬────────────┐
    HIGH       │          │ #11 phone │ #9 PII    │ #1 double  │
  LIKELIHOOD   │          │ #22 logs  │ #20 GDPR  │   send     │
               │          │ #18 corr  │ #21 TTL   │            │
               ├──────────┼───────────┼───────────┼────────────┤
    MEDIUM     │ #28 meml │ #5 cal    │ #4 rate   │ #2 PMS     │
               │          │ #12 TZ    │ #6 OpenR  │ #3 email   │
               │          │ #27 conn  │ #7 escal  │ #30 leader │
               │          │           │ #8 timing │            │
               │          │           │ #10 CSP   │            │
               │          │           │ #13 OOM   │            │
               │          │           │ #23 jose  │            │
               │          │           │ #26 DoS   │            │
               ├──────────┼───────────┼───────────┼────────────┤
     LOW       │ #17 CSRF │ #15 KEY   │ #14 dev   │            │
               │          │ #16 bot   │ #19 DLQ   │            │
               │          │ #24 unpin │ #29 bkup  │            │
               │          │ #25 img   │           │            │
               └──────────┴───────────┴───────────┴────────────┘
```

---

## IMPLEMENTATION PLAN

### P0 — Stop the Bleeding (before next deploy)

- [x] Add `SELECT FOR UPDATE` on scheduled draft queries — `worker_manager.py` — **S** — fixes #1
- [x] Add `if draft.status != "pending": return` guard at top of `_execute_draft` — `app.py` — **S** — fixes #1, #2, #3
- [x] Use `hmac.compare_digest()` in `verify_bot_token` — `billing.py:327` — **S** — fixes #8
- [x] Add file size limit (10 MB) on CSV/PDF uploads — `app.py:3785, 5870` — **S** — fixes #13, #26
- [x] Replace `python-jose` with `PyJWT` (maintained, no alg confusion CVE) — `requirements.txt` — **M** — fixes #23

### P1 — Fix Within 1 Week

- [x] Atomicise rate limit: use Redis Lua script for INCR+EXPIRE — `security.py` — **S** — fixes #4
- [x] Add leader election (Redis lock) so only one worker container processes jobs — `worker_manager.py` — **M** — fixes #30
- [x] Strip/hash guest PII before sending to OpenRouter — `classifier.py` — **M** — fixes #9, #20
- [x] Add unique constraint on `calendar_states(tenant_id, state_key)` — migration — **S** — fixes #5
- [x] Add dead-letter table for failed drafts — `models.py` + workers — **M** — fixes #19
- [x] Add escalation retry queue (3 attempts with backoff) — `email_worker.py` — **S** — fixes #7
- [x] Phone normalisation using E.164 (strip to digits, ensure country code) — `app.py` + workers — **M** — fixes #11

### P2 — Fix Within 1 Month

- [x] Remove `unsafe-inline` from CSP; extract inline JS to `.js` files with nonces — templates + `security.py` — **L** — fixes #10
- [x] Add per-request `X-Request-ID` / correlation ID across web + worker — `app.py` + workers — **M** — fixes #18
- [x] Add GDPR data deletion endpoint (`/api/tenant/delete`) and conversation TTL — `app.py` + `models.py` — **L** — fixes #21
- [x] Redact phone numbers from application logs — all workers — **S** — fixes #22
- [x] Pin all dependency upper bounds; pin Docker images by digest — `requirements.txt`, `docker-compose.yml` — **S** — fixes #24, #25
- [x] Store tenant timezone; use it for calendar trigger calculations — `models.py` + `calendar_worker.py` — **M** — fixes #12
- [x] Add memory cleanup for in-memory rate limiter — `security.py` — **S** — fixes #28
- [x] Add `pg_dump` exit code check in backup container — `docker-compose.yml` — **S** — fixes #29
- [x] Fail loudly in dev if Fernet key is auto-generated (log WARN, persist to file) — `crypto.py` — **S** — fixes #14

---

## SCORECARD

| Dimension | Score | Notes |
|-----------|-------|-------|
| D1 — Temporal & Concurrency | **3/10** | No locks on auto-send, non-atomic dedup — the #1 production risk |
| D2 — Failure Blast Radius | **5/10** | Redis fallback exists, but OpenRouter failure kills draft gen entirely |
| D3 — Data Trust Boundary Violations | **6/10** | Tenant isolation excellent; webhook sigs good; but PII leaks to OpenRouter |
| D4 — Silent Data Corruption | **4/10** | Phone normalisation missing, timezone off-by-one, no upload limits |
| D5 — Cryptographic & Auth Weaknesses | **6/10** | JWT pinned to HS256 (good), bcrypt (good); but python-jose has CVEs, truncated CSRF sig |
| D6 — Operational Blindspots | **4/10** | No correlation IDs, no dead-letter queue, no alerting on send failures |
| D7 — Regulatory & Compliance | **2/10** | Guest PII sent to third-party AI unredacted, no GDPR delete, no TTL, no consent |
| D8 — Supply Chain & Dependency | **5/10** | Some deps unpinned, Docker tags not digests, python-jose unmaintained |
| D9 — Load & Degradation | **5/10** | Rate limiting exists but has bugs; no upload limits; no connection pooling on PMS |
| D10 — Recovery & Chaos Readiness | **5/10** | Daily backups exist; but no leader election, no tested restore, pipe failures silent |
| **OVERALL** | **4.5/10** | |

---

## FINAL LINE

**"This project will fail in production when two guests message simultaneously, triggering the same scheduled draft auto-send without a database lock — both workers call `_execute_draft()`, and the guest receives the same reply twice, destroying host credibility on the first busy weekend."**

The secondary failure mode is a GDPR complaint: a European guest discovers their name, phone number, and complaint about bed bugs was sent verbatim to OpenRouter's API (a US-based third-party), with no data processing agreement, no consent, and no way to request deletion.

---

## DETAILED FINDINGS BY DIMENSION

### D1 — Temporal & Concurrency

**#1 — Scheduled Draft Double-Send (CRITICAL)**
`worker_manager.py:272-289` — The watchdog loop queries due drafts with no `SELECT FOR UPDATE`. If two worker containers run (possible with `docker compose scale worker=2` or a crash-restart overlap), both fetch the same pending draft and both call `_execute_draft()`. The guest receives the same message twice. Fix: add `.with_for_update(skip_locked=True)` to the query.

**#2 — PMS Auto-Send Status Race (CRITICAL)**
`pms_worker.py:495-524` — After `adapter.send_message()` returns `True`, code re-queries the draft at line 498 but never checks `if draft.status != "pending"` before marking it approved. A host manual approval between send and re-query causes the message to be sent once (by worker) + marked approved once (by host) + the draft record is then double-written.

**#3 — SMTP Send Before DB Commit (CRITICAL)**
`email_worker.py:644-661` — SMTP fires at line 646, then `_mark_draft_approved()` commits at 647. If the process crashes or the DB is momentarily unavailable between send and commit, the draft remains `"pending"` and will be processed again on the next poll cycle.

**#4 — Non-Atomic Redis Rate Limit (HIGH)**
`security.py:146-148` — `INCR` followed by a conditional `EXPIRE` is two round-trips. A key can expire between the two calls, resetting the window mid-flight. Under concurrent load this allows slightly more than `max_requests` through. Fix: `SET key 1 EX window NX` then `INCR key` in a Lua script.

**#5 — Calendar State Read-Write Race (MEDIUM)**
`calendar_worker.py:131-237` — `_state_fired()` and `_fire_state()` are separate DB calls. Two threads can both read "not fired", both pass the check, and both insert, generating duplicate pre-arrival/checkout drafts. Fix: unique constraint on `(tenant_id, state_key)` and catch `IntegrityError`.

---

### D2 — Failure Blast Radius

**#6 — OpenRouter Outage = Total Draft Loss (HIGH)**
`classifier.py:340-396` — After 3 retries, `generate_draft()` raises `RuntimeError`. Callers in `email_worker`, `pms_worker`, `calendar_worker` catch this and `return` — silently dropping the inbound guest message. No queuing, no retry-later, no host alert. If OpenRouter has a 30-minute outage during peak hours, all guest messages during that window are silently discarded.

**#7 — Escalation Alert Silently Dropped (HIGH)**
`email_worker.py:487-493` — When a message is flagged as an escalation, an alert email is sent to the host. If SMTP fails, the exception is caught and logged at WARNING level only. The escalation draft IS saved, but the host has no push notification path — they may not check the dashboard. A guest threatening legal action could go unnoticed for hours.

---

### D3 — Data Trust Boundary Violations

**#8 — Bot Token Timing Attack (HIGH)**
`billing.py:327` — `hashlib.sha256(raw_token.encode()).hexdigest() == cfg.bot_api_token_hash` uses Python's `==` operator. This is not constant-time on CPython for strings (early exit on first differing character). An attacker making thousands of requests from the same process can measure response-time variance to recover the hash prefix. Fix: `hmac.compare_digest()`.

**#9 — Guest PII Sent Raw to OpenRouter (HIGH)**
`classifier.py:269-331` — The full guest message (`f"Message: {text}"`), guest name (`<guest_name>{guest_name}</guest_name>`), and guest phone + reservation details (injected into property context) are sent to OpenRouter verbatim. OpenRouter is a US-based commercial API aggregator. These calls are logged in OpenRouter's infrastructure. There is no anonymisation, no data minimisation, and no mechanism to delete those logs on request.

**#10 — CSP unsafe-inline Defeats XSS Protection (HIGH)**
`security.py:225` — `script-src 'self' https://unpkg.com 'unsafe-inline'` means any XSS payload injected into the page (e.g., via an unescaped guest name in a template) can execute arbitrary JavaScript. The CSP header provides zero protection for script injection. The code acknowledges this at lines 216-220 but has not been fixed.

---

### D4 — Silent Data Corruption

**#11 — Phone Number Deduplication Failure (MEDIUM)**
`models.py:366` stores `guest_phone` as `String(32)` with no normalisation. A guest's phone might be recorded as `+919876543210` from a PMS sync and `09876543210` from a CSV import. These create two separate reservation records. WhatsApp routing uses the raw stored value, so messages meant for one guest may go to neither. The `meta_sender.py:25` normalisation (strip `+`, space, `-`) is only applied at send time, not at ingestion.

**#12 — Timezone Off-By-One for Check-in Triggers (MEDIUM)**
`calendar_worker.py:154-156` constructs `checkin_dt = datetime(checkin.year, checkin.month, checkin.day, _DEFAULT_CHECKIN_HOUR, tzinfo=timezone.utc)`. If the property is in UTC+5:30 (India) and the host's Airbnb calendar stores dates in local time, a check-in "date" of Dec 15 is actually midnight IST = 18:30 UTC Dec 14. The pre-arrival trigger fires ~18 hours early.

---

### D5 — Cryptographic & Auth Weaknesses

**#14 — Fernet Key Regenerated on Dev Restart (HIGH)**
`crypto.py:19-21` — In `development` mode, if `FIELD_ENCRYPTION_KEY` is not set, `Fernet.generate_key()` generates a new random key on every app startup. Every encrypted field (IMAP passwords, WhatsApp tokens, Twilio secrets) becomes permanently unreadable after a restart. Developers will see silent empty strings from `decrypt()` (line 43 catches exceptions and returns `""`), making this failure completely invisible.

**#16 — Bot Token SHA256 Without Salt (MEDIUM)**
`billing.py:317` — `hashlib.sha256(raw.encode()).hexdigest()` stores an unsalted SHA256 of the token. If the `tenant_configs` table is exfiltrated, a GPU-based rainbow table can recover all bot tokens in minutes. Tokens are `secrets.token_urlsafe(32)` (256-bit entropy) so this is low-probability but violates defence-in-depth.

**#23 — python-jose Unmaintained + CVE Risk (HIGH)**
`requirements.txt:15` — `python-jose==3.3.0` last released 2022. There are known issues with algorithm confusion attacks in the `python-jose` library. The codebase pins the algorithm to `HS256` (`auth.py:35`), which mitigates the `alg:none` attack, but the library's lack of maintenance means future CVEs will not be patched. Replacement: `PyJWT` (actively maintained, equivalent API).

---

### D6 — Operational Blindspots

**#18 — No Correlation ID (MEDIUM)**
There is no `X-Request-ID` header propagated from the web tier to background workers. When a draft fails to send, the only identifier is `tenant_id` and a timestamp. Tracing why a specific guest message was dropped requires manually correlating log lines across the `web`, `worker`, and `scheduler` containers by timestamp — unreliable under concurrent load.

**#19 — No Dead-Letter Queue (HIGH)**
Failed drafts are logged at ERROR level and discarded. There is no `failed_drafts` table, no retry queue, no admin UI to inspect failed generation attempts. If OpenRouter is down for 30 minutes, there is no way to know how many messages were lost or which guests need a manual reply.

---

### D7 — Regulatory & Compliance

**#20 — GDPR: No Lawful Basis for Third-Party AI Processing (HIGH)**
`classifier.py:269-396` — EU Regulation 2016/679 Article 28 requires a Data Processing Agreement (DPA) with any third party that processes personal data on your behalf. OpenRouter receives guest names, phone numbers, complaint text, and reservation details. There is no indication that hosts are informed of this data flow, no DPA in place at the platform level, and no mechanism for a guest to exercise Art. 17 (right to erasure) against the OpenRouter logs.

**#21 — No Data Retention TTL or Deletion Path (HIGH)**
`models.py` — Activity logs, draft history, guest messages, and timeline events are stored indefinitely. There is no scheduled purge, no configurable retention policy, and no `/api/gdpr/delete` endpoint. A host whose guest requests erasure under GDPR has no way to comply via the product.

---

### D8 — Supply Chain

**#23 — python-jose (HIGH)** — see D5 above.

**#24 — Unpinned Ranges (MEDIUM)**
`anthropic>=0.28.0`, `openai>=1.54.3`, `stripe>=9.0.0`, `twilio>=9.0.0` — major version bumps on any of these can break the API silently (e.g., OpenAI SDK v2 changed method signatures). Pin to `>=X.Y, <X+1` for all critical dependencies.

**#25 — Docker Image Tags Not Digests (MEDIUM)**
`docker-compose.yml:51` — `bitnami/pgbouncer:1` and `nginx:1.27-alpine` use mutable tags. A tag can be silently re-pointed to a new image with a dependency vulnerability. Pin with `@sha256:...` digests for production images.

---

### D9 — Load & Degradation

**#26 — Unbounded File Uploads → OOM (HIGH)**
`app.py:3785, 5870` — `await csv_file.read()` and `await faq_pdf.read()` read the entire file into memory with no size check. A 500 MB CSV or a 1 GB PDF will exhaust the container's memory, potentially killing the web process. A single authenticated user can trigger this. Fix: check `Content-Length` header and reject early; wrap `read()` with a size limit.

**#27 — No HTTP Session Pooling in PMS Adapters (MEDIUM)**
`pms_guesty.py`, `pms_hostaway.py`, `pms_lodgify.py`, `pms_generic.py` — each API call creates a new `requests` connection (new TCP handshake + TLS negotiation). Under a multi-tenant PMS sync with 50 tenants polling every 5 minutes, this is 10+ outbound connections/second with no reuse. Fix: use `requests.Session()` per adapter instance.

---

### D10 — Recovery & Chaos Readiness

**#29 — Silent Backup Corruption (HIGH)**
`docker-compose.yml:307-308`:
```sh
pg_dump -h db -U hostai hostai | gzip > $FNAME && echo "Backup OK" || echo "Backup FAILED"
```
The `&&` checks the exit code of `gzip`, not `pg_dump`. If `pg_dump` exits with an error mid-stream, `gzip` still writes a partial (corrupt) file and exits 0. The backup is stored, logged as `"Backup OK"`, and silently unrestorable. Fix: use `set -o pipefail` or check `${PIPESTATUS[0]}`.

**#30 — No Leader Election for Workers (CRITICAL)**
`worker_manager.py` — The `docker-compose.yml` runs a single `worker` service, but nothing prevents `docker compose scale worker=2` or a crash-restart overlap where two worker instances are briefly alive. There is no Redis-based leader lock, no distributed lock on tenant processing, and no idempotency check. The result: every race condition in D1 is amplified. Fix: use `SET worker:leader <hostname> NX EX 60` in Redis, renewed every 30s.
