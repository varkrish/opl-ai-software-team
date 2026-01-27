#!/usr/bin/env bash

# Packaging script for macOS/Linux
# Ensure pyinstaller is installed: pip install pyinstaller

set -e

echo "🔨 Building standalone executable for macOS/Linux..."

# Clean previous builds
echo "🧹 Cleaning previous builds..."
rm -rf build dist __pycache__

# Run PyInstaller using the spec file
# The spec file is configured for onefile, so we don't need --onefile flag
echo "📦 Running PyInstaller..."
pyinstaller --clean ../pyinstaller.spec

# Make executable on Unix systems
if [ -f "dist/ai_software_dev_crew" ]; then
    chmod +x dist/ai_software_dev_crew
    echo "✅ Packaging complete!"
    echo "📁 Executable is located at: ./dist/ai_software_dev_crew"
    echo "💡 You can run it with: ./dist/ai_software_dev_crew \"Build a calculator\""
else
    echo "❌ Build failed - executable not found"
    exit 1
fi
