# -*- mode: python ; coding: utf-8 -*-

import os
from pathlib import Path
from PyInstaller.utils.hooks import collect_all, collect_submodules, collect_data_files

block_cipher = None

# Get the project root directory
project_root = Path(SPECPATH)

# Collect all data files that need to be bundled
datas = []

# Add prompts directory
prompts_dir = project_root / 'src' / 'ai_software_dev_crew' / 'prompts'
if prompts_dir.exists():
    datas.append((str(prompts_dir), 'ai_software_dev_crew/prompts'))

# Add web templates and static files
web_templates = project_root / 'src' / 'ai_software_dev_crew' / 'web' / 'templates'
web_static = project_root / 'src' / 'ai_software_dev_crew' / 'web' / 'static'
if web_templates.exists():
    datas.append((str(web_templates), 'ai_software_dev_crew/web/templates'))
if web_static.exists():
    datas.append((str(web_static), 'ai_software_dev_crew/web/static'))

# Add config files (YAML files)
config_dir = project_root / 'src' / 'ai_software_dev_crew' / 'config'
if config_dir.exists():
    datas.append((str(config_dir), 'ai_software_dev_crew/config'))

# Add crews config files
crews_config_dir = project_root / 'src' / 'ai_software_dev_crew' / 'crews' / 'config'
if crews_config_dir.exists():
    datas.append((str(crews_config_dir), 'ai_software_dev_crew/crews/config'))

# Collect all submodules for packages that use dynamic imports
# This automatically includes all submodules without hardcoding
# Only collect for packages known to have dynamic imports
# 
# To add a new package with dynamic imports:
#   new_pkg_datas, new_pkg_binaries, new_pkg_hiddenimports = collect_all('new_package')
#   datas += new_pkg_datas
#   binaries += new_pkg_binaries  
#   hiddenimports += new_pkg_hiddenimports
crewai_datas, crewai_binaries, crewai_hiddenimports = collect_all('crewai')
flask_datas, flask_binaries, flask_hiddenimports = collect_all('flask')
pydantic_datas, pydantic_binaries, pydantic_hiddenimports = collect_all('pydantic')
jsonschema_datas, jsonschema_binaries, jsonschema_hiddenimports = collect_all('jsonschema')
tiktoken_datas, tiktoken_binaries, tiktoken_hiddenimports = collect_all('tiktoken')

# Combine collected data files
datas += crewai_datas + flask_datas + pydantic_datas + jsonschema_datas + tiktoken_datas

# For our project modules, only include the root package
# PyInstaller will discover submodules through import analysis
# The hook file (hooks/hook-ai_software_dev_crew.py) also helps discover modules
# If any are missed, they'll appear in the warnings and can be added to the hook file
project_hiddenimports = ['ai_software_dev_crew']

# Combine hidden imports
# PyInstaller will auto-detect most third-party packages from actual imports
# We only explicitly collect packages with known dynamic imports
hiddenimports = (
    project_hiddenimports +
    crewai_hiddenimports +
    flask_hiddenimports +
    pydantic_hiddenimports +
    jsonschema_hiddenimports +
    tiktoken_hiddenimports
)

# Analysis phase - PyInstaller will auto-detect most dependencies from imports
a = Analysis(
    ['run_app.py'],
    pathex=['.', './src', './src/ai_software_dev_crew'],
    binaries=crewai_binaries + flask_binaries + pydantic_binaries + jsonschema_binaries + tiktoken_binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=['hooks'],  # Use custom hooks directory
    runtime_hooks=[],
    excludes=[
        'tkinter',
        'matplotlib',
        'IPython',
        'jupyter',
        'notebook',
        'pytest',
        'test',
        'tests',
        'tensorflow',  # Exclude TensorFlow - not needed and causes AVX warnings
        'tensorflow_core',
        'keras',
        'torch',  # Exclude PyTorch if present
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data,
          cipher=block_cipher)

# Create onefile executable (exclude_binaries=False means bundle everything)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='ai_software_dev_crew',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
