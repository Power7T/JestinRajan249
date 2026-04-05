#!/usr/bin/env python3
"""
check_templates.py — Validate all HTML templates for common UI/layout bugs.

Checks per template:
  1. Sidebar has mobile translate classes (-translate-x-full md:translate-x-0)
  2. Sidebar has id="sidebar" or id="admin-sidebar"
  3. Main content has md:ml-[260px] (not raw ml-[260px])
  4. Header has md: width prefix (not raw w-[calc(100%-260px)])
  5. Main content has pt-[80px] top padding
  6. Hamburger toggle button exists (md:hidden)
  7. No duplicate Material Symbols CDN link
  8. No white-flash CSS fallback vars (var(--surface, #fff))
  9. No raw <style> light-mode overrides inside dark templates
"""

import os
import re
import sys

TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "..", "web", "templates")

RED    = "\033[91m"
GREEN  = "\033[92m"
YELLOW = "\033[93m"
RESET  = "\033[0m"

# Pages that intentionally don't have a sidebar (public/auth pages)
SIDEBAR_EXEMPT = {
    "base.html", "error.html", "login.html", "signup.html",
    "onboarding.html", "forgot_password.html", "reset_password.html",
    "verify_email.html", "logout_confirm.html", "invite_accept.html",
    "design-system.html", "pricing.html", "privacy.html", "terms.html",
    "index.html", "feedback_form.html", "checkin.html",
}

errors   = {}  # page -> list of errors
warnings = {}  # page -> list of warnings

def add_error(page, msg):
    errors.setdefault(page, []).append(msg)

def add_warning(page, msg):
    warnings.setdefault(page, []).append(msg)


def check_template(fname, content):
    # Skip pages without sidebars
    if fname in SIDEBAR_EXEMPT:
        return
    # Skip pages with no aside at all
    if "<aside" not in content:
        return

    # 1. Sidebar mobile translate
    if "translate-x" not in content:
        add_error(fname, "Sidebar missing -translate-x-full md:translate-x-0 transition-transform — sidebar inaccessible on mobile")

    # 2. Sidebar ID
    if 'id="sidebar"' not in content and 'id="admin-sidebar"' not in content:
        add_error(fname, 'Sidebar missing id="sidebar" or id="admin-sidebar" — JS toggle won\'t work')

    # 3. Mobile hamburger toggle
    if "md:hidden" not in content:
        add_error(fname, "No hamburger button (md:hidden) — users can't open sidebar on mobile")

    # 4. Main content responsive margin
    if "ml-[260px]" in content and "md:ml-[260px]" not in content:
        add_error(fname, "Main content uses ml-[260px] without md: prefix — content displaced on mobile")

    # 5. Header responsive width
    if "w-[calc(100%-260px)]" in content and "md:w-[calc(100%-260px)]" not in content:
        add_error(fname, "Header uses w-[calc(100%-260px)] without md: prefix — header clips on mobile")

    # 6. Top padding consistency
    known_bad_paddings = re.findall(r'pt-(?:8|16|24|32|48|56|64|96)\b', content)
    if known_bad_paddings and "pt-[80px]" not in content:
        add_warning(fname, f"Non-standard top padding found: {set(known_bad_paddings)} — expected pt-[80px]")

    # 7. Duplicate CDN links
    mat_count = content.count("Material+Symbols+Outlined")
    if mat_count > 1:
        add_error(fname, f"Duplicate Material Symbols CDN link ({mat_count}x) — causes double font load")

    # 8. White-flash CSS vars
    bad_vars = ["var(--surface, #fff)", "var(--bg-2, #f5f5f5)", "var(--text-1, #000)", "var(--bg, white)"]
    for bv in bad_vars:
        if bv in content:
            add_error(fname, f"Light-mode CSS fallback '{bv}' causes white-flash in dark mode UI")

    # 9. md:relative on aside (breaks fixed positioning)
    if "<aside" in content and "md:relative" in content:
        add_error(fname, "Sidebar has 'md:relative' — breaks fixed layout, causes huge top gap")

    # 10. current_user in templates (wrong auth pattern)
    if "current_user" in content:
        add_error(fname, "Template uses 'current_user' — app uses 'tenant' variable, not Flask-Login")

    # 11. Jinja2 zero-division risk
    divide_unsafe = re.findall(r'\{\{[^}]*\s*/\s*\w+[^}]*\}\}', content)
    safe_divides  = re.findall(r'\{\{[^}]*if\s+\w+[^}]*/[^}]*\}\}', content)
    raw_divides   = [d for d in divide_unsafe if d not in safe_divides]
    if raw_divides:
        add_warning(fname, f"Potential Jinja2 zero-division (use '(a/b) if b else 0'): {raw_divides[:2]}")


if __name__ == "__main__":
    print(f"\n{YELLOW}=== HostAI Template Validator ==={RESET}\n")

    all_templates = [f for f in os.listdir(TEMPLATES_DIR) if f.endswith(".html")]
    print(f"  Checking {len(all_templates)} templates...\n")

    for fname in sorted(all_templates):
        content = open(os.path.join(TEMPLATES_DIR, fname)).read()
        check_template(fname, content)

    # Print results
    pages_with_errors = list(errors.keys())
    pages_with_warns  = [p for p in warnings if p not in pages_with_errors]

    for page in sorted(pages_with_errors):
        print(f"{RED}❌ {page}{RESET}")
        for e in errors[page]:
            print(f"   ✗ {e}")
        for w in warnings.get(page, []):
            print(f"   {YELLOW}⚠ {w}{RESET}")
        print()

    for page in sorted(pages_with_warns):
        print(f"{YELLOW}⚠  {page}{RESET}")
        for w in warnings[page]:
            print(f"   ⚠ {w}")
        print()

    total_errors = sum(len(v) for v in errors.values())
    total_warns  = sum(len(v) for v in warnings.values())

    if total_errors:
        print(f"{RED}❌ {total_errors} errors across {len(pages_with_errors)} pages{RESET}")
        sys.exit(1)
    elif total_warns:
        print(f"{YELLOW}⚠  {total_warns} warnings across {len(pages_with_warns)} pages — review before shipping{RESET}")
        sys.exit(0)
    else:
        print(f"{GREEN}✅ All templates clean!{RESET}\n")
        sys.exit(0)
