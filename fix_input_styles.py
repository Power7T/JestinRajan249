import glob
import re

files = glob.glob('web/templates/*.html')

for file_path in files:
    try:
        with open(file_path, 'r') as f:
            content = f.read()

        # Find any CSS selector list for inputs that might be missing type=date or type=password
        # We'll just replace `.compat-layer input[type=text],.compat-layer input[type=email]` etc
        # with a generic selector that covers all text-like inputs.
        
        # A simple string replace for the start of these rules:
        content = content.replace(
            '.compat-layer input[type=text],.compat-layer input[type=email],.compat-layer select,.compat-layer textarea',
            '.compat-layer input:not([type=checkbox]):not([type=submit]):not([type=hidden]),.compat-layer select,.compat-layer textarea'
        )
        content = content.replace(
            '.compat-layer input[type=text],.compat-layer input[type=number],.compat-layer select,.compat-layer textarea',
            '.compat-layer input:not([type=checkbox]):not([type=submit]):not([type=hidden]),.compat-layer select,.compat-layer textarea'
        )
        content = content.replace(
            '.compat input[type=text],.compat input[type=email],.compat input[type=number],.compat select,.compat textarea',
            '.compat input:not([type=checkbox]):not([type=submit]):not([type=hidden]),.compat select,.compat textarea'
        )
        content = content.replace(
            '.settings-compat input[type=text],.settings-compat input[type=email],.settings-compat input[type=password],.settings-compat input[type=tel],.settings-compat select,.settings-compat textarea',
            '.settings-compat input:not([type=checkbox]):not([type=submit]):not([type=hidden]),.settings-compat select,.settings-compat textarea'
        )
        content = content.replace(
            'input[type=text],input[type=email],input[type=password],select,textarea',
            'input:not([type=checkbox]):not([type=submit]):not([type=hidden]),select,textarea'
        )

        with open(file_path, 'w') as f:
            f.write(content)
        print(f"Updated input styling in {file_path}")
    except Exception as e:
        print(f"Error touching {file_path}: {e}")
