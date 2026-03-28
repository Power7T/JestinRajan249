import re

files_to_fix = [
    "web/templates/conversations.html",
    "web/templates/settings.html",
    "web/templates/reservations.html",
    "web/templates/analytics.html"
]

with open("web/templates/dashboard.html") as f:
    dashboard_html = f.read()

aside_match = re.search(r'(<!-- SideNavBar Shell -->.*?</aside>)', dashboard_html, re.DOTALL)
if not aside_match:
    print("Could not find aside in dashboard")
    exit(1)
correct_aside = aside_match.group(1)

header_match = re.search(r'(<!-- TopNavBar Shell -->.*?</header>)', dashboard_html, re.DOTALL)
if not header_match:
    print("Could not find header in dashboard")
    exit(1)
correct_header = header_match.group(1)

for path in files_to_fix:
    with open(path) as f:
        content = f.read()
    
    # Replace aside
    new_content = re.sub(r'<aside.*?</aside>', correct_aside, content, flags=re.DOTALL)
    
    # Check if header exists, replace it, else prepend after aside
    if '<header' in new_content:
        new_content = re.sub(r'<header.*?</header>', correct_header, new_content, flags=re.DOTALL)
    else:
        # Prepend header right after aside
        new_content = new_content.replace(correct_aside, correct_aside + '\n' + correct_header)
        
    # Fix the guest_name index out of bounds error in conversations.html
    if 'conversations.html' in path:
        new_content = new_content.replace(
            "conversations[0].guest_name[0]|upper if conversations else",
            "conversations[0].guest_name[0]|upper if conversations and conversations[0].guest_name else"
        )
        new_content = new_content.replace(
            "conv.guest_name[0]|upper if conv.guest_name else",
            "conv.guest_name[0]|upper if conv.guest_name else"
        )
        
    with open(path, "w") as f:
        f.write(new_content)
    
    print(f"Fixed {path}")
