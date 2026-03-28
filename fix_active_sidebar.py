import glob
import re

files = glob.glob('web/templates/*.html')
internal_files = []

# Filter to files that have the sidebar
for f in files:
    with open(f, 'r') as file:
        content = file.read()
    if '<aside' in content and '<nav class="flex-1 px-4 space-y-1">' in content:
        internal_files.append(f)

new_nav = """<nav class="flex-1 px-4 space-y-1">
<a class="flex items-center gap-3 px-6 py-3 {% if request.url.path == '/dashboard' %}text-[#5B8DEF] bg-[#5B8DEF]/10 border-r-2 border-[#5B8DEF]{% else %}text-slate-400 hover:text-white hover:bg-white/5{% endif %} transition-all duration-200 active:scale-[0.98]" href="/dashboard">
<span class="material-symbols-outlined" data-icon="dashboard">dashboard</span>
                Dashboard
            </a>
<a class="flex items-center gap-3 px-6 py-3 {% if request.url.path == '/conversations' %}text-[#5B8DEF] bg-[#5B8DEF]/10 border-r-2 border-[#5B8DEF]{% else %}text-slate-400 hover:text-white hover:bg-white/5{% endif %} transition-all duration-200 active:scale-[0.98]" href="/conversations">
<span class="material-symbols-outlined" data-icon="forum">forum</span>
                Conversations
            </a>
<a class="flex items-center gap-3 px-6 py-3 {% if request.url.path == '/reservations' %}text-[#5B8DEF] bg-[#5B8DEF]/10 border-r-2 border-[#5B8DEF]{% else %}text-slate-400 hover:text-white hover:bg-white/5{% endif %} transition-all duration-200 active:scale-[0.98]" href="/reservations">
<span class="material-symbols-outlined" data-icon="event_available">event_available</span>
                Reservations
            </a>
<a class="flex items-center gap-3 px-6 py-3 {% if request.url.path == '/activity' %}text-[#5B8DEF] bg-[#5B8DEF]/10 border-r-2 border-[#5B8DEF]{% else %}text-slate-400 hover:text-white hover:bg-white/5{% endif %} transition-all duration-200 active:scale-[0.98]" href="/activity">
<span class="material-symbols-outlined" data-icon="history">history</span>
                Activity
            </a>
<a class="flex items-center gap-3 px-6 py-3 {% if request.url.path == '/analytics' %}text-[#5B8DEF] bg-[#5B8DEF]/10 border-r-2 border-[#5B8DEF]{% else %}text-slate-400 hover:text-white hover:bg-white/5{% endif %} transition-all duration-200 active:scale-[0.98]" href="/analytics">
<span class="material-symbols-outlined" data-icon="analytics">analytics</span>
                Analytics
            </a>
<a class="flex items-center gap-3 px-6 py-3 {% if request.url.path == '/workflow' or request.url.path == '/billing' or request.url.path == '/ops' %}text-[#5B8DEF] bg-[#5B8DEF]/10 border-r-2 border-[#5B8DEF]{% else %}text-slate-400 hover:text-white hover:bg-white/5{% endif %} transition-all duration-200 active:scale-[0.98]" href="/workflow">
<span class="material-symbols-outlined" data-icon="hub">hub</span>
                Workflow Center
            </a>
<a class="flex items-center gap-3 px-6 py-3 {% if request.url.path == '/settings' %}text-[#5B8DEF] bg-[#5B8DEF]/10 border-r-2 border-[#5B8DEF]{% else %}text-slate-400 hover:text-white hover:bg-white/5{% endif %} transition-all duration-200 active:scale-[0.98]" href="/settings">
<span class="material-symbols-outlined" data-icon="settings">settings</span>
                Settings
            </a>
{% if tenant is defined and tenant and is_admin(tenant.email) %}
<a class="flex items-center gap-3 px-6 py-3 {% if request.url.path.startswith('/admin') %}text-rose-400 bg-rose-400/10 border-r-2 border-rose-400 font-bold{% else %}text-rose-400 hover:text-rose-300 hover:bg-white/5 font-bold{% endif %} transition-all duration-200" href="/admin">
<span class="material-symbols-outlined" data-icon="admin_panel_settings">admin_panel_settings</span>
                Admin
            </a>
{% endif %}
</nav>"""

for file_path in internal_files:
    try:
        with open(file_path, 'r') as f:
            content = f.read()
            
        nav_match = re.search(r'<nav class="flex-1 px-4 space-y-1">.*?</nav>', content, re.DOTALL)
        if nav_match:
            content = content.replace(nav_match.group(0), new_nav)
            with open(file_path, 'w') as f:
                f.write(content)
            print(f"Fixed active navigation classes in {file_path}")
    except Exception as e:
        print(f"Error processing {file_path}: {e}")
