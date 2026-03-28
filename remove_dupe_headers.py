import glob
import re

files = [
    'web/templates/guest_contacts.html',
    'web/templates/workflow_center.html',
    'web/templates/billing.html',
    'web/templates/ops_queue.html',
    'web/templates/reservations.html',
    'web/templates/guest_timeline.html',
    'web/templates/analytics.html',
    'web/templates/settings.html',
    'web/templates/activity.html',
]

for file_path in files:
    try:
        with open(file_path, 'r') as f:
            content = f.read()
        
        # Remove <h1> inside compat-layer or settings-compat immediately
        # We find <div class="compat-layer"> or <div class="settings-compat">
        content = re.sub(
            r'(<div class="(?:compat-layer|settings-compat)">\s*)<h1>[^<]*</h1>',
            r'\1',
            content
        )
        
        # Remove any duplicated "Settings saved" block if it's already in the header
        # Settings has {% if saved %} ... {% endif %} inside settings-compat
        if 'settings.html' in file_path:
            content = re.sub(
                r'\{% if saved %\}\s*<div class="alert alert-success">[^<]*</div>\s*\{% endif %\}',
                r'',
                content,
                count=1 # replace the inner one
            )
            
        # In Reservations & Workflow Center, remove trailing section-lead paragraphs if they directly follow the <h1>
        content = re.sub(
            r'(<div class="compat-layer">\s*)<p class="section-lead">[^<]*</p>',
            r'\1',
            content
        )

        with open(file_path, 'w') as f:
            f.write(content)
        print(f"Fixed duplicate headers in {file_path}")
    except Exception as e:
        print(f"Error {file_path}: {e}")
