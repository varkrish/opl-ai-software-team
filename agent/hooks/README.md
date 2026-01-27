# PyInstaller Hooks

This directory contains custom PyInstaller hooks for the `ai_software_dev_crew` project.

## Purpose

Hooks help PyInstaller discover modules that might be missed during automatic analysis, especially:
- Modules imported dynamically (using `__import__()`, `importlib`, etc.)
- Project modules that might not be directly imported
- Packages with plugin systems or lazy loading

## Adding Missing Imports

If you encounter `ModuleNotFoundError` at runtime after building:

1. **Check the warnings**: Look in `build/pyinstaller/warn-pyinstaller.txt` after building
2. **Add to hook file**: Edit `hook-ai_software_dev_crew.py` and add the missing module:
   ```python
   hiddenimports += ['missing_module_name']
   ```
3. **Rebuild**: Run the build script again

## Best Practices

- Only add modules that are actually needed and missing
- Let PyInstaller auto-detect most imports from your code
- Use `collect_all()` for packages with many dynamic imports
- Test the executable after adding new imports



