#!/usr/bin/env python3
"""
check_migrations.py — Validate the Alembic migration chain before deploying.

Checks:
  1. No dangling down_revision references (KeyError crash)
  2. Exactly one head (no multiple-heads problem)
  3. Every migration-created table has a matching ORM model in models.py
  4. Every migration-added column has a matching field in models.py

Usage:
  python3 scripts/check_migrations.py        # run all checks
  python3 scripts/check_migrations.py --fast # skip model checks (just chain)
"""

import os
import re
import sys

VERSIONS_DIR = os.path.join(os.path.dirname(__file__), "..", "web", "alembic", "versions")
MODELS_FILE  = os.path.join(os.path.dirname(__file__), "..", "web", "models.py")

RED   = "\033[91m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
RESET = "\033[0m"

errors   = []
warnings = []

# ──────────────────────────────────────────────
# 1. Parse all migration files
# ──────────────────────────────────────────────
def parse_migrations():
    revmap = {}   # revision_id -> {file, down, creates_tables, adds_columns}
    for fname in os.listdir(VERSIONS_DIR):
        if not fname.endswith(".py") or fname.startswith("__"):
            continue
        content = open(os.path.join(VERSIONS_DIR, fname)).read()

        # Match   revision = 'abc123'  OR  revision: str = "abc123"
        rev_match  = re.search(r'^\s*revision\s*[=:][^=][^\n]*?["\']([^"\']+)["\']', content, re.MULTILINE)
        down_match = re.search(r'^\s*down_revision\s*[=:][^\n]+', content, re.MULTILINE)
        if not rev_match:
            continue

        rid  = rev_match.group(1).strip()
        dval = down_match.group(0).strip() if down_match else "None"

        # Extract all quoted strings as potential parent IDs
        parents = re.findall(r'["\']([a-zA-Z0-9_]{3,})["\']', dval)
        if "None" in dval and not parents:
            parents = []

        # Extract created tables
        creates = re.findall(r'create_table\s*\(\s*["\'](\w+)["\']', content)

        # Extract added columns (table, column)
        adds = re.findall(r"add_column\s*\(\s*['\"](\w+)['\"],\s*sa\.Column\s*\(['\"](\w+)['\"]", content)

        revmap[rid] = {
            "file":    fname,
            "down":    dval,
            "parents": parents,
            "creates": creates,
            "adds":    adds,
        }
    return revmap


# ──────────────────────────────────────────────
# 2. Check: no dangling parent references
# ──────────────────────────────────────────────
def check_chain(revmap):
    print("  Checking revision chain...")
    for rid, meta in sorted(revmap.items()):
        for parent in meta["parents"]:
            if parent not in revmap:
                errors.append(
                    f"Migration {meta['file']} references non-existent parent '{parent}' "
                    f"(down_revision = {meta['down']})"
                )


# ──────────────────────────────────────────────
# 3. Check: exactly one head
# ──────────────────────────────────────────────
def check_heads(revmap):
    print("  Checking for multiple heads...")
    all_parents = set()
    for meta in revmap.values():
        all_parents.update(meta["parents"])

    heads = [rid for rid in revmap if rid not in all_parents]
    if len(heads) > 1:
        errors.append(
            f"Multiple Alembic heads detected ({len(heads)}): {sorted(heads)}\n"
            f"  → Create a merge migration: alembic merge heads -m 'merge heads'"
        )
    elif len(heads) == 0:
        errors.append("No Alembic heads found — circular dependency in migration chain?")
    else:
        print(f"  {GREEN}✅ Single head: {heads[0]}{RESET}")


# ──────────────────────────────────────────────
# 4. Check: every created table has an ORM model
# ──────────────────────────────────────────────
def check_model_tables(revmap):
    print("  Checking migration tables against models.py...")
    models_content = open(MODELS_FILE).read()
    model_tables   = set(re.findall(r'__tablename__\s*=\s*["\'](\w+)["\']', models_content))

    # Tables that are intentionally absent from models (legacy / dropped)
    LEGACY_IGNORE = {"baileys_outbound", "baileys_callbacks"}

    for rid, meta in sorted(revmap.items()):
        for table in meta["creates"]:
            if table in LEGACY_IGNORE:
                continue
            if table not in model_tables:
                errors.append(
                    f"Migration {meta['file']} creates table '{table}' "
                    f"but no ORM model with __tablename__ = '{table}' found in models.py"
                )


# ──────────────────────────────────────────────
# 5. Check: every added column name exists in models.py
# ──────────────────────────────────────────────
def check_model_columns(revmap):
    print("  Checking migration columns against models.py...")
    models_content = open(MODELS_FILE).read()

    # Columns inherited from legacy / baileys we no longer maintain
    LEGACY_IGNORE_COLS = {
        "messaging_channel", "bot_api_token_expires_at", "bot_last_heartbeat",
        "baileys_max_batch_size", "baileys_max_per_minute",
    }

    for rid, meta in sorted(revmap.items()):
        for table, col in meta["adds"]:
            if col in LEGACY_IGNORE_COLS:
                continue
            if col not in models_content:
                errors.append(
                    f"Migration {meta['file']} adds column '{table}.{col}' "
                    f"but '{col}' not found anywhere in models.py"
                )


# ──────────────────────────────────────────────
# 6. Check: db_migrate() covers all migration-added columns
# ──────────────────────────────────────────────
def check_db_migrate_coverage(revmap):
    print("  Checking db.py ensure_columns coverage...")
    db_file = os.path.join(os.path.dirname(__file__), "..", "web", "db.py")
    db_content = open(db_file).read()

    LEGACY_IGNORE_COLS = {
        "messaging_channel", "bot_api_token_expires_at", "bot_last_heartbeat",
        "baileys_max_batch_size", "baileys_max_per_minute",
    }

    for rid, meta in sorted(revmap.items()):
        for table, col in meta["adds"]:
            if col in LEGACY_IGNORE_COLS:
                continue
            if col not in db_content:
                warnings.append(
                    f"Column '{table}.{col}' (added in {meta['file']}) "
                    f"is not in db_migrate() ensure_columns list — "
                    f"live DBs skipping Alembic won't get this column automatically"
                )


# ──────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────
if __name__ == "__main__":
    fast_mode = "--fast" in sys.argv

    print(f"\n{YELLOW}=== HostAI Migration Chain Validator ==={RESET}\n")

    revmap = parse_migrations()
    print(f"  Found {len(revmap)} migration files.\n")

    check_chain(revmap)
    check_heads(revmap)

    if not fast_mode:
        check_model_tables(revmap)
        check_model_columns(revmap)
        check_db_migrate_coverage(revmap)

    print()
    if warnings:
        print(f"{YELLOW}⚠️  WARNINGS ({len(warnings)}):{RESET}")
        for w in warnings:
            print(f"  {YELLOW}→ {w}{RESET}")
        print()

    if errors:
        print(f"{RED}❌ ERRORS ({len(errors)}) — fix before deploying:{RESET}")
        for e in errors:
            print(f"  {RED}✗ {e}{RESET}")
        print()
        sys.exit(1)
    else:
        print(f"{GREEN}✅ All migration checks passed!{RESET}\n")
        sys.exit(0)
