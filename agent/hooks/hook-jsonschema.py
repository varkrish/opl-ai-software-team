# PyInstaller hook for jsonschema
# Handle optional dependencies that might not be installed

from PyInstaller.utils.hooks import collect_all

# Collect jsonschema
hiddenimports = []

# Try to collect optional format checkers
optional_deps = [
    'rfc3987',
    'rfc3339_validator', 
    'webcolors',
    'jsonpointer',
    'uri_template',
    'isoduration',
    'fqdn',
    'idna',
]

for dep in optional_deps:
    try:
        __import__(dep)
        hiddenimports.append(dep)
    except ImportError:
        # Module not installed, skip it
        # jsonschema will handle the missing dependency gracefully
        pass

