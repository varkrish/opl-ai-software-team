# PyInstaller hook for ai_software_dev_crew
# This file helps PyInstaller discover all modules in our project
# If you add new packages with dynamic imports, add them here

from PyInstaller.utils.hooks import collect_all, collect_submodules

# Collect all submodules of our project
# This ensures all project modules are included even if not directly imported
try:
    hiddenimports = collect_submodules('ai_software_dev_crew')
except:
    # If package not installed, fall back to empty list
    # PyInstaller will discover modules through import analysis
    hiddenimports = []

# If you add new third-party packages with dynamic imports, add them here:
# Example:
# try:
#     pkg_datas, pkg_binaries, pkg_hiddenimports = collect_all('new_package')
#     hiddenimports += pkg_hiddenimports
# except:
#     pass

