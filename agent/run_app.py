#!/usr/bin/env python
"""
Entry point wrapper for PyInstaller.
This file uses absolute imports instead of relative imports.
"""

import sys
import os

# Add the package to the path for PyInstaller
if getattr(sys, 'frozen', False):
    # Running in a PyInstaller bundle
    bundle_dir = sys._MEIPASS
else:
    # Running in normal Python environment
    bundle_dir = os.path.dirname(os.path.abspath(__file__))

# Ensure the package can be imported
sys.path.insert(0, os.path.join(bundle_dir, 'src'))

# Now import and run the main function
from ai_software_dev_crew.main import run

if __name__ == "__main__":
    run()

