# Incident Postmortem — [Short Title]

**Date:** YYYY-MM-DD
**Duration:** HH:MM – HH:MM UTC (X hours Y minutes)
**Severity:** SEV1 / SEV2 / SEV3
**Status:** Resolved / Monitoring
**Author:** [Name]
**Reviewers:** [Names]

---

## Summary

One paragraph: what broke, how many users/tenants were affected, and how it was resolved.

---

## Impact

| Metric | Value |
|---|---|
| Tenants affected | X of Y |
| Duration | X minutes |
| Drafts lost / delayed | X |
| Revenue impact | $X (estimated) |
| Error rate peak | X% |

---

## Timeline (all times UTC)

| Time | Event |
|---|---|
| HH:MM | First alert / customer report received |
| HH:MM | On-call engineer paged |
| HH:MM | Root cause identified |
| HH:MM | Mitigation applied |
| HH:MM | Service restored |
| HH:MM | Incident closed |

---

## Root Cause

Describe the technical root cause. Be specific — "database was slow" is not a root cause; "Alembic migration left `alembic_version` in a failed state because revision ID exceeded VARCHAR(32)" is.

### Five Whys

1. **Why did [symptom] happen?** Because [cause 1].
2. **Why did [cause 1] happen?** Because [cause 2].
3. **Why did [cause 2] happen?** Because [cause 3].
4. **Why did [cause 3] happen?** Because [cause 4].
5. **Why did [cause 4] happen?** Because [root cause].

---

## What Went Well

- Item 1 (e.g., alert fired within 2 minutes)
- Item 2 (e.g., rollback was clean)

---

## What Went Badly

- Item 1 (e.g., no runbook for this failure mode)
- Item 2 (e.g., Redis being down masked the real error)

---

## Action Items

| Action | Owner | Due | Status |
|---|---|---|---|
| Add integration test for X | Name | YYYY-MM-DD | Open |
| Write runbook for Y | Name | YYYY-MM-DD | Open |
| Add alert for Z metric | Name | YYYY-MM-DD | Open |

---

## Lessons Learned

What would have caught this earlier? What would have made recovery faster?

---

*Postmortems are blameless. The goal is to make the system more resilient, not to assign fault.*
