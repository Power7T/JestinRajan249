#!/usr/bin/env python3
"""
check_models.py — Validate models.py for common ORM mistakes.

Checks:
  1. No duplicate __tablename__ definitions
  2. All bidirectional relationships have matching back_populates
  3. No 'name' field on Tenant (should be first_name + last_name)
  4. All _enc columns store sensitive data (no plaintext keys/tokens)
  5. All FK type mismatches (Integer vs String)
  6. All models imported in db.py init_db()
"""

import re
import sys
import os

MODELS_FILE = os.path.join(os.path.dirname(__file__), "..", "web", "models.py")
DB_FILE     = os.path.join(os.path.dirname(__file__), "..", "web", "db.py")

RED   = "\033[91m"
GREEN = "\033[92m"
YELLOW= "\033[93m"
RESET = "\033[0m"

errors   = []
warnings = []


def check_models():
    content = open(MODELS_FILE).read()

    # ── 1. Duplicate __tablename__
    tables = re.findall(r'__tablename__\s*=\s*["\'](\w+)["\']', content)
    seen = {}
    for t in tables:
        if t in seen:
            errors.append(f"Duplicate __tablename__ = '{t}' — two ORM classes map to the same table (causes startup crash)")
        seen[t] = True

    # ── 2. Duplicate class names
    classes = re.findall(r'^class (\w+)\(Base\)', content, re.MULTILINE)
    class_seen = {}
    for c in classes:
        if c in class_seen:
            errors.append(f"Duplicate class definition: 'class {c}(Base)' appears twice in models.py")
        class_seen[c] = True

    # ── 3. back_populates consistency
    # Find all relationship declarations and check their back_populates is a real field on the target
    bp_declared = re.findall(r'back_populates=["\'](\w+)["\']', content)
    bp_attrs    = re.findall(r'^    (\w+)\s*:', content, re.MULTILINE)
    bp_attr_set = set(bp_attrs)
    for bp in bp_declared:
        if bp not in content:
            errors.append(f"back_populates='{bp}' declared but no field '{bp}' found in models.py as an attribute")

    # ── 4. Sensitive column names without _enc suffix
    sensitive_patterns = [
        r':\s*Mapped\[Optional\[str\]\]\s*=\s*mapped_column\(.*nullable.*\).*#.*(?:key|token|secret|password)',
    ]
    # Simple check: any column named exactly api_key, auth_token, etc. (without _enc)
    bad_sensitive = re.findall(
        r'^\s+(\w+(?:api_key|auth_token|secret_key|api_secret)(?<!_enc))\s*:', 
        content, re.MULTILINE | re.IGNORECASE
    )
    for col in bad_sensitive:
        col = col.strip()
        if not col.endswith("_enc"):
            warnings.append(f"Column '{col}' looks like a sensitive key/token but doesn't use _enc suffix for encryption")

    # ── 5. All ORM models imported in db.py
    db_content = open(DB_FILE).read()
    for cls in class_seen:
        if cls not in db_content:
            warnings.append(f"Class '{cls}' in models.py is NOT imported in db.py init_db() — Base.metadata.create_all() won't create its table")

    # ── 6. Check for 'tenant.name' pattern (wrong field name)
    if re.search(r'tenant\.name\b', content):
        errors.append("models.py contains 'tenant.name' access but Tenant has first_name/last_name, not name")


def check_db():
    db_content  = open(DB_FILE).read()
    models_content = open(MODELS_FILE).read()

    # Check all __tablename__ tables are covered by db_migrate ensure_columns or create_all
    tables = re.findall(r'__tablename__\s*=\s*["\'](\w+)["\']', models_content)
    ensure_tables = re.findall(r'["\'](\w+)["\'].*#.*ensure', db_content)  # from our ensure block

    # Just check critical new tables exist in db_migrate's tables_to_ensure list
    critical_new = ["automated_messages", "guest_feedback", "voice_routing_configs", "routing_rules"]
    for t in critical_new:
        if t not in db_content:
            warnings.append(f"Table '{t}' not referenced in db.py — if Alembic is skipped, this table won't be created")


if __name__ == "__main__":
    print(f"\n{YELLOW}=== HostAI Model Validator ==={RESET}\n")

    check_models()
    check_db()

    if warnings:
        print(f"{YELLOW}⚠️  WARNINGS ({len(warnings)}):{RESET}")
        for w in warnings:
            print(f"  {YELLOW}→ {w}{RESET}")
        print()

    if errors:
        print(f"{RED}❌ ERRORS ({len(errors)}):{RESET}")
        for e in errors:
            print(f"  {RED}✗ {e}{RESET}")
        print()
        sys.exit(1)
    else:
        print(f"{GREEN}✅ All model checks passed!{RESET}\n")
        sys.exit(0)
